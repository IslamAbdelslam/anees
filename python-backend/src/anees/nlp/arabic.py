"""Arabic text processing using camel-tools"""

import logging
from typing import Any

from camel_tools.disambig.mle import MLEDisambiguator
from camel_tools.tokenizers.word import simple_word_tokenize
from camel_tools.utils.normalize import normalize_alef_ar, normalize_teh_marbuta

from anees.models import TashkeelResult

logger = logging.getLogger("anees.arabic")


class ArabicProcessor:
    def __init__(self) -> None:
        # Lazy: do NOT load camel-tools models in __init__, or the RPC
        # server blocks/crashes on startup when offline. Loaded on first
        # use via _ensure_loaded().
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
        self.disambiguator = MLEDisambiguator.pretrained()
        logger.info("Arabic disambiguator loaded")

    def add_tashkeel(self, text: str, mode: str = "semantic") -> TashkeelResult:
        """Add diacritics (tashkeel) to Arabic text"""
        if not text.strip():
            return TashkeelResult(text_with_diacritics=text, changes=0)

        # Normalize
        text = self._normalize(text)

        if not self._ensure_loaded():
            return TashkeelResult(text_with_diacritics=text, changes=0)

        # Tokenize
        tokens = simple_word_tokenize(text)

        # Disambiguate
        try:
            disambig = self.disambiguator.disambiguate(tokens)
        except Exception:
            logger.exception("Disambiguation failed")
            return TashkeelResult(text_with_diacritics=text, changes=0)

        # Reconstruct with diacritics based on mode
        if mode == "full":
            result = self._reconstruct_full(disambig)
        elif mode == "semantic":
            result = self._reconstruct_semantic(disambig, tokens)
        elif mode == "endings":
            result = self._reconstruct_endings(disambig, tokens)
        else:
            result = self._reconstruct_semantic(disambig, tokens)

        # Count added diacritics: comparing char-by-char with zip() is wrong
        # because inserting diacritics shifts all subsequent offsets.
        # Instead count diacritic codepoints added vs. the input.
        DIACRITICS = set("ًٌٍَُِّْٰٕٖٜٟٓٔٗ٘ٙٚٛٞـً")

        # Core Arabic vowel marks U+064B–U+0652 plus common extras
        def _diac_count(s: str) -> int:
            return sum(1 for c in s if c in DIACRITICS or "\u064b" <= c <= "\u0652")

        changes = _diac_count(result) - _diac_count(text)
        changes = max(0, changes)

        return TashkeelResult(text_with_diacritics=result, changes=changes)

    def _normalize(self, text: str) -> str:
        """Normalize Arabic text"""
        text = normalize_alef_ar(text)
        text = normalize_teh_marbuta(text)
        return text

    def _reconstruct_full(self, disambig: list) -> str:
        """Full diacritization - all analyses"""
        parts = []
        for d in disambig:
            if d.analyses:
                parts.append(d.analyses[0].diac)
            else:
                parts.append(d.word)
        return " ".join(parts)

    def _reconstruct_semantic(self, disambig: list, tokens: list[str]) -> str:
        """Semantic diacritization - only disambiguate homographs"""
        parts = []
        for i, d in enumerate(disambig):
            if d.analyses:
                # Check if word has multiple analyses with different diacritics
                diacritics = {a.diac for a in d.analyses}
                if len(diacritics) > 1:
                    # Homograph - use first analysis
                    parts.append(d.analyses[0].diac)
                else:
                    # No ambiguity - keep original
                    parts.append(tokens[i])
            else:
                parts.append(tokens[i])
        return " ".join(parts)

    def _reconstruct_endings(self, disambig: list, tokens: list[str]) -> str:
        """Endings only - case endings for pausing"""
        parts = []
        for i, d in enumerate(disambig):
            if d.analyses:
                diac = d.analyses[0].diac
                # Keep only last character's diacritics (case ending)
                if len(diac) > 1:
                    parts.append(tokens[i][:-1] + diac[-1])
                else:
                    parts.append(tokens[i])
            else:
                parts.append(tokens[i])
        return " ".join(parts)
