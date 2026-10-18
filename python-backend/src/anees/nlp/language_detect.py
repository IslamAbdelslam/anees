"""Language detection using fasttext"""

import logging
from pathlib import Path
from typing import Any

import fasttext
import fasttext.util

from anees.models import LanguageDetection

logger = logging.getLogger("anees.lang")

# Detections below this confidence are not auto-trusted and need review.
AUTO_CONFIDENCE_THRESHOLD = 0.8

ARABIC_RANGES = (
    ("\u0600", "\u06ff"),
    ("\u0750", "\u077f"),
    ("\u08a0", "\u08ff"),
    ("\ufb50", "\ufdff"),
    ("\ufe70", "\ufeff"),
)


class LanguageDetector:
    def __init__(self, model_path: str | None = None) -> None:
        # Lazy: do NOT download (126MB) or load the model here, or the
        # RPC server blocks/crashes on startup when offline. Resolved on
        # first use via _ensure_loaded(), or proactively via models.ensure.
        self.model: Any = None
        self.model_path = model_path
        self._cache: dict[str, LanguageDetection] = {}

    def _ensure_loaded(self) -> bool:
        """Load model on first use. Returns False (no raise) if unavailable."""
        if self.model is not None:
            return True
        try:
            path = self.model_path or self._get_model_path()
            self.model_path = path
            self._load_model()
            return self.model is not None
        except Exception:
            logger.exception("Language model unavailable (lazy load)")
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

    def _load_model(self) -> None:
        """Load fasttext model"""
        self.model = fasttext.load_model(self.model_path)
        logger.info("Language detection model loaded")

    def detect(self, text: str) -> LanguageDetection:
        """Detect language of text, cached per session"""
        key = text.strip()
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        result = self._detect_uncached(text)
        if result.lang != "unknown":
            self._cache[key] = result
        return result

    def _detect_uncached(self, text: str) -> LanguageDetection:
        if not text.strip():
            return LanguageDetection(lang="unknown", confidence=0.0)

        # Arabic + Latin letters in one segment means the segment is mixed.
        if self._has_arabic(text) and self._has_latin(text):
            return LanguageDetection(lang="mixed", confidence=1.0)

        if not self._ensure_loaded():
            return LanguageDetection(lang="unknown", confidence=0.0)

        try:
            clean_text = text.replace("\n", " ").strip()
            lang_code, confidence = self._predict(clean_text)
        except Exception:
            logger.exception("Language detection failed")
            return LanguageDetection(lang="unknown", confidence=0.0)

        # The app only speaks Arabic and English: map anything else by script.
        if lang_code in ("ar", "en"):
            lang = lang_code
        else:
            lang = "ar" if self._has_arabic(text) else "en"

        return LanguageDetection(lang=lang, confidence=max(0.0, min(1.0, confidence)))

    def _predict(self, text: str) -> tuple[str, float]:
        """Return (label, probability), tolerating fasttext's numpy>=2 breakage."""
        try:
            labels, probs = self.model.predict(text, k=1)
            return labels[0].replace("__label__", ""), float(probs[0])
        except ValueError:
            # fasttext<=0.9.3 calls np.array(..., copy=False), which numpy>=2
            # rejects; the underlying binding is unaffected.
            prob, label = self.model.f.predict(text + "\n", 1, 0.0, "strict")[0]
            return label.replace("__label__", ""), float(prob)

    def detect_segments(self, segments: list[dict]) -> list[dict]:
        """Detect language per segment; segments are ``{text, page}`` objects."""
        results: list[dict] = []
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict) or "text" not in segment:
                raise ValueError(f"segments[{index}] must be an object with a 'text' field")
            text = segment.get("text", "")
            if not isinstance(text, str):
                raise ValueError(f"segments[{index}].text must be a string")  # noqa: TRY004
            page = segment.get("page", 1)
            detection = self.detect(text)
            results.append(
                {
                    "text": text,
                    "page": page,
                    "lang": detection.lang,
                    "confidence": detection.confidence,
                    "needs_review": detection.confidence < AUTO_CONFIDENCE_THRESHOLD,
                }
            )
        return results

    def _has_arabic(self, text: str) -> bool:
        return any(any(lo <= c <= hi for lo, hi in ARABIC_RANGES) for c in text)

    def _has_latin(self, text: str) -> bool:
        return any("a" <= c.lower() <= "z" for c in text)
