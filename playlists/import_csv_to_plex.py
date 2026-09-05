#!/usr/bin/env python3
"""
Import a CSV playlist (YouTube Music / Soundiiz / Spotify export) into Plex.

Columns expected in CSV (flexible header names):
  title/name/song/track  — track title  (required)
  artist/artists         — artist name  (optional but improves matching)
  album                  — album name   (optional)

Matching strategy per track (in order):
  1. Exact normalised (title + artist)
  2. Exact normalised title-only (takes first candidate)
  3. Fuzzy title match (difflib, cutoff 0.82) filtered by artist if available

Usage (outside Docker):
  python3 playlists/import_csv_to_plex.py \
      --csv "/mnt/MyBook/itunes/Mojito Sunset.csv" \
      --playlist "🍹 Mojito sunset" \
      --plex-url http://localhost:32400 \
      --plex-token <TON_TOKEN_PLEX>

Usage (inside Docker via webui):
  --csv "/itunes/Mojito Sunset.csv"
  --plex-url http://host.docker.internal:32400
"""

import argparse
import csv
import difflib
import os
import re
import sqlite3
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Sequence

from plex_api import (
    default_plex_url,
    plex_create_audio_playlist,
    plex_delete_playlist,
    plex_find_playlist_rating_keys,
    plex_get_playlist_item_id_set,
    plex_machine_identifier,
    plex_request,
)
from text_normalization import normalize_ascii, normalize_unicode

# ─── defaults ────────────────────────────────────────────────────────────────

DEFAULT_PLEX_DB = Path(
    os.environ.get(
        "PLEX_DB",
        "/var/snap/plexmediaserver/common/Library/Application Support/"
        "Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db",
    )
)
DEFAULT_PLEX_URL   = os.environ.get("PLEX_URL", default_plex_url())
DEFAULT_PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")
DEFAULT_SLSKD_URL  = os.environ.get("SLSKD_URL",  "http://localhost:5030")
DEFAULT_SLSKD_KEY  = os.environ.get("SLSKD_API_KEY", "")

FUZZY_CUTOFF = 0.82  # SequenceMatcher ratio threshold

TrackRow = dict[str, str]
TitleArtistIndex = dict[tuple[str, str], int]
TitleIndex = dict[str, list[int]]
Candidate = tuple[str, str, int]
Candidates = list[Candidate]

# ─── Plex API helpers ─────────────────────────────────────────────────────────


def get_machine_id(plex_url: str, token: str) -> str:
    return plex_machine_identifier(plex_url, token)


def find_playlist_rating_keys(plex_url: str, token: str, title: str) -> list[str]:
    return plex_find_playlist_rating_keys(plex_url, token, title)


def delete_playlist(plex_url: str, token: str, rating_key: str) -> None:
    plex_delete_playlist(plex_url, token, rating_key)


def get_playlist_item_ids(plex_url: str, token: str, rating_key: str) -> set[int]:
    """Return the set of metadata item IDs already in a playlist."""
    return plex_get_playlist_item_id_set(plex_url, token, rating_key)


def create_playlist(
    plex_url: str, token: str, machine_id: str, title: str, track_ids: list[int]
) -> None:
    if not track_ids:
        print("  ⚠️  No tracks to add — playlist not created.", flush=True)
        return
    plex_create_audio_playlist(plex_url, token, title, track_ids, machine_id=machine_id, replace=True)
    print(f"  ✅ Playlist '{title}' créée/mise à jour avec {len(track_ids)} titres.", flush=True)


# ─── normalisation ─────────────────────────────────────────────────────────────


def norm(s: str) -> str:
    """NFKD → ASCII, lowercase, alphanumeric only."""
    return normalize_ascii(s)


def norm_u(s: str) -> str:
    """Lowercase, strip non-word chars but keep unicode (for non-Latin)."""
    return normalize_unicode(s)


# ─── DB helpers ───────────────────────────────────────────────────────────────


