//! Shared types and IPC protocol for Anees

pub mod error;
pub mod ipc;
pub mod models;
pub mod process;

pub use error::{AneesError, Result};
pub use ipc::{
    log_notification, notification, parse_message, progress_notification, request, ErrorCode,
    IpcClient, IpcMessage, IpcServer, LogNotification, Notification, ProgressNotification, Request,
    Response, RpcError, StdioReader, StdioTransport, StdioWriter,
};
pub use models::*;
pub use process::{BackendConfig, BackendManager, DEFAULT_PYTHON_BIN};
