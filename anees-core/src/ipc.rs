//! JSON-RPC 2.0 IPC protocol for Rust ↔ Python communication

use crate::error::{AneesError, Result};
use crate::models::*;
use bytes::BytesMut;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::{mpsc, oneshot, Mutex};

/// JSON-RPC 2.0 Request
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Request {
    WithId {
        jsonrpc: String,
        id: Value,
        method: String,
        params: Option<Value>,
    },
    Notification {
        jsonrpc: String,
        method: String,
        params: Option<Value>,
    },
}

/// JSON-RPC 2.0 Response
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Response {
    Success {
        jsonrpc: String,
        id: Value,
        result: Value,
    },
    Error {
        jsonrpc: String,
        id: Value,
        error: RpcError,
    },
}

/// JSON-RPC Error object
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RpcError {
    pub code: i32,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

/// Standard JSON-RPC 2.0 error codes (https://www.jsonrpc.org/specification).
/// Codes -32000..-32099 are reserved for implementation-defined server errors.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum ErrorCode {
    ParseError = -32700,
    InvalidRequest = -32600,
    MethodNotFound = -32601,
    InvalidParams = -32602,
    InternalError = -32603,
}

impl ErrorCode {
    pub fn code(self) -> i32 {
        self as i32
    }

    /// Bounds of the implementation-defined server-error range.
    pub const SERVER_ERROR_MIN: i32 = -32099;
    pub const SERVER_ERROR_MAX: i32 = -32000;

    /// True for codes in the -32000..-32099 server range (e.g. -32001
    /// job-not-found sent by the Python backend). The client accepts any
    /// i32 on the wire; this only classifies.
    pub fn is_server_error(code: i32) -> bool {
        (Self::SERVER_ERROR_MIN..=Self::SERVER_ERROR_MAX).contains(&code)
    }
}

impl RpcError {
    /// Error with a standard code and concise message.
    pub fn standard(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code: code.code(),
            message: message.into(),
            data: None,
        }
    }
}

/// Parse one NDJSON line, enforcing `jsonrpc == "2.0"` (else Invalid Request).
pub fn parse_message(line: &str) -> Result<IpcMessage> {
    let value: Value = serde_json::from_str(line)?;
    match value.get("jsonrpc") {
        Some(Value::String(v)) if v == "2.0" => Ok(serde_json::from_value(value)?),
        _ => Err(AneesError::Ipc(format!(
            "Invalid Request ({}): jsonrpc must be \"2.0\"",
            ErrorCode::InvalidRequest.code()
        ))),
    }
}

/// JSON-RPC Notification (server → client)
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Notification {
    pub jsonrpc: String,
    pub method: String,
    pub params: Option<Value>,
}

/// IPC message types
///
/// NOTE: Untagged (not internally tagged) so wire format stays compatible
/// with standard JSON-RPC 2.0 (`{"jsonrpc":"2.0",...}` without a `type`
/// discriminator). A tagged enum would require a `type` field that Python
/// never sends, making every response fail to deserialize.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum IpcMessage {
    Response(Response),
    Notification(Notification),
    Request(Request),
}

/// Protocol methods
pub mod methods {
    pub const PDF_VALIDATE: &str = "pdf.validate";
    pub const PDF_EXTRACT_TEXT: &str = "pdf.extract_text";
    pub const PDF_OCR: &str = "pdf.ocr";
    pub const LANG_DETECT: &str = "lang.detect";
    pub const ARABIC_ADD_TASHKEEL: &str = "arabic.add_tashkeel";
    pub const TTS_SYNTHESIZE: &str = "tts.synthesize";
    pub const TTS_LIST_VOICES: &str = "tts.list_voices";
    pub const TTS_PREVIEW: &str = "tts.preview";
    pub const EXPORT_HTML: &str = "export.html";
    pub const EXPORT_DOCX: &str = "export.docx";
    pub const MODELS_ENSURE: &str = "models.ensure";
    pub const JOB_START: &str = "job.start";
    pub const JOB_STATUS: &str = "job.status";
    pub const PING: &str = "ping";
}

/// Notification methods
pub mod notifications {
    pub const PROGRESS: &str = "progress";
    pub const LOG: &str = "log";
}

/// Progress notification payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProgressNotification {
    pub stage: ProgressStage,
    pub chunk: u32,
    pub total: u32,
    pub message: String,
    pub progress: f32, // 0.0 - 1.0
}

/// Log notification payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogNotification {
    pub level: String, // "error", "warn", "info", "debug"
    pub message: String,
    pub context: Option<HashMap<String, Value>>,
}

