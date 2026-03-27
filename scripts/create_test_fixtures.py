"""Generate binary test fixtures for sub-project E tests."""
import pathlib

FIXTURES = pathlib.Path("tests/fixtures/docs")
FIXTURES.mkdir(parents=True, exist_ok=True)

# sample.txt
(FIXTURES / "sample.txt").write_text(
    "This is a sample plain text document.\n" * 60,
    encoding="utf-8",
)

# sample.md
(FIXTURES / "sample.md").write_text(
    "# Sample Markdown Document\n\n"
    "This document contains **bold** and _italic_ text.\n\n"
    "- Item one\n- Item two\n- Item three\n\n"
    "```python\nprint('hello')\n```\n\n"
    "More content here to ensure chunking.\n" * 20,
    encoding="utf-8",
)

# sample_pii.txt
(FIXTURES / "sample_pii.txt").write_text(
    "Contact: jean.dupont@example.com\nPhone: +33 6 12 34 56 78\nRegular content here.\n",
    encoding="utf-8",
)

# sample.pdf -- minimal valid PDF (3 pages)
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Test Document")
    for page_num in range(1, 4):
        c.drawString(72, 700, f"Page {page_num} of Test Document")
        c.drawString(72, 680, "Known keyword: nanobot-stack sample content")
        c.showPage()
    c.save()
    (FIXTURES / "sample.pdf").write_bytes(buf.getvalue())
    print("sample.pdf created with reportlab")
except ImportError:
    # Minimal PDF stub (not parseable but prevents file-not-found)
    (FIXTURES / "sample.pdf").write_bytes(b"%PDF-1.4\n%stub\n")
    print("sample.pdf: reportlab not available, stub created")

# sample.docx -- minimal DOCX with title and paragraphs
try:
    import docx
    document = docx.Document()
    document.core_properties.title = "Test DOCX Document"
    for i in range(1, 6):
        document.add_paragraph(f"Paragraph {i}: This is sample content for testing.")
    document.save(str(FIXTURES / "sample.docx"))
    print("sample.docx created")
except ImportError:
    (FIXTURES / "sample.docx").write_bytes(b"PK stub")
    print("sample.docx: python-docx not available, stub created")

print("Fixtures written to", FIXTURES)
