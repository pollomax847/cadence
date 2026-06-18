#!/usr/bin/env python3
"""
Gestionnaire de playlists Plex base sur une playlist seed manuelle.

Fonctionnalites:
- Lister les playlists Plex existantes
- Inspecter le contenu d'une playlist manuelle
- Suggérer automatiquement des morceaux proches d'une playlist seed
- Nettoyer les playlists dupliquees en gardant la plus recente
- Synchroniser une playlist manuelle avec une selection calculee

Le mode simulation est actif par defaut pour eviter toute modification
involontaire de la base Plex.
"""

import argparse
import json
import logging
import locale
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
import re

from plex_api import (
    default_plex_url,
    plex_create_audio_playlist,
    plex_delete_playlist,
    plex_find_playlist_rating_keys,
    plex_machine_identifier,
    plex_request,
)


DEFAULT_PLEX_DB = Path(
    "/var/snap/plexmediaserver/common/Library/Application Support/"
    "Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
)

PLAYLIST_PROFILES = {
    "chillout": {
        "min_rating": 6.0,
        "allow_cross_artist": True,
        "include_seed_albums": True,
        "extra_keywords": [
            "chill",
            "chillout",
            "lounge",
            "relax",
            "relaxation",
            "massage",
            "ambient",
            "downtempo",
            "balearic",
            "after",
            "workout",
        ],
    },
    "fiesta": {
        "min_rating": 8.0,
        "allow_cross_artist": True,
        "include_seed_albums": False,
        "extra_keywords": [
            "party",
            "fiesta",
            "dance",
            "latin",
            "summer",
            "baila",
            "club",
        ],
    },
    "running": {
        "min_rating": 8.0,
        "allow_cross_artist": True,
        "include_seed_albums": False,
        "extra_keywords": [
            "running",
            "workout",
            "energy",
            "sport",
            "cardio",
            "power",
            "rise",
        ],
    },
}


@dataclass
class PlaylistTrack:
    metadata_item_id: int
    title: str
    artist: str
    album: str
    rating: float | None
    play_count: int
    last_viewed_at: int | None


