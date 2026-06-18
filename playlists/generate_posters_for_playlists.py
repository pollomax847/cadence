#!/usr/bin/env python3
"""Generate playlist posters using `auto_playlists_plexamp` logic for all playlists.

This script will call the poster generation routine for each playlist found in Plex (audio playlists).
Requires network access to Plex API and fonts installed used by the poster generator.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

try:
    import auto_playlists_plexamp as app  # noqa: E402
except Exception as e:
    print('auto_playlists_plexamp import failed:', e)
    raise

def main():
    inst = app.PlexAmpAutoPlaylist.__new__(app.PlexAmpAutoPlaylist)
    inst.logger = None
    inst.poster_style = inst._normalize_style(inst._default_poster_style())
    inst.POSTER_THEMES = []
    try:
        inst.generate_playlist_posters()
    except Exception as e:
        print('Poster generation failed:', e)
        return 1
    print('Poster generation finished — check poster_samples/ or Plex posters upload')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
