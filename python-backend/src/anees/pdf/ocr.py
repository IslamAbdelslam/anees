"""OCR engine: Tesseract primary, Nougat fallback for complex layouts.

Heavy dependencies (OpenCV, pytesseract, pdf2image, transformers) are imported
lazily so the routing logic and the module stay importable in lightweight
environments (CI installs only ruff + pytest).
"""

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from anees.models import ChunkData, TextSegment

logger = logging.getLogger("anees.ocr")

DEFAULT_DPI = 300
DEFAULT_LANGS = "ara+eng"
CHUNK_SIZE = 10
TESSERACT_CONFIG = "--psm 6 --oem 1"
OCR_CONFIDENCE_THRESHOLD = 0.6
MIN_EMBEDDED_CHARS = 100

LANG_CODES = {"ar": "ara", "en": "eng", "fa": "fas", "fr": "fra", "de": "deu"}

ProgressCallback = Callable[[int, int, str], None]


def tesseract_lang(langs: Sequence[str] | None) -> str:
    """Map ISO language codes (ar/en) to Tesseract codes (ara/eng)."""
    if not langs:
        return DEFAULT_LANGS
    return "+".join(LANG_CODES.get(lang, lang) for lang in langs)


def _detect_lang(text: str) -> str:
    if any("\u0600" <= c <= "\u06ff" for c in text):
        return "ar"
    if any("a" <= c.lower() <= "z" for c in text):
        return "en"
    return "mixed"


class NougatEngine:
    """Lazy wrapper around facebook/nougat-small for complex layouts.

    The model (~500MB) is downloaded on first use. Availability is reported
    honestly so callers can degrade to Tesseract when the optional
    ``transformers``/``torch`` extra is not installed.
    """

    MODEL_ID = "facebook/nougat-small"

    def __init__(self) -> None:
        self._processor: Any = None
        self._model: Any = None
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is None:
            try:
                import torch  # noqa: F401
                import transformers  # noqa: F401
            except ImportError:
                self._available = False
            else:
                self._available = True
        return self._available

    def _load(self) -> tuple[Any, Any]:
        if self._model is None:
            from transformers import NougatProcessor, VisionEncoderDecoderModel

            logger.info("Loading %s (download on first use)", self.MODEL_ID)
            self._processor = NougatProcessor.from_pretrained(self.MODEL_ID)
            self._model = VisionEncoderDecoderModel.from_pretrained(self.MODEL_ID)
        return self._processor, self._model

    def process(self, image: Any) -> str:
        """Run Nougat on one page image and return its markdown/LaTeX text."""
        import torch

        processor, model = self._load()
        pixel_values = processor(image, return_tensors="pt").pixel_values
        with torch.no_grad():
            outputs = model.generate(
                pixel_values,
                min_length=1,
                max_new_tokens=4096,
                bad_words_ids=[[processor.tokenizer.unk_token_id]],
            )
        sequence = processor.batch_decode(outputs, skip_special_tokens=True)[0]
        return str(processor.post_process_generation(sequence, fix_markdown=False))


