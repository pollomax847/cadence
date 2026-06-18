#!/usr/bin/env python3
"""
Supprime les ratings 2★ via l'API HTTP Plex.
Compatible Docker (pas besoin d'accès direct à la DB).

Usage:
    python3 ratings/clear_2star_ratings_api.py           # mode réel
    python3 ratings/clear_2star_ratings_api.py --dry-run # simulation
    python3 ratings/clear_2star_ratings_api.py --rating 3  # autre note (défaut: 2)
"""

import os
import sys
import argparse
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

PLEX_URL   = os.environ.get("PLEX_URL",   "http://host.docker.internal:32400").rstrip("/")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")

# Correspondance étoiles → valeur Plex (échelle 0-10 : 1★=2, 2★=4, 3★=6, 4★=8, 5★=10)
STARS_TO_PLEX = {1: 2, 2: 4, 3: 6, 4: 8, 5: 10}


def _plex_req(method: str, path: str, extra_params: dict | None = None) -> ET.Element | int:
    params: dict[str, str] = {"X-Plex-Token": PLEX_TOKEN}
    if extra_params:
        params.update(extra_params)
    separator = "&" if "?" in path else "?"
    url = f"{PLEX_URL}{path}{separator}" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            if method == "GET" and data:
                return ET.fromstring(data)
            return r.status
    except urllib.error.HTTPError as e:
        print(f"  ⚠️  HTTP {e.code} sur {path}", file=sys.stderr)
        return e.code


def plex_get(path: str) -> ET.Element:
    result = _plex_req("GET", path)
    assert isinstance(result, ET.Element), f"Réponse inattendue pour GET {path}"
    return result


def plex_put(path: str, params: dict | None = None) -> int:
    result = _plex_req("PUT", path, params)
    return result if isinstance(result, int) else 200


def get_audio_sections() -> list[dict]:
    root = plex_get("/library/sections")
    return [
        {"key": s.get("key"), "title": s.get("title", "?")}
        for s in root.findall(".//Directory")
        if s.get("type") == "artist"
    ]


def get_tracks_with_rating(section_key: str, plex_rating: int) -> list[dict]:
    """Retourne les tracks ayant exactement plex_rating (valeur 0-10)."""
    root = plex_get(f"/library/sections/{section_key}/all?type=10&userRating={plex_rating}")
    tracks = []
    for t in root.findall(".//Track"):
        rk = t.get("ratingKey")
        if rk:
            tracks.append({
                "ratingKey": rk,
                "title":     t.get("title", "?"),
                "artist":    t.get("grandparentTitle", "?"),
                "album":     t.get("parentTitle", "?"),
            })
    return tracks


def clear_rating(rating_key: str) -> bool:
    """Remet la note à 0 (= non noté) via l'API Plex."""
    status = plex_put(
        "/:/rate",
        {"key": rating_key, "identifier": "com.plexapp.plugins.library", "rating": "0"},
    )
    return status in (200, 204, 201)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Supprime les ratings d'une note cible dans Plex via l'API HTTP."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulation : aucune modification effectuée")
    parser.add_argument("--rating", type=int, default=2, choices=[1, 2, 3, 4, 5],
                        help="Note à effacer (étoiles, défaut: 2)")
    args = parser.parse_args()

    if not PLEX_TOKEN:
        print("❌ PLEX_TOKEN non défini (variable d'environnement manquante)", file=sys.stderr)
        sys.exit(1)

    plex_rating = STARS_TO_PLEX[args.rating]
    mode_label = "SIMULATION" if args.dry_run else "RÉEL"

    print(f"🧹 Nettoyage des ratings {args.rating}★ via API Plex [{mode_label}]")
    print(f"   URL  : {PLEX_URL}")
    print(f"   Cible: userRating={plex_rating} ({args.rating} étoiles dans Plex)")
    print("=" * 60)

    sections = get_audio_sections()
    if not sections:
        print("❌ Aucune section audio trouvée dans Plex.")
        sys.exit(1)

    total_found   = 0
    total_cleared = 0
    total_errors  = 0

    for section in sections:
        key   = section["key"]
        title = section["title"]
        print(f"\n📚 Section : {title} (key={key})")

        tracks = get_tracks_with_rating(key, plex_rating)
        total_found += len(tracks)
        print(f"   🎵 Tracks avec {args.rating}★ : {len(tracks)}")

        for track in tracks:
            rk     = track["ratingKey"]
            artist = track["artist"]
            name   = track["title"]
            album  = track["album"]

            if args.dry_run:
                print(f"   🔎 [SIMULATION] {artist} — {name} ({album})")
            else:
                ok = clear_rating(rk)
                if ok:
                    print(f"   ✅ Rating effacé : {artist} — {name} ({album})")
                    total_cleared += 1
                else:
                    print(f"   ❌ Erreur API    : {artist} — {name} ({album})")
                    total_errors += 1

    print("\n" + "=" * 60)
    if args.dry_run:
        print(f"ℹ️  Simulation terminée : {total_found} track(s) {args.rating}★ trouvée(s). Aucune modification.")
    else:
        print(f"✅ Terminé : {total_cleared} rating(s) effacé(s), {total_errors} erreur(s).")
        if total_cleared > 0:
            print(f"   La playlist '[Auto] ⭐ {args.rating} étoiles' disparaîtra au prochain scan Plex.")


if __name__ == "__main__":
    main()
