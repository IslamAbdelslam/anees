"""Arabic text processing using camel-tools"""

import logging
from typing import Any, cast

from camel_tools.disambig.mle import MLEDisambiguator
from camel_tools.tokenizers.word import simple_word_tokenize
from camel_tools.utils.dediac import AR_DIAC_CHARSET, dediac_ar
from camel_tools.utils.normalize import (
    normalize_alef_ar,
    normalize_teh_marbuta_ar,
    normalize_unicode,
)

from anees.models import TashkeelResult

logger = logging.getLogger("anees.arabic")

MODES = ("full", "semantic", "endings")

# MLE only returns the single best analysis by default; ask for a few so the
# semantic mode can tell a homograph (عَلَم / عِلْم / عُلِم) from an unambiguous
# word. Pure case-ending variation (العِلْمِ / العِلْمَ) is discounted.
TOP_ANALYSES = 5
SHADDA = "\u0651"


class ArabicProcessor:
    def __init__(self) -> None:
        # Lazy: do NOT load camel-tools models in __init__, or the RPC
        # server blocks/crashes on startup when offline. Loaded on first
        # use via _ensure_loaded(), then reused for the process lifetime.
        self.disambiguator: Any = None

    def _ensure_loaded(self) -> bool:
        if self.disambiguator is not None:
            return True
        try:
            self._load_model()
            return self.disambiguator is not None
        except Exception:
            logger.exception("Arabic model unavailable (lazy load)")
            return False

    def _load_model(self) -> None:
        """Load camel-tools disambiguator"""
        self.disambiguator = MLEDisambiguator.pretrained(top=TOP_ANALYSES)
        logger.info("Arabic disambiguator loaded")

    def add_tashkeel(self, text: str, mode: str = "semantic") -> TashkeelResult:
        """Add diacritics (tashkeel) to Arabic text"""
        if mode not in MODES:
            raise ValueError(f"invalid mode: {mode}")

        if not text.strip():
            return TashkeelResult(text_with_diacritics=text, changes=0)

        normalized = self._normalize(text)

        if not self._ensure_loaded():
            return TashkeelResult(text_with_diacritics=normalized, changes=0)

        tokens = simple_word_tokenize(normalized)

        try:
            disambig = self.disambiguator.disambiguate(tokens)
        except Exception:
            logger.exception("Disambiguation failed")
            return TashkeelResult(text_with_diacritics=normalized, changes=0)

        replacements = [
            self._render(disambig[index] if index < len(disambig) else None, token, mode)
            for index, token in enumerate(tokens)
        ]
        result = self._cleanup(self._rebuild(normalized, tokens, replacements))
        return TashkeelResult(
            text_with_diacritics=result,
            changes=max(0, self._diacritics(result) - self._diacritics(text)),
        )

    def _normalize(self, text: str) -> str:
        """Normalize Arabic text"""
        text = normalize_unicode(text)
        text = normalize_alef_ar(text)
        return cast(str, normalize_teh_marbuta_ar(text))

    def _render(self, disambiguated: Any, token: str, mode: str) -> str:
        analyses = self._analyses_of(disambiguated)
        if not analyses:
            # Fall back to a directly provided diacritized form if present.
            diac = self._diac_of(disambiguated)
            if diac is None:
                return token
            analyses = [disambiguated]

        diac = self._diac_of(analyses[0])
        if diac is None:
            return token
        if mode == "full":
            return diac
        if mode == "semantic":
            return diac if self._is_homograph(analyses) else token
        if mode == "endings":
            return cast(str, dediac_ar(token)) + self._ending(diac)
        return token

    def _is_homograph(self, analyses: list) -> bool:
        """True when analyses differ beyond their case ending."""
        stems = set()
        for analysis in analyses:
            diac = self._diac_of(analysis)
            if diac is None:
                continue
            stems.add(diac[: len(diac) - len(self._ending(diac))])
        return len(stems) > 1

    def _analyses_of(self, value: Any) -> list:
        if value is None:
            return []
        if isinstance(value, dict):
            analyses = value.get("analyses")
        else:
            analyses = getattr(value, "analyses", None)
        if isinstance(analyses, (list, tuple)):
            return list(analyses)
        return []

    def _diac_of(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("diac", "diacritized", "text", "word"):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    return candidate
            return None
        for attr in ("diac", "diacritized", "text", "word"):
            candidate = getattr(value, attr, None)
            if isinstance(candidate, str):
                return candidate
        return None

    def _ending(self, diac: str) -> str:
        """Trailing run of diacritics, i.e. the last letter's case ending."""
        index = len(diac)
        while index > 0 and diac[index - 1] in AR_DIAC_CHARSET:
            index -= 1
        return diac[index:]

    def _rebuild(self, text: str, tokens: list[str], replacements: list[str]) -> str:
        """Replace tokens in place so whitespace and punctuation are preserved."""
        parts: list[str] = []
        cursor = 0
        for token, replacement in zip(tokens, replacements):
            start = text.find(token, cursor)
            if start < 0:
                continue
            parts.append(text[cursor:start])
            parts.append(replacement)
            cursor = start + len(token)
        parts.append(text[cursor:])
        return "".join(parts)

    def _cleanup(self, text: str) -> str:
        """Drop doubled/stacked diacritics; shadda followed by a vowel is valid."""
        parts: list[str] = []
        previous_diacritic = ""
        for char in text:
            if char in AR_DIAC_CHARSET and previous_diacritic:
                if previous_diacritic != SHADDA:
                    continue
                if char == SHADDA:
                    continue
            parts.append(char)
            previous_diacritic = char if char in AR_DIAC_CHARSET else ""
        return "".join(parts)

    def _diacritics(self, text: str) -> int:
        return sum(1 for char in text if char in AR_DIAC_CHARSET)
