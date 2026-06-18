#!/usr/bin/env python3
from pathlib import Path
import sys
# Ensure local modules in playlists/ resolve as top-level imports
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from import_csv_to_plex import read_csv, copy_db, build_track_index, match_track, DEFAULT_PLEX_DB

csv = Path('/mnt/MyBook/itunes/Mojito Sunset.csv')
print('CSV exists:', csv.exists())
tracks = read_csv(csv)
print('Tracks in CSV:', len(tracks))

# create DB snapshot
db_copy = copy_db(DEFAULT_PLEX_DB)
print('Using DB snapshot:', db_copy)

# build index
ta_index, t_index, candidates = build_track_index(db_copy)

matched = []
missed = []
for tr in tracks:
    tid = match_track(tr['title'], tr.get('artist',''), ta_index, t_index, candidates)
    if tid:
        matched.append((tr['title'], tr.get('artist',''), tid))
    else:
        missed.append((tr['title'], tr.get('artist','')))

print(f"Matched {len(matched)} / {len(tracks)}")
print('\nMatched sample:')
for i, m in enumerate(matched[:20]):
    print(i+1, m)
print('\nMissed sample:')
for i, m in enumerate(missed[:20]):
    print(i+1, m)
