//! Shared data models for Anees

use serde::{Deserialize, Serialize};
use chrono::{DateTime, Utc};
use uuid::Uuid;

/// PDF document information
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PdfInfo {
    pub pages: u32,
    pub has_text: bool,
    pub file_size: u64,
    pub encrypted: bool,
    pub detected_langs: Vec<String>,
}

/// Text segment with language info
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TextSegment {
    pub page: u32,
    pub text: String,
    pub lang: String,           // "ar", "en", "mixed"
    pub confidence: f32,
    pub bbox: Option<[f32; 4]>, // x, y, w, h
}

/// Chunk of pages (10 pages each)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChunkData {
    pub chunk_index: u32,
    pub page_range: (u32, u32), // inclusive start, exclusive end
    pub segments: Vec<TextSegment>,
    pub status: ChunkStatus,
    pub progress: f32, // 0.0 - 1.0
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum ChunkStatus {
    Pending,
    Processing,
    Completed,
    Flagged,
    Failed,
}

/// Voice information
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VoiceInfo {
    pub id: String,
    pub name: String,
    pub language: String,
    pub gender: String, // "male", "female"
    pub style: String,  // "academic", "narrative", "conversational"
    pub recommended: bool,
}

/// TTS synthesis request
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TtsRequest {
    pub text: String,
    pub lang: String,
    pub voice: String,
    pub speed: f32,
}

/// Audio chunk result
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioChunk {
    pub data: Vec<f32>,      // 24kHz samples
    pub sample_rate: u32,
    pub duration: f32,       // seconds
}

/// Job status
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobStatus {
    pub job_id: Uuid,
    pub status: JobState,
    pub progress: f32,
    pub current_chunk: u32,
    pub total_chunks: u32,
    pub stage: ProgressStage,
    pub message: String,
    pub output_path: Option<String>,
    pub error: Option<String>,
    pub started_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum JobState {
    Queued,
    Processing,
    Completed,
    Failed,
    Cancelled,
}

/// Processing stage for progress tracking
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum ProgressStage {
    Validation,
    Extraction,
    Ocr,
    LanguageDetection,
    Tashkeel,
    Tts,
    Merging,
    Exporting,
}

impl std::fmt::Display for ProgressStage {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ProgressStage::Validation => write!(f, "Validation"),
            ProgressStage::Extraction => write!(f, "Extraction"),
            ProgressStage::Ocr => write!(f, "OCR"),
            ProgressStage::LanguageDetection => write!(f, "Language Detection"),
            ProgressStage::Tashkeel => write!(f, "Tashkeel"),
            ProgressStage::Tts => write!(f, "TTS"),
            ProgressStage::Merging => write!(f, "Merging"),
            ProgressStage::Exporting => write!(f, "Exporting"),
        }
    }
}

/// Language detection result
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LanguageDetection {
    pub lang: String,
    pub confidence: f32,
}

/// Tashkeel modes
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum TashkeelMode {
    Full,
    Semantic,
    Endings,
}

/// Export options
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExportOptions {
    pub format: ExportFormat,
    pub include_tashkeel: bool,
    pub rtl: bool,
    pub chapter_navigation: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum ExportFormat {
    Html,
    Docx,
    Pdf,
}

/// User settings (persisted)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserSettings {
    pub default_voice_ar: String,
    pub default_voice_en: String,
    pub default_speed: f32,
    pub default_tashkeel_mode: TashkeelMode,
    pub output_directory: String,
    pub auto_save_interval_sec: u32,
    pub max_concurrent_batches: u32,
    pub theme: String, // "light", "dark", "system"
}

impl Default for UserSettings {
    fn default() -> Self {
        Self {
            default_voice_ar: "af_msa".to_string(),
            default_voice_en: "af_bella".to_string(),
            default_speed: 1.0,
            default_tashkeel_mode: TashkeelMode::Semantic,
            output_directory: dirs::audio_dir()
                .unwrap_or_else(|| dirs::home_dir().unwrap_or_default())
                .to_string_lossy()
                .to_string(),
            auto_save_interval_sec: 30,
            max_concurrent_batches: num_cpus::get() as u32,
            theme: "system".to_string(),
        }
    }
}

/// Project metadata (persisted)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectMetadata {
    pub id: Uuid,
    pub name: String,
    pub pdf_path: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub status: JobState,
    pub total_pages: u32,
    pub total_chunks: u32,
    pub language_mode: LanguageMode,
    pub force_ocr: bool,
    pub settings_hash: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum LanguageMode {
    Auto,
    Arabic,
    English,
    Mixed,
    Multi,
}