//! Python backend process supervision (Issue #3).
//!
//! [`BackendManager`] spawns the backend once and owns its whole lifecycle:
//! piped stdio, split reader/writer halves (never a shared lock across a
//! blocking read — see `ipc`), stderr forwarding and server notifications
//! for the GUI log panel, ping health checks and bounded auto-restart.
//! [`BackendConfig::from_env`] honors `ANEES_PYTHON` / `ANEES_BACKEND` /
//! `ANEES_MODELS_DIR`.
//!
//! The manager lives in `anees-core` (not `anees-gui`) so both the GUI
//! (screens land in #18+) and `anees-cli` can share it. Always call
//! [`BackendManager::shutdown`] before dropping: the child is spawned with
//! `kill_on_drop`, which is a backstop, not a reaper.

use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use serde_json::Value;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use crate::{AneesError, IpcClient, IpcMessage, Notification, Request, Result, StdioTransport};

/// Stock interpreter name: `python3` on Unix (bare `python` rarely
/// exists there), `python` on Windows.
#[cfg(not(windows))]
pub const DEFAULT_PYTHON_BIN: &str = "python3";
/// Stock interpreter name: `python3` on Unix (bare `python` rarely
/// exists there), `python` on Windows.
#[cfg(windows)]
pub const DEFAULT_PYTHON_BIN: &str = "python";

/// How to find and (re)start the backend.
#[derive(Debug, Clone)]
pub struct BackendConfig {
    /// Python interpreter (default: `$ANEES_PYTHON`, else the per-OS
    /// stock binary — `python3` on Unix, `python` on Windows).
    pub python_bin: String,
    /// Backend entrypoint (default: `$ANEES_BACKEND` or
    /// `"python-backend/src/anees/server.py"`).
    pub backend_script: String,
    /// Extra argv after the script (default: none).
    pub script_args: Vec<String>,
    /// Passed to the child as `ANEES_MODELS_DIR` when set.
    pub models_dir: Option<PathBuf>,
    /// Max consecutive restarts before giving up (default 3).
    pub max_restarts: u32,
    /// Base backoff between restarts, multiplied by the attempt (default 1s).
    pub restart_backoff: Duration,
    /// Ping timeout for startup/health checks (default 10s).
    pub startup_timeout: Duration,
}

impl Default for BackendConfig {
    fn default() -> Self {
        Self {
            python_bin: DEFAULT_PYTHON_BIN.to_string(),
            backend_script: "python-backend/src/anees/server.py".to_string(),
            script_args: Vec::new(),
            models_dir: None,
            max_restarts: 3,
            restart_backoff: Duration::from_secs(1),
            startup_timeout: Duration::from_secs(10),
        }
    }
}

impl BackendConfig {
    /// Config with `$ANEES_PYTHON` / `$ANEES_BACKEND` / `$ANEES_MODELS_DIR`
    /// overrides applied.
    pub fn from_env() -> Self {
        let mut cfg = Self::default();
        if let Ok(bin) = std::env::var("ANEES_PYTHON") {
            if !bin.trim().is_empty() {
                cfg.python_bin = bin;
            }
        }
        if let Ok(script) = std::env::var("ANEES_BACKEND") {
            if !script.trim().is_empty() {
                cfg.backend_script = script;
            }
        }
        if let Ok(dir) = std::env::var("ANEES_MODELS_DIR") {
            if !dir.trim().is_empty() {
                cfg.models_dir = Some(PathBuf::from(dir));
            }
        }
        cfg
    }
}

/// Live handles for one backend child process.
///
/// NOTE: the client is shared as a bare `Arc<IpcClient>` on purpose.
/// `IpcClient` is internally synchronized; wrapping it in a `Mutex` and
/// holding the lock across `call()` deadlocks startup: response delivery
/// in the reader task needs the same lock the caller is holding while
/// waiting for the response.
pub struct BackendManager {
    config: BackendConfig,
    child: Option<Child>,
    client: Option<Arc<IpcClient>>,
    io_tasks: Vec<JoinHandle<()>>,
    restarts: u32,
    stderr_tx: mpsc::UnboundedSender<String>,
    notif_tx: mpsc::UnboundedSender<Notification>,
}

