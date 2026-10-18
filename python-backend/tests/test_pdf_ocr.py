"""OCR engine tests (Issue #9).

Smart-routing tests use stubs (no OpenCV/poppler needed); the end-to-end OCR
test is skipped where Tesseract/pdf2image are absent, mirroring
``test_pdf_extract.py`` so CI (ruff + pytest only) stays green.
"""

import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

fitz = pytest.importorskip("fitz")
pytest.importorskip("pdfplumber")

from anees.models import ChunkData, TextSegment
from anees.pdf.ocr import (
    NougatEngine,
    OCREngine,
    resolve_tesseract_path,
    resolve_traineddata_dir,
    tesseract_lang,
)

SERVER_DEPENDENCIES = (
    "docx",
    "jinja2",
    "camel_tools",
    "fasttext",
    "onnxruntime",
    "cv2",
    "pytesseract",
    "pdf2image",
)

LATIN_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

ARABIC_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
]


def _blank_pdf(path: Path, pages: int = 1) -> None:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()


def _text_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _scanned_pdf(
    path: Path,
    text: str,
    font_candidates: list[str] = LATIN_FONT_CANDIDATES,
    size: tuple[int, int] = (1400, 300),
) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font_path = next((p for p in font_candidates if Path(p).exists()), None)
    if font_path is None:
        pytest.skip("no TrueType font available to render the scanned page")
    font = ImageFont.truetype(font_path, 64)
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).text((40, 90), text, fill="black", font=font)
    png = path.with_suffix(".png")
    image.save(png)

    doc = fitz.open()
    page = doc.new_page(width=size[0], height=size[1])
    page.insert_image(page.rect, filename=str(png))
    doc.save(str(path))
    doc.close()


class _StubExtractor:
    def __init__(self, texts: dict[int, str]) -> None:
        self.texts = texts

    def extract(
        self,
        path: str,
        page_range: Sequence[int] | None = None,
        langs: list[str] | None = None,
    ) -> list[ChunkData]:
        return [
            ChunkData(
                chunk_index=0,
                page_range=[1, len(self.texts)],
                segments=[
                    TextSegment(page=page, text=text, lang="en")
                    for page, text in self.texts.items()
                ],
            )
        ]


class _StubNougat:
    def __init__(self, available: bool = True, text: str = "nougat output") -> None:
        self.available = available
        self.text = text
        self.calls = 0

    def is_available(self) -> bool:
        return self.available

    def process(self, image: Any) -> str:
        self.calls += 1
        return self.text


