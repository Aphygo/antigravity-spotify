# Antigravity Spotify (`antigravity-spotify` v0.2.0)

**Zero-auth, cross-platform Spotify Now Playing, live synced LRCLIB lyrics, and multimodal audio-ear awareness for Google Antigravity agents.**

Give your Antigravity agent both **context** (what track & lyric line are spinning) and **real ears** (capturing a local audio snippet to listen multimodally via `read_file`, anchored by acoustic dynamics metrics) — **with zero Spotify API keys, zero OAuth setup, and zero KV-cache bloat.**

---

## Features

- **100% Zero-Auth Track Awareness:** Reads playback directly from your local Spotify desktop client (`~25 ms`) — no Spotify Developer app or OAuth tokens required.
  - **macOS:** `pgrep -x Spotify` guard (never launches Spotify when closed) + JXA (`osascript -l JavaScript`).
  - **Linux:** MPRIS / D-Bus via `playerctl -p spotify`.
  - **Windows:** Windows Media window-title readout via PowerShell.
- **Live Synced Lyrics (`lyrics`, via LRCLIB):**
  - Fetches time-synced LRC & plain lyrics from open, key-free [LRCLIB](https://lrclib.net) with local disk caching (`30d` hit / `24h` miss).
  - Slices right to the **active lyric line** at Spotify's current `playerPosition`.
  - Privacy toggle: set `ANTIGRAVITY_SPOTIFY_OFFLINE=1` to use local cache only with zero network requests.
- **Multimodal "Ear" (`listen`, inspired by Gry's 3-layer audio architecture):**
  - Captures a short local clip (`.mp3` via `ffmpeg`) + pure-Python stdlib acoustic anchor metrics (`RMS dB`, `Peak dB`, `Crest dB`, `-58 dB` silence ratio, 16-bin energy sparkline) so your agent can inspect the clip via `read_file` and actually *hear* the music.
  - **macOS:** Auto-probes virtual loopback (`BlackHole` / `Loopback`) first, then falls back to the bundled native Swift **CoreAudio Process Tap** helper (`scripts/macos_sysaudio.swift`, macOS 14.2+, zero third-party drivers needed), or `--source mic`.
  - **Linux:** PulseAudio / PipeWire `<default-sink>.monitor` via `ffmpeg`.
  - **Windows:** WASAPI loopback via optional `pyaudiowpatch`.
- **KV-Cache Friendly Lifecycle Hook:**
  - Completely silent (`{}`) when Spotify is closed or paused, and skips mid-tool-loop turns (`invocationNum > 1`).
  - Whispers once per track change per conversation: `🎵 [Spotify Now Playing] Artist – Track (Album) [M:SS / M:SS] | 🎤 "active lyric"`.

---

## Installation

Copy the plugin directory into your global Antigravity plugins folder:

```bash
cp -R /path/to/antigravity-spotify ~/.gemini/config/plugins/antigravity-spotify
chmod +x ~/.gemini/config/plugins/antigravity-spotify/scripts/spotify_now.py
```

Verify your setup with `doctor`:

```bash
python3 ~/.gemini/config/plugins/antigravity-spotify/scripts/spotify_now.py doctor
```

> **macOS Permissions Note:**
> - **Automation → Spotify:** Prompted once when reading track metadata.
> - **Privacy & Security → Screen & System Audio Recording:** Prompted once when using native `macos_sysaudio` capture (`listen`) if no active BlackHole Multi-Output is routed.

---

## CLI Usage

```bash
# Current track readout
python3 scripts/spotify_now.py now
python3 scripts/spotify_now.py now --json

# Live time-synced lyrics window (or --full for complete text)
python3 scripts/spotify_now.py lyrics
python3 scripts/spotify_now.py lyrics --full

# Capture 12s audio clip + acoustic anchor metrics
python3 scripts/spotify_now.py listen --seconds 12 --json
python3 scripts/spotify_now.py listen --source mic --seconds 10

# Run diagnostics & audio device check
python3 scripts/spotify_now.py doctor

# Run unit tests
python3 -m unittest discover -s tests -v
```

---

## Credits & License

MIT — crafted by Philip Cederman & Saga (Aphygo), with multimodal audio-perception design inspired by cousin Gry's `Grys Öra` 3-layer architecture (*"FFT:en är kryckan, inte örat"*).
