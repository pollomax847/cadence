#!/usr/bin/env python3
"""
Import playlists from iTunes/music files (.m3u, .m3u8, .pls, .xspf) into Plex.

Behavior:
- Dry-run by default (no API write)
- Resolves relative paths inside playlist files
- Maps file paths to Plex track metadata IDs via SQLite read-only lookup
- Creates/replaces Plex audio playlists through Plex API
"""

import argparse
import configparser
import os
import re
import sqlite3
import tempfile
import shutil
import hashlib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Optionnel : slskd pour download des manquants
try:
    from slskd_downloader import SlskdClient
except ImportError:
    SlskdClient = None

from plex_api import (
    default_plex_url,
    plex_create_audio_playlist,
    plex_delete_playlist,
    plex_find_playlist_rating_keys,
    plex_machine_identifier,
    plex_request,
)
from playlist_detector import parse_playlist as parse_detected_playlist
from text_normalization import normalize_ascii, normalize_unicode as shared_normalize_unicode

DEFAULT_PLEX_DB = Path(
    os.environ.get(
        "PLEX_DB",
        "/var/snap/plexmediaserver/common/Library/Application Support/"
        "Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db",
    )
)

SUPPORTED_EXTENSIONS = {".m3u", ".m3u8", ".pls", ".xspf"}
DEFAULT_DUPLICATES_DIR = Path("/mnt/MyBook/itunes/playlist_duplicates")


@dataclass
class ImportResult:
    playlist_file: Path
    playlist_name: str
    total_entries: int
    resolved_entries: int
    mapped_tracks: int
    missing_tracks: int


@dataclass
class CleanupResult:
    empty_deleted: int
    duplicates_moved: int
    duplicates_kept: int
    duplicate_groups: int
    merged_groups: int
    merged_files_removed: int


@dataclass
class PlexExportResult:
    playlist_name: str
    playlist_rating_key: str
    track_count: int
    exported_count: int
    missing_count: int
    output_file: Path


@dataclass
class ItunesImportResult:
    playlists_total: int = 0
    playlists_created: int = 0
    playlists_skipped_existing: int = 0
    playlists_skipped_no_tracks: int = 0
    tracks_total: int = 0
    tracks_matched: int = 0
    tracks_unmatched: int = 0
    details: list = field(default_factory=list)  # list of (name, matched, total)


def get_machine_identifier(plex_url: str, token: str) -> str:
    return plex_machine_identifier(plex_url, token)


def find_audio_playlist_rating_keys(plex_url: str, token: str, title: str) -> list[str]:
    return plex_find_playlist_rating_keys(plex_url, token, title)


def delete_playlist(plex_url: str, token: str, rating_key: str) -> None:
    plex_delete_playlist(plex_url, token, rating_key)


def create_playlist(plex_url: str, token: str, machine_id: str, title: str, track_ids: list[int]) -> None:
    if not track_ids:
        return
    plex_create_audio_playlist(plex_url, token, title, track_ids, machine_id=machine_id, replace=True)


def parse_m3u_file(path: Path) -> list[str]:
    parsed = parse_detected_playlist(path)
    return [entry.path_or_uri for entry in parsed.entries if entry.path_or_uri]


def parse_pls_file(path: Path) -> list[str]:
    parsed = parse_detected_playlist(path)
    return [entry.path_or_uri for entry in parsed.entries if entry.path_or_uri]


def parse_xspf_file(path: Path) -> list[str]:
    parsed = parse_detected_playlist(path)
    return [entry.path_or_uri for entry in parsed.entries if entry.path_or_uri]


def parse_playlist_file(path: Path) -> list[str]:
    ext = path.suffix.lower()
    if ext in {".m3u", ".m3u8"}:
        return parse_m3u_file(path)
    if ext == ".pls":
        return parse_pls_file(path)
    if ext == ".xspf":
        return parse_xspf_file(path)
    return []


def write_m3u_file(path: Path, entries: list[str]) -> None:
    content = "\n".join(entries)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def write_pls_file(path: Path, entries: list[str]) -> None:
    parser = configparser.ConfigParser()
    parser["playlist"] = {
        "NumberOfEntries": str(len(entries)),
        "Version": "2",
    }
    for index, entry in enumerate(entries, start=1):
        parser["playlist"][f"File{index}"] = entry

    with path.open("w", encoding="utf-8") as handle:
        parser.write(handle)


def write_xspf_file(path: Path, entries: list[str]) -> None:
    root = ET.Element("playlist", attrib={"version": "1", "xmlns": "http://xspf.org/ns/0/"})
    track_list = ET.SubElement(root, "trackList")

    for entry in entries:
        track = ET.SubElement(track_list, "track")
        location = ET.SubElement(track, "location")
        location.text = Path(entry).as_uri() if Path(entry).is_absolute() else entry

    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_playlist_file(path: Path, entries: list[str]) -> None:
    ext = path.suffix.lower()
    if ext in {".m3u", ".m3u8"}:
        write_m3u_file(path, entries)
        return
    if ext == ".pls":
        write_pls_file(path, entries)
        return
    if ext == ".xspf":
        write_xspf_file(path, entries)
        return

    # Fallback to m3u style for unsupported extensions.
    write_m3u_file(path, entries)


def make_safe_playlist_filename(title: str) -> str:
    safe_title = "".join(char if char.isalnum() or char in {" ", "-", "_"} else "_" for char in title).strip()
    safe_title = "_".join(part for part in safe_title.split())
    return safe_title or "playlist"


def normalize_entry_to_path(entry: str, playlist_path: Path, entries_base_dir: Path | None = None) -> Path | None:
    value = entry.replace("\x00", "").strip()
    if not value:
        return None

    if value.startswith("file://"):
        parsed = urllib.parse.urlparse(value)
        value = urllib.parse.unquote(parsed.path)

    path = Path(value)
    try:
        if not path.is_absolute():
            base_dir = entries_base_dir if entries_base_dir is not None else playlist_path.parent
            # Keep normalization purely lexical to avoid expensive filesystem resolution on large mounts.
            path = Path(os.path.abspath(os.path.normpath(str(base_dir / path))))
        else:
            path = Path(os.path.abspath(os.path.normpath(str(path))))
    except (OSError, RuntimeError, ValueError):
        return None

    return path


def normalize_entry_string(entry: str, playlist_path: Path, entries_base_dir: Path | None = None) -> str | None:
    normalized_path = normalize_entry_to_path(entry, playlist_path, entries_base_dir)
    if normalized_path is None:
        return None
    return str(normalized_path).casefold()