class _RoutingEngine(OCREngine):
    """OCREngine with rendering/preprocessing/tesseract replaced by stubs."""

    def __init__(
        self,
        *args: Any,
        tesseract_result: tuple[str, float] = ("tess text", 0.9),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.tesseract_result = tesseract_result
        self.tesseract_calls = 0

    def _render(self, path: str, page_range: list[int]) -> list[Any]:
        return [object() for _ in range(max(0, page_range[1] - page_range[0]))]

    def preprocess_image(self, image: Any) -> Any:
        return image

    def _tesseract(self, image: Any, lang: str) -> tuple[str, float]:
        self.tesseract_calls += 1
        return self.tesseract_result


def test_tesseract_lang_maps_iso_codes() -> None:
    assert tesseract_lang(None) == "ara+eng"
    assert tesseract_lang(["ar", "en"]) == "ara+eng"
    assert tesseract_lang(["fa"]) == "fas"
    assert tesseract_lang(["eng"]) == "eng"


def test_embedded_text_skips_ocr(tmp_path: Path) -> None:
    path = tmp_path / "text.pdf"
    _text_pdf(path, "embedded text page")
    engine = _RoutingEngine(extractor=_StubExtractor({1: "embedded " * 30}))

    segments = engine.process(str(path))[0].segments

    assert engine.tesseract_calls == 0
    assert segments[0].text.startswith("embedded")
    assert segments[0].confidence == 1.0


def test_low_confidence_escalates_to_nougat(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    _blank_pdf(path)
    nougat = _StubNougat(available=True, text="nougat academic text")
    engine = _RoutingEngine(
        extractor=_StubExtractor({}), nougat=nougat, tesseract_result=("tess", 0.2)
    )

    segments = engine.process(str(path))[0].segments

    assert engine.tesseract_calls == 1
    assert nougat.calls == 1
    assert segments[0].text == "nougat academic text"
    assert segments[0].confidence == 1.0


def test_high_confidence_keeps_tesseract(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    _blank_pdf(path)
    nougat = _StubNougat(available=True)
    engine = _RoutingEngine(
        extractor=_StubExtractor({}), nougat=nougat, tesseract_result=("tess text", 0.95)
    )

    segments = engine.process(str(path))[0].segments

    assert segments[0].text == "tess text"
    assert nougat.calls == 0


def test_low_confidence_without_nougat_keeps_tesseract(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    _blank_pdf(path)
    nougat = _StubNougat(available=False)
    engine = _RoutingEngine(
        extractor=_StubExtractor({}), nougat=nougat, tesseract_result=("tess text", 0.1)
    )

    segments = engine.process(str(path))[0].segments

    assert segments[0].text == "tess text"
    assert nougat.calls == 0


def test_progress_callback_reports_each_page(tmp_path: Path) -> None:
    path = tmp_path / "three.pdf"
    _blank_pdf(path, pages=3)
    engine = _RoutingEngine(extractor=_StubExtractor({}))
    calls: list[tuple[int, int, str]] = []

    engine.process(str(path), on_progress=lambda p, t, m: calls.append((p, t, m)))

    assert [c[0] for c in calls] == [1, 2, 3]
    assert all(c[1] == 3 for c in calls)


def test_page_range_offsets_output_pages(tmp_path: Path) -> None:
    path = tmp_path / "three.pdf"
    _blank_pdf(path, pages=3)
    engine = _RoutingEngine(extractor=_StubExtractor({}))

    chunks = engine.process(str(path), page_range=[1, 3])

    assert chunks[0].page_range == [2, 3]
    assert [s.page for s in chunks[0].segments] == [2, 3]


def test_scanned_pdf_end_to_end(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    pytest.importorskip("pytesseract")
    pytest.importorskip("pdf2image")

    path = tmp_path / "scan.pdf"
    _scanned_pdf(path, "Hello OCR World")

    result = {"chunks": [c.model_dump() for c in OCREngine().process(str(path), langs=["en"])]}

    chunk = result["chunks"][0]
    assert chunk["page_range"] == [1, 1]
    segment = chunk["segments"][0]
    assert {"page", "text", "lang", "confidence"} <= set(segment)
    assert segment["page"] == 1
    assert "OCR" in segment["text"] or "Hello" in segment["text"]
    assert segment["lang"] == "en"
    assert segment["confidence"] > 0


def test_ocr_rpc_contract(tmp_path: Path) -> None:
    for module in SERVER_DEPENDENCIES:
        pytest.importorskip(module)
    from anees.server import AneesRPCServer

    path = tmp_path / "scan.pdf"
    _scanned_pdf(path, "RPC OCR World")
    server = AneesRPCServer()

    result = asyncio.run(server.ocr_pdf({"path": str(path), "page_range": [0, 1], "langs": ["en"]}))

    chunk = result["chunks"][0]
    assert chunk["page_range"] == [1, 1]
    assert chunk["segments"][0]["page"] == 1


def test_resolve_assets_prefers_explicit_then_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert resolve_traineddata_dir(str(tmp_path)) == str(tmp_path)
    assert resolve_tesseract_path("/opt/tesseract") == "/opt/tesseract"

    monkeypatch.setenv("ANEES_TESSDATA", "/custom/tessdata")
    monkeypatch.setenv("ANEES_TESSERACT", "/custom/tesseract")
    assert resolve_traineddata_dir() == "/custom/tessdata"
    assert resolve_tesseract_path() == "/custom/tesseract"


def test_deskew_corrects_skewed_scan() -> None:
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    engine = OCREngine()

    image = numpy.full((300, 600), 255, dtype=numpy.uint8)
    for y in range(60, 240, 40):
        cv2.line(image, (50, y), (550, y), 0, 3)
    rotation = cv2.getRotationMatrix2D((300, 150), 7.0, 1.0)
    skewed = cv2.warpAffine(image, rotation, (600, 300), borderValue=255)

    before = abs(engine._skew_angle(skewed))
    after = abs(engine._skew_angle(engine._deskew(skewed)))

    assert before > 3.0
    assert after < 1.0


def _has_traineddata(code: str) -> bool:
    directory = resolve_traineddata_dir()
    if directory and (Path(directory) / f"{code}.traineddata").is_file():
        return True
    import pytesseract

    try:
        return code in pytesseract.get_languages(config="")
    except (pytesseract.TesseractError, OSError):
        return False


def test_scanned_pdf_arabic(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    pytest.importorskip("pytesseract")
    pytest.importorskip("pdf2image")
    if not _has_traineddata("ara"):
        pytest.skip("Tesseract 'ara' traineddata is not installed")

    path = tmp_path / "arabic.pdf"
    _scanned_pdf(path, "مرحبا بالعالم", font_candidates=ARABIC_FONT_CANDIDATES)

    segment = OCREngine().process(str(path), langs=["ar", "en"])[0].segments[0]

    assert segment.lang == "ar"
    assert "مرحبا" in segment.text


def test_nougat_integration_when_optional_extra_installed() -> None:
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from PIL import Image, ImageDraw, ImageFont

    engine = NougatEngine()
    assert engine.is_available()

    font_path = next((p for p in LATIN_FONT_CANDIDATES if Path(p).exists()), None)
    if font_path is None:
        pytest.skip("no TrueType font available to render a page")
    page = Image.new("RGB", (896, 300), "white")
    ImageDraw.Draw(page).text(
        (40, 90), "Hello Nougat World", fill="black", font=ImageFont.truetype(font_path, 64)
    )

    assert isinstance(engine.process(page), str)
