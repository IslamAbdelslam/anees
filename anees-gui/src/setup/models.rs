//! Hugging Face ONNX model downloader with resume (Issue #5).

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use anees_core::{AneesError, Result};

/// Hugging Face resolve base; `/resolve/main/` serves the latest revision.
pub const HF_BASE: &str = "https://huggingface.co";

/// One downloadable model repo. `files` are repo-relative paths, kept under
/// `dir/<key>/` exactly as `hf_hub_download(local_dir=...)` would.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ModelSpec {
    pub key: &'static str,
    pub repo: &'static str,
    pub files: &'static [&'static str],
    pub optional: bool,
}

/// Models offered on first run (issue #5). File lists mirror the real trees
/// (verified via `GET /api/models/<repo>/tree/main?recursive=true`):
/// both required repos ship `onnx/model.onnx`, `config.json`, and voice
/// embeddings under `voices/`; there is no `vocab.json`.
pub const CATALOG: &[ModelSpec] = &[
    ModelSpec {
        key: "nabra-7m-distill",
        repo: "oddadmix/Nabra-7M-Distill-ONNX",
        files: &["onnx/model.onnx", "config.json", "voices/af_msa.bin"],
        optional: false,
    },
    ModelSpec {
        key: "kokoro-7m-distill",
        repo: "oddadmix/Kokoro-7M-Distill-ONNX",
        files: &[
            "onnx/model.onnx",
            "config.json",
            "voices/af_heart.bin",
            "voices/af_msa.bin",
        ],
        optional: false,
    },
    // Gated/private at the time of writing; downloaded only when reachable.
    ModelSpec {
        key: "nabra-saudi-7m",
        repo: "oddadmix/Nabra-Saudi-7M-ONNX",
        files: &["onnx/model.onnx", "config.json", "voices/af_saudi.bin"],
        optional: true,
    },
];

/// Download URL for one file of a repo.
pub fn resolve_url(repo: &str, file: &str) -> String {
    format!("{HF_BASE}/{repo}/resolve/main/{file}")
}

/// Per-user model directory, honouring `$ANEES_MODELS_DIR`.
pub fn models_dir() -> PathBuf {
    models_dir_for(
        dirs::config_dir(),
        std::env::var_os("ANEES_MODELS_DIR").map(PathBuf::from),
    )
}

/// Platform config dir + `anees/models`, unless an override is set.
pub fn models_dir_for(config: Option<PathBuf>, override_dir: Option<PathBuf>) -> PathBuf {
    if let Some(dir) = override_dir.filter(|d| !d.as_os_str().is_empty()) {
        return dir;
    }
    config
        .unwrap_or_else(|| PathBuf::from("."))
        .join("anees")
        .join("models")
}

/// In-progress file alongside the final destination.
pub fn part_path(dest: &Path) -> PathBuf {
    let mut os = dest.as_os_str().to_os_string();
    os.push(".part");
    PathBuf::from(os)
}

/// How a download should (re)start given bytes already on disk.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Resume {
    Fresh,
    From(u64),
    Complete,
}

/// Decide resume behaviour from the existing `.part` length and the
/// server-reported total (unknown until a response arrives).
pub fn plan_resume(existing: u64, total: Option<u64>) -> Resume {
    match total {
        Some(t) if t > 0 && existing >= t => Resume::Complete,
        _ if existing > 0 => Resume::From(existing),
        _ => Resume::Fresh,
    }
}

/// Total size from a `Content-Range: bytes */TOTAL` (or `bytes a-b/TOTAL`)
/// header, used to validate a rejected Range response.
pub fn content_range_total(header: Option<&str>) -> Option<u64> {
    header?.rsplit('/').next()?.trim().parse().ok()
}

/// Progress for one file within one model.
#[derive(Debug, Clone, PartialEq)]
pub struct ModelProgress {
    pub model: String,
    pub file: String,
    pub bytes_done: u64,
    pub bytes_total: Option<u64>,
}

