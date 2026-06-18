#!/usr/bin/env python3
"""Process all CSVs in `playlists/`, match to Plex IDs, update manual_playlists.json and push to Plex.

Usage: run from repository root with `.venv` active. Requires `PLEX_TOKEN` in env to push.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from import_csv_to_plex import read_csv, copy_db, build_track_index, match_track, DEFAULT_PLEX_DB


PLAYLISTS_DIR = Path(__file__).parent
TMP_DIR = Path('/tmp')


def slug_name(p: Path) -> str:
    name = p.stem
    name = name.replace('_', ' ').strip()
    return name


def process_csv(csv_path: Path) -> dict:
    tracks = read_csv(csv_path)
    db_snap = copy_db(DEFAULT_PLEX_DB)
    ta_index, t_index, candidates = build_track_index(db_snap)
    matched_ids = []
    missed = []
    for tr in tracks:
        tid = match_track(tr['title'], tr.get('artist', ''), ta_index, t_index, candidates)
        if tid:
            matched_ids.append(tid)
        else:
            missed.append(tr)

    out = {
        'csv': str(csv_path),
        'name': slug_name(csv_path),
        'count': len(tracks),
        'matched': len(matched_ids),
        'missed': len(missed),
        'matched_ids': matched_ids,
    }
    tmpf = TMP_DIR / f"{csv_path.stem}_matched.json"
    tmpf.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Wrote {tmpf} — matched {out['matched']}/{out['count']}")
    return out


def main():
    csvs = sorted(PLAYLISTS_DIR.glob('*.csv'))
    if not csvs:
        print('No CSV files found in playlists/'); return 1

    summary = []
    for c in csvs:
        try:
            summary.append(process_csv(c))
        except Exception as e:
            print(f'Error processing {c}: {e}')

    # Update manual_playlists.json
    manual_path = PLAYLISTS_DIR / 'manual_playlists.json'
    if manual_path.exists():
        backup = manual_path.with_suffix('.json.backup')
        backup.write_text(manual_path.read_text(encoding='utf-8'), encoding='utf-8')
        print(f'Backed up {manual_path} -> {backup}')
        data = json.loads(manual_path.read_text(encoding='utf-8'))
    else:
        data = {'playlists': []}

    existing = {p['name']: p for p in data.get('playlists', [])}
    for s in summary:
        name = s['name']
        ids = s['matched_ids']
        entry = {'name': name, 'ids': ids}
        existing[name] = entry

    data['playlists'] = list(existing.values())
    manual_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Updated {manual_path} with {len(data["playlists"])} playlists')

    # Push to Plex if token present
    token = os.environ.get('PLEX_TOKEN')
    if token:
        print('PLEX_TOKEN found — pushing to Plex via apply_manual_playlists.py')
        os.system(".venv/bin/python3 playlists/apply_manual_playlists.py")
    else:
        print('PLEX_TOKEN not set — skipping Plex push')

    # Write summary
    out = PLAYLISTS_DIR / 'process_all_csvs_summary.json'
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote summary to {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
