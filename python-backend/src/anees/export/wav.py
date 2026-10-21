"""WAV export (uncompressed PCM 24 kHz) for archival copies."""

import wave
from pathlib import Path

import numpy as np

from anees.tts.audio import SAMPLE_RATE


def write_wav(path: Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Path:
    """Write mono 16-bit PCM WAV and return the written path."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return path