def copy_db(db_path: Path) -> Path:
    """Create a consistent read-only SQLite snapshot on a writable volume.
    /tmp is a size-limited tmpfs in the webui container (often 64MB), far smaller
    than a real Plex database, so the snapshot goes to /app/data when available."""
    temp_dir = os.environ.get("PLEX_CSV_IMPORT_TMPDIR") or (
        "/app/data" if os.path.isdir("/app/data") else tempfile.gettempdir()
    )
    os.makedirs(temp_dir, exist_ok=True)
    tmp = Path(tempfile.mkstemp(prefix="plex_csv_import_", suffix=".db", dir=temp_dir)[1])
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as src:
        with sqlite3.connect(str(tmp)) as dst:
            src.backup(dst)
    return tmp


def build_track_index(db_path: Path) -> tuple[TitleArtistIndex, TitleIndex, Candidates]:
    """
    Returns:
      ta_index:    {(norm_title, norm_artist): track_id}   exact match
      t_index:     {norm_title: [track_id, ...]}           title-only fallback
      candidates:  [(norm_title, norm_artist, track_id)]   for fuzzy search
    """
    ta_index: TitleArtistIndex = {}
    t_index: TitleIndex = {}
    candidates: Candidates = []

    def _decode_bytes(b: bytes) -> str:
        return b.decode("utf-8", errors="replace")

    with sqlite3.connect(str(db_path)) as conn:
        conn.text_factory = _decode_bytes
        conn.row_factory = sqlite3.Row
        rows: list[sqlite3.Row] = conn.execute(
            """
            SELECT mi.id, mi.title,
                   COALESCE(art.title, '') AS artist
            FROM   metadata_items mi
            LEFT JOIN metadata_items album ON mi.parent_id = album.id
            LEFT JOIN metadata_items art   ON album.parent_id = art.id
            WHERE  mi.metadata_type = 10
            """
        ).fetchall()

    for row in rows:
        tid = int(row["id"])
        nt = norm(row["title"])
        na = norm(row["artist"])
        if not nt:
            continue
        ta_index.setdefault((nt, na), tid)      # first occurrence wins
        t_index.setdefault(nt, []).append(tid)
        candidates.append((nt, na, tid))

    return ta_index, t_index, candidates


# ─── title cleaning (YouTube-style video titles) ──────────────────────────────

# Patterns to strip from title (case-insensitive)
_STRIP_PATTERNS = [
    re.compile(r"\(official\s+(?:music\s+)?(?:video|audio|clip|hd|4k)\)", re.I),
    re.compile(r"\[official\s+(?:music\s+)?(?:video|audio|clip|hd|4k)\]", re.I),
    re.compile(r"\(official\)", re.I),
    re.compile(r"\[official\]", re.I),
    re.compile(r"\(music\s+video\)", re.I),
    re.compile(r"\[music\s+video\]", re.I),
    re.compile(r"\(lyric(?:s)?\s+video\)", re.I),
    re.compile(r"\[lyric(?:s)?\s+video\]", re.I),
    re.compile(r"\(visualizer\)", re.I),
    re.compile(r"\(hq\)", re.I),
    re.compile(r"\[hq\]", re.I),
    re.compile(r"\s*\bft\.\s+[^()\[\]]+", re.I),
    re.compile(r"\s*\bfeat\.\s+[^()\[\]]+", re.I),
    # Video description suffixes like "// official video", "- official video"
    re.compile(r"\s*[/\-–—]+\s*(?:official\s+)?(?:video|audio|clip|lyric)", re.I),
    # Label/channel suffixes like "[Suara]", "[Armada Music]"
    re.compile(r"\[(?:[A-Z][a-z]+\s*)+\]"),
]
# Remix/edit variants in parentheses — stripped as secondary pass
_STRIP_VARIANTS = re.compile(
    r"[\(\[]\s*(?:[^()\[\]]+ )?"
    r"(?:radio\s+edit|mix\s+cut|single\s+version|album\s+version|"
    r"extended\s+(?:mix|version)|original\s+mix|club\s+mix|"
    r"videoclip|video\s+clip)\s*[\)\]]",
    re.I,
)
_BRACKET_FEAT = re.compile(r"[\(\[]\s*feat[.\s][^\)\]]+[\)\]]", re.I)
_BRACKET_FT   = re.compile(r"[\(\[]\s*ft[.\s][^\)\]]+[\)\]]", re.I)


