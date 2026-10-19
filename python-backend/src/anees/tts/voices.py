"""Voice catalog and default-voice preferences for TTS."""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("anees.tts.voices")

VOICE_CATALOG: dict[str, dict[str, str]] = {
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

DEFAULT_VOICES = {
    "arabic": "af_msa",
    "english": "af_bella",
    "saudi": "af_saudi",
}

LANGUAGE_CODES = {
    "arabic": "ar",
    "english": "en",
    "saudi": "ar_saudi",
}

VOICE_LANGUAGES = {
    voice_id: LANGUAGE_CODES[language]
    for language, voices in VOICE_CATALOG.items()
    for voice_id in voices
}


def default_config_path() -> Path:
    override = os.environ.get("ANEES_CONFIG_DIR")
    base = Path(override) if override else Path.home() / ".config" / "anees"
    return base / "voices.json"


class VoiceManager:
    def __init__(self, config_path: Path | None = None) -> None:
        self.catalog = VOICE_CATALOG
        self.config_path = config_path or default_config_path()
        self.preferences = self._load_preferences()

    def get_catalog(self) -> dict[str, dict[str, str]]:
        """Return the voice catalog: language -> voice id -> display name."""
        return self.catalog

    def language_for_voice(self, voice_id: str) -> str | None:
        for language, voices in self.catalog.items():
            if voice_id in voices:
                return language
        return None

    def get_default_voice(self, language: str) -> str:
        voices = self.catalog.get(language)
        if not voices:
            raise ValueError(f"Unknown language: {language}")
        saved = self.preferences.get(language)
        if saved in voices:
            return saved
        return DEFAULT_VOICES.get(language) or next(iter(voices))

    def set_default_voice(self, language: str, voice_id: str) -> None:
        voices = self.catalog.get(language)
        if voices is None:
            raise ValueError(f"Unknown language: {language}")
        if voice_id not in voices:
            raise ValueError(f"Unknown voice for {language}: {voice_id}")
        self.preferences[language] = voice_id
        self._save_preferences()

    def get_preferences(self) -> dict[str, str]:
        return dict(self.preferences)

    def _load_preferences(self) -> dict[str, str]:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            logger.warning("Ignoring unreadable voice preferences: %s", self.config_path)
            return {}

        defaults = data.get("default_voices", {})
        if not isinstance(defaults, dict):
            return {}
        return {
            language: voice_id
            for language, voice_id in defaults.items()
            if language in self.catalog and voice_id in self.catalog[language]
        }

    def _save_preferences(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"default_voices": self.preferences}
        self.config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
