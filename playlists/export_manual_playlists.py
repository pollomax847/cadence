#!/usr/bin/env python3
"""Export applied playlists into a custom JSON file that the generator will
load as persistent/manual playlists (exact track lists).

Usage: ./export_manual_playlists.py --results playlists/decade_apply_results.json --out playlists/manual_playlists.json
"""
import json
import sqlite3
from pathlib import Path
import argparse
import os

DEFAULT_DB = '/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db'


def get_playlist_track_ids_from_db(db_path: str, playlist_metadata_id: int):
    conn = sqlite3.connect(db_path)
    conn.text_factory = lambda b: b.decode('utf-8', 'replace')
    cur = conn.cursor()
    rows = cur.execute('''SELECT pqg.metadata_item_id FROM play_queue_generators pqg
        WHERE pqg.playlist_id = ? AND pqg.metadata_item_id IS NOT NULL''', (playlist_metadata_id,)).fetchall()
    conn.close()
    return [int(r[0]) for r in rows]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--results', required=True)
    p.add_argument('--out', default='playlists/manual_playlists.json')
    p.add_argument('--db', default=os.getenv('PLEX_DB_PATH', DEFAULT_DB))
    args = p.parse_args()

    results = json.loads(Path(args.results).read_text(encoding='utf-8'))
    out = {'playlists': []}
    for entry in results:
        res = entry.get('result') or {}
        if entry.get('action') == 'skipped' or not res:
            continue
        old_meta = entry.get('id')
        new_rating = res.get('new_rating_key')
        title = entry.get('title') or f'playlist_{old_meta}'
        # try to read the new playlist content from DB (new_rating is ratingKey/metadata id)
        try:
            new_meta_id = int(str(new_rating))
            ids = get_playlist_track_ids_from_db(args.db, new_meta_id)
        except Exception:
            ids = []
        if not ids:
            continue
        out['playlists'].append({'name': title, 'prefix_auto': False, 'ids': ids})

    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {args.out} with {len(out["playlists"])} playlists')


if __name__ == '__main__':
    main()
