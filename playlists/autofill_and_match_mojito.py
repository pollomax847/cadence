#!/usr/bin/env python3
from pathlib import Path
import csv
import re
import sys
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from import_csv_to_plex import read_csv, copy_db, build_track_index, match_track, DEFAULT_PLEX_DB
import json

in_csv = Path('/tmp/Mojito_Sunset.cleaned.csv')
out_csv = Path('/tmp/Mojito_Sunset.cleaned.autofill.csv')

rows = []
with in_csv.open(encoding='utf-8') as fh:
    reader = csv.DictReader(fh)
    for r in reader:
        rows.append(r)

# heuristics to extract artist from title when artist missing
def extract_artist(title):
    t = title.strip()
    # common separators
    sep_match = re.match(r"^\s*(?P<artist>[^-–—|:]{1,60})\s*[-–—|:]\s*(?P<title>.+)$", t)
    if sep_match:
        return sep_match.group('artist').strip(), sep_match.group('title').strip()
    # vs or vs.
    vs_match = re.match(r"^(?P<a>[^vV]+)\s+vs\.?\s+(?P<b>.+)$", t)
    if vs_match:
        return vs_match.group('a').strip(), vs_match.group('b').strip()
    # feat/ft inside title
    feat_match = re.match(r"^(?P<artist>.+?)\s+(?:ft\.|feat\.|featuring)\s+(?P<rest>.+)$", t, re.I)
    if feat_match and len(feat_match.group('artist'))<=60:
        # leave title unchanged, artist extracted
        return feat_match.group('artist').strip(), t
    # Joe Goddard Feat. Mara Carlyle "She Burns" -> extract before Feat
    feat_in = re.search(r"(?P<artist>.+?)\s+Feat\.|Feat\.|feat\.", t)
    if feat_in:
        # fallback: split on first ' feat'
        parts = re.split(r"\s+feat(?:\.|\s)" , t, flags=re.I)
        if len(parts)>=2 and len(parts[0])<=60:
            return parts[0].strip(), t
    return None, t

changed = 0
for r in rows:
    if r.get('artist','').strip():
        continue
    artist, new_title = extract_artist(r['title'])
    if artist:
        r['artist'] = artist
        r['title'] = new_title
        changed += 1

with out_csv.open('w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=['title','artist','album','url'])
    w.writeheader()
    for r in rows:
        w.writerow({'title': r.get('title',''), 'artist': r.get('artist',''), 'album': r.get('album',''), 'url': r.get('url','')})

print('Auto-filled artists:', changed)

# run matching against Plex DB snapshot
snapshots = sorted(Path('/tmp').glob('plex_csv_import_*.db'), key=lambda p: p.stat().st_mtime, reverse=True)
if not snapshots:
    print('No plex snapshot found in /tmp')
    sys.exit(1)
db = snapshots[0]
print('Using snapshot:', db)

ta_index, t_index, candidates = build_track_index(db)

matched=[]
missed=[]
for r in rows:
    tid = match_track(r['title'], r.get('artist',''), ta_index, t_index, candidates)
    if tid:
        matched.append(tid)
    else:
        missed.append({'title': r['title'], 'artist': r.get('artist','')})

out = {'matched_count': len(matched), 'total': len(rows), 'matched_ids': matched, 'missed_count': len(missed), 'missed': missed}
Path('/tmp/mojito_matched_autofill.json').write_text(json.dumps(out, indent=2, ensure_ascii=False))
print('Wrote /tmp/mojito_matched_autofill.json')
