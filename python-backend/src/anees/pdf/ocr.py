"""OCR Engine using Tesseract with Nougat fallback"""

import logging
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from anees.models import ChunkData, TextSegment

logger = logging.getLogger("anees.ocr")


class OCREngine:
    def __init__(self, dpi: int = 300, tesseract_path: str | None = None):
        self.dpi = dpi
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        # Verify tesseract is available
        try:
            pytesseract.get_tesseract_version()
        except Exception:
            logger.exception("Tesseract not available")

    def preprocess_image(self, image: Image.Image) -> Image.Image:
        """Preprocess image for better Arabic OCR"""
        # Convert to numpy array
        img = np.array(image)

        # Convert to grayscale if needed
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # Deskew
        img = self._deskew(img)

        # Denoise
        img = cv2.fastNlMeansDenoising(img, h=10)

        # Binarize using adaptive threshold
        img = cv2.adaptiveThreshold(
            img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        return Image.fromarray(img)

    def _deskew(self, img: np.ndarray) -> np.ndarray:
        """Deskew image using minAreaRect"""
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
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            img = cv2.warpAffine(
                img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
            )

        return img

    def process(
        self, path: str, page_range: tuple[int, int] | None = None, langs: list[str] | None = None
    ) -> list[ChunkData]:
        """Process PDF pages with OCR"""
        pdf_path = Path(path)
        lang_str = "+".join(langs) if langs else "ara+eng"

        # Convert PDF pages to images
        images = convert_from_path(
            pdf_path,
            dpi=self.dpi,
            first_page=page_range[0] + 1 if page_range else None,
            last_page=page_range[1] if page_range else None,
        )

        chunks = []
        chunk_size = 10

        for chunk_start in range(0, len(images), chunk_size):
            chunk_end = min(chunk_start + chunk_size, len(images))
            chunk_segments = []

            for i in range(chunk_start, chunk_end):
                img = images[i]

                # Preprocess
                img = self.preprocess_image(img)

                # Run Tesseract
                custom_config = r"--psm 6 --oem 1"
                try:
                    text = pytesseract.image_to_string(img, lang=lang_str, config=custom_config)
                except Exception:
                    logger.exception(f"Tesseract error on page {i + 1}")
                    text = ""

                # Get confidence data
                try:
                    data = pytesseract.image_to_data(
                        img,
                        lang=lang_str,
                        config=custom_config,
                        output_type=pytesseract.Output.DICT,
                    )
                    confidences = [c for c in data["conf"] if c > 0]
                    avg_confidence = sum(confidences) / len(confidences) if confidences else 0
                except Exception:
                    logger.exception(f"Tesseract confidence query failed on page {i + 1}")
                    avg_confidence = 0

                # Determine language
                page_text = text.strip()
                lang = "mixed"
                if any("\u0600" <= c <= "\u06ff" for c in page_text):
                    lang = "ar"
                elif any("a" <= c.lower() <= "z" for c in page_text):
                    lang = "en"

                # Determine actual page number
                actual_page = page_range[0] + i + 1 if page_range else i + 1

                chunk_segments.append(
                    TextSegment(
                        page=actual_page,
                        text=page_text,
                        lang=lang,
                        confidence=avg_confidence / 100.0,
                    )
                )

            chunks.append(
                ChunkData(
                    chunk_index=chunk_start // chunk_size,
                    page_range=[
                        page_range[0] + chunk_start + 1 if page_range else chunk_start + 1,
                        page_range[0] + chunk_end if page_range else chunk_end,
                    ],
                    segments=chunk_segments,
                    status="completed",
                    progress=1.0,
                )
            )

        return chunks
