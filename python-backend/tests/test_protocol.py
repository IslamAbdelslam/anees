"""Protocol contract tests (Issue #2).

Stdlib-only on purpose: CI installs just ruff+pytest for this job, so these
tests validate the documented wire contract in docs/protocol.md without
importing the backend (heavy deps). They fail if the spec's JSON examples
drift into invalid shapes.
"""

import json
import re
from pathlib import Path

SPEC = Path(__file__).resolve().parents[2] / "docs" / "protocol.md"

EXPECTED_METHODS = [
    "ping",
    "pdf.validate",
    "pdf.extract_text",
    "pdf.ocr",
    "lang.detect",
    "arabic.add_tashkeel",
    "tts.synthesize",
    "tts.list_voices",
    "tts.preview",
    "export.html",
    "export.docx",
    "models.ensure",
    "job.start",
    "job.status",
]

EXPECTED_ERROR_CODES = [-32700, -32600, -32601, -32602, -32603]


def _json_blocks():
    text = SPEC.read_text(encoding="utf-8")
    return re.findall(r"```json\n(.*?)```", text, re.DOTALL)


def test_spec_exists_and_lists_all_methods():
    text = SPEC.read_text(encoding="utf-8")
    for method in EXPECTED_METHODS:
        assert f"`{method}`" in text, f"method missing from spec: {method}"
    for code in EXPECTED_ERROR_CODES:
        assert f"`{code}`" in text, f"error code missing from spec: {code}"


def test_all_spec_json_blocks_parse():
    blocks = _json_blocks()
    assert len(blocks) >= 3, "spec should show request/response/error examples"
    for block in blocks:
        json.loads(block)


def test_request_example_shape():
    text = SPEC.read_text(encoding="utf-8")
    example = json.loads(
        re.search(r"### 2\.1 Request.*?\n```json\n(.*?)```", text, re.DOTALL).group(1)
    )
    assert example["jsonrpc"] == "2.0"
    assert "id" in example and example["id"] is not None
    assert isinstance(example["method"], str)
    assert "result" not in example and "error" not in example


def test_response_examples_shape():
    blocks = _json_blocks()
    success = next(b for b in blocks if "result" in json.loads(b))
    failure = next(b for b in blocks if "error" in json.loads(b))
    success, failure = json.loads(success), json.loads(failure)
    assert success["jsonrpc"] == "2.0" and "id" in success
    assert "error" not in success
    assert failure["jsonrpc"] == "2.0" and "id" in failure
    assert failure["error"]["code"] in EXPECTED_ERROR_CODES
    assert isinstance(failure["error"]["message"], str) and failure["error"]["message"]
    assert "result" not in failure


def test_notification_table_shapes():
    text = SPEC.read_text(encoding="utf-8").replace("\\", "")
    assert "`progress`" in text and "progress: 0.0–1.0" in text
    assert "`log`" in text and "error|warn|info|debug" in text
