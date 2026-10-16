//! Reusable Anees widgets (Issue #4).
//!
//! Thin wrappers over egui with the [`crate::theme`] tokens applied.
//! Stateful behavior lives in plain structs so it is unit-testable without
//! a display; `show()` methods only wire state to egui.

use crate::theme::{self, palette};
use egui::{Align, Button, Color32, Frame, Response, RichText, Ui};

/// Button kinds from the design system.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ButtonKind {
    Primary,
    Secondary,
    Audio,
}

impl ButtonKind {
    /// (fill, label) colors for the kind.
    pub fn colors(self) -> (Color32, Color32) {
        match self {
            ButtonKind::Primary => (palette::primary(), palette::surface()),
            ButtonKind::Secondary => (palette::surface(), palette::primary()),
            ButtonKind::Audio => (palette::secondary(), palette::surface()),
        }
    }
}

/// Themed button. Returns the egui response for click handling.
pub fn button(ui: &mut Ui, kind: ButtonKind, label: &str) -> Response {
    let (fill, fg) = kind.colors();
    let mut btn = Button::new(RichText::new(label).color(fg)).fill(fill);
    if kind == ButtonKind::Secondary {
        btn = btn.stroke((1.0, palette::primary()));
    }
    ui.add(btn)
}

/// Card container: surface fill, subtle tertiary-tinted stroke, elevation 1.
pub fn card<R>(
    ui: &mut Ui,
    title: &str,
    add_contents: impl FnOnce(&mut Ui) -> R,
) -> egui::InnerResponse<R> {
    Frame::group(ui.style())
        .fill(palette::surface())
        .stroke((1.0, palette::tertiary()))
        .shadow(theme::elevation_shadow(1))
        .show(ui, |ui| {
            ui.heading(RichText::new(title).color(palette::ink()));
            ui.separator();
            add_contents(ui)
        })
}

/// Single-line text input with hint text.
pub fn text_input(ui: &mut Ui, buf: &mut String, hint: &str) -> Response {
    egui::TextEdit::singleline(buf)
        .hint_text(RichText::new(hint).color(palette::ink_soft()))
        .desired_width(f32::INFINITY)
        .show(ui)
        .response
}

/// Selectable chip. The caller owns `selected`; returns the response so the
/// caller can flip state on click.
pub fn chip(ui: &mut Ui, label: &str, selected: bool) -> Response {
    let (fill, fg) = chip_colors(selected);
    ui.add(
        Button::new(RichText::new(label).color(fg))
            .fill(fill)
            .rounding(12.0),
    )
}

/// (fill, label) colors for a chip state — pure for testability.
pub fn chip_colors(selected: bool) -> (Color32, Color32) {
    if selected {
        (palette::secondary(), palette::surface())
    } else {
        (Color32::from_rgb(0xE8, 0xEC, 0xF0), palette::ink())
    }
}

/// mm:ss with whole-second rounding (never emits 00:60).
pub fn fmt_time(secs: f32) -> String {
    let total = secs.round().max(0.0) as u32;
    format!("{:02}:{:02}", total / 60, total % 60)
}

/// Audio player state machine. Rendering (`show`) is a thin view over this.
#[derive(Debug, Clone, PartialEq)]
pub enum PlayerState {
    Stopped,
    Playing,
    Paused,
}

#[derive(Debug, Clone)]
pub struct AudioPlayer {
    state: PlayerState,
    /// Seconds into the track.
    position_secs: f32,
    /// Total track length in seconds.
    pub duration_secs: f32,
}

impl AudioPlayer {
    pub fn new(duration_secs: f32) -> Self {
        Self {
            state: PlayerState::Stopped,
            position_secs: 0.0,
            duration_secs: duration_secs.max(0.0),
        }
    }

    pub fn state(&self) -> &PlayerState {
        &self.state
    }

    pub fn position(&self) -> f32 {
        self.position_secs
    }

    /// 0.0–1.0 playback progress.
    pub fn progress(&self) -> f32 {
        if self.duration_secs <= 0.0 {
            0.0
        } else {
            (self.position_secs / self.duration_secs).clamp(0.0, 1.0)
        }
    }

