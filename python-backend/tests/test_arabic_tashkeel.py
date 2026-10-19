"""Arabic tashkeel tests (Issue #12)."""

import sys
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

pytest.importorskip("camel_tools")

from camel_tools.tokenizers.word import simple_word_tokenize

from anees.nlp.arabic import MODES, ArabicProcessor


def _fake_disambiguator(analyses: list[str]) -> Any:
    class _Analysis:
        def __init__(self, diac: str) -> None:
            self.diac = diac

    class _Disambig:
        def __init__(self, items: list[str]) -> None:
            self.analyses = [_Analysis(d) for d in items]

    class _Disambiguator:
        def disambiguate(self, tokens: list[str]) -> list[_Disambig]:
            return [_Disambig(list(analyses)) for _ in tokens]

    return _Disambiguator()


def test_modes_tuple() -> None:
    assert MODES == ("full", "semantic", "endings")


def test_invalid_mode_raises() -> None:
    proc = ArabicProcessor()
    with pytest.raises(ValueError):
        proc.add_tashkeel("علم", "bogus")


def test_empty_text() -> None:
    proc = ArabicProcessor()
    result = proc.add_tashkeel("   ", "full")
    assert result.text_with_diacritics == "   "
    assert result.changes == 0


def test_cleanup_preserves_shadda_both_orders() -> None:
    proc = ArabicProcessor()
    vowel_shadda = "a\u0650\u0651"
    shadda_vowel = "a\u0651\u0650"
    assert proc._cleanup(vowel_shadda) == vowel_shadda
    assert proc._cleanup(shadda_vowel) == shadda_vowel
    assert proc._cleanup("a\u064e\u064e") == "a\u064e"
    assert proc._cleanup("a\u0651\u0651") == "a\u0651"


def test_ending() -> None:
    proc = ArabicProcessor()
    assert proc._ending("العِلْمِ") == "ِ"
    assert proc._ending("عُلِمَ") == "َ"
    assert proc._ending("علم") == ""


def test_is_homograph() -> None:
    proc = ArabicProcessor()
    analyses_homo = [type("A", (), {"diac": d})() for d in ("عَلَم", "عِلْم", "عُلِم")]
    assert proc._is_homograph(analyses_homo) is True
    analyses_same = [type("A", (), {"diac": d})() for d in ("كِتابِ", "كِتابٍ", "كِتابَ")]
    assert proc._is_homograph(analyses_same) is False


def test_rebuild_preserves_punctuation_and_newlines() -> None:
    proc = ArabicProcessor()
    text = "مرحبا، كيف حالك؟\nشكرا"
    tokens = simple_word_tokenize(text)
    assert proc._rebuild(text, tokens, list(tokens)) == text


def test_semantic_fake_homograph() -> None:
    proc = ArabicProcessor()
    proc.disambiguator = _fake_disambiguator(["عَلَم", "عِلْم", "عُلِم"])
    result = proc.add_tashkeel("علم", "semantic")
    assert result.text_with_diacritics == "عَلَم"
    assert result.changes > 0


def test_semantic_fake_non_homograph_unchanged() -> None:
    proc = ArabicProcessor()
    proc.disambiguator = _fake_disambiguator(["كِتابِ", "كِتابٍ", "كِتابَ"])
    result = proc.add_tashkeel("كتاب", "semantic")
    assert result.text_with_diacritics == "كتاب"
    assert result.changes == 0


def test_full_fake_prefers_most_diacritized() -> None:
    proc = ArabicProcessor()
    proc.disambiguator = _fake_disambiguator(["مسرع", "مَسْرَعٌ"])
    result = proc.add_tashkeel("مسرع", "full")
    assert result.text_with_diacritics == "مَسْرَعٌ"


def test_endings_fake() -> None:
    proc = ArabicProcessor()
    proc.disambiguator = _fake_disambiguator(["كِتابِ", "كِتابٍ"])
    result = proc.add_tashkeel("كتاب", "endings")
    assert result.text_with_diacritics == "كتابِ"


def test_server_rpc_contract() -> None:
    import asyncio

    from anees.server import AneesRPCServer

    server = AneesRPCServer()
    proc = ArabicProcessor()
    proc.disambiguator = _fake_disambiguator(["عُلِمَ", "عِلْم"])
    server.arabic = proc

    result = asyncio.run(server.add_tashkeel({"text": "علم", "mode": "full"}))
    assert set(result) == {"text_with_diacritics", "changes"}
    with pytest.raises(ValueError):
        asyncio.run(server.add_tashkeel({}))
    with pytest.raises(ValueError):
        asyncio.run(server.add_tashkeel({"text": "علم", "mode": "nope"}))
