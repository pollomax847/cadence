#!/usr/bin/env python3
"""Apply `playlists/manual_playlists.json` to Plex using the API and PLEX_TOKEN env var.

Usage: ensure `PLEX_TOKEN` is exported in the environment, then run:
  .venv/bin/python3 playlists/apply_manual_playlists.py

This script will replace existing playlists with the same name.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from plex_api import default_plex_url
from plex_playlist_manager import PlexPlaylistManager, DEFAULT_PLEX_DB


def main() -> int:
    token = os.environ.get("PLEX_TOKEN")
    if not token:
        print("ERROR: PLEX_TOKEN not set in environment", file=sys.stderr)
        return 2

    plex_url = os.environ.get("PLEX_URL") or default_plex_url()
    manual_path = Path(__file__).parent / "manual_playlists.json"
    if not manual_path.exists():
        print(f"ERROR: {manual_path} not found", file=sys.stderr)
        return 3

    data = json.loads(manual_path.read_text(encoding="utf-8"))
    playlists = data.get("playlists") or []
    if not playlists:
        print("No playlists found in manual_playlists.json")
        return 0

    manager = PlexPlaylistManager(Path(DEFAULT_PLEX_DB), verbose=False)
    results = []
    for entry in playlists:
        name = entry.get("name")
        ids = entry.get("ids") or []
        if not name or not ids:
            results.append({"name": name, "status": "skipped", "reason": "missing name or ids"})
            continue
        try:
            print(f"Syncing playlist: {name} | tracks={len(ids)}")
            res = manager.sync_playlist_via_api(
                plex_url=plex_url, token=token, playlist_name=name, track_ids=ids, dry_run=False
            )
            results.append({"name": name, "status": "ok", "result": res})
        except Exception as exc:
            results.append({"name": name, "status": "error", "error": str(exc)})

    out_path = Path(__file__).parent / "apply_manual_playlists_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote results to {out_path} | playlists processed: {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
