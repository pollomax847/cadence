#!/usr/bin/env python3
"""Écrit des tags audio dans les fichiers (optionnel).

Usage: dry-run par défaut. Passez `--apply` pour modifier les fichiers.
"""
from __future__ import annotations

import argparse
import os
import re
import unicodedata
from pathlib import Path
from typing import List

from auto_playlists_plexamp import PlexAmpAutoPlaylist


PATH_MAPS = [
    ('/mnt/MyBook/itunes', '/itunes'),
    ('/mnt/MyBook/Music',  '/music'),
    ('/mnt/ssd/Musiques',  '/real-ssd'),
    ('/home/paulceline/Musique', '/real-home'),
]

def resolve_path(p: str) -> str:
    """Traduit un chemin hôte en chemin Docker si nécessaire."""
    for host, container in PATH_MAPS:
        if p.startswith(host):
            return container + p[len(host):]
    return p


def find_plex_db() -> str:
    env = os.getenv('PLEX_DB_PATH') or os.getenv('PLEX_DB')
    candidates = []
    if env:
        candidates.append(env)
    candidates += [
        '/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db',
        '/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db',
        '/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db',
        str(Path.home() / '.config' / 'Plex Media Server' / 'Plug-in Support' / 'Databases' / 'com.plexapp.plugins.library.db'),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise RuntimeError('Plex DB introuvable; définissez PLEX_DB_PATH env')


def _slug(s: str) -> str:
    s = (s or '').strip().lower()
    s = unicodedata.normalize('NFKD', s)
    s = re.sub(r"[^a-z0-9]+", ' ', s)
    return ' '.join(s.split())


def collect_playlist_tracks(ap: PlexAmpAutoPlaylist, playlist_name: str, limit: int | None = None) -> List[dict]:
    tracks = ap.get_track_data()
    all_playlists = ap._build_all_playlists(tracks)
    # dedupe by title|artist
    entries = all_playlists.get(playlist_name)
    if entries is None:
        # try fuzzy match (contains, case-insensitive)
        lowered = playlist_name.casefold()
        matches = [name for name in all_playlists.keys() if lowered in name.casefold()]
        if not matches:
            raise RuntimeError(f'Playlist introuvable: {playlist_name}')
        # choose the first match
        entries = all_playlists[matches[0]]
    seen = set()
    deduped = []
    for t in entries:
        k = _slug(f"{t.get('title','')}|{t.get('artist','')}")
        if k in seen:
            continue
        seen.add(k)
        deduped.append(t)
        if limit and len(deduped) >= limit:
            break
    return deduped


def run(args):
    plex_db = find_plex_db()
    ap = PlexAmpAutoPlaylist(plex_db_path=plex_db)

    if args.all:
        print("Collecting all tracks from DB (deduped by title|artist)...")
        raw = ap.get_track_data()
        seen = set()
        tracks = []
        for t in raw:
            k = _slug(f"{t.get('title','')}|{t.get('artist','')}")
            if k in seen:
                continue
            seen.add(k)
            tracks.append(t)
            if args.limit and len(tracks) >= args.limit:
                break
        print(f"Selected {len(tracks)} tracks (deduped)")
    else:
        tracks = collect_playlist_tracks(ap, args.playlist, limit=args.limit)
        print(f"Selected {len(tracks)} tracks from playlist '{args.playlist}' (deduped)")

    try:
        from mutagen import File as MutagenFile
    except Exception as e:
        print("mutagen not installed; pip install mutagen")
        raise

    changes = []
    for t in tracks:
        fp = t.get('file_path')
        if not fp:
            print(f"SKIP no file path for id={t.get('id')}")
            continue
        p = Path(resolve_path(fp))
        if not p.exists():
            print(f"SKIP missing file: {p}")
            continue

        try:
            audio = MutagenFile(str(p), easy=True)
        except Exception as e:
            print(f"SKIP mutagen error reading {p}: {e}")
            continue
        if audio is None:
            print(f"UNSUPPORTED format: {p}")
            continue

        _SKIP = {'unknown', 'unknown album', 'unknown genre', 'unknown artist', '???'}

        def _valid(v):
            return v and str(v).strip().lower() not in _SKIP

        desired = {}
        if _valid(t.get('title')):
            desired['title'] = [t.get('title')]
        if _valid(t.get('artist')):
            desired['artist'] = [t.get('artist')]
        if _valid(t.get('album')):
            desired['album'] = [t.get('album')]
        if t.get('year'):
            desired['date'] = [str(t.get('year'))]
        # Genre : n'écrire que si le fichier n'en a pas déjà un
        genre = t.get('genre', '').strip()
        cur_genre = (audio.tags.get('genre') if audio.tags else None)
        if _valid(genre) and not cur_genre:
            desired['genre'] = [genre]

        diff = {}
        for k, v in desired.items():
            cur = audio.tags.get(k) if audio.tags else None
            if not cur or list(cur) != v:
                diff[k] = (cur, v)

        if not diff:
            print(f"OK: {p} (no changes)")
            continue

        changes.append((p, diff))
        print(f"PROPOSE: {p}")
        for k, (cur, v) in diff.items():
            print(f"  {k}: {cur} -> {v}")

        if args.apply:
            try:
                for k, (_, v) in diff.items():
                    audio.tags[k] = v
                audio.save()
                print(f"WRITTEN: {p}")
            except TypeError:
                # Fallback for formats where tags expect ID3 Frame instances (e.g. AIFF)
                try:
                    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, ID3NoHeaderError

                    try:
                        id3 = ID3(str(p))
                    except ID3NoHeaderError:
                        id3 = ID3()

                    for k, (_, v) in diff.items():
                        # extract first value if list
                        val = v[0] if isinstance(v, list) and v else v
                        if k == 'title':
                            id3.add(TIT2(encoding=3, text=str(val)))
                        elif k == 'artist':
                            id3.add(TPE1(encoding=3, text=str(val)))
                        elif k == 'album':
                            id3.add(TALB(encoding=3, text=str(val)))
                        elif k in ('date', 'year'):
                            id3.add(TDRC(encoding=3, text=str(val)))

                    id3.save(str(p))
                    print(f"WRITTEN (ID3 fallback): {p}")
                except Exception as e:
                    print(f"FAILED to write {p}: {e}")

    print(f"Done. Proposed changes: {len(changes)}. Applied: {args.apply}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--playlist', required=False, help='Nom exact de la playlist générée')
    p.add_argument('--all', action='store_true', help='Traiter tous les fichiers audio de la DB (dédupliqués)')
    p.add_argument('--limit', type=int, default=10, help='Nombre max de pistes à traiter')
    p.add_argument('--apply', action='store_true', help='Écrire réellement les tags (danger)')
    args = p.parse_args()
    if not args.all and not args.playlist:
        p.error('Vous devez fournir --playlist ou --all')
    run(args)


if __name__ == '__main__':
    main()
