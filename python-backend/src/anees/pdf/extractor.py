"""PDF text extraction using pdfplumber and PyMuPDF"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pdfplumber

from anees.models import ChunkData, PdfInfo, TextSegment

logger = logging.getLogger("anees.pdf.extractor")

CHUNK_SIZE = 10


def _character_script(char: str) -> str:
    codepoint = ord(char)
    if (
        0x0600 <= codepoint <= 0x06FF
        or 0x0750 <= codepoint <= 0x077F
        or 0x08A0 <= codepoint <= 0x08FF
        or 0xFB50 <= codepoint <= 0xFDFF
        or 0xFE70 <= codepoint <= 0xFEFF
    ):
        return "ar"
    if char.isalpha() and codepoint < 0x0250:
        return "en"
    return "neutral"


def _word_script(text: str) -> str:
    arabic = sum(1 for c in text if _character_script(c) == "ar")
    latin = sum(1 for c in text if _character_script(c) == "en")
    if arabic and latin:
        return "ar" if arabic >= latin else "en"
    if arabic:
        return "ar"
    if latin:
        return "en"
    return "neutral"


def _words_bbox(words: list[dict[str, Any]]) -> list[float]:
    x0 = min(w["x0"] for w in words)
    x1 = max(w["x1"] for w in words)
    top = min(w["top"] for w in words)
    bottom = max(w["bottom"] for w in words)
    return [x0, top, x1 - x0, bottom - top]


def _center_inside(word: dict[str, Any], bbox: list[float]) -> bool:
    x0, top, width, height = bbox
    center_x = (float(word["x0"]) + float(word["x1"])) / 2
    center_y = (float(word["top"]) + float(word["bottom"])) / 2
    return x0 <= center_x <= x0 + width and top <= center_y <= top + height


class PDFExtractor:
    def __init__(
        self,
        dpi: int = 300,
        table_settings: dict[str, Any] | None = None,
        x_tolerance: float = 1.5,
        y_tolerance: float = 3.0,
    ) -> None:
        self.dpi = dpi
        self.table_settings: dict[str, Any] = table_settings or {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "edge_min_length": 3,
        }
        self.x_tolerance = x_tolerance
        self.y_tolerance = y_tolerance

    def validate(self, path: str) -> PdfInfo:
        """Validate PDF and return metadata"""
        pdf_path = Path(path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        file_size = pdf_path.stat().st_size

        try:
            with fitz.open(pdf_path) as doc:
                if doc.needs_pass:
                    return PdfInfo(
                        valid=True,
                        pages=doc.page_count,
                        has_text=False,
                        file_size=file_size,
                        encrypted=True,
                        detected_langs=[],
                    )

                has_text = False
                detected_langs = []

                for i, page in enumerate(doc):
                    text = page.get_text().strip()
                    if text:
                        has_text = True
                    if i < 5:
                        # Simple script detection on the sampled pages
                        if (
                            text
                            and "ar" not in detected_langs
                            and any("\u0600" <= c <= "\u06ff" for c in text)
                        ):
                            detected_langs.append("ar")
                        if (
                            text
                            and "en" not in detected_langs
                            and any("a" <= c.lower() <= "z" for c in text)
                        ):
                            detected_langs.append("en")
                    elif has_text:
                        break

                return PdfInfo(
                    valid=True,
                    pages=len(doc),
                    has_text=has_text,
                    file_size=file_size,
                    encrypted=doc.is_encrypted,
                    detected_langs=detected_langs,
                )
        except (RuntimeError, ValueError):
            return PdfInfo(
                valid=False,
                pages=0,
                has_text=False,
                file_size=file_size,
                encrypted=False,
                detected_langs=[],
            )

    def extract(
        self,
        path: str,
        page_range: Sequence[int] | None = None,
        langs: list[str] | None = None,
    ) -> list[ChunkData]:
        """Extract text page by page, preserving layout and reading order"""
        pdf_path = Path(path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        lang_filter = set(langs or [])

        with fitz.open(pdf_path) as doc:
            start, end = self._clamp(page_range, doc.page_count)
            chunks: list[ChunkData] = []
            pdf = None
            try:
                for chunk_start in range(start, end, CHUNK_SIZE):
                    chunk_end = min(chunk_start + CHUNK_SIZE, end)
                    segments: list[TextSegment] = []
                    for page_num in range(chunk_start, chunk_end):
                        fitz_page = doc[page_num]
                        fitz_words = self._fitz_words(fitz_page)
                        if self._is_simple_page(fitz_page, fitz_words):
                            page_words = fitz_words
                            pdf_page: Any = None
                            page_width = float(fitz_page.rect.width)
                        else:
                            if pdf is None:
                                pdf = pdfplumber.open(pdf_path)
                            pdf_page = pdf.pages[page_num]
                            page_words = self._pdfplumber_words(pdf_page)
                            page_width = float(pdf_page.width)
                        segments.extend(
                            self._page_segments(
                                page_words, pdf_page, page_width, page_num + 1, lang_filter
                            )
                        )
                    chunks.append(
                        ChunkData(
                            chunk_index=(chunk_start - start) // CHUNK_SIZE,
                            page_range=[chunk_start + 1, chunk_end],
                            segments=segments,
                            status="completed",
                            progress=1.0,
                        )
                    )
            finally:
                if pdf is not None:
                    pdf.close()

        return chunks

    def _clamp(self, page_range: Sequence[int] | None, total: int) -> tuple[int, int]:
        if page_range is None:
            return 0, total
        start = max(0, int(page_range[0]))
        end = min(total, int(page_range[1]))
        return start, end

    def _is_simple_page(self, page: Any, words: list[dict[str, Any]] | None = None) -> bool:
        """A page is simple when it has no vector graphics and a single column"""
        if page.get_drawings():
            return False
        if words is None:
            words = self._fitz_words(page)
        if not words:
            return True
        return len(self._detect_columns(words, float(page.rect.width))) <= 1

    def _detect_columns(
        self, words: list[dict[str, Any]], page_width: float
    ) -> list[tuple[float, float]]:
        """Split a page into x-bands separated by vertical whitespace gutters"""
        if not words:
            return [(0.0, page_width)]
        left = min(w["x0"] for w in words)
        right = max(w["x1"] for w in words)
        span = right - left
        if span <= 0:
            return [(left, right)]

        bins = int(span) + 1
        coverage = [0] * bins
        for word in words:
            start = max(0, int(word["x0"] - left))
            end = min(bins - 1, int(word["x1"] - left))
            for index in range(start, end + 1):
                coverage[index] += 1

        min_gutter = max(15.0, span * 0.025)
        bands: list[tuple[float, float]] = []
        band_start = left
        run_start = None
        for index in range(bins):
            if coverage[index] == 0:
                if run_start is None:
                    run_start = index
            elif run_start is not None:
                if index - run_start >= min_gutter:
                    boundary = left + (run_start + index) / 2
                    bands.append((band_start, boundary))
                    band_start = boundary
                run_start = None
        bands.append((band_start, right))
        return bands

    def _column_index(self, center: float, bands: list[tuple[float, float]]) -> int:
        for index in range(len(bands) - 1):
            if center < bands[index][1]:
                return index
        return len(bands) - 1

    @staticmethod
    def _column_rank(index: int, count: int, rtl: bool) -> int:
        return count - 1 - index if rtl else index

    def _fitz_words(self, page: Any) -> list[dict[str, Any]]:
        words = []
        for x0, y0, x1, y1, text, *_ in page.get_text("words"):
            if text.strip():
                words.append(
                    {
                        "text": text,
                        "x0": x0,
                        "x1": x1,
                        "top": y0,
                        "bottom": y1,
                        "height": y1 - y0,
                    }
                )
        return words

    def _pdfplumber_words(self, page: Any) -> list[dict[str, Any]]:
        words = page.extract_words(
            keep_blank_chars=False,
            use_text_flow=False,
            x_tolerance=self.x_tolerance,
            y_tolerance=self.y_tolerance,
        )
        return [
            {
                "text": w["text"],
                "x0": w["x0"],
                "x1": w["x1"],
                "top": w["top"],
                "bottom": w["bottom"],
                "height": w["bottom"] - w["top"],
            }
            for w in words
            if w["text"].strip()
        ]

    def _page_segments(
        self,
        words: list[dict[str, Any]],
        pdf_page: Any,
        page_width: float,
        page_num: int,
        lang_filter: set[str],
    ) -> list[TextSegment]:
        table_spans: list[TextSegment] = []
        if pdf_page is not None:
            table_spans, table_bboxes = self._table_spans(pdf_page, page_num)
            if table_bboxes:
                words = [w for w in words if not any(_center_inside(w, b) for b in table_bboxes)]

        bands = self._detect_columns(words, page_width)
        page_lang = self._dominant_lang(words)
        rtl = page_lang == "ar"
        count = len(bands)

        items: list[tuple[tuple[int, float], list[TextSegment]]] = []

        for span in table_spans:
            bbox = span.bbox
            if bbox is None:
                continue
            index = self._column_index(bbox[0] + bbox[2] / 2, bands)
            items.append(((self._column_rank(index, count, rtl), bbox[1]), [span]))

        column_words: dict[int, list[dict[str, Any]]] = {index: [] for index in range(count)}
        for word in words:
            center = (word["x0"] + word["x1"]) / 2
            column_words[self._column_index(center, bands)].append(word)

        for index in range(count):
            rank = self._column_rank(index, count, rtl)
            for line in self._group_lines(column_words[index]):
                spans = self._line_spans(line, page_lang, page_num)
                tops = [s.bbox[1] for s in spans if s.bbox]
                if tops:
                    items.append(((rank, min(tops)), spans))

        items.sort(key=lambda item: item[0])

        segments: list[TextSegment] = []
        for _, spans in items:
            for span in spans:
                if lang_filter and span.lang not in lang_filter:
                    continue
                segments.append(span)
        return segments

    def _group_lines(self, words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        lines: list[list[dict[str, Any]]] = []
        for word in sorted(words, key=lambda w: ((w["top"] + w["bottom"]) / 2, w["x0"])):
            center = (word["top"] + word["bottom"]) / 2
            if lines:
                last = lines[-1]
                reference = sum((w["top"] + w["bottom"]) / 2 for w in last) / len(last)
                tolerance = max(word["height"], last[0]["height"]) * 0.6
                if abs(center - reference) <= tolerance:
                    last.append(word)
                    continue
            lines.append([word])
        return lines

    def _dominant_lang(self, words: list[dict[str, Any]]) -> str:
        arabic = sum(1 for w in words if _word_script(w["text"]) == "ar")
        latin = sum(1 for w in words if _word_script(w["text"]) == "en")
        if arabic and arabic >= latin:
            return "ar"
        if latin:
            return "en"
        return "mixed"

    def _line_spans(
        self, line: list[dict[str, Any]], page_lang: str, page_num: int
    ) -> list[TextSegment]:
        arabic = sum(1 for w in line if _word_script(w["text"]) == "ar")
        latin = sum(1 for w in line if _word_script(w["text"]) == "en")
        line_lang = "ar" if (arabic and arabic >= latin) else ("en" if latin else page_lang)

        runs: list[tuple[str, list[dict[str, Any]]]] = []
        for word in sorted(line, key=lambda w: w["x0"]):
            script = _word_script(word["text"])
            lang = line_lang if script == "neutral" else script
            if runs and runs[-1][0] == lang:
                runs[-1][1].append(word)
            else:
                runs.append((lang, [word]))

        if line_lang == "ar":
            runs.reverse()

        spans = []
        for lang, run_words in runs:
            ordered = sorted(run_words, key=lambda w: w["x0"], reverse=(lang == "ar"))
            spans.append(
                TextSegment(
                    page=page_num,
                    text=" ".join(w["text"] for w in ordered),
                    lang=lang,
                    confidence=1.0,
                    bbox=_words_bbox(run_words),
                )
            )
        return spans

    def _table_spans(self, page: Any, page_num: int) -> tuple[list[TextSegment], list[list[float]]]:
        segments: list[TextSegment] = []
        bboxes: list[list[float]] = []
        try:
            tables = page.find_tables(table_settings=self.table_settings)
        except Exception:
            logger.exception("Table detection failed")
            return segments, bboxes

        for table in tables:
            tx0, ttop, tx1, tbottom = table.bbox
            bboxes.append([tx0, ttop, tx1 - tx0, tbottom - ttop])
            rows = table.extract()
            for index, cells in enumerate(rows):
                text = "  ".join(str(cell).strip() for cell in cells if cell and str(cell).strip())
                if not text:
                    continue
                cell_boxes = [c for c in table.rows[index].cells if c]
                if not cell_boxes:
                    continue
                x0 = min(c[0] for c in cell_boxes)
                top = min(c[1] for c in cell_boxes)
                x1 = max(c[2] for c in cell_boxes)
                bottom = max(c[3] for c in cell_boxes)
                lang = _word_script(text)
                if lang == "neutral":
                    lang = "en"
                segments.append(
                    TextSegment(
                        page=page_num,
                        text=text,
                        lang=lang,
                        confidence=1.0,
                        bbox=[x0, top, x1 - x0, bottom - top],
                    )
                )
        return segments, bboxes


class PDFChunker:
    def __init__(self, chunk_size: int = 10):
        self.chunk_size = chunk_size

    def split(self, total_pages: int) -> list[tuple[int, int]]:
        """Split pages into chunks"""
        chunks = []
        for start in range(0, total_pages, self.chunk_size):
            end = min(start + self.chunk_size, total_pages)
            chunks.append((start, end))
        return chunks
