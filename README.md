# Retail Invoice Scanner

A Streamlit app that turns a photo of a vendor packing slip or invoice into two ready-to-upload files for a retail POS system: an item-library import for genuinely new products, and a receiving import that logs the shipment against inventory. Built for and used in production at a small retail business I run the finance/ops function for.

## The problem

Adding a wholesale shipment to the POS system by hand meant retyping every line from a paper invoice into a rigid ~70-column import template, then separately searching for and receiving each item one at a time in the POS system's own receiving screen — the real bottleneck, not the data entry itself.

## What it does

- Takes a photo of an invoice, sends it to a vision-capable LLM with a tightly specified extraction prompt (exact column list, exact naming convention for SKUs, explicit handling for handwritten corrections vs. printed values), and gets back structured line items.
- Cross-checks every extracted line against a live mirror of the real POS catalog (barcode match, then SKU match, then exact name match, then fuzzy match) so it doesn't silently suggest re-creating an item that already exists — flagged for human review instead of auto-excluded or auto-created.
- Builds two separate export files with different column requirements, because the POS vendor's own item-library import and its receiving import are different templates with different matching logic — treating them as one file was the original app's design mistake.
- Emails both files to the back office or offers direct download.

## Real bugs found and fixed after shipping

This is the most heavily field-tested piece in this portfolio, and the module docstring in `app.py` documents each fix in place rather than hiding the history:

- A barcode-corruption bug traced to an Excel formula trick that only evaluates inside a spreadsheet app — anything reading the raw file bytes directly (including the POS importer itself) saw the literal formula text instead of the barcode. Fixed by switching to a real `.xlsx` with the barcode column's cell format forced to Text.
- A receiving-import matching failure discovered only through real use: the POS vendor's own documentation suggested one column as the primary match key, but real-world testing showed the vendor's receiving screen actually matches on a different field entirely. Re-verified against the vendor's own support docs before changing the logic, not guessed.
- An email-attachment bug where files arrived with no filename or recognizable type, traced to an untyped MIME attachment class not setting the content type where some mail clients actually look for it.

## My role

I specified the exact extraction rules (what counts as "new," the SKU naming convention, how to resolve a handwritten correction against a printed value), directed the AI-assisted implementation, and used this app myself in production — which is how each of the three bugs above actually surfaced and got root-caused rather than papered over.

## Stack

Python, Streamlit, a vision-capable LLM API for extraction, `pandas`/`openpyxl` for the exports, `gspread` for the live catalog mirror, `smtplib` for delivery.

*Business name and internal catalog figures have been genericized. Logic and structure are unchanged from what runs in production.*
