#!/usr/bin/env python3
"""Standardize running playlists: create Spotify/Deezer-like titles and replace old playlists.

Behavior:
 - Find audio playlists whose title contains 'run' or 'running' (case-insensitive)
 - For each: fetch its track IDs, compute a cleaned title, create a new playlist with an emoji prefix,
   and delete the old playlist rating key.

Requires `PLEX_TOKEN` in environment.
"""
from __future__ import annotations
import os
import re
import xml.etree.ElementTree as ET
from plex_api import (
    default_plex_url,
    plex_list_audio_playlists,
    plex_get_playlist_track_ids,
    plex_create_audio_playlist,
    plex_delete_playlist,
    plex_machine_identifier,
)


def clean_title(title: str) -> str:
    # remove counts like '(123 titres)', remove '[fusion]' tags, strip whitespace
    t = re.sub(r"\([^)]*titres[^)]*\)", "", title, flags=re.I)
    t = re.sub(r"\[.*?\]", "", t)
    t = re.sub(r"\s{2,}", " ", t)
    t = t.strip(' -–—')
    return t


def new_title_for(old: str) -> str:
    base = clean_title(old)
    # replace ampersand with ' & ' normalized to '—' for Spotify-like
    base = re.sub(r"\s*&\s*", " — ", base)
    # ensure capitalized style
    return f"🏃 {base}"


def main() -> int:
    token = os.environ.get("PLEX_TOKEN")
    if not token:
        print("ERROR: PLEX_TOKEN not set in environment")
        return 2
    plex_url = os.environ.get("PLEX_URL") or default_plex_url()

    playlists = plex_list_audio_playlists(plex_url, token)
    running = [p for p in playlists if "run" in p["title"].lower()]
    if not running:
        print("No running playlists found")
        return 0

    machine_id = plex_machine_identifier(plex_url, token)
    print(f"Found {len(running)} running playlists")
    for p in running:
        old_title = p["title"]
        old_rating_key = p["rating_key"]
        print(f"Processing: {old_title} (ratingKey={old_rating_key})")
        track_ids = plex_get_playlist_track_ids(plex_url, token, old_rating_key)
        if not track_ids:
            print("  ⚠ playlist empty — skipping")
            continue
        new_title = new_title_for(old_title)
        print(f"  → Creating new playlist: {new_title} ({len(track_ids)} tracks)")
        # create (replace True will remove any existing with same new_title)
        new_key = plex_create_audio_playlist(plex_url, token, new_title, track_ids, machine_id=machine_id, replace=True)
        print(f"  ✓ created ratingKey={new_key}")
        # delete old
        try:
            if old_rating_key != new_key:
                plex_delete_playlist(plex_url, token, old_rating_key)
                print(f"  🗑 deleted old playlist ratingKey={old_rating_key}")
        except Exception as e:
            print(f"  ⚠ failed to delete old playlist: {e}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
