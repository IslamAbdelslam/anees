//! Backend dependency installation via `uv` or `pip` (Issue #5).

use std::path::Path;
use std::process::Command;

use anees_core::{AneesError, Result};

/// Which package manager installs the backend's dependencies.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Installer {
    Uv,
    Pip,
}

impl Installer {
    pub fn binary(self) -> &'static str {
        match self {
            Installer::Uv => "uv",
            Installer::Pip => "pip",
        }
    }
}

/// `uv` is preferred when available; fall back to `pip`.
pub fn choose(uv_available: bool) -> Installer {
    if uv_available {
        Installer::Uv
    } else {
        Installer::Pip
    }
}

/// Whether `uv` is on `PATH`.
pub fn uv_available() -> bool {
    Command::new("uv")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Editable-install command for the backend package (pulls its deps).
/// `uv` needs the target interpreter explicitly; `pip` runs under it.
pub fn install_command(installer: Installer, python: &Path, backend_dir: &Path) -> Command {
    match installer {
        Installer::Uv => {
            let mut cmd = Command::new("uv");
            cmd.arg("pip")
                .arg("install")
                .arg("--python")
                .arg(python)
                .arg("-e")
                .arg(backend_dir);
            cmd
        }
        Installer::Pip => {
            let mut cmd = Command::new(python);
            cmd.arg("-m")
                .arg("pip")
                .arg("install")
                .arg("-e")
                .arg(backend_dir);
            cmd
        }
    }
}

/// Run the install, streaming output to the parent's stdio.
pub fn install(installer: Installer, python: &Path, backend_dir: &Path) -> Result<()> {
    let status = install_command(installer, python, backend_dir)
        .status()
        .map_err(|e| {
            AneesError::PythonBackend(format!("{} failed to start: {e}", installer.binary()))
        })?;
    if status.success() {
        Ok(())
    } else {
        Err(AneesError::PythonBackend(format!(
            "dependency install exited with {status}"
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(cmd: &Command) -> Vec<String> {
        cmd.get_args()
            .map(|a| a.to_string_lossy().into_owned())
            .collect()
    }

    #[test]
    fn uv_preferred_when_available() {
        assert_eq!(choose(true), Installer::Uv);
        assert_eq!(choose(false), Installer::Pip);
        assert_eq!(Installer::Uv.binary(), "uv");
        assert_eq!(Installer::Pip.binary(), "pip");
    }

    #[test]
    fn uv_command_targets_editable_backend() {
        let cmd = install_command(
            Installer::Uv,
            Path::new("/usr/bin/python3"),
            Path::new("/repo/python-backend"),
        );
        assert_eq!(cmd.get_program(), "uv");
        let a = args(&cmd);
        assert_eq!(
            a,
            vec![
                "pip",
                "install",
                "--python",
                "/usr/bin/python3",
                "-e",
                "/repo/python-backend"
            ]
        );
    }

    #[test]
    fn pip_command_runs_under_target_interpreter() {
        let cmd = install_command(
            Installer::Pip,
            Path::new("/usr/bin/python3"),
            Path::new("/repo/python-backend"),
        );
        assert_eq!(cmd.get_program(), "/usr/bin/python3");
        assert_eq!(
            args(&cmd),
            vec!["-m", "pip", "install", "-e", "/repo/python-backend"]
        );
    }
}
