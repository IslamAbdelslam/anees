"""Anees PDF-to-Speech Backend - JSON-RPC Server"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ValidationError

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from anees.export.docx import DOCXExporter
from anees.export.html import HTMLExporter
from anees.models import *
from anees.nlp.arabic import ArabicProcessor
from anees.nlp.language_detect import LanguageDetector
from anees.orchestrator import PipelineOrchestrator
from anees.pdf.chunker import PDFChunker
from anees.pdf.extractor import PDFExtractor
from anees.pdf.ocr import OCREngine, resolve_tesseract_path, resolve_traineddata_dir
from anees.tts.engine import TTSEngine
from anees.tts.voices import VoiceManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("anees")


class JSONRPCRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: Any = None
    method: str
    # Any: shape enforced in handle_request (-32602) so bad params are
    # reported as Invalid params, not Invalid Request.
    params: Any = None


class JSONRPCResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: Any = None
    result: Any = None
    error: dict[str, Any] | None = None


class JSONRPCNotification(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any] | None = None


class AneesRPCServer:
    def __init__(self) -> None:
        self.extractor = PDFExtractor()
        self.ocr = OCREngine(
            extractor=self.extractor,
            tesseract_path=resolve_tesseract_path(),
            traineddata_dir=resolve_traineddata_dir(),
        )
        self.chunker = PDFChunker()
        self.lang_detector = LanguageDetector()
        self.arabic = ArabicProcessor()
        self.tts = TTSEngine()
        self.voices = VoiceManager()
        self.html_exporter = HTMLExporter()
        self.docx_exporter = DOCXExporter()
        self.orchestrator = PipelineOrchestrator(
            self.extractor,
            self.ocr,
            self.chunker,
            self.lang_detector,
            self.arabic,
            self.tts,
            on_progress=self.emit_progress,
        )
        self.methods = {
            "pdf.validate": self.validate_pdf,
            "pdf.extract_text": self.extract_text,
            "pdf.ocr": self.ocr_pdf,
            "lang.detect": self.detect_language,
            "arabic.add_tashkeel": self.add_tashkeel,
            "tts.synthesize": self.synthesize_tts,
            "tts.list_voices": self.list_voices,
            "tts.preview": self.preview_voice,
            "export.html": self.export_html,
            "export.docx": self.export_docx,
            "models.ensure": self.ensure_models,
            "job.start": self.start_job,
            "job.status": self.job_status,
            "ping": self.ping,
        }

    async def handle_request(self, request: JSONRPCRequest) -> JSONRPCResponse:
        method = request.method
        params = request.params or {}

        if method not in self.methods:
            return JSONRPCResponse(
                id=request.id, error={"code": -32601, "message": f"Method not found: {method}"}
            )

        # This server accepts by-name (object) params only, per docs/protocol.md.
        # Per-method schemas are enforced by the issues owning each method (#7-#17).
        if not isinstance(params, dict):
            return JSONRPCResponse(
                id=request.id, error={"code": -32602, "message": "Invalid params: object expected"}
            )

        try:
            result = await self.methods[method](params)
            return JSONRPCResponse(id=request.id, result=result)
        except ValueError as e:
            # Handler-level param errors (missing/invalid params).
            return JSONRPCResponse(id=request.id, error={"code": -32602, "message": str(e)})
        except Exception as e:
            logger.exception(f"Error in method {method}")
            return JSONRPCResponse(id=request.id, error={"code": -32603, "message": str(e)})

    async def handle_notification(self, notification: JSONRPCNotification) -> None:
        logger.debug(f"Notification: {notification.method}")

    def attach_writer(self, writer: Any) -> None:
        """Attach the stdout writer so the server can push notifications."""
        self._writer = writer

    async def emit_notification(self, method: str, params: dict[str, Any] | None = None) -> bool:
        """Push a server→client notification. Returns False if unwritable."""
        writer = getattr(self, "_writer", None)
        if writer is None:
            return False
        notification = JSONRPCNotification(method=method, params=params)
        writer.write((notification.model_dump_json() + "\n").encode())
        await writer.drain()
        return True

    async def emit_progress(self, stage: str, chunk: int, total: int, message: str = "") -> bool:
        progress = chunk / total if total > 0 else 0.0
        return await self.emit_notification(
            "progress",
            {
                "stage": stage,
                "chunk": chunk,
                "total": total,
                "message": message,
                "progress": progress,
            },
        )

    async def emit_log(self, level: str, message: str) -> bool:
        return await self.emit_notification("log", {"level": level, "message": message})

    # --- Method implementations ---

    async def ping(self, params: dict) -> dict:
        return {"status": "ok"}

    async def validate_pdf(self, params: dict) -> dict:
        path = params.get("path")
        if not path:
            raise ValueError("path is required")
        info = self.extractor.validate(path)
        return info.model_dump()

    async def extract_text(self, params: dict) -> dict:
        path = params.get("path")
        page_range = params.get("page_range")
        langs = params.get("langs", [])
        if not path:
            raise ValueError("path is required")
        chunks = self.extractor.extract(path, page_range, langs)
        return {"chunks": [c.model_dump() for c in chunks]}

    async def ocr_pdf(self, params: dict) -> dict:
        path = params.get("path")
        page_range = params.get("page_range")
        langs = params.get("langs", [])
        if not path:
            raise ValueError("path is required")
        loop = asyncio.get_running_loop()

        def on_progress(page: int, total: int, message: str) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self.emit_progress("ocr", page, total, message), loop
            )
            try:
                future.result()
            except Exception:
                logger.exception("Failed to emit OCR progress notification")

        chunks = await asyncio.to_thread(self.ocr.process, path, page_range, langs, on_progress)
        return {"chunks": [c.model_dump() for c in chunks]}

    async def detect_language(self, params: dict) -> dict:
        text = params.get("text")
        segments = params.get("segments")
        if text:
            result = self.lang_detector.detect(text)
            return {"segments": [result.model_dump()]}
        elif segments:
            results = self.lang_detector.detect_batch(segments)
            return {"segments": [r.model_dump() for r in results]}
        raise ValueError("text or segments required")

    async def add_tashkeel(self, params: dict) -> dict:
        text = params.get("text")
        mode = params.get("mode", "semantic")
        if not text:
            raise ValueError("text is required")
        result = self.arabic.add_tashkeel(text, mode)
        return result.model_dump()

    async def synthesize_tts(self, params: dict) -> dict:
        text = params.get("text")
        lang = params.get("lang")
        voice = params.get("voice")
        speed = params.get("speed", 1.0)
        if not all([text, lang, voice]):
            raise ValueError("text, lang, voice required")
        audio = self.tts.synthesize(cast(str, text), cast(str, lang), cast(str, voice), speed)
        import base64

        return {
            "audio_base64": base64.b64encode(audio.tobytes()).decode(),
            "sample_rate": 24000,
            "duration": len(audio) / 24000,
        }

    async def list_voices(self, params: dict) -> dict:
        return self.voices.get_catalog()

    async def preview_voice(self, params: dict) -> dict:
        voice = params.get("voice")
        lang = params.get("lang", "ar")
        if not voice:
            raise ValueError("voice required")
        audio = self.tts.preview(voice, lang)
        import base64

        return {"audio_base64": base64.b64encode(audio.tobytes()).decode(), "sample_rate": 24000}

    async def export_html(self, params: dict) -> dict:
        chunks = params.get("chunks", [])
        metadata = params.get("metadata", {})
        options = params.get("options", {})
        path = self.html_exporter.export(chunks, metadata, options)
        return {"path": path}

    async def export_docx(self, params: dict) -> dict:
        chunks = params.get("chunks", [])
        metadata = params.get("metadata", {})
        options = params.get("options", {})
        path = self.docx_exporter.export(chunks, metadata, options)
        return {"path": path}

    async def ensure_models(self, params: dict) -> dict:
        model_names = params.get("models", ["nabra-7m-distill", "kokoro-7m-distill"])
        paths = self.tts.ensure_models(model_names)
        return {"paths": paths}

    async def start_job(self, params: dict) -> dict:
        job_id = params.get("job_id")
        pdf_path = params.get("pdf_path")
        language_mode = params.get("language_mode", "auto")
        voice = params.get("voice")
        output = params.get("output")
        if not all([job_id, pdf_path]):
            raise ValueError("job_id and pdf_path required")
        job = await self.orchestrator.start_job(
            cast(str, job_id), cast(str, pdf_path), language_mode, voice, cast(str, output)
        )
        return job.to_dict()

    async def job_status(self, params: dict) -> dict:
        job_id = params.get("job_id")
        if not job_id:
            raise ValueError("job_id required")
        status = await self.orchestrator.get_status(job_id)
        return status.to_dict()


async def main() -> None:
    server = AneesRPCServer()

    # Read from stdin, write to stdout
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)
    writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, asyncio.get_event_loop())
    server.attach_writer(writer)

    logger.info("Anees RPC Server started")

    async def send_error_response(req_id: Any, code: int, message: str) -> None:
        response = JSONRPCResponse(id=req_id, error={"code": code, "message": message})
        writer.write((response.model_dump_json() + "\n").encode())
        await writer.drain()

    while True:
        line = await reader.readline()
        if not line:
            break

        payload = line.decode().strip()
        if not payload:
            continue

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            # Unparseable: the id is unknowable, answer with null per spec.
            await send_error_response(None, -32700, "Parse error: invalid JSON")
            continue

        if not isinstance(data, dict):
            await send_error_response(None, -32600, "Invalid Request: object expected")
            continue

        if "id" in data and data["id"] is not None:
            try:
                request = JSONRPCRequest.model_validate(data)
            except ValidationError as e:
                await send_error_response(
                    data["id"], -32600, f"Invalid Request: {e.errors()[0]['msg']}"
                )
                continue
            response = await server.handle_request(request)
            writer.write((response.model_dump_json() + "\n").encode())
            await writer.drain()
        elif "id" in data:
            # Explicit null id: has an id member so it is not a notification,
            # but null ids are invalid -> -32600 echoing null.
            await send_error_response(None, -32600, "Invalid Request: id must not be null")
        else:
            # Notification: handle and never reply (no write below).
            try:
                notification = JSONRPCNotification.model_validate(data)
            except ValidationError as e:
                await send_error_response(None, -32600, f"Invalid Request: {e.errors()[0]['msg']}")
                continue
            await server.handle_notification(notification)
            continue

    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
