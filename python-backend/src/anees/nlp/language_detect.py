"""Language detection using fasttext"""

import fasttext
import fasttext.util
from pathlib import Path
from typing import List, Optional
from anees.models import LanguageDetection, TextSegment
import logging

logger = logging.getLogger("anees.lang")


class LanguageDetector:
    def __init__(self, model_path: Optional[str] = None):
        # Lazy: do NOT download (126MB) or load the model here, or the
        # RPC server blocks/crashes on startup when offline. Resolved on
        # first use via _ensure_loaded(), or proactively via models.ensure.
        self.model = None
        self.model_path = model_path

    def _ensure_loaded(self) -> bool:
        """Load model on first use. Returns False (no raise) if unavailable."""
        if self.model is not None:
            return True
        try:
            path = self.model_path or self._get_model_path()
            self.model_path = path
            self._load_model()
            return self.model is not None
        except Exception as e:
            logger.error(f"Language model unavailable (lazy load): {e}")
            return False

    def _get_model_path(self) -> str:
        """Get or download fasttext language detection model"""
        cache_dir = Path.home() / ".cache" / "anees" / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        model_file = cache_dir / "lid.176.bin"

        if not model_file.exists():
            logger.info("Downloading fasttext language detection model...")
            fasttext.util.download_model("lid.176", ifilename=str(model_file))

        return str(model_file)

    def _load_model(self):
        """Load fasttext model"""
        try:
            self.model = fasttext.load_model(self.model_path)
            logger.info("Language detection model loaded")
        except Exception as e:
            logger.error(f"Failed to load language model: {e}")
            raise

    def detect(self, text: str) -> LanguageDetection:
        """Detect language of text"""
        if not text.strip():
            return LanguageDetection(lang="unknown", confidence=0.0)

        if not self._ensure_loaded():
            return LanguageDetection(lang="unknown", confidence=0.0)

        # Clean text
        clean_text = text.replace("\n", " ").strip()
        if not clean_text:
            return LanguageDetection(lang="unknown", confidence=0.0)

        try:
            predictions = self.model.predict(clean_text, k=1)
            lang_code = predictions[0][0].replace("__label__", "")
            confidence = float(predictions[1][0])

            # Map fasttext codes to our codes
            lang_map = {
                "ar": "ar",
                "en": "en",
                "fr": "fr",
                "de": "de",
                "es": "es",
                "it": "it",
                "pt": "pt",
                "ru": "ru",
                "zh": "zh",
                "ja": "ja",
                "ko": "ko",
            }

            mapped_lang = lang_map.get(lang_code, lang_code)

            # Check for mixed scripts
            if self._has_arabic(text) and self._has_latin(text):
                mapped_lang = "mixed"

            return LanguageDetection(lang=mapped_lang, confidence=confidence)

        except Exception as e:
            logger.error(f"Language detection failed: {e}")
            return LanguageDetection(lang="unknown", confidence=0.0)

    def detect_batch(self, segments: List[TextSegment]) -> List[LanguageDetection]:
        """Detect language for multiple segments"""
        return [self.detect(seg.text) for seg in segments]

    def _has_arabic(self, text: str) -> bool:
        return any('\u0600' <= c <= '\u06FF' for c in text)

    def _has_latin(self, text: str) -> bool:
        return any('a' <= c.lower() <= 'z' for c in text)