"""PDF text extraction using pdfplumber and PyMuPDF"""

import fitz  # PyMuPDF
import pdfplumber
from pathlib import Path
from typing import List, Optional, Tuple
from anees.models import PdfInfo, TextSegment, ChunkData


class PDFExtractor:
    def __init__(self, dpi: int = 300):
        self.dpi = dpi

    def validate(self, path: str) -> PdfInfo:
        """Validate PDF and return metadata"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")

        doc = fitz.open(path)
        has_text = False
        detected_langs = []

        # Sample first 5 pages for language detection
        for i, page in enumerate(doc):
            if i >= 5:
                break
            text = page.get_text().strip()
            if text:
                has_text = True
                # Simple script detection
                if any('\u0600' <= c <= '\u06FF' for c in text):
                    if "ar" not in detected_langs:
                        detected_langs.append("ar")
                if any('a' <= c.lower() <= 'z' for c in text):
                    if "en" not in detected_langs:
                        detected_langs.append("en")

        return PdfInfo(
            pages=len(doc),
            has_text=has_text,
            file_size=path.stat().st_size,
            encrypted=doc.is_encrypted,
            detected_langs=detected_langs,
        )

    def extract(
        self,
        path: str,
        page_range: Optional[Tuple[int, int]] = None,
        langs: Optional[List[str]] = None
    ) -> List[ChunkData]:
        """Extract text from PDF using pdfplumber (layout-aware)"""
        path = Path(path)
        doc = fitz.open(path)
        total_pages = len(doc)

        if page_range is None:
            page_range = (0, total_pages)

        start, end = page_range
        start = max(0, start)
        end = min(total_pages, end)

        with pdfplumber.open(path) as pdf:
            chunks = []
            chunk_size = 10

            for chunk_start in range(start, end, chunk_size):
                chunk_end = min(chunk_start + chunk_size, end)
                chunk_segments = []

                for page_num in range(chunk_start, chunk_end):
                    page = pdf.pages[page_num]

                    # Extract with layout preservation
                    text = page.extract_text(
                        layout=True,
                        x_tolerance=2,
                        y_tolerance=2
                    ) or ""

                    # Get tables
                    tables = page.extract_tables()
                    for table in tables:
                        # Convert table to text representation
                        table_text = "\n".join(["\t".join(str(cell) if cell else "" for cell in row) for row in table])
                        text += f"\n[TABLE]\n{table_text}\n[/TABLE]\n"

                    # Get bounding boxes for words
                    words = page.extract_words(
                        keep_blank_chars=False,
                        x_tolerance=2,
                        y_tolerance=2
                    ) or []

                    # Use pdfplumber's word positions for bbox
                    bbox = None
                    if words:
                        x_coords = [w["x0"] for w in words] + [w["x1"] for w in words]
                        y_coords = [w["top"] for w in words] + [w["bottom"] for w in words]
                        bbox = [min(x_coords), min(y_coords), max(x_coords) - min(x_coords), max(y_coords) - min(y_coords)]

                    # Determine language for this page
                    lang = "mixed"
                    if any('\u0600' <= c <= '\u06FF' for c in text):
                        lang = "ar"
                    elif any('a' <= c.lower() <= 'z' for c in text):
                        lang = "en"

                    if text.strip():
                        chunk_segments.append(TextSegment(
                            page=page_num + 1,
                            text=text.strip(),
                            lang=lang,
                            confidence=1.0,
                            bbox=bbox
                        ))

                chunks.append(ChunkData(
                    chunk_index=(chunk_start - start) // chunk_size,
                    page_range=[chunk_start + 1, chunk_end],
                    segments=chunk_segments,
                    status="completed",
                    progress=1.0
                ))

        return chunks


class PDFChunker:
    def __init__(self, chunk_size: int = 10):
        self.chunk_size = chunk_size

    def split(self, total_pages: int) -> List[Tuple[int, int]]:
        """Split pages into chunks"""
        chunks = []
        for start in range(0, total_pages, self.chunk_size):
            end = min(start + self.chunk_size, total_pages)
            chunks.append((start, end))
        return chunks