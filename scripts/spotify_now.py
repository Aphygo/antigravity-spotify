#!/usr/bin/env python3
"""Zero-auth, cross-platform Spotify Now Playing, Synced Lyrics & Ear bridge for Antigravity."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audio_ear  # noqa: E402
import lyrics     # noqa: E402


VERSION = "0.2.0"
SEVEN_DAYS_SEC = 7 * 24 * 60 * 60

MACOS_JXA = r"""
const app = Application("Spotify");
if (!app.running()) {
  JSON.stringify({running: false, state: "stopped"});
} else {
  const st = String(app.playerState());
  if (st === "stopped") {
    JSON.stringify({running: true, state: "stopped"});
  } else {
    const t = app.currentTrack;
    let pos = 0;
    try { pos = Number(app.playerPosition()) || 0; } catch (e) {}
    let artist = "", title = "", album = "", dur = 0, url = "", tid = "";
    try { artist = String(t.artist() || ""); } catch (e) {}
    try { title = String(t.name() || ""); } catch (e) {}
    try { album = String(t.album() || ""); } catch (e) {}
    try { dur = Number(t.duration()) || 0; } catch (e) {}
    try { url = String(t.spotifyUrl() || ""); } catch (e) {}
    try { tid = String(t.id() || ""); } catch (e) {}
    JSON.stringify({
      running: true,
      state: st,
      artist: artist,
      title: title,
      album: album,
      position_sec: pos,
      duration_raw: dur,
      spotify_url: url,
      track_id: tid
    });
  }
}
""".strip()


def normalize_duration_sec(raw: float | int | None) -> float:
    """Normalize Spotify track duration (macOS AppleScript returns milliseconds)."""
    if raw is None:
        return 0.0
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if val <= 0:
        return 0.0
    return val / 1000.0 if val > 10000 else val


def format_mmss(sec: float | int | None) -> str:
    """Format seconds as M:SS."""
    if sec is None:
        return "0:00"
    try:
        total = max(0, int(round(float(sec))))
    except (TypeError, ValueError):
        return "0:00"
    return f"{total // 60}:{total % 60:02d}"


def build_progress_str(position_sec: float, duration_sec: float) -> str:
    """Format playback progress string."""
    if duration_sec > 0:
        return f"{format_mmss(position_sec)} / {format_mmss(duration_sec)}"
    if position_sec > 0:
        return format_mmss(position_sec)
    return ""


def get_spotify_macos() -> dict[str, Any]:
    """Query Spotify on macOS without ever launching it if closed."""
    proc_check = subprocess.run(
        ["pgrep", "-x", "Spotify"],
        capture_output=True,
        text=True,
        timeout=2,
    )
    if proc_check.returncode != 0:
        return {"running": False, "state": "stopped", "platform": "macos"}

    res = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", MACOS_JXA],
        capture_output=True,
        text=True,
        timeout=3,
    )
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip()
        is_perm = "-1743" in err or "Not authorized to send Apple events" in err
        return {
            "running": True,
            "state": "unknown",
            "platform": "macos",
            "permission_error": is_perm,
            "error": err,
        }

    data = json.loads(res.stdout.strip() or "{}")
    data["platform"] = "macos"
    pos = float(data.get("position_sec") or 0.0)
    dur = normalize_duration_sec(data.pop("duration_raw", 0))
    data["position_sec"] = round(pos, 2)
    data["duration_sec"] = round(dur, 2)
    prog = build_progress_str(pos, dur)
    if prog:
        data["progress_str"] = prog
    return data


def get_spotify_linux() -> dict[str, Any]:
    """Query Spotify on Linux via MPRIS / playerctl."""
    try:
        status_res = subprocess.run(
            ["playerctl", "-p", "spotify", "status"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except FileNotFoundError:
        return {
            "running": False,
            "state": "stopped",
            "platform": "linux",
            "missing_playerctl": True,
        }

    if status_res.returncode != 0:
        return {"running": False, "state": "stopped", "platform": "linux"}

    state = status_res.stdout.strip().lower()
    if state == "stopped":
        return {"running": True, "state": "stopped", "platform": "linux"}

    meta_res = subprocess.run(
        [
            "playerctl",
            "-p",
            "spotify",
            "metadata",
            "--format",
            "{{artist}}||{{title}}||{{album}}||{{position}}||{{mpris:length}}||{{xesam:url}}",
        ],
        capture_output=True,
        text=True,
        timeout=2,
    )
    parts = (meta_res.stdout.strip() if meta_res.returncode == 0 else "").split("||")
    while len(parts) < 6:
        parts.append("")
    artist, title, album, pos_raw, len_raw, url = parts[:6]

    def _us_to_sec(raw: str) -> float:
        try:
            v = float(raw)
            return v / 1_000_000.0 if v > 10000 else v
        except (TypeError, ValueError):
            return 0.0

    pos = _us_to_sec(pos_raw)
    dur = _us_to_sec(len_raw)
    out: dict[str, Any] = {
        "running": True,
        "state": state,
        "platform": "linux",
        "artist": artist,
        "title": title,
        "album": album,
        "position_sec": round(pos, 2),
        "duration_sec": round(dur, 2),
        "spotify_url": url,
    }
    prog = build_progress_str(pos, dur)
    if prog:
        out["progress_str"] = prog
    return out


def get_spotify_windows() -> dict[str, Any]:
    """Query Spotify on Windows via main window title."""
    ps_cmd = (
        "Get-Process -Name Spotify -ErrorAction SilentlyContinue | "
        "Where-Object { $_.MainWindowTitle } | "
        "Select-Object -First 1 -ExpandProperty MainWindowTitle"
    )
    res = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        timeout=2,
    )
    title = (res.stdout or "").strip()
    if not title:
        return {"running": False, "state": "stopped", "platform": "windows"}
    if title in {"Spotify", "Spotify Free", "Spotify Premium"}:
        return {"running": True, "state": "paused", "platform": "windows"}
    if " - " in title:
        artist, track = title.split(" - ", 1)
    else:
        artist, track = "", title
    return {
        "running": True,
        "state": "playing",
        "platform": "windows",
        "artist": artist.strip(),
        "title": track.strip(),
        "album": "",
    }


def get_now_playing() -> dict[str, Any]:
    """Dispatch to the current OS backend."""
    sys_name = platform.system().lower()
    if sys_name == "darwin":
        return get_spotify_macos()
    if sys_name == "linux":
        return get_spotify_linux()
    if sys_name == "windows":
        return get_spotify_windows()
    return {"running": False, "state": "stopped", "platform": sys_name}


def format_track_line(info: dict[str, Any], prefix: str = "🎵 [Spotify Now Playing]") -> str:
    """Format a compact one-line representation of the track."""
    artist = (info.get("artist") or "").strip()
    title = (info.get("title") or "").strip()
    album = (info.get("album") or "").strip()
    prog = (info.get("progress_str") or "").strip()

    main = f"{artist} – {title}" if artist else title
    parts = [prefix, main]
    if album:
        parts.append(f"({album})")
    if prog:
        parts.append(f"[{prog}]")
    return " ".join(parts)


def get_state_dir() -> Path:
    """Resolve persistent cache directory for track deduplication, lyrics & clips."""
    plugin_data = os.environ.get("PLUGIN_DATA", "").strip()
    if plugin_data:
        return Path(plugin_data)
    return Path.home() / ".cache" / "antigravity-spotify"


def is_offline_mode() -> bool:
    """Return True if network lyrics lookup is disabled."""
    return os.environ.get("ANTIGRAVITY_SPOTIFY_OFFLINE", "").strip().lower() in {"1", "true", "yes"}


def load_last_seen(state_file: Path, now_ts: float) -> dict[str, dict[str, Any]]:
    """Load and prune deduplication state older than 7 days."""
    if not state_file.exists():
        return {}
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {
            k: v
            for k, v in raw.items()
            if isinstance(v, dict) and (now_ts - float(v.get("ts", 0))) < SEVEN_DAYS_SEC
        }
    except Exception:
        return {}


def save_last_seen(state_file: Path, data: dict[str, dict[str, Any]]) -> None:
    """Persist deduplication state safely."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_file)


