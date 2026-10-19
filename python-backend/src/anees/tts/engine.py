"""TTS Engine using ONNX Runtime"""

import logging
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort

from anees.tts.voices import VOICE_LANGUAGES

logger = logging.getLogger("anees.tts")

SAMPLE_RATE = 24000
PREVIEW_SECONDS = 3.0
STYLE_DIM = 256

PREVIEW_TEXT = {
    "ar": "مرحباً بكم في أنيس",
    "ar_saudi": "مرحباً بكم في أنيس",
    "en": "Welcome to Anees",
}


class TTSEngine:
    def __init__(self, models_dir: Path | None = None):
        self.models_dir = models_dir or Path.home() / ".config" / "anees" / "models"
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.sessions: dict[str, ort.InferenceSession] = {}
        self.tokenizers: dict[str, Any] = {}
        self.configs: dict[str, dict] = {}

        # Voice embeddings, loaded lazily and keyed by voice id
        self.voice_embeddings: dict[str, np.ndarray] = {}

    def ensure_models(self, model_names: list[str]) -> dict[str, str]:
        """Download and verify models from Hugging Face"""
        from huggingface_hub import hf_hub_download

        model_repos = {
            "nabra-7m-distill": "oddadmix/Nabra-7M-Distill-ONNX",
            "kokoro-7m-distill": "oddadmix/Kokoro-7M-Distill-ONNX",
            "nabra-saudi-7m": "oddadmix/Nabra-Saudi-7M-ONNX",
        }

        paths = {}
        for name in model_names:
            if name not in model_repos:
                logger.warning(f"Unknown model: {name}")
                continue

            repo = model_repos[name]
            model_dir = self.models_dir / name
            model_dir.mkdir(exist_ok=True)

            try:
                for fname in ["model.onnx", "config.json", "vocab.json"]:
                    hf_hub_download(repo_id=repo, filename=fname, local_dir=model_dir)
                paths[name] = str(model_dir)
                logger.info(f"Model {name} ready at {model_dir}")
            except Exception:
                logger.exception(f"Failed to download {name}")

        return paths

    def _load_model(self, lang: str) -> ort.InferenceSession:
        """Load ONNX model for language"""
        model_map = {
            "ar": "nabra-7m-distill",
            "en": "kokoro-7m-distill",
            "ar_saudi": "nabra-saudi-7m",
        }

        model_name = model_map.get(lang, "nabra-7m-distill")

        if model_name not in self.sessions:
            model_path = self.models_dir / model_name / "model.onnx"
            if not model_path.exists():
                self.ensure_models([model_name])
                if not model_path.exists():
                    raise FileNotFoundError(f"Model not found: {model_path}")

            self.sessions[model_name] = ort.InferenceSession(
                str(model_path), providers=["CPUExecutionProvider"]
            )

            # Load config
            import json

            config_path = self.models_dir / model_name / "config.json"
            if config_path.exists():
                with open(config_path) as f:
                    self.configs[model_name] = json.load(f)

            # Load vocab
            vocab_path = self.models_dir / model_name / "vocab.json"
            if vocab_path.exists():
                with open(vocab_path) as f:
                    self.tokenizers[model_name] = json.load(f)

        return self.sessions[model_name]

    def _phonemize(self, text: str, lang: str, voice: str) -> np.ndarray:
        """Convert text to phoneme IDs"""
        # This is a simplified phonemization
        # In production, use espeak-ng or similar
        # For now, return dummy phoneme sequence
        # TODO: Integrate espeak-ng properly
        vocab = self.tokenizers.get("nabra-7m-distill", {})
        phoneme_ids = [vocab.get(c, 0) for c in text[:512]]
        return np.array([phoneme_ids], dtype=np.int64)

    def synthesize(self, text: str, lang: str, voice: str, speed: float = 1.0) -> np.ndarray:
        """Synthesize speech from text"""
        session = self._load_model(lang)

        # Phonemize
        phoneme_ids = self._phonemize(text, lang, voice)

        # Get voice embedding
        voice_emb = self._get_voice_embedding(voice, phoneme_ids.shape[-1])

        # Run inference
        try:
            outputs = session.run(
                None,
                {
                    "phonemes": phoneme_ids,
                    "voice_embedding": voice_emb,
                    "speed": np.array([speed], dtype=np.float32),
                },
            )
            audio = outputs[0].squeeze()
            return np.asarray(audio, dtype=np.float32)
        except Exception:
            logger.exception("TTS inference failed")
            # Return silence as fallback
            return np.zeros(24000, dtype=np.float32)

    def find_voice_file(self, voice_id: str) -> Path | None:
        """Locate a voice embedding in any downloaded model repo."""
        for suffix in (".pt", ".bin"):
            matches = sorted(self.models_dir.glob(f"*/voices/{voice_id}{suffix}"))
            if matches:
                return matches[0]
        return None

    def load_voice_embedding(self, voice_id: str) -> np.ndarray:
        """Load a voice embedding on demand and cache it by voice id."""
        cached = self.voice_embeddings.get(voice_id)
        if cached is not None:
            return cached

        path = self.find_voice_file(voice_id)
        if path is None:
            raise FileNotFoundError(f"Voice embedding not found: {voice_id}")

        embedding = (
            self._read_torch_embedding(path)
            if path.suffix == ".pt"
            else self._read_raw_embedding(path)
        )
        self.voice_embeddings[voice_id] = embedding
        return embedding

    @staticmethod
    def _read_torch_embedding(path: Path) -> np.ndarray:
        import torch

        value = torch.load(path, map_location="cpu", weights_only=True)
        if isinstance(value, dict):
            value = next(iter(value.values()))
        return np.asarray(value.detach().cpu().numpy(), dtype=np.float32)

    @staticmethod
    def _read_raw_embedding(path: Path) -> np.ndarray:
        raw = np.fromfile(path, dtype=np.float32)
        if raw.size and raw.size % STYLE_DIM == 0:
            raw = raw.reshape(-1, 1, STYLE_DIM)
        return raw

    def _get_voice_embedding(self, voice: str, num_phonemes: int = 1) -> np.ndarray:
        """Select the style row for this utterance and shape it as [1, STYLE_DIM]."""
        embedding = self.load_voice_embedding(voice)
        index = min(max(num_phonemes - 1, 0), embedding.shape[0] - 1)
        if embedding.ndim == 3 or (embedding.ndim == 2 and embedding.shape[-1] == STYLE_DIM):
            embedding = embedding[index]
        return np.asarray(embedding, dtype=np.float32).reshape(1, -1)

    def preview(self, voice: str, lang: str | None = None) -> np.ndarray:
        """Synthesize a fixed-length sample for voice audition."""
        language = lang or VOICE_LANGUAGES.get(voice, "ar")
        text = PREVIEW_TEXT.get(language, PREVIEW_TEXT["ar"])
        return self._fit_duration(self.synthesize(text, language, voice, 1.0), PREVIEW_SECONDS)

    @staticmethod
    def _fit_duration(audio: np.ndarray, seconds: float) -> np.ndarray:
        samples = int(SAMPLE_RATE * seconds)
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size >= samples:
            return audio[:samples]
        return np.pad(audio, (0, samples - audio.size))
