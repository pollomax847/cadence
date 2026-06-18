#!/usr/bin/env python3
"""
slskd search + download helper.

Provides SlskdClient: search for a track by title+artist on Soulseek via
the slskd REST API, score results and enqueue the best file for download.

Environment variables (read automatically):
  SLSKD_URL      slskd base URL       (default: http://localhost:5030)
  SLSKD_API_KEY  slskd API key        (required unless passed explicitly)
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Optional

from text_normalization import normalize_ascii

# ─── defaults ────────────────────────────────────────────────────────────────

DEFAULT_SLSKD_URL = os.environ.get("SLSKD_URL", "http://localhost:5030")
DEFAULT_SLSKD_KEY = os.environ.get("SLSKD_API_KEY", "")

# Score thresholds
_EXT_SCORE = {
    ".flac": 100, ".wav": 80, ".m4a": 65, ".ogg": 60, ".aac": 55,
    ".mp3": 50, ".wma": 25, ".opus": 55,
}
_SEARCH_TIMEOUT_S = 30      # max seconds to wait for search completion
_POLL_INTERVAL_S  = 2       # polling interval
_FILE_LIMIT       = 25      # max files returned by slskd
_MIN_SCORE        = 30      # discard candidates below this score


# ─── data ─────────────────────────────────────────────────────────────────────

@dataclass
class SlskdFile:
    username: str
    filename: str        # full path as returned by slskd (Windows-style separators)
    size: int            # bytes
    sampleRate: int      # Hz (0 if unknown)
    bitDepth: int        # bits (0 if unknown)
    extension: str       # ".flac", ".mp3", etc. (derived from filename)
    isLocked: bool
    hasFreeUploadSlot: bool
    uploadSpeed: int     # bytes/s
    resp_token: int      # connection token from the parent response
    score: int = 0       # computed match score


@dataclass
class DownloadResult:
    queued: bool
    file: Optional[SlskdFile]
    error: Optional[str] = None


# ─── helpers ──────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Lowercase, NFKD→ASCII, alphanumeric only."""
    return normalize_ascii(s)


def _basename(path: str) -> str:
    """Extract filename (without extension) from a Windows or POSIX path string."""
    # slskd paths use backslashes
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def _ext(path: str) -> str:
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if "." in name:
        return "." + name.rsplit(".", 1)[-1].lower()
    return ""


def _score_file(f: SlskdFile, norm_title: str, norm_artist: str) -> int:
    """Score a candidate file. Higher is better. Returns 0 if unusable."""
    if f.isLocked:
        return 0

    score = 0

    # ── Format / quality ──────────────────────────────────────────────────
    ext = f.extension or _ext(f.filename)
    score += _EXT_SCORE.get(ext, 10)

    # Lossless bonus
    if ext in (".flac", ".wav"):
        if f.bitDepth >= 24:
            score += 15
        if f.sampleRate and f.sampleRate >= 88200:
            score += 5

    # ── Upload slot / speed ──────────────────────────────────────────────
    if f.hasFreeUploadSlot:
        score += 15
    # Up to +10 for speed (normalise to 10 MB/s)
    score += min(int(f.uploadSpeed / 1_000_000), 10)

    # ── Filename match ────────────────────────────────────────────────────
    bn = _norm(_basename(f.filename))
    if norm_title and norm_title in bn:
        score += 30
    if norm_artist and norm_artist in bn:
        score += 20

    return score


# ─── client ───────────────────────────────────────────────────────────────────

