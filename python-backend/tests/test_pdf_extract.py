"""PDF text extraction tests (Issue #8).

Skipped when the backend PDF dependencies (PyMuPDF, pdfplumber) are absent:
CI's lightweight Python job installs only ruff+pytest, so these run wherever
the full backend environment is present.
"""

import asyncio
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

fitz = pytest.importorskip("fitz")
pytest.importorskip("pdfplumber")

from anees.pdf.extractor import PDFExtractor

try:
    import anees.server  # noqa: F401

    _HAS_SERVER = True
except ImportError:
    _HAS_SERVER = False


def _make_text_pdf(path: Path, pages: list[str]) -> None:
    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _make_table_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    for x in (72, 272, 472):
        page.draw_line((x, 72), (x, 142))
    for y in (72, 107, 142):
        page.draw_line((72, y), (472, y))
    page.insert_text((80, 95), "Name")
    page.insert_text((280, 95), "Age")
    page.insert_text((80, 130), "Ali")
    page.insert_text((280, 130), "30")
    doc.save(str(path))
    doc.close()


def _word(text: str, x0: float, x1: float, top: float = 100.0, bottom: float = 112.0) -> dict:
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": bottom, "height": bottom - top}


def test_extract_returns_segments_with_bbox_and_lang(tmp_path: Path) -> None:
    path = tmp_path / "en.pdf"
    _make_text_pdf(path, ["Hello world English text"])

    chunks = PDFExtractor().extract(str(path))

    assert len(chunks) == 1
    assert chunks[0].page_range == [1, 1]
    segment = chunks[0].segments[0]
    assert segment.page == 1
    assert "Hello world" in segment.text
    assert segment.lang == "en"
    bbox = segment.bbox
    assert bbox is not None
    assert len(bbox) == 4
    assert bbox[2] > 0
    assert bbox[3] > 0


def test_page_range_is_zero_based_and_output_pages_one_based(tmp_path: Path) -> None:
    path = tmp_path / "multi.pdf"
    _make_text_pdf(path, ["page one", "page two", "page three"])

    chunks = PDFExtractor().extract(str(path), page_range=[1, 3])

    pages = [s.page for c in chunks for s in c.segments]
    assert pages == [2, 3]
    assert chunks[0].page_range == [2, 3]


def test_langs_filter_drops_non_matching_segments(tmp_path: Path) -> None:
    path = tmp_path / "en.pdf"
    _make_text_pdf(path, ["Only English here"])
    extractor = PDFExtractor()

    assert extractor.extract(str(path), langs=["ar"])[0].segments == []
    assert extractor.extract(str(path), langs=["en"])[0].segments != []


def test_empty_page_yields_chunk_without_segments(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()

    chunks = PDFExtractor().extract(str(path))

    assert len(chunks) == 1
    assert chunks[0].segments == []


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        PDFExtractor().extract("/no/such/file.pdf")


def test_simple_page_detection(tmp_path: Path) -> None:
    simple = tmp_path / "simple.pdf"
    _make_text_pdf(simple, ["plain text"])
    table = tmp_path / "table.pdf"
    _make_table_pdf(table)

    with fitz.open(simple) as doc:
        assert PDFExtractor()._is_simple_page(doc[0]) is True
    with fitz.open(table) as doc:
        assert PDFExtractor()._is_simple_page(doc[0]) is False


def test_table_page_uses_layout_aware_extraction(tmp_path: Path) -> None:
    path = tmp_path / "table.pdf"
    _make_table_pdf(path)

    chunks = PDFExtractor().extract(str(path))

    texts = [s.text for c in chunks for s in c.segments]
    assert any("Name" in t and "Age" in t for t in texts)
    assert any("Ali" in t for t in texts)


def test_rtl_line_reads_right_to_left() -> None:
    line = [_word("واحد", 50, 80), _word("اثنان", 90, 130), _word("ثلاثة", 140, 190)]

    spans = PDFExtractor()._line_spans(line, "ar", 1)

    assert [s.lang for s in spans] == ["ar"]
    assert spans[0].text == "ثلاثة اثنان واحد"


def test_bidi_line_splits_into_script_spans() -> None:
    line = [_word("Hello", 50, 100), _word("مرحبا", 200, 250), _word("World", 300, 350)]

    spans = PDFExtractor()._line_spans(line, "en", 1)

    assert [s.lang for s in spans] == ["en", "ar", "en"]
    assert [s.text for s in spans] == ["Hello", "مرحبا", "World"]


@pytest.mark.skipif(not _HAS_SERVER, reason="backend dependencies missing")
def test_extract_text_rpc_contract(tmp_path: Path) -> None:
    from anees.server import AneesRPCServer

    path = tmp_path / "en.pdf"
    _make_text_pdf(path, ["RPC extraction"])
    server = AneesRPCServer()

    result = asyncio.run(
        server.extract_text({"path": str(path), "page_range": [0, 1], "langs": ["en"]})
    )

    chunk = result["chunks"][0]
    assert chunk["page_range"] == [1, 1]
    assert chunk["segments"][0]["page"] == 1
    assert "RPC extraction" in chunk["segments"][0]["text"]