impl BackendManager {
    /// Create a stopped manager. Also returns receivers for stderr lines
    /// and server→client notifications (the GUI log-panel feed).
    pub fn new(
        config: BackendConfig,
    ) -> (
        Self,
        mpsc::UnboundedReceiver<String>,
        mpsc::UnboundedReceiver<Notification>,
    ) {
        let (stderr_tx, stderr_rx) = mpsc::unbounded_channel();
        let (notif_tx, notif_rx) = mpsc::unbounded_channel();
        (
            Self {
                config,
                child: None,
                client: None,
                io_tasks: Vec::new(),
                restarts: 0,
                stderr_tx,
                notif_tx,
            },
            stderr_rx,
            notif_rx,
        )
    }

    /// Spawn the backend and wire stdio. Errors if already running.
    pub async fn start(&mut self) -> Result<()> {
        if self.child.is_some() {
            return Err(AneesError::PythonBackend(
                "backend already running".to_string(),
            ));
        }

        let mut cmd = Command::new(&self.config.python_bin);
        cmd.arg(&self.config.backend_script)
            .args(&self.config.script_args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        if let Some(dir) = &self.config.models_dir {
            cmd.env("ANEES_MODELS_DIR", dir);
        }
        let mut child = cmd.spawn().map_err(AneesError::Io)?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| AneesError::PythonBackend("child stdin not captured".to_string()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| AneesError::PythonBackend("child stdout not captured".to_string()))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| AneesError::PythonBackend("child stderr not captured".to_string()))?;

        // stderr → channel (GUI log panel consumes it).
        let stderr_tx = self.stderr_tx.clone();
        self.io_tasks.push(tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if stderr_tx.send(line).is_err() {
                    break;
                }
            }
        }));

        // JSON-RPC client over split transport halves.
        let (tx, mut rx) = mpsc::unbounded_channel::<IpcMessage>();
        let (ntx, mut nrx) = mpsc::unbounded_channel::<Notification>();
        let client = Arc::new(IpcClient::new(tx, ntx));
        let (reader, writer) = StdioTransport::new(stdout, stdin).split();

        self.io_tasks.push(tokio::spawn(async move {
            let mut writer = writer;
            while let Some(msg) = rx.recv().await {
                if writer.send(&msg).await.is_err() {
                    break;
                }
            }
        }));
        let reader_client = client.clone();
        self.io_tasks.push(tokio::spawn(async move {
            let mut reader = reader;
            loop {
                match reader.receive().await {
                    Ok(Some(msg)) => match msg {
                        IpcMessage::Response(resp) => {
                            reader_client.handle_response(resp).await;
                        }
                        IpcMessage::Notification(notif) => {
                            reader_client.handle_notification(notif).await;
                        }
                        IpcMessage::Request(Request::Notification { method, params, .. }) => {
                            reader_client
                                .handle_notification(Notification {
                                    jsonrpc: "2.0".to_string(),
                                    method,
                                    params,
                                })
                                .await;
                        }
                        _ => {}
                    },
                    // A malformed line (stray print, traceback fragment)
                    // must never kill delivery: log and keep reading.
                    // Only EOF ends the loop.
                    Ok(None) => break,
                    Err(e) => {
                        tracing::warn!("Skipping malformed IPC line: {e}");
                        continue;
                    }
                }
            }
        }));
        // Server notifications → manager channel (progress/log feed).
        let fwd_tx = self.notif_tx.clone();
        self.io_tasks.push(tokio::spawn(async move {
            while let Some(notif) = nrx.recv().await {
                if fwd_tx.send(notif).is_err() {
                    break;
                }
            }
        }));

        self.child = Some(child);
        self.client = Some(client);
        Ok(())
    }

    /// True while the child handle exists and has not exited.
    pub fn is_running(&mut self) -> bool {
        match self.child.as_mut() {
            None => false,
            Some(child) => matches!(child.try_wait(), Ok(None)),
        }
    }

    /// Send a JSON-RPC call. Errors if the backend is not running.
    /// Takes no lock: `IpcClient` synchronizes internally.
    pub async fn call(&self, method: &str, params: Option<Value>) -> Result<Value> {
        let client = self
            .client
            .as_ref()
            .ok_or_else(|| AneesError::PythonBackend("backend not running".to_string()))?;
        client.call(method, params).await
    }

    /// Ping health check bounded by `startup_timeout`. Uses
    /// [`IpcClient::call_with_timeout`] so a dead backend surfaces as an
    /// error instead of leaking a pending entry.
    pub async fn health_check(&self) -> Result<Value> {
        let client = self
            .client
            .as_ref()
            .ok_or_else(|| AneesError::PythonBackend("backend not running".to_string()))?;
        client
            .call_with_timeout("ping", None, self.config.startup_timeout)
            .await
    }

    /// Ensure a healthy backend: ping when running (alive is not healthy —
    /// a wedged backend still answers `try_wait`), otherwise restart with
    /// backoff up to `max_restarts`, verifying each restart with a ping.
    /// Resets the restart counter on success.
    ///
    /// Note: `is_running` needs `&mut` (`try_wait` reaps), so shared
    /// polling should wrap the manager in a `Mutex`.
    pub async fn ensure_running(&mut self) -> Result<()> {
        if self.is_running() {
            return match self.health_check().await {
                Ok(_) => {
                    self.restarts = 0;
                    Ok(())
                }
                Err(_) => {
                    self.shutdown().await;
                    self.restart_loop().await
                }
            };
        }
        self.restart_loop().await
    }

    async fn restart_loop(&mut self) -> Result<()> {
        let mut attempt = self.restarts;
        loop {
            if attempt >= self.config.max_restarts {
                return Err(AneesError::PythonBackend(format!(
                    "backend failed to stay up after {} restarts",
                    self.config.max_restarts
                )));
            }
            attempt += 1;
            self.restarts = attempt;
            self.shutdown().await;
            tokio::time::sleep(self.config.restart_backoff * attempt).await;
            if self.start().await.is_err() {
                continue;
            }
            // Skip the ping when the child already died (instant crash):
            // a slow-but-alive backend (model loading) still gets the
            // full health timeout below.
            tokio::time::sleep(Duration::from_millis(50)).await;
            if !self.is_running() {
                continue;
            }
            if self.health_check().await.is_ok() {
                self.restarts = 0;
                return Ok(());
            }
        }
    }

    /// Kill the child (if any), reap it, and stop all IO tasks.
    /// Call before dropping; `kill_on_drop` is only a backstop.
    pub async fn shutdown(&mut self) {
        for handle in self.io_tasks.drain(..) {
            handle.abort();
        }
        self.client = None;
        if let Some(mut child) = self.child.take() {
            child.kill().await.ok();
            // Reap: never leave a zombie (tokio kill() already waits;
            // this is the explicit second half per tokio docs).
            child.wait().await.ok();
        }
    }

    /// Consecutive failed restarts since the last healthy check.
    pub fn restart_count(&self) -> u32 {
        self.restarts
    }
}

