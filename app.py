import streamlit as st
import pandas as pd
import boto3

st.set_page_config(page_title="Vytis Invoice App")
st.title("Vytis Ltd. | Invoice-to-Toast Converter")
st.info("Upload a packing slip to extract data and format it for Toast Retail.")

# Securely load AWS credentials from your hidden vault
try:
    textract = boto3.client(
        'textract',
        aws_access_key_id=st.secrets["aws"]["access_key_id"],
        aws_secret_access_key=st.secrets["aws"]["secret_access_key"],
        region_name=st.secrets["aws"]["region"]
    )
except Exception as e:
    st.error(f"System Error: {e}")
    st.stop()

# Accepting images for the Textract Expense API
uploaded_file = st.file_uploader("Upload Packing Slip (JPG, PNG)", type=["png", "jpg", "jpeg"])

if uploaded_file:
        st.success(f"File '{uploaded_file.name}' uploaded! Sending to Amazon AI...")
        
        with st.spinner("Analyzing document..."):
            try:
                document_bytes = uploaded_file.read()
                # Calling the specific Textract API designed for receipts/invoices
                response = textract.analyze_expense(Document={'Bytes': document_bytes})
                
                items_list = []
                for expense_doc in response.get('ExpenseDocuments', []):
                    for line_item_group in expense_doc.get('LineItemGroups', []):
                        for line_item in line_item_group.get('LineItems', []):
                            # Setting up the exact columns Toast requires
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
                            
                            for field in line_item.get('LineItemExpenseFields', []):
                                field_type = field.get('Type', {}).get('Text')
                                field_val = field.get('ValueDetection', {}).get('Text')
                                
                                if field_type == 'ITEM':
                                    item_data["Item Name"] = field_val
                                elif field_type == 'QUANTITY':
                                    item_data["Item Quantity"] = field_val
                                elif field_type == 'PRODUCT_CODE':
                                    item_data["Supplier Item ID"] = field_val
                            
                            # Only add to our list if it actually found an item name
                            if item_data["Item Name"]: 
                                items_list.append(item_data)
                                
            except Exception as e:
                st.error(f"An error occurred: {e}")            
            if items_list:
                df = pd.DataFrame(items_list)
                st.subheader("1. Verify Extracted Data (Human-in-the-Loop)")
                edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)

                st.subheader("2. Export to Toast")
                toast_csv = edited_df[["Supplier Item ID", "Item Name", "Item Quantity", "Receiving Unit"]]
                
                st.download_button(
                    label="Download Toast-Ready CSV",
                    data=toast_csv.to_csv(index=False).encode('utf-8'),
                    file_name='toast_import.csv',
                    mime='text/csv',
                )
            else:
                st.warning("Textract couldn't find any clear line items. Try a different photo!")
        except Exception as e:
            st.error(f"An error occurred: {e}")