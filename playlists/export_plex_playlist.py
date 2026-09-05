#!/usr/bin/env python3
"""
Exporter des playlists Plex vers un disque externe en format compilation.

Organisation des fichiers :
  <destination>/<Nom Playlist>/XX - Artiste - Titre.ext

Fonctionnalités :
  - Lister les playlists audio Plex
  - Exporter une ou plusieurs playlists
  - Exporter TOUTES les playlists d'un coup
  - Télécharger les fichiers manquants avec yt-dlp (optionnel)
  - Mode simulation (dry-run) par défaut
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


PLEX_URL = os.environ.get("PLEX_URL", "http://127.0.0.1:32400")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")


def plex_request(url: str, token: str) -> bytes:
    req = urllib.request.Request(url=url, method="GET")
    req.add_header("X-Plex-Token", token)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def list_playlists(plex_url: str, token: str) -> list[dict]:
    root = ET.fromstring(plex_request(f"{plex_url.rstrip('/')}/playlists", token))
    playlists = []
    for item in root.findall("Playlist") + root.findall("Directory"):
        if item.attrib.get("playlistType") != "audio":
            continue
        leaf_count_raw = item.attrib.get("leafCount")
        leaf_count = int(leaf_count_raw) if leaf_count_raw and leaf_count_raw.isdigit() else 0
        playlists.append({
            "title": item.attrib.get("title", ""),
            "rating_key": item.attrib.get("ratingKey", ""),
            "leaf_count": leaf_count,
        })
    return playlists


def get_playlist_tracks(plex_url: str, token: str, rating_key: str) -> list[dict]:
    root = ET.fromstring(
        plex_request(f"{plex_url.rstrip('/')}/playlists/{rating_key}/items", token)
    )
    tracks = []
    for item in root.findall("Track") + root.findall("Metadata"):
        artist = item.attrib.get("grandparentTitle", item.attrib.get("originalTitle", ""))
        title = item.attrib.get("title", "")
        part = item.find(".//Part")
        file_path = part.attrib.get("file", "") if part is not None else ""
        tracks.append({
            "artist": artist,
            "title": title,
            "file": file_path,
        })
    return tracks


def sanitize_filename(name: str) -> str:
    """Nettoyer un nom pour l'utiliser comme nom de fichier."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip('. ')
    return name[:200]


