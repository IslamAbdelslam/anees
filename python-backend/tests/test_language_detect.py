"""Per-segment language detection tests (Issue #11).

Pure-logic tests inject a fake fasttext model so they do not download the
126MB `lid.176.bin`; the real-model test is skipped where the model is
absent, mirroring the other heavy-dependency suites so CI stays green.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

pytest.importorskip("pydantic")
pytest.importorskip("fasttext")

from anees.nlp.language_detect import AUTO_CONFIDENCE_THRESHOLD, LanguageDetector

MODEL_FILE = Path.home() / ".cache" / "anees" / "models" / "lid.176.bin"

SERVER_DEPENDENCIES = (
    "docx",
    "jinja2",
    "camel_tools",
    "fasttext",
    "onnxruntime",
    "cv2",
    "pytesseract",
    "pdf2image",
)


class _FakeBinding:
    def __init__(self, label: str, confidence: float) -> None:
        self.label = label
        self.confidence = confidence

    def predict(self, text: str, k: int, threshold: float, on_unicode_error: str) -> list:
        return [(self.confidence, self.label)]


class _NumpyBrokenModel:
    """Mimics fasttext<=0.9.3 on numpy>=2: predict() raises, binding works."""

    def __init__(self, label: str = "__label__en", confidence: float = 0.9) -> None:
        self.label = label
        self.confidence = confidence
        self.f = _FakeBinding(label, confidence)

    def predict(self, text: str, k: int = 1) -> tuple[list[str], list[float]]:
        raise ValueError("Unable to avoid copy while creating an array as requested.")


class _FakeModel:
    def __init__(self, label: str = "__label__ar", confidence: float = 0.95) -> None:
        self.label = label
        self.confidence = confidence
        self.calls = 0

    def predict(self, text: str, k: int = 1) -> tuple[list[str], list[float]]:
        self.calls += 1
        return [self.label], [self.confidence]


def _detector(label: str = "__label__ar", confidence: float = 0.95) -> LanguageDetector:
    detector = LanguageDetector()
    detector.model = _FakeModel(label, confidence)
    return detector


def test_arabic_segment_detected_as_ar() -> None:
    assert _detector("__label__ar").detect("نص عربي").lang == "ar"


def test_english_segment_detected_as_en() -> None:
    assert _detector("__label__en").detect("english text").lang == "en"


def test_other_language_maps_by_script() -> None:
    assert _detector("__label__fr").detect("english text").lang == "en"
    assert _detector("__label__fa").detect("نص عربي").lang == "ar"


def test_mixed_script_is_mixed_without_model() -> None:
    detector = LanguageDetector()

    result = detector.detect("Hello مرحبا")

    assert result.lang == "mixed"
    assert detector.model is None


def test_empty_text_is_unknown() -> None:
    result = _detector().detect("   ")

    assert result.lang == "unknown"
    assert result.confidence == 0.0


def test_unavailable_model_returns_unknown() -> None:
    detector = LanguageDetector(model_path="/nonexistent/lid.176.bin")

    assert detector.detect("some text").lang == "unknown"


def test_results_are_cached_per_session() -> None:
    detector = _detector()
    model = detector.model

    detector.detect("same text")
    detector.detect("same text")

    assert model.calls == 1


def test_mixed_detection_skips_model() -> None:
    detector = _detector()

    detector.detect("Hello مرحبا")
    detector.detect("Hello مرحبا")

    assert detector.model.calls == 0


def test_auto_confidence_threshold_is_point_eight() -> None:
    assert AUTO_CONFIDENCE_THRESHOLD == 0.8


def test_low_confidence_is_reported() -> None:
    detector = LanguageDetector()
    detector.model = _FakeModel("__label__ar", 0.5)

    assert detector.detect("text").confidence == 0.5


def test_detect_segments_preserves_text_and_page() -> None:
    detector = _detector()

    results = detector.detect_segments([{"text": "one", "page": 1}, {"text": "two", "page": 9}])

    assert [r["text"] for r in results] == ["one", "two"]
    assert [r["page"] for r in results] == [1, 9]
    assert all(set(r) == {"text", "page", "lang", "confidence", "needs_review"} for r in results)


def test_detect_segments_defaults_page_to_one() -> None:
    results = _detector().detect_segments([{"text": "no page"}])

    assert results[0]["page"] == 1


def test_detect_segments_flags_low_confidence() -> None:
    detector = LanguageDetector()
    detector.model = _FakeModel("__label__ar", 0.4)

    assert detector.detect_segments([{"text": "x", "page": 1}])[0]["needs_review"] is True


def test_detect_segments_rejects_missing_text() -> None:
    detector = _detector()

    with pytest.raises(ValueError):
        detector.detect_segments([{"page": 1}])

    with pytest.raises(ValueError):
        detector.detect_segments([{"text": 123, "page": 1}])


def test_numpy_incompatible_predict_falls_back_to_binding() -> None:
    detector = LanguageDetector()
    detector.model = _NumpyBrokenModel("__label__en", 0.9)

    result = detector.detect("an english sentence")

    assert result.lang == "en"
    assert result.confidence == 0.9


@pytest.mark.skipif(not MODEL_FILE.exists(), reason="fasttext lid.176.bin not present")
def test_real_model_detects_ar_en_and_mixed() -> None:
    detector = LanguageDetector()

    assert detector.detect("مرحبا بكم في تطبيق أنيس، هذا نص عربي طويل").lang == "ar"
    assert detector.detect("Welcome to Anees, this is a long English sentence").lang == "en"
    assert detector.detect("Welcome مرحبا").lang == "mixed"


def test_lang_detect_rpc_contract() -> None:
    for module in SERVER_DEPENDENCIES:
        pytest.importorskip(module)
    from anees.server import AneesRPCServer

    server = AneesRPCServer()
    server.lang_detector = _detector("__label__ar", 0.9)

    single: dict[str, Any] = asyncio.run(server.detect_language({"text": "hello"}))
    assert single["segments"] == [
        {
            "text": "hello",
            "page": 1,
            "lang": "ar",
            "confidence": 0.9,
            "needs_review": False,
        }
    ]

    batched: dict[str, Any] = asyncio.run(
        server.detect_language({"segments": [{"text": "hello", "page": 7}]})
    )
    assert batched["segments"][0]["page"] == 7

    with pytest.raises(ValueError):
        asyncio.run(server.detect_language({}))
