import streamlit as st
import google.generativeai as genai
import pandas as pd
import io
import smtplib
from PIL import Image
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
try:
    APP_PASSWORD = st.secrets["APP_PASSWORD"]
except KeyError:
    st.error("⚠️ App password not found in secrets. Please configure it.")
    st.stop()

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

# --- SIDEBAR CONFIG ---
st.sidebar.title("⚙️ Settings")
# Use secrets if available, otherwise allow manual input
try:
    default_api_key = st.secrets["GEMINI_API_KEY"]
except KeyError:
    default_api_key = ""

api_key = st.sidebar.text_input("Gemini API Key", value=default_api_key, type="password")

st.sidebar.markdown("""
---
**Instructions for Employees:**
1. Upload vendor invoice images.
2. Add any context in the text box (e.g., 'All items are new').
3. Review the data, make edits if needed, and email to the Back Office.
""")

# --- MAIN UI ---
st.title("Mary Jane's Invoice Scanner 🧾")
st.write("Snap a picture of a vendor packing slip or invoice.")

# Additional Instructions (Handles the "No handwritten notes" scenario)
extra_instructions = st.text_area(
    "Context / Instructions (Optional)", 
    placeholder="e.g., 'All items on this invoice are new.', 'Markup is 4x instead of 3x.'",
    help="Provide context if there are no handwritten notes on the invoice."
)

uploaded_files = st.file_uploader("Upload Image(s)", type=["jpg", "jpeg", "png"], accept_multiple_files=True)

def get_system_prompt(user_instructions):
    return f"""
    You are a Retail Inventory Migration Specialist extracting data from wholesale invoice images.
    Your objective is to extract data and format it perfectly into a CSV for a Toast Retail upload.
    
    CRITICAL RULE: Output ONLY valid, raw CSV text. Do not include markdown wrappers (like ```csv), and do not include conversational text.
    
    CSV Columns Required Exactly:
    name,pos name,category group,category,subcategory,price,cost,barcode,supplier
    
    Extraction & Logic Rules:
    1. Filtering: ONLY extract line items that are hand-marked, underlined, or explicitly labeled as "New". 
       If the user provides Additional Instructions regarding which items to process, prioritize those instructions.
    2. Naming Convention (name column):
       - Clothing Sized: [SKU]-[Color]-[Size] (e.g., MT-2200-Navy-S)
       - Clothing One-Size: [SKU]-[Color]
       - Non-Clothing: Just the [SKU]
    3. POS Name: Copy the description verbatim from the invoice.
    4. Category Mapping: Map based on item type (Accessories, Beer, BTG Wine, Clothing, Gifts, Handbags, Hats, Home, Jewelry, Snacks & Drinks, Wine Bottles). 
       Category Group is ALWAYS "Retail".
    5. Cost & Price:
       - Cost: Handwritten value if present; else printed unit cost. Do not round the cost.
       - Price: Handwritten retail price. If missing, calculate as Cost * 3. ALWAYS round the final retail price to the nearest whole dollar.
    6. Barcode: Printed UPC/barcode or leave blank.
    7. Subcategory & Supplier: Use the brand name found at the top of the invoice.
    
    User Additional Instructions: {user_instructions}
    """

# --- PROCESSING ---
if uploaded_files:
    # Use session state to avoid reprocessing on every interaction
    file_names = [f.name for f in uploaded_files]
    if "current_files" not in st.session_state or st.session_state.current_files != file_names:
        
        if st.button("✨ Extract Data", use_container_width=True, type="primary"):
            if not api_key:
                st.error("Please provide a Gemini API Key in the sidebar or AWS secrets.")
            else:
                with st.spinner("Analyzing invoices with Gemini 1.5 Pro..."):
                    try:
                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-pro-latest')
                        
                        prompt = get_system_prompt(extra_instructions)
                        images = [Image.open(file) for file in uploaded_files]
                        inputs = [prompt] + images
                        
                        response = model.generate_content(inputs)
                        
                        # Clean markdown wrappers from output
                        raw_csv = response.text.strip()
                        if raw_csv.startswith("```csv"): raw_csv = raw_csv[6:]
                        if raw_csv.startswith("```"): raw_csv = raw_csv[3:]
                        if raw_csv.endswith("```"): raw_csv = raw_csv[:-3]
                        raw_csv = raw_csv.strip()
                        
                        df = pd.read_csv(io.StringIO(raw_csv))
                        
                        st.session_state.invoice_data = df
                        st.session_state.current_files = file_names
                        st.rerun()

                    except pd.errors.ParserError:
                        st.error("❌ The AI failed to format the output as a valid CSV. Please adjust your instructions.")
                        with st.expander("View Raw AI Output"):
                            st.text(response.text)
                    except Exception as e:
                        st.error(f"❌ An error occurred: {str(e)}")

# --- DISPLAY & EXPORT ---
if "invoice_data" in st.session_state and not st.session_state.invoice_data.empty:
    st.success("✅ Extraction Complete! Review and edit the data below before sending.")
    
    # Interactive Data Editor
    edited_export_df = st.data_editor(st.session_state.invoice_data, use_container_width=True, hide_index=True)
    
    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        if st.button("📤 Send CSV to Back Office", use_container_width=True):
            try:
                sender = st.secrets["SENDER_EMAIL"]
                recipient = st.secrets["RECIPIENT_EMAIL"]
                sender_pwd = st.secrets["SENDER_APP_PASSWORD"]

                csv_bytes = edited_export_df.to_csv(index=False).encode('utf-8')

                msg = MIMEMultipart()
                msg["From"] = sender
                msg["To"] = recipient
                msg["Subject"] = "Toast Invoice Upload - New Items"
                msg.attach(MIMEText("Please find the extracted new items CSV attached.", "plain"))

                part = MIMEBase("application", "octet-stream")
                part.set_payload(csv_bytes)
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    'attachment; filename="toast_invoice_import.csv"',
                )
                msg.attach(part)

                with smtplib.SMTP("mail.smtp2go.com", 2525) as server:
                    server.starttls()
                    server.login(sender, sender_pwd)
                    server.sendmail(sender, recipient, msg.as_string())

                st.success("✅ CSV sent to Back Office successfully!")
            except KeyError as e:
                st.error(f"Missing email configuration in AWS Secrets: {e}")
            except Exception as e:
                st.error(f"Failed to send email: {type(e).__name__}: {e}")

    with col2:
        st.download_button(
            label="⬇️ Download Toast CSV",
            data=edited_export_df.to_csv(index=False).encode('utf-8'),
            file_name="toast_invoice_import.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.divider()

    if st.button("🔄 Scan a New Invoice", use_container_width=True):
        del st.session_state.invoice_data
        del st.session_state.current_files
        st.rerun()
