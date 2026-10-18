"""Batch orchestration tests (Issue #10).

Stubs replace the heavy pipeline stages so the batch loop, progress
notifications, per-batch error handling, and JSON resume are exercised
without numpy/scipy/onnxruntime. TTS is asserted to never run here: these
tests cover batch orchestration, not synthesis.
"""

import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

pytest.importorskip("pydantic")

from anees.models import (
    ChunkData,
    LanguageDetection,
    PdfInfo,
    TashkeelResult,
    TextSegment,
)
from anees.orchestrator import Job, PipelineOrchestrator
from anees.pdf.chunker import PDFChunker


class _StubExtractor:
    def __init__(self, pages: int, fail_on: int | None = None, lang: str = "ar") -> None:
        self.pages = pages
        self.fail_on = fail_on
        self.lang = lang
        self.calls: list[tuple[int, int]] = []

    def validate(self, path: str) -> PdfInfo:
        return PdfInfo(valid=True, pages=self.pages, has_text=True, file_size=0, encrypted=False)

    def extract(
        self,
        path: str,
        page_range: Sequence[int] | None = None,
        langs: list[str] | None = None,
    ) -> list[ChunkData]:
        assert page_range is not None
        start, end = page_range
        self.calls.append((start, end))
        if self.fail_on is not None and start == self.fail_on:
            raise RuntimeError("boom")
        return [
            ChunkData(
                chunk_index=0,
                page_range=[start + 1, end],
                segments=[
                    TextSegment(page=p, text=f"page {p}", lang=self.lang)
                    for p in range(start + 1, end + 1)
                ],
                status="completed",
                progress=1.0,
            )
        ]


class _StubDetector:
    def __init__(self) -> None:
        self.calls = 0

    def detect(self, text: str) -> LanguageDetection:
        self.calls += 1
        return LanguageDetection(lang="en", confidence=0.9)


class _StubArabic:
    def __init__(self) -> None:
        self.calls = 0

    def add_tashkeel(self, text: str, mode: str = "semantic") -> TashkeelResult:
        self.calls += 1
        return TashkeelResult(text_with_diacritics=text)


class _StubTTS:
    def synthesize(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("TTS must not run in batch orchestration tests")


def _make(
    extractor: _StubExtractor,
    events: list[tuple[str, int, int, str]],
    output: Path,
    detector: _StubDetector | None = None,
) -> tuple[PipelineOrchestrator, _StubArabic]:
    async def on_progress(stage: str, chunk: int, total: int, message: str) -> None:
        events.append((stage, chunk, total, message))

    arabic = _StubArabic()
    orch = PipelineOrchestrator(
        extractor,
        object(),
        PDFChunker(),
        detector or _StubDetector(),
        arabic,
        _StubTTS(),
        on_progress=on_progress,
    )
    return orch, arabic


def _job(output: Path) -> Job:
    return Job(
        job_id="job-1", pdf_path="x.pdf", language_mode="auto", voice=None, output=str(output)
    )


def test_batches_emit_progress_and_recover_all_pages(tmp_path: Path) -> None:
    events: list[tuple[str, int, int, str]] = []
    extractor = _StubExtractor(pages=87)
    orch, arabic = _make(extractor, events, tmp_path / "out.mp3")

    chunks = asyncio.run(orch._process_batches(_job(tmp_path / "out.mp3"), extractor.validate("x")))

    assert orch.chunker.split(87)[-1] == (80, 87)
    assert len(extractor.calls) == 9
    assert sum(len(c.segments) for c in chunks) == 87
    assert [e[0] for e in events[:3]] == ["extraction", "language_detection", "tashkeel"]
    assert ("extraction", 3, 9, "Processing pages 21-30") in events
    assert ("tashkeel", 9, 9, "Adding tashkeel to chunk 9/9") in events
    assert arabic.calls == 87


def test_batches_write_state_json_per_chunk(tmp_path: Path) -> None:
    events: list[tuple[str, int, int, str]] = []
    output = tmp_path / "out.mp3"
    extractor = _StubExtractor(pages=15)
    orch, _ = _make(extractor, events, output)

    asyncio.run(orch._process_batches(_job(output), extractor.validate("x")))

    state = json.loads(output.with_suffix(".state.json").read_text(encoding="utf-8"))
    assert len(state["chunks"]) == 2
    assert state["chunks"]["0"]["status"] == "completed"
    assert state["chunks"]["0"]["page_range"] == [1, 10]
    assert state["chunks"]["1"]["page_range"] == [11, 15]


def test_resume_skips_completed_batches(tmp_path: Path) -> None:
    output = tmp_path / "out.mp3"
    first = _StubExtractor(pages=15)
    orch1, _ = _make(first, [], output)
    asyncio.run(orch1._process_batches(_job(output), first.validate("x")))
    assert first.calls == [(0, 10), (10, 15)]

    events: list[tuple[str, int, int, str]] = []
    second = _StubExtractor(pages=15)
    orch2, _ = _make(second, events, output)
    chunks = asyncio.run(orch2._process_batches(_job(output), second.validate("x")))

    assert second.calls == []
    assert sum(len(c.segments) for c in chunks) == 15
    assert [e[0] for e in events] == ["extraction", "extraction"]
    assert all("Restored" in e[3] for e in events)


def test_failed_batch_is_recorded_and_processing_continues(tmp_path: Path) -> None:
    output = tmp_path / "out.mp3"
    extractor = _StubExtractor(pages=25, fail_on=10)
    orch, _ = _make(extractor, [], output)
    job = _job(output)

    chunks = asyncio.run(orch._process_batches(job, extractor.validate("x")))

    assert extractor.calls == [(0, 10), (10, 20), (20, 25)]
    assert sum(len(c.segments) for c in chunks) == 15
    assert job.error is not None and "1 of 3 batches failed" in job.error
    state = json.loads(output.with_suffix(".state.json").read_text(encoding="utf-8"))
    assert state["chunks"]["0"]["status"] == "completed"
    assert state["chunks"]["1"]["status"] == "failed"
    assert state["chunks"]["1"]["error"] == "boom"
    assert state["chunks"]["2"]["status"] == "completed"


def test_retry_reprocesses_only_failed_batches(tmp_path: Path) -> None:
    output = tmp_path / "out.mp3"
    failing = _StubExtractor(pages=25, fail_on=10)
    orch1, _ = _make(failing, [], output)
    asyncio.run(orch1._process_batches(_job(output), failing.validate("x")))

    retry = _StubExtractor(pages=25)
    orch2, _ = _make(retry, [], output)
    chunks = asyncio.run(orch2._process_batches(_job(output), retry.validate("x")))

    assert retry.calls == [(10, 20)]
    assert sum(len(c.segments) for c in chunks) == 25


def test_mixed_segments_are_language_detected(tmp_path: Path) -> None:
    output = tmp_path / "out.mp3"
    extractor = _StubExtractor(pages=10, lang="mixed")
    detector = _StubDetector()
    orch, _ = _make(extractor, [], output, detector=detector)

    chunks = asyncio.run(orch._process_batches(_job(output), extractor.validate("x")))

    assert detector.calls == 10
    assert all(seg.lang == "en" for seg in chunks[0].segments)
