import streamlit as st
import boto3
import pandas as pd
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import re

# --- MOBILE UI OPTIMIZATIONS ---
st.set_page_config(page_title="Invoice Scanner", page_icon="🧾", layout="centered")

st.markdown("""
    <style>
    /* Make buttons thick and easy to tap on mobile screens */
    .stButton>button {
        height: 3.5em;
        font-size: 18px;
        font-weight: bold;
        border-radius: 8px;
    }
    </style>
""", unsafe_allow_html=True)

# --- PASSWORD GATE ---
APP_PASSWORD = st.secrets["APP_PASSWORD"]

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("Mary Jane's Scanner")
    entered = st.text_input("Enter password to continue:", type="password")
    if st.button("Login", use_container_width=True):
        if entered == APP_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()
# --- END PASSWORD GATE ---

st.title("Mary Jane's Invoice Scanner 🧾")
st.write("Snap a picture of a vendor packing slip.")

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

# The Upload Button (Mobile phones will automatically ask "Take Photo or Choose Library" here)
uploaded_file = st.file_uploader("Upload Image", type=["jpg", "jpeg", "png", "pdf"])

if uploaded_file:
    # 1. THE CACHE: Only call Amazon if this is a brand new file.
    if "current_file" not in st.session_state or st.session_state.current_file != uploaded_file.name:
        st.info("Scanning document with Amazon AI... this takes about 10 seconds.")
        
        with st.spinner("Processing lines..."):
            try:
                document_bytes = uploaded_file.read()
                response = textract.analyze_expense(Document={'Bytes': document_bytes})

                items_list = []

                # Parse the AI response
                for expense_doc in response.get('ExpenseDocuments', []):
                    for line_item_group in expense_doc.get('LineItemGroups', []):
                        for line_item in line_item_group.get('LineItems', []):

                            # The Master Toast/Shopify Matrix
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
                                "_Raw Cost": 0.0, 
                                "Markup (x)": 2.7 # DEFAULT MULTIPLIER
                            }

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

                # Lock the extracted data in the vault
                st.session_state.invoice_data = pd.DataFrame(items_list)
                st.session_state.current_file = uploaded_file.name

            except Exception as e:
                st.error(f"Amazon parsing failed: {type(e).__name__}: {e}")

    # 2. THE BULLETPROOF DATA PIPELINE
    if "invoice_data" in st.session_state and not st.session_state.invoice_data.empty:
        st.divider()
        st.subheader("1. Adjust Markup")
        st.caption("Scroll right to edit multipliers. Double-tap an Item Name to fix typos.")
        
        df = st.session_state.invoice_data.copy()
        
        # Reorder columns so Markup is easy to see on mobile
        if "Markup (x)" in df.columns and "Price (Retail)" in df.columns:
            cols = list(df.columns)
            cols.insert(cols.index("Price (Retail)"), cols.pop(cols.index("Markup (x)")))
            df = df[cols]

        # Interactive data editor (User Inputs)
        edited_df = st.data_editor(
            df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Markup (x)": st.column_config.NumberColumn(
                    "Markup", # Shorter title for mobile screens
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

        # Apply the math to create the final data
        final_df = edited_df.copy()
        final_df['Price (Retail)'] = final_df.apply(calc_retail, axis=1)

        # Clean up the hidden math columns before sending to Toast
        final_export_df = final_df.drop(columns=['_Raw Cost', 'Markup (x)'])

        st.divider()
        st.subheader("2. Final Price Preview")
        st.caption("Verify final retail prices before sending.")
        
        # Show a slimmed down mobile preview
        st.dataframe(
            final_export_df[['Item Name', 'Receiving Unit Net Cost', 'Price (Retail)']], 
            hide_index=True, 
            use_container_width=True
        )

        st.divider()
        # THE PRIMARY ACTION BUTTON
        if st.button("📤 Send CSV to Back Office", type="primary", use_container_width=True):
            with st.spinner("Sending secure email..."):
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

                    st.success("✅ CSV sent to Back Office successfully!")
                    st.balloons()
                except Exception as e:
                    st.error(f"Failed to send email: {type(e).__name__}: {e}")

        # Secondary Backup Button
        csv = final_export_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download CSV Backup",
            data=csv,
            file_name="toast_inventory_upload.csv",
            mime="text/csv",
            use_container_width=True
        )
