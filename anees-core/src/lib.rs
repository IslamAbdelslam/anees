//! Shared types and IPC protocol for Anees

pub mod error;
pub mod ipc;
pub mod models;

pub use error::{AneesError, Result};
pub use ipc::{
    log_notification, notification, progress_notification, request, IpcClient, IpcMessage,
    IpcServer, LogNotification, Notification, ProgressNotification, Request, Response, StdioReader,
    StdioTransport, StdioWriter,
};
pub use models::*;