class SlskdClient:
    def __init__(self, url: str = DEFAULT_SLSKD_URL, api_key: str = DEFAULT_SLSKD_KEY):
        self.base = url.rstrip("/")
        self.api_key = api_key
        if not self.api_key:
            raise ValueError("SLSKD_API_KEY is required (set env var or pass api_key=)")

    # ── low-level ────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, body=None) -> dict:
        url = f"{self.base}/api/v0{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-API-Key", self.api_key)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read()
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")
            raise RuntimeError(f"slskd {method} {path} → HTTP {e.code}: {body_text}") from e

    # ── search ───────────────────────────────────────────────────────────

    def search(self, title: str, artist: str = "") -> list[SlskdFile]:
        """
        Search slskd for title+artist. Returns a scored, sorted list of
        candidate SlskdFile objects (best first). Cleans up the search when done.
        """
        query = f"{artist} {title}".strip() if artist else title
        payload = {
            "searchText": query,
            "fileLimit": _FILE_LIMIT,
            "filterResponses": False,
        }
        search_obj = self._request("POST", "/searches", payload)
        sid = search_obj.get("id")
        if not sid:
            return []

        # Poll for completion
        deadline = time.monotonic() + _SEARCH_TIMEOUT_S
        while time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL_S)
            result = self._request("GET", f"/searches/{sid}?includeResponses=true")
            if result.get("isComplete"):
                break

        # Collect files
        files: list[SlskdFile] = []
        norm_title  = _norm(title)
        norm_artist = _norm(artist)

        for resp in result.get("responses", []):
            username          = resp.get("username", "")
            upload_speed      = resp.get("uploadSpeed") or 0
            has_free_slot     = resp.get("hasFreeUploadSlot", False)
            resp_token        = resp.get("token", 0)

            for fdata in resp.get("files", []):
                fname = fdata.get("filename", "")
                if not fname:
                    continue
                ext = _ext(fname)
                sf = SlskdFile(
                    username=username,
                    filename=fname,
                    size=fdata.get("size", 0),
                    sampleRate=fdata.get("sampleRate") or 0,
                    bitDepth=fdata.get("bitDepth") or 0,
                    extension=ext,
                    isLocked=fdata.get("isLocked", False),
                    hasFreeUploadSlot=has_free_slot,
                    uploadSpeed=upload_speed,
                    resp_token=resp_token,
                )
                sf.score = _score_file(sf, norm_title, norm_artist)
                if sf.score >= _MIN_SCORE:
                    files.append(sf)

        # Sort best first
        files.sort(key=lambda f: f.score, reverse=True)

        # Cleanup
        try:
            self._request("DELETE", f"/searches/{sid}")
        except Exception:
            pass

        return files

    # ── download ─────────────────────────────────────────────────────────

    def download(self, f: SlskdFile) -> DownloadResult:
        """Enqueue a file for download in slskd."""
        body = [{"filename": f.filename, "size": f.size, "token": f.resp_token}]
        try:
            self._request("POST", f"/transfers/downloads/{urllib.parse.quote(f.username)}", body)
            return DownloadResult(queued=True, file=f)
        except RuntimeError as e:
            return DownloadResult(queued=False, file=f, error=str(e))

    # ── convenience ──────────────────────────────────────────────────────

    def search_and_download(
        self, title: str, artist: str = "", verbose: bool = True
    ) -> DownloadResult:
        """Search then download the best result. Returns a DownloadResult."""
        if verbose:
            label = f"{artist} — {title}" if artist else title
            print(f"  🔍 slskd: searching '{label}'…", flush=True)

        candidates = self.search(title, artist)
        if not candidates:
            if verbose:
                print("       no results.", flush=True)
            return DownloadResult(queued=False, file=None, error="no results")

        best = candidates[0]
        if verbose:
            bn = best.filename.replace("\\", "/").rsplit("/", 1)[-1]
            print(
                f"       best: {best.username}/{bn}"
                f"  [{best.extension or '?'}  {best.size//1024//1024}MB"
                f"  score={best.score}]",
                flush=True,
            )

        result = self.download(best)
        if verbose:
            if result.queued:
                print("       ✅ queued for download.", flush=True)
            else:
                print(f"       ❌ download failed: {result.error}", flush=True)

        return result


# ─── CLI (standalone use) ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, sys

    p = argparse.ArgumentParser(description="Search + download a track via slskd.")
    p.add_argument("--title",  required=True)
    p.add_argument("--artist", default="")
    p.add_argument("--url",    default=DEFAULT_SLSKD_URL)
    p.add_argument("--key",    default=DEFAULT_SLSKD_KEY)
    p.add_argument("--dry-run", action="store_true", help="Search only, don't download.")
    args = p.parse_args()

    if not args.key:
        sys.exit("❌  Provide --key or set SLSKD_API_KEY")

    client = SlskdClient(url=args.url, api_key=args.key)

    candidates = client.search(args.title, args.artist)
    if not candidates:
        print("No results found.")
        sys.exit(1)

    print(f"\nTop {min(5, len(candidates))} candidates:")
    for i, f in enumerate(candidates[:5], 1):
        bn = f.filename.replace("\\", "/").rsplit("/", 1)[-1]
        print(f"  {i}. [{f.score:3d}] {f.username}/{bn}  ({f.size//1024//1024}MB)")

    if not args.dry_run:
        result = client.download(candidates[0])
        if result.queued:
            print("\n✅  Download queued.")
        else:
            print(f"\n❌  Download failed: {result.error}")
            sys.exit(1)
