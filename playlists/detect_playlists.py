#!/usr/bin/env python3
"""
detect_playlists.py — Outil CLI de diagnostic pour la détection de playlists.

S'appuie sur playlist_detector.py pour offrir une boîte à outils de débogage :

  scan      Scanner un dossier et lister les playlists détectées
  analyze   Parser une playlist et afficher son contenu (encodage, entrées, tags)
  match     Tester le matching des entrées contre une bibliothèque musicale
  plex      Lister les playlists côté Plex (audio/video/photo, smart incluses)
  stats     Statistiques agrégées sur un dossier de playlists

Exemples :

  # Scanner récursivement
  python3 detect_playlists.py scan ~/Musiques

  # Analyser une playlist douteuse (encodage, chemins)
  python3 detect_playlists.py analyze "/mnt/MyBook/playlists/Été 2024.m3u"

  # Tester le taux de matching
  python3 detect_playlists.py match ~/playlists/fete.m3u --library ~/Musiques

  # Lister TOUTES les playlists Plex (y compris smart)
  python3 detect_playlists.py plex --url http://127.0.0.1:32400 --token XXX

  # Statistiques sur un dossier (formats, encodages, entrées, cassées)
  python3 detect_playlists.py stats /mnt/MyBook/playlists --library ~/Musiques
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

# Permettre l'import quand lancé directement
sys.path.insert(0, str(Path(__file__).parent))
from playlist_detector import (  # noqa: E402
    MusicIndex,
    PlexPlaylistClient,
    discover_playlists,
    parse_playlist,
    resolve_entry_path,
)


# ---------- couleurs ----------
def c(code: str, s: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"

GREEN = lambda s: c("32", s)   # noqa: E731
YELLOW = lambda s: c("33", s)  # noqa: E731
RED = lambda s: c("31", s)     # noqa: E731
BLUE = lambda s: c("34", s)    # noqa: E731
BOLD = lambda s: c("1", s)     # noqa: E731


# =============================================================================
#                               Commandes
# =============================================================================

def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser()
    if not root.exists():
        print(RED(f"❌ Introuvable : {root}"))
        return 1

    files = discover_playlists(
        root,
        recursive=not args.no_recursive,
        max_depth=args.max_depth,
        follow_symlinks=args.follow_symlinks,
        excludes=set(args.exclude or []),
        progress=args.progress,
    )

    print(BOLD(f"\n{len(files)} playlists détectées sous {root}\n"))
    by_ext: Counter = Counter(f.suffix.lower() for f in files)
    for ext, n in by_ext.most_common():
        print(f"  {ext or '(aucune)':8} {n:>5}")
    print()

    if args.list or args.verbose:
        for f in files:
            try:
                rel = f.relative_to(root)
            except ValueError:
                rel = f
            print(f"  {rel}")

    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    if not path.is_file():
        print(RED(f"❌ Fichier introuvable : {path}"))
        return 1

    pl = parse_playlist(path)
    print(BOLD(f"\n📄 {pl.path}"))
    print(f"  Format    : {BLUE(pl.format)}")
    print(f"  Encodage  : {BLUE(pl.encoding)}")
    print(f"  Nom       : {pl.name}")
    print(f"  Entrées   : {pl.track_count}")

    # Diagnostics
    resolved = 0
    missing = 0
    for e in pl.entries:
        p = resolve_entry_path(e.path_or_uri, pl.path)
        if p:
            resolved += 1
            if not p.exists():
                missing += 1

    print(f"  Résolues  : {resolved}/{pl.track_count}")
    print(f"  Manquantes sur disque : {RED(str(missing)) if missing else GREEN('0')}")
    print()

    limit = args.limit or 20
    for i, e in enumerate(pl.entries[:limit], 1):
        p = resolve_entry_path(e.path_or_uri, pl.path)
        exists = p.exists() if p else False
        mark = GREEN("✓") if exists else RED("✗")
        meta = ""
        if e.artist or e.title:
            meta = f"  [{e.artist or '?'} — {e.title or '?'}]"
        if e.duration:
            meta += f"  ({e.duration}s)"
        print(f"  {mark} {i:3}. {e.path_or_uri}{meta}")

    if pl.track_count > limit:
        print(f"  ... (+{pl.track_count - limit} entrées)")
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    lib = Path(args.library).expanduser()
    if not path.is_file():
        print(RED(f"❌ Playlist introuvable : {path}"))
        return 1
    if not lib.is_dir():
        print(RED(f"❌ Bibliothèque introuvable : {lib}"))
        return 1

    print(BOLD(f"📚 Indexation de {lib}..."))
    index = MusicIndex.build(lib, read_tags=args.tags, progress=True)
    print(f"   {len(index)} pistes indexées\n")

    pl = parse_playlist(path)
    print(BOLD(f"🎵 {pl.name} ({pl.track_count} entrées, format {pl.format})\n"))

    mappings = [tuple(m.split("=", 1)) for m in (args.map or []) if "=" in m]

    by_strategy: Counter = Counter()
    unmatched: list = []
    for i, e in enumerate(pl.entries, 1):
        result = index.match(e, playlist_path=pl.path, path_mappings=mappings)
        if result:
            by_strategy[result.strategy] += 1
            if args.verbose:
                print(f"  {GREEN('✓')} {i:3}. [{result.strategy:20}] "
                      f"{result.confidence:.2f}  →  {result.track.path.name}")
        else:
            unmatched.append(e)
            if args.verbose:
                label = e.title or Path(e.path_or_uri).name
                print(f"  {RED('✗')} {i:3}. NO MATCH  {label}")

    total = pl.track_count
    matched = total - len(unmatched)
    rate = 100 * matched / total if total else 0

    print(f"\n{BOLD('Résultat')} : {matched}/{total} pistes matchées "
          f"({GREEN(f'{rate:.1f}%') if rate >= 90 else YELLOW(f'{rate:.1f}%') if rate >= 70 else RED(f'{rate:.1f}%')})")
    for strat, n in by_strategy.most_common():
        print(f"  {strat:25} {n:>5}")

    if unmatched and not args.verbose:
        print(f"\n{YELLOW('Premières non matchées :')}")
        for e in unmatched[:10]:
            label = e.title or Path(e.path_or_uri).name
            print(f"  ✗ {label}")
        if len(unmatched) > 10:
            print(f"  ... (+{len(unmatched) - 10})")
    return 0 if rate >= 50 else 2


def cmd_plex(args: argparse.Namespace) -> int:
    client = PlexPlaylistClient(args.url, args.token)
    try:
        playlists = client.list_all(
            types=args.types.split(",") if args.types else ("audio", "video", "photo"),
            include_smart=not args.no_smart,
        )
    except Exception as exc:
        print(RED(f"❌ Erreur Plex : {exc}"))
        return 1

    if not playlists:
        print(YELLOW("Aucune playlist trouvée."))
        return 0

    # Tri
    playlists.sort(key=lambda p: (p.type, p.title.casefold()))

    print(BOLD(f"\n🎧 {len(playlists)} playlists Plex\n"))
    print(f"  {'TYPE':6} {'SMART':5} {'COUNT':>6}  {'DURATION':>9}  TITRE")
    print(f"  {'-'*6} {'-'*5} {'-'*6}  {'-'*9}  {'-'*40}")
    for pl in playlists:
        dur_min = pl.duration_ms // 60000 if pl.duration_ms else 0
        smart_mark = "★" if pl.smart else " "
        print(f"  {pl.type:6} {smart_mark:5} {pl.leaf_count:>6}  {dur_min:>6} min  {pl.title}")

    # Stats
    by_type: Counter = Counter(p.type for p in playlists)
    smart_count = sum(1 for p in playlists if p.smart)
    print(f"\n  Par type : " + ", ".join(f"{t}={n}" for t, n in by_type.most_common()))
    print(f"  Smart    : {smart_count}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser()
    if not root.is_dir():
        print(RED(f"❌ Dossier introuvable : {root}"))
        return 1

    files = discover_playlists(root, progress=args.progress)
    if not files:
        print(YELLOW("Aucune playlist détectée."))
        return 0

    print(BOLD(f"\n📊 Statistiques sur {len(files)} playlists sous {root}\n"))

    by_format: Counter = Counter()
    by_encoding: Counter = Counter()
    total_entries = 0
    total_broken = 0
    errored: list[tuple[Path, str]] = []

    index: MusicIndex | None = None
    if args.library:
        print(BOLD(f"📚 Indexation de {args.library}..."))
        index = MusicIndex.build(Path(args.library).expanduser(), progress=True)
        print(f"   {len(index)} pistes indexées\n")

    total_matched = 0

    for f in files:
        try:
            pl = parse_playlist(f)
        except Exception as exc:
            errored.append((f, str(exc)))
            continue
        by_format[pl.format] += 1
        by_encoding[pl.encoding] += 1
        total_entries += pl.track_count

        for e in pl.entries:
            p = resolve_entry_path(e.path_or_uri, pl.path)
            if p and not p.exists():
                total_broken += 1
            if index:
                if index.match(e, playlist_path=pl.path):
                    total_matched += 1

    def _show(label: str, counter: Counter) -> None:
        print(BOLD(label))
        for k, n in counter.most_common():
            print(f"  {k:20} {n:>5}")
        print()

    _show("Formats :", by_format)
    _show("Encodages :", by_encoding)

    print(BOLD("Entrées :"))
    print(f"  Total              {total_entries:>6}")
    print(f"  Chemins cassés     {RED(f'{total_broken:>6}') if total_broken else f'{total_broken:>6}'}")
    if index:
        rate = 100 * total_matched / total_entries if total_entries else 0
        print(f"  Matchées en biblio {GREEN(f'{total_matched:>6}')} ({rate:.1f}%)")
    print()

    if errored:
        print(RED(f"⚠️  {len(errored)} playlists en erreur :"))
        for f, msg in errored[:10]:
            print(f"  - {f}: {msg}")
        if len(errored) > 10:
            print(f"  ... (+{len(errored) - 10})")

    return 0


# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="detect_playlists.py",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Voir le docstring du script pour plus d'exemples.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # scan
    s = sub.add_parser("scan", help="Scanner un dossier")
    s.add_argument("path")
    s.add_argument("--no-recursive", action="store_true")
    s.add_argument("--max-depth", type=int, default=0)
    s.add_argument("--follow-symlinks", action="store_true")
    s.add_argument("--exclude", action="append", help="Nom de dossier à ignorer (répétable)")
    s.add_argument("--list", action="store_true", help="Lister les chemins")
    s.add_argument("--verbose", "-v", action="store_true")
    s.add_argument("--progress", action="store_true")

    # analyze
    a = sub.add_parser("analyze", help="Parser une playlist")
    a.add_argument("path")
    a.add_argument("--limit", type=int, default=20)

    # match
    m = sub.add_parser("match", help="Tester le matching")
    m.add_argument("path")
    m.add_argument("--library", required=True, help="Racine de la biblio musicale")
    m.add_argument("--tags", action="store_true", help="Lire les tags ID3 (lent, nécessite mutagen)")
    m.add_argument("--map", action="append", help="Mapping de préfixe src=dst (répétable)")
    m.add_argument("--verbose", "-v", action="store_true")

    # plex
    pp = sub.add_parser("plex", help="Lister les playlists Plex")
    pp.add_argument("--url", default=os.environ.get("PLEX_URL", "http://127.0.0.1:32400"))
    pp.add_argument("--token", default=os.environ.get("PLEX_TOKEN", ""))
    pp.add_argument("--types", default="audio,video,photo")
    pp.add_argument("--no-smart", action="store_true")

    # stats
    st = sub.add_parser("stats", help="Stats sur un dossier de playlists")
    st.add_argument("path")
    st.add_argument("--library", help="Biblio pour mesurer le taux de matching")
    st.add_argument("--progress", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "scan": cmd_scan,
        "analyze": cmd_analyze,
        "match": cmd_match,
        "plex": cmd_plex,
        "stats": cmd_stats,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
