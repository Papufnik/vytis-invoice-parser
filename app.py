import streamlit as st
import boto3
import pandas as pd
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import re

# --- PASSWORD GATE ---
APP_PASSWORD = st.secrets["APP_PASSWORD"]

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("Mary Jane's Invoice Scanner")
    entered = st.text_input("Enter password to continue:", type="password")
    if st.button("Login"):
        if entered == APP_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()
# --- END PASSWORD GATE ---

st.title("Mary Jane's Invoice Scanner")
st.write("Upload a vendor packing slip to generate a Toast/Shopify inventory CSV.")

# Connect to the secure AWS Vault
try:
    textract = boto3.client(
        'textract',
        aws_access_key_id=st.secrets["AWS_ACCESS_KEY_ID"].strip(),
        aws_secret_access_key=st.secrets["AWS_SECRET_ACCESS_KEY"].strip(),
        region_name=st.secrets["AWS_REGION"].strip(),
        aws_session_token=None
    )
except KeyError as e:
    st.error(f"Missing AWS credential in secrets: {e}. Contact your administrator.")
    st.stop()

# The Upload Button
uploaded_file = st.file_uploader("Upload an Image", type=["jpg", "jpeg", "png", "pdf"])

if uploaded_file:
    # BUG FIX 1: THE CACHE. Only call Amazon if this is a brand new file.
    if "current_file" not in st.session_state or st.session_state.current_file != uploaded_file.name:
        st.success(f"File '{uploaded_file.name}' uploaded! Sending to Amazon AI...")
        
        with st.spinner("Analyzing document..."):
            try:
                document_bytes = uploaded_file.read()
                # Call the Amazon Textract Expense AI
                response = textract.analyze_expense(Document={'Bytes': document_bytes})

                items_list = []

                # Parse the AI response
                for expense_doc in response.get('ExpenseDocuments', []):
                    for line_item_group in expense_doc.get('LineItemGroups', []):
                        for line_item in line_item_group.get('LineItems', []):

                            # The Master Toast/Shopify Matrix Dictionary
                            item_data = {
                                "Supplier Item ID": "",
                                "Item Name": "",
                                "Color": "",
                                "Size": "",
                                "Item Quantity": "1",
                                "Receiving Unit": "Each",
                                "Receiving Unit Net Cost": "",
                                "Price (Retail)": "",
                                "Barcode": "",
                                "SKU": "",
                                "_Raw Cost": 0.0, # Hidden helper column for math
                                "Markup (x)": 2.7 # DEFAULT MULTIPLIER
                            }

                            # Fill in what the AI found
                            for field in line_item.get('LineItemExpenseFields', []):
                                field_type = field.get('Type', {}).get('Text')
                                field_val = field.get('ValueDetection', {}).get('Text') or ""

                                if field_type == 'ITEM':
                                    if item_data["Item Name"]:
                                        item_data["Item Name"] += " " + field_val
                                    else:
                                        item_data["Item Name"] = field_val
                                elif field_type == 'QUANTITY':
                                    item_data["Item Quantity"] = field_val
                                elif field_type == 'PRODUCT_CODE':
                                    item_data["Supplier Item ID"] = field_val
                                elif field_type == 'UNIT_PRICE':
                                    item_data["Receiving Unit Net Cost"] = field_val
                                    numeric_cost = re.sub(r'[^\d.]', '', field_val)
                                    if numeric_cost:
                                        item_data["_Raw Cost"] = float(numeric_cost)
                                elif field_type == 'PRICE':
                                    item_data["Price (Retail)"] = field_val

                            if item_data["Item Name"]:
                                items_list.append(item_data)

                # Lock the extracted data in the vault so Amazon isn't called again
                st.session_state.invoice_data = pd.DataFrame(items_list)
                st.session_state.current_file = uploaded_file.name

            except Exception as e:
                st.error(f"Amazon parsing failed: {type(e).__name__}: {e}")

    # BUG FIX 2: THE RECALCULATION LOOP. Display the grid from the cached data.
    if "invoice_data" in st.session_state and not st.session_state.invoice_data.empty:
        st.write("### Review & Price")
        st.write("Adjust your markup below. Retail prices will instantly calculate and round to the nearest $5. **You can also double-click an Item Name to fix it if the AI missed a line.**")
        
        df = st.session_state.invoice_data.copy()
        
        # Reorder columns so Markup is easy to see
        if "Markup (x)" in df.columns and "Price (Retail)" in df.columns:
            cols = list(df.columns)
            cols.insert(cols.index("Price (Retail)"), cols.pop(cols.index("Markup (x)")))
            df = df[cols]

        # Create the interactive data editor
        edited_df = st.data_editor(
            df,
            hide_index=True,
            column_config={
                "Markup (x)": st.column_config.NumberColumn(
                    "Markup (x)",
                    min_value=0.1,
                    step=0.1,
                    format="%.1f"
                ),
                "_Raw Cost": None # Keep the raw math hidden
            }
        )

        # The Mathematical Engine
        def calc_retail(row):
            try:
                cost = float(row['_Raw Cost'])
                markup = float(row['Markup (x)'])
                if cost > 0 and markup > 0:
                    raw_retail = cost * markup
                    rounded_retail = 5 * round(raw_retail / 5)
                    return f"{rounded_retail:.2f}"
            except:
                pass
            return row['Price (Retail)']

        # Calculate the new retail prices based on the user's edits
        new_df = edited_df.copy()
        new_df['Price (Retail)'] = new_df.apply(calc_retail, axis=1)

        # If the newly calculated math doesn't match the vault, update the vault and reload the UI instantly
        if not new_df.equals(st.session_state.invoice_data):
            st.session_state.invoice_data = new_df
            st.rerun()

        # Clean up the hidden math columns before sending to Toast
        final_export_df = new_df.drop(columns=['_Raw Cost', 'Markup (x)'])

        if st.button("📤 Send CSV to Back Office"):
            try:
                sender = st.secrets["SENDER_EMAIL"]
                recipient = st.secrets["RECIPIENT_EMAIL"]

                csv_bytes = final_export_df.to_csv(index=False).encode('utf-8')

                msg = MIMEMultipart()
                msg["From"] = sender
                msg["To"] = recipient
                msg["Subject"] = "Toast Invoice Upload"
                msg.attach(MIMEText("Please find the invoice CSV attached.", "plain"))

                part = MIMEBase("application", "octet-stream")
                part.set_payload(csv_bytes)
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    'attachment; filename="toast_invoice_upload.csv"',
                )
                msg.attach(part)

                with smtplib.SMTP("mail.smtp2go.com", 2525) as server:
                    server.login(sender, st.secrets["SENDER_APP_PASSWORD"])
                    server.sendmail(sender, recipient, msg.as_string())

                st.success("CSV sent to Back Office successfully!")
            except Exception as e:
                st.error(f"Failed to send email: {type(e).__name__}: {e}")

        csv = final_export_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Toast CSV",
            data=csv,
            file_name="toast_inventory_upload.csv",
            mime="text/csv",
        )