def discover_playlist_files(
    root: Path,
    max_files: int = 0,
    max_depth: int = 0,
    exclude_subdirs: set[str] | None = None,
) -> list[Path]:
    files: list[Path] = []
    files_scanned = 0
    excluded = {name.casefold() for name in (exclude_subdirs or set())}

    base_depth = len(root.parts)
    dirs_visited = 0
    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        current_depth = len(current_path.parts) - base_depth

        if excluded:
            dirnames[:] = [dirname for dirname in dirnames if dirname.casefold() not in excluded]

        if max_depth > 0 and current_depth >= max_depth:
            dirnames[:] = []

        dirs_visited += 1
        if dirs_visited % 200 == 0:
            print(f"  [scan] {dirs_visited} dossiers parcourus, {len(files)} playlists trouvées...", flush=True)

        for filename in filenames:
            files_scanned += 1
            if files_scanned % 20000 == 0:
                print(
                    f"  [scan] {dirs_visited} dossiers, {files_scanned} fichiers inspectés, {len(files)} playlists trouvées...",
                    flush=True,
                )
            file_path = current_path / filename
            if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            files.append(file_path)
            if len(files) % 200 == 0:
                print(f"  [scan] {len(files)} playlists détectées...", flush=True)
            if max_files > 0 and len(files) >= max_files:
                files.sort()
                return files

    files.sort()
    return files


def make_playlist_signature(entries: list[str], playlist_file: Path, entries_base_dir: Path | None = None) -> str:
    normalized_entries = [normalize_entry_string(entry, playlist_file, entries_base_dir) for entry in entries]
    normalized_entries = [entry for entry in normalized_entries if entry]
    normalized_entries = sorted(dict.fromkeys(normalized_entries))
    payload = "\n".join(normalized_entries).encode("utf-8", errors="ignore")
    return hashlib.sha1(payload).hexdigest()


def cleanup_playlist_files(
    playlist_files: list[Path],
    duplicates_dir: Path,
    apply_changes: bool,
    merge_by_name: bool,
) -> CleanupResult:
    duplicates_dir.mkdir(parents=True, exist_ok=True)

    empty_deleted = 0
    duplicates_moved = 0
    duplicates_kept = 0
    duplicate_groups = 0
    merged_groups = 0
    merged_files_removed = 0

    non_empty_files: list[Path] = []
    non_empty_entries: dict[Path, list[str]] = {}
    signatures: dict[str, list[Path]] = {}

    for index, playlist_file in enumerate(playlist_files, start=1):
        if index % 50 == 0:
            print(f"  [cleanup] {index}/{len(playlist_files)} playlists analysées...", flush=True)
        entries = parse_playlist_file(playlist_file)
        normalized_paths = [normalize_entry_to_path(entry, playlist_file) for entry in entries]
        normalized_paths = [path for path in normalized_paths if path is not None]
        normalized_entries = [str(path) for path in normalized_paths]

        if not normalized_entries:
            empty_deleted += 1
            if apply_changes:
                playlist_file.unlink(missing_ok=True)
            continue

        non_empty_files.append(playlist_file)
        non_empty_entries[playlist_file] = normalized_entries
        signature = make_playlist_signature(entries, playlist_file)
        signatures.setdefault(signature, []).append(playlist_file)

    if merge_by_name:
        grouped_by_name: dict[str, list[Path]] = {}
        for playlist_file in non_empty_files:
            grouped_by_name.setdefault(playlist_file.stem.casefold(), []).append(playlist_file)

        for same_name_group in grouped_by_name.values():
            if len(same_name_group) <= 1:
                continue

            merged_groups += 1
            same_name_group.sort(key=lambda p: str(p))
            keep_file = same_name_group[0]

            merged_entries: list[str] = []
            seen: set[str] = set()
            for group_file in same_name_group:
                for entry in non_empty_entries.get(group_file, []):
                    key = entry.casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    merged_entries.append(entry)

            non_empty_entries[keep_file] = merged_entries

            for duplicate_file in same_name_group[1:]:
                merged_files_removed += 1
                if apply_changes:
                    duplicate_file.unlink(missing_ok=True)
                non_empty_entries.pop(duplicate_file, None)

        non_empty_files = sorted(non_empty_entries.keys(), key=lambda p: str(p))
        signatures = {}
        for playlist_file in non_empty_files:
            entries = non_empty_entries[playlist_file]
            signature_payload = "\n".join(sorted({entry.casefold() for entry in entries})).encode(
                "utf-8", errors="ignore"
            )
            signature = hashlib.sha1(signature_payload).hexdigest()
            signatures.setdefault(signature, []).append(playlist_file)

        if apply_changes:
            for playlist_file, entries in non_empty_entries.items():
                write_playlist_file(playlist_file, entries)

    for same_group in signatures.values():
        if len(same_group) <= 1:
            continue
        duplicate_groups += 1
        same_group.sort(key=lambda p: str(p))
        keep_file = same_group[0]
        duplicates_kept += 1
        for duplicate_file in same_group[1:]:
            duplicates_moved += 1
            if apply_changes:
                relative = duplicate_file.relative_to(Path("/")) if duplicate_file.is_absolute() else duplicate_file
                target = duplicates_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(duplicate_file), str(target))

    return CleanupResult(
        empty_deleted=empty_deleted,
        duplicates_moved=duplicates_moved,
        duplicates_kept=duplicates_kept,
        duplicate_groups=duplicate_groups,
        merged_groups=merged_groups,
        merged_files_removed=merged_files_removed,
    )


def list_audio_playlists(plex_url: str, token: str) -> list[dict]:
    root = ET.fromstring(plex_request("GET", f"{plex_url.rstrip('/')}/playlists", token))
    playlists: list[dict] = []
    for item in root.findall("Playlist") + root.findall("Directory"):
        if item.attrib.get("playlistType") != "audio":
            continue
        leaf_count_raw = item.attrib.get("leafCount")
        leaf_count = int(leaf_count_raw) if leaf_count_raw and leaf_count_raw.isdigit() else 0
        playlists.append(
            {
                "title": item.attrib.get("title", ""),
                "rating_key": item.attrib.get("ratingKey", ""),
                "leaf_count": leaf_count,
                "smart": item.attrib.get("smart", "0") == "1",
            }
        )
    return playlists


def get_playlist_track_ids(plex_url: str, token: str, rating_key: str) -> list[int]:
    root = ET.fromstring(plex_request("GET", f"{plex_url.rstrip('/')}/playlists/{rating_key}/items", token))
    ids: list[int] = []
    for item in root.findall("Track") + root.findall("Metadata"):
        track_key = item.attrib.get("ratingKey")
        if not track_key:
            continue
        try:
            ids.append(int(track_key))
        except ValueError:
            continue
    return ids


def list_audio_playlists_from_db(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT mi.id, mi.title, COALESCE(mi.media_item_count, 0)
        FROM metadata_items mi
        WHERE mi.metadata_type = 15
        ORDER BY LOWER(mi.title), mi.id
        """
    ).fetchall()
    return [
        {
            "title": str(row[1] or ""),
            "rating_key": str(row[0]),
            "leaf_count": int(row[2] or 0),
        }
        for row in rows
    ]


def get_playlist_track_details_from_db(conn: sqlite3.Connection, playlist_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            pqg.metadata_item_id,
            track.title,
            COALESCE(artist.title, 'Unknown Artist') AS artist_name,
            mp.file
        FROM play_queue_generators pqg
        JOIN metadata_items track ON pqg.metadata_item_id = track.id
        JOIN media_items media ON media.metadata_item_id = track.id
        JOIN media_parts mp ON mp.media_item_id = media.id
        LEFT JOIN metadata_items album ON track.parent_id = album.id
        LEFT JOIN metadata_items artist ON album.parent_id = artist.id
        WHERE pqg.playlist_id = ?
        AND pqg.metadata_item_id IS NOT NULL
        AND track.metadata_type = 10
        ORDER BY pqg.`order`, pqg.id
        """,
        (playlist_id,),
    ).fetchall()
    return [
        {
            "track_id": int(row[0]),
            "title": str(row[1] or "Unknown Title"),
            "artist": str(row[2] or "Unknown Artist"),
            "file_path": str(row[3] or ""),
        }
        for row in rows
    ]


