"""Pipeline Orchestrator"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from anees.models import ChunkData

logger = logging.getLogger("anees.orchestrator")

ProgressCallback = Callable[[str, int, int, str], Awaitable[Any]]


@dataclass
class Job:
    job_id: str
    pdf_path: str
    language_mode: str
    voice: str | None
    output: str
    status: str = "queued"
    progress: float = 0.0
    current_chunk: int = 0
    total_chunks: int = 0
    stage: str = "validation"
    message: str = ""
    output_path: str | None = None
    error: str | None = None
    started_at: str = ""
    updated_at: str = ""
    chunks: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


class PipelineOrchestrator:
    def __init__(
        self,
        extractor: Any,
        ocr: Any,
        chunker: Any,
        lang_detector: Any,
        arabic: Any,
        tts: Any,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.extractor = extractor
        self.ocr = ocr
        self.chunker = chunker
        self.lang_detector = lang_detector
        self.arabic = arabic
        self.tts = tts
        self.on_progress = on_progress
        self.jobs: dict[str, Job] = {}

    async def start_job(
        self, job_id: str, pdf_path: str, language_mode: str, voice: str | None, output: str
    ) -> Job:
        job = Job(
            job_id=job_id,
            pdf_path=pdf_path,
            language_mode=language_mode,
            voice=voice,
            output=output,
        )
        self.jobs[job_id] = job

        # Start processing in background
        asyncio.create_task(self._process_job(job))
        return job

    async def get_status(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            raise KeyError(f"Job not found: {job_id}")
        return self.jobs[job_id]

    async def _emit(self, stage: str, chunk: int, total: int, message: str) -> None:
        if self.on_progress is None:
            return
        try:
            await self.on_progress(stage, chunk, total, message)
        except Exception:
            logger.exception("Progress callback failed")

    def _state_path(self, job: Job) -> Path:
        return Path(job.output).with_suffix(".state.json")

    def _load_state(self, job: Job) -> dict[str, Any]:
        path = self._state_path(job)
        if not path.exists():
            return {"job_id": job.job_id, "chunks": {}}
        try:
            state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Ignoring unreadable job state: %s", path)
            return {"job_id": job.job_id, "chunks": {}}
        state.setdefault("chunks", {})
        return state

    def _save_state(self, job: Job, state: dict[str, Any]) -> None:
        path = self._state_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _process_batches(self, job: Job, info: Any) -> list[ChunkData]:
        chunk_ranges = self.chunker.split(info.pages)
        job.total_chunks = len(chunk_ranges)
        state = self._load_state(job)
        all_chunks: list[ChunkData] = []
        failed = 0

        for idx, (start, end) in enumerate(chunk_ranges):
            chunk_no = idx + 1
            saved = state["chunks"].get(str(idx))
            if saved and saved.get("status") == "completed" and "data" in saved:
                all_chunks.extend(ChunkData.model_validate(c) for c in saved["data"])
                await self._emit(
                    "extraction", chunk_no, job.total_chunks, f"Restored pages {start + 1}-{end}"
                )
                continue

            job.current_chunk = idx
            try:
                chunks = await self._process_batch(job, info, start, end, chunk_no)
            except Exception as e:
                failed += 1
                logger.exception("Job %s: batch %s failed", job.job_id, chunk_no)
                state["chunks"][str(idx)] = {
                    "index": idx,
                    "page_range": [start + 1, end],
                    "status": "failed",
                    "error": str(e),
                }
                self._save_state(job, state)
                continue

            all_chunks.extend(chunks)
            state["chunks"][str(idx)] = {
                "index": idx,
                "page_range": [start + 1, end],
                "status": "completed",
                "data": [c.model_dump() for c in chunks],
            }
            self._save_state(job, state)

        job.chunks = [c.model_dump() for c in all_chunks]
        if failed:
            job.error = f"{failed} of {job.total_chunks} batches failed; retry to process them"
        return all_chunks

    async def _process_batch(
        self, job: Job, info: Any, start: int, end: int, chunk_no: int
    ) -> list[ChunkData]:
        job.stage = "extraction"
        job.message = f"Processing pages {start + 1}-{end}"
        self._update_progress(job, 0.1 + 0.7 * (chunk_no - 1) / max(1, job.total_chunks))
        await self._emit("extraction", chunk_no, job.total_chunks, job.message)

        chunks: list[ChunkData]
        if info.has_text:
            chunks = self.extractor.extract(job.pdf_path, (start, end))
        else:
            chunks = self.ocr.process(job.pdf_path, (start, end))

        job.stage = "language_detection"
        job.message = f"Detecting languages in chunk {chunk_no}/{job.total_chunks}"
        await self._emit("language_detection", chunk_no, job.total_chunks, job.message)
        for chunk in chunks:
            for seg in chunk.segments:
                if seg.lang == "mixed":
                    det = self.lang_detector.detect(seg.text)
                    seg.lang = det.lang
                    seg.confidence = det.confidence

        job.stage = "tashkeel"
        job.message = f"Adding tashkeel to chunk {chunk_no}/{job.total_chunks}"
        await self._emit("tashkeel", chunk_no, job.total_chunks, job.message)
        for chunk in chunks:
            for seg in chunk.segments:
                if seg.lang in ("ar", "mixed"):
                    result = self.arabic.add_tashkeel(seg.text, "semantic")
                    seg.text = result.text_with_diacritics

        return chunks

    async def _process_job(self, job: Job) -> None:
        try:
            job.status = "processing"
            job.updated_at = datetime.now().isoformat()

            # 1. Validate PDF
            job.stage = "validation"
            job.message = "Validating PDF..."
            self._update_progress(job, 0.05)

            info = self.extractor.validate(job.pdf_path)
            logger.info(
                "Job %s: PDF has %s pages, %s chunks",
                job.job_id,
                info.pages,
                len(self.chunker.split(info.pages)),
            )

            # 2. Process each batch: extract -> detect lang -> tashkeel
            all_chunks = await self._process_batches(job, info)
            if not all_chunks:
                raise RuntimeError("No text could be extracted from the PDF")

            # 3. TTS Synthesis
            job.stage = "tts"
            job.message = "Generating audio..."
            self._update_progress(job, 0.85)

            voice = job.voice or "af_msa"
            all_audio = []

            for chunk in all_chunks:
                for seg in chunk.segments:
                    lang = seg.lang if seg.lang != "mixed" else "ar"
                    audio = self.tts.synthesize(seg.text, lang, voice)
                    all_audio.append(audio)

            # 4. Merge and export
            job.stage = "merging"
            job.message = "Merging audio and exporting..."
            self._update_progress(job, 0.95)

            import numpy as np
            from scipy.io import wavfile

            # Concatenate audio
            combined = np.concatenate(all_audio)

            # Save output
            output_path = Path(job.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Save as WAV first
            wav_path = output_path.with_suffix(".wav")
            wavfile.write(str(wav_path), 24000, combined.astype(np.float32))

            # Convert to MP3 using ffmpeg if available (off the event loop)
            try:
                import subprocess

                mp3_path = output_path.with_suffix(".mp3")
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(wav_path),
                        "-codec:a",
                        "libmp3lame",
                        "-b:a",
                        "128k",
                        str(mp3_path),
                    ],
                    check=True,
                    capture_output=True,
                )
                job.output_path = str(mp3_path)
                wav_path.unlink()
            except Exception:
                logger.exception("ffmpeg MP3 conversion failed; keeping WAV")
                job.output_path = str(wav_path)

            if job.error:
                job.status = "failed"
                job.message = job.error
            else:
                job.status = "completed"
                job.progress = 1.0
                job.message = "Completed successfully"
            job.updated_at = datetime.now().isoformat()
            logger.info("Job %s finished: %s", job.job_id, job.status)

        except Exception as e:
            logger.exception(f"Job {job.job_id} failed")
            job.status = "failed"
            job.error = str(e)
            job.message = f"Failed: {e}"
            job.updated_at = datetime.now().isoformat()

    def _update_progress(self, job: Job, progress: float) -> None:
        job.progress = max(0.0, min(1.0, progress))
        job.updated_at = datetime.now().isoformat()
