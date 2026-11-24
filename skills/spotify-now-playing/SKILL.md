---
name: spotify-now-playing
description: >-
  Read what the user is currently listening to on Spotify locally with zero API keys,
  fetch live time-synced lyrics via LRCLIB, or capture a local audio clip to hear
  the music multimodally via read_file. Use when the user asks what is playing,
  asks about the lyrics, asks you to listen to the song, or wants to run doctor.
---

# Spotify Now Playing, Lyrics & Multimodal Ear Skill

Check live Spotify playback, synced lyrics, and real audio locally with zero OAuth or API keys.

## Commands

Run the installed helper script:

```bash
# Current track metadata (JSON)
python3 ~/.gemini/config/plugins/antigravity-spotify/scripts/spotify_now.py now --json

# Live time-synced lyrics window at current playback position (or --full for complete text)
python3 ~/.gemini/config/plugins/antigravity-spotify/scripts/spotify_now.py lyrics
python3 ~/.gemini/config/plugins/antigravity-spotify/scripts/spotify_now.py lyrics --full

# Capture a local 12-second audio clip + WAV acoustic anchor metrics
python3 ~/.gemini/config/plugins/antigravity-spotify/scripts/spotify_now.py listen --seconds 12 --json

# Run diagnostics (OS, latency, audio devices, CoreAudio tap, cache)
python3 ~/.gemini/config/plugins/antigravity-spotify/scripts/spotify_now.py doctor
```

## Multimodal Listening Protocol (Gry's Rule)

When the user asks you to *listen* to or feel the music:

1. Run `spotify_now.py listen --seconds 12 --json`.
2. Check `capture.ok` and `capture.metrics.is_silent`. If `is_silent` is `true`, share the `capture.hint` (e.g., macOS *Screen & System Audio Recording* permission, BlackHole routing, or `--source mic`) instead of guessing what the audio sounds like.
3. If audio was captured cleanly (`is_silent: false`), **call `read_file` on `capture.clip_path`** so you actually hear the MP3 waveform multimodally. Treat the DSP numbers (`rms_db`, `peak_db`, `crest_db`, `energy_contour`) strictly as an objective anchor alongside what you hear.

## Notes

- On **macOS**, Spotify is never launched if closed (`pgrep -x Spotify` runs first).
- Set `ANTIGRAVITY_SPOTIFY_OFFLINE=1` to disable network LRCLIB lookups (local cache only).
- First-time macOS permissions: **Automation → Spotify** (for track metadata) and **Screen & System Audio Recording** (for native `macos_sysaudio` capture when `--source auto/sysaudio` is used).
