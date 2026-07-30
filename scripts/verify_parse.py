"""Create a test PDF and run the three-tier parsing chain on it."""
import os, sys, time

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fpdf import FPDF

# Step 1: Create test PDF
pdf_path = os.path.join(os.path.dirname(__file__), "supply_chain_qa_test.pdf")
pdf = FPDF()
pdf.add_page()
pdf.set_font("Helvetica", size=12)
pdf.cell(200, 10, text="Supply Chain QA Test PDF - Supply Chain Report", align="C")
pdf.ln(15)
pdf.set_font("Helvetica", size=10)
pdf.multi_cell(0, 6, text="This is a test PDF document for the Supply Chain QA document parsing pipeline. It validates the three-tier fallback chain: opendataloader -> pymupdf4llm -> pdfplumber.")
pdf.ln(10)
# Table
pdf.set_font("Helvetica", "B", size=10)
for h in ["Material", "Quantity", "Price", "Total"]:
    pdf.cell(40, 8, h, border=1)
pdf.ln()
pdf.set_font("Helvetica", size=10)
for row in [("MAT-001", "500", "12.50", "6250.00"), ("MAT-002", "300", "8.90", "2670.00"), ("MAT-003", "1200", "3.40", "4080.00")]:
    for cell in row:
        pdf.cell(40, 8, cell, border=1)
    pdf.ln()
pdf.output(pdf_path)
print(f"[CREATE] {pdf_path} ({os.path.getsize(pdf_path)} bytes)")

# Step 2: Test _read_pdf directly
os.chdir(os.path.join(os.path.dirname(__file__), "..", "backend"))
from app.api.knowledge import _read_pdf, _check_java

has_java = _check_java()
print(f"[JAVA] available={has_java}")

t0 = time.time()
text = _read_pdf(pdf_path)
elapsed = time.time() - t0

print(f"[PARSE] {len(text)} chars in {elapsed:.2f}s")
print(f"[TABLE] {'|' in text and '---' in text}")
print(f"[PREVIEW] {text[:300]}...")
