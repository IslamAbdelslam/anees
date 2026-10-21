"""MP3 export with ID3 metadata and chapter markers."""

import logging
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import ffmpeg
import numpy as np
from mutagen.id3 import CHAP, CTOC, ID3, TALB, TIT2, TPE1

from anees.export.wav import write_wav
from anees.tts.audio import SAMPLE_RATE, Chapter

logger = logging.getLogger("anees.export.mp3")

BITRATES = (64, 128, 192, 320)
DEFAULT_BITRATE = 128
ARTIST = "Anees"
ALBUM = "Anees"

ProgressCallback = Callable[[int, str], None]


class MP3Exporter:
    """Encode 24 kHz mono audio to MP3 with ID3 tags and chapter markers."""

    def __init__(self, bitrate: int = DEFAULT_BITRATE, sample_rate: int = SAMPLE_RATE) -> None:
        if bitrate not in BITRATES:
            raise ValueError(f"Unsupported bitrate {bitrate}; expected one of {BITRATES}")
        self.bitrate = bitrate
        self.sample_rate = sample_rate

    def export(
        self,
        audio: np.ndarray,
        job_id: str,
        output_dir: Path,
        *,
        title: str = ARTIST,
        album: str = ALBUM,
        artist: str = ARTIST,
        chapters: Sequence[Chapter] = (),
        on_progress: ProgressCallback | None = None,
    ) -> Path:
        """Write `{job_id}.mp3` into `output_dir` and return the written path."""
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            raise ValueError("No audio to export")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}.mp3"

        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = write_wav(Path(tmp_dir) / "source.wav", samples, self.sample_rate)
            self._encode(wav_path, output_path, samples.size / self.sample_rate, on_progress)

        self._tag(output_path, title, album, artist, chapters)
        logger.info("MP3 exported to %s", output_path)
        return output_path

    def _encode(
        self,
        wav_path: Path,
        output_path: Path,
        duration: float,
        on_progress: ProgressCallback | None,
    ) -> None:
        stream = ffmpeg.input(str(wav_path)).output(
            str(output_path),
            format="mp3",
            acodec="libmp3lame",
            audio_bitrate=f"{self.bitrate}k",
            progress="pipe:1",
            nostats=None,
        )
        args = ffmpeg.compile(stream, overwrite_output=True)

        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg is required to export MP3") from exc

        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            if key != "out_time_us" or duration <= 0:
                continue
            percent = min(100, int(int(value) / (duration * 1_000_000) * 100))
            if on_progress is not None:
                on_progress(percent, f"Encoding MP3 ({percent}%)")

        stderr = process.stderr.read()
        if process.wait() != 0:
            raise RuntimeError(f"ffmpeg failed to encode MP3: {stderr.strip()[-500:]}")

    def _tag(
        self,
        output_path: Path,
        title: str,
        album: str,
        artist: str,
        chapters: Sequence[Chapter],
    ) -> None:
        tags = ID3()
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        tags.add(TALB(encoding=3, text=album))

        element_ids = []
        for index, chapter in enumerate(chapters):
            element_id = f"chp{index}"
            element_ids.append(element_id)
            tags.add(
                CHAP(
                    element_id=element_id,
                    start_time=int(chapter.start * 1000),
                    end_time=int(chapter.end * 1000),
                    start_offset=0xFFFFFFFF,
                    end_offset=0xFFFFFFFF,
                    sub_frames=[TIT2(encoding=3, text=chapter.title)],
                )
            )
        if element_ids:
            tags.add(
                CTOC(
                    element_id="toc",
                    flags=3,
                    child_element_ids=element_ids,
                    sub_frames=[TIT2(encoding=3, text="Chapters")],
                )
            )

        tags.save(str(output_path), v2_version=4)