def is_smart_playlist(conn: sqlite3.Connection, playlist_id: int) -> bool:
    """Return True if the playlist uses a URI-based filter (smart playlist)."""
    row = conn.execute(
        "SELECT uri FROM play_queue_generators WHERE playlist_id = ? AND uri IS NOT NULL LIMIT 1",
        (playlist_id,),
    ).fetchone()
    return row is not None


def get_smart_playlist_tracks_via_api(
    conn: sqlite3.Connection, plex_url: str, token: str, rating_key: str
) -> list[dict]:
    """Fetch smart playlist tracks via Plex API, then map file paths from local DB."""
    track_ids = get_playlist_track_ids(plex_url, token, rating_key)
    if not track_ids:
        return []
    placeholders = ",".join("?" * len(track_ids))
    rows = conn.execute(
        f"""
        SELECT
            track.id,
            track.title,
            COALESCE(artist.title, 'Unknown Artist') AS artist_name,
            mp.file
        FROM metadata_items track
        JOIN media_items media ON media.metadata_item_id = track.id
        JOIN media_parts mp ON mp.media_item_id = media.id
        LEFT JOIN metadata_items album ON track.parent_id = album.id
        LEFT JOIN metadata_items artist ON album.parent_id = artist.id
        WHERE track.id IN ({placeholders})
        AND track.metadata_type = 10
        """,
        track_ids,
    ).fetchall()
    return [
        {
            "track_id": int(r[0]),
            "title": str(r[1] or "Unknown Title"),
            "artist": str(r[2] or "Unknown Artist"),
            "file_path": str(r[3] or ""),
        }
        for r in rows
    ]


