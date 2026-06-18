#!/usr/bin/env python3
"""Générateur de playlists Top France (prototype)

Récupère les charts Spotify (CSV) pour la région FR et tente de matcher
les titres avec la bibliothèque Plex locale. Exporte un M3U et liste
les titres non trouvés pour revue.

Usage example:
  python3 generate_top_france.py --plex-db /path/to/com.plexapp.plugins.library.db
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys
import urllib.request
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from text_normalization import normalize_ascii
from auto_playlists_plexamp import PlexAmpAutoPlaylist
from historical_fetchers import (
    fetch_wikipedia_year, fetch_lescharts_year,
    fetch_lastfm_geo_toptracks, fetch_lastfm_tag_toptracks,
    fetch_lastfm_france_decade, fetch_discogs_releases,
    LASTFM_API_KEY, DISCOGS_USER_TOKEN,
)
from plex_api import plex_create_audio_playlist, default_plex_url, plex_machine_identifier

SPOTIFYCHARTS_URL = "https://spotifycharts.com/regional/{region}/{period}/{date}/download"


def fetch_spotifycharts(region: str = "fr", period: str = "daily", date: str = "latest") -> List[Dict[str, str]]:
    url = SPOTIFYCHARTS_URL.format(region=region, period=period, date=date)
    resp = urllib.request.urlopen(url, timeout=30)
    data = resp.read()
    # spotifycharts CSV sometimes includes a UTF-8 BOM
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for r in reader:
        # Expected fields: 'Track Name' and 'Artist'
        track = (r.get('Track Name') or r.get('Track name') or r.get('Track') or '').strip()
        artist = (r.get('Artist') or r.get('Artists') or '').strip()
        if track:
            rows.append({'track': track, 'artist': artist})
    return rows


def normalized(s: str) -> str:
    return normalize_ascii((s or '').lower())


def norm_for_matching(s: str) -> str:
    if not s:
        return ''
    # Remove parenthetical info and feat./ft.
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\bfeat\.?\b.*", "", s, flags=re.I)
    s = re.sub(r"\bft\.?\b.*", "", s, flags=re.I)
    return normalized(s)


def _extract_year_from_text(s: str) -> Optional[int]:
    if not s:
        return None
    m = re.search(r'(19|20)\d{2}', s)
    if m:
        try:
            y = int(m.group(0))
            if 1950 <= y <= 2030:
                return y
        except Exception:
            return None
    return None


def get_track_year(t: Dict) -> Optional[int]:
    # Prefer explicit year metadata
    y = t.get('year')
    if y:
        try:
            return int(y)
        except Exception:
            pass
    # try album/title/file_path heuristics
    for key in ('album', 'title', 'file_path'):
        v = t.get(key) or ''
        if v:
            # check for year inside parentheses or anywhere
            yy = _extract_year_from_text(v)
            if yy:
                return yy
    # try parent folder year from file_path
    fp = t.get('file_path') or ''
    if fp:
        # look for /YYYY/ in path
        m = re.search(r'/(19|20)\d{2}/', fp)
        if m:
            try:
                return int(m.group(0).strip('/'))
            except Exception:
                pass
    return None


def match_chart_to_library(chart_entries: List[Dict[str, str]], tracks: List[Dict], threshold: float = 0.78) -> Tuple[List[int], List[Dict[str, str]]]:
    """Retourne (matched_ids, unmatched_entries).

    Matching strategy (rapide):
      1. Lookup exact dict title+artist  → O(1)
      2. Lookup exact dict title seul    → O(1)
      3. Fuzzy sur candidats pré-filtrés par mots communs (index inversé)
    """
    # ── Construction de l'index ─────────────────────────────────────────────
    _STOP = {'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'and', 'or', 'de', 'la', 'le', 'les', 'du', 'un', 'une'}

    def _words(s: str) -> List[str]:
        return [w for w in re.split(r'\W+', s) if len(w) > 2 and w not in _STOP]

    lib_index: List[Dict] = []
    exact_ta: Dict[Tuple[str, str], int] = {}   # (norm_title, norm_artist) → id
    exact_t:  Dict[str, int] = {}               # norm_title → id
    word_idx: Dict[str, List[int]] = {}         # word → [list indices in lib_index]

    for t in tracks:
        nt = norm_for_matching(t.get('title') or '')
        na = norm_for_matching(t.get('artist') or '')
        tid = int(t.get('id') or 0)
        idx = len(lib_index)
        lib_index.append({'id': tid, 'norm_title': nt, 'norm_artist': na})
        if nt and (nt, na) not in exact_ta:
            exact_ta[(nt, na)] = tid
        if nt and nt not in exact_t:
            exact_t[nt] = tid
        for w in _words(nt) + _words(na):
            word_idx.setdefault(w, []).append(idx)

    # ── Matching par entrée ─────────────────────────────────────────────────
    matched_ids: List[int] = []
    unmatched:   List[Dict[str, str]] = []
    seen_ids:    set = set()

    def _try_match(n_title: str, n_artist: str) -> Optional[int]:
        # 1) exact title + artist
        fid = exact_ta.get((n_title, n_artist))
        if fid:
            return fid
        # 2) exact title only
        fid = exact_t.get(n_title)
        if fid:
            return fid
        # 3) fuzzy sur candidats ayant ≥1 mot en commun
        query_words = _words(n_title) + _words(n_artist)
        if not query_words:
            return None
        candidate_idxs: set = set()
        for w in query_words:
            for ci in word_idx.get(w, []):
                candidate_idxs.add(ci)
        best_score, best_id = 0.0, None
        for ci in candidate_idxs:
            rec = lib_index[ci]
            tr = SequenceMatcher(None, n_title, rec['norm_title']).ratio() if n_title and rec['norm_title'] else 0.0
            ar = SequenceMatcher(None, n_artist, rec['norm_artist']).ratio() if n_artist and rec['norm_artist'] else 0.0
            score = 0.7 * tr + 0.3 * ar
            if score > best_score:
                best_score, best_id = score, rec['id']
        return best_id if best_score >= threshold else None

    for entry in chart_entries:
        c_title  = entry.get('track') or ''
        c_artist = entry.get('artist') or ''
        n_title  = norm_for_matching(c_title)
        n_artist = norm_for_matching(c_artist)

        fid = _try_match(n_title, n_artist)
        # essai inversé (certaines sources donnent artist/title dans le mauvais sens)
        if not fid:
            fid = _try_match(norm_for_matching(c_artist), norm_for_matching(c_title))

        if fid and fid not in seen_ids:
            seen_ids.add(fid)
            matched_ids.append(fid)
        elif not fid:
            unmatched.append({'track': c_title, 'artist': c_artist})

    return matched_ids, unmatched


def export_m3u(track_ids: List[int], tracks: List[Dict], out_path: Path, name: str):
    id_map = {int(t.get('id') or 0): t for t in tracks}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        f.write(f'#PLAYLIST:{name}\n\n')
        for tid in track_ids:
            t = id_map.get(int(tid))
            if not t:
                continue
            duration = int((t.get('duration_ms') or 0) / 1000) if t.get('duration_ms') else -1
            artist = t.get('artist') or 'Unknown'
            title = t.get('title') or 'Unknown'
            file_path = t.get('file_path') or ''
            f.write(f'#EXTINF:{duration},{artist} - {title}\n')
            f.write(file_path + '\n')


def main():
    parser = argparse.ArgumentParser(description='Génère playlist Top France depuis spotifycharts et match local')
    parser.add_argument('--plex-db', required=True, help='Chemin vers la DB Plex (com.plexapp.plugins.library.db)')
    parser.add_argument('--period', default='daily', choices=['daily', 'weekly'])
    parser.add_argument('--date', default='latest', help='Date slug pour spotifycharts (default latest)')
    parser.add_argument('--region', default='fr', help='Code région (par défaut fr)')
    parser.add_argument('--source', choices=['spotify', 'lescharts', 'wikipedia', 'both', 'lastfm', 'discogs'], default='spotify', help='Source pour charts historiques (lastfm=top tracks scrobblées en France; discogs=releases populaires)')
    parser.add_argument('--lastfm-api-key', default='', help='Clé API Last.fm (sinon LASTFM_API_KEY env)')
    parser.add_argument('--discogs-token', default='', help='Token Discogs (sinon DISCOGS_USER_TOKEN env)')
    parser.add_argument('--country', default='France', help='Pays pour Last.fm geo (défaut: France)')
    parser.add_argument('--years', help='Comma-separated years or ranges (e.g. 1995,2000-2005)')
    parser.add_argument('--decades', help='Comma-separated decades (e.g. 1980,1990)')
    parser.add_argument('--push', action='store_true', help='Pousser les playlists sur Plex via API (nécessite PLEX env vars)')
    parser.add_argument('--dry-run', action='store_true', help='Simuler les opérations de push vers Plex sans créer de playlist')
    parser.add_argument('--threshold', type=float, default=0.78, help='Seuil de matching fuzzy (0-1)')
    parser.add_argument('--out-dir', default=str(Path(__file__).parent / 'generated'), help='Dossier de sortie pour M3U')
    parser.add_argument('--limit', type=int, default=50, help='Nombre max de titres à inclure depuis le chart')
    # Mode local: génère des playlists par décennie/rap à partir de la bibliothèque
    parser.add_argument('--local', action='store_true', help='Générer playlists locales par décennie/genre sans appeler spotifycharts')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    logger = logging.getLogger(__name__)

    # Charger la bibliothèque Plex
    pa = PlexAmpAutoPlaylist(plex_db_path=args.plex_db)
    logger.info('Chargement des métadonnées Plex...')
    tracks = pa.get_track_data()
    if not tracks:
        logger.error('Aucune piste chargée depuis la DB Plex. Abandon.')
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.local:
        # Générer playlists locales par décennie et rap
        logger.info('Génération de playlists locales par décennie/rap...')

        def top_by_year_range(start_year, end_year, limit=200):
            sel = []
            for t in tracks:
                ty = get_track_year(t)
                if ty and start_year <= ty <= end_year:
                    sel.append(t)
            sel.sort(key=lambda x: ((x.get('play_count') or 0), (x.get('rating') or 0)), reverse=True)
            return sel[:limit]

        def top_rap_by_year_range(start_year, end_year, limit=200):
            def is_rap(t):
                genres = [g.lower() for g in t.get('genres', [t.get('genre', '')]) if g]
                text = ' '.join([t.get('title',''), t.get('artist',''), ' '.join(genres)]).lower()
                return any(k in text for k in ('rap', 'hip hop', 'hip-hop', 'gangsta', 'boom bap', 'trap'))

            sel = []
            for t in tracks:
                ty = get_track_year(t)
                if ty and start_year <= ty <= end_year:
                    if is_rap(t):
                        sel.append(t)
                else:
                    # include rap tracks without year metadata
                    if is_rap(t) and not ty:
                        sel.append(t)
            sel.sort(key=lambda x: ((x.get('play_count') or 0), (x.get('rating') or 0)), reverse=True)
            return sel[:limit]

        playlists_to_create = [
            ("Top France 80s", top_by_year_range(1980, 1989, limit=args.limit)),
            ("Top France 90s", top_by_year_range(1990, 1999, limit=args.limit)),
            ("Top France 2000s", top_by_year_range(2000, 2009, limit=args.limit)),
            ("Top Rap 90s", top_rap_by_year_range(1990, 1999, limit=args.limit)),
            ("Top Rap 2000s", top_rap_by_year_range(2000, 2009, limit=args.limit)),
        ]

        for name, plist in playlists_to_create:
            safe_name = name.replace(' ', '_')
            out_path = out_dir / (f"{safe_name}.m3u")
            export_m3u([int(t.get('id') or 0) for t in plist], tracks, out_path, name)
            logger.info(f'Playlist locale exportée: {out_path} ({len(plist)} titres)')
        return

    # If years/decades specified or source historical, fetch from historical sources
    chart_entries: List[Dict[str, str]] = []
    years_to_fetch: List[int] = []
    if args.years:
        parts = [p.strip() for p in args.years.split(',') if p.strip()]
        for p in parts:
            if '-' in p:
                a, b = p.split('-', 1)
                years_to_fetch.extend(list(range(int(a), int(b) + 1)))
            else:
                years_to_fetch.append(int(p))
    if args.decades:
        parts = [p.strip() for p in args.decades.split(',') if p.strip()]
        for d in parts:
            decade = int(d)
            years_to_fetch.extend(list(range(decade, decade + 10)))

    years_to_fetch = sorted(set(years_to_fetch))

    lfm_key = args.lastfm_api_key or LASTFM_API_KEY
    discogs_token = args.discogs_token or DISCOGS_USER_TOKEN

    # ── Last.fm ──────────────────────────────────────────────────────────────
    if args.source == 'lastfm':
        logger.info('Récupération Last.fm (geo France + tags décennie)...')
        if not lfm_key:
            logger.error('Clé Last.fm manquante: définissez LASTFM_API_KEY ou --lastfm-api-key')
            sys.exit(1)

        decades_to_fetch = []
        if args.decades:
            decades_to_fetch = [int(d.strip()) for d in args.decades.split(',') if d.strip()]
        if years_to_fetch and not decades_to_fetch:
            decades_to_fetch = sorted({(y // 10) * 10 for y in years_to_fetch})

        # Index année Plex : id → année (pour filtrer après matching)
        track_year_map: Dict[int, Optional[int]] = {
            int(t.get('id') or 0): get_track_year(t) for t in tracks
        }

        # Noms des playlists à écraser (correspond aux playlists existantes)
        _DECADE_NAMES = {
            1960: 'Top France 60s', 1970: 'Top France 70s',
            1980: 'Top France 80s', 1990: 'Top France 90s',
            2000: 'Top France 2000s', 2010: 'Top France 2010s',
            2020: 'Top France 2020s',
        }

        plex_url   = os.getenv('PLEX_URL', default_plex_url())
        plex_token = os.getenv('PLEX_TOKEN')

        if decades_to_fetch:
            for decade in decades_to_fetch:
                logger.info(f'  Décennie {decade}s...')
                raw = fetch_lastfm_france_decade(decade=decade, limit=args.limit * 6, api_key=lfm_key)
                decade_entries = [{'track': r['title'], 'artist': r['artist']} for r in raw]

                matched_ids, unmatched = match_chart_to_library(decade_entries, tracks, threshold=args.threshold)

                # Filtrer : garder uniquement les pistes dont l'année Plex est dans la décennie
                # (les tracks sans année sont EXCLUS pour éviter de polluer avec de la musique moderne)
                filtered_ids = [
                    tid for tid in matched_ids
                    if (y := track_year_map.get(tid)) is not None and decade <= y <= decade + 9
                ][:args.limit]

                name = _DECADE_NAMES.get(decade, f'Top France {decade}s')
                logger.info(f'  → {len(filtered_ids)} titres retenus ({len(matched_ids)} matchés avant filtre année)')

                out_path = out_dir / (re.sub(r'[^\w\-]', '_', name) + '.m3u')
                export_m3u(filtered_ids, tracks, out_path, name)
                logger.info(f'  M3U: {out_path}')

                if unmatched:
                    um_path = out_path.with_suffix('.unmatched.txt')
                    with open(um_path, 'w', encoding='utf-8') as f:
                        for e in unmatched:
                            f.write(f"{e['track']} — {e['artist']}\n")

                if args.push and not args.dry_run:
                    if plex_token:
                        plex_create_audio_playlist(plex_url, plex_token, name, filtered_ids, machine_id=os.getenv('PLEX_MACHINE_ID'), replace=True)
                        logger.info(f'  ✅ Playlist "{name}" poussée sur Plex ({len(filtered_ids)} titres)')
                elif args.push and args.dry_run:
                    logger.info(f'  Dry-run: "{name}" — {len(filtered_ids)} titres seraient poussés')
        else:
            # Pas de décennie : top all-time France, une seule playlist
            logger.info(f'  Top all-time France ({args.country})...')
            raw = fetch_lastfm_geo_toptracks(country=args.country, limit=args.limit * 4, api_key=lfm_key)
            chart_entries = [{'track': r['title'], 'artist': r['artist']} for r in raw]
            matched_ids, unmatched = match_chart_to_library(chart_entries, tracks, threshold=args.threshold)
            matched_ids = matched_ids[:args.limit]
            name = f'Top France Last.fm ({args.country})'
            logger.info(f'  → {len(matched_ids)} titres appariés')
            out_path = out_dir / (re.sub(r'[^\w\-]', '_', name) + '.m3u')
            export_m3u(matched_ids, tracks, out_path, name)
            logger.info(f'  M3U: {out_path}')
            if unmatched:
                um_path = out_path.with_suffix('.unmatched.txt')
                with open(um_path, 'w', encoding='utf-8') as f:
                    for e in unmatched:
                        f.write(f"{e['track']} — {e['artist']}\n")
            if args.push and not args.dry_run and plex_token:
                plex_create_audio_playlist(plex_url, plex_token, name, matched_ids, machine_id=os.getenv('PLEX_MACHINE_ID'), replace=True)
                logger.info(f'Playlist poussée sur Plex')
            elif args.push and args.dry_run:
                logger.info('Dry-run: %d titres seraient poussés sur Plex', len(matched_ids))
        return

    # ── Discogs ───────────────────────────────────────────────────────────────
    if args.source == 'discogs':
        logger.info('Récupération Discogs (releases France triées par popularité)...')
        if not discogs_token:
            logger.error('Token Discogs manquant: définissez DISCOGS_USER_TOKEN ou --discogs-token')
            sys.exit(1)

        fetch_years = years_to_fetch or ([None] if not args.decades else None)
        if args.decades and not years_to_fetch:
            fetch_years = []
            for d in [int(d.strip()) for d in args.decades.split(',') if d.strip()]:
                fetch_years.extend(range(d, d + 10))

        for y in (fetch_years or [None]):
            raw = fetch_discogs_releases(year=y, country=args.country, limit=args.limit, user_token=discogs_token)
            chart_entries.extend([{'track': r['title'], 'artist': r['artist'], 'year': r.get('year')} for r in raw])

        logger.info(f'{len(chart_entries)} entrées Discogs récupérées')
        if args.limit and len(chart_entries) > args.limit:
            chart_entries = chart_entries[:args.limit]

        matched_ids, unmatched = match_chart_to_library(chart_entries, tracks, threshold=args.threshold)
        logger.info(f'{len(matched_ids)} titres appariés, {len(unmatched)} non trouvés')
        name = f'Top France Discogs {years_to_fetch[0] if years_to_fetch else ""}'.strip()
        out_path = out_dir / (re.sub(r'[^\w\-]', '_', name) + '.m3u')
        export_m3u(matched_ids, tracks, out_path, name)
        logger.info(f'M3U exporté: {out_path}')
        if unmatched:
            um_path = out_path.with_suffix('.unmatched.txt')
            with open(um_path, 'w', encoding='utf-8') as f:
                for e in unmatched:
                    f.write(f"{e['track']} — {e['artist']}\n")
            logger.info(f'Non-trouvés: {um_path}')
        if args.push and not args.dry_run:
            plex_url = os.getenv('PLEX_URL', default_plex_url())
            plex_token = os.getenv('PLEX_TOKEN')
            if plex_token:
                plex_create_audio_playlist(plex_url, plex_token, name, matched_ids, machine_id=os.getenv('PLEX_MACHINE_ID'), replace=True)
                logger.info('Playlist poussée sur Plex')
        elif args.push and args.dry_run:
            logger.info('Dry-run: %d titres seraient poussés sur Plex', len(matched_ids))
        return

    # ── Wikipedia / Lescharts ────────────────────────────────────────────────
    if years_to_fetch or args.source in ('lescharts', 'wikipedia', 'both'):
        logger.info(f'Récupération historique pour années: {years_to_fetch or "(default decades)"}, source={args.source}')
        if not years_to_fetch:
            years_to_fetch = list(range(1980, 2010))

        for y in years_to_fetch:
            if args.source in ('wikipedia', 'both'):
                try:
                    res = fetch_wikipedia_year(y)
                    if res:
                        chart_entries.extend([{'track': r['title'], 'artist': r['artist'], 'year': r.get('year')} for r in res])
                except Exception:
                    pass
            if args.source in ('lescharts', 'both'):
                try:
                    res = fetch_lescharts_year(y)
                    if res:
                        chart_entries.extend([{'track': r['title'], 'artist': r['artist'], 'year': r.get('year')} for r in res])
                except Exception:
                    pass

        logger.info(f'{len(chart_entries)} entrées historiques récupérées')
        if args.limit and len(chart_entries) > args.limit:
            chart_entries = chart_entries[: args.limit]

        matched_ids, unmatched = match_chart_to_library(chart_entries, tracks, threshold=args.threshold)
        logger.info(f'{len(matched_ids)} titres appariés, {len(unmatched)} non trouvés')

        name = f'Historical_Top_FR_{years_to_fetch[0]}_{years_to_fetch[-1]}' if years_to_fetch else 'Historical_Top_FR'
        out_path = out_dir / (name.replace(' ', '_') + '.m3u')
        export_m3u(matched_ids, tracks, out_path, name)
        logger.info(f'M3U exporté: {out_path}')

        if unmatched:
            um_path = out_dir / (name.replace(' ', '_') + '.unmatched.txt')
            with open(um_path, 'w', encoding='utf-8') as f:
                for e in unmatched:
                    f.write(f"{e['track']} — {e['artist']}\n")
            logger.info(f'Liste des non-trouvés écrite: {um_path}')

        if args.push:
            plex_url = os.getenv('PLEX_URL', default_plex_url())
            plex_token = os.getenv('PLEX_TOKEN')
            plex_machine_id = os.getenv('PLEX_MACHINE_ID')
            if not plex_token:
                logger.error('PLEX_TOKEN non défini en env; push annulé')
            else:
                if args.dry_run:
                    logger.info('Dry-run: playlist non poussée. Nombre de titres à pousser: %d', len(matched_ids))
                else:
                    try:
                        plex_create_audio_playlist(plex_url, plex_token, name, matched_ids, machine_id=plex_machine_id, replace=True)
                        logger.info('Playlist poussée sur Plex')
                    except Exception as e:
                        logger.error(f'Erreur push Plex: {e}')
        return

    # Default: try remote spotifycharts
    logger.info('Récupération du chart spotifycharts...')
    try:
        chart = fetch_spotifycharts(region=args.region, period=args.period, date=args.date)
    except Exception as e:
        logger.error(f'Impossible de récupérer spotifycharts: {e}')
        sys.exit(1)

    if args.limit:
        chart = chart[: args.limit]

    logger.info(f'{len(chart)} entrées récupérées depuis le chart')

    matched_ids, unmatched = match_chart_to_library(chart, tracks, threshold=args.threshold)
    logger.info(f'{len(matched_ids)} titres appariés, {len(unmatched)} non trouvés')

    name = f'Top France ({args.period} {args.date})'
    out_path = out_dir / (name.replace(' ', '_') + '.m3u')
    export_m3u(matched_ids, tracks, out_path, name)
    logger.info(f'M3U exporté: {out_path}')

    if unmatched:
        um_path = out_dir / (name.replace(' ', '_') + '.unmatched.txt')
        with open(um_path, 'w', encoding='utf-8') as f:
            for e in unmatched:
                f.write(f"{e['track']} — {e['artist']}\n")
        logger.info(f'Liste des non-trouvés écrite: {um_path}')


if __name__ == '__main__':
    main()
