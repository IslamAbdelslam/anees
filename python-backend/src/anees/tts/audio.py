"""Shared audio primitives used by the TTS engine and the audio exporters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

SAMPLE_RATE = 24000
CROSSFADE_MS = 50


@dataclass(frozen=True)
class Chapter:
    """A named region of an audio file, in seconds from the start."""

    title: str
    start: float
    end: float


@dataclass(frozen=True)
class StitchedAudio:
    """Stitched samples together with the chapter boundaries inside them."""

    audio: np.ndarray
    chapters: tuple[Chapter, ...]
    sample_rate: int = SAMPLE_RATE
