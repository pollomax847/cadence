#!/usr/bin/env bash
set -euo pipefail

BASE="/home/paulceline/scripts"
LOG="$BASE/logs/spotify_daily.log"
mkdir -p "$BASE/logs"
cd "$BASE"

echo "=== $(date -u) Starting spotify daily run ===" >> "$LOG"

# Fetch rendered page via headless scraper
/usr/bin/node playlists/headless/scrape_page.js "https://spotifycharts.com/regional/fr/daily/latest" > playlists/cache/spotify_latest_node.html 2>>"$LOG" || true

# Parse cached HTML and generate M3U
PYTHONPATH=playlists python3 - <<'PY' >>"$LOG" 2>&1
import re
from pathlib import Path
from generate_top_france import match_chart_to_library, export_m3u
from auto_playlists_plexamp import PlexAmpAutoPlaylist

html = Path('playlists/cache/spotify_latest_node.html').read_text(encoding='utf-8', errors='ignore')
entries = []
for m in re.finditer(r'<li[^>]*data-testid="charts-entry-item"[\s\S]*?<p[^>]*>(.*?)</p>[\s\S]*?<span[^>]*data-testid="artists-names"[^>]*>([\s\S]*?)</span>', html, re.I):
    raw_title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
    raw_title = re.sub(r'^[0-9]+\s*[-–]\s*', '', raw_title)
    raw_title = re.sub(r'^[0-9]+\s*New\s*', '', raw_title, flags=re.I)
    raw_title = raw_title.replace('\u00A0',' ').replace('\xa0',' ')
    raw_title = re.sub(r'\s+', ' ', raw_title).strip()
    artist_html = m.group(2)
    artists = re.findall(r'>([^<>]+?)</a>', artist_html)
    if not artists:
        artists = [re.sub(r'<[^>]+>','', artist_html).strip()]
    artist = ', '.join(a.strip() for a in artists if a.strip())
    if raw_title:
        entries.append({'track': raw_title, 'artist': artist})

pa = PlexAmpAutoPlaylist(plex_db_path='/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db')
tracks = pa.get_track_data()
matched_ids, unmatched = match_chart_to_library(entries, tracks, threshold=0.75)
out = Path('playlists/generated/Top_France_(daily_latest).m3u')
export_m3u(matched_ids, tracks, out, 'Top France (daily_latest)')
if unmatched:
    um = out.with_suffix('.unmatched.txt')
    with open(um, 'w', encoding='utf-8') as f:
        for e in unmatched:
            f.write(f"{e['track']} — {e['artist']}\n")
PY

# Detect token and push deduped playlist
TOKEN=$(sudo python3 utils/detect_plex_token.py --json | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])") || TOKEN=""
export PLEX_TOKEN="$TOKEN"
export PLEX_URL='http://127.0.0.1:32400'

PYTHONPATH=playlists python3 playlists/headless/dedupe_and_push.py --m3u 'playlists/generated/Top_France_(daily_latest).m3u' --plex-db '/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db' --push >>"$LOG" 2>&1 || echo "Push failed" >> "$LOG"

echo "=== $(date -u) Done ===" >> "$LOG"
