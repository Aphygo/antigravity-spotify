#!/usr/bin/env python3
"""Cross-platform local audio capture & acoustic anchor analysis (pure Python stdlib)."""

from __future__ import annotations

import math
import os
import platform
import re
import shutil
import struct
import subprocess
import time
import wave
from pathlib import Path
from typing import Any


SPARK_CHARS = "▁▂▃▄▅▆▇█"
SILENCE_FRAME_DB = -58.0  # Gry's dynamic-silence threshold
SILENT_CLIP_DB = -60.0    # Gry's pre-capture silence check
MAX_STORED_CLIPS = 10


def analyze_wav(wav_path: Path) -> dict[str, Any]:
    """Compute acoustic anchor metrics (RMS dB, peak dB, crest, silence ratio, energy contour) from 16-bit PCM WAV."""
    with wave.open(str(wav_path), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width != 2 or n_frames == 0 or sample_rate <= 0:
        return {
            "duration_sec": 0.0,
            "sample_rate": sample_rate,
            "channels": channels,
            "rms_db": -96.0,
            "peak_db": -96.0,
            "crest_db": 0.0,
            "silence_ratio": 1.0,
            "is_silent": True,
            "energy_contour": SPARK_CHARS[0] * 16,
        }

    total_samples = n_frames * channels
    samples = struct.unpack(f"<{total_samples}h", raw)

    # Mix down to mono float in [-1.0, 1.0]
    inv_max = 1.0 / 32768.0
    if channels == 1:
        mono = [s * inv_max for s in samples]
    else:
        mono = [
            sum(samples[i : i + channels]) * inv_max / channels
            for i in range(0, len(samples), channels)
        ]

    n = len(mono)
    sum_sq = sum(x * x for x in mono)
    peak = max((abs(x) for x in mono), default=0.0)
    rms = math.sqrt(sum_sq / n) if n else 0.0

    rms_db = 20.0 * math.log10(max(rms, 1e-5))
    peak_db = 20.0 * math.log10(max(peak, 1e-5))
    crest_db = max(0.0, peak_db - rms_db)

    # 100ms frame silence ratio
    win = max(1, int(sample_rate * 0.1))
    silent_frames = 0
    total_windows = 0
    for i in range(0, n, win):
        seg = mono[i : i + win]
        if not seg:
            continue
        seg_rms = math.sqrt(sum(x * x for x in seg) / len(seg))
        seg_db = 20.0 * math.log10(max(seg_rms, 1e-5))
        if seg_db < SILENCE_FRAME_DB:
            silent_frames += 1
        total_windows += 1
    silence_ratio = (silent_frames / total_windows) if total_windows else 1.0

    # 16-bin energy contour sparkline
    bins = 16
    step = max(1, n // bins)
    bin_dbs: list[float] = []
    for b in range(bins):
        seg = mono[b * step : (b + 1) * step if b < bins - 1 else n]
        if not seg:
            bin_dbs.append(-96.0)
        else:
            b_rms = math.sqrt(sum(x * x for x in seg) / len(seg))
            bin_dbs.append(20.0 * math.log10(max(b_rms, 1e-5)))

    floor_db, ceil_db = -60.0, -6.0
    contour_chars = []
    for db in bin_dbs:
        norm = (db - floor_db) / (ceil_db - floor_db)
        norm = max(0.0, min(1.0, norm))
        idx = min(len(SPARK_CHARS) - 1, int(round(norm * (len(SPARK_CHARS) - 1))))
        contour_chars.append(SPARK_CHARS[idx])

    return {
        "duration_sec": round(n_frames / sample_rate, 2),
        "sample_rate": sample_rate,
        "channels": channels,
        "rms_db": round(rms_db, 1),
        "peak_db": round(peak_db, 1),
        "crest_db": round(crest_db, 1),
        "silence_ratio": round(silence_ratio, 2),
        "is_silent": rms_db < SILENT_CLIP_DB,
        "energy_contour": "".join(contour_chars),
    }


def list_macos_avfoundation_audio_devices() -> list[tuple[str, str]]:
    """List AVFoundation audio device (index, name) tuples via ffmpeg."""
    if not shutil.which("ffmpeg"):
        return []
    res = subprocess.run(
        ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True,
        text=True,
        timeout=3,
    )
    devices: list[tuple[str, str]] = []
    in_audio = False
    for line in (res.stderr or "").splitlines():
        if "AVFoundation audio devices:" in line:
            in_audio = True
            continue
        if "AVFoundation video devices:" in line:
            in_audio = False
            continue
        if in_audio:
            m = re.search(r"\[(\d+)\]\s+(.+)$", line)
            if m:
                devices.append((m.group(1), m.group(2).strip()))
    return devices


def get_macos_sysaudio_binary(cache_dir: Path) -> Path | None:
    """Compile (and cache) macos_sysaudio.swift into cache_dir/bin/macos_sysaudio."""
    swift_src = Path(__file__).resolve().parent / "macos_sysaudio.swift"
    if not swift_src.exists() or not shutil.which("swiftc"):
        return None
    bin_path = cache_dir / "macos_sysaudio"
    if bin_path.exists() and bin_path.stat().st_mtime >= swift_src.stat().st_mtime:
        return bin_path
    cache_dir.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        [
            "swiftc",
            "-O",
            str(swift_src),
            "-o",
            str(bin_path),
            "-framework",
            "CoreAudio",
            "-framework",
            "AudioToolbox",
            "-framework",
            "Foundation",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if res.returncode == 0 and bin_path.exists():
        return bin_path
    return None


def _record_avfoundation_wav(device_idx: str, seconds: float, wav_path: Path) -> tuple[bool, str]:
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg not found"
    res = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "avfoundation",
            "-i",
            f":{device_idx}",
            "-t",
            f"{seconds:.2f}",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ],
        capture_output=True,
        text=True,
        timeout=max(10, int(seconds) + 8),
    )
    if res.returncode != 0 or not wav_path.exists():
        return False, (res.stderr or "ffmpeg failed").strip()[-300:]
    return True, ""


def _record_sysaudio_wav(bin_path: Path, seconds: float, wav_path: Path) -> tuple[bool, str]:
    res = subprocess.run(
        [str(bin_path), str(wav_path), f"{seconds:.2f}"],
        capture_output=True,
        text=True,
        timeout=max(10, int(seconds) + 8),
    )
    if res.returncode != 0 or not wav_path.exists():
        return False, (res.stderr or "macos_sysaudio failed").strip()[-300:]
    return True, ""


def _encode_mp3_and_prune(wav_path: Path, mp3_path: Path) -> tuple[Path, bool]:
    """Encode WAV to 128kbps MP3 if ffmpeg is available; keep last MAX_STORED_CLIPS clips."""
    clips_dir = mp3_path.parent
    clips = sorted(clips_dir.glob("ear_*.mp3"), key=lambda p: p.stat().st_mtime)
    for old in clips[: max(0, len(clips) - (MAX_STORED_CLIPS - 1))]:
        try:
            old.unlink()
        except Exception:
            pass

    if not shutil.which("ffmpeg"):
        return wav_path, False

    res = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if res.returncode == 0 and mp3_path.exists():
        try:
            wav_path.unlink()
        except Exception:
            pass
        return mp3_path, True
    return wav_path, False


def _find_device(devices: list[tuple[str, str]], keywords: tuple[str, ...]) -> tuple[str, str] | None:
    for idx, name in devices:
        low = name.lower()
        if any(k in low for k in keywords):
            return idx, name
    return None


def _capture_macos(seconds: float, source: str, out_dir: Path, cache_dir: Path) -> dict[str, Any]:
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    wav_path = clips_dir / f"ear_{ts}.wav"
    mp3_path = clips_dir / f"ear_{ts}.mp3"

    devices = list_macos_avfoundation_audio_devices()
    loopback_dev = _find_device(devices, ("blackhole", "loopback", "soundflower"))
    mic_dev = _find_device(devices, ("macbook", "microphone", "built-in"))
    if mic_dev is None and devices:
        mic_dev = devices[0]

    used_source = source
    ok = False
    err = ""

    if source == "blackhole":
        if loopback_dev is None:
            return {"ok": False, "error": "No BlackHole/Loopback audio device found."}
        used_source = f"blackhole ({loopback_dev[1]})"
        ok, err = _record_avfoundation_wav(loopback_dev[0], seconds, wav_path)

    elif source == "mic":
        if mic_dev is None:
            return {"ok": False, "error": "No microphone device found."}
        used_source = f"mic ({mic_dev[1]})"
        ok, err = _record_avfoundation_wav(mic_dev[0], seconds, wav_path)

    elif source == "sysaudio":
        bin_path = get_macos_sysaudio_binary(cache_dir)
        if bin_path is None:
            return {"ok": False, "error": "Failed to build/locate macos_sysaudio helper (requires swiftc on macOS 14.2+)."}
        used_source = "macos_sysaudio (CoreAudio Process Tap)"
        ok, err = _record_sysaudio_wav(bin_path, seconds, wav_path)

    else:  # auto
        # 1) Probe virtual loopback first if present
        if loopback_dev is not None:
            probe_wav = clips_dir / f"_probe_{ts}.wav"
            p_ok, _ = _record_avfoundation_wav(loopback_dev[0], 0.7, probe_wav)
            if p_ok and probe_wav.exists():
                probe_metrics = analyze_wav(probe_wav)
                try:
                    probe_wav.unlink()
                except Exception:
                    pass
                if not probe_metrics.get("is_silent"):
                    used_source = f"blackhole ({loopback_dev[1]})"
                    ok, err = _record_avfoundation_wav(loopback_dev[0], seconds, wav_path)

        # 2) Fallback to native macOS CoreAudio Process Tap
        if not ok:
            bin_path = get_macos_sysaudio_binary(cache_dir)
            if bin_path is not None:
                used_source = "macos_sysaudio (CoreAudio Process Tap)"
                ok, err = _record_sysaudio_wav(bin_path, seconds, wav_path)
            elif not err:
                err = "No active virtual loopback audio and swiftc/macos_sysaudio unavailable."

    if not ok:
        return {
            "ok": False,
            "platform": "macos",
            "source": used_source,
            "error": err,
            "hint": "Grant System Audio Recording permission in macOS System Settings -> Privacy & Security -> Screen & System Audio Recording, or try --source mic.",
        }

    metrics = analyze_wav(wav_path)
    clip_path, is_mp3 = _encode_mp3_and_prune(wav_path, mp3_path)

    res: dict[str, Any] = {
        "ok": True,
        "platform": "macos",
        "source": used_source,
        "format": "mp3" if is_mp3 else "wav",
        "clip_path": str(clip_path),
        "metrics": metrics,
    }
    if metrics.get("is_silent"):
        res["hint"] = (
            "Captured audio is silent (< -60 dB). Check macOS System Settings -> Privacy & Security -> "
            "Screen & System Audio Recording permission, ensure audio is playing on this Mac (not external Sonos/AirPlay), "
            "or try --source mic."
        )
    return res


def _capture_linux(seconds: float, source: str, out_dir: Path) -> dict[str, Any]:
    if not shutil.which("ffmpeg"):
        return {"ok": False, "platform": "linux", "error": "ffmpeg is required for audio capture."}
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    wav_path = clips_dir / f"ear_{ts}.wav"
    mp3_path = clips_dir / f"ear_{ts}.mp3"

    monitor_src = "default"
    if source != "mic" and shutil.which("pactl"):
        sink_res = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=2)
        sink = (sink_res.stdout or "").strip()
        if sink:
            monitor_src = f"{sink}.monitor"

    res = subprocess.run(
        ["ffmpeg", "-y", "-f", "pulse", "-i", monitor_src, "-t", f"{seconds:.2f}", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(wav_path)],
        capture_output=True,
        text=True,
        timeout=max(10, int(seconds) + 8),
    )
    if res.returncode != 0 or not wav_path.exists():
        return {"ok": False, "platform": "linux", "source": monitor_src, "error": (res.stderr or "ffmpeg pulse failed")[-300:]}

    metrics = analyze_wav(wav_path)
    clip_path, is_mp3 = _encode_mp3_and_prune(wav_path, mp3_path)
    return {
        "ok": True,
        "platform": "linux",
        "source": f"pulse ({monitor_src})",
        "format": "mp3" if is_mp3 else "wav",
        "clip_path": str(clip_path),
        "metrics": metrics,
    }


