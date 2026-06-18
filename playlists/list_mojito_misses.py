#!/usr/bin/env python3
from pathlib import Path
import sys, glob
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from import_csv_to_plex import read_csv, build_track_index, match_track
import json

# find latest plex snapshot
snapshots = sorted(glob.glob('/tmp/plex_csv_import_*.db'), key=lambda p: Path(p).stat().st_mtime, reverse=True)
if not snapshots:
    print('No plex snapshot found in /tmp')
    sys.exit(1)
db = snapshots[0]
print('Using snapshot:', db)

tracks = read_csv(Path('/mnt/MyBook/itunes/Mojito Sunset.csv'))
ta_index, t_index, candidates = build_track_index(db)
missed = []
for tr in tracks:
    tid = match_track(tr['title'], tr.get('artist',''), ta_index, t_index, candidates)
    if not tid:
        missed.append(tr)

out = {'missed_count': len(missed), 'missed': missed}
Path('/tmp/mojito_missed_details.json').write_text(json.dumps(out, indent=2, ensure_ascii=False))
print('Wrote /tmp/mojito_missed_details.json')