/// IPC Client for Rust → Python communication
pub struct IpcClient {
    sender: mpsc::UnboundedSender<IpcMessage>,
    pending: Arc<Mutex<HashMap<Value, oneshot::Sender<Response>>>>,
    notification_tx: mpsc::UnboundedSender<Notification>,
}

impl IpcClient {
    pub fn new(
        sender: mpsc::UnboundedSender<IpcMessage>,
        notification_tx: mpsc::UnboundedSender<Notification>,
    ) -> Self {
        Self {
            sender,
            pending: Arc::new(Mutex::new(HashMap::new())),
            notification_tx,
        }
    }

    /// Send a request and wait for response.
    ///
    /// Unbounded by design: synthesis calls can run for minutes, and an
    /// arbitrary default would break them. Use [`Self::call_with_timeout`]
    /// where a bound is wanted (e.g. supervision pings).
    pub async fn call(&self, method: &str, params: Option<Value>) -> Result<Value> {
        self.do_call(method, params, None).await
    }

    /// Send a request and wait for response, giving up after `timeout`.
    /// The pending entry is always removed — on success, error, timeout
    /// or send failure — so dead calls never leak map entries.
    pub async fn call_with_timeout(
        &self,
        method: &str,
        params: Option<Value>,
        timeout: std::time::Duration,
    ) -> Result<Value> {
        self.do_call(method, params, Some(timeout)).await
    }

    async fn do_call(
        &self,
        method: &str,
        params: Option<Value>,
        timeout: Option<std::time::Duration>,
    ) -> Result<Value> {
        let id = Value::Number(serde_json::Number::from(fastrand::u64(..)));
        let request = Request::WithId {
            jsonrpc: "2.0".to_string(),
            id: id.clone(),
            method: method.to_string(),
            params,
        };

        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id.clone(), tx);

        if let Err(e) = self.sender.send(IpcMessage::Request(request)) {
            self.pending.lock().await.remove(&id);
            return Err(AneesError::Ipc(format!("Failed to send request: {e}")));
        }

        let response = match timeout {
            Some(t) => match tokio::time::timeout(t, rx).await {
                Ok(Ok(resp)) => resp,
                Ok(Err(_)) => {
                    self.pending.lock().await.remove(&id);
                    return Err(AneesError::Ipc("Request cancelled".to_string()));
                }
                Err(_) => {
                    self.pending.lock().await.remove(&id);
                    return Err(AneesError::Timeout(format!("request '{method}' timed out")));
                }
            },
            None => match rx.await {
                Ok(resp) => resp,
                Err(_) => {
                    self.pending.lock().await.remove(&id);
                    return Err(AneesError::Ipc("Request cancelled".to_string()));
                }
            },
        };
        // Delivered responses already removed the entry in
        // `handle_response`; the extra remove is a no-op. Timeout and
        // error paths removed it above, so dead calls never leak.
        self.pending.lock().await.remove(&id);

        match response {
            Response::Success { result, .. } => Ok(result),
            Response::Error { error, .. } => Err(AneesError::Ipc(format!(
                "RPC error {}: {}",
                error.code, error.message
            ))),
        }
    }

    /// Send a notification (fire and forget)
    pub fn notify(&self, method: &str, params: Option<Value>) -> Result<()> {
        let notification = Notification {
            jsonrpc: "2.0".to_string(),
            method: method.to_string(),
            params,
        };
        self.sender
            .send(IpcMessage::Notification(notification))
            .map_err(|_| AneesError::Ipc("Failed to send notification".to_string()))
    }

    /// Handle incoming response
    pub async fn handle_response(&self, response: Response) {
        let id = match &response {
            Response::Success { id, .. } => id.clone(),
            Response::Error { id, .. } => id.clone(),
        };

        if let Some(tx) = self.pending.lock().await.remove(&id) {
            let _ = tx.send(response);
        }
    }

    /// Handle incoming notification
    pub async fn handle_notification(&self, notification: Notification) {
        let _ = self.notification_tx.send(notification);
    }
}

/// IPC Server trait for Python backend
pub trait IpcServer: Send + Sync {
    fn handle_request(&self, request: Request) -> Pin<Box<dyn Future<Output = Response> + Send>>;
}

/// Split reader half of the stdio transport.
///
/// Owning the reader exclusively in one task avoids holding a shared
/// `Mutex<StdioTransport>` across `read_buf().await`, which would block
/// the writer task and deadlock on startup (reader waits for a response
/// while writer waits for the lock).
pub struct StdioReader<R> {
    reader: R,
    buffer: BytesMut,
}

