"""Anees PDF-to-Speech Backend - JSON-RPC Server"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from anees.pdf.extractor import PDFExtractor, PDFChunker
from anees.pdf.ocr import OCREngine
from anees.nlp.language_detect import LanguageDetector
from anees.nlp.arabic import ArabicProcessor
from anees.tts.engine import TTSEngine
from anees.tts.voices import VoiceManager
from anees.export.html import HTMLExporter
from anees.export.docx import DOCXExporter
from anees.orchestrator import PipelineOrchestrator
from anees.models import *

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("anees")


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Any = None
    method: str
    params: Optional[Dict[str, Any]] = None


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Any = None
    result: Any = None
    error: Optional[Dict[str, Any]] = None


class JSONRPCNotification(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Optional[Dict[str, Any]] = None


class AneesRPCServer:
    def __init__(self):
        self.extractor = PDFExtractor()
        self.ocr = OCREngine()
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
                id=request.id,
                error={"code": -32601, "message": f"Method not found: {method}"}
            )

        try:
            result = await self.methods[method](params)
            return JSONRPCResponse(id=request.id, result=result)
        except Exception as e:
            logger.exception(f"Error in method {method}")
            return JSONRPCResponse(
                id=request.id,
                error={"code": -32603, "message": str(e)}
            )

    async def handle_notification(self, notification: JSONRPCNotification):
        logger.debug(f"Notification: {notification.method}")

    # --- Method implementations ---

    async def ping(self, params: Dict) -> Dict:
        return {"status": "ok"}

    async def validate_pdf(self, params: Dict) -> Dict:
        path = params.get("path")
        if not path:
            raise ValueError("path is required")
        info = self.extractor.validate(path)
        return info.model_dump()

    async def extract_text(self, params: Dict) -> Dict:
        path = params.get("path")
        page_range = params.get("page_range")
        langs = params.get("langs", [])
        if not path:
            raise ValueError("path is required")
        chunks = self.extractor.extract(path, page_range, langs)
        return {"chunks": [c.model_dump() for c in chunks]}

    async def ocr_pdf(self, params: Dict) -> Dict:
        path = params.get("path")
        page_range = params.get("page_range")
        langs = params.get("langs", [])
        if not path:
            raise ValueError("path is required")
        chunks = self.ocr.process(path, page_range, langs)
        return {"chunks": [c.model_dump() for c in chunks]}

    async def detect_language(self, params: Dict) -> Dict:
        text = params.get("text")
        segments = params.get("segments")
        if text:
            result = self.lang_detector.detect(text)
            return {"segments": [result.model_dump()]}
        elif segments:
            results = self.lang_detector.detect_batch(segments)
            return {"segments": [r.model_dump() for r in results]}
        raise ValueError("text or segments required")

    async def add_tashkeel(self, params: Dict) -> Dict:
        text = params.get("text")
        mode = params.get("mode", "semantic")
        if not text:
            raise ValueError("text is required")
        result = self.arabic.add_tashkeel(text, mode)
        return result.model_dump()

    async def synthesize_tts(self, params: Dict) -> Dict:
        text = params.get("text")
        lang = params.get("lang")
        voice = params.get("voice")
        speed = params.get("speed", 1.0)
        if not all([text, lang, voice]):
            raise ValueError("text, lang, voice required")
        audio = self.tts.synthesize(text, lang, voice, speed)
        import base64
        return {"audio_base64": base64.b64encode(audio.tobytes()).decode(), "sample_rate": 24000, "duration": len(audio) / 24000}

    async def list_voices(self, params: Dict) -> Dict:
        return self.voices.get_catalog()

    async def preview_voice(self, params: Dict) -> Dict:
        voice = params.get("voice")
        lang = params.get("lang", "ar")
        if not voice:
            raise ValueError("voice required")
        audio = self.tts.preview(voice, lang)
        import base64
        return {"audio_base64": base64.b64encode(audio.tobytes()).decode(), "sample_rate": 24000}

    async def export_html(self, params: Dict) -> Dict:
        chunks = params.get("chunks", [])
        metadata = params.get("metadata", {})
        options = params.get("options", {})
        path = self.html_exporter.export(chunks, metadata, options)
        return {"path": path}

    async def export_docx(self, params: Dict) -> Dict:
        chunks = params.get("chunks", [])
        metadata = params.get("metadata", {})
        options = params.get("options", {})
        path = self.docx_exporter.export(chunks, metadata, options)
        return {"path": path}

    async def ensure_models(self, params: Dict) -> Dict:
        model_names = params.get("models", ["nabra-7m-distill", "kokoro-7m-distill"])
        paths = self.tts.ensure_models(model_names)
        return {"paths": paths}

    async def start_job(self, params: Dict) -> Dict:
        job_id = params.get("job_id")
        pdf_path = params.get("pdf_path")
        language_mode = params.get("language_mode", "auto")
        voice = params.get("voice")
        output = params.get("output")
        if not all([job_id, pdf_path]):
            raise ValueError("job_id and pdf_path required")
        job = await self.orchestrator.start_job(job_id, pdf_path, language_mode, voice, output)
        return job.to_dict()

    async def job_status(self, params: Dict) -> Dict:
        job_id = params.get("job_id")
        if not job_id:
            raise ValueError("job_id required")
        status = await self.orchestrator.get_status(job_id)
        return status.to_dict()


async def main():
    server = AneesRPCServer()

    # Read from stdin, write to stdout
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)
    writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, asyncio.get_event_loop())

    logger.info("Anees RPC Server started")

    while True:
        line = await reader.readline()
        if not line:
            break

        line = line.decode().strip()
        if not line:
            continue

        try:
            data = json.loads(line)
            if "id" in data and data.get("id") is not None:
                request = JSONRPCRequest(**data)
                response = await server.handle_request(request)
                writer.write((response.model_dump_json() + "\n").encode())
                await writer.drain()
            else:
                notification = JSONRPCNotification(**data)
                await server.handle_notification(notification)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON: {line}")
        except Exception as e:
            logger.exception(f"Error processing request: {e}")

    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())