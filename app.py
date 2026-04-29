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

# This CSS hides the Streamlit top menu, footer, and styles the buttons
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stButton>button { height: 3.5em; font-size: 18px; font-weight: bold; border-radius: 8px; }
    [data-testid="stMetricValue"] { font-size: 1.8rem; color: #1E88E5; }
    </style>
""", unsafe_allow_html=True)

# --- PASSWORD GATE ---
try:
    APP_PASSWORD = st.secrets["APP_PASSWORD"]
except KeyError:
    st.error("⚠️ App password not found in server secrets. Please contact Administrator.")
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
st.sidebar.title("⚙️ Instructions")
st.sidebar.markdown("""
1. Upload vendor invoice images.
2. Add context if needed (e.g., 'All items are new', 'Markup 4x').
3. Click Extract Data.
4. Review the data on screen.
5. Email Toast & Shopify files to the Back Office.
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
            try:
                backend_api_key = st.secrets["GEMINI_API_KEY"]
            except KeyError:
                st.error("⚠️ System Error: Gemini API Key missing from server secrets. Contact Administrator.")
                st.stop()
                
            with st.spinner("Analyzing invoices with Gemini Pro..."):
                try:
                    genai.configure(api_key=backend_api_key)
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
        handle = re.sub(r'\s+', '-', handle)
        
        # Determine the Option names based on the first variant
        first_row = group.iloc[0]
        c_val = str(first_row['color']).strip()
        s_val = str(first_row['size']).strip()
        
        if c_val and s_val:
            opt1_n, opt2_n = "Color", "Size"
        elif c_val and not s_val:
            opt1_n, opt2_n = "Color", ""
        elif not c_val and s_val:
            opt1_n, opt2_n = "Size", ""
        else:
            opt1_n, opt2_n = "Title", ""

        for _, row in group.iterrows():
            s_row = {col: "" for col in SHOPIFY_COLUMNS}
            s_row['URL handle'] = handle
            s_row['SKU'] = row['name']
            
            # Clean up Barcode (Remove the ="" formula so Shopify reads it normally)
            raw_bc = str(row['barcode']).replace('="', '').replace('"', '')
            s_row['Barcode'] = raw_bc
            
            s_row['Price'] = row['price']
            s_row['Cost per item'] = row['cost']
            
            # Variant Option Values
            r_color = str(row['color']).strip()
            r_size = str(row['size']).strip()
            
            if opt1_n == "Color" and opt2_n == "Size":
                s_row['Option1 value'] = r_color
                s_row['Option2 value'] = r_size
            elif opt1_n == "Color" and opt2_n == "":
                s_row['Option1 value'] = r_color
            elif opt1_n == "Size" and opt2_n == "":
                s_row['Option1 value'] = r_size
            else:
                s_row['Option1 value'] = "Default Title"
            
            s_row['Charge tax'] = 'TRUE'
            s_row['Fulfillment service'] = 'manual'
            s_row['Inventory tracker'] = 'shopify'
            s_row['Inventory quantity'] = '0'
            s_row['Continue selling when out of stock'] = 'deny'
            
            # Only fill these fields out for the FIRST row of the product
            if is_first:
                s_row['Title'] = pos_str.title()
                s_row['Vendor'] = row['supplier']
                s_row['Product category'] = "Apparel & Accessories"
                s_row['Type'] = row['category']
                s_row['Tags'] = f"{row['supplier']}, Retail" if row['supplier'] else "Retail"
                s_row['Published on online store'] = 'FALSE'
                s_row['Status'] = 'draft'
                
                s_row['Option1 name'] = opt1_n
                s_row['Option2 name'] = opt2_n
                
                is_first = False
            
            shopify_rows.append(s_row)
            
    shopify_output = pd.DataFrame(shopify_rows, columns=SHOPIFY_COLUMNS)

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
                shopify_bytes = shopify_output.to_csv(index=False).encode('utf-8')

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
            data=shopify_output.to_csv(index=False).encode('utf-8'),
            file_name=shopify_filename,
            mime="text/csv",
            use_container_width=True,
        )

    st.divider()

    if st.button("🔄 Scan a New Invoice", use_container_width=True):
        del st.session_state.invoice_data
        del st.session_state.current_files
        st.rerun()
