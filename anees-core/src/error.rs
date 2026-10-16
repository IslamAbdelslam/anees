//! Error types for Anees

use thiserror::Error;

#[derive(Error, Debug)]
pub enum AneesError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("JSON serialization error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("UTF-8 error: {0}")]
    Utf8(#[from] std::str::Utf8Error),

    #[error("IPC error: {0}")]
    Ipc(String),

    #[error("PDF error: {0}")]
    Pdf(String),

    #[error("OCR error: {0}")]
    Ocr(String),

    #[error("TTS error: {0}")]
    Tts(String),

    #[error("Model error: {0}")]
    Model(String),

    #[error("Validation error: {0}")]
    Validation(String),

    #[error("Configuration error: {0}")]
    Config(String),

    #[error("Persistence error: {0}")]
    Persistence(String),

    #[error("Python backend error: {0}")]
    PythonBackend(String),

    #[error("Timeout: {0}")]
    Timeout(String),

    #[error("Cancelled")]
    Cancelled,

    #[error("Not found: {0}")]
    NotFound(String),

    #[error("Internal error: {0}")]
    Internal(String),
}

pub type Result<T> = std::result::Result<T, AneesError>;