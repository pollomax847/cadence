#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from import_csv_to_plex import read_csv, clean_title, split_artist_dash_title
import csv

in_csv = Path('/mnt/MyBook/itunes/Mojito Sunset.csv')
out_csv = Path('/tmp/Mojito_Sunset.cleaned.csv')
rows = read_csv(in_csv)

seen = set()
cleaned = []
missing_artist_before = sum(1 for r in rows if not r.get('artist'))
for r in rows:
    title = r['title']
    artist = r.get('artist','')
    # try split "Artist - Title"
    emb_artist, emb_title = split_artist_dash_title(title, artist)
    if emb_title and not artist:
        artist = emb_artist or ''
        title = emb_title
    # clean title
    clean_t = clean_title(title)
    key = (clean_t.lower().strip(), artist.lower().strip())
    if key in seen:
        continue
    seen.add(key)
    cleaned.append({'title': clean_t, 'artist': artist or '', 'album': r.get('album',''), 'url': r.get('url','')})

missing_artist_after = sum(1 for r in cleaned if not r.get('artist'))
# write cleaned CSV
with open(out_csv, 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=['title','artist','album','url'])
    w.writeheader()
    for r in cleaned:
        w.writerow(r)

print('Input rows:', len(rows))
print('Cleaned rows:', len(cleaned))
print('Missing artist before:', missing_artist_before)
print('Missing artist after:', missing_artist_after)
print('Wrote:', out_csv)
