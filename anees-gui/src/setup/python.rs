//! Python 3.10+ interpreter discovery (Issue #5).

use std::path::{Path, PathBuf};
use std::process::Command;

use anees_core::{AneesError, Result, DEFAULT_PYTHON_BIN};

/// Minimum interpreter version required by `python-backend` (pyproject).
pub const MIN_PYTHON: (u8, u8) = (3, 10);

/// Parse `(major, minor)` from a version string such as `"3.12.4"`,
/// `"Python 3.10.0rc1"`, or the bare `"3.10"` our probe prints.
pub fn parse_version(text: &str) -> Option<(u8, u8)> {
    let token = text
        .split_whitespace()
        .find(|t| t.starts_with(|c: char| c.is_ascii_digit()))?;
    let (major, rest) = token.split_once('.')?;
    let minor: String = rest.chars().take_while(char::is_ascii_digit).collect();
    Some((major.parse().ok()?, minor.parse().ok()?))
}

/// True when the version satisfies [`MIN_PYTHON`].
pub fn is_supported(version: (u8, u8)) -> bool {
    version >= MIN_PYTHON
}

/// A usable interpreter and the version it reported.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PythonInfo {
    pub path: PathBuf,
    pub version: (u8, u8),
}

/// Interpreters to try, in priority order: an embedded runtime shipped next
/// to the app, then the per-OS stock binary on `PATH`.
pub fn candidates(exe_dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    #[cfg(windows)]
    {
        out.push(exe_dir.join("python").join("python.exe"));
        out.push(exe_dir.join("python.exe"));
    }
    #[cfg(not(windows))]
    {
        out.push(exe_dir.join("python").join("bin").join("python3"));
    }
    out.push(PathBuf::from(DEFAULT_PYTHON_BIN));
    out.push(PathBuf::from("python"));
    out
}

fn probe(path: &Path) -> Result<(u8, u8)> {
    let output = Command::new(path)
        .args(["-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
        .output()
        .map_err(|e| AneesError::PythonBackend(format!("{}: {e}", path.display())))?;
    if !output.status.success() {
        return Err(AneesError::PythonBackend(format!(
            "{} probe exited with {}",
            path.display(),
            output.status
        )));
    }
    let text = String::from_utf8_lossy(&output.stdout);
    parse_version(text.trim())
        .ok_or_else(|| AneesError::PythonBackend(format!("unrecognized version: {text:?}")))
}

/// First candidate reporting a supported version. `$ANEES_PYTHON`, when set,
/// overrides discovery entirely (it must still be 3.10+).
pub fn detect(exe_dir: &Path) -> Result<PythonInfo> {
    if let Ok(bin) = std::env::var("ANEES_PYTHON") {
        if !bin.trim().is_empty() {
            let path = PathBuf::from(&bin);
            let version = probe(&path)?;
            if !is_supported(version) {
                return Err(AneesError::PythonBackend(format!(
                    "$ANEES_PYTHON ({}) is Python {}.{}, but {}.{} + is required",
                    path.display(),
                    version.0,
                    version.1,
                    MIN_PYTHON.0,
                    MIN_PYTHON.1
                )));
            }
            return Ok(PythonInfo { path, version });
        }
    }

    let mut outdated: Option<PythonInfo> = None;
    for path in candidates(exe_dir) {
        if let Ok(version) = probe(&path) {
            if is_supported(version) {
                return Ok(PythonInfo { path, version });
            }
            outdated.get_or_insert(PythonInfo { path, version });
        }
    }
    match outdated {
        Some(info) => Err(AneesError::PythonBackend(format!(
            "found Python {}.{} at {} but {}.{} + is required",
            info.version.0,
            info.version.1,
            info.path.display(),
            MIN_PYTHON.0,
            MIN_PYTHON.1
        ))),
        None => Err(AneesError::PythonBackend(format!(
            "no Python {}.{} + interpreter found",
            MIN_PYTHON.0, MIN_PYTHON.1
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_python_version_strings() {
        assert_eq!(parse_version("Python 3.12.4"), Some((3, 12)));
        assert_eq!(parse_version("3.10.0"), Some((3, 10)));
        assert_eq!(parse_version("Python 3.10.0rc1"), Some((3, 10)));
        assert_eq!(parse_version("3.9.18"), Some((3, 9)));
        assert_eq!(parse_version("no version here"), None);
    }

    #[test]
    fn minimum_version_is_3_10() {
        assert_eq!(MIN_PYTHON, (3, 10));
        assert!(is_supported((3, 10)));
        assert!(is_supported((4, 0)));
        assert!(!is_supported((3, 9)));
    }

    #[test]
    fn candidates_include_embedded_and_stock_python() {
        let dir = Path::new("/opt/anees");
        let cands = candidates(dir);
        #[cfg(windows)]
        assert!(cands.contains(&dir.join("python").join("python.exe")));
        #[cfg(not(windows))]
        assert!(cands.contains(&dir.join("python").join("bin").join("python3")));
        assert!(cands.iter().any(|p| p == Path::new(DEFAULT_PYTHON_BIN)));
    }
}