def _capture_windows(seconds: float, source: str, out_dir: Path) -> dict[str, Any]:
    try:
        import pyaudiowpatch as pyaudio  # type: ignore
    except ImportError:
        return {
            "ok": False,
            "platform": "windows",
            "error": "pyaudiowpatch is not installed.",
            "hint": "Install pyaudiowpatch (`pip install pyaudiowpatch`) for Windows WASAPI loopback capture.",
        }

    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    wav_path = clips_dir / f"ear_{ts}.wav"
    mp3_path = clips_dir / f"ear_{ts}.mp3"

    with pyaudio.PyAudio() as p:
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
        if not default_speakers.get("isLoopbackDevice"):
            for loopback in p.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    default_speakers = loopback
                    break
        rate = int(default_speakers["defaultSampleRate"])
        ch = int(default_speakers["maxInputChannels"])
        stream = p.open(format=pyaudio.paInt16, channels=ch, rate=rate, frames_per_buffer=1024, input=True, input_device_index=default_speakers["index"])
        frames = [stream.read(1024) for _ in range(0, int(rate / 1024 * seconds))]
        stream.stop_stream()
        stream.close()

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(frames))

    metrics = analyze_wav(wav_path)
    clip_path, is_mp3 = _encode_mp3_and_prune(wav_path, mp3_path)
    return {
        "ok": True,
        "platform": "windows",
        "source": f"wasapi ({default_speakers['name']})",
        "format": "mp3" if is_mp3 else "wav",
        "clip_path": str(clip_path),
        "metrics": metrics,
    }


def capture_audio(
    seconds: float = 12.0,
    source: str = "auto",
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Capture local playback audio clip and return acoustic metrics + clip path."""
    target_dir = out_dir or (Path.home() / ".cache" / "antigravity-spotify")
    sys_name = platform.system().lower()
    if sys_name == "darwin":
        return _capture_macos(seconds=seconds, source=source, out_dir=target_dir, cache_dir=target_dir)
    if sys_name == "linux":
        return _capture_linux(seconds=seconds, source=source, out_dir=target_dir)
    if sys_name == "windows":
        return _capture_windows(seconds=seconds, source=source, out_dir=target_dir)
    return {"ok": False, "platform": sys_name, "error": f"Unsupported platform: {sys_name}"}
