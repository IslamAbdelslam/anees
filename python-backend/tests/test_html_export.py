"""HTML export tests (Issue #16)."""

import asyncio
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

pytest.importorskip("jinja2")
pytest.importorskip("pydantic")

from anees.export.html import HTMLExporter
from anees.models import ChunkData, TextSegment


def _chunks_dict() -> list[dict]:
    return [
        {
            "chunk_index": 0,
            "page_range": [1, 2],
            "segments": [
                {"page": 1, "text": "مرحبا", "lang": "ar", "confidence": 0.5},
                {"page": 2, "text": "Hello", "lang": "en", "confidence": 0.99},
            ],
        }
    ]


def test_html_export_dict_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    exporter = HTMLExporter()
    path = exporter.export(
        _chunks_dict(),
        {"title": "Title", "author": "Author", "language": "ar"},
        {"tashkeel": True, "rtl": True, "audio_path": "audio.mp3"},
    )
    assert isinstance(path, str)
    assert path.endswith(".html")
    html = Path(path).read_text(encoding="utf-8")
    assert "<article>" in html
    assert "<section" in html
    assert "<nav" in html
    assert 'dir="rtl"' in html
    assert 'dir="ltr"' in html
    assert "low-confidence" in html
    assert "<audio" in html
    assert "audio.mp3" in html
    assert "tashkeel-toggle" in html
    assert "scroll-behavior: smooth" in html
    assert "@media print" in html
    assert "prefers-color-scheme" in html
    assert "IBM Plex Sans Arabic" in html
    assert "data:font/woff2;base64," in html
    assert "#cd7f32" in html


def test_html_export_tashkeel_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    exporter = HTMLExporter()
    path = exporter.export(
        _chunks_dict(),
        {"title": "Title", "language": "en"},
        {"tashkeel": False, "rtl": False},
    )
    html = Path(path).read_text(encoding="utf-8")
    assert 'dir="ltr"' in html
    assert "Tashkeel enabled" not in html


def test_html_export_chunkdata_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    chunks = [
        ChunkData(
            chunk_index=0,
            page_range=[3, 4],
            segments=[
                TextSegment(page=3, text="text", lang="mixed", confidence=1.0),
            ],
        )
    ]
    exporter = HTMLExporter()
    path = exporter.export(chunks, {"title": "Doc"}, {"tashkeel": True, "rtl": True})
    html = Path(path).read_text(encoding="utf-8")
    assert "Pages 3–4" in html
    assert 'dir="rtl"' in html


def test_html_export_rpc_contract() -> None:
    try:
        import anees.server as server_mod
    except ImportError as exc:
        pytest.skip(f"backend dependencies missing: {exc}")

    async def run() -> dict:
        server = server_mod.AneesRPCServer()
        return await server.export_html(
            {
                "chunks": _chunks_dict(),
                "metadata": {"title": "T"},
                "options": {"tashkeel": True, "rtl": True},
            }
        )

    result = asyncio.run(run())
    assert "html_path" in result
    assert Path(result["html_path"]).exists()
