#!/usr/bin/env python3
"""
detect_plex_token.py — Détecte automatiquement le token Plex local.

Lit PlexOnlineToken depuis Preferences.xml (plusieurs chemins testés selon
le type d'installation : snap / deb / flatpak / docker-host / NAS).

Usage :
    python3 detect_plex_token.py                   # affiche le token
    python3 detect_plex_token.py --env             # format KEY=VAL
    python3 detect_plex_token.py --write-env .env  # met à jour .env
    python3 detect_plex_token.py --json            # JSON {token, source, url}
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

# Ordre de priorité : snap → deb → flatpak → user → docker volumes standards
CANDIDATES = [
    "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Preferences.xml",
    "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml",
    "~/.var/app/tv.plex.PlexMediaServer/config/Library/Application Support/Plex Media Server/Preferences.xml",
    "~/Library/Application Support/Plex Media Server/Preferences.xml",  # macOS
    "/config/Library/Application Support/Plex Media Server/Preferences.xml",  # linuxserver docker
    "/plex/Preferences.xml",  # monté dans nos conteneurs
]

TOKEN_RE = re.compile(r'PlexOnlineToken="([^"]+)"')


def find_token(extra_paths: list[str] | None = None) -> tuple[str, Path] | None:
    paths = [Path(os.path.expanduser(p)) for p in (extra_paths or []) + CANDIDATES]
    for p in paths:
        try:
            if p.is_file():
                content = p.read_text(encoding="utf-8", errors="replace")
                m = TOKEN_RE.search(content)
                if m:
                    return m.group(1), p
        except PermissionError:
            print(f"⚠ {p} : permission refusée (essayer avec sudo)", file=sys.stderr)
    return None


def update_env_file(env_path: Path, token: str, url: str = "http://127.0.0.1:32400") -> bool:
    """Met à jour PLEX_TOKEN et PLEX_URL dans un .env (crée ou remplace)."""
    lines: list[str] = []
    seen = {"PLEX_TOKEN": False, "PLEX_URL": False}
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("PLEX_TOKEN="):
                lines.append(f"PLEX_TOKEN={token}")
                seen["PLEX_TOKEN"] = True
            elif s.startswith("PLEX_URL=") and not s.startswith("#"):
                lines.append(f"PLEX_URL={url}")
                seen["PLEX_URL"] = True
            else:
                lines.append(line)
    if not seen["PLEX_TOKEN"]:
        lines.append(f"PLEX_TOKEN={token}")
    if not seen["PLEX_URL"]:
        lines.append(f"PLEX_URL={url}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--extra", action="append",
                   help="Chemin supplémentaire vers un Preferences.xml")
    p.add_argument("--env", action="store_true", help="Format KEY=VAL")
    p.add_argument("--json", action="store_true", help="Sortie JSON")
    p.add_argument("--write-env", metavar="FILE",
                   help="Écrit/remplace PLEX_TOKEN et PLEX_URL dans ce fichier .env")
    p.add_argument("--url", default="http://127.0.0.1:32400",
                   help="URL Plex associée (défaut http://127.0.0.1:32400)")
    args = p.parse_args(argv)

    found = find_token(args.extra)
    if not found:
        print("❌ Token Plex introuvable. Chemins testés :", file=sys.stderr)
        for c in (args.extra or []) + CANDIDATES:
            print(f"   - {c}", file=sys.stderr)
        print("\nAstuce :", file=sys.stderr)
        print("  sudo cat '/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Preferences.xml' | grep -oE 'PlexOnlineToken=\"[^\"]+\"'", file=sys.stderr)
        return 1

    token, source = found

    if args.write_env:
        update_env_file(Path(args.write_env), token, args.url)
        print(f"✓ {args.write_env} mis à jour (source : {source})")
        return 0

    if args.json:
        print(json.dumps({"token": token, "source": str(source), "url": args.url}))
    elif args.env:
        print(f"PLEX_TOKEN={token}")
        print(f"PLEX_URL={args.url}")
    else:
        print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