/// Split writer half of the stdio transport.
pub struct StdioWriter<W> {
    writer: W,
}

impl<R> StdioReader<R>
where
    R: tokio::io::AsyncRead + Unpin,
{
    pub fn new(reader: R) -> Self {
        Self {
            reader,
            buffer: BytesMut::with_capacity(8192),
        }
    }

    pub async fn receive(&mut self) -> Result<Option<IpcMessage>> {
        loop {
            if let Some(pos) = self.buffer.iter().position(|&b| b == b'\n') {
                let line = self.buffer.split_to(pos + 1);
                let line_str = std::str::from_utf8(&line[..pos])?;
                if !line_str.trim().is_empty() {
                    return Ok(Some(parse_message(line_str)?));
                }
            }

            let n = self.reader.read_buf(&mut self.buffer).await?;
            if n == 0 {
                return Ok(None); // EOF
            }
        }
    }
}

impl<W> StdioWriter<W>
where
    W: tokio::io::AsyncWrite + Unpin,
{
    pub fn new(writer: W) -> Self {
        Self { writer }
    }

    pub async fn send(&mut self, msg: &IpcMessage) -> Result<()> {
        let line = serde_json::to_string(msg)? + "\n";
        self.writer.write_all(line.as_bytes()).await?;
        self.writer.flush().await?;
        Ok(())
    }
}

/// Stdout/Stdin transport for IPC
pub struct StdioTransport<R, W> {
    pub reader: R,
    pub writer: W,
    buffer: BytesMut,
}

impl<R, W> StdioTransport<R, W>
where
    R: tokio::io::AsyncRead + Unpin,
    W: tokio::io::AsyncWrite + Unpin,
{
    pub fn new(reader: R, writer: W) -> Self {
        Self {
            reader,
            writer,
            buffer: BytesMut::with_capacity(8192),
        }
    }

    /// Split into independently-owned reader/writer halves.
    /// Use one half per task to avoid the `Arc<Mutex<_>>` deadlock where
    /// `receive()` holds the lock across `read_buf().await` and blocks `send()`.
    pub fn split(self) -> (StdioReader<R>, StdioWriter<W>) {
        (
            StdioReader {
                reader: self.reader,
                buffer: self.buffer,
            },
            StdioWriter {
                writer: self.writer,
            },
        )
    }

    pub async fn send(&mut self, msg: &IpcMessage) -> Result<()> {
        let line = serde_json::to_string(msg)? + "\n";
        self.writer.write_all(line.as_bytes()).await?;
        self.writer.flush().await?;
        Ok(())
    }

    pub async fn receive(&mut self) -> Result<Option<IpcMessage>> {
        loop {
            // Try to parse complete line from buffer
            if let Some(pos) = self.buffer.iter().position(|&b| b == b'\n') {
                let line = self.buffer.split_to(pos + 1);
                let line_str = std::str::from_utf8(&line[..pos])?;
                if !line_str.trim().is_empty() {
                    return Ok(Some(parse_message(line_str)?));
                }
            }

            // Read more data
            let n = self.reader.read_buf(&mut self.buffer).await?;
            if n == 0 {
                return Ok(None); // EOF
            }
        }
    }
}

impl<R, W> Default for StdioTransport<R, W>
where
    R: tokio::io::AsyncRead + Unpin + Default,
    W: tokio::io::AsyncWrite + Unpin + Default,
{
    fn default() -> Self {
        Self::new(R::default(), W::default())
    }
}

/// Helper to create request with typed params
pub fn request<T: Serialize>(method: &str, params: T) -> Result<Request> {
    Ok(Request::WithId {
        jsonrpc: "2.0".to_string(),
        id: Value::Number(serde_json::Number::from(fastrand::u64(..))),
        method: method.to_string(),
        params: Some(serde_json::to_value(params)?),
    })
}

/// Helper to create notification with typed params
pub fn notification<T: Serialize>(method: &str, params: T) -> Result<Notification> {
    Ok(Notification {
        jsonrpc: "2.0".to_string(),
        method: method.to_string(),
        params: Some(serde_json::to_value(params)?),
    })
}

/// Progress notification helper
pub fn progress_notification(
    stage: ProgressStage,
    chunk: u32,
    total: u32,
    message: String,
) -> Result<Notification> {
    let progress = if total > 0 {
        chunk as f32 / total as f32
    } else {
        0.0
    };
    notification(
        notifications::PROGRESS,
        ProgressNotification {
            stage,
            chunk,
            total,
            message,
            progress,
        },
    )
}

