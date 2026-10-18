//! Release packaging assets (Issue #6).
//!
//! The Linux `.desktop` entry and the Windows NSIS script are shipped
//! artifacts, not Rust code, but a release breaks silently if they lose a
//! required key (MIME type, icon, uninstaller, install root). These tests
//! pin those contracts so the files cannot regress unnoticed.
//!
//! Issue #9 additionally bundles a static Tesseract 5.x build and the
//! `ara`/`eng` traineddata with the app; the release workflow and the OCR
//! fetch script are pinned here too.

#[cfg(test)]
mod tests {
    const DESKTOP: &str = include_str!("../../installer/linux/anees.desktop");
    const NSIS: &str = include_str!("../../installer/windows/installer.nsi");
    const FETCH_OCR: &str = include_str!("../../scripts/fetch-tesseract.sh");
    const RELEASE_WORKFLOW: &str = include_str!("../../.github/workflows/release.yml");
    const PYPROJECT: &str = include_str!("../../python-backend/pyproject.toml");

    #[test]
    fn desktop_entry_is_a_pdf_handler() {
        assert!(DESKTOP.contains("[Desktop Entry]"));
        assert!(DESKTOP.contains("Type=Application"));
        assert!(DESKTOP.contains("Name=Anees"));
        assert!(DESKTOP.contains("Exec=anees-gui --file %f"));
        assert!(DESKTOP.contains("Icon=anees-gui"));
        assert!(DESKTOP.contains("MimeType=application/pdf;"));
    }

    #[test]
    fn appimage_icon_exists_and_is_a_png() {
        let icon = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../installer/linux/anees-gui.png"
        );
        let bytes = std::fs::read(icon).expect("packaging icon must exist");
        assert!(bytes.starts_with(&[0x89, b'P', b'N', b'G']));
    }

    #[test]
    fn nsis_installs_per_machine_with_uninstaller_and_shortcuts() {
        assert!(NSIS.contains("!define APP_NAME \"Anees\""));
        assert!(NSIS.contains("InstallDir \"$PROGRAMFILES64\\${APP_NAME}\""));
        assert!(NSIS.contains("WriteUninstaller"));
        assert!(NSIS.contains("$SMPROGRAMS"));
        assert!(NSIS.contains("Software\\Classes\\Anees.PDF"));
    }

    #[test]
    fn release_bundles_static_tesseract_and_arabic_english_traineddata() {
        assert!(FETCH_OCR.contains("tessdata_fast"));
        assert!(FETCH_OCR.contains("TESSERACT_VERSION="));
        assert!(FETCH_OCR.contains("--disable-shared"));
        assert!(FETCH_OCR.contains("eng.traineddata"));
        assert!(FETCH_OCR.contains("ara.traineddata"));
        assert!(RELEASE_WORKFLOW.contains("scripts/fetch-tesseract.sh"));
    }

    #[test]
    fn ocr_assets_ship_inside_the_python_package() {
        assert!(PYPROJECT.contains("[tool.setuptools.package-data]"));
        assert!(PYPROJECT.contains("\"anees.pdf\" = [\"bin/*\", \"tessdata/*\"]"));
    }
}
