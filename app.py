import streamlit as st
import google.generativeai as genai
import pandas as pd
import io
import smtplib
import re
from PIL import Image
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

# --- PAGE CONFIG & MOBILE UI ---
st.set_page_config(page_title="Invoice Scanner", page_icon="🧾", layout="wide")

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
try:
    default_api_key = st.secrets["GEMINI_API_KEY"]
except KeyError:
    default_api_key = ""

api_key = st.sidebar.text_input("Gemini API Key", value=default_api_key, type="password")

st.sidebar.markdown("""
---
**Instructions:**
1. Upload vendor invoice images.
2. Add context if needed (e.g., 'All items are new').
3. Click Extract Data.
4. The app will automatically build Toast & Shopify CSVs.
""")

# --- MAIN UI ---
st.title("Mary Jane's Invoice Scanner 🧾")
st.write("Snap a picture of a vendor packing slip or invoice.")

extra_instructions = st.text_area(
    "Context / Instructions (Optional)", 
    placeholder="e.g., 'All items on this invoice are new.', 'Markup is 4x instead of 3x.'",
)

uploaded_files = st.file_uploader("Upload Image(s)", type=["jpg", "jpeg", "png"], accept_multiple_files=True)

def get_system_prompt(user_instructions):
    return f"""
    You are a Retail Inventory Migration Specialist extracting data from wholesale invoice images.
    Your objective is to extract data and format it perfectly into a CSV.
    
    CRITICAL RULE: Output ONLY valid, raw CSV text. Do not include markdown wrappers (like ```csv).
    
    CSV Columns Required Exactly:
    name,pos name,category group,category,subcategory,price,cost,barcode,supplier,color,size
    
    Extraction & Logic Rules:
    1. Filtering: ONLY extract line items that are hand-marked, underlined, or explicitly labeled as "New". 
       Prioritize user instructions regarding which items to process.
    2. Naming Convention (name column):
       - Clothing Sized: [SKU]-[Color]-[Size]
       - Clothing One-Size: [SKU]-[Color]
       - Non-Clothing: Just the [SKU]
    3. Color & Size Columns: Extract the Color and Size from the description and place them in their respective columns. If none exist, leave blank.
    4. POS Name: Copy the description verbatim.
    5. Category Mapping: Map based on item type (Accessories, Beer, BTG Wine, Clothing, Gifts, Handbags, Hats, Home, Jewelry, Snacks & Drinks, Wine Bottles). Category Group is ALWAYS "Retail".
    6. Cost & Price: Handwritten value if present; else printed unit cost. Price is Handwritten retail price, or Cost * 3. ALWAYS round the retail price to nearest dollar.
    7. Barcode: Printed UPC/barcode or leave blank.
    8. Subcategory & Supplier: Use the brand name found at top of invoice.
    
    User Additional Instructions: {user_instructions}
    """

# --- HELPER: LOGICAL SIZE SORTING ---
def get_size_rank(size_val):
    size_str = str(size_val).upper().strip()
    size_order = {
        'XXS': 1, 'XS': 2, 'S': 3, 'SMALL': 3,
        'M': 4, 'MEDIUM': 4, 'L': 5, 'LARGE': 5,
        'XL': 6, 'XXL': 7, '2XL': 7, '3XL': 8, '4XL': 9,
        'OS': 0, 'ONE SIZE': 0
    }
    if size_str in size_order:
        return size_order[size_str]
    
    num_match = re.search(r'\d+', size_str)
    if num_match:
        return 100 + float(num_match.group())
    
    return 50

# --- PROCESSING ---
if uploaded_files:
    file_names = [f.name for f in uploaded_files]
    if "current_files" not in st.session_state or st.session_state.current_files != file_names:
        
        if st.button("✨ Extract Data", use_container_width=True, type="primary"):
            if not api_key:
                st.error("Please provide a Gemini API Key in the sidebar or secrets.")
            else:
                with st.spinner("Analyzing invoices with Gemini Pro..."):
                    try:
                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel('gemini-pro-latest')
                        
                        prompt = get_system_prompt(extra_instructions)
                        images = [Image.open(file) for file in uploaded_files]
                        inputs = [prompt] + images
                        
                        response = model.generate_content(inputs)
                        
                        raw_csv = response.text.strip()
                        if raw_csv.startswith("```csv"): raw_csv = raw_csv[6:]
                        if raw_csv.startswith("```"): raw_csv = raw_csv[3:]
                        if raw_csv.endswith("```"): raw_csv = raw_csv[:-3]
                        raw_csv = raw_csv.strip()
                        
                        df = pd.read_csv(io.StringIO(raw_csv))
                        
                        # Apply Custom Logical Sorting
                        if 'size' in df.columns and 'color' in df.columns:
                            df['_size_rank'] = df['size'].apply(get_size_rank)
                            df = df.sort_values(by=['pos name', 'color', '_size_rank'], na_position='first')
                            df = df.drop(columns=['_size_rank'])
                        
                        st.session_state.invoice_data = df
                        st.session_state.current_files = file_names
                        st.rerun()

                    except Exception as e:
                        st.error(f"❌ An error occurred: {str(e)}")