    pub fn play(&mut self) {
        if self.state != PlayerState::Playing {
            if self.state == PlayerState::Stopped {
                self.position_secs = 0.0;
            }
            self.state = PlayerState::Playing;
        }
    }

    pub fn pause(&mut self) {
        if self.state == PlayerState::Playing {
            self.state = PlayerState::Paused;
        }
    }

    pub fn stop(&mut self) {
        self.state = PlayerState::Stopped;
        self.position_secs = 0.0;
    }

    pub fn toggle(&mut self) {
        match self.state {
            PlayerState::Playing => self.pause(),
            _ => self.play(),
        }
    }

    /// Advance the clock; auto-stops at the end (no real audio in #4 —
    /// synthesis/playback wiring arrives with #30).
    pub fn tick(&mut self, dt_secs: f32) {
        if self.state == PlayerState::Playing {
            self.position_secs += dt_secs;
            if self.position_secs >= self.duration_secs {
                self.stop();
            }
        }
    }

    /// Play/pause button + progress bar + time readout.
    pub fn show(&mut self, ui: &mut Ui) {
        // Progress only advances on repaint; request frames while playing.
        if self.state == PlayerState::Playing {
            ui.ctx().request_repaint();
        }
        ui.horizontal(|ui| {
            let glyph = if self.state == PlayerState::Playing {
                "| |"
            } else {
                ">  "
            };
            if button(ui, ButtonKind::Audio, glyph).clicked() {
                self.toggle();
            }
            let bar = egui::ProgressBar::new(self.progress())
                .fill(palette::secondary())
                .show_percentage();
            ui.add_sized(
                egui::vec2((ui.available_width() - 110.0).max(40.0), 18.0),
                bar,
            );
            ui.with_layout(egui::Layout::right_to_left(Align::Center), |ui| {
                ui.monospace(format!(
                    "{} / {}",
                    fmt_time(self.position_secs),
                    fmt_time(self.duration_secs)
                ));
            });
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn button_kinds_use_theme_colors() {
        assert_eq!(
            ButtonKind::Primary.colors(),
            (palette::primary(), palette::surface())
        );
        assert_eq!(
            ButtonKind::Audio.colors(),
            (palette::secondary(), palette::surface())
        );
        let (fill, fg) = ButtonKind::Secondary.colors();
        assert_eq!(fill, palette::surface());
        assert_eq!(fg, palette::primary());
        assert_ne!(ButtonKind::Primary, ButtonKind::Secondary);
    }

    #[test]
    fn chip_colors_flip_with_state() {
        let (on_fill, _) = chip_colors(true);
        let (off_fill, _) = chip_colors(false);
        assert_eq!(on_fill, palette::secondary());
        assert_ne!(on_fill, off_fill);
    }

    #[test]
    fn time_format_never_shows_sixty_seconds() {
        assert_eq!(fmt_time(0.0), "00:00");
        assert_eq!(fmt_time(59.4), "00:59");
        assert_eq!(fmt_time(59.6), "01:00");
        assert_eq!(fmt_time(125.0), "02:05");
    }

    #[test]
    fn player_lifecycle() {
        let mut p = AudioPlayer::new(100.0);
        assert_eq!(*p.state(), PlayerState::Stopped);
        assert_eq!(p.progress(), 0.0);

        p.toggle(); // stopped -> playing from 0
        assert_eq!(*p.state(), PlayerState::Playing);
        p.tick(25.0);
        assert_eq!(p.progress(), 0.25);

        p.toggle(); // playing -> paused, clock frozen
        assert_eq!(*p.state(), PlayerState::Paused);
        p.tick(25.0);
        assert_eq!(p.progress(), 0.25);

        p.play(); // paused -> playing resumes (no restart)
        p.tick(75.0); // reaches end -> auto-stop
        assert_eq!(*p.state(), PlayerState::Stopped);
        assert_eq!(p.progress(), 0.0);
        assert_eq!(p.position(), 0.0);
    }

    #[test]
    fn player_clamps_and_handles_zero_duration() {
        let mut p = AudioPlayer::new(0.0);
        assert_eq!(p.progress(), 0.0);
        p.play();
        p.tick(10.0);
        assert_eq!(*p.state(), PlayerState::Stopped);
    }
}