class OCREngine:
    """Render pages at 300 DPI, OCR with Tesseract, fall back to Nougat.

    Smart routing (per page): embedded text from the extractor wins when it
    clears ``min_embedded_chars``; otherwise Tesseract runs, and a Tesseract
    confidence below ``confidence_threshold`` escalates to Nougat.
    """

    def __init__(
        self,
        dpi: int = DEFAULT_DPI,
        tesseract_path: str | None = None,
        extractor: Any | None = None,
        nougat: Any | None = None,
        confidence_threshold: float = OCR_CONFIDENCE_THRESHOLD,
        min_embedded_chars: int = MIN_EMBEDDED_CHARS,
        traineddata_dir: str | None = None,
    ) -> None:
        self.dpi = dpi
        self.tesseract_path = tesseract_path
        self.extractor = extractor
        self.nougat = nougat if nougat is not None else NougatEngine()
        self.confidence_threshold = confidence_threshold
        self.min_embedded_chars = min_embedded_chars
        self.traineddata_dir = traineddata_dir
        self._tesseract_checked = False
        self._nougat_available_cache: bool | None = None

    def _ensure_tesseract(self) -> None:
        if self._tesseract_checked:
            return
        import pytesseract

        if self.tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
        try:
            pytesseract.get_tesseract_version()
        except Exception:
            logger.exception("Tesseract not available")
        self._tesseract_checked = True

    def preprocess_image(self, image: Any) -> Any:
        """Deskew, denoise, and binarize a page for better Arabic OCR."""
        import cv2
        import numpy as np
        from PIL import Image

        img = np.array(image)
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        img = self._deskew(img)
        img = cv2.fastNlMeansDenoising(img, h=10)
        img = cv2.adaptiveThreshold(
            img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        return Image.fromarray(img)

    def _deskew(self, img: Any) -> Any:
        import cv2
        import numpy as np

        coords = np.column_stack(np.where(img > 0))
        if len(coords) < 10:
            return img

        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle

        if abs(angle) > 0.5:
            (h, w) = img.shape[:2]
            center = (w // 2, h // 2)
            matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            img = cv2.warpAffine(
                img, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
            )
        return img

    def _page_count(self, path: str) -> int:
        import fitz

        with fitz.open(path) as doc:
            return int(doc.page_count)

    def _render(self, path: str, page_range: list[int]) -> list[Any]:
        from pdf2image import convert_from_path

        return list(
            convert_from_path(
                path,
                dpi=self.dpi,
                first_page=page_range[0] + 1,
                last_page=page_range[1],
            )
        )

    def _embedded_text(self, path: str, start: int, end: int) -> dict[int, str]:
        if self.extractor is None:
            return {}
        chunks = self.extractor.extract(path, [start, end])
        return {seg.page: seg.text for chunk in chunks for seg in chunk.segments}

    def _tesseract(self, image: Any, lang: str) -> tuple[str, float]:
        import pytesseract

        self._ensure_tesseract()
        config = TESSERACT_CONFIG
        if self.traineddata_dir:
            config = f"--tessdata-dir {self.traineddata_dir} {config}"

        text = pytesseract.image_to_string(image, lang=lang, config=config)
        try:
            data = pytesseract.image_to_data(
                image,
                lang=lang,
                config=config,
                output_type=pytesseract.Output.DICT,
            )
            confidences = [int(c) for c in data["conf"] if int(c) > 0]
            confidence = sum(confidences) / len(confidences) / 100.0 if confidences else 0.0
        except Exception:
            logger.exception("Tesseract confidence query failed")
            confidence = 0.0
        return text.strip(), confidence

    def _nougat_available(self) -> bool:
        if self._nougat_available_cache is None:
            try:
                self._nougat_available_cache = bool(self.nougat and self.nougat.is_available())
            except Exception:
                logger.exception("Nougat availability check failed")
                self._nougat_available_cache = False
        return self._nougat_available_cache

    def _ocr_page(self, image: Any, original: Any, lang: str) -> tuple[str, float, str]:
        processed = self.preprocess_image(image)
        text, confidence = self._tesseract(processed, lang)
        engine = "tesseract"
        if confidence < self.confidence_threshold and self._nougat_available():
            try:
                text = self.nougat.process(original)
                confidence = 1.0
                engine = "nougat"
            except Exception:
                logger.exception("Nougat fallback failed; keeping Tesseract output")
        return text.strip(), confidence, engine

    def process(
        self,
        path: str,
        page_range: Sequence[int] | None = None,
        langs: list[str] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> list[ChunkData]:
        """OCR the given pages, returning the same shape as pdf.extract_text."""
        pdf_path = Path(path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        total_pages = self._page_count(str(pdf_path))
        start = max(0, int(page_range[0])) if page_range else 0
        end = min(total_pages, int(page_range[1])) if page_range else total_pages
        total = max(0, end - start)

        embedded = self._embedded_text(str(pdf_path), start, end)
        needs_ocr = [
            len(embedded.get(page_number + 1, "").strip()) < self.min_embedded_chars
            for page_number in range(start, end)
        ]

        images: list[Any] = []
        if any(needs_ocr):
            images = self._render(str(pdf_path), [start, end])

        lang = tesseract_lang(langs)
        segments: list[TextSegment] = []
        for index in range(total):
            page_number = start + index + 1
            if not needs_ocr[index]:
                text = embedded[page_number]
                confidence: float = 1.0
                engine = "text"
            else:
                image = images[index]
                text, confidence, engine = self._ocr_page(image, image, lang)

            segments.append(
                TextSegment(
                    page=page_number,
                    text=text,
                    lang=_detect_lang(text),
                    confidence=confidence,
                )
            )
            if on_progress is not None:
                on_progress(index + 1, total, f"{engine} page {page_number}")

        return self._chunk(start, total, segments)

    @staticmethod
    def _chunk(start: int, total: int, segments: list[TextSegment]) -> list[ChunkData]:
        chunks: list[ChunkData] = []
        for chunk_start in range(0, total, CHUNK_SIZE):
            chunk_end = min(chunk_start + CHUNK_SIZE, total)
            chunks.append(
                ChunkData(
                    chunk_index=chunk_start // CHUNK_SIZE,
                    page_range=[start + chunk_start + 1, start + chunk_end],
                    segments=segments[chunk_start:chunk_end],
                    status="completed",
                    progress=1.0,
                )
            )
        return chunks
