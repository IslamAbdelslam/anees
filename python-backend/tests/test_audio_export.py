"""Audio stitching and MP3/WAV export tests (Issue #15).

The MP3 tests need ffmpeg on PATH plus the ffmpeg-python/mutagen packages;
they are skipped when those are unavailable.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

np = pytest.importorskip("numpy")

from anees.tts.audio import SAMPLE_RATE
from anees.tts.engine import crossfade_concat, stitch_chunks


def _tone(samples: int, freq: float = 440.0, amplitude: float = 0.5) -> "np.ndarray":
    t = np.arange(samples) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_crossfade_concat_joins_without_gap() -> None:
    a = _tone(SAMPLE_RATE)
    b = _tone(SAMPLE_RATE, freq=660.0)

    joined = crossfade_concat([a, b])

    expected = a.size + b.size - int(SAMPLE_RATE * 0.05)
    assert joined.size == expected
    assert joined.dtype == np.float32


def test_crossfade_concat_smooths_the_join() -> None:
    """A hard cut would leave a discontinuity; the crossfade removes it."""
    a = np.full(SAMPLE_RATE, 0.5, dtype=np.float32)
    b = np.full(SAMPLE_RATE, -0.5, dtype=np.float32)

    joined = crossfade_concat([a, b])

    jump = np.abs(np.diff(joined)).max()
    assert jump < 0.01


def test_crossfade_concat_handles_empty_and_single_parts() -> None:
    a = _tone(1000)

    assert crossfade_concat([]).size == 0
    assert crossfade_concat([np.zeros(0, dtype=np.float32), a]).size == a.size
    assert crossfade_concat([a]).size == a.size


def test_stitch_chunks_reports_chapter_boundaries() -> None:
    chunks = [_tone(SAMPLE_RATE), _tone(SAMPLE_RATE, freq=660.0), _tone(SAMPLE_RATE, freq=880.0)]
    titles = ["Chapter One", "Chapter Two", "Chapter Three"]

    stitched = stitch_chunks(chunks, titles)

    assert [c.title for c in stitched.chapters] == titles
    assert stitched.chapters[0].start == 0.0
    assert stitched.chapters[-1].end == pytest.approx(stitched.audio.size / SAMPLE_RATE)
    for previous, following in zip(stitched.chapters, stitched.chapters[1:]):
        assert previous.end == pytest.approx(following.start)


def test_stitch_chunks_keeps_24khz_rate() -> None:
    stitched = stitch_chunks([_tone(SAMPLE_RATE)], ["Only"])

    assert stitched.sample_rate == SAMPLE_RATE


def test_stitch_chunks_defaults_chapter_titles() -> None:
    stitched = stitch_chunks([_tone(1000), _tone(1000)])

    assert [c.title for c in stitched.chapters] == ["Chapter 1", "Chapter 2"]


def test_stitch_chunks_skips_empty_chunks() -> None:
    stitched = stitch_chunks(
        [_tone(1000), np.zeros(0, dtype=np.float32), _tone(1000)], ["A", "B", "C"]
    )

    assert [c.title for c in stitched.chapters] == ["A", "C"]


def test_write_wav_is_uncompressed_24khz(tmp_path: Path) -> None:
    from anees.export.wav import write_wav

    path = write_wav(tmp_path / "audio.wav", _tone(SAMPLE_RATE // 2))

    assert path.exists()
    with open(path, "rb") as handle:
        header = handle.read(44)
    assert header[:4] == b"RIFF"
    assert header[8:12] == b"WAVE"
    # PCM format (1), 1 channel, 24 kHz
    assert int.from_bytes(header[20:22], "little") == 1
    assert int.from_bytes(header[22:24], "little") == 1
    assert int.from_bytes(header[24:28], "little") == SAMPLE_RATE


ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg binary not available"
)


@ffmpeg_required
def test_mp3_export_writes_id3_tags_and_chapters(tmp_path: Path) -> None:
    pytest.importorskip("ffmpeg")
    mutagen_id3 = pytest.importorskip("mutagen.id3")

    from anees.export.mp3 import MP3Exporter
    from anees.tts.audio import Chapter

    chapters = (
        Chapter(title="Intro", start=0.0, end=0.5),
        Chapter(title="Body", start=0.5, end=1.0),
    )
    audio = _tone(SAMPLE_RATE)

    exporter = MP3Exporter()
    out = exporter.export(
        audio,
        "job-42",
        tmp_path,
        title="My Audiobook",
        chapters=chapters,
    )

    assert out == tmp_path / "job-42.mp3"
    assert out.exists()
    assert out.stat().st_size > 0

    tags = mutagen_id3.ID3(str(out))
    assert tags["TIT2"].text == ["My Audiobook"]
    assert tags["TPE1"].text == ["Anees"]
    assert tags["TALB"].text == ["Anees"]
    assert sorted(f.element_id for f in tags.getall("CHAP")) == ["chp0", "chp1"]


@ffmpeg_required
def test_mp3_export_reports_progress(tmp_path: Path) -> None:
    pytest.importorskip("ffmpeg")

    from anees.export.mp3 import MP3Exporter

    seen: list[int] = []
    exporter = MP3Exporter()
    exporter.export(
        _tone(SAMPLE_RATE),
        "job-43",
        tmp_path,
        on_progress=lambda percent, message: seen.append(percent),
    )

    assert seen
    assert seen[-1] == 100
    assert seen == sorted(seen)


def _probe_bitrate(path: Path) -> int | None:
    """Return the encoded stream bitrate, or None when ffprobe is unavailable."""
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=bit_rate",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    return int(probe.stdout.strip())


@ffmpeg_required
def test_mp3_export_uses_requested_bitrate(tmp_path: Path) -> None:
    pytest.importorskip("ffmpeg")
    subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)

    from anees.export.mp3 import MP3Exporter

    exporter = MP3Exporter(bitrate=64)
    out = exporter.export(_tone(SAMPLE_RATE * 2), "job-44", tmp_path)

    bitrate = _probe_bitrate(out)
    if bitrate is None:
        pytest.skip("ffprobe not available")
    assert 60_000 <= bitrate <= 70_000


@ffmpeg_required
def test_mp3_export_defaults_to_128kbps(tmp_path: Path) -> None:
    pytest.importorskip("ffmpeg")
    subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)

    from anees.export.mp3 import MP3Exporter

    exporter = MP3Exporter()
    out = exporter.export(_tone(SAMPLE_RATE * 2), "job-45", tmp_path)

    bitrate = _probe_bitrate(out)
    if bitrate is None:
        pytest.skip("ffprobe not available")
    assert 120_000 <= bitrate <= 135_000


def test_mp3_exporter_rejects_unsupported_bitrate() -> None:
    pytest.importorskip("ffmpeg")
    from anees.export.mp3 import MP3Exporter

    with pytest.raises(ValueError):
        MP3Exporter(bitrate=99)
