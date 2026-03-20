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
    /* Make the metric numbers pop a bit more */
    [data-testid="stMetricValue"] {
        font-size: 1.8rem;
        color: #1E88E5; 
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

# The Upload Button
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

                for expense_doc in response.get('ExpenseDocuments', []):
                    for line_item_group in expense_doc.get('LineItemGroups', []):
                        for line_item in line_item_group.get('LineItems', []):

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
                                "Markup (x)": 2.7 
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
                                    
                                    # FIX: THE COMMA BUG
                                    fixed_val = re.sub(r',(\d{2})$', r'.\1', field_val.strip())
                                    numeric_cost = re.sub(r'[^\d.]', '', fixed_val)
                                    
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

    # 2. THE NEW MOBILE CARD UI
    if "invoice_data" in st.session_state and not st.session_state.invoice_data.empty:
        st.divider()
        st.subheader("1. Item Review & Pricing")
        st.caption("Verify item names and adjust your markups below.")
        
        # Pull the data out of the vault to work with it
        working_data = st.session_state.invoice_data.to_dict('records')
        
        # Build a beautiful, isolated card for every single item
        for i, row in enumerate(working_data):
            # The 'border=True' creates a distinct visual box for each item
            with st.container(border=True):
                
                # Big, easy text box to fix any missing lines from Amazon
                new_name = st.text_input(
                    f"Item {i+1} Description", 
                    value=row["Item Name"], 
                    key=f"name_{i}"
                )
                
                # Break the math into three clean columns
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.metric("Cost", f"${row['_Raw Cost']:.2f}")
                    
                with col2:
                    new_markup = st.number_input(
                        "Markup", 
                        value=float(row.get("Markup (x)", 2.7)), 
                        step=0.1, 
                        format="%.1f", 
                        key=f"markup_{i}"
                    )
                    
                with col3:
                    # Instantly calculate and round the retail price
                    raw_retail = row['_Raw Cost'] * new_markup
                    if raw_retail > 0:
                        rounded_retail = 5 * round(raw_retail / 5)
                    else:
                        rounded_retail = 0
                    
                    st.metric("Retail", f"${rounded_retail:.2f}")
                
                # Save the user's edits back to the working data
                working_data[i]["Item Name"] = new_name
                working_data[i]["Markup (x)"] = new_markup
                working_data[i]["Price (Retail)"] = f"{rounded_retail:.2f}"

        # Convert the newly edited cards back into a final Master Spreadsheet
        final_df = pd.DataFrame(working_data)
        final_export_df = final_df.drop(columns=['_Raw Cost', 'Markup (x)'])

        st.divider()
        st.subheader("2. Master Spreadsheet Preview")
        st.caption("This is the exact file going to the back office.")
        
        # Display the classic spreadsheet view at the bottom
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
                        server.sendmail(sender, recipient, msg
