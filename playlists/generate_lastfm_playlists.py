#!/usr/bin/env python3
"""Générateur de playlists Last.fm (genres, décennies mondiales, chart global, tops user).

Modes disponibles via --mode :
  genre       Top par tag/genre  (ex: jazz, metal, hip-hop, chanson française)
  decade      Top Monde par décennie (60s, 70s, 80s, 90s, 2000s...)
  chart       Top Charts Mondial en temps réel
  user-top    Top tracks d'un utilisateur Last.fm (périodes: 7day/1month/3month/6month/12month/overall)
  user-loved  Pistes aimées (loved) d'un utilisateur Last.fm

Usage:
  python3 generate_lastfm_playlists.py --mode genre --tags "jazz,metal,hip-hop" --limit 200 --push --plex-db ...
  python3 generate_lastfm_playlists.py --mode decade --decades 1970,1980,1990 --limit 200 --push --plex-db ...
  python3 generate_lastfm_playlists.py --mode chart --limit 100 --push --plex-db ...
  python3 generate_lastfm_playlists.py --mode user-top --lastfm-user monpseudo --period 12month --push --plex-db ...
  python3 generate_lastfm_playlists.py --mode user-loved --lastfm-user monpseudo --push --plex-db ...
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from auto_playlists_plexamp import PlexAmpAutoPlaylist
from generate_top_france import match_chart_to_library, export_m3u, get_track_year, norm_for_matching
from historical_fetchers import (
    fetch_lastfm_geo_toptracks,
    fetch_lastfm_tag_toptracks,
    fetch_lastfm_global_decade,
    fetch_lastfm_chart_toptracks,
    fetch_lastfm_user_toptracks,
    fetch_lastfm_user_lovedtracks,
    LASTFM_API_KEY,
)
from plex_api import plex_create_audio_playlist, default_plex_url

# ── Tags genre → noms "Top Monde [Genre]" (source Last.fm mondiale) ───────────
# Plusieurs tags Last.fm peuvent pointer vers la même playlist (fusion).
DEFAULT_GENRE_TAGS: List[Tuple[str, str]] = [
    # (tag Last.fm,          nom playlist Plex)
    ('jazz',                 'Top Monde Jazz'),
    ('blues',                'Top Monde Jazz'),
    ('hip-hop',              'Top Monde Hip-Hop'),
    ('hip hop',              'Top Monde Hip-Hop'),
    ('rap',                  'Top Monde Hip-Hop'),
    ('chanson française',    'Top FR Chanson Française'),
    ('variété française',    'Top FR Chanson Française'),
    ('french pop',           'Top FR Pop'),
    ('pop',                  'Top Monde Pop'),
    ('reggae',               'Top Monde Reggae'),
    ('roots reggae',         'Top Monde Reggae'),
    ('electronic',           'Top Monde Electronic'),
    ('techno',               'Top Monde Electronic'),
    ('house',                'Top Monde Electronic'),
    ('soul',                 'Top Monde Soul & Funk'),
    ('funk',                 'Top Monde Soul & Funk'),
    ('r&b',                  'Top Monde Soul & Funk'),
    ('classical',            'Top Monde Classical'),
    ('orchestral',           'Top Monde Classical'),
    ('country',              'Top Monde Country & Folk'),
    ('folk',                 'Top Monde Country & Folk'),
    ('indie',                'Top Monde Indie'),
    ('alternative',          'Top Monde Indie'),
    ('latin',                'Top Monde Latin'),
    ('bossa nova',           'Top Monde Latin'),
    ('world',                'Top Monde World'),
    ('world music',          'Top Monde World'),
    ('rock',                 'Top Monde Rock'),
    ('classic rock',         'Top Monde Rock'),
]

_GENRE_TAG_TO_NAME: Dict[str, str] = {tag: name for tag, name in DEFAULT_GENRE_TAGS}

# Mapping genres Plex (niveau album) → noms playlists "Top FR"
# Les genres Plex viennent de iTunes/MusicBrainz, pas de Last.fm — normalisation nécessaire.
_PLEX_GENRE_TO_FR_PLAYLIST: Dict[str, str] = {
    'pop/rock':             'Top FR Pop',
    'pop rock':             'Top FR Pop',
    'pop':                  'Top FR Pop',
    'pop music':            'Top FR Pop',
    'pop hits':             'Top FR Pop',
    'french pop':           'Top FR Chanson Française',
    'variété française':    'Top FR Chanson Française',
    'chanson française':    'Top FR Chanson Française',
    'chanson':              'Top FR Chanson Française',
    'r&b':                  'Top FR R&B & Soul',
    'r&b/soul':             'Top FR R&B & Soul',
    'contemporary r&b':     'Top FR R&B & Soul',
    'soul':                 'Top FR R&B & Soul',
    'funk':                 'Top FR R&B & Soul',
    'urban':                'Top FR R&B & Soul',
    'vocal':                'Top FR R&B & Soul',
    'rap':                  'Top FR Hip-Hop',
    'hip-hop/rap':          'Top FR Hip-Hop',
    'hip hop':              'Top FR Hip-Hop',
    'hip-hop':              'Top FR Hip-Hop',
    'electronic':           'Top FR Electronic',
    'electronica':          'Top FR Electronic',
    'electronica et dance': 'Top FR Electronic',
    'house':                'Top FR Electronic',
    'techno':               'Top FR Electronic',
    'dance':                'Top FR Electronic',
    'trance':               'Top FR Electronic',
    'electro':              'Top FR Electronic',
    'ambient':              'Top FR Electronic',
    'disco':                'Top FR Electronic',
    'jazz':                 'Top FR Jazz',
    'blues':                'Top FR Jazz',
    'reggae':               'Top FR Reggae',
    'latin':                'Top FR Latin',
    'bossa nova':           'Top FR Latin',
    'rock':                 'Top FR Rock',
    'alternative rock':     'Top FR Rock',
    'alternative':          'Top FR Rock',
    'indie':                'Top FR Rock',
    'classical':            'Top FR Classical',
    'easy listening':       'Top FR Pop',
    'new age':              None,
    'international':        None,
    'world':                None,
    'musiques du monde & musiques traditionnelles': None,
    'other':                None,
    'autre':                None,
    'comedy/spoken':        None,
    'religious':            None,
    'holiday':              None,
    'stage & screen':       None,
    'soundtrack':           None,
    'music':                None,
}


def _load_track_genre_map(plex_db_path: str) -> Dict[int, str]:
    """Retourne {track_id: playlist_name_top_fr} en lisant les genres au niveau album dans Plex."""
    from urllib.parse import quote as _quote
    import sqlite3 as _sqlite3

    uri = f'file:{_quote(plex_db_path, safe="/")}?mode=ro&immutable=1'
    try:
        conn = _sqlite3.connect(uri, uri=True)
        conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
        cur = conn.cursor()
        rows = cur.execute('''
            SELECT mi.id,
                   (SELECT GROUP_CONCAT(LOWER(t.tag), '||')
                    FROM taggings tg JOIN tags t ON t.id=tg.tag_id
                    WHERE tg.metadata_item_id=album.id AND t.tag_type=1) as genres
            FROM metadata_items mi
            JOIN metadata_items album ON album.id=mi.parent_id
            WHERE mi.metadata_type=10
        ''').fetchall()
        conn.close()
    except Exception:
        return {}

    result: Dict[int, str] = {}
    for track_id, genres_raw in rows:
        if not genres_raw:
            continue
        for g in genres_raw.split('||'):
            g = g.strip()
            pl = _PLEX_GENRE_TO_FR_PLAYLIST.get(g)
            if pl:
                result[track_id] = pl
                break
    return result

_DECADE_NAMES: Dict[int, str] = {
    1950: 'Top Monde 50s', 1960: 'Top Monde 60s', 1970: 'Top Monde 70s',
    1980: 'Top Monde 80s', 1990: 'Top Monde 90s', 2000: 'Top Monde 2000s',
    2010: 'Top Monde 2010s', 2020: 'Top Monde 2020s',
}

# Noms correspondant aux playlists Plex existantes pour les tops user
_USER_PERIOD_NAMES: Dict[str, str] = {
    '7day':    'Top Tracks (Last 7 days)',
    '1month':  'Top Tracks (Last 30 days)',
    '3month':  'Top Tracks (Last 3  months)',
    '6month':  'Top Tracks (Last 6  months)',
    '12month': 'Top Tracks (Last 12  months)',
    'overall': 'Top Tracks (All Time)',
}


def _push_or_log(
    name: str,
    matched_ids: List[int],
    tracks: List[Dict],
    out_dir: Path,
    plex_url: str,
    plex_token: Optional[str],
    dry_run: bool,
    push: bool,
    logger: logging.Logger,
) -> None:
    out_path = out_dir / (re.sub(r'[^\w\-]', '_', name) + '.m3u')
    export_m3u(matched_ids, tracks, out_path, name)
    logger.info(f'  M3U: {out_path}')

    if push and not dry_run and plex_token:
        plex_create_audio_playlist(
            plex_url, plex_token, name, matched_ids,
            machine_id=os.getenv('PLEX_MACHINE_ID'), replace=True,
        )
        logger.info(f'  ✅ "{name}" poussée sur Plex ({len(matched_ids)} titres)')
    elif push and dry_run:
        logger.info(f'  Dry-run: "{name}" — {len(matched_ids)} titres seraient poussés')


def main() -> None:
    parser = argparse.ArgumentParser(description='Générateur de playlists Last.fm')
    parser.add_argument('--plex-db', required=True, help='Chemin vers la DB Plex')
    parser.add_argument('--mode', required=True,
                        choices=['genre', 'france-genre', 'decade', 'chart', 'user-top', 'user-loved'],
                        help='Type de playlist à générer')
    # genre
    parser.add_argument('--tags', default='',
                        help='Tags Last.fm séparés par virgule (mode genre). Ex: "jazz,metal,hip-hop"')
    parser.add_argument('--all-genres', action='store_true',
                        help='Générer toutes les playlists genre prédéfinies')
    # decade
    parser.add_argument('--decades', default='',
                        help='Décennies séparées par virgule (mode decade). Ex: "1970,1980,1990"')
    # user
    parser.add_argument('--lastfm-user', default=os.environ.get('LASTFM_USER', ''),
                        help='Pseudo Last.fm (modes user-top et user-loved)')
    parser.add_argument('--period',
                        choices=['7day', '1month', '3month', '6month', '12month', 'overall'],
                        default='overall', help='Période pour user-top')
    # commun
    parser.add_argument('--limit', type=int, default=200, help='Nombre max de titres par playlist')
    parser.add_argument('--threshold', type=float, default=0.78, help='Seuil fuzzy matching')
    parser.add_argument('--push', action='store_true', help='Pousser sur Plex')
    parser.add_argument('--dry-run', action='store_true', help='Simulation sans écriture Plex')
    parser.add_argument('--out-dir', default=str(Path(__file__).parent / 'generated'))
    parser.add_argument('--lastfm-api-key', default='')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    logger = logging.getLogger(__name__)

    lfm_key = args.lastfm_api_key or LASTFM_API_KEY
    if not lfm_key:
        logger.error('Clé Last.fm manquante : LASTFM_API_KEY ou --lastfm-api-key')
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plex_url   = os.getenv('PLEX_URL', default_plex_url())
    plex_token = os.getenv('PLEX_TOKEN')

    logger.info('Chargement des métadonnées Plex...')
    pa = PlexAmpAutoPlaylist(plex_db_path=args.plex_db)
    tracks = pa.get_track_data()
    if not tracks:
        logger.error('Aucune piste chargée. Abandon.')
        sys.exit(1)
    logger.info(f'📊 {len(tracks)} pistes chargées')

    track_year_map: Dict[int, Optional[int]] = {
        int(t.get('id') or 0): get_track_year(t) for t in tracks
    }

    # ── MODE FRANCE GENRE ────────────────────────────────────────────────────
    # Fetch top France réel via geo.getTopTracks, match dans Plex, groupe par genre Plex
    if args.mode == 'france-genre':
        from collections import defaultdict

        france_geo_limit = max(args.limit * 6, 1000)
        logger.info(f'Fetching top France réel ({france_geo_limit} tracks via geo.getTopTracks)...')
        raw_france = fetch_lastfm_geo_toptracks(country='France', limit=france_geo_limit, api_key=lfm_key)
        if not raw_france:
            logger.error('Aucun résultat geo.getTopTracks France')
            sys.exit(1)
        logger.info(f'  → {len(raw_france)} tracks récupérés depuis Last.fm France')

        entries = [{'track': r['title'], 'artist': r['artist']} for r in raw_france]
        all_matched_ids, unmatched = match_chart_to_library(entries, tracks, threshold=args.threshold)
        logger.info(f'  → {len(all_matched_ids)} matchés dans la bibliothèque Plex ({len(unmatched)} non trouvés)')

        # Playlist "Top France" all-time (top 200 sans filtre genre)
        top_fr_all = all_matched_ids[:args.limit]
        logger.info(f'"Top France" all-time → {len(top_fr_all)} titres')
        _push_or_log('Top France', top_fr_all, tracks, out_dir, plex_url, plex_token,
                     args.dry_run, args.push, logger)

        # Grouper par genre Plex
        logger.info('Chargement des genres Plex par track...')
        track_genre_map = _load_track_genre_map(args.plex_db)

        genre_buckets: Dict[str, List[int]] = defaultdict(list)
        for tid in all_matched_ids:
            pl_name = track_genre_map.get(tid)
            if pl_name:
                genre_buckets[pl_name].append(tid)

        logger.info(f'{len(genre_buckets)} genres "Top FR" trouvés :')
        for pl_name, ids in sorted(genre_buckets.items(), key=lambda x: -len(x[1])):
            count = min(len(ids), args.limit)
            logger.info(f'  "{pl_name}" → {count} titres')
            _push_or_log(pl_name, ids[:args.limit], tracks, out_dir, plex_url, plex_token,
                         args.dry_run, args.push, logger)
        return

    # ── MODE GENRE ────────────────────────────────────────────────────────────
    if args.mode == 'genre':
        tag_list: List[Tuple[str, str]] = []

        if args.all_genres:
            tag_list = DEFAULT_GENRE_TAGS
        elif args.tags:
            for raw_tag in [t.strip() for t in args.tags.split(',') if t.strip()]:
                name = _GENRE_TAG_TO_NAME.get(raw_tag.lower(), f'Top {raw_tag.title()}')
                tag_list.append((raw_tag, name))
        else:
            logger.error('Précisez --tags "jazz,metal,..." ou --all-genres')
            sys.exit(1)

        # Regrouper les tags par nom de playlist (plusieurs tags → une seule playlist fusionnée)
        from collections import defaultdict
        tags_by_playlist: Dict[str, List[str]] = defaultdict(list)
        for tag, name in tag_list:
            tags_by_playlist[name].append(tag)

        for playlist_name, tags in tags_by_playlist.items():
            logger.info(f'"{playlist_name}" ← tags: {", ".join(tags)}')
            seen_entries: set = set()
            entries: List[Dict] = []
            for tag in tags:
                raw = fetch_lastfm_tag_toptracks(tag=tag, limit=args.limit * 4, api_key=lfm_key)
                for r in raw:
                    k = (r['title'].lower(), r['artist'].lower())
                    if k not in seen_entries:
                        seen_entries.add(k)
                        entries.append({'track': r['title'], 'artist': r['artist']})
            matched_ids, unmatched = match_chart_to_library(entries, tracks, threshold=args.threshold)
            matched_ids = matched_ids[:args.limit]
            logger.info(f'  → {len(matched_ids)} titres retenus ({len(unmatched)} non trouvés)')
            _push_or_log(playlist_name, matched_ids, tracks, out_dir, plex_url, plex_token,
                         args.dry_run, args.push, logger)

    # ── MODE DECADE MONDIAL ───────────────────────────────────────────────────
    elif args.mode == 'decade':
        if not args.decades:
            logger.error('Précisez --decades "1970,1980,1990"')
            sys.exit(1)

        decades = [int(d.strip()) for d in args.decades.split(',') if d.strip()]
        for decade in decades:
            playlist_name = _DECADE_NAMES.get(decade, f'Top Monde {decade}s')
            logger.info(f'Décennie mondiale {decade}s → "{playlist_name}"')
            raw = fetch_lastfm_global_decade(decade=decade, limit=args.limit * 4, api_key=lfm_key)
            entries = [{'track': r['title'], 'artist': r['artist']} for r in raw]
            matched_ids, unmatched = match_chart_to_library(entries, tracks, threshold=args.threshold)
            # Garder uniquement les tracks dont l'année Plex est dans la décennie (sans année = exclus)
            filtered = [
                tid for tid in matched_ids
                if (y := track_year_map.get(tid)) is not None and decade <= y <= decade + 9
            ][:args.limit]
            logger.info(f'  → {len(filtered)} titres retenus ({len(matched_ids)} matchés avant filtre)')
            _push_or_log(playlist_name, filtered, tracks, out_dir, plex_url, plex_token,
                         args.dry_run, args.push, logger)

    # ── MODE CHART MONDIAL ────────────────────────────────────────────────────
    elif args.mode == 'chart':
        playlist_name = 'Weekly Track Chart'
        logger.info(f'Chart mondial en temps réel → "{playlist_name}"')
        raw = fetch_lastfm_chart_toptracks(limit=args.limit * 4, api_key=lfm_key)
        entries = [{'track': r['title'], 'artist': r['artist']} for r in raw]
        matched_ids, unmatched = match_chart_to_library(entries, tracks, threshold=args.threshold)
        matched_ids = matched_ids[:args.limit]
        logger.info(f'  → {len(matched_ids)} titres retenus ({len(unmatched)} non trouvés)')
        _push_or_log(playlist_name, matched_ids, tracks, out_dir, plex_url, plex_token,
                     args.dry_run, args.push, logger)

    # ── MODE USER TOP ─────────────────────────────────────────────────────────
    elif args.mode == 'user-top':
        if not args.lastfm_user:
            logger.error('Précisez --lastfm-user monpseudo ou LASTFM_USER env')
            sys.exit(1)
        playlist_name = _USER_PERIOD_NAMES.get(args.period, f'Mon Top {args.period}')
        logger.info(f'Top user "{args.lastfm_user}" ({args.period}) → "{playlist_name}"')
        raw = fetch_lastfm_user_toptracks(
            username=args.lastfm_user, period=args.period,
            limit=args.limit * 3, api_key=lfm_key,
        )
        entries = [{'track': r['title'], 'artist': r['artist']} for r in raw]
        matched_ids, unmatched = match_chart_to_library(entries, tracks, threshold=args.threshold)
        matched_ids = matched_ids[:args.limit]
        logger.info(f'  → {len(matched_ids)} titres retenus ({len(unmatched)} non trouvés)')
        _push_or_log(playlist_name, matched_ids, tracks, out_dir, plex_url, plex_token,
                     args.dry_run, args.push, logger)

    # ── MODE USER LOVED ───────────────────────────────────────────────────────
    elif args.mode == 'user-loved':
        if not args.lastfm_user:
            logger.error('Précisez --lastfm-user monpseudo ou LASTFM_USER env')
            sys.exit(1)
        playlist_name = 'Liked Songs (Loved Tracks)'
        logger.info(f'Loved tracks "{args.lastfm_user}" → "{playlist_name}"')
        raw = fetch_lastfm_user_lovedtracks(
            username=args.lastfm_user, limit=args.limit * 3, api_key=lfm_key,
        )
        entries = [{'track': r['title'], 'artist': r['artist']} for r in raw]
        matched_ids, unmatched = match_chart_to_library(entries, tracks, threshold=args.threshold)
        matched_ids = matched_ids[:args.limit]
        logger.info(f'  → {len(matched_ids)} titres retenus ({len(unmatched)} non trouvés)')
        _push_or_log(playlist_name, matched_ids, tracks, out_dir, plex_url, plex_token,
                     args.dry_run, args.push, logger)


if __name__ == '__main__':
    main()
