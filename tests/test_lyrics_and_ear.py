import json
import math
import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audio_ear    # noqa: E402
import lyrics       # noqa: E402
import spotify_now  # noqa: E402


def _write_sine_wav(path: Path, freq_hz: float = 440.0, amp: float = 0.25, duration_s: float = 0.5, sr: int = 16000) -> None:
    n = int(sr * duration_s)
    frames = bytearray()
    for i in range(n):
        val = int(round(amp * 32767.0 * math.sin(2.0 * math.pi * freq_hz * i / sr)))
        frames.extend(struct.pack("<hh", val, val))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(frames))


def _write_silent_wav(path: Path, duration_s: float = 0.5, sr: int = 16000) -> None:
    n = int(sr * duration_s)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00\x00\x00" * n)


class TestLyricsAndEar(unittest.TestCase):
    def test_clean_track_title(self):
        self.assertEqual(
            lyrics.clean_track_title("Teeth Marks - feat. Haley Roughton"),
            "Teeth Marks",
        )
        self.assertEqual(
            lyrics.clean_track_title("Winter Sun (Remastered 2024)"),
            "Winter Sun",
        )
        self.assertEqual(
            lyrics.clean_track_title("Riverside [Live Acoustic]"),
            "Riverside",
        )

    def test_parse_lrc_and_slice_synced_lines(self):
        sample = (
            "[00:23.34] Down by the river by the boat\n"
            "[00:27.10] Where everybody goes to be alone\n"
            "[00:30.39] Where you won't see any rising sun\n"
            "[00:34.02] Down to the river we will run\n"
        )
        parsed = lyrics.parse_lrc(sample)
        self.assertEqual(len(parsed), 4)
        self.assertAlmostEqual(parsed[0][0], 23.34, places=2)

        sliced = lyrics.slice_synced_lines(parsed, position_sec=28.0, before=1, after=1)
        self.assertEqual(sliced["current"], "Where everybody goes to be alone")
        active_rows = [w for w in sliced["window"] if w["active"]]
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(active_rows[0]["text"], "Where everybody goes to be alone")

        before_start = lyrics.slice_synced_lines(parsed, position_sec=5.0)
        self.assertEqual(before_start["current"], "")

    def test_fetch_lyrics_cache_and_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp)
            off = lyrics.fetch_lyrics("Agnes Obel", "Riverside", cache_dir=cdir, offline=True)
            self.assertFalse(off["found"])
            self.assertTrue(off.get("offline"))

            fake_rec = {
                "syncedLyrics": "[00:23.34] Down by the river by the boat\n",
                "plainLyrics": "Down by the river by the boat",
                "instrumental": False,
            }
            with mock.patch.object(lyrics, "_query_lrclib_get", return_value=fake_rec) as m_get:
                res1 = lyrics.fetch_lyrics("Agnes Obel", "Riverside", "Philharmonics", 229.0, cache_dir=cdir)
                self.assertTrue(res1["found"])
                self.assertFalse(res1["cached"])
                self.assertEqual(m_get.call_count, 1)

                # Second call uses disk cache even when offline=True
                res2 = lyrics.fetch_lyrics("Agnes Obel", "Riverside", "Philharmonics", 229.0, cache_dir=cdir, offline=True)
                self.assertTrue(res2["found"])
                self.assertTrue(res2["cached"])
                self.assertEqual(m_get.call_count, 1)

    def test_analyze_wav_sine_vs_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            sine_wav = tdir / "sine.wav"
            silent_wav = tdir / "silent.wav"
            _write_sine_wav(sine_wav)
            _write_silent_wav(silent_wav)

            m_sine = audio_ear.analyze_wav(sine_wav)
            self.assertFalse(m_sine["is_silent"])
            self.assertGreater(m_sine["rms_db"], -25.0)
            self.assertEqual(m_sine["silence_ratio"], 0.0)
            self.assertEqual(len(m_sine["energy_contour"]), 16)

            m_sil = audio_ear.analyze_wav(silent_wav)
            self.assertTrue(m_sil["is_silent"])
            self.assertEqual(m_sil["silence_ratio"], 1.0)
            self.assertLess(m_sil["rms_db"], -60.0)

    def test_hook_enrichment_with_lyric_instrumental_and_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            track = {
                "running": True,
                "state": "playing",
                "artist": "Agnes Obel",
                "title": "Riverside",
                "album": "Philharmonics",
                "position_sec": 28.0,
                "duration_sec": 229.0,
                "progress_str": "0:28 / 3:49",
            }
            out_lyr = spotify_now.handle_hook(
                "pre_invocation",
                json.dumps({"conversationId": "c-lyr", "invocationNum": 1}),
                query_fn=lambda: track,
                lyrics_fn=lambda _info, _dir: {
                    "found": True,
                    "instrumental": False,
                    "has_synced": True,
                    "synced_lyrics": "[00:23.34] Down by the river by the boat\n[00:27.10] Where everybody goes to be alone\n",
                },
                state_dir=sdir,
                now_ts=1000.0,
            )
            msg_lyr = out_lyr["injectSteps"][0]["systemMessage"]["systemMessage"]
            self.assertIn('🎤 "Where everybody goes to be alone"', msg_lyr)

            out_inst = spotify_now.handle_hook(
                "pre_invocation",
                json.dumps({"conversationId": "c-inst", "invocationNum": 1}),
                query_fn=lambda: track,
                lyrics_fn=lambda _info, _dir: {"found": True, "instrumental": True, "has_synced": False},
                state_dir=sdir,
                now_ts=1000.0,
            )
            msg_inst = out_inst["injectSteps"][0]["systemMessage"]["systemMessage"]
            self.assertIn("| [Instrumental]", msg_inst)

            # Offline mode prevents network call when lyrics_fn is omitted
            with mock.patch.dict(os.environ, {"ANTIGRAVITY_SPOTIFY_OFFLINE": "1"}):
                with mock.patch.object(lyrics, "_query_lrclib_get") as m_net:
                    out_off = spotify_now.handle_hook(
                        "pre_invocation",
                        json.dumps({"conversationId": "c-off", "invocationNum": 1}),
                        query_fn=lambda: track,
                        state_dir=sdir,
                        now_ts=1000.0,
                    )
                    self.assertIn("injectSteps", out_off)
                    self.assertEqual(m_net.call_count, 0)


if __name__ == "__main__":
    unittest.main()
