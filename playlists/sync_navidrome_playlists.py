#!/usr/bin/env python3
"""Copie les playlists M3U générées par Cadence vers le dossier scanné par Navidrome.

Les playlists (playlists/generated/*.m3u*) contiennent des chemins de titres
absolus. Navidrome doit tourner sur la même machine et voir ces mêmes chemins
pour pouvoir les résoudre — ce script ne fait que déposer les fichiers dans le
dossier que Navidrome surveille (ND_PLAYLISTSPATH ou un sous-dossier de sa
bibliothèque musicale), sans réécrire leur contenu.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def sync_playlists(source_dir: Path, dest_dir: Path, prune: bool, dry_run: bool) -> int:
    if not source_dir.is_dir():
        print(f"Dossier source introuvable: {source_dir}", file=sys.stderr)
        return 1

    source_files = sorted(
        p for p in list(source_dir.glob("*.m3u")) + list(source_dir.glob("*.m3u8"))
        if p.is_file()
    )
    if not source_files:
        print(f"Aucune playlist M3U trouvée dans {source_dir}")
        return 0

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for src in source_files:
        dst = dest_dir / src.name
        if dry_run:
            print(f"[dry-run] {src} -> {dst}")
            continue
        shutil.copy2(src, dst)
        copied += 1
    print(f"{copied} playlist(s) copiée(s) vers {dest_dir}")

    if prune and dest_dir.is_dir():
        keep = {p.name for p in source_files}
        removed = 0
        existing_files = list(dest_dir.glob("*.m3u")) + list(dest_dir.glob("*.m3u8"))
        for existing in existing_files:
            if existing.name in keep:
                continue
            if dry_run:
                print(f"[dry-run] suppression {existing}")
                continue
            existing.unlink()
            removed += 1
        if removed:
            print(f"{removed} ancienne(s) playlist(s) supprimée(s) de {dest_dir}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronise les playlists M3U générées vers le dossier surveillé par Navidrome."
    )
    parser.add_argument(
        "--source-dir",
        default=str(Path(__file__).parent / "generated"),
        help="Dossier contenant les .m3u générés par Cadence (défaut: playlists/generated)",
    )
    parser.add_argument(
        "--dest-dir",
        default=None,
        help="Dossier surveillé par Navidrome (défaut: $PLAYLISTS_DIR ou /playlists)",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Supprime dans la destination les playlists absentes de la source",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="N'écrit rien, affiche les actions prévues"
    )
    args = parser.parse_args()

    dest = args.dest_dir or os.environ.get("PLAYLISTS_DIR", "/playlists")
    return sync_playlists(Path(args.source_dir), Path(dest), args.prune, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
