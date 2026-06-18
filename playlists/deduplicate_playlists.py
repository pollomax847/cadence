#!/usr/bin/env python3
"""Déduplique toutes les playlists générées et exporte M3U dédupliqués.
Option: pour remplacer les playlists sur Plex, exporter `REPLACE_IN_PLEX=1`.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Dict, List

from auto_playlists_plexamp import PlexAmpAutoPlaylist
import plex_api


def _slug(s: str) -> str:
    s = (s or '').strip().lower()
    s = unicodedata.normalize('NFKD', s)
    s = re.sub(r"[^a-z0-9]+", ' ', s)
    return ' '.join(s.split())


def find_plex_db() -> str:
    env = os.getenv('PLEX_DB_PATH') or os.getenv('PLEX_DB')
    candidates = []
    if env:
        candidates.append(env)
    candidates += [
        '/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db',
        '/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db',
        '/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db',
        str(Path.home() / '.config' / 'Plex Media Server' / 'Plug-in Support' / 'Databases' / 'com.plexapp.plugins.library.db'),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise RuntimeError('Plex DB introuvable; définissez PLEX_DB_PATH env')


def safe_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 _-]", '', name)
    s = s.strip().replace(' ', '_')
    return s[:120]


def main():
    out_dir = Path(__file__).parent / 'reports' / 'deduped'
    out_dir.mkdir(parents=True, exist_ok=True)

    plex_db = find_plex_db()
    ap = PlexAmpAutoPlaylist(plex_db_path=plex_db)
    print("Chargement des pistes depuis la DB...")
    tracks = ap.get_track_data()
    all_playlists = ap._build_all_playlists(tracks)

    replace_in_plex = os.getenv('REPLACE_IN_PLEX', '') in ('1', 'true', 'yes')
    plex_url = os.getenv('PLEX_URL', plex_api.default_plex_url()).rstrip('/')
    plex_token = os.getenv('PLEX_TOKEN', '')
    plex_machine_id = os.getenv('PLEX_MACHINE_ID', None)

    report_summary = []

    for name, entries in sorted(all_playlists.items()):
        key_map: Dict[str, List[Dict]] = {}
        deduped = []
        seen = set()
        for t in entries:
            k = _slug(f"{t.get('title','')}|{t.get('artist','')}")
            if k in seen:
                continue
            seen.add(k)
            deduped.append(t)

        removed = len(entries) - len(deduped)
        safe = safe_name(name)
        m3u_path = out_dir / f"{safe}_deduped.m3u"
        with m3u_path.open('w', encoding='utf-8') as f:
            f.write('#EXTM3U\n')
            for t in deduped:
                duration = int((t.get('duration_ms') or 0) / 1000)
                artist = t.get('artist') or ''
                title = t.get('title') or ''
                f.write(f"#EXTINF:{duration},{artist} - {title}\n")
                f.write(str(t.get('file_path') or '') + '\n')

        print(f"Playlist: {name} — total={len(entries)}, deduped={len(deduped)}, removed={removed}, m3u={m3u_path}")

        if replace_in_plex:
            if not plex_token:
                print("REPLACE_IN_PLEX demandé mais PLEX_TOKEN absent — saut")
            else:
                track_ids = [int(t.get('id')) for t in deduped if t.get('id')]
                try:
                    rk = plex_api.plex_create_audio_playlist(plex_url, plex_token, name, track_ids, machine_id=plex_machine_id, replace=True)
                    print(f"Remplacée sur Plex: ratingKey={rk}")
                except Exception as e:
                    print(f"Erreur remplacement Plex pour {name}: {e}")

        report_summary.append((name, len(entries), len(deduped), removed, str(m3u_path)))

    # write summary
    summary_path = out_dir / 'dedup_summary.csv'
    import csv
    with summary_path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['playlist', 'total', 'deduped', 'removed', 'm3u'])
        for r in report_summary:
            w.writerow(r)

    print(f"Résumé exporté: {summary_path}")


if __name__ == '__main__':
    main()
