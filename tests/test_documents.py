from pathlib import Path
import fitz
import pytest
from shared.platform import extract_resume_text


def test_synthetic_docx_extraction():
    path=Path(__file__).parents[1]/"sample-data"/"resumes"/"candidate-01.docx"
    text,warnings=extract_resume_text(str(path))
    assert "Avery Chen" in text
    assert "FastAPI" in text
    assert not warnings

def test_scanned_pdf_is_flagged_for_ocr(tmp_path):
    path=tmp_path/"scanned.pdf"
    doc=fitz.open();doc.new_page();doc.save(path);doc.close()
    text,warnings=extract_resume_text(str(path))
    assert text==""
    assert any("OCR is required" in item for item in warnings)

def test_corrupt_docx_is_not_reported_as_parsed(tmp_path):
    path=tmp_path/"broken.docx";path.write_bytes(b"not a docx")
    with pytest.raises(Exception): extract_resume_text(str(path))
