import streamlit as st
import boto3
import pandas as pd
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

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
                            "SKU": ""
                        }

                        # Fill in what the AI found
                        for field in line_item.get('LineItemExpenseFields', []):
                            field_type = field.get('Type', {}).get('Text')
                            # Sanitize: Textract can return None if confidence is too low
                            field_val = field.get('ValueDetection', {}).get('Text') or ""

                            if field_type == 'ITEM':
                                item_data["Item Name"] = field_val
                            elif field_type == 'QUANTITY':
                                item_data["Item Quantity"] = field_val
                            elif field_type == 'PRODUCT_CODE':
                                item_data["Supplier Item ID"] = field_val
                            elif field_type == 'UNIT_PRICE':
                                item_data["Receiving Unit Net Cost"] = field_val
                            elif field_type == 'PRICE':
                                item_data["Price (Retail)"] = field_val

                        # Only keep rows that actually have an item name
                        if item_data["Item Name"]:
                            items_list.append(item_data)

            # Display the grid and create the download button
            if items_list:
                df = pd.DataFrame(items_list)
                st.dataframe(df)

                if st.button("📤 Send CSV to Back Office"):
                    try:
                        sender = st.secrets["SENDER_EMAIL"]
                        recipient = st.secrets["RECIPIENT_EMAIL"]

                        csv_bytes = df.to_csv(index=False).encode('utf-8')

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

                        with smtplib.SMTP("smtp.office365.com", 587) as server:
                            server.starttls()
                            server.login(sender, st.secrets["SENDER_APP_PASSWORD"])
                            server.sendmail(sender, recipient, msg.as_string())

                        st.success("CSV sent to Back Office successfully!")
                    except Exception as e:
                        st.error(f"Failed to send email: {type(e).__name__}: {e}")

                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="Download Toast CSV",
                    data=csv,
                    file_name="toast_inventory_upload.csv",
                    mime="text/csv",
                )
            else:
                st.warning("Amazon couldn't find any clear line items on this document.")

        except textract.exceptions.UnsupportedDocumentException:
            st.error(
                "Unsupported file format. Please upload a JPG, PNG, or single-page PDF."
            )
        except textract.exceptions.DocumentTooLargeException:
            st.error(
                "This file is too large for Amazon Textract (5 MB limit for direct upload). "
                "Please compress the image and try again."
            )
        except textract.exceptions.BadDocumentException:
            st.error(
                "Amazon could not read this document. The image may be too blurry, "
                "rotated, or corrupt. Try re-scanning at 300 DPI or higher."
            )
        except textract.exceptions.ProvisionedThroughputExceededException:
            st.error(
                "The scanning service is temporarily overloaded. Wait 30 seconds and try again."
            )
        except textract.exceptions.ThrottlingException:
            st.error(
                "Too many requests sent to Amazon. Please wait a moment and re-upload."
            )
        except textract.exceptions.InvalidParameterException as e:
            st.error(f"Invalid request sent to Amazon Textract: {e}")
        except Exception as e:
            st.error(
                f"An unexpected error occurred. Details for your developer: `{type(e).__name__}: {e}`"
            )
