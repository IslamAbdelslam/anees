# Anees JSON-RPC 2.0 Protocol (Issue #2)

Contract between the Rust host (`anees-core` `IpcClient`) and the Python
backend (`anees.server.AneesRPCServer`). Follows
[JSON-RPC 2.0](https://www.jsonrpc.org/specification); deviations are listed
under Conformance.

## 1. Transport

- **Channel**: child-process `stdin`/`stdout` pipes (no sockets). Host writes
  requests to the backend's `stdin`; backend writes responses/notifications
  to `stdout`. Backend logs go to `stderr` only.
- **Framing**: NDJSON — exactly one JSON object per line, `\n`-terminated,
  UTF-8. Blank lines are ignored.
- **Ownership**: reader and writer halves are independently owned on both
  sides; never hold a shared lock across a blocking read.

## 2. Messages

### 2.1 Request (client → server)

```json
{"jsonrpc": "2.0", "id": 7, "method": "ping", "params": null}
```

- `jsonrpc`: MUST be exactly `"2.0"` (anything else → `-32600`).
- `id`: number or string, echoed verbatim in the response. `null` ids are
  discouraged; a message **without** an `id` member is a notification.
- `method`: one of the 14 methods in §3 (unknown → `-32601`).
- `params`: object (per-method schema) or omitted. Wrong shape → `-32602`.

### 2.2 Response (server → client)

Success:

```json
{"jsonrpc": "2.0", "id": 7, "result": {"status": "ok"}}
```

Error:

```json
{"jsonrpc": "2.0", "id": 7, "error": {"code": -32601, "message": "Method not found: foo"}}
```

- Exactly one of `result` / `error` is present. `id` echoes the request;
  `id: null` is used only when the request id is unknown (parse/validation
  failures).
- `message` is a concise single sentence; `data` carries details (optional).

### 2.3 Notification

Client→server: a request object **without** `id` (fire-and-forget).
Server→client: `{jsonrpc, method, params}` (see §4). **The server MUST NOT
reply to a notification.**

## 3. Methods

| Method | Params | Result |
|---|---|---|
| `ping` | — | `{"status": "ok"}` |
| `pdf.validate` | `{"path": str}` | `PdfInfo` |
| `pdf.extract_text` | `{"path": str, "page_range"?: str, "langs"?: str[]}` | `{"chunks": ChunkData[]}` |
| `pdf.ocr` | same as extract | `{"chunks": ChunkData[]}` |
| `lang.detect` | `{"text": str}` or `{"segments": TextSegment[]}` | `{"segments": LanguageDetection[]}` |
| `arabic.add_tashkeel` | `{"text": str, "mode"?: "full"\|"semantic"\|"endings"}` | `TashkeelResult` |
| `tts.synthesize` | `{"text": str, "lang": str, "voice": str, "speed"?: float}` | `{"audio_base64": str, "sample_rate": 24000, "duration": float}` |
| `tts.list_voices` | — | voice catalog `{lang: {id: VoiceInfo}}` |
| `tts.preview` | `{"voice": str, "lang"?: str}` | `{"audio_base64": str, "sample_rate": 24000}` |
| `export.html` | `{"chunks": ChunkData[], "metadata": object, "options"?: object}` | `{"path": str}` |
| `export.docx` | same as html | `{"path": str}` |
| `models.ensure` | `{"models"?: str[]}` | `{"paths": {name: path}}` |
| `job.start` | `{"job_id": str, "pdf_path": str, "language_mode"?: str, "voice"?: str, "output": str}` | `Job` |
| `job.status` | `{"job_id": str}` | `Job` |

Shared shapes (`ChunkData`, `TextSegment`, `PdfInfo`, `VoiceInfo`,
`TashkeelResult`, `LanguageDetection`, `Job`) are defined once in
`anees-core/src/models.rs` (Rust) and `python-backend/src/anees/models.py`
(Python, pydantic v2); the two MUST stay field-compatible.

## 4. Server → client notifications

| Method | Payload |
|---|---|
| `progress` | `{stage, chunk, total, message, progress: 0.0–1.0}` |
| `log` | `{level: error\|warn\|info\|debug, message, context?}` |

## 5. Error codes

| Code | Meaning | When |
|---|---|---|
| `-32700` | Parse error | Line is not valid JSON |
| `-32600` | Invalid Request | Not a Request object (`jsonrpc` ≠ `"2.0"`, missing `method`, …) |
| `-32601` | Method not found | Unknown method name |
| `-32602` | Invalid params | Params fail per-method schema |
| `-32603` | Internal error | Handler raised unexpectedly |
| `-32000…-32099` | Server error | Reserved; e.g. `-32001` job not found |

## 6. Conformance checklist (Issue #2 acceptance)

- [ ] Request/response/notification shapes validate per §2 (both sides)
- [ ] All 14 methods reachable with typed schemas (§3)
- [ ] `progress` notifications stream during long jobs (§4)
- [ ] Errors use §5 codes; unknown-id failures answer with `id: null`
- [ ] `ping` round-trip < 10 ms (in-process serialize→parse budget)
- [ ] Invalid payloads (bad JSON, wrong version, unknown method, bad
      params) are rejected with the matching §5 code — covered by
  `anees-core` unit tests and `python-backend/tests/test_protocol.py`
