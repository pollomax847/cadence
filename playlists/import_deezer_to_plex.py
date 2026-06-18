#!/usr/bin/env python3
"""Import a public Deezer playlist into Plex by matching title+artist.

This script fetches the Deezer playlist JSON (public playlists), writes a
temporary CSV and delegates matching/import to `import_csv_to_plex.py`.

Defaults to append/merge behavior (no overwrite) unless `--replace` is set.
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import tempfile
import urllib.request
import json
from pathlib import Path


DEEZER_API_PLAYLIST = "https://api.deezer.com/playlist/{id}"


def extract_id_from_url(u: str) -> str | None:
    # Expect URLs like https://www.deezer.com/playlist/123456789
    try:
        parts = u.rstrip("/\n ").split("/")
        return parts[-1] if parts and parts[-1].isdigit() else None
    except Exception:
        return None


def fetch_deezer_playlist(pid: str) -> dict:
    url = DEEZER_API_PLAYLIST.format(id=pid)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = resp.read()
    return json.loads(data)


def write_csv(tracks: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["title", "artist", "album"])
        for t in tracks:
            writer.writerow([t.get("title", ""), t.get("artist", ""), t.get("album", "")])


def main() -> int:
    p = argparse.ArgumentParser(description="Import a public Deezer playlist into Plex")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="Deezer playlist URL (public)")
    g.add_argument("--id", help="Deezer playlist numeric ID")
    p.add_argument("--playlist", help="Plex playlist name (defaults to Deezer playlist title)")
    p.add_argument("--plex-url", default="http://localhost:32400", help="Plex URL")
    p.add_argument("--plex-token", required=True, help="Plex token")
    p.add_argument("--replace", action="store_true", help="Replace existing playlist instead of merging/appending")
    p.add_argument("--dry-run", action="store_true", help="Dry run: don't write to Plex")
    p.add_argument("--missing-file", help="Write unmatched tracks to this file (path)")
    args = p.parse_args()

    pid = args.id or extract_id_from_url(args.url or "")
    if not pid:
        print("❌ Unable to determine Deezer playlist ID from input", file=sys.stderr)
        return 2

    try:
        data = fetch_deezer_playlist(pid)
    except Exception as e:
        print(f"❌ Failed to fetch Deezer playlist: {e}", file=sys.stderr)
        return 3

    title = args.playlist or data.get("title") or f"Deezer_{pid}"
    raw_tracks = data.get("tracks", {}).get("data", [])
    tracks = []
    for item in raw_tracks:
        track_title = item.get("title") or ""
        artist = ""
        album = ""
        if item.get("artist"):
            artist = item["artist"].get("name", "")
        if item.get("album"):
            album = item["album"].get("title", "")
        tracks.append({"title": track_title, "artist": artist, "album": album})

    if not tracks:
        print("⚠️  No tracks found in Deezer playlist", file=sys.stderr)
        return 4

    # write temp csv
    tf = Path(tempfile.mkstemp(prefix="deezer_playlist_", suffix=".csv")[1])
    try:
        write_csv(tracks, tf)

        # build command to call import_csv_to_plex.py
        cmd = [sys.executable, str(Path(__file__).parent / "import_csv_to_plex.py"), "--csv", str(tf), "--playlist", title, "--plex-url", args.plex_url, "--plex-token", args.plex_token]
        if args.dry_run:
            cmd.append("--dry-run")
        if args.missing_file:
            cmd.extend(["--missing-file", args.missing_file])
        # default: append (merge). If replace requested, do not pass --append and pass --replace to the underlying importer
        if args.replace:
            cmd.append("--replace")
        else:
            cmd.append("--append")

        print("Running importer:", " ".join(cmd))
        rc = subprocess.call(cmd)
        return rc
    finally:
        try:
            tf.unlink()
        except Exception:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
