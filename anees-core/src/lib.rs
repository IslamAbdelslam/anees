//! Shared types and IPC protocol for Anees

pub mod error;
pub mod ipc;
pub mod models;

pub use error::{AneesError, Result};
pub use ipc::{
    IpcClient, IpcServer, Request, Response, Notification, IpcMessage,
    ProgressNotification, LogNotification,
    request, notification, progress_notification, log_notification,
    StdioTransport, StdioReader, StdioWriter,
};
pub use models::*;
