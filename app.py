import streamlit as st
import boto3
import pandas as pd

st.title("Mary Jane's Invoice Scanner")
st.write("Upload a vendor packing slip to generate a Toast/Shopify inventory CSV.")

# Connect to the secure AWS Vault
textract = boto3.client(
    'textract',
    aws_access_key_id=st.secrets["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=st.secrets["AWS_SECRET_ACCESS_KEY"],
    region_name=st.secrets["AWS_REGION"]
)

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
                            field_val = field.get('ValueDetection', {}).get('Text')
                            
                            if field_type == 'ITEM':
                                item_data["Item Name"] = field_val
                            elif field_type == 'QUANTITY':
                                item_data["Item Quantity"] = field_val
                            elif field_type == 'PRODUCT_CODE':
                                item_data["Supplier Item ID"] = field_val
                        
                        # Only keep rows that actually have an item name
                        if item_data["Item Name"]: 
                            items_list.append(item_data)
            
            # Display the grid and create the download button
            if items_list:
                df = pd.DataFrame(items_list)
                st.dataframe(df)
                
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="Download Toast CSV",
                    data=csv,
                    file_name="toast_inventory_upload.csv",
                    mime="text/csv",
                )
            else:
                st.warning("Amazon couldn't find any clear line items on this document.")
                
        except Exception as e:
            # This is where the SubscriptionRequiredException will trigger
            st.error(f"An error occurred: {e}")