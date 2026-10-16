//! Anees CLI - Main entry point

use anees_core::{
    AneesError, BackendConfig, IpcClient, IpcMessage, LogNotification, ProgressNotification,
    Request, Result, StdioTransport,
};
use clap::{Parser, Subcommand};
use serde_json::{json, Value};
use std::sync::Arc;
use tracing_subscriber::{fmt, EnvFilter};
use uuid::Uuid;

#[derive(Parser, Debug)]
#[command(
    name = "anees-cli",
    version,
    about = "Anees - Arabic/English PDF-to-Speech CLI"
)]
struct Cli {
    /// Enable debug logging
    #[arg(short, long)]
    debug: bool,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Validate a PDF file
    Validate {
        /// Path to PDF file
        path: String,
    },

    /// Extract text from PDF
    Extract {
        /// Path to PDF file
        path: String,
        /// Page range (e.g., "1-10")
        #[arg(short, long)]
        pages: Option<String>,
        /// Languages (comma-separated)
        #[arg(short, long)]
        langs: Option<String>,
    },

    /// Run OCR on PDF
    Ocr {
        /// Path to PDF file
        path: String,
        /// Page range (e.g., "1-10")
        #[arg(short, long)]
        pages: Option<String>,
        /// Languages (comma-separated)
        #[arg(short, long)]
        langs: Option<String>,
    },

    /// Add Arabic tashkeel
    Tashkeel {
        /// Text to diacritize
        text: String,
        /// Mode: full, semantic, endings
        #[arg(short, long, default_value = "semantic")]
        mode: String,
    },

    /// Synthesize speech
    Tts {
        /// Text to synthesize
        text: String,
        /// Language: ar, en
        #[arg(short, long)]
        lang: String,
        /// Voice ID
        #[arg(short, long)]
        voice: String,
        /// Speed factor
        #[arg(short, long, default_value = "1.0")]
        speed: f32,
        /// Output file
        #[arg(short, long)]
        output: String,
    },

    /// Process PDF through full pipeline
    Process {
        /// Path to PDF file
        path: String,
        /// Language mode: auto, ar, en, mixed
        #[arg(short, long, default_value = "auto")]
        lang_mode: String,
        /// Voice for TTS
        #[arg(short, long)]
        voice: Option<String>,
        /// Output file
        #[arg(short, long)]
        output: String,
    },

    /// List available voices
    Voices,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    let filter = if cli.debug {
        EnvFilter::new("debug")
    } else {
        EnvFilter::new("info")
    };
    fmt().with_env_filter(filter).init();

    tracing::info!("Starting Anees CLI v{}", env!("CARGO_PKG_VERSION"));

    // Start Python backend process
    // Backend location honors ANEES_PYTHON / ANEES_BACKEND, defaulting
    // to the per-OS stock interpreter (python3 on Unix).
    let backend_cfg = BackendConfig::from_env();
    let python_cmd = backend_cfg.python_bin;
    let backend_script = backend_cfg.backend_script;

    let mut child = tokio::process::Command::new(&python_cmd)
        .arg(&backend_script)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(AneesError::Io)?;

    // Use child process pipes directly: ChildStdout implements AsyncRead, ChildStdin implements AsyncWrite
    let stdin = child.stdin.take().unwrap(); // implements AsyncWrite
    let stdout = child.stdout.take().unwrap(); // implements AsyncRead
    let stderr = child.stderr.take().unwrap();

    // Forward stderr to our stderr
    tokio::spawn(async move {
        use tokio::io::AsyncBufReadExt;
        let reader = tokio::io::BufReader::new(stderr);
        let mut lines = reader.lines();
        while let Ok(Some(line)) = lines.next_line().await {
            eprintln!("[Python] {}", line);
        }
    });

    // Create IPC client
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    let (notif_tx, mut notif_rx) = tokio::sync::mpsc::unbounded_channel();

    // IpcClient is internally synchronized: share it bare, never hold an
    // external lock across call()/handle_*() (that deadlocks: response
    // delivery needs the same lock the caller waits on).
    let client = Arc::new(IpcClient::new(tx, notif_tx));

    // Create transport with child process pipes, then split into
    // independently-owned halves so the reader task never holds a lock
    // across `read_buf().await` while the writer needs to send.
    let transport = StdioTransport::new(stdout, stdin);
    let (mut reader, writer) = transport.split();
    // Writer half is owned by its task: single consumer, no lock needed.
    tokio::spawn(async move {
        let mut writer = writer;
        while let Some(msg) = rx.recv().await {
            if let Err(e) = writer.send(&msg).await {
                tracing::error!("Failed to send IPC message: {}", e);
                break;
            }
        }
    });

    // Spawn transport reader (exclusive ownership, no shared lock)
    let client_clone = client.clone();

