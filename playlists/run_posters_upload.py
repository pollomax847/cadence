#!/usr/bin/env python3
"""Run poster generation and upload posters to Plex using environment PLEX_TOKEN/PLEX_URL.

This script constructs a `PlexAmpAutoPlaylist` instance and calls
`generate_playlist_posters()` which uploads posters via Plex HTTP API.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

try:
    import auto_playlists_plexamp as app
except Exception as e:
    print('Import failed:', e)
    raise


def main():
    plex_db = os.getenv('PLEX_DB', '/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db')
    # Create generator; it will only use Plex HTTP API for posters
    gen = app.PlexAmpAutoPlaylist(plex_db, verbose=True)

    # Ensure poster style loaded (fallback to default if needed)
    try:
        gen.poster_style = gen._load_poster_style() or gen.DEFAULT_POSTER_STYLE
    except Exception:
        gen.poster_style = gen.DEFAULT_POSTER_STYLE

    # Run generation+upload
    gen.generate_playlist_posters()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
