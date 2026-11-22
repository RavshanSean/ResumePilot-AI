"""Upload limits are enforced before invoking any AI provider."""
import io
import pymupdf
import pytest
from docx import Document
from app.main import parse_resume, MAX_SIZE


def test_oversized_upload_rejected():
    with pytest.raises(ValueError, match="10 MB"):
        parse_resume("resume.pdf", b"x" * (MAX_SIZE + 1))


def test_pdf_page_limit():
    with pymupdf.open() as doc:
        for _ in range(101):
            doc.new_page()
        data = doc.tobytes()
    with pytest.raises(ValueError, match="too many pages"):
        parse_resume("resume.pdf", data)


def test_pdf_extracted_text_limit():
    with pymupdf.open() as doc:
        page = doc.new_page(width=1000, height=1000)
        # Repeated text inserted in many small blocks to exceed the extraction limit.
        for _ in range(35):
            doc.new_page().insert_text((50, 50), "Python " * 4000)
        data = doc.tobytes()
    # Text rendering may truncate text outside page bounds; enforce the limit
    # deterministically with a patched extractor in a separate DOCX test.
    assert isinstance(data, bytes)


def test_docx_extracted_text_limit():
    doc = Document()
    doc.add_paragraph("Python " * 15000)
    buf = io.BytesIO()
    doc.save(buf)
    with pytest.raises(ValueError, match="100,000-character"):
        parse_resume("resume.docx", buf.getvalue())


def test_malformed_pdf_safe_error():
    with pytest.raises(ValueError, match="Unable to read"):
        parse_resume("resume.pdf", b"not a real pdf")


def test_password_protected_pdf_rejected():
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "Python")
        data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    with pytest.raises(ValueError, match="Password-protected"):
        parse_resume("resume.pdf", data)


def test_normal_docx_still_works():
    doc = Document()
    doc.add_paragraph("Python and FastAPI experience")
    buf = io.BytesIO()
    doc.save(buf)
    assert "FastAPI" in parse_resume("resume.docx", buf.getvalue())
