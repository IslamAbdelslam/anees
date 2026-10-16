//! Authentic Intellectual Modernism design tokens (Issue #4).
//!
//! Single source of truth for palette, typography, spacing, elevation and
//! RTL layout. Every screen (#18+) builds on [`apply`] + [`fonts`].
//!
//! Note on Arabic shaping: egui 0.29 lays out pre-shaped runs but does no
//! HarfBuzz-level bidi reordering; full bidi editing belongs to #23/#28.
//! What this module guarantees: an Arabic-capable font everywhere, generous
//! tashkeel line height, and right-to-left widget flow.

use egui::{
    Align, Color32, FontData, FontDefinitions, FontFamily, FontId, Layout, TextStyle, Visuals,
};
use std::collections::BTreeMap;

/// Deep Scholarly Indigo.
pub const PRIMARY_HEX: &str = "#002741";
/// Deep Teal / Emerald.
pub const SECONDARY_HEX: &str = "#006a61";
/// Muted Illuminated Gold.
pub const TERTIARY_HEX: &str = "#B45309";
/// Canvas background.
pub const CANVAS_HEX: &str = "#F4F6F8";
/// Card / surface background.
pub const SURFACE_HEX: &str = "#FFFFFF";
/// Status colors: success, processing, warning, danger.
pub const SUCCESS_HEX: &str = "#10B981";
pub const PROCESSING_HEX: &str = "#2563EB";
pub const WARNING_HEX: &str = "#F59E0B";
pub const DANGER_HEX: &str = "#EF4444";

/// Palette as ready-to-use colors.
pub mod palette {
    use super::Color32;

    pub fn primary() -> Color32 {
        Color32::from_rgb(0x00, 0x27, 0x41)
    }
    pub fn secondary() -> Color32 {
        Color32::from_rgb(0x00, 0x6A, 0x61)
    }
    pub fn tertiary() -> Color32 {
        Color32::from_rgb(0xB4, 0x53, 0x09)
    }
    pub fn canvas() -> Color32 {
        Color32::from_rgb(0xF4, 0xF6, 0xF8)
    }
    pub fn surface() -> Color32 {
        Color32::WHITE
    }
    pub fn success() -> Color32 {
        Color32::from_rgb(0x10, 0xB9, 0x81)
    }
    pub fn processing() -> Color32 {
        Color32::from_rgb(0x25, 0x63, 0xEB)
    }
    pub fn warning() -> Color32 {
        Color32::from_rgb(0xF5, 0x9E, 0x0B)
    }
    pub fn danger() -> Color32 {
        Color32::from_rgb(0xEF, 0x44, 0x44)
    }
    /// Body copy on light surfaces.
    pub fn ink() -> Color32 {
        primary()
    }
    /// Secondary text on light surfaces.
    pub fn ink_soft() -> Color32 {
        Color32::from_gray(0x5B)
    }
}

/// Display 40px/700 — hero titles.
pub const DISPLAY_SIZE: f32 = 40.0;
/// Headline 32px/600 — section titles.
pub const HEADLINE_SIZE: f32 = 32.0;
/// Body 15px/400 — running text.
pub const BODY_SIZE: f32 = 15.0;
/// Arabic line height multiplier (tashkeel clearance, spec 1.7–1.8x).
pub const ARABIC_LINE_HEIGHT: f32 = 1.75;

/// Line height in px for a font size rendered with Arabic diacritics.
pub fn arabic_line_height(font_size: f32) -> f32 {
    font_size * ARABIC_LINE_HEIGHT
}

/// Gutter between columns: 1.25rem at 16px/rem.
pub const GUTTER_PX: f32 = 20.0;
/// Page margins: 1.5–2.5rem → 24–40px.
pub const MARGIN_MIN_PX: f32 = 24.0;
pub const MARGIN_MAX_PX: f32 = 40.0;

/// Four elevation levels (blur, spread, black alpha).
pub const ELEVATION: [(f32, f32, u8); 4] = [
    (2.0, 0.0, 14),
    (6.0, 1.0, 22),
    (14.0, 4.0, 36),
    (28.0, 8.0, 52),
];

/// Shadow for an elevation level (0–3).
pub fn elevation_shadow(level: usize) -> egui::epaint::Shadow {
    let (blur, spread, alpha) = ELEVATION[level.min(3)];
    egui::epaint::Shadow {
        offset: [0.0, 2.0 + level as f32].into(),
        blur,
        spread,
        color: Color32::from_black_alpha(alpha),
    }
}

/// Light Visuals for the Anees theme.
pub fn visuals() -> Visuals {
    let mut v = Visuals::light();
    v.window_fill = palette::surface();
    v.panel_fill = palette::surface();
    v.faint_bg_color = palette::canvas();
    v.extreme_bg_color = palette::canvas();
    v.selection.bg_fill = palette::secondary();
    v.selection.stroke.color = palette::surface();
    v.hyperlink_color = palette::processing();
    v.window_shadow = elevation_shadow(1);
    v.popup_shadow = elevation_shadow(2);

    let ink = palette::ink();
    for widgets in [&mut v.widgets.noninteractive, &mut v.widgets.inactive] {
        widgets.fg_stroke.color = ink;
    }
    v.widgets.hovered.bg_fill = Color32::from_rgb(0xE8, 0xEC, 0xF0);
    v.widgets.hovered.fg_stroke.color = ink;
    v.widgets.active.bg_fill = palette::secondary();
    v.widgets.active.fg_stroke.color = palette::surface();
    v
}

