#!/usr/bin/env python3
"""Télécharge via slskd les tracks manquantes d'une playlist ou de toutes.

Usage:
  # Toutes les playlists Last.fm (décennies + genres) :
  python3 slskd_download_missing.py --all

  # Une décennie spécifique :
  python3 slskd_download_missing.py --decade 1980

  # Un tag genre Last.fm :
  python3 slskd_download_missing.py --tags "jazz,blues"

  # Tracks manuelles :
  python3 slskd_download_missing.py --tracks "The Police|Every Breath You Take"

Env requis: SLSKD_URL, SLSKD_API_KEY, LASTFM_API_KEY
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path
from typing import List, Tuple, Dict

sys.path.insert(0, str(Path(__file__).parent))

from slskd_downloader import SlskdClient
from generate_top_france import norm_for_matching
from historical_fetchers import (
    fetch_lastfm_france_decade,
    fetch_lastfm_geo_toptracks,
    fetch_lastfm_tag_toptracks,
    fetch_lastfm_global_decade,
    _DECADE_TAGS,
    LASTFM_API_KEY,
)

PLEX_DB_DEFAULT = '/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db'

# Toutes les playlists Last.fm décennies (France + Monde)
DECADE_PLAYLISTS: Dict[str, dict] = {
    'Top France 80s':   {'type': 'france_decade', 'decade': 1980},
    'Top France 90s':   {'type': 'france_decade', 'decade': 1990},
    'Top France 2000s': {'type': 'france_decade', 'decade': 2000},
    'Top Monde 70s':    {'type': 'global_decade', 'decade': 1970},
    'Top Monde 80s':    {'type': 'global_decade', 'decade': 1980},
    'Top Monde 90s':    {'type': 'global_decade', 'decade': 1990},
    'Top Monde 2000s':  {'type': 'global_decade', 'decade': 2000},
    'Top Monde 2010s':  {'type': 'global_decade', 'decade': 2010},
}

# Toutes les playlists genre (tags Last.fm → nom Plex)
GENRE_PLAYLISTS: Dict[str, List[str]] = {
    'Top Monde Jazz':         ['jazz', 'blues'],
    'Top Monde Hip-Hop':      ['hip-hop', 'rap'],
    'Top Monde Pop':          ['pop'],
    'Top FR Chanson Française': ['chanson française', 'variété française', 'french pop'],
    'Top Monde Reggae':       ['reggae', 'roots reggae'],
    'Top Monde Electronic':   ['electronic', 'techno', 'house'],
    'Top Monde Soul & Funk':  ['soul', 'funk', 'r&b'],
    'Top Monde Classical':    ['classical'],
    'Top Monde Country & Folk': ['country', 'folk'],
    'Top Monde Indie':        ['indie', 'alternative'],
    'Top Monde Latin':        ['latin', 'bossa nova'],
    'Top Monde World':        ['world', 'world music'],
    'Top Monde Rock':         ['rock', 'classic rock'],
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_library_tracks(plex_db: str) -> List[Tuple[str, str]]:
    """Retourne [(title, artist)] de TOUTE la bibliothèque Plex."""
    uri = f'file:{urllib.parse.quote(plex_db, safe="/")}?mode=ro&immutable=1'
    conn = sqlite3.connect(uri, uri=True)
    conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
    cur = conn.cursor()
    rows = cur.execute('''
        SELECT mi.title, mi.original_title, art.title
        FROM metadata_items mi
        JOIN metadata_items album ON album.id=mi.parent_id
        JOIN metadata_items art ON art.id=album.parent_id
        WHERE mi.metadata_type=10
    ''').fetchall()
    conn.close()
    return [((orig or title or '').strip(), (artist or '').strip())
            for title, orig, artist in rows]


def find_missing(
    chart: List[Tuple[str, str]],
    library: List[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    lib_set = {(norm_for_matching(t), norm_for_matching(a)) for t, a in library}
    return [
        (title, artist) for title, artist in chart
        if (norm_for_matching(title), norm_for_matching(artist)) not in lib_set
    ]


def fetch_chart(playlist_name: str, cfg: dict, limit: int, api_key: str) -> List[Tuple[str, str]]:
    """Fetch Last.fm chart pour une playlist décennie."""
    ptype = cfg['type']
    decade = cfg['decade']
    seen: set = set()
    results = []

    def _add(tracks):
        for t in tracks:
            k = (norm_for_matching(t['title']), norm_for_matching(t['artist']))
            if k not in seen:
                seen.add(k)
                results.append((t['title'], t['artist']))

    if ptype == 'france_decade':
        _add(fetch_lastfm_france_decade(decade=decade, limit=limit, api_key=api_key))
    elif ptype == 'global_decade':
        _add(fetch_lastfm_global_decade(decade=decade, limit=limit, api_key=api_key))

    return results


def fetch_genre_chart(tags: List[str], limit: int, api_key: str) -> List[Tuple[str, str]]:
    seen: set = set()
    results = []
    for tag in tags:
        for t in fetch_lastfm_tag_toptracks(tag=tag, limit=limit, api_key=api_key):
            k = (norm_for_matching(t['title']), norm_for_matching(t['artist']))
            if k not in seen:
                seen.add(k)
                results.append((t['title'], t['artist']))
    return results


def wait_for_slskd(client: SlskdClient, timeout: int = 300) -> bool:
    import urllib.request, json
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"{client.base}/api/v0/application")
            req.add_header("X-API-Key", client.api_key)
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.load(r)
                state = d.get('state', '')
                if 'Scanning' not in state:
                    print(f"  ✅ slskd prêt (state={state})", flush=True)
                    return True
                print(f"  ⏳ slskd scan en cours ({state})…", flush=True)
        except Exception as e:
            print(f"  ⏳ slskd non disponible ({e}), retry dans 15s…", flush=True)
        time.sleep(15)
    return False


def download_list(
    client: SlskdClient,
    to_download: List[Tuple[str, str]],
    dry_run: bool,
    delay: float,
    label: str = '',
) -> Tuple[int, int, int]:
    """Télécharge une liste de (title, artist). Retourne (queued, not_found, errors)."""
    queued = not_found = errors = 0
    total = len(to_download)
    for i, (title, artist) in enumerate(to_download, 1):
        lbl = f"{artist} — {title}" if artist else title
        print(f"  [{i}/{total}] {lbl}", flush=True)
        if dry_run:
            candidates = client.search(title, artist)
            if candidates:
                best = candidates[0]
                bn = best.filename.replace('\\', '/').rsplit('/', 1)[-1]
                print(f"    ✅ {best.username}/{bn} [{best.extension} score={best.score}]", flush=True)
                queued += 1
            else:
                print("    ❌ introuvable", flush=True)
                not_found += 1
        else:
            result = client.search_and_download(title, artist, verbose=False)
            if result.queued:
                f = result.file
                bn = f.filename.replace('\\', '/').rsplit('/', 1)[-1] if f else '?'
                print(f"    ✅ queued: {bn}", flush=True)
                queued += 1
            elif result.error == 'no results':
                print("    ❌ introuvable", flush=True)
                not_found += 1
            else:
                print(f"    ⚠️  {result.error}", flush=True)
                errors += 1
        if i < total:
            time.sleep(delay)
    return queued, not_found, errors


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--plex-db', default=PLEX_DB_DEFAULT)
    parser.add_argument('--all', action='store_true',
                        help='Toutes les playlists (décennies + genres)')
    parser.add_argument('--decade', type=int, default=0,
                        help='Une décennie France: 1980, 1990, 2000…')
    parser.add_argument('--global-decade', type=int, default=0,
                        help='Une décennie mondiale: 1970, 1980…')
    parser.add_argument('--tags', default='',
                        help='Tags Last.fm genre: "jazz,blues"')
    parser.add_argument('--tracks', default='',
                        help='Manuel: "Artiste|Titre,Artiste2|Titre2"')
    parser.add_argument('--limit', type=int, default=300,
                        help='Nb tracks à récupérer depuis Last.fm')
    parser.add_argument('--max-dl', type=int, default=50,
                        help='Nb max de téléchargements par playlist')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--slskd-url',
                        default=os.environ.get('SLSKD_URL', 'http://localhost:5030'))
    parser.add_argument('--slskd-key',
                        default=os.environ.get('SLSKD_API_KEY', ''))
    parser.add_argument('--delay', type=float, default=3.0)
    args = parser.parse_args()

    if not args.slskd_key:
        sys.exit('❌  SLSKD_API_KEY manquant')

    api_key = LASTFM_API_KEY

    client = SlskdClient(url=args.slskd_url, api_key=args.slskd_key)

    print('⏳ Vérification slskd…', flush=True)
    if not wait_for_slskd(client, timeout=300):
        sys.exit('❌  slskd non disponible après 5 minutes')

    print('📚 Chargement bibliothèque Plex…', flush=True)
    library = get_library_tracks(args.plex_db)
    print(f'   {len(library):,} tracks dans la bibliothèque', flush=True)

    total_q = total_nf = total_err = 0

    # ── Mode --tracks manuel ─────────────────────────────────────────────────
    if args.tracks:
        to_dl = []
        for entry in args.tracks.split(','):
            parts = entry.strip().split('|', 1)
            if len(parts) == 2:
                to_dl.append((parts[1].strip(), parts[0].strip()))
            elif parts:
                to_dl.append((parts[0].strip(), ''))
        print(f'\n🎵 {len(to_dl)} tracks manuelles', flush=True)
        q, nf, e = download_list(client, to_dl, args.dry_run, args.delay)
        total_q += q; total_nf += nf; total_err += e

    # ── Mode --decade (France) ───────────────────────────────────────────────
    elif args.decade:
        cfg = {'type': 'france_decade', 'decade': args.decade}
        pl_name = f'Top France {args.decade}s' if args.decade != 2000 else 'Top France 2000s'
        print(f'\n🇫🇷 {pl_name} — fetch Last.fm…', flush=True)
        chart = fetch_chart(pl_name, cfg, args.limit, api_key)
        missing = find_missing(chart, library)[:args.max_dl]
        print(f'   {len(missing)} manquants à télécharger', flush=True)
        q, nf, e = download_list(client, missing, args.dry_run, args.delay, pl_name)
        total_q += q; total_nf += nf; total_err += e

    # ── Mode --global-decade ─────────────────────────────────────────────────
    elif args.global_decade:
        cfg = {'type': 'global_decade', 'decade': args.global_decade}
        pl_name = f'Top Monde {args.global_decade}s' if args.global_decade != 2000 else 'Top Monde 2000s'
        print(f'\n🌍 {pl_name} — fetch Last.fm…', flush=True)
        chart = fetch_chart(pl_name, cfg, args.limit, api_key)
        missing = find_missing(chart, library)[:args.max_dl]
        print(f'   {len(missing)} manquants à télécharger', flush=True)
        q, nf, e = download_list(client, missing, args.dry_run, args.delay, pl_name)
        total_q += q; total_nf += nf; total_err += e

    # ── Mode --tags genre ────────────────────────────────────────────────────
    elif args.tags:
        tags = [t.strip() for t in args.tags.split(',') if t.strip()]
        pl_name = f'Top {tags[0].title()}'
        print(f'\n🎸 {pl_name} (tags: {", ".join(tags)}) — fetch Last.fm…', flush=True)
        chart = fetch_genre_chart(tags, args.limit, api_key)
        missing = find_missing(chart, library)[:args.max_dl]
        print(f'   {len(missing)} manquants', flush=True)
        q, nf, e = download_list(client, missing, args.dry_run, args.delay, pl_name)
        total_q += q; total_nf += nf; total_err += e

    # ── Mode --all (tout) ────────────────────────────────────────────────────
    elif args.all:
        all_playlists = list(DECADE_PLAYLISTS.items()) + [
            (name, {'type': 'genre', 'tags': tags})
            for name, tags in GENRE_PLAYLISTS.items()
        ]
        print(f'\n🚀 Mode ALL — {len(all_playlists)} playlists\n', flush=True)

        for pl_name, cfg in all_playlists:
            ptype = cfg['type']
            print(f'\n── {pl_name} ──', flush=True)

            if ptype == 'genre':
                chart = fetch_genre_chart(cfg['tags'], args.limit, api_key)
            else:
                chart = fetch_chart(pl_name, cfg, args.limit, api_key)

            missing = find_missing(chart, library)[:args.max_dl]
            print(f'   {len(chart)} dans chart, {len(missing)} à télécharger', flush=True)

            if missing:
                q, nf, e = download_list(client, missing, args.dry_run, args.delay, pl_name)
                total_q += q; total_nf += nf; total_err += e

    else:
        parser.print_help()
        sys.exit(1)

    print(f'\n{"[DRY-RUN] " if args.dry_run else ""}Résultat total:')
    print(f'  ✅  queued    : {total_q}')
    print(f'  ❌  introuvable: {total_nf}')
    if total_err:
        print(f'  ⚠️   erreurs   : {total_err}')


if __name__ == '__main__':
    main()
