import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import spotify_now  # noqa: E402


class TestSpotifyNow(unittest.TestCase):
    def test_normalize_duration_sec(self):
        self.assertEqual(spotify_now.normalize_duration_sec(225000), 225.0)
        self.assertEqual(spotify_now.normalize_duration_sec(225), 225.0)
        self.assertEqual(spotify_now.normalize_duration_sec(0), 0.0)
        self.assertEqual(spotify_now.normalize_duration_sec(None), 0.0)

    def test_format_mmss(self):
        self.assertEqual(spotify_now.format_mmss(0), "0:00")
        self.assertEqual(spotify_now.format_mmss(83), "1:23")
        self.assertEqual(spotify_now.format_mmss(225.4), "3:45")
        self.assertEqual(spotify_now.format_mmss(-5), "0:00")

    def test_hook_silent_when_not_running_or_paused(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            for payload_info in [
                {"running": False, "state": "stopped"},
                {"running": True, "state": "paused", "artist": "Ólafur Arnalds", "title": "Saman"},
                {"running": True, "state": "stopped"},
            ]:
                out = spotify_now.handle_hook(
                    "pre_invocation",
                    json.dumps({"conversationId": "c1", "invocationNum": 1}),
                    query_fn=lambda info=payload_info: info,
                    state_dir=sdir,
                    now_ts=1000.0,
                )
                self.assertEqual(out, {})

    def test_hook_silent_on_tool_loop_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = spotify_now.handle_hook(
                "pre_invocation",
                json.dumps({"conversationId": "c1", "invocationNum": 2}),
                query_fn=lambda: {"running": True, "state": "playing", "artist": "Björk", "title": "Jóga"},
                state_dir=Path(tmp),
                now_ts=1000.0,
            )
            self.assertEqual(out, {})

    def test_hook_deduplication_and_track_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp)
            track1 = {
                "running": True,
                "state": "playing",
                "artist": "Ólafur Arnalds",
                "title": "Saman",
                "album": "re:member",
                "progress_str": "0:42 / 2:13",
            }
            stdin_c1 = json.dumps({"conversationId": "conv-a", "invocationNum": 1})

            # First turn injects track1
            out1 = spotify_now.handle_hook("pre_invocation", stdin_c1, query_fn=lambda: track1, state_dir=sdir, now_ts=1000.0)
            self.assertIn("injectSteps", out1)
            msg1 = out1["injectSteps"][0]["systemMessage"]["systemMessage"]
            self.assertIn("Ólafur Arnalds – Saman", msg1)
            self.assertIn("(re:member)", msg1)

            # Second turn with same track is silent (KV-cache friendly)
            out2 = spotify_now.handle_hook("pre_invocation", stdin_c1, query_fn=lambda: track1, state_dir=sdir, now_ts=1020.0)
            self.assertEqual(out2, {})

            # Different conversationId gets its own whisper
            stdin_c2 = json.dumps({"conversationId": "conv-b", "invocationNum": 1})
            out_b = spotify_now.handle_hook("pre_invocation", stdin_c2, query_fn=lambda: track1, state_dir=sdir, now_ts=1025.0)
            self.assertIn("injectSteps", out_b)

            # Track change triggers new whisper on conv-a
            track2 = {
                "running": True,
                "state": "playing",
                "artist": "Agnes Obel",
                "title": "Riverside",
                "album": "Philharmonics",
                "progress_str": "0:10 / 3:48",
            }
            out3 = spotify_now.handle_hook("pre_invocation", stdin_c1, query_fn=lambda: track2, state_dir=sdir, now_ts=1100.0)
            self.assertIn("injectSteps", out3)
            self.assertIn("Agnes Obel – Riverside", out3["injectSteps"][0]["systemMessage"]["systemMessage"])

    def test_hook_handles_backend_exception_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            def _boom():
                raise RuntimeError("boom")

            out = spotify_now.handle_hook(
                "session_start",
                "{bad json",
                query_fn=_boom,
                state_dir=Path(tmp),
                now_ts=1000.0,
            )
            self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()
