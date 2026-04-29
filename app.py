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

# --- SHOPIFY EXACT COLUMNS ---
SHOPIFY_COLUMNS = [
    "Title", "URL handle", "Description", "Vendor", "Product category", "Type", "Tags",
    "Published on online store", "Status", "SKU", "Barcode", 
    "Option1 name", "Option1 value", "Option1 Linked To",
    "Option2 name", "Option2 value", "Option2 Linked To",
    "Option3 name", "Option3 value", "Option3 Linked To",
    "Price", "Compare-at price", "Cost per item", "Charge tax", "Tax code",
    "Unit price total measure", "Unit price total measure unit",
    "Unit price base measure", "Unit price base measure unit",
    "Inventory tracker", "Inventory quantity", "Continue selling when out of stock",
    "Weight value (grams)", "Weight unit for display", "Requires shipping",
    "Fulfillment service", "Product image URL", "Image position", "Image alt text",
    "Variant image URL", "Gift card", "SEO title", "SEO description",
    "Color (product.metafields.shopify.color-pattern)",
    "Google Shopping / Google product category", "Google Shopping / Gender",
    "Google Shopping / Age group", "Google Shopping / Manufacturer part number (MPN)",
    "Google Shopping / Ad group name", "Google Shopping / Ads labels",
    "Google Shopping / Condition", "Google Shopping / Custom product",
    "Google Shopping / Custom label 0", "Google Shopping / Custom label 1",
    "Google Shopping / Custom label 2", "Google Shopping / Custom label 3",
    "Google Shopping / Custom label 4"
]

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
    
    # Clean data to prevent missing columns from breaking export
    for col in ['color', 'size', 'barcode']:
        if col not in edited_export_df.columns:
            edited_export_df[col] = ''
            
    edited_export_df['color'] = edited_export_df['color'].fillna('')
    edited_export_df['size'] = edited_export_df['size'].fillna('')
    edited_export_df['barcode'] = edited_export_df['barcode'].fillna('')

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
    toast_df['barcode'] = toast_df['barcode'].apply(excel_safe_barcode)
    toast_output = toast_df[['name','pos name','category group','category','subcategory','price','cost','barcode','supplier']]

    # --- 3. SHOPIFY CONVERTER LOGIC (PARENT/CHILD VARIANT HIERARCHY) ---
    shopify_rows = []
    grouped = edited_export_df.groupby('pos name', sort=False)
    
    for pos_name, group in grouped:
        is_first = True
        
        # Generate Shopify Handle
        pos_str = str(pos_name)
        handle = re.sub(r'[^a-z0-9 ]+', '', pos_str.lower()).strip()
        handle = re.sub(r