# --- DISPLAY & EXPORT ---
if "invoice_data" in st.session_state and not st.session_state.invoice_data.empty:
    st.success("✅ Extraction Complete! Review the data below. Sizes have been sorted logically for Shopify.")
    
    edited_export_df = st.data_editor(st.session_state.invoice_data, use_container_width=True, hide_index=True)
    
    # --- 1. DYNAMIC FILE NAMING ---
    try:
        brand_name = str(edited_export_df['subcategory'].iloc[0]).strip()
        if not brand_name or brand_name.lower() == "nan": brand_name = "Invoice"
    except:
        brand_name = "Invoice"
        
    date_str = datetime.now().strftime("%m%d%Y")
    toast_filename = f"{brand_name} {date_str} - Toast.csv"
    shopify_filename = f"{brand_name} {date_str} - Shopify.csv"

    # --- 2. TOAST EXPORT FORMATTING ---
    def excel_safe_barcode(val):
        val_str = str(val).strip()
        if val_str and val_str.lower() != 'nan':
            return f'="{val_str}"'
        return ""

    toast_df = edited_export_df.copy()
    if 'barcode' in toast_df.columns:
        toast_df['barcode'] = toast_df['barcode'].apply(excel_safe_barcode)
        
    toast_output = toast_df[['name','pos name','category group','category','subcategory','price','cost','barcode','supplier']]

    # --- 3. SHOPIFY CONVERTER LOGIC ---
    shopify_df = pd.DataFrame()
    shopify_df['Title'] = edited_export_df['pos name'].str.title()
    shopify_df['URL handle'] = edited_export_df['pos name'].astype(str).str.lower().str.replace(r'[^a-z0-9]+', '-', regex=True).str.strip('-')
    shopify_df['Description'] = ""
    shopify_df['Vendor'] = edited_export_df['supplier']
    shopify_df['Product category'] = "Apparel & Accessories"
    shopify_df['Type'] = edited_export_df['category']
    shopify_df['Tags'] = edited_export_df['supplier'].astype(str) + ", Retail"
    shopify_df['Published on online store'] = "FALSE"
    shopify_df['Status'] = 'draft' 
    shopify_df['SKU'] = edited_export_df['name']
    shopify_df['Barcode'] = edited_export_df['barcode'] 
    shopify_df['Option1 name'] = "Color"
    shopify_df['Option1 value'] = edited_export_df.get('color', '')
    shopify_df['Option2 name'] = "Size"
    shopify_df['Option2 value'] = edited_export_df.get('size', '')
    shopify_df['Price'] = edited_export_df['price']
    shopify_df['Cost per item'] = edited_export_df['cost']
    shopify_df['Charge tax'] = 'TRUE'

    st.divider()

    # --- EXPORT BUTTONS ---
    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        if st.button("📤 Email BOTH to Back Office", use_container_width=True):
            try:
                sender = st.secrets["SENDER_EMAIL"]
                recipient = st.secrets["RECIPIENT_EMAIL"]
                sender_pwd = st.secrets["SENDER_APP_PASSWORD"]

                toast_bytes = toast_output.to_csv(index=False).encode('utf-8')
                shopify_bytes = shopify_df.to_csv(index=False).encode('utf-8')

                msg = MIMEMultipart()
                msg["From"] = sender
                msg["To"] = recipient
                msg["Subject"] = f"Invoice Uploads - {brand_name}"
                msg.attach(MIMEText(f"Attached are the Toast and Shopify CSV files for {brand_name}.", "plain"))

                part1 = MIMEBase("application", "octet-stream")
                part1.set_payload(toast_bytes)
                encoders.encode_base64(part1)
                part1.add_header("Content-Disposition", f'attachment; filename="{toast_filename}"')
                msg.attach(part1)

                part2 = MIMEBase("application", "octet-stream")
                part2.set_payload(shopify_bytes)
                encoders.encode_base64(part2)
                part2.add_header("Content-Disposition", f'attachment; filename="{shopify_filename}"')
                msg.attach(part2)

                with smtplib.SMTP("mail.smtp2go.com", 2525) as server:
                    server.starttls()
                    server.login(sender, sender_pwd)
                    server.sendmail(sender, recipient, msg.as_string())

                st.success("✅ Both CSVs sent to Back Office successfully!")
            except Exception as e:
                st.error(f"Failed to send email: {type(e).__name__}: {e}")

    with col2:
        st.download_button(
            label="⬇️ Download Toast CSV",
            data=toast_output.to_csv(index=False).encode('utf-8'),
            file_name=toast_filename,
            mime="text/csv",
            use_container_width=True,
        )

    with col3:
        st.download_button(
            label="⬇️ Download Shopify CSV",
            data=shopify_df.to_csv(index=False).encode('utf-8'),
            file_name=shopify_filename,
            mime="text/csv",
            use_container_width=True,
        )

    st.divider()

    if st.button("🔄 Scan a New Invoice", use_container_width=True):
        del st.session_state.invoice_data
        del st.session_state.current_files
        st.rerun()
