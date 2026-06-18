#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import List, Dict

from generate_top_france import match_chart_to_library
from plex_api import plex_create_audio_playlist
from auto_playlists_plexamp import PlexAmpAutoPlaylist


def parse_m3u(path: Path) -> List[Dict[str,str]]:
    text = path.read_text(encoding='utf-8', errors='replace')
    lines = [l.rstrip('\n') for l in text.splitlines()]
    entries: List[Dict[str,str]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTINF'):
            m = re.match(r'#EXTINF:([^,]*),(.*)', line)
            info = m.group(2).strip() if m else ''
            artist = ''
            title = ''
            if ' - ' in info:
                artist, title = info.split(' - ', 1)
            else:
                title = info
            file_path = ''
            if i + 1 < len(lines):
                file_path = lines[i+1].strip()
            entries.append({'artist': artist.strip(), 'track': title.strip(), 'file_path': file_path})
            i += 2
        else:
            i += 1
    return entries


def primary_artist(artist: str) -> str:
    if not artist:
        return ''
    a = artist.split(',')[0]
    a = re.split(r'\bfeat\b|\bft\b|\bfeaturing\b', a, flags=re.I)[0]
    return re.sub(r"\s+", " ", a).strip().casefold()


def dedupe_entries(entries: List[Dict[str,str]]) -> List[Dict[str,str]]:
    seen_paths = set()
    seen_titles = set()
    seen_artists = set()
    out: List[Dict[str,str]] = []
    for e in entries:
        fp = (e.get('file_path') or '').strip()
        if fp and fp in seen_paths:
            continue
        title_norm = re.sub(r'\s+', ' ', (e.get('track') or '').strip()).casefold()
        if title_norm and title_norm in seen_titles:
            continue
        artist_primary = primary_artist(e.get('artist') or '')
        if artist_primary and artist_primary in seen_artists:
            continue
        if fp:
            seen_paths.add(fp)
        if title_norm:
            seen_titles.add(title_norm)
        if artist_primary:
            seen_artists.add(artist_primary)
        out.append(e)
    return out


def write_m3u(entries: List[Dict[str,str]], out_path: Path, name: str):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        f.write(f'#PLAYLIST:{name}\n\n')
        for e in entries:
            # duration unknown (-1)
            artist = e.get('artist') or 'Unknown'
            title = e.get('track') or 'Unknown'
            fp = e.get('file_path') or ''
            f.write(f'#EXTINF:-1,{artist} - {title}\n')
            f.write(fp + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--m3u', required=True, help='Input M3U to dedupe')
    parser.add_argument('--plex-db', required=True, help='Path to Plex DB for matching')
    parser.add_argument('--out', help='Output M3U path (default adds .deduped.m3u)')
    parser.add_argument('--push', action='store_true', help='Push deduped playlist to Plex')
    parser.add_argument('--title', help='Playlist title to use on Plex')
    parser.add_argument('--threshold', type=float, default=0.75, help='Matching threshold')
    args = parser.parse_args()

    in_path = Path(args.m3u)
    if not in_path.exists():
        raise SystemExit(f'Input M3U not found: {in_path}')

    entries = parse_m3u(in_path)
    deduped = dedupe_entries(entries)

    out_path = Path(args.out) if args.out else in_path.with_name(in_path.stem + '.deduped.m3u')
    playlist_title = args.title or (in_path.stem.replace('_', ' ')) + ' (deduped)'

    write_m3u(deduped, out_path, playlist_title)
    print(f'Wrote deduped M3U: {out_path} ({len(deduped)} entries)')

    # If push requested, match to Plex and push
    if args.push:
        plex_db = args.plex_db
        pa = PlexAmpAutoPlaylist(plex_db_path=plex_db)
        tracks = pa.get_track_data()
        # Build chart-like entries for matching
        chart_entries = [{'track': e.get('track') or '', 'artist': e.get('artist') or ''} for e in deduped]
        matched_ids, unmatched = match_chart_to_library(chart_entries, tracks, threshold=args.threshold)
        print(f'Matched {len(matched_ids)} of {len(chart_entries)} to Plex')
        if unmatched:
            um_path = out_path.with_suffix('.unmatched.txt')
            with open(um_path, 'w', encoding='utf-8') as f:
                for e in unmatched:
                    f.write(f"{e['track']} — {e['artist']}\n")
            print(f'Wrote unmatched: {um_path}')

        if matched_ids:
            plex_token = os.getenv('PLEX_TOKEN')
            plex_url = os.getenv('PLEX_URL') or 'http://127.0.0.1:32400'
            if not plex_token:
                raise SystemExit('PLEX_TOKEN not set in environment; cannot push')
            try:
                rating_key = plex_create_audio_playlist(plex_url, plex_token, playlist_title, matched_ids, replace=True)
                print(f'Playlist created on Plex: ratingKey={rating_key}')
            except Exception as e:
                raise SystemExit(f'Error pushing playlist to Plex: {e}')


if __name__ == '__main__':
    main()
