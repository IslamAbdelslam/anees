"""Live JSON-RPC round-trip tests (Issue #2, reviewer follow-up).

Spawns the real backend over stdio and asserts wire behavior:
ping, notification no-reply, parse/invalid-request/method-not-found/
invalid-params error codes.

Skipped when backend dependencies are not installed (e.g. CI, which only
installs ruff+pytest): doc-contract tests in test_protocol.py cover CI.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

try:
    import anees.server  # noqa: F401 -- backend deps present?
except ImportError as e:
    pytest.skip(f"backend dependencies missing: {e}", allow_module_level=True)


def _run_session(lines: list[str], timeout: int = 120) -> list[dict]:
    """Feed NDJSON lines to a fresh server; return parsed response lines."""
    env = dict(os.environ, PYTHONPATH=str(SRC))
    proc = subprocess.Popen(
        [sys.executable, str(SRC / "anees" / "server.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    out, _ = proc.communicate(input="".join(lines), timeout=timeout)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_live_protocol_conformance() -> None:
    responses = _run_session(
        [
            '{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n',
            '{"jsonrpc": "2.0", "method": "log", "params": {"level": "info", "message": "hi"}}\n',
            "not json\n",
            '{"jsonrpc": "2.0", "id": 2, "method": "nope"}\n',
            '{"jsonrpc": "1.0", "id": 3, "method": "ping"}\n',
            '{"jsonrpc": "2.0", "id": 4, "method": "ping", "params": [1, 2]}\n',
            '{"jsonrpc": "2.0", "id": null, "method": "ping"}\n',
        ]
    )
    # The notification (line 2) MUST NOT produce a response: 6 replies, not 7.
    assert len(responses) == 6, responses

    ping, parse_err, not_found, bad_version, bad_params, null_id = responses

    assert ping == {"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}}

    assert parse_err["id"] is None
    assert parse_err["error"]["code"] == -32700

    assert not_found["id"] == 2
    assert not_found["error"]["code"] == -32601

    assert bad_version["id"] == 3
    assert bad_version["error"]["code"] == -32600

    assert bad_params["id"] == 4
    assert bad_params["error"]["code"] == -32602

    assert null_id["id"] is None
    assert null_id["error"]["code"] == -32600