def _fetch_track_lyrics(info: dict[str, Any], cache_dir: Path, timeout: float = 2.5, offline: bool | None = None) -> dict[str, Any]:
    off = is_offline_mode() if offline is None else offline
    return lyrics.fetch_lyrics(
        artist=info.get("artist", ""),
        title=info.get("title", ""),
        album=info.get("album", ""),
        duration_sec=float(info.get("duration_sec") or 0.0),
        cache_dir=cache_dir,
        timeout=timeout,
        offline=off,
    )


def handle_hook(
    event: str,
    stdin_text: str,
    query_fn: Callable[[], dict[str, Any]] = get_now_playing,
    lyrics_fn: Callable[[dict[str, Any], Path], dict[str, Any]] | None = None,
    state_dir: Path | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """Process an Antigravity lifecycle hook payload safely."""
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    if event == "pre_invocation":
        try:
            inv_num = int(payload.get("invocationNum", 1))
        except (TypeError, ValueError):
            inv_num = 1
        if inv_num > 1:
            return {}

    try:
        info = query_fn()
    except Exception:
        return {}

    if not info.get("running") or info.get("state") != "playing":
        return {}

    title = (info.get("title") or "").strip()
    if not title:
        return {}

    artist = (info.get("artist") or "").strip()
    conv_id = str(payload.get("conversationId") or "default")
    track_key = f"{artist}::{title}"

    ts = time.time() if now_ts is None else now_ts
    s_dir = get_state_dir() if state_dir is None else state_dir
    state_file = s_dir / "last_seen.json"

    state = load_last_seen(state_file, ts)
    if state.get(conv_id, {}).get("track_key") == track_key:
        return {}

    state[conv_id] = {"track_key": track_key, "ts": ts}
    try:
        save_last_seen(state_file, state)
    except Exception:
        pass

    msg = format_track_line(info)

    # Enrich once-per-track whisper with synced lyric snippet or instrumental tag
    try:
        lyr = (
            lyrics_fn(info, s_dir)
            if lyrics_fn is not None
            else _fetch_track_lyrics(info, s_dir, timeout=1.5)
        )
        if lyr.get("instrumental"):
            msg += " | [Instrumental]"
        elif lyr.get("has_synced"):
            parsed = lyrics.parse_lrc(lyr.get("synced_lyrics", ""))
            sliced = lyrics.slice_synced_lines(parsed, float(info.get("position_sec") or 0.0), before=0, after=0)
            curr = (sliced.get("current") or "").strip()
            if curr:
                msg += f' | 🎤 "{curr}"'
    except Exception:
        pass

    return {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": msg,
                }
            }
        ]
    }


