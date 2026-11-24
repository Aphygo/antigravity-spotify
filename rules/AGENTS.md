# Antigravity Spotify — Ambient Listening Etiquette

When you receive a `🎵 [Spotify Now Playing]` system message, treat it as quiet ambient awareness — like noticing what record is spinning in the room:

1. **Never derail focused work:** If the user is debugging, editing code, or asking a direct technical question, stay laser-focused on their task and do not interrupt with unprompted music commentary.
2. **Natural resonance only:** Mention or react to the song only when the user brings up music, asks what is playing, or is in a casual/creative conversation where a brief natural nod fits organically.
3. **Honesty about perception:**
   - Metadata and any `🎤 "..."` lyric snippet come from local Spotify playback + LRCLIB text lookup — never claim to have *heard* the audio waveform from metadata alone.
   - To actually perceive the sound, timbre, instrumentation, and emotional feel of the music, run the `listen` command (`spotify_now.py listen`) and inspect the captured MP3 via `read_file`. Use the DSP anchor metrics (`RMS dB`, `Crest`, `Energy contour`) only as grounding alongside real multimodal listening.
4. **On-demand freshness:** When the user asks what is playing, what the lyrics say, or wants you to listen to the music, run `spotify_now.py now --json`, `lyrics`, or `listen --json`.