impl ModelProgress {
    /// 0.0–1.0, or 0.0 while the total is unknown.
    pub fn fraction(&self) -> f32 {
        match self.bytes_total {
            Some(t) if t > 0 => (self.bytes_done as f32 / t as f32).clamp(0.0, 1.0),
            _ => 0.0,
        }
    }
}

/// Download one file to `dest`, resuming from a sibling `.part` when present.
/// Reports `(bytes_done, bytes_total)` after each chunk. Blocking I/O — run on
/// a worker thread from the UI.
pub fn download_file(
    url: &str,
    dest: &Path,
    mut on_progress: impl FnMut(u64, Option<u64>),
) -> Result<()> {
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
    let part = part_path(dest);
    let existing = fs::metadata(&part).map(|m| m.len()).unwrap_or(0);
    let start = match plan_resume(existing, None) {
        Resume::From(offset) => offset,
        _ => 0,
    };

    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(15))
        .build();
    let mut request = agent.get(url);
    if start > 0 {
        request = request.set("Range", &format!("bytes={start}-"));
    }
    let response = match request.call() {
        Ok(resp) => resp,
        // 416 means the partial is already >= the remote size: finalize only
        // when the remote total proves it complete.
        Err(ureq::Error::Status(416, resp)) => {
            let total = content_range_total(resp.header("Content-Range"));
            return match plan_resume(existing, total) {
                Resume::Complete => Ok(fs::rename(&part, dest)?),
                _ => Err(AneesError::Model(format!(
                    "{url}: incomplete (have {existing}, remote {total:?})"
                ))),
            };
        }
        Err(e) => return Err(AneesError::Model(format!("{url}: {e}"))),
    };

    let mut offset = start;
    let mut file = if response.status() == 206 {
        OpenOptions::new().append(true).open(&part)?
    } else {
        // Server ignored Range (200) or this is a fresh request: start over.
        offset = 0;
        File::create(&part)?
    };
    let total = response
        .header("Content-Length")
        .and_then(|v| v.parse::<u64>().ok())
        .map(|len| len + offset);

    let mut reader = response.into_reader();
    let mut buffer = [0u8; 64 * 1024];
    let mut done = offset;
    on_progress(done, total);
    loop {
        let n = reader.read(&mut buffer).map_err(AneesError::Io)?;
        if n == 0 {
            break;
        }
        file.write_all(&buffer[..n])?;
        done += n as u64;
        on_progress(done, total);
    }
    file.flush()?;
    drop(file);

    if let Some(expected) = total {
        if done != expected {
            return Err(AneesError::Model(format!(
                "{url}: incomplete ({done} of {expected} bytes)"
            )));
        }
    }
    fs::rename(&part, dest)?;
    Ok(())
}

