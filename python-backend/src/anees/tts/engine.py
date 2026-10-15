"""TTS Engine using ONNX Runtime"""

import onnxruntime as ort
import numpy as np
from pathlib import Path
from typing import Dict, Optional, List
import logging

logger = logging.getLogger("anees.tts")


class TTSEngine:
    def __init__(self, models_dir: Optional[Path] = None):
        self.models_dir = models_dir or Path.home() / ".config" / "anees" / "models"
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.sessions: Dict[str, ort.InferenceSession] = {}
        self.tokenizers: Dict[str, any] = {}
        self.configs: Dict[str, Dict] = {}

        # Voice embeddings
        self.voice_embeddings: Dict[str, np.ndarray] = {}

    def ensure_models(self, model_names: List[str]) -> Dict[str, str]:
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
            except Exception as e:
                logger.error(f"Failed to download {name}: {e}")

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
                str(model_path),
                providers=['CPUExecutionProvider']
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

    def synthesize(
        self,
        text: str,
        lang: str,
        voice: str,
        speed: float = 1.0
    ) -> np.ndarray:
        """Synthesize speech from text"""
        model_name = "nabra-7m-distill" if lang == "ar" else "kokoro-7m-distill"
        session = self._load_model(lang)

        # Phonemize
        phoneme_ids = self._phonemize(text, lang, voice)

        # Get voice embedding
        voice_emb = self._get_voice_embedding(voice, lang)

        # Run inference
        try:
            outputs = session.run(
                None,
                {
                    "phonemes": phoneme_ids,
                    "voice_embedding": voice_emb,
                    "speed": np.array([speed], dtype=np.float32)
                }
            )
            audio = outputs[0].squeeze()
            return audio.astype(np.float32)
        except Exception as e:
            logger.error(f"TTS inference failed: {e}")
            # Return silence as fallback
            return np.zeros(24000, dtype=np.float32)

    def _get_voice_embedding(self, voice: str, lang: str) -> np.ndarray:
        """Get voice embedding"""
        key = f"{lang}:{voice}"
        if key not in self.voice_embeddings:
            # Load from model directory or generate default
            # In production, load from .pt files
            self.voice_embeddings[key] = np.random.randn(256).astype(np.float32)
        return self.voice_embeddings[key].reshape(1, -1)

    def preview(self, voice: str, lang: str) -> np.ndarray:
        """Generate preview audio for voice"""
        preview_text = "مرحباً بكم في أنيس" if lang == "ar" else "Welcome to Anees"
        return self.synthesize(preview_text, lang, voice, 1.0)