class PlexPlaylistManager:
    def __init__(self, plex_db_path: Path, verbose: bool = False):
        self.plex_db_path = plex_db_path
        self.verbose = verbose
        self._temp_db_path: Path | None = None
        self.setup_logging()

    def setup_logging(self) -> None:
        log_level = logging.DEBUG if self.verbose else logging.INFO
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
        self.logger = logging.getLogger(__name__)

    def prepare_db(self, use_copy: bool = True) -> Path:
        if not self.plex_db_path.exists():
            raise FileNotFoundError(f"Base Plex introuvable: {self.plex_db_path}")

        if not use_copy:
            return self.plex_db_path

        temp_db = Path(tempfile.mkstemp(prefix="plex_playlist_", suffix=".db")[1])
        shutil.copy2(self.plex_db_path, temp_db)
        self._temp_db_path = temp_db
        self.logger.debug("Base Plex copiee vers %s", temp_db)
        return temp_db

    def cleanup(self) -> None:
        if self._temp_db_path and self._temp_db_path.exists():
            self._temp_db_path.unlink()
            self.logger.debug("Base temporaire supprimee: %s", self._temp_db_path)

    def connect(self, db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_path))
        conn.create_collation("icu_root", self._icu_root_collation)
        return conn

    @staticmethod
    def _icu_root_collation(left: str | None, right: str | None) -> int:
        left_value = "" if left is None else str(left)
        right_value = "" if right is None else str(right)
        return locale.strcoll(left_value.casefold(), right_value.casefold())

    def get_metadata_items_triggers(self, conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'metadata_items' ORDER BY name"
        ).fetchall()
        return [row[0] for row in rows if row[0]]

    def drop_metadata_items_triggers(self, conn: sqlite3.Connection) -> None:
        trigger_names = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'metadata_items' ORDER BY name"
        ).fetchall()
        for (name,) in trigger_names:
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")

    def restore_metadata_items_triggers(self, conn: sqlite3.Connection, trigger_sql_list: list[str]) -> None:
        for trigger_sql in trigger_sql_list:
            conn.execute(trigger_sql)

    def list_playlists(self, conn: sqlite3.Connection) -> list[dict]:
        query = """
        SELECT
            mi.id,
            mi.title,
            mi.media_item_count,
            datetime(mi.created_at, 'unixepoch') as created_at,
            datetime(mi.updated_at, 'unixepoch') as updated_at,
            mia.account_id
        FROM metadata_items mi
        LEFT JOIN metadata_item_accounts mia ON mia.metadata_item_id = mi.id
        WHERE mi.metadata_type = 15
        ORDER BY mi.updated_at DESC, mi.title
        """
        rows = conn.execute(query).fetchall()
        return [
            {
                "id": row[0],
                "title": row[1],
                "media_item_count": row[2] or 0,
                "created_at": row[3],
                "updated_at": row[4],
                "account_id": row[5],
            }
            for row in rows
        ]

    def get_playlist_by_name(self, conn: sqlite3.Connection, playlist_name: str) -> dict:
        query = """
        SELECT mi.id, mi.title, mi.media_item_count, mia.account_id, mi.created_at, mi.updated_at
        FROM metadata_items mi
        LEFT JOIN metadata_item_accounts mia ON mia.metadata_item_id = mi.id
        WHERE mi.metadata_type = 15 AND LOWER(mi.title) = LOWER(?)
        ORDER BY mi.updated_at DESC
        LIMIT 1
        """
        row = conn.execute(query, (playlist_name,)).fetchone()
        if not row:
            raise ValueError(f"Playlist introuvable: {playlist_name}")
        return {
            "id": row[0],
            "title": row[1],
            "media_item_count": row[2] or 0,
            "account_id": row[3],
            "created_at": row[4],
            "updated_at": row[5],
        }

    def get_playlist_tracks(self, conn: sqlite3.Connection, playlist_id: int) -> list[PlaylistTrack]:
        query = """
        SELECT
            pqg.metadata_item_id,
            track.title,
            COALESCE(artist.title, 'Unknown Artist') as artist_name,
            COALESCE(album.title, 'Unknown Album') as album_title,
            mis.rating,
            COALESCE(mis.view_count, 0) as play_count,
            mis.last_viewed_at
        FROM play_queue_generators pqg
        JOIN metadata_items track ON pqg.metadata_item_id = track.id
        LEFT JOIN metadata_items album ON track.parent_id = album.id
        LEFT JOIN metadata_items artist ON album.parent_id = artist.id
        LEFT JOIN metadata_item_settings mis ON track.guid = mis.guid
        WHERE pqg.playlist_id = ?
        AND pqg.metadata_item_id IS NOT NULL
        ORDER BY artist_name, album_title, track.title
        """
        rows = conn.execute(query, (playlist_id,)).fetchall()
        return [
            PlaylistTrack(
                metadata_item_id=row[0],
                title=row[1],
                artist=row[2],
                album=row[3],
                rating=row[4],
                play_count=row[5],
                last_viewed_at=row[6],
            )
            for row in rows
        ]

    def extract_keywords(self, values: Iterable[str]) -> list[str]:
        stop_words = {
            "with",
            "from",
            "that",
            "this",
            "songs",
            "song",
            "feat",
            "feat.",
            "version",
            "edit",
            "radio",
            "workout",
            "various",
            "artists",
        }
        keywords: set[str] = set()
        for value in values:
            for token in re.findall(r"[a-zA-Z]{4,}", value.lower()):
                if token not in stop_words:
                    keywords.add(token)
        return sorted(keywords)

    def suggest_tracks(
        self,
        conn: sqlite3.Connection,
        playlist_id: int,
        min_rating: float,
        limit: int,
        require_seed_artist: bool,
        extra_keywords: list[str] | None = None,
        include_seed_albums: bool = False,
    ) -> list[PlaylistTrack]:
        playlist = conn.execute("SELECT title FROM metadata_items WHERE id = ?", (playlist_id,)).fetchone()
        seed_tracks = self.get_playlist_tracks(conn, playlist_id)
        seed_track_ids = {track.metadata_item_id for track in seed_tracks}
        seed_artists = sorted({track.artist for track in seed_tracks if track.artist and track.artist != 'Unknown Artist'})
        seed_albums = sorted({track.album for track in seed_tracks if track.album and track.album != 'Unknown Album'})
        seed_titles = sorted({track.title for track in seed_tracks if track.title})
        seed_album_ids = [
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT track.parent_id
                FROM play_queue_generators pqg
                JOIN metadata_items track ON pqg.metadata_item_id = track.id
                WHERE pqg.playlist_id = ?
                AND pqg.metadata_item_id IS NOT NULL
                AND track.parent_id IS NOT NULL
                """,
                (playlist_id,),
            ).fetchall()
        ]

        if not seed_tracks:
            raise ValueError("La playlist seed est vide. Ajoute d'abord quelques morceaux manuellement.")

        if require_seed_artist and not seed_artists:
            raise ValueError("Impossible de calculer les suggestions: aucun artiste seed exploitable.")

        keyword_source = [playlist[0] if playlist else "", *seed_albums, *seed_titles]
        seed_keywords = self.extract_keywords(keyword_source)
        if extra_keywords:
            seed_keywords = sorted(set(seed_keywords) | {keyword.lower() for keyword in extra_keywords})

        album_candidates: list[PlaylistTrack] = []
        if include_seed_albums and seed_album_ids:
            placeholders = ", ".join("?" for _ in seed_album_ids)
            album_rows = conn.execute(
                f"""
                SELECT
                    track.id,
                    track.title,
                    COALESCE(artist.title, 'Unknown Artist') as artist_name,
                    COALESCE(album.title, 'Unknown Album') as album_title,
                    mis.rating,
                    COALESCE(mis.view_count, 0) as play_count,
                    mis.last_viewed_at
                FROM metadata_items track
                LEFT JOIN metadata_items album ON track.parent_id = album.id
                LEFT JOIN metadata_items artist ON album.parent_id = artist.id
                LEFT JOIN metadata_item_settings mis ON track.guid = mis.guid
                WHERE track.parent_id IN ({placeholders})
                AND track.metadata_type = 10
                ORDER BY artist_name, album_title, track.title
                """,
                seed_album_ids,
            ).fetchall()
            album_candidates = [
                PlaylistTrack(
                    metadata_item_id=row[0],
                    title=row[1],
                    artist=row[2],
                    album=row[3],
                    rating=row[4],
                    play_count=row[5],
                    last_viewed_at=row[6],
                )
                for row in album_rows
                if row[0] not in seed_track_ids
            ]

        query = """
        SELECT
            track.id,
            track.title,
            COALESCE(artist.title, 'Unknown Artist') as artist_name,
            COALESCE(album.title, 'Unknown Album') as album_title,
            mis.rating,
            COALESCE(mis.view_count, 0) as play_count,
            mis.last_viewed_at
        FROM metadata_items track
        LEFT JOIN metadata_items album ON track.parent_id = album.id
        LEFT JOIN metadata_items artist ON album.parent_id = artist.id
        LEFT JOIN metadata_item_settings mis ON track.guid = mis.guid
        WHERE track.metadata_type = 10
        AND mis.rating IS NOT NULL
        AND mis.rating >= ?
        """
        rows = conn.execute(query, (min_rating,)).fetchall()

        scored_candidates: list[tuple[float, PlaylistTrack]] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for candidate in album_candidates:
            dedupe_key = (candidate.artist.lower(), candidate.album.lower(), candidate.title.lower())
            if dedupe_key not in seen_keys:
                seen_keys.add(dedupe_key)
                score = 100.0
                if candidate.rating:
                    score += candidate.rating / 2.0
                scored_candidates.append((score, candidate))

        for row in rows:
            candidate = PlaylistTrack(
                metadata_item_id=row[0],
                title=row[1],
                artist=row[2],
                album=row[3],
                rating=row[4],
                play_count=row[5],
                last_viewed_at=row[6],
            )
            if candidate.metadata_item_id in seed_track_ids:
                continue
            if require_seed_artist and candidate.artist not in seed_artists:
                continue

            score = 0.0
            haystack = f"{candidate.artist} {candidate.album} {candidate.title}".lower()
            artist_match = candidate.artist in seed_artists
            album_match = candidate.album in seed_albums
            keyword_hits = 0

            if artist_match:
                score += 4.0
            if album_match:
                score += 3.0
            for keyword in seed_keywords:
                if keyword in haystack:
                    keyword_hits += 1
                    score += 1.5

            if not artist_match and not album_match and keyword_hits == 0:
                continue

            if candidate.rating:
                score += candidate.rating / 2.0
            score += min(candidate.play_count, 10) / 10.0

            dedupe_key = (candidate.artist.lower(), candidate.album.lower(), candidate.title.lower())
            if score > 0 and dedupe_key not in seen_keys:
                seen_keys.add(dedupe_key)
                scored_candidates.append((score, candidate))

        scored_candidates.sort(
            key=lambda item: (
                item[0],
                item[1].rating or 0,
                item[1].play_count,
                item[1].artist,
                item[1].album,
                item[1].title,
            ),
            reverse=True,
        )
        return [candidate for _, candidate in scored_candidates[:limit]]

    def find_duplicate_playlists(self, conn: sqlite3.Connection) -> list[dict]:
        query = """
        SELECT
            title,
            COUNT(*) as duplicate_count,
            GROUP_CONCAT(id) as playlist_ids,
            MAX(updated_at) as keep_updated_at
        FROM metadata_items
        WHERE metadata_type = 15
        GROUP BY LOWER(title)
        HAVING COUNT(*) > 1
        ORDER BY title
        """
        rows = conn.execute(query).fetchall()
        duplicates = []
        for title, duplicate_count, playlist_ids, _ in rows:
            ids = [int(value) for value in playlist_ids.split(",")]
            detail_rows = conn.execute(
                """
                SELECT id, title, media_item_count, created_at, updated_at
                FROM metadata_items
                WHERE id IN ({placeholders})
                ORDER BY updated_at DESC, id DESC
                """.format(placeholders=", ".join("?" for _ in ids)),
                ids,
            ).fetchall()
            keep_id = detail_rows[0][0]
            remove_ids = [row[0] for row in detail_rows[1:]]
            duplicates.append(
                {
                    "title": title,
                    "duplicate_count": duplicate_count,
                    "keep_id": keep_id,
                    "remove_ids": remove_ids,
                    "entries": [
                        {
                            "id": row[0],
                            "title": row[1],
                            "media_item_count": row[2] or 0,
                            "created_at": datetime.fromtimestamp(row[3]).isoformat(sep=" ") if row[3] else None,
                            "updated_at": datetime.fromtimestamp(row[4]).isoformat(sep=" ") if row[4] else None,
                        }
                        for row in detail_rows
                    ],
                }
            )
        return duplicates

    def cleanup_duplicate_playlists(self, conn: sqlite3.Connection, apply_changes: bool) -> list[dict]:
        duplicates = self.find_duplicate_playlists(conn)
        if not duplicates:
            return []

        if not apply_changes:
            return duplicates

        trigger_sql_list = self.get_metadata_items_triggers(conn)
        now_ts = int(datetime.now().timestamp())
        try:
            self.drop_metadata_items_triggers(conn)
            for duplicate in duplicates:
                for playlist_id in duplicate["remove_ids"]:
                    conn.execute("DELETE FROM play_queue_generators WHERE playlist_id = ?", (playlist_id,))
                    conn.execute("DELETE FROM metadata_item_accounts WHERE metadata_item_id = ?", (playlist_id,))
                    conn.execute("DELETE FROM metadata_items WHERE id = ?", (playlist_id,))
                conn.execute(
                    "UPDATE metadata_items SET updated_at = ? WHERE id = ?",
                    (now_ts, duplicate["keep_id"]),
                )
            self.restore_metadata_items_triggers(conn, trigger_sql_list)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return duplicates

    def sync_playlist(
        self,
        conn: sqlite3.Connection,
        playlist_id: int,
        track_ids: Iterable[int],
        dry_run: bool,
    ) -> int:
        track_ids = list(dict.fromkeys(track_ids))
        if dry_run:
            return len(track_ids)

        now_ts = int(datetime.now().timestamp())
        trigger_sql_list = self.get_metadata_items_triggers(conn)
        try:
            self.drop_metadata_items_triggers(conn)
            conn.execute(
                "DELETE FROM play_queue_generators WHERE playlist_id = ? AND metadata_item_id IS NOT NULL",
                (playlist_id,),
            )
            for order_index, metadata_item_id in enumerate(track_ids, start=1):
                conn.execute(
                    """
                    INSERT INTO play_queue_generators (
                        playlist_id, metadata_item_id, `order`, created_at, updated_at, changed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (playlist_id, metadata_item_id, float(order_index), now_ts, now_ts, now_ts),
                )

            conn.execute(
                "UPDATE metadata_items SET media_item_count = ?, updated_at = ? WHERE id = ?",
                (len(track_ids), now_ts, playlist_id),
            )
            self.restore_metadata_items_triggers(conn, trigger_sql_list)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return len(track_ids)

    def _plex_request(self, method: str, url: str, token: str) -> bytes:
        return plex_request(method, url, token)

    def get_plex_machine_identifier(self, plex_url: str, token: str) -> str:
        return plex_machine_identifier(plex_url, token)

    def find_playlist_rating_keys(self, plex_url: str, token: str, playlist_name: str) -> list[str]:
        return plex_find_playlist_rating_keys(plex_url, token, playlist_name)

    def delete_playlist_api(self, plex_url: str, token: str, rating_key: str) -> None:
        plex_delete_playlist(plex_url, token, rating_key)

    def create_playlist_api(
        self,
        plex_url: str,
        token: str,
        machine_identifier: str,
        playlist_name: str,
        track_ids: list[int],
    ) -> None:
        if not track_ids:
            raise ValueError("Aucun morceau a ajouter: la playlist cible serait vide")
        plex_create_audio_playlist(
            plex_url,
            token,
            playlist_name,
            track_ids,
            machine_id=machine_identifier,
            replace=True,
        )

    def sync_playlist_via_api(
        self,
        plex_url: str,
        token: str,
        playlist_name: str,
        track_ids: list[int],
        dry_run: bool,
    ) -> dict:
        machine_id = self.get_plex_machine_identifier(plex_url, token)
        existing_rating_keys = self.find_playlist_rating_keys(plex_url, token, playlist_name)
        existing_rating_key = existing_rating_keys[0] if existing_rating_keys else None
        if dry_run:
            return {
                "mode": "dry-run",
                "playlist": playlist_name,
                "existing_playlist_rating_key": existing_rating_key,
                "existing_playlist_rating_keys": existing_rating_keys,
                "machine_identifier": machine_id,
                "track_count": len(track_ids),
            }

        for rating_key in existing_rating_keys:
            self.delete_playlist_api(plex_url, token, rating_key)

        self.create_playlist_api(plex_url, token, machine_id, playlist_name, track_ids)
        new_rating_keys = self.find_playlist_rating_keys(plex_url, token, playlist_name)
        new_rating_key = new_rating_keys[0] if new_rating_keys else None
        return {
            "mode": "apply",
            "playlist": playlist_name,
            "deleted_previous_rating_key": existing_rating_key,
            "deleted_previous_rating_keys": existing_rating_keys,
            "new_rating_key": new_rating_key,
            "machine_identifier": machine_id,
            "track_count": len(track_ids),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gestionnaire de playlists Plex base sur une playlist seed")
    parser.add_argument("command", choices=["list", "inspect", "suggest", "cleanup-duplicates", "sync-from-seed", "api-sync-from-seed"], help="Action a executer")
    parser.add_argument("--plex-db", default=str(DEFAULT_PLEX_DB), help="Chemin vers la base Plex")
    parser.add_argument("--plex-url", default=default_plex_url(), help="URL Plex pour l'API (ex: http://127.0.0.1:32400)")
    parser.add_argument("--plex-token", help="Token API Plex (X-Plex-Token)")
    parser.add_argument("--playlist", help="Nom de la playlist a inspecter ou synchroniser")
    parser.add_argument("--profile", choices=sorted(PLAYLIST_PROFILES.keys()), help="Profil de suggestion preconfigure")
    parser.add_argument("--min-rating", type=float, default=8.0, help="Rating minimal Plex a retenir (8.0 = 4 etoiles)")
    parser.add_argument("--limit", type=int, default=50, help="Nombre maximum de suggestions")
    parser.add_argument("--include-seed", action="store_true", help="Inclure les morceaux seed dans la sync finale")
    parser.add_argument("--include-seed-albums", action="store_true", help="Inclure automatiquement tous les morceaux des albums deja presents dans la playlist seed")
    parser.add_argument("--allow-cross-artist", action="store_true", help="Ne pas restreindre les suggestions aux artistes presents dans la playlist seed")
    parser.add_argument("--apply", action="store_true", help="Appliquer reellement les changements sur la base choisie")
    parser.add_argument("--write-direct", action="store_true", help="Ecrire directement dans la base au lieu d'utiliser une copie temporaire")
    parser.add_argument("--json", action="store_true", help="Afficher le resultat en JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Mode verbeux")
    return parser.parse_args()


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def print_playlist_tracks(playlist_name: str, tracks: list[PlaylistTrack]) -> None:
    print(f"🎵 Playlist: {playlist_name}")
    print(f"📊 Morceaux: {len(tracks)}")
    for track in tracks:
        rating_display = f"{track.rating / 2:.1f}⭐" if track.rating else "-"
        print(f"  - {track.artist} | {track.album} | {track.title} | {rating_display} | plays={track.play_count}")


def print_suggestions(playlist_name: str, suggestions: list[PlaylistTrack], min_rating: float) -> None:
    print(f"🎯 Suggestions pour: {playlist_name}")
    print(f"📊 Candidats trouves: {len(suggestions)} | seuil rating Plex >= {min_rating}")
    for track in suggestions:
        rating_display = f"{track.rating / 2:.1f}⭐" if track.rating else "-"
        print(f"  - {track.artist} | {track.album} | {track.title} | {rating_display} | plays={track.play_count}")


def main() -> int:
    args = parse_args()
    manager = PlexPlaylistManager(Path(args.plex_db), verbose=args.verbose)
    db_path = manager.prepare_db(use_copy=not args.write_direct)
    profile = PLAYLIST_PROFILES.get(args.profile, {})
    min_rating = args.min_rating if "min_rating" not in profile else profile["min_rating"]
    allow_cross_artist = args.allow_cross_artist or profile.get("allow_cross_artist", False)
    extra_keywords = profile.get("extra_keywords", [])
    include_seed_albums = args.include_seed_albums or profile.get("include_seed_albums", False)

    try:
        with manager.connect(db_path) as conn:
            if args.command == "list":
                playlists = manager.list_playlists(conn)
                if args.json:
                    print_json(playlists)
                else:
                    print(f"📋 Playlists trouvees: {len(playlists)}")
                    for playlist in playlists:
                        print(
                            f"  - #{playlist['id']} | {playlist['title']} | items={playlist['media_item_count']} "
                            f"| account={playlist['account_id']} | maj={playlist['updated_at']}"
                        )
                return 0

            if args.command == "cleanup-duplicates":
                duplicates = manager.cleanup_duplicate_playlists(conn, apply_changes=args.apply)
                if args.json:
                    print_json(duplicates)
                else:
                    if not duplicates:
                        print("✅ Aucun doublon de playlist trouve")
                    else:
                        mode = "suppression reelle" if args.apply else "simulation"
                        print(f"🧹 Doublons detectes ({mode})")
                        for duplicate in duplicates:
                            print(
                                f"  - {duplicate['title']} | garder #{duplicate['keep_id']} | supprimer {duplicate['remove_ids']}"
                            )
                return 0

            if not args.playlist:
                raise ValueError("--playlist est obligatoire pour cette commande")

            playlist = manager.get_playlist_by_name(conn, args.playlist)
            seed_tracks = manager.get_playlist_tracks(conn, playlist["id"])

            if args.command == "inspect":
                if args.json:
                    print_json(
                        {
                            "playlist": playlist,
                            "tracks": [track.__dict__ for track in seed_tracks],
                        }
                    )
                else:
                    print_playlist_tracks(playlist["title"], seed_tracks)
                return 0

            suggestions = manager.suggest_tracks(
                conn,
                playlist["id"],
                min_rating=min_rating,
                limit=args.limit,
                require_seed_artist=not allow_cross_artist,
                extra_keywords=extra_keywords,
                include_seed_albums=include_seed_albums,
            )

            if args.command == "suggest":
                if args.json:
                    print_json(
                        {
                            "playlist": playlist,
                            "seed_tracks": [track.__dict__ for track in seed_tracks],
                            "suggestions": [track.__dict__ for track in suggestions],
                        }
                    )
                else:
                    print_playlist_tracks(playlist["title"], seed_tracks)
                    print()
                    if args.profile:
                        print(f"🧭 Profil: {args.profile}")
                    print_suggestions(playlist["title"], suggestions, min_rating)
                return 0

            track_ids = [track.metadata_item_id for track in seed_tracks] if args.include_seed else []
            track_ids.extend(track.metadata_item_id for track in suggestions)

            if args.command == "api-sync-from-seed":
                if not args.plex_token:
                    raise ValueError("--plex-token est obligatoire pour api-sync-from-seed")
                result = manager.sync_playlist_via_api(
                    plex_url=args.plex_url,
                    token=args.plex_token,
                    playlist_name=playlist["title"],
                    track_ids=track_ids,
                    dry_run=not args.apply,
                )
                if args.json:
                    print_json(result)
                else:
                    mode_label = "simulation API" if not args.apply else "sync API reelle"
                    print(f"🌐 {mode_label}: {result['playlist']} | morceaux={result['track_count']}")
                    if result.get("existing_playlist_rating_key"):
                        print(f"  - playlist existante: {result['existing_playlist_rating_key']}")
                    if result.get("deleted_previous_rating_key"):
                        print(f"  - ancienne supprimee: {result['deleted_previous_rating_key']}")
                    if result.get("new_rating_key"):
                        print(f"  - nouvelle playlist id: {result['new_rating_key']}")
                return 0

            affected = manager.sync_playlist(conn, playlist["id"], track_ids, dry_run=not args.apply)
            mode = "sync reelle" if args.apply else "simulation"
            print(f"🔄 {mode}: {playlist['title']} recevrait {affected} morceau(x)")
            return 0
    except Exception as exc:
        manager.logger.error("❌ %s", exc)
        return 1
    finally:
        manager.cleanup()


if __name__ == "__main__":
    sys.exit(main())