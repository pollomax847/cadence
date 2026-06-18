#!/usr/bin/env python3
"""Apply playlist replacements to the bank by computing removed track IDs
and adding them to `excluded_tracks.json`. Also export CSVs of removed tracks.

Usage: ./make_bank_changes.py --results decade_apply_results.json --plex-url http://localhost:32400 --plex-token TOKEN
"""
import argparse
import json
import sqlite3
import csv
from pathlib import Path
import os
import plex_api


DEFAULT_DB = '/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db'


def load_results(path: Path):
	return json.loads(path.read_text(encoding='utf-8'))


def get_playlist_track_ids_from_db(db_path: str, playlist_metadata_id: int):
	conn = sqlite3.connect(db_path)
	conn.text_factory = lambda b: b.decode('utf-8', 'replace')
	cur = conn.cursor()
	rows = cur.execute('''SELECT pqg.metadata_item_id FROM play_queue_generators pqg
		WHERE pqg.playlist_id = ? AND pqg.metadata_item_id IS NOT NULL''', (playlist_metadata_id,)).fetchall()
	conn.close()
	return [int(r[0]) for r in rows]


def fetch_track_metadata(db_path: str, ids: list):
	if not ids:
		return {}
	conn = sqlite3.connect(db_path)
	conn.text_factory = lambda b: b.decode('utf-8', 'replace')
	cur = conn.cursor()
	q = f"SELECT mi.id, mi.title, mi.year, albums.title as album, artists.title as artist, GROUP_CONCAT(DISTINCT genres.tag) as genres FROM metadata_items mi LEFT JOIN metadata_items albums ON mi.parent_id = albums.id LEFT JOIN metadata_items artists ON albums.parent_id = artists.id LEFT JOIN taggings ON mi.id = taggings.metadata_item_id LEFT JOIN tags genres ON taggings.tag_id = genres.id AND genres.tag_type = 1 WHERE mi.id IN ({','.join('?' for _ in ids)}) GROUP BY mi.id"
	rows = cur.execute(q, ids).fetchall()
	conn.close()
	out = {}
	for r in rows:
		out[int(r[0])] = {'title': r[1], 'year': r[2], 'album': r[3], 'artist': r[4], 'genres': r[5]}
	return out


def safe_filename(s: str) -> str:
	import re
	s = re.sub(r'[^0-9a-zA-Z_\- ]+', '', s)
	s = s.strip().replace(' ', '_')
	return s or 'playlist'


def main():
	p = argparse.ArgumentParser()
	p.add_argument('--results', required=True, help='JSON file with apply results (decade_apply_results.json)')
	p.add_argument('--plex-url', default=os.getenv('PLEX_URL', 'http://localhost:32400'))
	p.add_argument('--plex-token', default=os.getenv('PLEX_TOKEN'))
	p.add_argument('--db', default=os.getenv('PLEX_DB_PATH', DEFAULT_DB))
	p.add_argument('--out-dir', default=str(Path(__file__).parent))
	args = p.parse_args()

	if not args.plex_token:
		raise SystemExit('PLEX_TOKEN must be provided via --plex-token or PLEX_TOKEN env')

	results = load_results(Path(args.results))
	excluded_path = Path(args.out_dir) / 'excluded_tracks.json'
	existing = set()
	if excluded_path.exists():
		try:
			raw = json.loads(excluded_path.read_text(encoding='utf-8'))
			if isinstance(raw, list):
				existing = set(int(x) for x in raw)
		except Exception:
			existing = set()

	total_new_excluded = set()
	out_dir = Path(args.out_dir)
	out_dir.mkdir(parents=True, exist_ok=True)

	for entry in results:
		res = entry.get('result') or {}
		if entry.get('action') == 'skipped' or not res:
			continue
		# old playlist metadata id is entry['id']
		old_meta_id = entry.get('id')
		new_rating_key = res.get('new_rating_key')
		if not old_meta_id or not new_rating_key:
			continue

		print(f'Processing playlist {old_meta_id} -> new ratingKey {new_rating_key}')

		orig_ids = set(get_playlist_track_ids_from_db(args.db, old_meta_id))

		# Try Plex API first, fall back to reading new playlist from local DB
		new_ids = set()
		try:
			if args.plex_token:
				new_ids = set(plex_api.plex_get_playlist_track_ids(args.plex_url, args.plex_token, str(new_rating_key)))
		except Exception as e:
			print(f'  Plex API fetch failed: {e} — falling back to DB lookup')

		if not new_ids:
			# ratingKey is usually the metadata_items.id for the created playlist
			try:
				new_meta_id = int(str(new_rating_key))
				new_ids = set(get_playlist_track_ids_from_db(args.db, new_meta_id))
			except Exception:
				new_ids = set()

		removed = sorted(orig_ids - new_ids)
		if not removed:
			print('  no removed tracks')
			continue

		total_new_excluded.update(removed)

		# Write CSV of removed tracks
		meta = fetch_track_metadata(args.db, removed)
		title = entry.get('title') or f'playlist_{old_meta_id}'
		fname = out_dir / f"removed_tracks_{old_meta_id}_{safe_filename(title)}.csv"
		with open(fname, 'w', newline='', encoding='utf-8') as f:
			writer = csv.writer(f)
			writer.writerow(['id', 'artist', 'title', 'album', 'year', 'genres'])
			for tid in removed:
				m = meta.get(tid, {})
				writer.writerow([tid, m.get('artist') or '', m.get('title') or '', m.get('album') or '', m.get('year') or '', m.get('genres') or ''])
		print(f'  Wrote {fname} ({len(removed)} rows)')

	# Merge and write excluded_tracks.json
	final = sorted(set(existing) | total_new_excluded)
	excluded_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
	print(f'Updated {excluded_path} with {len(final)} excluded ids (added {len(total_new_excluded)})')


if __name__ == '__main__':
	main()

