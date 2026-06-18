#!/usr/bin/env python3
"""Diagnostique des playlists générées : détecte doublons (titre+artiste) et exporte CSV."""
from __future__ import annotations

import csv
import os
import unicodedata
import re
from pathlib import Path
from typing import Dict, List

from auto_playlists_plexamp import PlexAmpAutoPlaylist


def _slug(s: str) -> str:
    s = (s or '').strip().lower()
    s = unicodedata.normalize('NFKD', s)
    s = re.sub(r"[^a-z0-9]+", ' ', s)
    return ' '.join(s.split())


def ensure_reports_dir() -> Path:
    p = Path(__file__).parent / 'reports'
    p.mkdir(parents=True, exist_ok=True)
    return p


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
    raise RuntimeError('Plex DB introuvable; définissez PLEX_DB_PATH env ou montez le DB attendu')


def diagnose_all(output_dir: Path) -> None:
    plex_db = find_plex_db()
    ap = PlexAmpAutoPlaylist(plex_db_path=plex_db)
    print("Chargement des pistes depuis la DB (peut prendre un moment)...")
    tracks = ap.get_track_data()
    print(f"{len(tracks)} pistes chargées depuis la DB.")

    all_playlists = ap._build_all_playlists(tracks)
    summary_rows = []

    for name, entries in sorted(all_playlists.items()):
        safe = re.sub(r"[^A-Za-z0-9 _-]", '', name)[:120].strip().replace(' ', '_')
        out_csv = output_dir / f"{safe}_duplicates.csv"

        key_map: Dict[str, List[Dict]] = {}
        for t in entries:
            k = _slug(f"{t.get('title','')}|{t.get('artist','')}")
            key_map.setdefault(k, []).append(t)

        duplicates = {k: v for k, v in key_map.items() if len(v) > 1}
        total = len(entries)
        dup_count = sum(len(v) for v in duplicates.values())

        with out_csv.open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['playlist', 'duplicate_group_id', 'count', 'track_id', 'title', 'artist', 'album', 'duration_s'])
            gid = 0
            for k, group in duplicates.items():
                gid += 1
                for t in group:
                    writer.writerow([name, gid, len(group), t.get('id'), t.get('title'), t.get('artist'), t.get('album'), int((t.get('duration_ms') or 0) / 1000)])

        summary_rows.append((name, total, dup_count, out_csv))
        print(f"Playlist: {name} — total={total}, duplicates_items={dup_count} (report: {out_csv})")

    # Global summary
    summary_path = output_dir / 'playlists_duplicates_summary.csv'
    with summary_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['playlist', 'total_tracks', 'duplicate_items', 'report_path'])
        for row in summary_rows:
            writer.writerow([row[0], row[1], row[2], str(row[3])])

    print(f"Résumé exporté: {summary_path}")


if __name__ == '__main__':
    out = ensure_reports_dir()
    diagnose_all(out)