def clean_title(title: str) -> str:
    """Strip video/feat/variant decorations from a YouTube-style title."""
    t = title
    t = _BRACKET_FEAT.sub("", t)
    t = _BRACKET_FT.sub("", t)
    for pat in _STRIP_PATTERNS:
        t = pat.sub("", t)
    t = _STRIP_VARIANTS.sub("", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—[]()_")
    return t


def split_artist_dash_title(title: str, csv_artist: str) -> tuple[Optional[str], Optional[str]]:
    """
    If title looks like 'ARTIST - SONG', return (ARTIST, SONG).
    Returns (None, None) if pattern not detected.
    """
    if " - " not in title:
        return None, None
    parts = title.split(" - ", 1)
    left, right = parts[0].strip(), parts[1].strip()
    # heuristic: left part should be short-ish and look like an artist name
    if left and right and len(left) <= 60 and len(right) >= 2:
        return left, right
    return None, None



def _try_match_title_artist(
    title: str,
    artist: str,
    ta_index: TitleArtistIndex,
    t_index: TitleIndex,
    candidates: Candidates,
) -> Optional[int]:
    """Single-attempt exact/fuzzy match for a given (title, artist) pair."""
    nt = norm(title)
    na = norm(artist)
    if not nt:
        return None

    # exact (title + artist)
    if na and (nt, na) in ta_index:
        return ta_index[(nt, na)]

    # exact title-only
    if nt in t_index:
        if na:
            for cand_nt, cand_na, cand_id in candidates:
                if cand_nt == nt and cand_na == na:
                    return cand_id
        return t_index[nt][0]

    # fuzzy title
    unique_titles: list[str] = list({c[0] for c in candidates})
    close = difflib.get_close_matches(nt, unique_titles, n=5, cutoff=FUZZY_CUTOFF)
    if not close:
        return None

    best_id: Optional[int] = None
    best_ratio = 0.0
    for ct in close:
        title_ratio = difflib.SequenceMatcher(None, nt, ct).ratio()
        for cand_nt, cand_na, cand_id in candidates:
            if cand_nt != ct:
                continue
            if na and cand_na:
                a_ratio = difflib.SequenceMatcher(None, na, cand_na).ratio()
                combined = title_ratio * 0.7 + a_ratio * 0.3
            else:
                combined = title_ratio
            if combined > best_ratio:
                best_ratio = combined
                best_id = cand_id

    return best_id if best_ratio >= FUZZY_CUTOFF else None


def match_track(
    title: str,
    artist: str,
    ta_index: TitleArtistIndex,
    t_index: TitleIndex,
    candidates: Candidates,
) -> Optional[int]:
    """Try multiple title/artist variations to find a Plex track."""

    # 1 — raw title + artist
    tid = _try_match_title_artist(title, artist, ta_index, t_index, candidates)
    if tid is not None:
        return tid

    # 2 — clean title (strip "(Official Video)" etc.) + raw artist
    ct = clean_title(title)
    if ct and ct != title:
        tid = _try_match_title_artist(ct, artist, ta_index, t_index, candidates)
        if tid is not None:
            return tid

    # 3 — "ARTIST - TITLE" split: try extracted title with extracted artist
    emb_artist, emb_title = split_artist_dash_title(title, artist)
    if emb_title:
        clean_emb = clean_title(emb_title)
        extracted_artist = emb_artist or ""
        variations: list[tuple[str, str]] = [
            (emb_title, extracted_artist),
            (clean_emb, extracted_artist),
            (emb_title, artist),
            (clean_emb, artist),
        ]
        for t_try, a_try in variations:
            if not t_try:
                continue
            tid = _try_match_title_artist(t_try, a_try, ta_index, t_index, candidates)
            if tid is not None:
                return tid

    # 4 — clean title with no artist constraint (last resort)
    if ct and ct != title:
        tid = _try_match_title_artist(ct, "", ta_index, t_index, candidates)
        if tid is not None:
            return tid

    # 5 — strip ALL bracketed content and try again (very aggressive, last resort)
    bare = re.sub(r"[\(\[][^\)\]]*[\)\]]", "", title).strip(" -–—_")
    bare = re.sub(r"\s{2,}", " ", bare).strip()
    if bare and bare != title and bare != ct:
        for a_try in ([artist] if artist else []) + [""]:
            tid = _try_match_title_artist(bare, a_try, ta_index, t_index, candidates)
            if tid is not None:
                return tid

    return None


# ─── CSV parsing ──────────────────────────────────────────────────────────────

# Column aliases (lowercase)
_TITLE_COLS  = {"title", "name", "song", "track", "titre", "nom", "chanson"}
_ARTIST_COLS = {"artist", "artists", "artiste", "artistes", "performer"}
_ALBUM_COLS  = {"album"}


def _pick_col(fieldnames: Sequence[str], aliases: set[str]) -> Optional[str]:
    for f in fieldnames:
        if f.strip().lower() in aliases:
            return f
    return None


def read_csv(csv_path: Path) -> list[TrackRow]:
    """
    Returns list of dicts with 'title', 'artist', 'album' keys (may be empty strings).
    """
    tracks: list[TrackRow] = []
    with open(csv_path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        # Detect delimiter (comma or tab or semicolon)
        sample = fh.read(4096)
        fh.seek(0)
        sniffer = csv.Sniffer()
        try:
            dialect = sniffer.sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel  # default to comma

        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header row: {csv_path}")

        title_col  = _pick_col(reader.fieldnames, _TITLE_COLS)
        artist_col = _pick_col(reader.fieldnames, _ARTIST_COLS)
        album_col  = _pick_col(reader.fieldnames, _ALBUM_COLS)

        if not title_col:
            raise ValueError(
                f"No title column found in CSV. "
                f"Detected columns: {reader.fieldnames}. "
                f"Expected one of: {sorted(_TITLE_COLS)}"
            )

        for row in reader:
            row_dict: dict[str, str] = {
                str(k): (v or "")
                for k, v in row.items()
                if k is not None
            }
            t = row_dict.get(title_col, "").strip()
            a = row_dict.get(artist_col, "").strip() if artist_col else ""
            al = row_dict.get(album_col, "").strip() if album_col else ""
            if t:
                tracks.append({"title": t, "artist": a, "album": al})

    return tracks


# ─── slskd downloader ────────────────────────────────────────────────────────


def _run_slskd_downloads(
    missed: list[TrackRow], slskd_url: str, slskd_key: str, dry_run: bool
) -> None:
    """Search slskd for each missed track and queue the best result for download."""
    # Lazy import to avoid hard dependency when slskd is not used
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        from slskd_downloader import SlskdClient
    except ImportError as e:
        print(f"  ❌ Cannot import slskd_downloader: {e}", flush=True)
        return

    print(
        f"\n🎵 slskd: searching {len(missed)} missing track(s)…"
        f"{'  (DRY RUN — no downloads)' if dry_run else ''}",
        flush=True,
    )

    try:
        client = SlskdClient(url=slskd_url, api_key=slskd_key)
    except ValueError as e:
        print(f"  ❌ {e}", flush=True)
        return

    queued = 0
    failed = 0

    for row in missed:
        # Use cleaned title for search (strip YouTube suffixes)
        title  = clean_title(row["title"])
        artist = row["artist"]
        # If title still looks like "ARTIST - SONG", extract both
        emb_artist, emb_title = split_artist_dash_title(title, artist)
        if emb_title:
            if not artist:
                artist = emb_artist or ""
            title = clean_title(emb_title)

        label = f"{artist} — {title}" if artist else title
        print(f"  🔎 {label}", flush=True)

        candidates = client.search(title, artist)
        if not candidates:
            print("       ❌ no results", flush=True)
            failed += 1
            continue

        best = candidates[0]
        bn = best.filename.replace("\\", "/").rsplit("/", 1)[-1]
        print(
            f"       → {best.username}/{bn}"
            f"  [{best.extension or '?'}  {best.size // 1024 // 1024}MB"
            f"  score={best.score}]",
            flush=True,
        )

        if dry_run:
            queued += 1
            continue

        result = client.download(best)
        if result.queued:
            print("       ✅ queued", flush=True)
            queued += 1
        else:
            print(f"       ❌ {result.error}", flush=True)
            failed += 1

    verb = "would be queued" if dry_run else "queued"
    print(
        f"\n  slskd: {queued} {verb}, {failed} not found / failed.",
        flush=True,
    )


# ─── main ─────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Import a CSV playlist into Plex by matching title+artist."
    )
    p.add_argument("--csv", required=True, help="Path to the CSV file.")
    p.add_argument(
        "--playlist",
        help="Plex playlist name. Defaults to the CSV file stem.",
    )
    p.add_argument("--plex-url", default=DEFAULT_PLEX_URL)
    p.add_argument("--plex-token", default=DEFAULT_PLEX_TOKEN, required=not DEFAULT_PLEX_TOKEN)
    p.add_argument(
        "--plex-db",
        default=str(DEFAULT_PLEX_DB),
        help="Path to Plex SQLite DB (read-only copy will be made).",
    )
    p.add_argument(
        "--append",
        action="store_true",
        help="Append to existing playlist instead of replacing it.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without writing to Plex.",
    )
    p.add_argument(
        "--missing-file",
        help="Write unmatched track lines to this file.",
    )

    # ── slskd options ──────────────────────────────────────────────────────
    slskd = p.add_argument_group(
        "slskd",
        "Search & download missing tracks via Soulseek (slskd)."
        " Set SLSKD_URL and SLSKD_API_KEY env vars or use the flags below.",
    )
    slskd.add_argument(
        "--slskd-url",
        default=DEFAULT_SLSKD_URL,
        help="slskd base URL (default: %(default)s).",
    )
    slskd.add_argument(
        "--slskd-key",
        default=DEFAULT_SLSKD_KEY,
        help="slskd API key (or set SLSKD_API_KEY env var).",
    )
    slskd.add_argument(
        "--slskd-search",
        action="store_true",
        help="For tracks not found in Plex, search slskd and queue best match for download.",
    )
    return p.parse_args()


def main():
    args = parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"❌ CSV not found: {csv_path}")

    db_path = Path(args.plex_db)
    if not db_path.exists():
        sys.exit(f"❌ Plex DB not found: {db_path}")

    playlist_name = args.playlist or csv_path.stem

    print(f"📄 CSV:        {csv_path}")
    print(f"🎵 Playlist:   {playlist_name}")
    print(f"🔌 Plex:       {args.plex_url}")
    print(f"💾 Plex DB:    {db_path}", flush=True)
    if getattr(args, "slskd_search", False):
        print(f"🎧 slskd:      {args.slskd_url}", flush=True)
    if args.dry_run:
        print("  (DRY RUN — no changes will be written)", flush=True)
    print()

    # ── Read CSV ──────────────────────────────────────────────────────────────
    print("📖 Reading CSV…", flush=True)
    csv_tracks = read_csv(csv_path)
    print(f"   {len(csv_tracks)} tracks in CSV.", flush=True)

    # ── Build Plex index ──────────────────────────────────────────────────────
    print("🗄️  Copying Plex DB snapshot…", flush=True)
    tmp_db = copy_db(db_path)
    try:
        print("🔍 Building track index…", flush=True)
        ta_index, t_index, candidates = build_track_index(tmp_db)
        print(f"   {len(ta_index)} unique (title+artist) entries indexed.", flush=True)
    finally:
        tmp_db.unlink(missing_ok=True)

    # ── Match tracks ──────────────────────────────────────────────────────────
    print("\n🎯 Matching tracks…", flush=True)
    matched_ids: list[int] = []
    missed: list[TrackRow] = []

    for row in csv_tracks:
        title, artist = row["title"], row["artist"]
        tid = match_track(title, artist, ta_index, t_index, candidates)

        if tid is not None:
            matched_ids.append(tid)
        else:
            missed.append(row)
            print(f"  ❓ NOT FOUND: {title!r} — {artist!r}", flush=True)

    # Dédupliquer en préservant l'ordre d'apparition
    seen_ids: set[int] = set()
    deduped_ids: list[int] = []
    for mid in matched_ids:
        if mid not in seen_ids:
            seen_ids.add(mid)
            deduped_ids.append(mid)
    if len(deduped_ids) < len(matched_ids):
        print(f"  🔁 {len(matched_ids) - len(deduped_ids)} doublon(s) retiré(s) du CSV.", flush=True)
    matched_ids = deduped_ids

    print(f"\n  ✅ Matched  : {len(matched_ids)}/{len(csv_tracks)}")
    print(f"  ❌ Not found: {len(missed)}", flush=True)

    if args.missing_file and missed:
        mf = Path(args.missing_file)
        with open(mf, "w", encoding="utf-8") as fh:
            fh.write("title\tartist\talbum\n")
            for r in missed:
                fh.write(f"{r['title']}\t{r['artist']}\t{r['album']}\n")
        print(f"  📝 Missing tracks written to: {mf}", flush=True)

    # ── slskd search for missing tracks ───────────────────────────────────────
    if missed and getattr(args, "slskd_search", False):
        if not args.slskd_key:
            print(
                "\n⚠️  --slskd-search requested but no API key found."
                " Set SLSKD_API_KEY or use --slskd-key.",
                flush=True,
            )
        else:
            _run_slskd_downloads(missed, args.slskd_url, args.slskd_key, args.dry_run)

    if args.dry_run:
        print("\n(Dry run — skipping Plex write.)", flush=True)
        return

    if not matched_ids:
        print("\n⚠️  Nothing to import.", flush=True)
        return

    # ── Write to Plex ─────────────────────────────────────────────────────────
    print("\n📡 Connecting to Plex…", flush=True)
    machine_id = get_machine_id(args.plex_url, args.plex_token)
    print(f"   Machine ID: {machine_id}", flush=True)

    existing_rks = find_playlist_rating_keys(args.plex_url, args.plex_token, playlist_name)
    existing_rk = existing_rks[0] if existing_rks else None

    if existing_rks and len(existing_rks) > 1:
        print(
            f"  🧹 {len(existing_rks)} playlists existantes trouvées pour '{playlist_name}' ; consolidation en cours.",
            flush=True,
        )

    if existing_rks and not args.append:
        print(
            f"  🗑️  Deleting existing playlist(s) '{playlist_name}' (ratingKeys={', '.join(existing_rks)})…",
            flush=True,
        )
        for rating_key in existing_rks:
            delete_playlist(args.plex_url, args.plex_token, rating_key)
        existing_rk = None

    if existing_rk and args.append:
        # Exclure les morceaux déjà présents et fusionner les doublons éventuels dans une seule playlist.
        keeper_item_ids = get_playlist_item_ids(args.plex_url, args.plex_token, existing_rk)
        duplicate_item_ids: set[int] = set()
        for duplicate_rk in existing_rks[1:]:
            duplicate_item_ids.update(get_playlist_item_ids(args.plex_url, args.plex_token, duplicate_rk))

        merged_ids: list[int] = []
        seen_ids: set[int] = set()
        for item_id in list(duplicate_item_ids) + matched_ids:
            if item_id not in keeper_item_ids and item_id not in seen_ids:
                seen_ids.add(item_id)
                merged_ids.append(item_id)

        before = len(matched_ids)
        matched_ids = [i for i in matched_ids if i in merged_ids]
        if before - len(matched_ids):
            print(f"  🔁 {before - len(matched_ids)} morceau(x) déjà dans la playlist, ignoré(s).", flush=True)
        if not matched_ids and not duplicate_item_ids:
            print("  ℹ️  Aucun nouveau morceau à ajouter.", flush=True)
            return

        # Append by adding to existing playlist
        batch_size = 200
        for idx in range(0, len(merged_ids), batch_size):
            batch = merged_ids[idx : idx + batch_size]
            bcsv = ",".join(str(i) for i in batch)
            uri = (
                f"server://{machine_id}/com.plexapp.plugins.library"
                f"/library/metadata/{bcsv}"
            )
            bq = urllib.parse.urlencode({"uri": uri})
            plex_request(
                "PUT",
                f"{args.plex_url.rstrip('/')}/playlists/{existing_rk}/items?{bq}",
                args.plex_token,
            )
        for duplicate_rk in existing_rks[1:]:
            delete_playlist(args.plex_url, args.plex_token, duplicate_rk)
        print(
            f"  ✅ Appended {len(merged_ids)} tracks to existing playlist '{playlist_name}'.",
            flush=True,
        )
    else:
        create_playlist(args.plex_url, args.plex_token, machine_id, playlist_name, matched_ids)

    print("\n🎉 Done.", flush=True)


if __name__ == "__main__":
    main()
