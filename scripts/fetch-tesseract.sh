#!/usr/bin/env bash
# Fetch and build the OCR runtime assets that ship with Anees (Issue #9):
#
#   - ara.traineddata / eng.traineddata (tessdata_fast, pinned + checksummed)
#       -> python-backend/src/anees/pdf/tessdata/
#   - a statically-linked Tesseract 5.x (compiled from source, Linux)
#       -> python-backend/src/anees/pdf/bin/tesseract
#
# The Python backend discovers both automatically (see anees.pdf.ocr), so the
# packaged artifact needs no runtime download. Run `--traineddata-only` when a
# platform-provided Tesseract is used instead (e.g. Windows).
#
# Usage: scripts/fetch-tesseract.sh [--traineddata-only]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_DIR="$REPO_ROOT/python-backend/src/anees/pdf"
TESSDATA_DIR="$PKG_DIR/tessdata"
BIN_DIR="$PKG_DIR/bin"

# Pinned so the shipped languages never drift.
TESSDATA_REF="87416418657359cb625c412a48b6e1d6d41c29bd"
TESSERACT_VERSION="5.5.0"
LEPTONICA_VERSION="1.85.0"

TRAINEDDATA_ONLY=0
[ "${1:-}" = "--traineddata-only" ] && TRAINEDDATA_ONLY=1

log() { printf '[ocr-assets] %s\n' "$*"; }

fetch_traineddata() {
  mkdir -p "$TESSDATA_DIR"
  local name want dest url
  while read -r name want; do
    dest="$TESSDATA_DIR/$name"
    if [ -f "$dest" ] && [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$want" ]; then
      log "traineddata present: $name"
      continue
    fi
    url="https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$TESSDATA_REF/$name"
    log "fetching $name"
    curl -fsSL "$url" -o "$dest"
    echo "$want  $dest" | sha256sum -c --status || {
      log "checksum mismatch for $name"; exit 1; }
  done <<'EOF'
eng.traineddata 7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2
ara.traineddata e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729
EOF
}

build_static_tesseract() {
  local prefix work out
  for tool in gcc make pkg-config autoconf automake libtool curl; do
    command -v "$tool" >/dev/null || { log "missing build tool: $tool"; exit 1; }
  done

  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  prefix="$work/prefix"

  log "building static leptonica $LEPTONICA_VERSION"
  curl -fsSL "https://github.com/DanBloomberg/leptonica/releases/download/$LEPTONICA_VERSION/leptonica-$LEPTONICA_VERSION.tar.gz" \
    | tar xz -C "$work"
  (
    cd "$work/leptonica-$LEPTONICA_VERSION"
    ./configure --prefix="$prefix" --disable-shared --enable-static --without-libwebp >/dev/null
    make -j"$(nproc)" >/dev/null
    make install >/dev/null
  )

  log "building static tesseract $TESSERACT_VERSION"
  curl -fsSL "https://github.com/tesseract-ocr/tesseract/archive/refs/tags/$TESSERACT_VERSION.tar.gz" \
    | tar xz -C "$work"
  (
    cd "$work/tesseract-$TESSERACT_VERSION"
    ./autogen.sh >/dev/null
    PKG_CONFIG_PATH="$prefix/lib/pkgconfig" ./configure --prefix="$prefix" \
      --disable-shared --enable-static >/dev/null
    make -j"$(nproc)" >/dev/null
  )

  mkdir -p "$BIN_DIR"
  out="$BIN_DIR/tesseract"
  cp "$work/tesseract-$TESSERACT_VERSION/src/tesseract" "$out"
  chmod +x "$out"
  log "installed static tesseract -> $out"
}

fetch_traineddata
if [ "$TRAINEDDATA_ONLY" = 1 ]; then
  log "traineddata-only; skipping Tesseract build"
elif [ "$(uname -s)" = "Linux" ]; then
  build_static_tesseract
else
  log "static Tesseract build is Linux-only; provide a binary via \$ANEES_TESSERACT"
fi
