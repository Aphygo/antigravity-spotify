#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Installing Antigravity Spotify plugin from: $PLUGIN_ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required." >&2
  exit 1
fi

chmod +x "$PLUGIN_ROOT/scripts/spotify_now.py" \
         "$PLUGIN_ROOT/scripts/lyrics.py" \
         "$PLUGIN_ROOT/scripts/audio_ear.py" \
         "$PLUGIN_ROOT/scripts/install.sh"

CACHE_DIR="${PLUGIN_DATA:-$HOME/.cache/antigravity-spotify}"
mkdir -p "$CACHE_DIR"

if [[ "$(uname -s)" == "Darwin" ]]; then
  if command -v swiftc >/dev/null 2>&1; then
    echo "Pre-compiling macOS CoreAudio Process Tap helper..."
    if swiftc -O "$PLUGIN_ROOT/scripts/macos_sysaudio.swift" \
        -o "$CACHE_DIR/macos_sysaudio" \
        -framework CoreAudio \
        -framework AudioToolbox \
        -framework Foundation; then
      echo "Compiled: $CACHE_DIR/macos_sysaudio"
    else
      echo "Warning: swiftc compilation failed; --source blackhole or --source mic remain available." >&2
    fi
  else
    echo "Note: swiftc not found (Xcode Command Line Tools). Native sysaudio tap skipped; --source blackhole or --source mic remain available."
  fi
fi

echo ""
python3 "$PLUGIN_ROOT/scripts/spotify_now.py" doctor
