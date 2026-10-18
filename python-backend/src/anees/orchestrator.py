"""Pipeline Orchestrator"""

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("anees.orchestrator")


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
    ) -> None:
        self.extractor = extractor
        self.ocr = ocr
        self.chunker = chunker
        self.lang_detector = lang_detector
        self.arabic = arabic
        self.tts = tts
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

    async def _process_job(self, job: Job) -> None:
        try:
            job.status = "processing"
            job.updated_at = datetime.now().isoformat()

            # 1. Validate PDF
            job.stage = "validation"
            job.message = "Validating PDF..."
            self._update_progress(job, 0.05)

            info = self.extractor.validate(job.pdf_path)
            job.total_chunks = len(self.chunker.split(info.pages))
            logger.info(f"Job {job.job_id}: PDF has {info.pages} pages, {job.total_chunks} chunks")

            # 2. Process each chunk
            all_chunks = []
            chunk_ranges = self.chunker.split(info.pages)

            for chunk_idx, (start, end) in enumerate(chunk_ranges):
                job.current_chunk = chunk_idx
                job.stage = "extraction"
                job.message = f"Processing pages {start + 1}-{end}"
                self._update_progress(job, 0.1 + 0.7 * chunk_idx / job.total_chunks)

                # Extract or OCR
                if info.has_text:
                    chunks = self.extractor.extract(job.pdf_path, (start, end))
                else:
                    chunks = self.ocr.process(job.pdf_path, (start, end))

                # Language detection
                job.stage = "language_detection"
                job.message = f"Detecting languages in chunk {chunk_idx + 1}/{job.total_chunks}"
                for chunk in chunks:
                    for seg in chunk.segments:
                        if seg.lang == "mixed":
                            det = self.lang_detector.detect(seg.text)
                            seg.lang = det.lang
                            seg.confidence = det.confidence

                # Tashkeel for Arabic
                job.stage = "tashkeel"
                job.message = f"Adding tashkeel to chunk {chunk_idx + 1}/{job.total_chunks}"
                for chunk in chunks:
                    for seg in chunk.segments:
                        if seg.lang in ("ar", "mixed"):
                            result = self.arabic.add_tashkeel(seg.text, "semantic")
                            seg.text = result.text_with_diacritics

                all_chunks.extend(chunks)
                job.chunks.extend([c.__dict__ for c in chunks])

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

            job.status = "completed"
            job.progress = 1.0
            job.message = "Completed successfully"
            job.updated_at = datetime.now().isoformat()
            logger.info(f"Job {job.job_id} completed: {job.output_path}")

        except Exception as e:
            logger.exception(f"Job {job.job_id} failed")
            job.status = "failed"
            job.error = str(e)
            job.message = f"Failed: {e}"
            job.updated_at = datetime.now().isoformat()

    def _update_progress(self, job: Job, progress: float) -> None:
        job.progress = max(0.0, min(1.0, progress))
        job.updated_at = datetime.now().isoformat()