def cmd_now(as_json: bool) -> int:
    info = get_now_playing()
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    if info.get("permission_error"):
        print("Spotify permission denied (-1743). Allow Terminal/Antigravity under macOS Settings -> Privacy & Security -> Automation -> Spotify.")
        return 1
    if not info.get("running"):
        print("Spotify is not running.")
        return 0
    state = info.get("state")
    if state == "playing":
        print(format_track_line(info))
    elif state == "paused":
        print(format_track_line(info, prefix="⏸ Paused:"))
    else:
        print(f"Spotify is open (state: {state}).")
    return 0


def cmd_lyrics(as_json: bool, full: bool) -> int:
    info = get_now_playing()
    if not info.get("running") or info.get("state") not in {"playing", "paused"} or not info.get("title"):
        if as_json:
            print(json.dumps({"found": False, "track": info}, ensure_ascii=False, indent=2))
        else:
            print("No active Spotify track.")
        return 0

    s_dir = get_state_dir()
    lyr = _fetch_track_lyrics(info, s_dir, timeout=3.0)
    pos = float(info.get("position_sec") or 0.0)
    synced_view: dict[str, Any] = {"current": "", "window": []}
    if lyr.get("has_synced"):
        parsed = lyrics.parse_lrc(lyr.get("synced_lyrics", ""))
        synced_view = lyrics.slice_synced_lines(parsed, pos, before=1, after=3)

    if as_json:
        out = {
            "track": info,
            "lyrics": lyr,
            "synced_slice": synced_view,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(format_track_line(info, prefix="🎵"))
    if lyr.get("instrumental"):
        print("🎹 Instrumental track (per LRCLIB).")
        return 0
    if not lyr.get("found"):
        msg = "Lyrics unavailable (offline mode)." if lyr.get("offline") else "No lyrics found on LRCLIB."
        print(msg)
        return 0

    if lyr.get("has_synced") and not full and synced_view.get("window"):
        print("")
        for row in synced_view["window"]:
            marker = "▶" if row["active"] else " "
            print(f"  {marker} [{row['time']}] {row['text']}")
        return 0

    text = (lyr.get("plain_lyrics") or lyr.get("synced_lyrics") or "").strip()
    print("")
    print(text)
    return 0


def cmd_listen(seconds: float, source: str, as_json: bool) -> int:
    s_dir = get_state_dir()
    info = get_now_playing()
    cap = audio_ear.capture_audio(seconds=seconds, source=source, out_dir=s_dir)

    lyric_hint: dict[str, Any] = {}
    if info.get("running") and info.get("title"):
        lyr = _fetch_track_lyrics(info, s_dir, timeout=1.5)
        if lyr.get("instrumental"):
            lyric_hint = {"instrumental": True}
        elif lyr.get("has_synced"):
            parsed = lyrics.parse_lrc(lyr.get("synced_lyrics", ""))
            lyric_hint = lyrics.slice_synced_lines(parsed, float(info.get("position_sec") or 0.0), before=1, after=2)

    if as_json:
        out = {
            "track": info,
            "capture": cap,
            "lyrics_context": lyric_hint,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if cap.get("ok") else 1

    if not cap.get("ok"):
        print(f"Audio capture failed: {cap.get('error')}")
        if cap.get("hint"):
            print(f"Hint: {cap['hint']}")
        return 1

    if info.get("running") and info.get("title"):
        print(format_track_line(info, prefix="🎧"))
    m = cap.get("metrics", {})
    print(f"- Clip path: {cap.get('clip_path')}")
    print(f"- Source: {cap.get('source')} ({cap.get('format')}, {m.get('duration_sec')}s)")
    print(f"- Anchor metrics: RMS {m.get('rms_db')} dB | Peak {m.get('peak_db')} dB | Crest {m.get('crest_db')} dB | Silence {m.get('silence_ratio')} | Contour {m.get('energy_contour')}")
    if lyric_hint.get("current"):
        print(f"- Active lyric: \"{lyric_hint['current']}\"")
    if cap.get("hint"):
        print(f"- Notice: {cap['hint']}")
    return 0


def cmd_doctor() -> int:
    t0 = time.perf_counter()
    info = get_now_playing()
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    state_dir = get_state_dir()
    writable = True
    write_err = ""
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        writable = False
        write_err = str(exc)

    print(f"Antigravity Spotify v{VERSION} — Doctor")
    print(f"- Platform: {platform.system()} ({platform.machine()})")
    print(f"- Query latency: {latency_ms} ms")
    print(f"- Spotify running: {'yes' if info.get('running') else 'no (not launched)'}")
    print(f"- Player state: {info.get('state')}")
    if info.get("permission_error"):
        print("- Automation permission: BLOCKED (-1743)")
        print("  Fix: System Settings -> Privacy & Security -> Automation -> allow Antigravity/Terminal to control Spotify.")
    elif info.get("missing_playerctl"):
        print("- Backend dependency: playerctl not found (install playerctl for Linux MPRIS support).")
    else:
        print("- Backend access: OK")
    if info.get("state") in {"playing", "paused"} and info.get("title"):
        print(f"- Current track: {format_track_line(info, prefix='🎧')}")

    print(f"- Lyrics mode: {'OFFLINE (cached only)' if is_offline_mode() else 'LRCLIB live + local cache'}")
    ffmpeg_path = shutil.which("ffmpeg")
    print(f"- ffmpeg: {ffmpeg_path if ffmpeg_path else 'NOT found (MP3 encoding & AVFoundation/Pulse capture unavailable)'}")

    if platform.system().lower() == "darwin":
        swift_bin = state_dir / "macos_sysaudio"
        print(f"- macOS CoreAudio tap binary: {swift_bin} ({'built' if swift_bin.exists() else 'will auto-compile on first listen'})")
        devs = audio_ear.list_macos_avfoundation_audio_devices()
        if devs:
            dev_str = ", ".join(f"[{idx}] {name}" for idx, name in devs)
            print(f"- AVFoundation audio inputs: {dev_str}")

    print(f"- State cache dir: {state_dir} ({'writable' if writable else f'NOT writable: {write_err}'})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity Spotify Now Playing, Lyrics & Ear CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_now = sub.add_parser("now", help="Show current Spotify track")
    p_now.add_argument("--json", action="store_true", help="Emit JSON output")

    p_lyr = sub.add_parser("lyrics", help="Fetch live synced/plain lyrics via LRCLIB")
    p_lyr.add_argument("--json", action="store_true", help="Emit JSON output")
    p_lyr.add_argument("--full", action="store_true", help="Print full lyrics instead of synced window")

    p_ear = sub.add_parser("listen", help="Capture a local audio snippet + acoustic metrics for multimodal listening")
    p_ear.add_argument("--seconds", type=float, default=12.0, help="Clip duration in seconds (default: 12)")
    p_ear.add_argument("--source", choices=["auto", "sysaudio", "blackhole", "mic"], default="auto", help="Audio capture source")
    p_ear.add_argument("--json", action="store_true", help="Emit JSON output")

    sub.add_parser("doctor", help="Run diagnostic checks")

    p_hook = sub.add_parser("hook", help="Antigravity lifecycle hook handler")
    p_hook.add_argument("--event", choices=["session_start", "pre_invocation"], required=True)

    args = parser.parse_args()
    if args.cmd == "now":
        return cmd_now(args.json)
    if args.cmd == "lyrics":
        return cmd_lyrics(args.json, args.full)
    if args.cmd == "listen":
        return cmd_listen(args.seconds, args.source, args.json)
    if args.cmd == "doctor":
        return cmd_doctor()
    if args.cmd == "hook":
        try:
            stdin_text = sys.stdin.read() if not sys.stdin.isatty() else ""
            out = handle_hook(args.event, stdin_text)
        except Exception:
            out = {}
        print(json.dumps(out, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