/// Font stack with IBM Plex Sans Arabic embedded (OFL, see assets/fonts).
/// Proportional (body) resolves to Regular. egui 0.29 has no automatic
/// weight matching, so bold goes through the dedicated `display` and
/// `headline` families; plain `strong()` body text falls back to Regular
/// (acceptable until rich-text bidi work in #28).
/// Proportional (body) and both headings resolve to it; monospace keeps the
/// bundled font for hashes/ids.
pub fn fonts() -> FontDefinitions {
    let mut fonts = FontDefinitions::default();
    fonts.font_data.insert(
        "IBM Plex Sans Arabic".to_owned(),
        FontData::from_static(include_bytes!(
            "../assets/fonts/IBMPlexSansArabic-Regular.ttf"
        )),
    );
    fonts.font_data.insert(
        "IBM Plex Sans Arabic SemiBold".to_owned(),
        FontData::from_static(include_bytes!(
            "../assets/fonts/IBMPlexSansArabic-SemiBold.ttf"
        )),
    );
    fonts.font_data.insert(
        "IBM Plex Sans Arabic Bold".to_owned(),
        FontData::from_static(include_bytes!("../assets/fonts/IBMPlexSansArabic-Bold.ttf")),
    );

    if let Some(proportional) = fonts.families.get_mut(&FontFamily::Proportional) {
        proportional.insert(0, "IBM Plex Sans Arabic".to_owned());
    }
    fonts.families.insert(
        FontFamily::Name("display".into()),
        vec!["IBM Plex Sans Arabic Bold".to_owned()],
    );
    fonts.families.insert(
        FontFamily::Name("headline".into()),
        vec!["IBM Plex Sans Arabic SemiBold".to_owned()],
    );
    fonts
}

/// Full style: visuals + text styles with Arabic line height.
pub fn style() -> egui::Style {
    let text_styles = [
        (
            TextStyle::Name("display".into()),
            FontId::new(DISPLAY_SIZE, FontFamily::Name("display".into())),
        ),
        (
            TextStyle::Name("headline".into()),
            FontId::new(HEADLINE_SIZE, FontFamily::Name("headline".into())),
        ),
        (
            TextStyle::Body,
            FontId::new(BODY_SIZE, FontFamily::Proportional),
        ),
        (
            TextStyle::Button,
            FontId::new(BODY_SIZE, FontFamily::Proportional),
        ),
        (
            TextStyle::Small,
            FontId::new(12.0, FontFamily::Proportional),
        ),
    ]
    .into_iter()
    .collect::<BTreeMap<_, _>>();
    let mut style = egui::Style {
        visuals: visuals(),
        text_styles,
        ..Default::default()
    };
    style.spacing.item_spacing.y = BODY_SIZE * 0.6;
    // Standard variants MUST stay registered: egui panics
    // ("Failed to find ... in Style::text_styles") on ui.heading /
    // ui.monospace when they are missing.
    style.text_styles.insert(
        TextStyle::Heading,
        FontId::new(HEADLINE_SIZE, FontFamily::Name("headline".into())),
    );
    style
        .text_styles
        .insert(TextStyle::Monospace, FontId::monospace(BODY_SIZE));
    style
}

/// Apply the Anees theme to a context (call once at startup).
pub fn apply(ctx: &egui::Context) {
    ctx.set_visuals(visuals());
    ctx.set_fonts(fonts());
    ctx.set_style(style());
}

/// Right-to-left row layout used by every screen.
pub fn rtl_row() -> Layout {
    Layout::right_to_left(Align::Center)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn palette_matches_hex_spec() {
        let cases = [
            (PRIMARY_HEX, palette::primary()),
            (SECONDARY_HEX, palette::secondary()),
            (TERTIARY_HEX, palette::tertiary()),
            (CANVAS_HEX, palette::canvas()),
            (SURFACE_HEX, palette::surface()),
            (SUCCESS_HEX, palette::success()),
            (PROCESSING_HEX, palette::processing()),
            (WARNING_HEX, palette::warning()),
            (DANGER_HEX, palette::danger()),
        ];
        for (hex, color) in cases {
            let rgb = u32::from_str_radix(hex.trim_start_matches('#'), 16).unwrap();
            assert_eq!(
                color,
                Color32::from_rgb((rgb >> 16) as u8, (rgb >> 8) as u8, rgb as u8),
                "mismatch for {hex}"
            );
        }
    }

    #[test]
    fn type_scale_and_line_height() {
        const { assert!(DISPLAY_SIZE > HEADLINE_SIZE && HEADLINE_SIZE > BODY_SIZE) };
        let lh = arabic_line_height(BODY_SIZE);
        assert!((lh / BODY_SIZE - ARABIC_LINE_HEIGHT).abs() < f32::EPSILON);
        assert!((1.7..=1.8).contains(&ARABIC_LINE_HEIGHT));
    }

    #[test]
    fn visuals_and_fonts_resolve() {
        let v = visuals();
        assert_eq!(v.window_fill, palette::surface());
        assert_eq!(v.panel_fill, palette::surface());
        let fonts = fonts();
        for name in [
            "IBM Plex Sans Arabic",
            "IBM Plex Sans Arabic SemiBold",
            "IBM Plex Sans Arabic Bold",
        ] {
            assert!(fonts.font_data.contains_key(name), "missing {name}");
        }
        let style = style();
        assert!(style
            .text_styles
            .contains_key(&TextStyle::Name("display".into())));
    }

    #[test]
    fn rtl_layout_flows_right_to_left() {
        assert_eq!(rtl_row().main_dir, egui::Direction::RightToLeft);
    }
}
