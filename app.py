import streamlit as st
import boto3
import pandas as pd
import smtplib
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# --- PAGE CONFIG & MOBILE UI ---
st.set_page_config(page_title="Invoice Scanner", page_icon="🧾", layout="centered")

st.markdown("""
    <style>
    .stButton>button { height: 3.5em; font-size: 18px; font-weight: bold; border-radius: 8px; }
    [data-testid="stMetricValue"] { font-size: 1.8rem; color: #1E88E5; }
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

# --- AWS TEXTRACT CLIENT ---
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

def round_to_nearest_5(value):
    return int(round(value / 5) * 5)

EXPORT_COLUMNS = [
    "Supplier Item ID", "Item Name", "Color", "Size",
    "Item Quantity", "Receiving Unit", "Receiving Unit Net Cost",
    "Price (Retail)", "Barcode", "SKU"
]

# --- FILE UPLOAD ---
uploaded_file = st.file_uploader("Upload Image", type=["jpg", "jpeg", "png", "pdf"])

if uploaded_file:
    # Only call Textract when a new file is uploaded
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
                            }

                            for field in line_item.get('LineItemExpenseFields', []):
                                field_type = field.get('Type', {}).get('Text')
                                field_val = field.get('ValueDetection', {}).get('Text') or ""

                                if field_type == 'ITEM':
                                    item_data["Item Name"] = (
                                        item_data["Item Name"] + " " + field_val
                                        if item_data["Item Name"] else field_val
                                    )
                                elif field_type == 'QUANTITY':
                                    item_data["Item Quantity"] = field_val
                                elif field_type == 'PRODUCT_CODE':
                                    item_data["Supplier Item ID"] = field_val
                                elif field_type == 'UNIT_PRICE':
                                    item_data["Receiving Unit Net Cost"] = field_val
                                    numeric_cost = re.sub(r'[^\d.]', '', re.sub(r',(\d{2})$', r'.\1', field_val.strip()))
                                    if numeric_cost:
                                        try:
                                            item_data["_Raw Cost"] = float(numeric_cost)
                                        except ValueError:
                                            pass
                                elif field_type == 'PRICE':
                                    item_data["Price (Retail)"] = field_val

                            if item_data["Item Name"]:
                                items_list.append(item_data)

                st.session_state.invoice_data = pd.DataFrame(items_list)
                st.session_state.current_file = uploaded_file.name

                # Clear markup values from any previous invoice
                for key in list(st.session_state.keys()):
                    if key.startswith("markup_"):
                        del st.session_state[key]

            except textract.exceptions.UnsupportedDocumentException:
                st.error("Unsupported file format. Please upload a JPG, PNG, or single-page PDF.")
            except textract.exceptions.DocumentTooLargeException:
                st.error(
                    "This file is too large for Amazon Textract (5 MB limit). "
                    "Please compress the image and try again."
                )
            except textract.exceptions.BadDocumentException:
                st.error(
                    "Amazon could not read this document. The image may be too blurry, "
                    "rotated, or corrupt. Try re-scanning at 300 DPI or higher."
                )
            except textract.exceptions.ProvisionedThroughputExceededException:
                st.error("The scanning service is temporarily overloaded. Wait 30 seconds and try again.")
            except textract.exceptions.ThrottlingException:
                st.error("Too many requests sent to Amazon. Please wait a moment and re-upload.")
            except textract.exceptions.InvalidParameterException as e:
                st.error(f"Invalid request sent to Amazon Textract: {e}")
            except Exception as e:
                st.error(f"An unexpected error occurred: `{type(e).__name__}: {e}`")

    # --- DISPLAY RESULTS ---
    if "invoice_data" in st.session_state and not st.session_state.invoice_data.empty:
        df = st.session_state.invoice_data

        st.success(f"Found {len(df)} line item(s). Adjust markups below.")
        st.subheader("Pricing")

        # One card per item — adjust markup and see retail price update live
        for i, row in df.iterrows():
            with st.container(border=True):
                st.markdown(f"**{row['Item Name']}**")
                if row['Supplier Item ID']:
                    st.caption(f"ID: {row['Supplier Item ID']}")

                col1, col2 = st.columns(2)

                with col1:
                    st.markdown("**Markup ×**")
                    markup = st.number_input(
                        "Markup",
                        min_value=0.1,
                        max_value=99.0,
                        value=float(st.session_state.get(f"markup_{i}", 2.7)),
                        step=0.1,
                        format="%.2f",
                        key=f"markup_{i}",
                        label_visibility="collapsed",
                    )

                with col2:
                    if row['_Raw Cost'] > 0:
                        retail = round_to_nearest_5(row['_Raw Cost'] * markup)
                        st.metric("Retail Price", f"${retail}")
                    else:
                        st.metric("Retail Price", "—")
                        st.caption("No cost detected")

        st.divider()

        # Build the export dataframe with computed retail prices applied
        export_df = df[EXPORT_COLUMNS].copy()
        for i, row in df.iterrows():
            markup = st.session_state.get(f"markup_{i}", 2.7)
            if row['_Raw Cost'] > 0:
                export_df.at[i, 'Price (Retail)'] = str(round_to_nearest_5(row['_Raw Cost'] * markup))

        st.subheader("Full Spreadsheet Preview")
        st.dataframe(export_df, use_container_width=True, hide_index=True)

        st.divider()

        col1, col2 = st.columns(2)

        with col1:
            if st.button("📤 Send CSV to Back Office", use_container_width=True):
                try:
                    sender = st.secrets["SENDER_EMAIL"]
                    recipient = st.secrets["RECIPIENT_EMAIL"]

                    csv_bytes = export_df.to_csv(index=False).encode('utf-8')

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

                    # --- FIXED SMTP SERVER ---
                    with smtplib.SMTP("mail.smtp2go.com", 2525) as server:
                        server.starttls()
                        server.login(sender, st.secrets["SENDER_APP_PASSWORD"])
                        server.sendmail(sender, recipient, msg.as_string())

                    st.success("CSV sent to Back Office successfully!")
                except Exception as e:
                    st.error(f"Failed to send email: {type(e).__name__}: {e}")

        with col2:
            st.download_button(
                label="⬇️ Download Toast CSV",
                data=export_df.to_csv(index=False).encode('utf-8'),
                file_name="toast_invoice_upload.csv",
                mime="text/csv",
                use_container_width=True,
            )

        st.divider()

        if st.button("🔄 Scan a New Invoice", use_container_width=True):
            for key in list(st.session_state.keys()):
                if key.startswith("markup_"):
                    del st.session_state[key]
            del st.session_state.invoice_data
            del st.session_state.current_file
            st.rerun()