    tokio::spawn(async move {
        loop {
            match reader.receive().await {
                Ok(Some(msg)) => match msg {
                    IpcMessage::Response(resp) => {
                        client_clone.handle_response(resp).await;
                    }
                    IpcMessage::Notification(notif) => {
                        client_clone.handle_notification(notif).await;
                    }
                    IpcMessage::Request(Request::Notification { method, params, .. }) => {
                        client_clone
                            .handle_notification(anees_core::Notification {
                                jsonrpc: "2.0".to_string(),
                                method,
                                params,
                            })
                            .await;
                    }
                    _ => {}
                },
                // A malformed line must never kill delivery (see BackendManager).
                Ok(None) => break,
                Err(e) => {
                    tracing::warn!("Skipping malformed IPC line: {}", e);
                    continue;
                }
            }
        }
    });

    // Spawn notification handler
    tokio::spawn(async move {
        while let Some(notif) = notif_rx.recv().await {
            match notif.method.as_str() {
                "progress" => {
                    if let Ok(payload) = serde_json::from_value::<ProgressNotification>(
                        notif.params.unwrap_or_default(),
                    ) {
                        println!(
                            "[Progress] {}: {}% - {}",
                            payload.stage,
                            (payload.progress * 100.0) as u32,
                            payload.message
                        );
                    }
                }
                "log" => {
                    if let Ok(payload) =
                        serde_json::from_value::<LogNotification>(notif.params.unwrap_or_default())
                    {
                        println!("[{}] {}", payload.level.to_uppercase(), payload.message);
                    }
                }
                _ => {}
            }
        }
    });

    // Wait for backend to be ready
    tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;

    // Ping to verify connection
    {
        match client.call("ping", None).await {
            Ok(_) => tracing::info!("Connected to Python backend"),
            Err(e) => {
                tracing::error!("Failed to connect to Python backend: {}", e);
                child.kill().await.ok();
                child.wait().await.ok();
                return Err(e);
            }
        }
    }

    // Execute command
    let result = match cli.command {
        Commands::Validate { path } => {
            let result: Value = {
                client
                    .call("pdf.validate", Some(json!({"path": path})))
                    .await?
            };
            println!("{}", serde_json::to_string_pretty(&result)?);
            Ok(())
        }
        Commands::Extract { path, pages, langs } => {
            let mut params = json!({"path": path});
            if let Some(p) = pages {
                params["page_range"] = json!(p);
            }
            if let Some(l) = langs {
                params["langs"] = json!(l.split(',').collect::<Vec<_>>());
            }
            let result: Value = { client.call("pdf.extract_text", Some(params)).await? };
            println!("{}", serde_json::to_string_pretty(&result)?);
            Ok(())
        }
        Commands::Ocr { path, pages, langs } => {
            let mut params = json!({"path": path});
            if let Some(p) = pages {
                params["page_range"] = json!(p);
            }
            if let Some(l) = langs {
                params["langs"] = json!(l.split(',').collect::<Vec<_>>());
            }
            let result: Value = { client.call("pdf.ocr", Some(params)).await? };
            println!("{}", serde_json::to_string_pretty(&result)?);
            Ok(())
        }
        Commands::Tashkeel { text, mode } => {
            let result: Value = {
                client
                    .call(
                        "arabic.add_tashkeel",
                        Some(json!({"text": text, "mode": mode})),
                    )
                    .await?
            };
            println!("{}", serde_json::to_string_pretty(&result)?);
            Ok(())
        }
        Commands::Tts {
            text,
            lang,
            voice,
            speed,
            output: _output,
        } => {
            let result: Value = {
                client
                    .call(
                        "tts.synthesize",
                        Some(json!({
                            "text": text, "lang": lang, "voice": voice, "speed": speed
                        })),
                    )
                    .await?
            };
            // TODO: Save audio to file
            println!("{}", serde_json::to_string_pretty(&result)?);
            Ok(())
        }
        Commands::Process {
            path,
            lang_mode,
            voice,
            output,
        } => {
            let job_id = Uuid::new_v4();
            let result: Value = {
                client
                    .call(
                        "job.start",
                        Some(json!({
                            "job_id": job_id.to_string(),
                            "pdf_path": path,
                            "language_mode": lang_mode,
                            "voice": voice,
                            "output": output,
                        })),
                    )
                    .await?
            };
            println!("Job started: {}", job_id);
            println!("{}", serde_json::to_string_pretty(&result)?);
            Ok(())
        }
        Commands::Voices => {
            let result: Value = { client.call("tts.list_voices", None).await? };
            println!("{}", serde_json::to_string_pretty(&result)?);
            Ok(())
        }
    };

    // Shutdown (kill + wait: never leave a zombie).
    child.kill().await.ok();
    child.wait().await.ok();
    result
}
