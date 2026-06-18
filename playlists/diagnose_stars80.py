#!/usr/bin/env python3
"""Diagnostic pour la playlist Stars 80.

Affiche les pistes retenues par `create_stars80_playlist()` et compare
avec une petite liste attendue fournie ici pour repérer les absents.
"""
from pathlib import Path
import sys
import json
import argparse

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import auto_playlists_plexamp as app  # noqa: E402


EXPECTED = [
    ("Les Démons de Minuit", "Image"),
    ("Voyage, Voyage", "Desireless"),
    ("Joe le taxi", "Vanessa Paradis"),
    ("Ella, elle l'a", "France Gall"),
    ("En rouge et noir", "Jeanne Mas"),
    ("L'Aziza", "Daniel Balavoine"),
    ("Marcia Baila", "Rita Mitsouko"),
    ("C'est comme ça", "Rita Mitsouko"),
    ("Nuit de folie", "Début de Soirée"),
    ("Ouragan", "Stéphanie"),
    ("Besoin de rien, envie de toi", "Peter et Sloane"),
    ("Une autre histoire", "Gérard Blanc"),
    ("Le géant de papier", "Jean-Jacques Lafon"),
    ("Femme libérée", "Cookie Dingler"),
    ("Partenaire Particulier", "Partenaire Particulier"),
]


def find_matches_in_list(expected, tracks):
    found = {}
    for etitle, eartist in expected:
        matched = []
        for t in tracks:
            if etitle.lower() in t.get('title', '').lower() and eartist.lower() in t.get('artist', '').lower():
                matched.append(t)
        found[(etitle, eartist)] = matched
    return found


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plex-db', default='', help='chemin vers la base Plex')
    p.add_argument('--detailed', action='store_true', help='exporter les pistes sans année dans un CSV')
    p.add_argument('--enrich', action='store_true', help='tenter d enrichir les métadonnées via MusicBrainz (limité, respect rate-limit)')
    p.add_argument('--max-enrich-calls', type=int, default=60, help='nombre max d appels MusicBrainz lors de l enrichissement')
    args = p.parse_args()

    # Default DB path
    plex_db = args.plex_db or "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

    gen = app.PlexAmpAutoPlaylist(plex_db, verbose=False)
    print("Chargement des pistes depuis la DB (peut prendre un moment)...")
    tracks = gen.get_track_data()
    print(f"{len(tracks)} pistes chargées depuis la DB.")

    if args.enrich:
        # Limiter aux artistes récemment ajoutés pour éviter trop d'appels
        artists_to_try = ['vanessa paradis', 'jean-jacques lafon', 'gérard blanc']
        print(f"Tentative d'enrichissement via MusicBrainz pour quelques artistes (limité, max={args.max_enrich_calls})...")
        n = gen.enrich_tracks_years(tracks, artists_filter=artists_to_try, max_calls=args.max_enrich_calls)
        print(f"Enrichies en mémoire: {n} pistes")

    stars = gen.create_stars80_playlist(tracks)
    if not stars:
        print("Aucune playlist Stars 80 créée.")
        return 1
    name, items = list(stars.items())[0]
    print(f"Playlist générée: {name} — {len(items)} titres")

    # Simplified report: list first 50 titles
    print("\nExtrait (50 premiers):")
    for i, t in enumerate(items[:50], 1):
        print(f"{i:02d}. {t.get('title')} — {t.get('artist')} ({int((t.get('duration_ms') or 0)/1000)}s)")

    if args.detailed:
        # Collect tracks without a usable year
        no_year = []
        for t in items:
            year = None
            try:
                year = int(t.get('year') or 0) or None
            except Exception:
                year = None
            if not year:
                if t.get('release_date'):
                    try:
                        y2 = int(str(t.get('release_date'))[:4])
                        if y2:
                            year = y2
                    except Exception:
                        pass
            if year is None:
                no_year.append(t)

        reports_dir = Path(__file__).parent / 'reports'
        reports_dir.mkdir(exist_ok=True)
        out_path = reports_dir / 'stars80_unknown_years.csv'
        import csv
        with open(out_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['id', 'title', 'artist', 'album', 'duration_s', 'file_path'])
            for t in no_year:
                writer.writerow([t.get('id'), t.get('title'), t.get('artist'), t.get('album'), int((t.get('duration_ms') or 0)/1000), t.get('file_path')])

        print(f"\nRapport détaillé: {len(no_year)} pistes sans année exportées vers {out_path}")
        print("Extrait (50 premiers du rapport):")
        for i, t in enumerate(no_year[:50], 1):
            print(f"{i:02d}. {t.get('id')} — {t.get('title')} — {t.get('artist')} — {t.get('album')} ({int((t.get('duration_ms') or 0)/1000)}s)")

    # Compare expected
    matches = find_matches_in_list(EXPECTED, items)
    print("\nComparaison avec la liste attendue:")
    for (et, ea), m in matches.items():
        if m:
            print(f"  ✓ Trouvé: {et} — {ea} (versions: {len(m)})")
        else:
            # rechercher dans DB si present mais filtré
            present_elsewhere = [t for t in tracks if et.lower() in t.get('title','').lower() and ea.lower() in t.get('artist','').lower()]
            if present_elsewhere:
                reasons = []
                for t in present_elsewhere:
                    if (t.get('duration_ms') or 0) < 120000:
                        reasons.append('durée < 120s')
                    else:
                        reasons.append('filtré par matching')
                print(f"  ⚠️ Présent mais non retenu: {et} — {ea} (raisons: {', '.join(set(reasons))})")
            else:
                print(f"  ❌ Non trouvé en DB: {et} — {ea}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
