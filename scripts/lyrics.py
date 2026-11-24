#!/usr/bin/env python3
"""Zero-auth live synced lyrics (LRCLIB) with local disk caching."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "antigravity-spotify/0.2.0 (https://github.com/aphygo/antigravity-spotify)"

HIT_TTL_SEC = 30 * 24 * 60 * 60
MISS_TTL_SEC = 24 * 60 * 60

_LRC_TS_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_CLEAN_PATTERNS = [
    re.compile(r"\s*[-–—]\s*feat\.?\s+.*$", re.IGNORECASE),
    re.compile(r"\s*\(feat\.?\s+[^)]*\)", re.IGNORECASE),
    re.compile(r"\s*\(ft\.?\s+[^)]*\)", re.IGNORECASE),
    re.compile(r"\s*[-–—]\s*(?:remaster(?:ed)?|radio edit|acoustic|live|mono|stereo).*$", re.IGNORECASE),
    re.compile(r"\s*\([^)]*remaster[^)]*\)", re.IGNORECASE),
    re.compile(r"\s*\[[^\]]*\]"),
]


def clean_track_title(title: str) -> str:
    """Strip featured-artist and remaster suffixes for fallback lyrics lookup."""
    cleaned = title.strip()
    for pat in _CLEAN_PATTERNS:
        cleaned = pat.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_lrc(lrc_text: str) -> list[tuple[float, str]]:
    """Parse LRC text into sorted (seconds, lyric_line) tuples, skipping blank lines."""
    if not lrc_text:
        return []
    entries: list[tuple[float, str]] = []
    for raw_line in lrc_text.splitlines():
        matches = list(_LRC_TS_RE.finditer(raw_line))
        if not matches:
            continue
        text = _LRC_TS_RE.sub("", raw_line).strip()
        if not text:
            continue
        for m in matches:
            mins = int(m.group(1))
            secs = int(m.group(2))
            frac_str = m.group(3) or "0"
            frac = int(frac_str) / (1000.0 if len(frac_str) == 3 else 100.0 if len(frac_str) == 2 else 10.0)
            ts = mins * 60.0 + secs + frac
            entries.append((ts, text))
    entries.sort(key=lambda item: item[0])
    return entries


def _fmt_ts(sec: float) -> str:
    total = max(0, int(sec))
    return f"{total // 60:02d}:{total % 60:02d}"


def slice_synced_lines(
    lines: list[tuple[float, str]],
    position_sec: float,
    before: int = 1,
    after: int = 2,
) -> dict[str, Any]:
    """Find the active line at position_sec and return a compact surrounding window."""
    if not lines:
        return {"current": "", "window": []}
    active_idx = -1
    for i, (ts, _text) in enumerate(lines):
        if ts <= position_sec + 0.5:
            active_idx = i
        else:
            break
    if active_idx < 0:
        active_idx = 0
        has_started = False
    else:
        has_started = True

    start = max(0, active_idx - before)
    end = min(len(lines), active_idx + after + 1)
    window = [
        {
            "ts": round(lines[i][0], 2),
            "time": _fmt_ts(lines[i][0]),
            "text": lines[i][1],
            "active": has_started and (i == active_idx),
        }
        for i in range(start, end)
    ]
    current = lines[active_idx][1] if has_started else ""
    return {"current": current, "window": window}


def _cache_path(cache_dir: Path, artist: str, title: str, album: str) -> Path:
    key = f"{artist.strip().lower()}::{title.strip().lower()}::{album.strip().lower()}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return cache_dir / "lyrics" / f"{digest}.json"


def _http_get_json(url: str, timeout: float) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, None


def _normalize_lrclib_record(rec: dict[str, Any], cached: bool = False) -> dict[str, Any]:
    synced = (rec.get("syncedLyrics") or "").strip()
    plain = (rec.get("plainLyrics") or "").strip()
    instrumental = bool(rec.get("instrumental"))
    found = bool(instrumental or synced or plain)
    return {
        "found": found,
        "instrumental": instrumental,
        "has_synced": bool(synced),
        "synced_lyrics": synced,
        "plain_lyrics": plain,
        "source": "lrclib",
        "cached": cached,
    }


def _query_lrclib_get(artist: str, title: str, album: str, duration_sec: float, timeout: float) -> dict[str, Any] | None:
    params: dict[str, str] = {"artist_name": artist, "track_name": title}
    if album:
        params["album_name"] = album
    if duration_sec > 0:
        params["duration"] = str(int(round(duration_sec)))
    url = f"{LRCLIB_BASE}/get?{urllib.parse.urlencode(params)}"
    status, data = _http_get_json(url, timeout=timeout)
    if status == 200 and isinstance(data, dict):
        return data
    return None


def _query_lrclib_search(artist: str, title: str, duration_sec: float, timeout: float) -> dict[str, Any] | None:
    params = {"artist_name": artist, "track_name": title}
    url = f"{LRCLIB_BASE}/search?{urllib.parse.urlencode(params)}"
    status, data = _http_get_json(url, timeout=timeout)
    if status != 200 or not isinstance(data, list) or not data:
        return None

    def _score(item: dict[str, Any]) -> tuple[int, float]:
        has_sync = 0 if (item.get("syncedLyrics") or "").strip() else 1
        dur = float(item.get("duration") or 0.0)
        diff = abs(dur - duration_sec) if (duration_sec > 0 and dur > 0) else 0.0
        return (has_sync, diff)

    best = min((d for d in data if isinstance(d, dict)), key=_score, default=None)
    return best


def fetch_lyrics(
    artist: str,
    title: str,
    album: str = "",
    duration_sec: float = 0.0,
    cache_dir: Path | None = None,
    timeout: float = 2.5,
    offline: bool = False,
) -> dict[str, Any]:
    """Fetch synced or plain lyrics from LRCLIB with local disk cache."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    album = (album or "").strip()
    if not artist or not title:
        return {"found": False, "instrumental": False, "has_synced": False, "synced_lyrics": "", "plain_lyrics": "", "source": "lrclib", "cached": False}

    cfile = _cache_path(cache_dir, artist, title, album) if cache_dir is not None else None
    now = time.time()

    if cfile is not None and cfile.exists():
        try:
            cached_obj = json.loads(cfile.read_text(encoding="utf-8"))
            ttl = HIT_TTL_SEC if cached_obj.get("found") else MISS_TTL_SEC
            if (now - float(cached_obj.get("_cached_at", 0))) < ttl:
                result = dict(cached_obj)
                result.pop("_cached_at", None)
                result["cached"] = True
                return result
        except Exception:
            pass

    if offline:
        return {
            "found": False,
            "instrumental": False,
            "has_synced": False,
            "synced_lyrics": "",
            "plain_lyrics": "",
            "source": "lrclib",
            "cached": False,
            "offline": True,
        }

    result: dict[str, Any] = {
        "found": False,
        "instrumental": False,
        "has_synced": False,
        "synced_lyrics": "",
        "plain_lyrics": "",
        "source": "lrclib",
        "cached": False,
    }
    try:
        rec = _query_lrclib_get(artist, title, album, duration_sec, timeout)
        if rec is None:
            cleaned = clean_track_title(title)
            if cleaned and cleaned.lower() != title.lower():
                rec = _query_lrclib_get(artist, cleaned, "", duration_sec, timeout)
            if rec is None:
                rec = _query_lrclib_search(artist, cleaned or title, duration_sec, timeout)
        if rec is not None:
            result = _normalize_lrclib_record(rec, cached=False)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    if cfile is not None:
        try:
            cfile.parent.mkdir(parents=True, exist_ok=True)
            to_save = dict(result)
            to_save["_cached_at"] = now
            tmp = cfile.with_suffix(".tmp")
            tmp.write_text(json.dumps(to_save, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(cfile)
        except Exception:
            pass

    return result