impl Drop for BackendManager {
    /// Backstop: abort IO tasks synchronously. The child itself is killed
    /// by `kill_on_drop(true)` (without reaping — call [`Self::shutdown`]
    /// for a clean, reaped exit).
    fn drop(&mut self) {
        for handle in self.io_tasks.drain(..) {
            handle.abort();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_config(python_bin: &str, script: &str, args: &[&str]) -> BackendConfig {
        BackendConfig {
            python_bin: python_bin.to_string(),
            backend_script: script.to_string(),
            script_args: args.iter().map(|s| s.to_string()).collect(),
            models_dir: None,
            max_restarts: 2,
            restart_backoff: Duration::from_millis(0),
            startup_timeout: Duration::from_secs(2),
        }
    }

    /// Minimal JSON-RPC responder over stdio (no backend deps needed).
    /// Prints one garbage line first: the reader must skip it without
    /// dying, proving malformed-line resilience end to end.
    const RESPONDER: &str = "import sys, json\nprint('boot noise (not JSON)', flush=True)\nfor line in sys.stdin:\n    line = line.strip()\n    if not line:\n        continue\n    try:\n        msg = json.loads(line)\n    except Exception:\n        continue\n    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': msg.get('id'), 'result': {'status': 'ok'}}) + chr(10))\n    sys.stdout.flush()\n";

    /// Wait (bounded) until the child has exited.
    async fn wait_for_exit(mgr: &mut BackendManager) {
        for _ in 0..100 {
            if !mgr.is_running() {
                return;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("child did not exit in time");
    }

    #[test]
    fn default_python_matches_stock_os_interpreter() {
        // Bare `python` rarely exists on Unix (Ubuntu/Debian/macOS ship
        // only `python3`); Windows is the opposite.
        #[cfg(not(windows))]
        assert_eq!(BackendConfig::default().python_bin, "python3");
        #[cfg(windows)]
        assert_eq!(BackendConfig::default().python_bin, "python");
    }

    #[tokio::test]
    async fn spawn_failure_gives_up_after_max_restarts() {
        let (mut mgr, _stderr, _notif) =
            BackendManager::new(test_config("/nonexistent/python-xyz", "server.py", &[]));
        assert!(mgr.start().await.is_err());
        assert!(!mgr.is_running());
        let err = mgr.ensure_running().await.unwrap_err();
        assert!(err.to_string().contains("failed to stay up"));
        assert_eq!(mgr.restart_count(), 2);
    }

    #[tokio::test]
    async fn stderr_lines_are_forwarded() {
        let script = "import sys; print('log-line-1', file=sys.stderr); print('log-line-2', file=sys.stderr)";
        let (mut mgr, mut stderr_rx, _notif) =
            BackendManager::new(test_config("python3", "-c", &[script]));
        mgr.start().await.expect("python3 must exist on CI");
        let first = tokio::time::timeout(Duration::from_secs(10), stderr_rx.recv())
            .await
            .expect("stderr line in time")
            .expect("channel open");
        assert_eq!(first, "log-line-1");
        mgr.shutdown().await;
    }

    #[tokio::test]
    async fn shutdown_kills_child() {
        let script = "import time; time.sleep(60)";
        let (mut mgr, _stderr, _notif) =
            BackendManager::new(test_config("python3", "-c", &[script]));
        mgr.start().await.expect("python3 must exist on CI");
        assert!(mgr.is_running());
        mgr.shutdown().await;
        assert!(!mgr.is_running());
    }

    #[tokio::test]
    async fn ping_health_check_ok() {
        let (mut mgr, _stderr, _notif) =
            BackendManager::new(test_config("python3", "-c", &[RESPONDER]));
        mgr.start().await.expect("python3 must exist on CI");
        let result = mgr.health_check().await.expect("ping must succeed");
        assert_eq!(result["status"], "ok");
        mgr.shutdown().await;
    }

    #[tokio::test]
    async fn crash_loop_gives_up_after_max_restarts() {
        let script = "import sys; sys.exit(1)";
        let (mut mgr, _stderr, _notif) =
            BackendManager::new(test_config("python3", "-c", &[script]));
        mgr.start().await.expect("python3 must exist on CI");
        wait_for_exit(&mut mgr).await;
        let err = mgr.ensure_running().await.unwrap_err();
        assert!(err.to_string().contains("failed to stay up"));
        assert_eq!(mgr.restart_count(), 2);
        mgr.shutdown().await;
    }

    #[tokio::test]
    async fn unresponsive_backend_fails_health_and_restarts() {
        // Never answers: ping must time out (2 s budget, not hang), and
        // supervision must burn its restart budget and give up.
        let script = "import time; time.sleep(60)";
        let (mut mgr, _stderr, _notif) =
            BackendManager::new(test_config("python3", "-c", &[script]));
        mgr.start().await.expect("python3 must exist on CI");
        assert!(mgr.health_check().await.is_err());
        let err = mgr.ensure_running().await.unwrap_err();
        assert!(err.to_string().contains("failed to stay up"));
        assert_eq!(mgr.restart_count(), 2);
        mgr.shutdown().await;
    }
}
