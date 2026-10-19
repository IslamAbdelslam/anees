"""Voice catalog, embeddings, preview, and preferences (Issue #14)."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from anees.tts.voices import VoiceManager

EXPECTED_CATALOG = {
    "arabic": {
        "af_msa": "Modern Standard Arabic (Female)",
        "am_msa": "Modern Standard Arabic (Male)",
    },
    "english": {
        "af_bella": "US English (Female)",
        "am_michael": "US English (Male)",
        "bf_emma": "UK English (Female)",
    },
    "saudi": {
        "af_saudi": "Saudi Arabic (Female)",
    },
}


def test_catalog_matches_rpc_contract(tmp_path: Path) -> None:
    manager = VoiceManager(config_path=tmp_path / "voices.json")
    assert manager.get_catalog() == EXPECTED_CATALOG


def test_default_voice_per_language(tmp_path: Path) -> None:
    manager = VoiceManager(config_path=tmp_path / "voices.json")
    assert manager.get_default_voice("arabic") == "af_msa"
    assert manager.get_default_voice("english") == "af_bella"
    assert manager.get_default_voice("saudi") == "af_saudi"


def test_unknown_language_raises(tmp_path: Path) -> None:
    manager = VoiceManager(config_path=tmp_path / "voices.json")
    with pytest.raises(ValueError):
        manager.get_default_voice("french")
    with pytest.raises(ValueError):
        manager.set_default_voice("french", "af_bella")


def test_preference_is_persisted(tmp_path: Path) -> None:
    config_path = tmp_path / "voices.json"
    manager = VoiceManager(config_path=config_path)
    manager.set_default_voice("english", "bf_emma")

    assert manager.get_default_voice("english") == "bf_emma"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["default_voices"]["english"] == "bf_emma"

    reloaded = VoiceManager(config_path=config_path)
    assert reloaded.get_default_voice("english") == "bf_emma"


def test_invalid_saved_voice_is_ignored(tmp_path: Path) -> None:
    config_path = tmp_path / "voices.json"
    config_path.write_text(
        json.dumps({"default_voices": {"english": "does_not_exist"}}), encoding="utf-8"
    )
    manager = VoiceManager(config_path=config_path)
    assert manager.get_default_voice("english") == "af_bella"


def test_unreadable_preferences_are_ignored(tmp_path: Path) -> None:
    config_path = tmp_path / "voices.json"
    config_path.write_text("not json", encoding="utf-8")
    manager = VoiceManager(config_path=config_path)
    assert manager.get_default_voice("arabic") == "af_msa"


np = pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")

from anees.tts.engine import PREVIEW_SECONDS, SAMPLE_RATE, TTSEngine


def test_bin_embedding_is_loaded_and_cached(tmp_path: Path) -> None:
    voices_dir = tmp_path / "kokoro-7m-distill" / "voices"
    voices_dir.mkdir(parents=True)
    style = np.arange(510 * 256, dtype=np.float32).reshape(510, 1, 256)
    style.tofile(voices_dir / "af_bella.bin")

    engine = TTSEngine(models_dir=tmp_path)
    first = engine.load_voice_embedding("af_bella")
    second = engine.load_voice_embedding("af_bella")

    assert first.shape == (510, 1, 256)
    assert second is first


def test_pt_embedding_is_loaded(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    voices_dir = tmp_path / "nabra-7m-distill" / "voices"
    voices_dir.mkdir(parents=True)
    torch.save({"af_msa": torch.zeros(3, 1, 256)}, voices_dir / "af_msa.pt")

    engine = TTSEngine(models_dir=tmp_path)
    embedding = engine.load_voice_embedding("af_msa")

    assert embedding.shape == (3, 1, 256)


def test_missing_embedding_raises(tmp_path: Path) -> None:
    engine = TTSEngine(models_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        engine.load_voice_embedding("am_michael")


def test_style_row_follows_phoneme_count(tmp_path: Path) -> None:
    voices_dir = tmp_path / "kokoro-7m-distill" / "voices"
    voices_dir.mkdir(parents=True)
    style = np.arange(4 * 256, dtype=np.float32).reshape(4, 1, 256)
    style.tofile(voices_dir / "af_bella.bin")

    engine = TTSEngine(models_dir=tmp_path)
    selected = engine._get_voice_embedding("af_bella", 3)

    assert selected.shape == (1, 256)
    np.testing.assert_array_equal(selected[0], style[2, 0])


def test_preview_pads_to_three_seconds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = TTSEngine(models_dir=tmp_path)
    monkeypatch.setattr(
        engine, "synthesize", lambda text, lang, voice, speed=1.0: np.zeros(1000, dtype=np.float32)
    )

    audio = engine.preview("af_bella")

    assert audio.shape == (int(SAMPLE_RATE * PREVIEW_SECONDS),)


def test_preview_truncates_to_three_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = TTSEngine(models_dir=tmp_path)
    monkeypatch.setattr(
        engine,
        "synthesize",
        lambda text, lang, voice, speed=1.0: np.ones(SAMPLE_RATE * 10, dtype=np.float32),
    )

    audio = engine.preview("af_msa")

    assert audio.shape == (int(SAMPLE_RATE * PREVIEW_SECONDS),)


def test_preview_infers_language_from_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = TTSEngine(models_dir=tmp_path)
    seen: dict[str, str] = {}

    def fake_synthesize(text: str, lang: str, voice: str, speed: float = 1.0) -> Any:
        seen["lang"] = lang
        return np.zeros(1, dtype=np.float32)

    monkeypatch.setattr(engine, "synthesize", fake_synthesize)
    engine.preview("af_bella")

    assert seen["lang"] == "en"
