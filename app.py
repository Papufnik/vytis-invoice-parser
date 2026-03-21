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

  # Columns that get exported to the Toast CSV (internal helper columns are excluded)
  EXPORT_COLUMNS = [
      "Supplier Item ID", "Item Name", "Color", "Size",
      "Item Quantity", "Receiving Unit", "Receiving Unit Net Cost",
      "Price (Retail)", "Barcode", "SKU"
  ]

  # --- FILE UPLOAD ---
  uploaded_file = st.file_uploader("Upload Image", type=["jpg", "jpeg", "png", "pdf"])

  if uploaded_file:
      # Only call Textract when a new file is uploaded — skips re-scanning on every rerender
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
                                  "Markup (x)": 2.7,
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
                                      # Handle both US (1,234.56) and European (1.234,56) formats
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
      # This block runs on every rerender so results stay visible after the scan completes
      if "invoice_data" in st.session_state and not st.session_state.invoice_data.empty:
          df = st.session_state.invoice_data

          st.success(f"Found {len(df)} line item(s). You can edit any cell before sending.")

          # Editable grid — user can fix anything Textract got wrong before exporting
          edited_df = st.data_editor(df, use_container_width=True, hide_index=True)

          col1, col2 = st.columns(2)

          with col1:
              if st.button("📤 Send CSV to Back Office", use_container_width=True):
                  try:
                      sender = st.secrets["SENDER_EMAIL"]
                      recipient = st.secrets["RECIPIENT_EMAIL"]

                      csv_bytes = edited_df[EXPORT_COLUMNS].to_csv(index=False).encode('utf-8')

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

                      with smtplib.SMTP("smtp.gmail.com", 587) as server:
                          server.starttls()
                          server.login(sender, st.secrets["SENDER_APP_PASSWORD"])
                          server.sendmail(sender, recipient, msg.as_string())

                      st.success("CSV sent to Back Office successfully!")
                  except Exception as e:
                      st.error(f"Failed to send email: {type(e).__name__}: {e}")

          with col2:
              st.download_button(
                  label="⬇️ Download Toast CSV",
                  data=edited_df[EXPORT_COLUMNS].to_csv(index=False).encode('utf-8'),
                  file_name="toast_invoice_upload.csv",
                  mime="text/csv",
                  use_container_width=True,
              )

          st.divider()

          if st.button("🔄 Scan a New Invoice", use_container_width=True):
              del st.session_state.invoice_data
              del st.session_state.current_file
              st.rerun()
