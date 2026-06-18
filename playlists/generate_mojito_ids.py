#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from import_csv_to_plex import read_csv, copy_db, build_track_index, match_track, DEFAULT_PLEX_DB
import json

csv = Path('/mnt/MyBook/itunes/Mojito Sunset.csv')
tracks = read_csv(csv)

db_copy = copy_db(DEFAULT_PLEX_DB)

ta_index, t_index, candidates = build_track_index(db_copy)

matched_ids = []
missed = []
for tr in tracks:
    tid = match_track(tr['title'], tr.get('artist',''), ta_index, t_index, candidates)
    if tid:
        matched_ids.append(tid)
    else:
        missed.append(tr)

out = {
    'matched_count': len(matched_ids),
    'total': len(tracks),
    'matched_ids': matched_ids,
    'missed_count': len(missed)
}
Path('/tmp/mojito_matched.json').write_text(json.dumps(out, indent=2))
print('Wrote /tmp/mojito_matched.json')
