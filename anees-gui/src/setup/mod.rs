//! First-run setup (Issue #5): Python check → dependency install → model
//! download, with the progress state a dialog renders.
//!
//! The heavy work lives in [`python`], [`install`] and [`models`] as plain
//! blocking functions; [`FirstRun`] is the state machine the GUI drives.
//! Screens that host it (and run the work on a worker thread) land in #18+.

pub mod install;
pub mod models;
pub mod python;

pub use install::Installer;
pub use models::{ModelProgress, ModelSpec, CATALOG};
pub use python::PythonInfo;

use std::path::Path;
use std::sync::{Arc, Mutex};

use egui::{ProgressBar, Ui};

use crate::components;
use crate::theme::palette;

/// Ordered first-run stages.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Step {
    #[default]
    PythonCheck,
    InstallDeps,
    DownloadModels,
    Ready,
}

impl Step {
    pub fn label(self) -> &'static str {
        match self {
            Step::PythonCheck => "Checking for Python 3.10+…",
            Step::InstallDeps => "Installing backend dependencies…",
            Step::DownloadModels => "Downloading speech models…",
            Step::Ready => "Setup complete",
        }
    }
}

/// Shared, cloneable download-progress sink. Cloning shares the same buffer,
/// so a #18 worker thread can record updates while the UI reads snapshots
/// each frame.
#[derive(Debug, Clone, Default)]
pub struct ProgressStore(Arc<Mutex<Vec<ModelProgress>>>);

impl ProgressStore {
    pub fn snapshot(&self) -> Vec<ModelProgress> {
        self.0.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    /// Insert or update the entry for a `(model, file)` pair.
    pub fn record(&self, progress: ModelProgress) {
        let mut entries = self.0.lock().unwrap_or_else(|e| e.into_inner());
        match entries
            .iter_mut()
            .find(|e| e.model == progress.model && e.file == progress.file)
        {
            Some(slot) => *slot = progress,
            None => entries.push(progress),
        }
    }
}

/// First-run state: detected interpreter, chosen installer, shared download
/// progress, and the last error (if any). Pure data so it is testable without
/// a display; `show` only renders it.
#[derive(Debug, Clone, Default)]
pub struct FirstRun {
    pub step: Step,
    pub python: Option<PythonInfo>,
    pub installer: Option<Installer>,
    pub progress: ProgressStore,
    pub error: Option<String>,
}

impl FirstRun {
    pub fn new() -> Self {
        Self::default()
    }

    /// Step 1: locate a supported interpreter (advances to `InstallDeps`).
    pub fn check_python(&mut self, exe_dir: &Path) {
        match python::detect(exe_dir) {
            Ok(info) => {
                self.python = Some(info);
                self.step = Step::InstallDeps;
                self.error = None;
            }
            Err(e) => self.error = Some(e.to_string()),
        }
    }

    /// Step 2: install backend deps (advances to `DownloadModels`).
    pub fn install_deps(&mut self, backend_dir: &Path) {
        let Some(python) = self.python.as_ref() else {
            self.error = Some("Python has not been detected yet".to_string());
            return;
        };
        let installer = install::choose(install::uv_available());
        self.installer = Some(installer);
        match install::install(installer, &python.path, backend_dir) {
            Ok(()) => {
                self.step = Step::DownloadModels;
                self.error = None;
            }
            Err(e) => self.error = Some(e.to_string()),
        }
    }

    /// Step 3: download the given models (advances to `Ready`). Progress
    /// streams into [`Self::progress`] as it arrives.
    pub fn download_models(&mut self, dir: &Path, specs: &[ModelSpec]) {
        self.progress = ProgressStore::default();
        let store = self.progress.clone();
        let result = models::download_models(specs, dir, |p| store.record(p));
        match result {
            Ok(_) => {
                self.step = Step::Ready;
                self.error = None;
            }
            Err(e) => self.error = Some(e.to_string()),
        }
    }

    /// Render the step, detected interpreter, per-file progress and error.
    pub fn show(&self, ui: &mut Ui) {
        components::card(ui, "First-run setup", |ui| {
            ui.label(self.step.label());
            if let Some(py) = &self.python {
                ui.label(format!(
                    "Python {}.{} · {}",
                    py.version.0,
                    py.version.1,
                    py.path.display()
                ));
            }
            if let Some(installer) = self.installer {
                ui.label(format!("Installer: {}", installer.binary()));
            }
            for p in self.progress.snapshot() {
                ui.add(ProgressBar::new(p.fraction()).text(format!("{} · {}", p.model, p.file)));
            }
            if let Some(err) = &self.error {
                ui.colored_label(palette::danger(), err);
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_run_starts_at_python_check() {
        let fr = FirstRun::new();
        assert_eq!(fr.step, Step::PythonCheck);
        assert!(fr.python.is_none());
        assert!(fr.installer.is_none());
        assert!(fr.progress.snapshot().is_empty());
        assert!(fr.error.is_none());
    }

    #[test]
    fn every_step_has_a_label() {
        for step in [
            Step::PythonCheck,
            Step::InstallDeps,
            Step::DownloadModels,
            Step::Ready,
        ] {
            assert!(!step.label().is_empty());
        }
    }

    #[test]
    fn install_without_python_reports_error() {
        let mut fr = FirstRun::new();
        fr.install_deps(Path::new("/repo/python-backend"));
        assert!(fr.error.is_some());
        assert_eq!(fr.step, Step::PythonCheck);
    }

    #[test]
    fn progress_store_upserts_by_file() {
        let store = ProgressStore::default();
        let mut p = ModelProgress {
            model: "m".to_string(),
            file: "f".to_string(),
            bytes_done: 10,
            bytes_total: Some(100),
        };
        store.record(p.clone());
        p.bytes_done = 50;
        store.record(p);
        store.record(ModelProgress {
            model: "m".to_string(),
            file: "g".to_string(),
            bytes_done: 1,
            bytes_total: None,
        });
        let snap = store.snapshot();
        assert_eq!(snap.len(), 2);
        assert_eq!(snap[0].bytes_done, 50);
    }

    #[test]
    fn clones_share_progress_store() {
        let fr = FirstRun::new();
        let worker = fr.clone();
        fr.progress.record(ModelProgress {
            model: "m".to_string(),
            file: "f".to_string(),
            bytes_done: 5,
            bytes_total: None,
        });
        assert_eq!(worker.progress.snapshot().len(), 1);
    }
}