def export_playlist(
    plex_url: str,
    token: str,
    playlist: dict,
    dest_base: str,
    use_ytdlp: bool = False,
    dry_run: bool = True,
) -> dict:
    """Exporter une playlist vers le disque."""
    title = playlist["title"]
    rating_key = playlist["rating_key"]
    folder_name = sanitize_filename(title)
    dest_dir = os.path.join(dest_base, folder_name)

    tracks = get_playlist_tracks(plex_url, token, rating_key)

    stats = {"title": title, "total": len(tracks), "copied": 0, "downloaded": 0, "missing": 0, "errors": []}

    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)

    for i, track in enumerate(tracks, 1):
        artist = sanitize_filename(track["artist"]) or "Unknown Artist"
        track_title = sanitize_filename(track["title"]) or "Unknown Track"
        src = track["file"]

        if src and os.path.isfile(src):
            ext = os.path.splitext(src)[1]
            dest_name = f"{i:02d} - {artist} - {track_title}{ext}"
            dest_path = os.path.join(dest_dir, dest_name)

            if dry_run:
                print(f"  [COPIE] {dest_name}")
                stats["copied"] += 1
            elif not os.path.exists(dest_path):
                try:
                    shutil.copy2(src, dest_path)
                    stats["copied"] += 1
                except Exception as e:
                    stats["errors"].append(f"{dest_name}: {e}")
            else:
                stats["copied"] += 1  # déjà présent

        elif use_ytdlp and track["artist"] and track["title"]:
            dest_name = f"{i:02d} - {artist} - {track_title}.m4a"
            dest_path = os.path.join(dest_dir, dest_name)
            search_query = f"{track['artist']} {track['title']}"

            if dry_run:
                print(f"  [YT-DLP] {dest_name} (recherche: {search_query})")
                stats["downloaded"] += 1
            elif not os.path.exists(dest_path):
                try:
                    result = subprocess.run(
                        [
                            "yt-dlp", "-x",
                            "--audio-format", "m4a",
                            "--audio-quality", "0",
                            "-o", dest_path.replace(".m4a", ".%(ext)s"),
                            f"ytsearch1:{search_query}",
                        ],
                        capture_output=True, text=True, timeout=120,
                    )
                    if result.returncode == 0:
                        stats["downloaded"] += 1
                    else:
                        stats["errors"].append(f"{dest_name}: yt-dlp error: {result.stderr[:200]}")
                        stats["missing"] += 1
                except Exception as e:
                    stats["errors"].append(f"{dest_name}: {e}")
                    stats["missing"] += 1
            else:
                stats["downloaded"] += 1  # déjà là
        else:
            dest_name = f"{i:02d} - {artist} - {track_title}"
            if dry_run:
                print(f"  [MANQUANT] {dest_name} (source: {src})")
            stats["missing"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Exporter des playlists Plex vers un disque externe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Lister les playlists
  %(prog)s --list

  # Simulation d'export d'une playlist
  %(prog)s --playlist "Stars 80" --dest /media/paulceline/MUSIC

  # Exporter pour de vrai avec téléchargement des manquants
  %(prog)s --playlist "Stars 80" --dest /media/paulceline/MUSIC --go --ytdlp

  # Exporter plusieurs playlists
  %(prog)s --playlist "Stars 80" --playlist "Chill" --dest /media/paulceline/MUSIC --go

  # Exporter TOUTES les playlists
  %(prog)s --all --dest /media/paulceline/MUSIC --go
""",
    )
    parser.add_argument("--list", action="store_true", help="Lister les playlists Plex")
    parser.add_argument("--playlist", action="append", default=[], help="Nom de la playlist à exporter (peut être répété)")
    parser.add_argument("--all", action="store_true", help="Exporter toutes les playlists")
    parser.add_argument("--dest", default="/media/paulceline/MUSIC", help="Dossier de destination (défaut: /media/paulceline/MUSIC)")
    parser.add_argument("--ytdlp", action="store_true", help="Télécharger les fichiers manquants avec yt-dlp")
    parser.add_argument("--go", action="store_true", help="Exécuter réellement (sans --go = simulation)")
    parser.add_argument("--plex-url", default=PLEX_URL, help="URL Plex")
    parser.add_argument("--plex-token", default=PLEX_TOKEN, help="Token Plex")

    args = parser.parse_args()
    dry_run = not args.go

    # Lister les playlists
    playlists = list_playlists(args.plex_url, args.plex_token)

    if args.list:
        print(f"\n{'#':>3}  {'Pistes':>6}  Playlist")
        print(f"{'─'*3}  {'─'*6}  {'─'*50}")
        for i, p in enumerate(sorted(playlists, key=lambda x: x["title"].lower()), 1):
            print(f"{i:>3}  {p['leaf_count']:>6}  {p['title']}")
        print(f"\nTotal : {len(playlists)} playlists")
        return

    # Sélectionner les playlists à exporter
    if args.all:
        to_export = playlists
    elif args.playlist:
        to_export = []
        for name in args.playlist:
            # Match exact d'abord
            matched = [p for p in playlists if p["title"].lower() == name.lower()]
            if not matched:
                # Recherche partielle, mais demander confirmation si plusieurs résultats
                matched = [p for p in playlists if name.lower() in p["title"].lower()]
                if len(matched) > 1:
                    print(f"⚠ '{name}' correspond à {len(matched)} playlists :")
                    for j, m in enumerate(matched, 1):
                        print(f"  {j}. {m['title']} ({m['leaf_count']} pistes)")
                    try:
                        choice = input("Numéro(s) à exporter (ex: 1,3) ou 'all' ou Entrée pour annuler : ").strip()
                    except (EOFError, KeyboardInterrupt):
                        choice = ""
                    if choice.lower() == "all":
                        pass  # garder tous les matched
                    elif choice:
                        indices = [int(x.strip()) - 1 for x in choice.split(",") if x.strip().isdigit()]
                        matched = [matched[i] for i in indices if 0 <= i < len(matched)]
                    else:
                        matched = []
            if matched:
                to_export.extend(matched)
            else:
                print(f"⚠ Playlist non trouvée ou ignorée : '{name}'")
        if not to_export:
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

    # Vérifier la destination
    if not os.path.isdir(args.dest):
        print(f"Erreur : le dossier '{args.dest}' n'existe pas")
        sys.exit(1)

    mode = "SIMULATION" if dry_run else "EXPORT"
    print(f"\n{'='*60}")
    print(f"  Mode : {mode}")
    print(f"  Destination : {args.dest}")
    print(f"  Playlists : {len(to_export)}")
    print(f"  yt-dlp : {'oui' if args.ytdlp else 'non'}")
    print(f"{'='*60}\n")

    total_stats = {"copied": 0, "downloaded": 0, "missing": 0, "errors": 0}

    for playlist in sorted(to_export, key=lambda x: x["title"].lower()):
        print(f"📀 {playlist['title']} ({playlist['leaf_count']} pistes)")
        stats = export_playlist(
            args.plex_url, args.plex_token, playlist,
            args.dest, use_ytdlp=args.ytdlp, dry_run=dry_run,
        )
        total_stats["copied"] += stats["copied"]
        total_stats["downloaded"] += stats["downloaded"]
        total_stats["missing"] += stats["missing"]
        total_stats["errors"] += len(stats["errors"])

        status = f"  → Copiés: {stats['copied']}"
        if stats["downloaded"]:
            status += f" | Téléchargés: {stats['downloaded']}"
        if stats["missing"]:
            status += f" | Manquants: {stats['missing']}"
        if stats["errors"]:
            status += f" | Erreurs: {len(stats['errors'])}"
            for err in stats["errors"]:
                print(f"    ❌ {err}")
        print(status)
        print()

    print(f"{'='*60}")
    print(f"  RÉSUMÉ {'(SIMULATION)' if dry_run else ''}")
    print(f"  Copiés : {total_stats['copied']}")
    if total_stats["downloaded"]:
        print(f"  Téléchargés : {total_stats['downloaded']}")
    if total_stats["missing"]:
        print(f"  Manquants : {total_stats['missing']}")
    if total_stats["errors"]:
        print(f"  Erreurs : {total_stats['errors']}")
    print(f"{'='*60}")

    if dry_run:
        print("\n💡 Ajouter --go pour exécuter réellement")


if __name__ == "__main__":
    main()
