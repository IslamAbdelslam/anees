"""Arabic text processing using camel-tools"""

from camel_tools.disambig.mle import MLEDisambiguator
from camel_tools.tokenizers.word import simple_word_tokenize
from camel_tools.utils.normalize import normalize_alef_ar, normalize_teh_marbuta
from typing import List
from anees.models import TashkeelResult
import logging

logger = logging.getLogger("anees.arabic")


class ArabicProcessor:
    def __init__(self):
        self.disambiguator = None
        self._load_model()

    def _load_model(self):
        """Load camel-tools disambiguator"""
        try:
            self.disambiguator = MLEDisambiguator.pretrained()
            logger.info("Arabic disambiguator loaded")
        except Exception as e:
            logger.error(f"Failed to load Arabic model: {e}")
            raise

    def add_tashkeel(self, text: str, mode: str = "semantic") -> TashkeelResult:
        """Add diacritics (tashkeel) to Arabic text"""
        if not text.strip():
            return TashkeelResult(text_with_diacritics=text, changes=0)

        # Normalize
        text = self._normalize(text)

        # Tokenize
        tokens = simple_word_tokenize(text)

        # Disambiguate
        try:
            disambig = self.disambiguator.disambiguate(tokens)
        except Exception as e:
            logger.error(f"Disambiguation failed: {e}")
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

        # Count changes
        changes = sum(1 for a, b in zip(text, result) if a != b and b in '\u064e\u064f\u0650\u0651\u0652')

        return TashkeelResult(text_with_diacritics=result, changes=changes)

    def _normalize(self, text: str) -> str:
        """Normalize Arabic text"""
        text = normalize_alef_ar(text)
        text = normalize_teh_marbuta(text)
        return text

    def _reconstruct_full(self, disambig: List) -> str:
        """Full diacritization - all analyses"""
        parts = []
        for d in disambig:
            if d.analyses:
                parts.append(d.analyses[0].diac)
            else:
                parts.append(d.word)
        return " ".join(parts)

    def _reconstruct_semantic(self, disambig: List, tokens: List[str]) -> str:
        """Semantic diacritization - only disambiguate homographs"""
        parts = []
        for i, d in enumerate(disambig):
            if d.analyses:
                # Check if word has multiple analyses with different diacritics
                diacritics = set(a.diac for a in d.analyses)
                if len(diacritics) > 1:
                    # Homograph - use first analysis
                    parts.append(d.analyses[0].diac)
                else:
                    # No ambiguity - keep original
                    parts.append(tokens[i])
            else:
                parts.append(tokens[i])
        return " ".join(parts)

    def _reconstruct_endings(self, disambig: List, tokens: List[str]) -> str:
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