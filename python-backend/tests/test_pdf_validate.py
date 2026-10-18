"""PDF validation tests (Issue #7).

Skipped when the backend PDF dependencies (PyMuPDF, pdfplumber) are absent:
CI's lightweight Python job installs only ruff+pytest, so these run wherever
the full backend environment is present.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

fitz = pytest.importorskip("fitz")
pytest.importorskip("pdfplumber")

from anees.models import PdfInfo
from anees.pdf.extractor import PDFExtractor

ARABIC_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
]


def _arabic_font() -> str | None:
    return next((p for p in ARABIC_FONT_CANDIDATES if Path(p).exists()), None)


def _make_pdf(path: Path, text: str, font: str | None = None) -> None:
    doc = fitz.open()
    page = doc.new_page()
    kwargs = {"fontname": "F0", "fontfile": font} if font else {}
    page.insert_text((72, 72), text, **kwargs)
    doc.save(str(path))
    doc.close()


def test_valid_text_pdf_reports_metadata(tmp_path: Path) -> None:
    path = tmp_path / "text.pdf"
    _make_pdf(path, "Hello world English text")

    info = PDFExtractor().validate(str(path))

    assert isinstance(info, PdfInfo)
    assert info.valid is True
    assert info.pages == 1
    assert info.has_text is True
    assert info.file_size == path.stat().st_size
    assert info.encrypted is False
    assert info.detected_langs == ["en"]


def test_arabic_page_detected(tmp_path: Path) -> None:
    font = _arabic_font()
    if font is None:
        pytest.skip("no Arabic-capable system font available")

    path = tmp_path / "ar.pdf"
    _make_pdf(path, "ما شاء الله", font=font)

    info = PDFExtractor().validate(str(path))

    assert info.valid is True
    assert "ar" in info.detected_langs


def test_empty_pdf_has_no_text(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()

    info = PDFExtractor().validate(str(path))

    assert info.valid is True
    assert info.has_text is False
    assert info.detected_langs == []


def test_text_past_sample_window_is_found(tmp_path: Path) -> None:
    path = tmp_path / "late.pdf"
    doc = fitz.open()
    for _ in range(5):
        doc.new_page()
    doc.new_page().insert_text((72, 72), "Text on the sixth page")
    doc.save(str(path))
    doc.close()

    info = PDFExtractor().validate(str(path))

    assert info.valid is True
    assert info.pages == 6
    assert info.has_text is True
    assert info.detected_langs == []


def test_corrupt_pdf_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4 not a real pdf")

    info = PDFExtractor().validate(str(path))

    assert info.valid is False
    assert info.pages == 0
    assert info.has_text is False
    assert info.file_size == path.stat().st_size


def test_encrypted_pdf_flagged(tmp_path: Path) -> None:
    path = tmp_path / "enc.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "secret")
    doc.save(str(path), encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    doc.close()

    info = PDFExtractor().validate(str(path))

    assert info.valid is True
    assert info.encrypted is True
    assert info.has_text is False


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        PDFExtractor().validate(str(tmp_path / "nope.pdf"))
