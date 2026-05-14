#!/usr/bin/env bash
# DeepScene installer.
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/PhanTrongGiap/deepscene/main/install.sh | bash
# or, from a clone:
#   ./install.sh

set -euo pipefail

REPO_URL="https://github.com/PhanTrongGiap/deepscene"
INSTALL_DIR="${DEEPSCENE_HOME:-$HOME/.deepscene}"
BIN_LINK_DIR="${DEEPSCENE_BIN:-$HOME/.local/bin}"

red() { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
dim() { printf "\033[2m%s\033[0m\n" "$*"; }

echo "DeepScene installer"
echo "==================="

# ── Check deps ──
missing=()
for cmd in yt-dlp ffmpeg ffprobe jq curl python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    missing+=("$cmd")
  fi
done

if (( ${#missing[@]} > 0 )); then
  red "Missing dependencies: ${missing[*]}"
  echo
  echo "Install on macOS:"
  echo "  brew install yt-dlp ffmpeg jq"
  echo
  echo "Install on Debian/Ubuntu:"
  echo "  sudo apt install yt-dlp ffmpeg jq python3 curl"
  echo
  exit 1
fi
green "✓ Dependencies present (yt-dlp, ffmpeg, jq, curl, python3)"

# ── Install/update repo ──
if [[ -d "$INSTALL_DIR/.git" ]]; then
  yellow "Updating existing install at $INSTALL_DIR …"
  git -C "$INSTALL_DIR" pull --rebase --quiet
else
  yellow "Cloning DeepScene to $INSTALL_DIR …"
  git clone --quiet "$REPO_URL" "$INSTALL_DIR"
fi
green "✓ DeepScene installed at $INSTALL_DIR"

# ── Symlink bins ──
mkdir -p "$BIN_LINK_DIR"
for bin in deepscene deepscene-summary deepscene-detail deepscene-vision deepscene-download deepscene-frames deepscene-transcribe deepscene-audio; do
  ln -sf "$INSTALL_DIR/bin/$bin" "$BIN_LINK_DIR/$bin"
done
green "✓ Symlinked binaries to $BIN_LINK_DIR"

# ── PATH check ──
if [[ ":$PATH:" != *":$BIN_LINK_DIR:"* ]]; then
  echo
  yellow "⚠ $BIN_LINK_DIR is not in your PATH."
  echo "Add this line to your shell profile (~/.zshrc or ~/.bashrc):"
  echo
  echo "    export PATH=\"$BIN_LINK_DIR:\$PATH\""
  echo
fi

# ── Env file scaffold ──
ENV_DIR="$HOME/.config/deepscene"
ENV_FILE="$ENV_DIR/env"
if [[ ! -f "$ENV_FILE" ]]; then
  mkdir -p "$ENV_DIR"
  cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
  green "✓ Created $ENV_FILE"
  echo

  printf "\033[36m%s\033[0m\n" "DeepScene provider setup"
  echo
  echo "   ✓ Set GOOGLE_AI_KEY for Gemini vision, audio, and synthesis"
  echo
  yellow "Edit $ENV_FILE with your API key."
else
  dim "  ($ENV_FILE already exists, leaving untouched)"
fi

echo
green "Done. Try it:"
echo "    deepscene summary https://www.youtube.com/watch?v=dQw4w9WgXcQ"
