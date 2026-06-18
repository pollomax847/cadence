from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from time import sleep


def default_plex_url() -> str:
    if Path("/.dockerenv").is_file():
        return "http://host.docker.internal:32400"
    return "http://localhost:32400"


def plex_request(method: str, url: str, token: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url=url, method=method)
    request.add_header("X-Plex-Token", token)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def plex_machine_identifier(url: str, token: str) -> str:
    root = ET.fromstring(plex_request("GET", f"{url.rstrip('/')}/", token))
    machine_id = root.attrib.get("machineIdentifier")
    if not machine_id:
        raise RuntimeError("machineIdentifier missing from Plex API response")
    return machine_id


def plex_find_playlist_rating_keys(
    url: str,
    token: str,
    title: str,
    playlist_type: str = "audio",
) -> list[str]:
    root = ET.fromstring(plex_request("GET", f"{url.rstrip('/')}/playlists", token))
    rating_keys: list[str] = []
    expected_title = (title or "").strip().casefold()
    for item in root.findall("Playlist") + root.findall("Directory"):
        if item.attrib.get("playlistType") != playlist_type:
            continue
        if item.attrib.get("title", "").strip().casefold() != expected_title:
            continue
        rating_key = item.attrib.get("ratingKey")
        if rating_key:
            rating_keys.append(rating_key)
    return rating_keys


def plex_find_playlist_rating_key(
    url: str,
    token: str,
    title: str,
    playlist_type: str = "audio",
) -> str | None:
    rating_keys = plex_find_playlist_rating_keys(url, token, title, playlist_type=playlist_type)
    return rating_keys[0] if rating_keys else None


def plex_delete_playlist(url: str, token: str, rating_key: str) -> None:
    plex_request("DELETE", f"{url.rstrip('/')}/playlists/{rating_key}", token)


def _normalize_playlist_base(title: str) -> str:
    text = str(title or "")
    text = re.sub(r"\s*\(\d+\s+titres\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\[fusion\]\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(deduped\)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def plex_find_playlist_rating_keys_by_base(
    url: str,
    token: str,
    title: str,
    playlist_type: str = "audio",
) -> list[str]:
    root = ET.fromstring(plex_request("GET", f"{url.rstrip('/')}/playlists", token))
    rating_keys: list[str] = []
    expected_base = _normalize_playlist_base(title)
    for item in root.findall("Playlist") + root.findall("Directory"):
        if item.attrib.get("playlistType") != playlist_type:
            continue
        if _normalize_playlist_base(item.attrib.get("title", "")) != expected_base:
            continue
        rating_key = item.attrib.get("ratingKey")
        if rating_key:
            rating_keys.append(rating_key)
    return rating_keys


def plex_consolidate_playlist_duplicates(
    url: str,
    token: str,
    title: str,
    keep_rating_key: str,
    playlist_type: str = "audio",
) -> None:
    keep = str(keep_rating_key or "")
    if not keep:
        return

    for _ in range(3):
        matches = plex_find_playlist_rating_keys_by_base(url, token, title, playlist_type=playlist_type)
        removed = False
        for rating_key in matches:
            if rating_key == keep:
                continue
            plex_delete_playlist(url, token, rating_key)
            removed = True
        if not removed:
            break
        sleep(0.2)


def plex_list_audio_playlists(url: str, token: str) -> list[dict[str, str]]:
    root = ET.fromstring(plex_request("GET", f"{url.rstrip('/')}/playlists", token))
    playlists: list[dict[str, str]] = []
    for item in root.findall("Playlist") + root.findall("Directory"):
        if item.attrib.get("playlistType") != "audio":
            continue
        title = str(item.attrib.get("title") or "").strip()
        rating_key = str(item.attrib.get("ratingKey") or "").strip()
        if not title or not rating_key:
            continue
        playlists.append({"title": title, "rating_key": rating_key})
    return playlists


def plex_create_audio_playlist(
    url: str,
    token: str,
    title: str,
    track_ids: list[int],
    *,
    machine_id: str | None = None,
    replace: bool = False,
    batch_size: int = 200,
) -> str:
    seen: set[int] = set()
    deduped_track_ids: list[int] = []
    for track_id in track_ids:
        tid = int(track_id)
        if tid in seen:
            continue
        seen.add(tid)
        deduped_track_ids.append(tid)
    track_ids = deduped_track_ids

    if replace:
        existing_keys = plex_find_playlist_rating_keys_by_base(url, token, title, playlist_type="audio")
        for existing in existing_keys:
            plex_delete_playlist(url, token, existing)

    if not track_ids:
        raise RuntimeError("No tracks to add - playlist not created")

    resolved_machine_id = machine_id or plex_machine_identifier(url, token)
    first_uri = (
        f"server://{resolved_machine_id}/com.plexapp.plugins.library/library/metadata/{track_ids[0]}"
    )
    create_query = urllib.parse.urlencode(
        {"type": "audio", "title": title, "smart": "0", "uri": first_uri}
    )
    created = ET.fromstring(
        plex_request("POST", f"{url.rstrip('/')}/playlists?{create_query}", token)
    )

    playlist_node = created.find("Playlist") or created.find("Directory")
    playlist_rating_key = playlist_node.attrib.get("ratingKey") if playlist_node is not None else None
    if not playlist_rating_key:
        # Some Plex servers create the playlist but omit ratingKey in the POST response.
        # Resolve it via title lookup with short retries to handle eventual consistency.
        for _ in range(3):
            matches = plex_find_playlist_rating_keys(url, token, title, playlist_type="audio")
            if matches:
                numeric_matches = [key for key in matches if key.isdigit()]
                if numeric_matches:
                    playlist_rating_key = max(numeric_matches, key=int)
                else:
                    playlist_rating_key = matches[-1]
                break
            sleep(0.25)

    if not playlist_rating_key:
        raise RuntimeError(f"Playlist created but ratingKey missing for: {title!r}")

    remaining = track_ids[1:]
    for idx in range(0, len(remaining), batch_size):
        batch_ids = remaining[idx : idx + batch_size]
        metadata_csv = ",".join(str(track_id) for track_id in batch_ids)
        uri = f"server://{resolved_machine_id}/com.plexapp.plugins.library/library/metadata/{metadata_csv}"
        batch_query = urllib.parse.urlencode({"uri": uri})
        plex_request("PUT", f"{url.rstrip('/')}/playlists/{playlist_rating_key}/items?{batch_query}", token)

    plex_consolidate_playlist_duplicates(
        url,
        token,
        title,
        str(playlist_rating_key),
        playlist_type="audio",
    )

    return playlist_rating_key


def plex_get_playlist_track_ids(url: str, token: str, rating_key: str) -> list[int]:
    root = ET.fromstring(plex_request("GET", f"{url.rstrip('/')}/playlists/{rating_key}/items", token))
    track_ids: list[int] = []
    for item in root.findall("Track") + root.findall("Video") + root.findall("Photo") + root.findall("Metadata"):
        item_rating_key = item.attrib.get("ratingKey", "")
        if item_rating_key.isdigit():
            track_ids.append(int(item_rating_key))
    return track_ids


def plex_get_playlist_item_id_set(url: str, token: str, rating_key: str) -> set[int]:
    return set(plex_get_playlist_track_ids(url, token, rating_key))