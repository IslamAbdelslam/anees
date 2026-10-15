//! Anees GUI - Main entry point

use anees_core::Result;
use clap::Parser;
use tracing_subscriber::{fmt, EnvFilter};

#[derive(Parser, Debug)]
#[command(name = "anees-gui", version, about = "Anees - Arabic/English PDF-to-Speech Desktop App")]
struct Args {
    /// Enable debug logging
    #[arg(short, long)]
    debug: bool,

    /// Open specific PDF file on startup
    #[arg(short, long)]
    file: Option<String>,
}

fn main() -> Result<()> {
    let args = Args::parse();

    // Initialize logging
    let filter = if args.debug {
        EnvFilter::new("debug")
    } else {
        EnvFilter::new("info")
    };
    fmt().with_env_filter(filter).init();

    tracing::info!("Starting Anees GUI v{}", env!("CARGO_PKG_VERSION"));

    if let Some(file) = args.file {
        tracing::info!("Opening file: {}", file);
    }

    // TODO: Initialize egui application
    // let options = eframe::NativeOptions::default();
    // eframe::run_native(
    //     "Anees",
    //     options,
    //     Box::new(|cc| Box::new(anees_gui::AneesApp::new(cc))),
    // )?;

    tracing::warn!("GUI not yet implemented");
    Ok(())
}