/// Download every file of every spec into `dir/<key>/`. Optional models are
/// skipped on failure; required-model failures abort. Returns `(key, path)`
/// for each completed model.
pub fn download_models(
    specs: &[ModelSpec],
    dir: &Path,
    mut on_progress: impl FnMut(ModelProgress),
) -> Result<Vec<(String, PathBuf)>> {
    let mut ready = Vec::new();
    for spec in specs {
        let model_dir = dir.join(spec.key);
        let mut failed = false;
        for file in spec.files {
            let dest = model_dir.join(file);
            let url = resolve_url(spec.repo, file);
            let report = |bytes_done, bytes_total| {
                on_progress(ModelProgress {
                    model: spec.key.to_string(),
                    file: (*file).to_string(),
                    bytes_done,
                    bytes_total,
                })
            };
            if let Err(e) = download_file(&url, &dest, report) {
                if spec.optional {
                    tracing::warn!("skipping optional model {}: {e}", spec.key);
                    failed = true;
                    break;
                }
                return Err(e);
            }
        }
        if !failed {
            ready.push((spec.key.to_string(), model_dir));
        }
    }
    Ok(ready)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_matches_real_repo_layout() {
        let keys: Vec<_> = CATALOG.iter().map(|m| m.key).collect();
        assert_eq!(
            keys,
            vec!["nabra-7m-distill", "kokoro-7m-distill", "nabra-saudi-7m"]
        );
        for spec in &CATALOG[..2] {
            assert!(
                spec.files.contains(&"onnx/model.onnx"),
                "{} needs onnx/model.onnx",
                spec.key
            );
            assert!(spec.files.contains(&"config.json"));
            assert!(spec.files.iter().any(|f| f.starts_with("voices/")));
            assert!(!spec.files.contains(&"vocab.json"));
        }
        assert!(CATALOG[2].optional);
    }

    #[test]
    fn resolve_url_targets_hf_main_revision() {
        assert_eq!(
            resolve_url("oddadmix/Nabra-7M-Distill-ONNX", "onnx/model.onnx"),
            "https://huggingface.co/oddadmix/Nabra-7M-Distill-ONNX/resolve/main/onnx/model.onnx"
        );
    }

    #[test]
    fn models_dir_prefers_override_then_config() {
        assert_eq!(
            models_dir_for(Some(PathBuf::from("/home/u/.config")), None),
            PathBuf::from("/home/u/.config/anees/models")
        );
        assert_eq!(
            models_dir_for(
                Some(PathBuf::from("/home/u/.config")),
                Some(PathBuf::from("/m"))
            ),
            PathBuf::from("/m")
        );
        assert!(models_dir_for(None, None).ends_with("anees/models"));
    }

    #[test]
    fn resume_plan_covers_fresh_partial_and_complete() {
        assert_eq!(plan_resume(0, Some(100)), Resume::Fresh);
        assert_eq!(plan_resume(40, Some(100)), Resume::From(40));
        assert_eq!(plan_resume(100, Some(100)), Resume::Complete);
        assert_eq!(plan_resume(40, None), Resume::From(40));
    }

    #[test]
    fn content_range_total_reads_last_segment() {
        assert_eq!(content_range_total(Some("bytes */2155")), Some(2155));
        assert_eq!(content_range_total(Some("bytes 0-99/1000")), Some(1000));
        assert_eq!(content_range_total(Some("garbage")), None);
        assert_eq!(content_range_total(None), None);
    }

    #[test]
    fn part_path_appends_suffix() {
        assert_eq!(
            part_path(Path::new("/a/model.onnx")),
            PathBuf::from("/a/model.onnx.part")
        );
    }

    #[test]
    fn progress_fraction_guards_unknown_total() {
        let p = ModelProgress {
            model: "m".to_string(),
            file: "f".to_string(),
            bytes_done: 50,
            bytes_total: Some(100),
        };
        assert!((p.fraction() - 0.5).abs() < f32::EPSILON);
        let unknown = ModelProgress {
            bytes_total: None,
            ..p
        };
        assert_eq!(unknown.fraction(), 0.0);
    }

    /// Opt-in network check: `ANEES_LIVE_HF_TEST=1 cargo test -p anees-gui live_`.
    /// Probes every required file with a 1-byte Range (no full download) and
    /// pulls one small file end to end, so a stale path fails loudly.
    #[test]
    fn live_catalog_paths_resolve_and_download() {
        if std::env::var("ANEES_LIVE_HF_TEST").as_deref() != Ok("1") {
            return;
        }
        let agent = ureq::AgentBuilder::new().build();
        for spec in &CATALOG[..2] {
            for file in spec.files {
                let url = resolve_url(spec.repo, file);
                let resp = agent
                    .get(&url)
                    .set("Range", "bytes=0-0")
                    .call()
                    .unwrap_or_else(|e| panic!("{url}: {e}"));
                assert!(
                    matches!(resp.status(), 200 | 206),
                    "{url} -> {}",
                    resp.status()
                );
            }
        }
        let dir = std::env::temp_dir().join("anees-live-hf-test");
        let _ = fs::remove_dir_all(&dir);
        let dest = dir.join("config.json");
        download_file(
            &resolve_url(CATALOG[0].repo, "config.json"),
            &dest,
            |_, _| {},
        )
        .expect("live config.json download");
        assert!(!fs::read(&dest).unwrap().is_empty());
        let _ = fs::remove_dir_all(&dir);
    }
}
