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

TOP_ANALYSES = 5
SHADDA = "\u0651"


class ArabicProcessor:
    def __init__(self) -> None:
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
        self.disambiguator = MLEDisambiguator.pretrained(top=TOP_ANALYSES)
        logger.info("Arabic disambiguator loaded")

    def add_tashkeel(self, text: str, mode: str = "semantic") -> TashkeelResult:
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
        text = normalize_unicode(text)
        text = normalize_alef_ar(text)
        return cast(str, normalize_teh_marbuta_ar(text))

    def _render(self, disambiguated: Any, token: str, mode: str) -> str:
        analyses = self._analyses_of(disambiguated)
        if not analyses:
            diac = self._diac_of(disambiguated)
            if diac is None:
                return token
            analyses = [disambiguated]

        if mode == "full":
            best = self._best_diacritized(analyses)
            diac = self._diac_of(best) if best is not None else None
            if diac is None or self._diacritics(diac) < self._diacritics(token):
                return token
            return diac

        if mode == "semantic":
            diac = self._diac_of(analyses[0])
            if diac is None:
                return token
            if not self._is_homograph(analyses):
                return token
            if self._diacritics(diac) < self._diacritics(token):
                return token
            return diac

        if mode == "endings":
            best = self._best_ending(analyses)
            diac = self._diac_of(best) if best is not None else None
            ending = self._ending(diac) if diac is not None else ""
            if not ending:
                return token
            return cast(str, dediac_ar(token)) + ending

        raise ValueError(f"invalid mode: {mode}")

    def _best_diacritized(self, analyses: list) -> Any:
        best = None
        best_count = -1
        for analysis in analyses:
            diac = self._diac_of(analysis)
            if diac is None:
                continue
            count = self._diacritics(diac)
            if count > best_count:
                best_count = count
                best = analysis
        return best

    def _best_ending(self, analyses: list) -> Any:
        best = None
        best_len = -1
        for analysis in analyses:
            diac = self._diac_of(analysis)
            if diac is None:
                continue
            ending_len = len(self._ending(diac))
            if ending_len > best_len:
                best_len = ending_len
                best = analysis
        return best

    def _is_homograph(self, analyses: list) -> bool:
        stems = set()
        for analysis in analyses:
            diac = self._diac_of(analysis)
            if diac is None:
                continue
            stems.add(diac[: len(diac) - len(self._ending(diac))])
        return len(stems) > 1

    def _ending(self, diac: str) -> str:
        index = len(diac)
        while index > 0 and diac[index - 1] in AR_DIAC_CHARSET:
            index -= 1
        return diac[index:]

    def _analyses_of(self, value: Any) -> list:
        analyses = getattr(value, "analyses", None)
        if isinstance(analyses, (list, tuple)):
            return list(analyses)
        return []

    def _diac_of(self, value: Any) -> str | None:
        if isinstance(value, str):
            return value
        diac = getattr(value, "diac", None)
        if isinstance(diac, str):
            return diac
        return None

    def _rebuild(self, text: str, tokens: list[str], replacements: list[str]) -> str:
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
        parts: list[str] = []
        previous_diacritic = ""
        for char in text:
            if char in AR_DIAC_CHARSET and previous_diacritic in AR_DIAC_CHARSET:
                if char == previous_diacritic:
                    continue
                if char != SHADDA and previous_diacritic != SHADDA:
                    continue
            parts.append(char)
            previous_diacritic = char if char in AR_DIAC_CHARSET else ""
        return "".join(parts)

    def _diacritics(self, text: str) -> int:
        return sum(1 for char in text if char in AR_DIAC_CHARSET)