def write_exported_playlist(path: Path, playlist_name: str, tracks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("#EXTM3U\n")
        handle.write(f"#PLAYLIST:{playlist_name}\n\n")
        for track in tracks:
            handle.write(f"#EXTINF:-1,{track['artist']} - {track['title']}\n")
            handle.write(f"{track['file_path']}\n\n")


def export_plex_playlists_to_dir(
    plex_db: Path,
    output_dir: Path,
    apply_changes: bool,
    prune_output_dir: bool,
    plex_url: str | None = None,
    token: str | None = None,
) -> dict:
    if not plex_db.exists():
        raise FileNotFoundError(f"Plex database not found: {plex_db}")

    temp_db = prepare_temp_db(plex_db)
    results: list[PlexExportResult] = []
    exported_names: set[str] = set()
    pruned_files = 0
    smart_resolved = 0

    try:
        with sqlite3.connect(str(temp_db)) as conn:
            playlists = list_audio_playlists_from_db(conn)
            for index, playlist in enumerate(playlists, start=1):
                if index % 20 == 0:
                    print(f"  [plex-export] {index}/{len(playlists)} playlists Plex analysées...", flush=True)

                track_details = get_playlist_track_details_from_db(conn, int(playlist["rating_key"]))

                # Fallback API pour les smart playlists (filtre URI, pistes non stockées dans la DB)
                if not track_details and plex_url and token:
                    if is_smart_playlist(conn, int(playlist["rating_key"])):
                        try:
                            track_details = get_smart_playlist_tracks_via_api(
                                conn, plex_url, token, playlist["rating_key"]
                            )
                            if track_details:
                                smart_resolved += 1
                        except Exception:
                            pass  # API indisponible ou erreur réseau, on ignore

                exported_tracks: list[dict] = []
                missing_count = 0
                for track in track_details:
                    file_path = track.get("file_path")
                    if not file_path:
                        missing_count += 1
                        continue
                    exported_tracks.append(
                        {
                            "title": track["title"],
                            "artist": track["artist"],
                            "file_path": file_path,
                        }
                    )

                output_file = output_dir / f"{make_safe_playlist_filename(playlist['title'])}.m3u8"
                if exported_tracks:
                    exported_names.add(output_file.name)
                    if apply_changes:
                        write_exported_playlist(output_file, playlist["title"], exported_tracks)

                results.append(
                    PlexExportResult(
                        playlist_name=playlist["title"],
                        playlist_rating_key=playlist["rating_key"],
                        track_count=len(track_details),
                        exported_count=len(exported_tracks),
                        missing_count=missing_count,
                        output_file=output_file,
                    )
                )

        if apply_changes and prune_output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            for existing_file in output_dir.glob("*.m3u8"):
                if existing_file.name in exported_names:
                    continue
                existing_file.unlink(missing_ok=True)
                pruned_files += 1
    finally:
        if temp_db.exists():
            temp_db.unlink()

    return {
        "mode": "APPLY" if apply_changes else "DRY-RUN",
        "playlist_count": len(playlists),
        "exported_playlist_count": sum(1 for result in results if result.exported_count > 0),
        "smart_playlists_resolved": smart_resolved,
        "total_tracks": sum(result.track_count for result in results),
        "exported_tracks": sum(result.exported_count for result in results),
        "missing_tracks": sum(result.missing_count for result in results),
        "pruned_files": pruned_files,
        "results": results,
    }


def find_identical_playlist_groups_from_db(plex_db: Path) -> list[list[dict]]:
    """Return groups of static playlists that have identical track content (from DB)."""
    from collections import defaultdict
    temp_db = prepare_temp_db(plex_db)
    groups: list[list[dict]] = []
    try:
        with sqlite3.connect(str(temp_db)) as conn:
            rows = conn.execute(
                "SELECT id, title FROM metadata_items WHERE metadata_type=15 ORDER BY id"
            ).fetchall()
            content_map: dict[frozenset, list[dict]] = defaultdict(list)
            for pid, title in rows:
                is_smart = conn.execute(
                    "SELECT COUNT(*) FROM play_queue_generators WHERE playlist_id=? AND uri IS NOT NULL AND metadata_item_id IS NULL",
                    (pid,),
                ).fetchone()[0] > 0
                if is_smart:
                    continue
                track_ids = frozenset(
                    r[0]
                    for r in conn.execute(
                        "SELECT metadata_item_id FROM play_queue_generators WHERE playlist_id=? AND metadata_item_id IS NOT NULL",
                        (pid,),
                    ).fetchall()
                    if r[0]
                )
                if track_ids:
                    content_map[track_ids].append({"rating_key": str(pid), "title": title, "track_count": len(track_ids)})
            groups = [v for v in content_map.values() if len(v) > 1]
    finally:
        if temp_db.exists():
            temp_db.unlink()
    return groups


def _playlist_base_name(title: str) -> str:
    """Strip trailing _NNN_titres / NNN titres suffix for grouping similar playlists."""
    return re.sub(r'[_\s]?\d+[_\s]?titres$', '', title, flags=re.IGNORECASE).rstrip('_').strip().casefold()


def cleanup_plex_playlists(
    plex_url: str,
    token: str,
    apply_changes: bool,
    remove_empty: bool,
    merge_duplicates: bool,
    remove_identical: bool = False,
    merge_similar: bool = False,
    plex_db: Path | None = None,
) -> dict:
    playlists = list_audio_playlists(plex_url, token)
    api_by_key: dict[str, dict] = {p["rating_key"]: p for p in playlists}
    grouped_by_name: dict[str, list[dict]] = {}
    for playlist in playlists:
        grouped_by_name.setdefault(playlist["title"].casefold(), []).append(playlist)

    duplicate_groups = 0
    duplicate_playlists_deleted = 0
    duplicate_playlists_recreated = 0
    empty_deleted = 0
    identical_groups = 0
    identical_deleted = 0
    similar_groups = 0
    similar_merged = 0
    processed_rating_keys: set[str] = set()

    machine_id: str | None = None

    if merge_duplicates:
        for group in grouped_by_name.values():
            if len(group) <= 1:
                continue

            duplicate_groups += 1
            group_sorted = sorted(group, key=lambda entry: entry["rating_key"])
            title = group_sorted[0]["title"]

            # Smart playlist duplicates: keep the oldest (lowest rating_key), delete the rest
            if all(entry.get("smart") for entry in group_sorted):
                to_delete = group_sorted[1:]  # keep index 0
                for entry in to_delete:
                    processed_rating_keys.add(entry["rating_key"])
                    duplicate_playlists_deleted += 1
                    if apply_changes:
                        delete_playlist(plex_url, token, entry["rating_key"])
                continue

            # Static playlist duplicates: merge all tracks into one
            merged_track_ids: list[int] = []
            seen_tracks: set[int] = set()
            for entry in group_sorted:
                processed_rating_keys.add(entry["rating_key"])
                for track_id in get_playlist_track_ids(plex_url, token, entry["rating_key"]):
                    if track_id in seen_tracks:
                        continue
                    seen_tracks.add(track_id)
                    merged_track_ids.append(track_id)

            if apply_changes:
                for entry in group_sorted:
                    delete_playlist(plex_url, token, entry["rating_key"])
                    duplicate_playlists_deleted += 1

                if merged_track_ids:
                    if machine_id is None:
                        machine_id = get_machine_identifier(plex_url, token)
                    create_playlist(plex_url, token, machine_id, title, merged_track_ids)
                    duplicate_playlists_recreated += 1

    if remove_identical and plex_db:
        identical_groups_data = find_identical_playlist_groups_from_db(plex_db)
        for group in identical_groups_data:
            # Only act on playlists present in current API listing
            active = [p for p in group if p["rating_key"] in api_by_key and p["rating_key"] not in processed_rating_keys]
            if len(active) <= 1:
                continue
            identical_groups += 1
            # Keep the one with highest leaf_count per API (tie-break: lowest rating_key)
            active_sorted = sorted(
                active,
                key=lambda p: (-api_by_key[p["rating_key"]]["leaf_count"], p["rating_key"]),
            )
            to_keep = active_sorted[0]
            to_delete = active_sorted[1:]
            for entry in to_delete:
                processed_rating_keys.add(entry["rating_key"])
                identical_deleted += 1
                if apply_changes:
                    delete_playlist(plex_url, token, entry["rating_key"])

    if merge_similar:
        from collections import defaultdict as _dd
        base_map: dict[str, list[dict]] = _dd(list)
        for playlist in playlists:
            if playlist.get("smart"):
                continue
            if playlist["rating_key"] in processed_rating_keys:
                continue
            base_map[_playlist_base_name(playlist["title"])].append(playlist)
        for base, group in base_map.items():
            if len(group) <= 1:
                continue
            similar_groups += 1
            # Keep the playlist with most tracks (leaf_count), tie-break: lowest rating_key
            group_sorted = sorted(group, key=lambda p: (-p["leaf_count"], p["rating_key"]))
            keeper = group_sorted[0]
            to_merge = group_sorted[1:]
            # Collect union of all tracks via API
            merged_track_ids: list[int] = []
            seen_tracks: set[int] = set()
            for entry in group_sorted:
                processed_rating_keys.add(entry["rating_key"])
                for track_id in get_playlist_track_ids(plex_url, token, entry["rating_key"]):
                    if track_id in seen_tracks:
                        continue
                    seen_tracks.add(track_id)
                    merged_track_ids.append(track_id)
            similar_merged += len(to_merge)
            if apply_changes:
                for entry in group_sorted:
                    delete_playlist(plex_url, token, entry["rating_key"])
                if merged_track_ids:
                    if machine_id is None:
                        machine_id = get_machine_identifier(plex_url, token)
                    create_playlist(plex_url, token, machine_id, keeper["title"], merged_track_ids)

    if remove_empty:
        for playlist in playlists:
            rating_key = playlist["rating_key"]
            if rating_key in processed_rating_keys:
                continue
            if playlist["leaf_count"] > 0:
                continue
            empty_deleted += 1
            processed_rating_keys.add(rating_key)
            if apply_changes:
                delete_playlist(plex_url, token, rating_key)

    return {
        "audio_playlists_total": len(playlists),
        "duplicate_groups": duplicate_groups,
        "duplicate_playlists_deleted": duplicate_playlists_deleted,
        "duplicate_playlists_recreated": duplicate_playlists_recreated,
        "identical_groups": identical_groups,
        "identical_deleted": identical_deleted,
        "similar_groups": similar_groups,
        "similar_merged": similar_merged,
        "empty_playlists_deleted": empty_deleted,
        "mode": "APPLY" if apply_changes else "DRY-RUN",
    }


def prepare_temp_db(db_path: Path) -> Path:
    db_size = db_path.stat().st_size
    candidates: list[Path] = []

    tmpdir_env = os.environ.get("TMPDIR", "").strip()
    if tmpdir_env:
        candidates.append(Path(tmpdir_env).expanduser())
    candidates.extend([Path("/data/tmp"), Path("/data"), Path("/tmp")])

    checked: list[str] = []
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            free_bytes = shutil.disk_usage(candidate).free
            checked.append(f"{candidate} (free={free_bytes})")
            if free_bytes < db_size:
                continue
            temp_path = Path(tempfile.mkstemp(prefix="plex_import_", suffix=".db", dir=str(candidate))[1])
            # Use SQLite online backup for a consistent snapshot of a live DB.
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as src_conn:
                with sqlite3.connect(str(temp_path)) as dst_conn:
                    src_conn.backup(dst_conn)
            return temp_path
        except OSError:
            checked.append(f"{candidate} (unusable)")

    raise OSError(
        28,
        "No space left on device for temporary Plex DB copy",
        f"checked: {', '.join(checked)}",
    )


def normalize(s: str) -> str:
    """Normalize a string for fuzzy matching: NFKD→ASCII, lowercase, alphanum only."""
    return normalize_ascii(s)


def normalize_unicode(s: str) -> str:
    """Normalize keeping unicode chars: lowercase, strip punctuation (for non-Latin languages)."""
    return shared_normalize_unicode(s)


def parse_itunes_xml(xml_path: Path) -> tuple[dict, list[dict]]:
    """Parse iTunes XML library file.

    Returns:
        (tracks_by_id, playlists) where:
        - tracks_by_id: {int track_id: {Name, Artist, Location, ...}}
        - playlists: [{'name': str, 'track_ids': [int], 'smart': bool}]
    """
    _SYSTEM_NAMES = frozenset({
        # English
        "Library", "Music", "Movies", "TV Shows", "Podcasts",
        "Audiobooks", "iTunes U", "Books", "Genius", "Purchased",
        # French
        "Bibliothèque", "Musique", "Films", "Séries TV",
        "iTunes U", "Livres", "Genius", "Achats",
    })

    def _parse_value(elem: ET.Element):
        tag = elem.tag
        if tag == "dict":
            return _parse_dict(elem)
        elif tag == "array":
            return [_parse_value(c) for c in elem]
        elif tag == "string":
            return elem.text or ""
        elif tag == "integer":
            return int(elem.text or 0)
        elif tag == "real":
            return float(elem.text or 0)
        elif tag == "true":
            return True
        elif tag == "false":
            return False
        elif tag == "date":
            return elem.text
        return None

    def _parse_dict(elem: ET.Element) -> dict:
        result: dict = {}
        children = list(elem)
        for i in range(0, len(children) - 1, 2):
            key = children[i].text
            result[key] = _parse_value(children[i + 1])
        return result

    tree = ET.parse(str(xml_path))
    root = tree.getroot()
    top = _parse_dict(root[0])

    tracks_by_id: dict[int, dict] = {}
    for str_id, info in (top.get("Tracks") or {}).items():
        tracks_by_id[int(str_id)] = info

    playlists: list[dict] = []
    for pl in (top.get("Playlists") or []):
        name = pl.get("Name", "")
        if name in _SYSTEM_NAMES:
            continue
        if pl.get("Folder"):
            continue
        is_smart = bool(pl.get("Smart Info") or pl.get("Smart Criteria"))
        items = pl.get("Playlist Items") or []
        track_ids = [item["Track ID"] for item in items if "Track ID" in item]
        playlists.append({"name": name, "track_ids": track_ids, "smart": is_smart})

    return tracks_by_id, playlists


def itunes_location_to_path(location: str, path_maps: list[tuple[str, str]]) -> Path | None:
    """Convert iTunes file:// URL to filesystem path, applying prefix remapping."""
    if not location:
        return None
    if location.startswith("file://localhost/"):
        path_str = urllib.parse.unquote(location[len("file://localhost"):])
    elif location.startswith("file:///"):
        path_str = urllib.parse.unquote(location[len("file://"):])
    elif location.startswith("file://"):
        # file://hostname/path — drop hostname
        rest = location[len("file://"):]
        slash = rest.find("/")
        path_str = urllib.parse.unquote(rest[slash:] if slash != -1 else "/" + rest)
    else:
        path_str = urllib.parse.unquote(location)
    for old_prefix, new_prefix in path_maps:
        if path_str.startswith(old_prefix):
            path_str = new_prefix + path_str[len(old_prefix):]
            break
    return Path(path_str)


def map_paths_to_track_ids(
    conn: sqlite3.Connection,
    paths: Iterable[Path],
    allow_basename_fallback: bool = False,
    basename_to_ids: dict[str, set[int]] | None = None,
) -> dict[str, int]:
    mapping: dict[str, int] = {}
    query = """
    SELECT mi.id
    FROM media_parts mp
    JOIN media_items m ON mp.media_item_id = m.id
    JOIN metadata_items mi ON m.metadata_item_id = mi.id
    WHERE mp.file = ? AND mi.metadata_type = 10
    LIMIT 1
    """
    for p in paths:
        row = conn.execute(query, (str(p),)).fetchone()
        if row:
            mapping[str(p)] = int(row[0])

    if allow_basename_fallback:
        unresolved = [p for p in paths if str(p) not in mapping]
        if unresolved:
            if basename_to_ids is None:
                basename_to_ids = {}
                rows = conn.execute(
                    """
                    SELECT mi.id, mp.file
                    FROM media_parts mp
                    JOIN media_items m ON mp.media_item_id = m.id
                    JOIN metadata_items mi ON m.metadata_item_id = mi.id
                    WHERE mi.metadata_type = 10
                    """
                ).fetchall()
                for row in rows:
                    item_id = int(row[0])
                    file_path = str(row[1] or "")
                    if not file_path:
                        continue
                    basename = Path(file_path).name.casefold()
                    if not basename:
                        continue
                    basename_to_ids.setdefault(basename, set()).add(item_id)

            for p in unresolved:
                basename = p.name.casefold()
                ids = basename_to_ids.get(basename, set())
                if len(ids) == 1:
                    mapping[str(p)] = next(iter(ids))

    return mapping


def build_basename_index(conn: sqlite3.Connection) -> dict[str, set[int]]:
    index: dict[str, set[int]] = {}
    rows = conn.execute(
        """
        SELECT mi.id, CAST(mp.file AS BLOB)
        FROM media_parts mp
        JOIN media_items m ON mp.media_item_id = m.id
        JOIN metadata_items mi ON m.metadata_item_id = mi.id
        WHERE mi.metadata_type = 10
        """
    ).fetchall()
    for row in rows:
        item_id = int(row[0])
        raw_file = row[1]
        if isinstance(raw_file, (bytes, bytearray)):
            file_path = raw_file.decode("utf-8", errors="replace")
        else:
            file_path = str(raw_file or "")
        if not file_path:
            continue
        basename = Path(file_path).name.casefold()
        if not basename:
            continue
        index.setdefault(basename, set()).add(item_id)
    return index


def import_itunes_xml_to_plex(
    xml_path: Path,
    plex_db: Path,
    plex_url: str,
    token: str,
    apply_changes: bool,
    replace_existing: bool,
    path_maps: list[tuple[str, str]],
    skip_smart: bool = True,
) -> ItunesImportResult:
    """Import playlists from an iTunes XML library file into Plex.

    Matching strategy (in order):
    1. Direct file path lookup in Plex DB (after applying path_maps prefix remapping)
    2. Title + artist normalization (NFKD→ASCII lowercase)
    3. Title normalization (unicode-aware, for non-Latin titles) + unique title match
    """
    tracks_by_id, playlists = parse_itunes_xml(xml_path)
    result = ItunesImportResult(playlists_total=len(playlists))

    # Get existing Plex playlist names
    existing_playlists: set[str] = {p["title"].casefold() for p in list_audio_playlists(plex_url, token)}

    machine_id = get_machine_identifier(plex_url, token)

    temp_db = prepare_temp_db(plex_db)
    try:
        with sqlite3.connect(str(temp_db)) as conn:
            conn.text_factory = lambda b: b.decode("utf-8", errors="replace")

            # Build title+artist indexes for fallback matching
            ta_index: dict[tuple[str, str], int] = {}   # (norm_title, norm_artist) -> id
            t_ascii_index: dict[str, list[int]] = {}    # norm_title_ascii -> [id]
            t_uni_index: dict[str, list[int]] = {}      # norm_title_unicode -> [id]

            rows = conn.execute(
                """
                SELECT mi.id, mi.title, COALESCE(art.title, '') as artist
                FROM metadata_items mi
                LEFT JOIN metadata_items album ON mi.parent_id = album.id
                LEFT JOIN metadata_items art ON album.parent_id = art.id
                WHERE mi.metadata_type = 10
                """
            ).fetchall()
            for row in rows:
                tid, title, artist = int(row[0]), row[1] or "", row[2] or ""
                nt_ascii = normalize(title)
                na_ascii = normalize(artist)
                nt_uni = normalize_unicode(title)
                if nt_ascii:
                    ta_index[(nt_ascii, na_ascii)] = tid
                    t_ascii_index.setdefault(nt_ascii, []).append(tid)
                if nt_uni:
                    t_uni_index.setdefault(nt_uni, []).append(tid)

            for pl in playlists:
                name: str = pl["name"]
                is_smart: bool = pl["smart"]
                itunes_track_ids: list[int] = pl["track_ids"]

                if is_smart and skip_smart:
                    print(f"  SKIP smart: {name}")
                    continue

                if name.casefold() in existing_playlists and not replace_existing:
                    result.playlists_skipped_existing += 1
                    print(f"  SKIP (déjà dans Plex): {name}")
                    continue

                plex_track_ids: list[int] = []
                unmatched_count = 0

                for itunes_tid in itunes_track_ids:
                    track_info = tracks_by_id.get(itunes_tid, {})
                    location: str = track_info.get("Location", "")
                    result.tracks_total += 1
                    plex_id: int | None = None

                    # 1. Path lookup
                    path = itunes_location_to_path(location, path_maps)
                    if path:
                        row_db = conn.execute(
                            """
                            SELECT mi.id FROM media_parts mp
                            JOIN media_items m ON mp.media_item_id = m.id
                            JOIN metadata_items mi ON m.metadata_item_id = mi.id
                            WHERE mp.file = ? AND mi.metadata_type = 10 LIMIT 1
                            """,
                            (str(path),),
                        ).fetchone()
                        if row_db:
                            plex_id = int(row_db[0])

                    # 2. Title + artist (ASCII normalized)
                    if plex_id is None:
                        title = track_info.get("Name", "")
                        artist = track_info.get("Artist", "")
                        nt_ascii = normalize(title)
                        na_ascii = normalize(artist)
                        nt_uni = normalize_unicode(title)
                        if nt_ascii:
                            plex_id = ta_index.get((nt_ascii, na_ascii))
                            if plex_id is None:
                                ids = t_ascii_index.get(nt_ascii, [])
                                if len(ids) == 1:
                                    plex_id = ids[0]
                        # 3. Unicode title (for non-Latin: Arabic, etc.)
                        if plex_id is None and nt_uni:
                            ids = t_uni_index.get(nt_uni, [])
                            if len(ids) == 1:
                                plex_id = ids[0]

                    if plex_id is not None:
                        plex_track_ids.append(plex_id)
                        result.tracks_matched += 1
                    else:
                        unmatched_count += 1
                        result.tracks_unmatched += 1

                matched_pct = int(100 * len(plex_track_ids) / max(len(itunes_track_ids), 1)) if itunes_track_ids else 0
                result.details.append((name, len(plex_track_ids), len(itunes_track_ids), matched_pct))

                if not plex_track_ids:
                    result.playlists_skipped_no_tracks += 1
                    print(f"  SKIP (0/{len(itunes_track_ids)} pistes): {name}")
                    continue

                action = "CRÉÉ" if apply_changes else "SERAIT CRÉÉ"
                print(f"  {action} ({len(plex_track_ids)}/{len(itunes_track_ids)} = {matched_pct}%): {name}")
                result.playlists_created += 1

                if apply_changes:
                    unique_ids = list(dict.fromkeys(plex_track_ids))
                    existing_keys = find_audio_playlist_rating_keys(plex_url, token, name)
                    if existing_keys and replace_existing:
                        for existing_key in existing_keys:
                            delete_playlist(plex_url, token, existing_key)
                        existing_keys = []
                    if not existing_keys:
                        create_playlist(plex_url, token, machine_id, name, unique_ids)
    finally:
        if temp_db.exists():
            temp_db.unlink()

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Import iTunes/music playlist files into Plex")
    parser.add_argument("--source-dir", default="/mnt/MyBook/itunes/Music", help="Root directory to scan for playlist files")
    parser.add_argument("--plex-db", default=str(DEFAULT_PLEX_DB), help="Path to Plex SQLite database")
    parser.add_argument("--plex-url", default=default_plex_url(), help="Plex URL")
    parser.add_argument("--plex-token", help="X-Plex-Token")
    parser.add_argument("--limit-files", type=int, default=0, help="Limit number of playlist files processed (0 = all)")
    parser.add_argument("--max-depth", type=int, default=8, help="Maximum directory depth to scan under source-dir (0 = unlimited)")
    parser.add_argument(
        "--exclude-subdir",
        action="append",
        default=[],
        help="Subdirectory name to skip during source-dir scan (can be repeated)",
    )
    parser.add_argument("--entries-base-dir", help="Base directory used to resolve relative entries inside playlists")
    parser.add_argument("--replace", action="store_true", help="Replace existing Plex playlist with same name")
    parser.add_argument("--map-by-basename", action="store_true", help="Fallback mapping by unique filename when absolute path match fails")
    parser.add_argument("--apply", action="store_true", help="Apply API writes (default is dry-run)")
    parser.add_argument("--cleanup-empty", action="store_true", help="Delete empty playlist files")
    parser.add_argument("--cleanup-duplicates", action="store_true", help="Move duplicate playlist files to archive directory")
    parser.add_argument("--merge-duplicate-playlists", action="store_true", help="Merge duplicate playlist files with same name before dedupe")
    parser.add_argument("--cleanup-plex-empty", action="store_true", help="Delete empty audio playlists in Plex")
    parser.add_argument("--cleanup-plex-duplicates", action="store_true", help="Merge/delete duplicate audio playlists in Plex (same title)")
    parser.add_argument("--cleanup-plex-identical", action="store_true", help="Delete playlists with identical track content in Plex (keeps one per group)")
    parser.add_argument("--cleanup-plex-similar", action="store_true", help="Merge playlists with same base name (ignoring trailing _NNN_titres suffix) into one")
    parser.add_argument("--export-plex-dir", help="Export Plex audio playlists to M3U8 files in this directory")
    parser.add_argument("--prune-export-dir", action="store_true", help="Delete exported M3U8 files no longer present in Plex")
    parser.add_argument("--prune-plex-from-export-dir", action="store_true", help="Delete Plex playlists whose M3U8 file was removed from --export-plex-dir")
    parser.add_argument("--skip-import", action="store_true", help="Run cleanup only, skip playlist import into Plex")
    parser.add_argument("--duplicates-dir", default=str(DEFAULT_DUPLICATES_DIR), help="Directory where duplicate playlist files are moved")
    parser.add_argument("--import-itunes-xml", metavar="XML_PATH", help="iTunes XML library file to import playlists from")
    parser.add_argument(
        "--itunes-path-map",
        action="append",
        default=[],
        metavar="OLD:NEW",
        help="Rewrite path prefix in iTunes track locations (e.g. /mnt/MyBook/:/mnt/ssd/). Can be repeated.",
    )
    parser.add_argument("--itunes-include-smart", action="store_true", help="Also import iTunes smart playlists (skipped by default)")
    parser.add_argument("--itunes-replace", action="store_true", help="Replace existing Plex playlists imported from iTunes XML")

    # slskd options
    parser.add_argument("--slskd-search", action="store_true", help="Search & download missing tracks via slskd (Soulseek)")
    parser.add_argument("--slskd-url", default=os.environ.get("SLSKD_URL", "http://localhost:5030"), help="slskd base URL")
    parser.add_argument("--slskd-key", default=os.environ.get("SLSKD_API_KEY", ""), help="slskd API key")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    plex_db = Path(args.plex_db)
    entries_base_dir = Path(args.entries_base_dir).resolve() if args.entries_base_dir else None

    if not source_dir.exists():
        raise SystemExit(f"Source directory not found: {source_dir}")
    import_mode_requested = bool(args.plex_token) and not args.skip_import
    if import_mode_requested and not plex_db.exists():
        raise SystemExit(f"Plex database not found: {plex_db}")

    # slskd download for missing tracks (all playlists)
    if args.slskd_search and SlskdClient is not None:
        all_missing = []
        for r in results:
            if r.missing_tracks:
                # Try to reload entries for missing tracks
                entries = parse_playlist_file(r.playlist_file)
                resolved_paths = [normalize_entry_to_path(e, r.playlist_file, entries_base_dir) for e in entries]
                resolved_paths = [p for p in resolved_paths if p is not None]
                mapped = map_paths_to_track_ids(
                    sqlite3.connect(str(plex_db)),
                    resolved_paths,
                    allow_basename_fallback=args.map_by_basename,
                )
                missing_paths = [p for p in resolved_paths if str(p) not in mapped]
                for p in missing_paths:
                    all_missing.append({"title": p.stem, "artist": "", "album": ""})
        if all_missing:
            print(f"\n🎧 slskd: recherche et téléchargement de {len(all_missing)} pistes manquantes...")
            slskd_url = args.slskd_url
            slskd_key = args.slskd_key
            if not slskd_key:
                print("  ❌ SLSKD_API_KEY manquant (option --slskd-key ou variable d'environnement)")
            else:
                client = SlskdClient(url=slskd_url, api_key=slskd_key)
                queued = 0
                failed = 0
                for track in all_missing:
                    title = track["title"]
                    artist = track["artist"]
                    try:
                        candidates = client.search(title, artist)
                        if not candidates:
                            print(f"  ❌ {title} — aucun résultat")
                            failed += 1
                            continue
                        result = client.download(candidates[0])
                        if result.queued:
                            print(f"  ✅ {title} — téléchargé/queue")
                            queued += 1
                        else:
                            print(f"  ❌ {title} — {result.error}")
                            failed += 1
                    except Exception as exc:
                        print(f"  ❌ {title} — erreur: {exc}")
                        failed += 1
                print(f"  slskd: {queued} en file, {failed} échecs.")
        else:
            print("🎧 slskd: aucune piste manquante à télécharger.")

    cleanup_mode_requested = (
        args.cleanup_empty
        or args.cleanup_duplicates
        or args.cleanup_plex_empty
        or args.cleanup_plex_duplicates
        or args.cleanup_plex_identical
        or args.cleanup_plex_similar
        or args.merge_duplicate_playlists
        or bool(args.export_plex_dir)
    )

    if args.apply and not args.plex_token and not cleanup_mode_requested:
        raise SystemExit("--plex-token is required when using --apply for Plex API import")

    if (args.cleanup_plex_empty or args.cleanup_plex_duplicates or args.cleanup_plex_identical or args.cleanup_plex_similar) and not args.plex_token:
        raise SystemExit("--plex-token is required for Plex cleanup/export operations")

    file_cleanup_requested = args.cleanup_empty or args.cleanup_duplicates or args.merge_duplicate_playlists
    file_scan_required = file_cleanup_requested or import_mode_requested
    playlist_files: list[Path] = []

    if file_scan_required:
        playlist_files = discover_playlist_files(
            source_dir,
            max_files=args.limit_files,
            max_depth=args.max_depth,
            exclude_subdirs=set(args.exclude_subdir),
        )

        if not playlist_files:
            print("No playlist files found.")
            if import_mode_requested or file_cleanup_requested:
                return 0

    if file_cleanup_requested:
        cleanup_result = cleanup_playlist_files(
            playlist_files=playlist_files,
            duplicates_dir=Path(args.duplicates_dir),
            apply_changes=args.apply,
            merge_by_name=args.merge_duplicate_playlists,
        )
        print(f"Cleanup mode: {'APPLY' if args.apply else 'DRY-RUN'}")
        print(f"- empty playlists {'deleted' if args.apply else 'to delete'}: {cleanup_result.empty_deleted}")
        print(f"- merge groups (same name): {cleanup_result.merged_groups}")
        print(f"- merged files {'removed' if args.apply else 'to remove'}: {cleanup_result.merged_files_removed}")
        print(f"- duplicate groups: {cleanup_result.duplicate_groups}")
        print(f"- duplicates {'moved' if args.apply else 'to move'}: {cleanup_result.duplicates_moved}")
        print(f"- duplicate anchors kept: {cleanup_result.duplicates_kept}")

        # Refresh file list after cleanup if changes were applied.
        if args.apply:
            playlist_files = discover_playlist_files(
                source_dir,
                max_files=args.limit_files,
                max_depth=args.max_depth,
                exclude_subdirs=set(args.exclude_subdir),
            )

        if not args.plex_token:
            return 0

    if args.cleanup_plex_empty or args.cleanup_plex_duplicates or args.cleanup_plex_identical or args.cleanup_plex_similar:
        plex_cleanup_result = cleanup_plex_playlists(
            plex_url=args.plex_url,
            token=args.plex_token,
            apply_changes=args.apply,
            remove_empty=args.cleanup_plex_empty,
            merge_duplicates=args.cleanup_plex_duplicates,
            remove_identical=args.cleanup_plex_identical,
            merge_similar=args.cleanup_plex_similar,
            plex_db=plex_db if args.cleanup_plex_identical else None,
        )
        print(f"Plex cleanup mode: {plex_cleanup_result['mode']}")
        print(f"- audio playlists total: {plex_cleanup_result['audio_playlists_total']}")
        if plex_cleanup_result['duplicate_groups']:
            print(f"- duplicate name groups: {plex_cleanup_result['duplicate_groups']}")
            print(f"- duplicate playlists {'deleted' if args.apply else 'to delete'}: {plex_cleanup_result['duplicate_playlists_deleted']}")
            if plex_cleanup_result['duplicate_playlists_recreated']:
                print(f"- merged playlists recreated: {plex_cleanup_result['duplicate_playlists_recreated']}")
        if plex_cleanup_result['identical_groups']:
            print(f"- identical content groups: {plex_cleanup_result['identical_groups']}")
            print(f"- identical playlists {'deleted' if args.apply else 'to delete'}: {plex_cleanup_result['identical_deleted']}")
        if plex_cleanup_result['similar_groups']:
            print(f"- similar name groups (merged): {plex_cleanup_result['similar_groups']}")
            print(f"- similar playlists {'removed' if args.apply else 'to remove'}: {plex_cleanup_result['similar_merged']}")
        print(f"- empty playlists {'deleted' if args.apply else 'to delete'}: {plex_cleanup_result['empty_playlists_deleted']}")
        if args.skip_import:
            if not args.export_plex_dir and not args.prune_plex_from_export_dir:
                return 0

    if args.prune_plex_from_export_dir:
        if not args.plex_token:
            raise SystemExit("--prune-plex-from-export-dir requires --plex-token")
        export_dir = Path(args.export_plex_dir) if args.export_plex_dir else None
        if export_dir is None:
            raise SystemExit("--prune-plex-from-export-dir requires --export-plex-dir")
        existing_files = {p.stem.casefold() for p in export_dir.glob("*.m3u8")}
        plex_playlists = list_audio_playlists(args.plex_url, args.plex_token)
        to_delete: list[dict] = []
        for pl in plex_playlists:
            safe = make_safe_playlist_filename(pl["title"]).casefold()
            if safe not in existing_files:
                to_delete.append(pl)
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"\nSync suppressions MyBook→Plex ({mode}) — dossier: {export_dir}")
        print(f"- fichiers présents dans le dossier: {len(existing_files)}")
        print(f"- playlists Plex total: {len(plex_playlists)}")
        print(f"- playlists à supprimer de Plex: {len(to_delete)}")
        for pl in to_delete:
            action = "SUPPRIMÉ" if args.apply else "SERAIT SUPPRIMÉ"
            print(f"  {action}: {pl['title']}")
            if args.apply:
                delete_playlist(args.plex_url, args.plex_token, pl["rating_key"])
        if args.skip_import and not args.export_plex_dir and not args.import_itunes_xml:
            return 0

    if args.export_plex_dir:
        export_result = export_plex_playlists_to_dir(
            plex_db=plex_db,
            output_dir=Path(args.export_plex_dir),
            apply_changes=args.apply,
            prune_output_dir=args.prune_export_dir,
            plex_url=args.plex_url if args.plex_url else None,
            token=args.plex_token if args.plex_token else None,
        )
        print(f"Plex export mode: {export_result['mode']}")
        print(f"- playlists found: {export_result['playlist_count']}")
        print(f"- playlists exported (incl. smart): {export_result['exported_playlist_count']}")
        if export_result["smart_playlists_resolved"]:
            print(f"- smart playlists resolved via API: {export_result['smart_playlists_resolved']}")
        print(f"- tracks in Plex playlists: {export_result['total_tracks']}")
        print(f"- tracks exported to files: {export_result['exported_tracks']}")
        print(f"- tracks missing from DB mapping: {export_result['missing_tracks']}")
        print(f"- old exported files pruned: {export_result['pruned_files']}")

        if args.skip_import and not args.import_itunes_xml:
            return 0

    if args.import_itunes_xml:
        xml_path = Path(args.import_itunes_xml)
        if not xml_path.exists():
            raise SystemExit(f"iTunes XML not found: {xml_path}")
        if not args.plex_token:
            raise SystemExit("--plex-token is required for --import-itunes-xml")

        # Parse prefix remaps: "OLD:NEW" pairs
        path_maps: list[tuple[str, str]] = []
        for entry in args.itunes_path_map:
            colon = entry.find(":")
            if colon == -1:
                raise SystemExit(f"--itunes-path-map must be OLD:NEW (no colon found in '{entry}')")
            path_maps.append((entry[:colon], entry[colon + 1:]))
        # Default remapping for this system if none specified
        if not path_maps:
            path_maps = [("/mnt/MyBook/", "/mnt/ssd/")]

        print(f"\nImport iTunes XML: {xml_path} ({'APPLY' if args.apply else 'DRY-RUN'})")
        print(f"Path prefix remapping: {path_maps}")
        itunes_result = import_itunes_xml_to_plex(
            xml_path=xml_path,
            plex_db=plex_db,
            plex_url=args.plex_url,
            token=args.plex_token,
            apply_changes=args.apply,
            replace_existing=args.itunes_replace,
            path_maps=path_maps,
            skip_smart=not args.itunes_include_smart,
        )
        print(f"\n--- Résultat import iTunes XML ---")
        print(f"- playlists dans le XML: {itunes_result.playlists_total}")
        print(f"- playlists {'créées' if args.apply else 'à créer'}: {itunes_result.playlists_created}")
        print(f"- skippées (déjà dans Plex): {itunes_result.playlists_skipped_existing}")
        print(f"- skippées (0 piste trouvée): {itunes_result.playlists_skipped_no_tracks}")
        print(f"- pistes totales: {itunes_result.tracks_total}")
        print(f"- pistes matchées: {itunes_result.tracks_matched}")
        print(f"- pistes non trouvées: {itunes_result.tracks_unmatched}")

        if args.skip_import:
            return 0

    temp_db = prepare_temp_db(plex_db)
    machine_id = get_machine_identifier(args.plex_url, args.plex_token)
    results: list[ImportResult] = []

    try:
        with sqlite3.connect(str(temp_db)) as conn:
            basename_index = build_basename_index(conn) if args.map_by_basename else None
            for playlist_file in playlist_files:
                raw_entries = parse_playlist_file(playlist_file)
                resolved_paths = [normalize_entry_to_path(e, playlist_file, entries_base_dir) for e in raw_entries]
                resolved_paths = [p for p in resolved_paths if p is not None]
                mapped = map_paths_to_track_ids(
                    conn,
                    resolved_paths,
                    allow_basename_fallback=args.map_by_basename,
                    basename_to_ids=basename_index,
                )
                track_ids = list(dict.fromkeys(mapped.values()))
                playlist_name = playlist_file.stem

                results.append(
                    ImportResult(
                        playlist_file=playlist_file,
                        playlist_name=playlist_name,
                        total_entries=len(raw_entries),
                        resolved_entries=len(resolved_paths),
                        mapped_tracks=len(track_ids),
                        missing_tracks=max(len(resolved_paths) - len(mapped), 0),
                    )
                )

                if args.apply and track_ids:
                    existing_keys = find_audio_playlist_rating_keys(args.plex_url, args.plex_token, playlist_name)
                    if existing_keys and args.replace:
                        for existing in existing_keys:
                            delete_playlist(args.plex_url, args.plex_token, existing)
                        existing_keys = []
                    if (not existing_keys) or args.replace:
                        try:
                            create_playlist(args.plex_url, args.plex_token, machine_id, playlist_name, track_ids)
                        except Exception as exc:
                            print(f"[warn] création playlist échouée: {playlist_name} ({exc})")

    finally:
        if temp_db.exists():
            temp_db.unlink()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    print(f"Playlist files scanned: {len(results)}")
    imported = 0
    for r in results:
        if r.mapped_tracks > 0:
            imported += 1
        print(
            f"- {r.playlist_name}: entries={r.total_entries}, resolved={r.resolved_entries}, "
            f"mapped={r.mapped_tracks}, missing={r.missing_tracks}"
        )
    print(f"Playable/importable playlists: {imported}/{len(results)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