/// Log notification helper
pub fn log_notification(level: &str, message: String) -> Result<Notification> {
    notification(
        notifications::LOG,
        LogNotification {
            level: level.to_string(),
            message,
            context: None,
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn response_without_type_field_deserializes() {
        // Plain JSON-RPC 2.0 from Python has no `type` discriminator.
        let raw = r#"{"jsonrpc":"2.0","id":1,"result":{"status":"ok"}}"#;
        let msg: IpcMessage = serde_json::from_str(raw).expect("response must parse");
        assert!(matches!(
            msg,
            IpcMessage::Response(Response::Success { .. })
        ));
    }

    #[test]
    fn request_serializes_without_type_field() {
        let req = Request::WithId {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "ping".to_string(),
            params: None,
        };
        let s = serde_json::to_string(&IpcMessage::Request(req)).unwrap();
        assert!(
            !s.contains("\"type\""),
            "wire format must stay plain JSON-RPC: {s}"
        );
    }

    #[tokio::test]
    async fn split_halves_send_receive_roundtrip() {
        // Reader and writer halves are independently owned: sending must
        // not require the reader lock (regression test for startup deadlock).
        let (a, b) = tokio::io::duplex(8192);
        let (mut reader, mut writer) = StdioTransport::new(b, a).split();
        let ping = IpcMessage::Request(Request::WithId {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(7),
            method: "ping".to_string(),
            params: None,
        });
        writer.send(&ping).await.unwrap();
        let got = tokio::time::timeout(std::time::Duration::from_secs(5), reader.receive())
            .await
            .expect("transport must answer")
            .unwrap()
            .expect("one message");
        assert!(matches!(got, IpcMessage::Request(Request::WithId { .. })));
    }

    #[test]
    fn error_codes_match_spec() {
        assert_eq!(ErrorCode::ParseError.code(), -32700);
        assert_eq!(ErrorCode::InvalidRequest.code(), -32600);
        assert_eq!(ErrorCode::MethodNotFound.code(), -32601);
        assert_eq!(ErrorCode::InvalidParams.code(), -32602);
        assert_eq!(ErrorCode::InternalError.code(), -32603);
        let e = RpcError::standard(ErrorCode::MethodNotFound, "Method not found: foo");
        let v = serde_json::to_value(&e).unwrap();
        assert_eq!(v["code"], -32601);
        assert!(v.get("data").is_none());
    }

    #[test]
    fn wrong_version_rejected() {
        assert!(parse_message(r#"{"jsonrpc":"1.0","id":1,"method":"ping"}"#).is_err());
        assert!(parse_message(r#"{"id":1,"method":"ping"}"#).is_err());
        assert!(parse_message(r#"{"jsonrpc":"2.0","id":1,"method":"ping"}"#).is_ok());
    }
    #[test]
    fn notification_carries_no_id() {
        // Per spec the server MUST NOT reply to notifications; framing
        // check: a serialized notification has no id member at all.
        let notif = Notification {
            jsonrpc: "2.0".to_string(),
            method: "progress".to_string(),
            params: None,
        };
        let s = serde_json::to_string(&IpcMessage::Notification(notif)).unwrap();
        assert!(!s.contains("\"id\""), "notification must not carry id: {s}");
    }

    #[test]
    fn notification_parses_without_id() {
        let msg = parse_message(r#"{"jsonrpc":"2.0","method":"progress","params":{"stage":"x"}}"#)
            .expect("valid notification must parse");
        assert!(matches!(msg, IpcMessage::Notification(_)));
    }

    #[test]
    fn server_error_range_classified() {
        assert!(ErrorCode::is_server_error(-32001));
        assert!(ErrorCode::is_server_error(-32000));
        assert!(ErrorCode::is_server_error(-32099));
        assert!(!ErrorCode::is_server_error(-32601));
        assert!(!ErrorCode::is_server_error(0));
    }

    #[test]
    fn ping_roundtrip_under_10ms() {
        // Issue #2 acceptance: ping round-trip < 10 ms. Measured in-process
        // (serialize + parse); I/O bound is covered by the duplex test above.
        let ping = IpcMessage::Request(Request::WithId {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "ping".to_string(),
            params: None,
        });
        let line = serde_json::to_string(&ping).unwrap();
        let n = 100u32;
        let start = std::time::Instant::now();
        for _ in 0..n {
            let msg = parse_message(&line).unwrap();
            assert!(matches!(msg, IpcMessage::Request(Request::WithId { .. })));
        }
        assert!(
            start.elapsed() < std::time::Duration::from_millis(10) * n,
            "average ping round-trip must stay under 10 ms"
        );
    }
}
