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
                st.session_state.invoice
