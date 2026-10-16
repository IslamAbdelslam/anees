"""Pydantic models for Anees backend"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TextSegment(BaseModel):
    page: int
    text: str
    lang: str
    confidence: float = 1.0
    bbox: list[float] | None = None


class ChunkData(BaseModel):
    chunk_index: int
    page_range: list[int]
    segments: list[TextSegment]
    status: Literal["pending", "processing", "completed", "flagged", "failed"] = "pending"
    progress: float = 0.0
    error: str | None = None


class PdfInfo(BaseModel):
    pages: int
    has_text: bool
    file_size: int
    encrypted: bool
    detected_langs: list[str] = []


class VoiceInfo(BaseModel):
    id: str
    name: str
    language: str
    gender: str
    style: str
    recommended: bool = False


class TashkeelResult(BaseModel):
    text_with_diacritics: str
    changes: int = 0


class LanguageDetection(BaseModel):
    lang: str
    confidence: float


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "processing", "completed", "failed", "cancelled"]
    progress: float
    current_chunk: int
    total_chunks: int
    stage: str
    message: str
    output_path: str | None = None
    error: str | None = None
    started_at: datetime
    updated_at: datetime


class UserSettings(BaseModel):
    default_voice_ar: str = "af_msa"
    default_voice_en: str = "af_bella"
    default_speed: float = 1.0
    default_tashkeel_mode: Literal["full", "semantic", "endings"] = "semantic"
    output_directory: str = ""
    auto_save_interval_sec: int = 30
    max_concurrent_batches: int = 4
    theme: str = "system"


class ProjectMetadata(BaseModel):
    id: str
    name: str
    pdf_path: str
    created_at: datetime
    updated_at: datetime
    status: Literal["queued", "processing", "completed", "failed", "cancelled"]
    total_pages: int
    total_chunks: int
    language_mode: Literal["auto", "ar", "en", "mixed", "multi"]
    force_ocr: bool
    settings_hash: str


class ExportOptions(BaseModel):
    format: Literal["html", "docx", "pdf"]
    include_tashkeel: bool = True
    rtl: bool = True
    chapter_navigation: bool = True
