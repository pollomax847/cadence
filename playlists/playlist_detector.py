#!/usr/bin/env python3
"""
playlist_detector.py — Module partagé de détection & matching de playlists.

Couvre 4 domaines où la détection du projet était limitée :

1. DÉCOUVERTE sur disque
   - Scan récursif configurable (profondeur, exclusions, follow_symlinks)
   - Formats : .m3u .m3u8 .pls .xspf .wpl .asx .zpl .cue
   - Détection heuristique du type même sans extension connue

2. PARSING robuste
   - Auto-détection d'encodage : BOM (UTF-8/16), UTF-8, Latin-1, CP1252
   - M3U étendu : #EXTINF, #EXTM3U, #PLAYLIST, #EXTALB, #EXTART, #EXTGENRE
   - PLS, XSPF, WPL, ASX, CUE
   - Conversion automatique des chemins Windows (C:\\..., \\\\share\\..., file:///)

3. MATCHING multi-stratégie (M3U/iTunes → fichiers réels)
   - Index O(1) de la bibliothèque par : chemin absolu, nom de fichier,
     (artiste, titre) normalisés, (artiste, titre, durée)
   - Normalisation Unicode (NFKD + casefold + stripping ponctuation)
   - Fallbacks gradués : exact → normalisé → basename → tags → fuzzy ratio

4. DÉTECTION côté Plex (API)
   - Liste TOUS les types (audio/video/photo), smart ET classiques
   - Récupère durée, icon, ajoutée/modifiée, taille pour chaque playlist

Usage en bibliothèque :

    from playlist_detector import (
        PlaylistDetector, MusicIndex, PlexPlaylistClient,
        parse_playlist, discover_playlists,
    )

    # Découverte
    files = discover_playlists("/home/me/Musiques", recursive=True)

    # Parsing robuste
    pl = parse_playlist(files[0])
    for entry in pl.entries:
        print(entry.path_or_uri, entry.artist, entry.title, entry.duration)

    # Matching
    index = MusicIndex.build("/home/me/Musiques")
    hit = index.match(entry)
    if hit:
        print("→", hit.path, "(via", hit.strategy, ")")

    # Plex
    client = PlexPlaylistClient("http://127.0.0.1:32400", token="XXX")
    for pl in client.list_all():
        print(pl.title, pl.type, pl.smart, pl.leaf_count)

Utilisable aussi en CLI via `detect_playlists.py`.
"""

from __future__ import annotations

import configparser
import difflib
import io
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


# =============================================================================
#                           Constantes & helpers
# =============================================================================

PLAYLIST_EXTENSIONS = {
    ".m3u", ".m3u8", ".pls", ".xspf", ".wpl", ".asx", ".zpl", ".cue",
}

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".wav", ".wma", ".aiff", ".aif", ".alac", ".ape", ".dsd", ".dsf",
}

# Dossiers typiquement à ignorer
DEFAULT_EXCLUDES = {
    ".git", ".svn", ".hg", "__pycache__", "node_modules", ".venv",
    ".thumbnails", ".Trash", ".Trash-1000", "System Volume Information",
    "$RECYCLE.BIN", ".DS_Store", ".Spotlight-V100", ".fseventsd",
}

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_FEAT_RE = re.compile(r"\s*[\(\[](feat|ft|featuring|with)\.?\s+[^\)\]]*[\)\]]",
                      re.IGNORECASE)
_TRACKNUM_RE = re.compile(r"^\s*\d{1,3}\s*[-._\s]+")


def normalize_text(value: str) -> str:
    """Normalisation agressive pour le matching : NFKD + casefold + sans ponctuation."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = _FEAT_RE.sub("", value)
    value = _PUNCT_RE.sub(" ", value)
    value = _WS_RE.sub(" ", value).strip().casefold()
    return value


def normalize_filename_stem(filename: str) -> str:
    """Nettoie un nom de fichier pour matching : retire numéro de piste, ext, etc."""
    stem = Path(filename).stem
    stem = _TRACKNUM_RE.sub("", stem)
    return normalize_text(stem)


def detect_encoding_and_read(path: Path) -> tuple[str, str]:
    """Lit un fichier texte en détectant son encodage. Retourne (texte, encoding)."""
    raw = path.read_bytes()
    # BOM checks
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace"), "utf-16"
    # Essais successifs
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def convert_windows_path(value: str) -> str:
    """Convertit un chemin Windows vers style POSIX (purement lexical)."""
    value = value.replace("\\", "/")
    # Lettre de lecteur (C:/ → /c/)
    if re.match(r"^[A-Za-z]:/", value):
        value = "/" + value[0].lower() + value[2:]
    # UNC \\\\server\\share → //server/share (déjà fait par le replace)
    return value


def from_uri(value: str) -> str:
    """Convertit un URI file:// vers un chemin local."""
    if value.startswith("file://"):
        parsed = urllib.parse.urlparse(value)
        path = urllib.parse.unquote(parsed.path)
        # Windows : file:///C:/path
        if re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]
        return path
    return value


# =============================================================================
#                           Modèles de données
# =============================================================================

@dataclass
class PlaylistEntry:
    """Une entrée de playlist (une piste)."""
    path_or_uri: str                # Valeur brute telle que lue
    resolved_path: Path | None = None  # Chemin absolu résolu (si applicable)
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    duration: int | None = None      # en secondes
    extra: dict = field(default_factory=dict)


@dataclass
class ParsedPlaylist:
    """Une playlist complète après parsing."""
    path: Path
    format: str                      # "m3u" | "m3u8" | "pls" | "xspf" | "wpl" | "asx" | "cue"
    encoding: str
    name: str
    entries: list[PlaylistEntry] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def track_count(self) -> int:
        return len(self.entries)


@dataclass
class IndexedTrack:
    """Une piste indexée depuis la bibliothèque musicale."""
    path: Path
    artist: str = ""
    title: str = ""
    album: str = ""
    duration: int = 0


@dataclass
class MatchResult:
    track: IndexedTrack
    strategy: str                    # "exact" | "normalized" | "basename" | "tags" | "fuzzy"
    confidence: float                # 0.0 — 1.0


# =============================================================================
#                           Découverte de fichiers
# =============================================================================

def discover_playlists(
    root: Path | str,
    *,
    recursive: bool = True,
    max_depth: int = 0,
    follow_symlinks: bool = False,
    excludes: Iterable[str] | None = None,
    extensions: Iterable[str] | None = None,
    progress: bool = False,
) -> list[Path]:
    """Scan un dossier pour trouver des playlists.

    Args:
        root: racine du scan.
        recursive: scanner les sous-dossiers.
        max_depth: 0 = illimité.
        follow_symlinks: traverser les liens symboliques.
        excludes: noms de dossiers à ignorer (union avec DEFAULT_EXCLUDES).
        extensions: extensions à détecter (défaut: PLAYLIST_EXTENSIONS).
        progress: afficher une progression sur stderr.

    Returns:
        Liste triée des chemins de playlists détectées.
    """
    root = Path(root).expanduser()
    if not root.exists():
        return []

    exts = {e.lower() for e in (extensions or PLAYLIST_EXTENSIONS)}
    excluded = {name.casefold() for name in DEFAULT_EXCLUDES}
    if excludes:
        excluded |= {e.casefold() for e in excludes}

    found: list[Path] = []
    base_depth = len(root.parts)
    dirs_seen = 0

    for current, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        current_path = Path(current)
        depth = len(current_path.parts) - base_depth

        dirnames[:] = [d for d in dirnames if d.casefold() not in excluded]
        if not recursive and depth >= 1:
            dirnames[:] = []
        elif max_depth > 0 and depth >= max_depth:
            dirnames[:] = []

        dirs_seen += 1
        if progress and dirs_seen % 500 == 0:
            print(f"  [scan] {dirs_seen} dossiers, {len(found)} playlists",
                  file=sys.stderr, flush=True)

        for name in filenames:
            if Path(name).suffix.lower() in exts:
                found.append(current_path / name)

    found.sort()
    return found


# =============================================================================
#                           Parsing
# =============================================================================

def _parse_m3u(text: str) -> tuple[str, list[PlaylistEntry], dict]:
    entries: list[PlaylistEntry] = []
    name = ""
    extra: dict = {}

    current: dict = {}
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue
        if line.startswith("#"):
            upper = line.upper()
            if upper.startswith("#EXTM3U"):
                continue
            if upper.startswith("#PLAYLIST:"):
                name = line.split(":", 1)[1].strip()
                continue
            if upper.startswith("#EXTINF:"):
                # #EXTINF:duration [key=value...],Artist - Title
                body = line[len("#EXTINF:"):]
                head, _, label = body.partition(",")
                duration_str = head.strip().split()[0] if head.strip() else ""
                try:
                    current["duration"] = int(float(duration_str))
                except ValueError:
                    pass
                label = label.strip()
                if " - " in label:
                    artist, _, title = label.partition(" - ")
                    current["artist"] = artist.strip()
                    current["title"] = title.strip()
                else:
                    current["title"] = label
                continue
            if upper.startswith("#EXTART:"):
                current["artist"] = line.split(":", 1)[1].strip()
                continue
            if upper.startswith("#EXTALB:"):
                current["album"] = line.split(":", 1)[1].strip()
                continue
            if upper.startswith("#EXTGENRE:"):
                current.setdefault("extra", {})["genre"] = line.split(":", 1)[1].strip()
                continue
            # commentaire inconnu : ignoré
            continue
        # Ligne de chemin
        entries.append(PlaylistEntry(
            path_or_uri=line,
            artist=current.get("artist"),
            title=current.get("title"),
            album=current.get("album"),
            duration=current.get("duration"),
            extra=current.get("extra", {}),
        ))
        current = {}
    return name, entries, extra


def _parse_pls(text: str) -> tuple[str, list[PlaylistEntry], dict]:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return "", [], {}

    section = None
    for s in parser.sections():
        if s.lower() == "playlist":
            section = s
            break
    if not section:
        return "", [], {}

    entries_map: dict[int, PlaylistEntry] = {}
    name = ""
    extra: dict = {"num_entries": parser.get(section, "NumberOfEntries", fallback="")}

    for key, value in parser.items(section):
        k = key.lower()
        if k.startswith("file"):
            idx = int(re.sub(r"\D", "", k) or "0")
            entries_map.setdefault(idx, PlaylistEntry(path_or_uri="")).path_or_uri = value.strip()
        elif k.startswith("title"):
            idx = int(re.sub(r"\D", "", k) or "0")
            entries_map.setdefault(idx, PlaylistEntry(path_or_uri="")).title = value.strip()
        elif k.startswith("length"):
            idx = int(re.sub(r"\D", "", k) or "0")
            try:
                entries_map.setdefault(idx, PlaylistEntry(path_or_uri="")).duration = int(value)
            except ValueError:
                pass
        elif k == "playlistname":
            name = value.strip()

    entries = [entries_map[k] for k in sorted(entries_map) if entries_map[k].path_or_uri]
    return name, entries, extra


def _parse_xspf(text: str) -> tuple[str, list[PlaylistEntry], dict]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "", [], {}

    def local(tag: str) -> str:
        return tag.split("}", 1)[-1]

    name = ""
    for child in root:
        if local(child.tag) == "title" and child.text:
            name = child.text.strip()

    entries: list[PlaylistEntry] = []
    for track in root.iter():
        if local(track.tag) != "track":
            continue
        location = title = creator = album = ""
        duration = None
        for el in track:
            tag = local(el.tag)
            if tag == "location" and el.text:
                location = el.text.strip()
            elif tag == "title" and el.text:
                title = el.text.strip()
            elif tag == "creator" and el.text:
                creator = el.text.strip()
            elif tag == "album" and el.text:
                album = el.text.strip()
            elif tag == "duration" and el.text:
                try:
                    duration = int(el.text) // 1000  # xspf: ms
                except ValueError:
                    pass
        if location:
            entries.append(PlaylistEntry(
                path_or_uri=location,
                artist=creator or None,
                title=title or None,
                album=album or None,
                duration=duration,
            ))
    return name, entries, {}


def _parse_wpl_asx(text: str) -> tuple[str, list[PlaylistEntry], dict]:
    # WPL (Windows Media Playlist) ≈ SMIL simplifié ; ASX ≈ XML
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "", [], {}

    def local(t: str) -> str:
        return t.split("}", 1)[-1].lower()

    name = ""
    entries: list[PlaylistEntry] = []
    for el in root.iter():
        tag = local(el.tag)
        if tag == "title" and el.text and not name:
            name = el.text.strip()
        if tag in {"media", "ref"}:
            src = el.attrib.get("src") or el.attrib.get("href") or el.attrib.get("Src")
            if src:
                entries.append(PlaylistEntry(path_or_uri=src.strip()))
    return name, entries, {}


def _parse_cue(text: str) -> tuple[str, list[PlaylistEntry], dict]:
    """Parse un fichier CUE (un seul FILE, plusieurs TRACKs)."""
    name = ""
    current_file = ""
    entries: list[PlaylistEntry] = []
    current: PlaylistEntry | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if line.upper().startswith("TITLE ") and not name and not current:
            name = line[6:].strip().strip('"')
        elif line.upper().startswith("FILE "):
            m = re.match(r'FILE\s+"([^"]+)"', line, re.IGNORECASE) or \
                re.match(r"FILE\s+(\S+)", line, re.IGNORECASE)
            if m:
                current_file = m.group(1)
        elif line.upper().startswith("TRACK "):
            if current:
                entries.append(current)
            current = PlaylistEntry(path_or_uri=current_file)
        elif line.upper().startswith("TITLE ") and current:
            current.title = line[6:].strip().strip('"')
        elif line.upper().startswith("PERFORMER ") and current:
            current.artist = line[10:].strip().strip('"')
    if current:
        entries.append(current)
    return name, entries, {"cue": True}


def parse_playlist(path: Path | str) -> ParsedPlaylist:
    """Parse une playlist quel que soit son format. Ne résout pas les chemins."""
    path = Path(path)
    text, encoding = detect_encoding_and_read(path)
    ext = path.suffix.lower()

    # Détection par contenu si extension inconnue
    if ext not in PLAYLIST_EXTENSIONS:
        head = text[:200].lstrip()
        if head.startswith("#EXTM3U"):
            ext = ".m3u"
        elif head.startswith("[playlist]"):
            ext = ".pls"
        elif "<playlist" in head and "xspf" in head:
            ext = ".xspf"
        elif "<smil" in head or "<asx" in head.lower():
            ext = ".wpl"
        else:
            ext = ".m3u"  # fallback raisonnable

    if ext in {".m3u", ".m3u8"}:
        fmt = ext.lstrip(".")
        name, entries, extra = _parse_m3u(text)
    elif ext == ".pls":
        fmt = "pls"
        name, entries, extra = _parse_pls(text)
    elif ext == ".xspf":
        fmt = "xspf"
        name, entries, extra = _parse_xspf(text)
    elif ext in {".wpl", ".asx", ".zpl"}:
        fmt = ext.lstrip(".")
        name, entries, extra = _parse_wpl_asx(text)
    elif ext == ".cue":
        fmt = "cue"
        name, entries, extra = _parse_cue(text)
    else:
        fmt = "unknown"
        name, entries, extra = _parse_m3u(text)

    if not name:
        name = path.stem

    return ParsedPlaylist(
        path=path, format=fmt, encoding=encoding,
        name=name, entries=entries, extra=extra,
    )


# =============================================================================
#                           Résolution de chemins
# =============================================================================

def resolve_entry_path(
    entry: str,
    playlist_path: Path,
    base_dir: Path | None = None,
    path_mappings: list[tuple[str, str]] | None = None,
) -> Path | None:
    """Convertit une entrée texte de playlist en Path absolu (lexical only)."""
    value = entry.replace("\x00", "").strip().strip('"')
    if not value:
        return None

    # URI
    value = from_uri(value)

    # Mappings explicites (préfixe → préfixe)
    if path_mappings:
        for src, dst in path_mappings:
            if value.startswith(src):
                value = dst + value[len(src):]
                break

    # Windows-style
    if "\\" in value or re.match(r"^[A-Za-z]:", value):
        value = convert_windows_path(value)

    try:
        p = Path(value)
        if not p.is_absolute():
            root = base_dir if base_dir else playlist_path.parent
            p = Path(os.path.abspath(os.path.normpath(str(root / p))))
        else:
            p = Path(os.path.abspath(os.path.normpath(str(p))))
        return p
    except (OSError, ValueError, RuntimeError):
        return None


# =============================================================================
#                           Index de bibliothèque + matching
# =============================================================================

class MusicIndex:
    """Index O(1) d'une bibliothèque musicale pour matching rapide."""

    def __init__(self) -> None:
        self.tracks: list[IndexedTrack] = []
        self._by_path: dict[str, IndexedTrack] = {}
        self._by_basename: dict[str, list[IndexedTrack]] = {}
        self._by_norm_basename: dict[str, list[IndexedTrack]] = {}
        self._by_tags: dict[tuple[str, str], list[IndexedTrack]] = {}
        self._norm_titles: list[tuple[str, IndexedTrack]] = []

    @classmethod
    def build(
        cls,
        root: Path | str,
        *,
        read_tags: bool = False,
        progress: bool = False,
    ) -> "MusicIndex":
        """Construit l'index depuis un dossier musical.

        Par défaut, n'utilise que le nom de fichier (rapide, sans lecture de tags).
        Avec read_tags=True, tente d'extraire artiste/titre avec mutagen (plus lent).
        """
        idx = cls()
        root = Path(root).expanduser()
        if not root.exists():
            return idx

        tag_reader = None
        if read_tags:
            try:
                from mutagen import File as MutagenFile  # type: ignore
                tag_reader = MutagenFile
            except ImportError:
                tag_reader = None

        count = 0
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.casefold() not in {e.casefold() for e in DEFAULT_EXCLUDES}]
            for name in filenames:
                ext = Path(name).suffix.lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                path = Path(current) / name
                track = IndexedTrack(path=path)
                if tag_reader:
                    try:
                        meta = tag_reader(path)
                        if meta and getattr(meta, "tags", None):
                            track.artist = str(meta.tags.get("artist", [""])[0] if hasattr(meta.tags, "get") else "")
                            track.title = str(meta.tags.get("title", [""])[0] if hasattr(meta.tags, "get") else "")
                            track.album = str(meta.tags.get("album", [""])[0] if hasattr(meta.tags, "get") else "")
                        if meta and getattr(meta, "info", None):
                            track.duration = int(meta.info.length or 0)
                    except Exception:
                        pass
                idx._add(track)
                count += 1
                if progress and count % 2000 == 0:
                    print(f"  [index] {count} pistes", file=sys.stderr, flush=True)
        return idx

    def _add(self, track: IndexedTrack) -> None:
        self.tracks.append(track)
        self._by_path[str(track.path).casefold()] = track

        basename = track.path.name.casefold()
        self._by_basename.setdefault(basename, []).append(track)

        norm_name = normalize_filename_stem(track.path.name)
        if norm_name:
            self._by_norm_basename.setdefault(norm_name, []).append(track)

        if track.artist and track.title:
            key = (normalize_text(track.artist), normalize_text(track.title))
            self._by_tags.setdefault(key, []).append(track)

        if track.title:
            self._norm_titles.append((normalize_text(track.title), track))

    # ------------------------------------------------------------------
    def match(
        self,
        entry: PlaylistEntry,
        *,
        playlist_path: Path | None = None,
        path_mappings: list[tuple[str, str]] | None = None,
        fuzzy_cutoff: float = 0.85,
    ) -> MatchResult | None:
        """Essaie plusieurs stratégies, retourne le meilleur match ou None."""

        # Stratégie 1 — chemin exact
        resolved = entry.resolved_path
        if resolved is None and playlist_path:
            resolved = resolve_entry_path(
                entry.path_or_uri, playlist_path,
                path_mappings=path_mappings,
            )
        if resolved:
            hit = self._by_path.get(str(resolved).casefold())
            if hit:
                return MatchResult(hit, "exact", 1.0)

        # Stratégie 2 — basename exact
        basename = Path(entry.path_or_uri).name.casefold()
        candidates = self._by_basename.get(basename, [])
        if len(candidates) == 1:
            return MatchResult(candidates[0], "basename", 0.95)

        # Stratégie 3 — basename normalisé (retire numéro de piste, ponctuation)
        norm = normalize_filename_stem(entry.path_or_uri)
        if norm:
            cands = self._by_norm_basename.get(norm, [])
            if len(cands) == 1:
                return MatchResult(cands[0], "normalized", 0.90)
            if len(cands) > 1 and entry.artist:
                # Désambiguïsation via artiste
                want_artist = normalize_text(entry.artist)
                for c in cands:
                    if want_artist in normalize_text(c.artist):
                        return MatchResult(c, "normalized+artist", 0.88)

        # Stratégie 4 — tags (artiste, titre)
        if entry.artist and entry.title:
            key = (normalize_text(entry.artist), normalize_text(entry.title))
            cands = self._by_tags.get(key, [])
            if cands:
                # Si durée disponible, préférer le plus proche
                if entry.duration and len(cands) > 1:
                    best = min(cands, key=lambda t: abs((t.duration or 0) - entry.duration))
                    return MatchResult(best, "tags+duration", 0.85)
                return MatchResult(cands[0], "tags", 0.80)

        # Stratégie 5 — fuzzy sur le titre seul
        if entry.title and self._norm_titles:
            want = normalize_text(entry.title)
            best_ratio = 0.0
            best_track: IndexedTrack | None = None
            # Limite pour ne pas exploser en coût — top 50 titres proches via difflib
            candidates_norm = [t[0] for t in self._norm_titles]
            close = difflib.get_close_matches(want, candidates_norm, n=5, cutoff=fuzzy_cutoff)
            if close:
                for cand_norm in close:
                    for (nt, track) in self._norm_titles:
                        if nt == cand_norm:
                            ratio = difflib.SequenceMatcher(None, want, nt).ratio()
                            if ratio > best_ratio:
                                best_ratio = ratio
                                best_track = track
                            break
            if best_track and best_ratio >= fuzzy_cutoff:
                return MatchResult(best_track, "fuzzy", best_ratio)

        return None

    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.tracks)


# =============================================================================
#                           Client Plex
# =============================================================================

@dataclass
class PlexPlaylist:
    rating_key: str
    title: str
    type: str                        # "audio" | "video" | "photo"
    smart: bool
    leaf_count: int
    duration_ms: int = 0
    added_at: int = 0
    updated_at: int = 0
    composite: str = ""


class PlexPlaylistClient:
    """Client minimaliste pour lister les playlists Plex (tous types)."""

    def __init__(self, url: str, token: str, timeout: int = 30) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _get(self, path: str) -> bytes:
        req = urllib.request.Request(f"{self.url}{path}")
        req.add_header("X-Plex-Token", self.token)
        req.add_header("Accept", "application/xml")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def list_all(
        self,
        *,
        types: Iterable[str] = ("audio", "video", "photo"),
        include_smart: bool = True,
    ) -> list[PlexPlaylist]:
        """Liste TOUTES les playlists, y compris smart et collections."""
        data = self._get("/playlists?includeCollections=1")
        root = ET.fromstring(data)

        type_filter = {t.casefold() for t in types}
        results: list[PlexPlaylist] = []

        for item in root.findall("Playlist") + root.findall("Directory"):
            ptype = (item.attrib.get("playlistType") or "").casefold()
            if ptype and ptype not in type_filter:
                continue

            smart = item.attrib.get("smart") == "1"
            if smart and not include_smart:
                continue

            def _int(key: str) -> int:
                v = item.attrib.get(key, "")
                return int(v) if v and v.isdigit() else 0

            results.append(PlexPlaylist(
                rating_key=item.attrib.get("ratingKey", ""),
                title=item.attrib.get("title", ""),
                type=ptype,
                smart=smart,
                leaf_count=_int("leafCount"),
                duration_ms=_int("duration"),
                added_at=_int("addedAt"),
                updated_at=_int("updatedAt"),
                composite=item.attrib.get("composite", ""),
            ))

        return results

    def get_tracks(self, rating_key: str) -> list[dict]:
        data = self._get(f"/playlists/{rating_key}/items")
        root = ET.fromstring(data)
        tracks: list[dict] = []
        for item in list(root):
            part = item.find(".//Part")
            tracks.append({
                "title": item.attrib.get("title", ""),
                "artist": item.attrib.get("grandparentTitle", item.attrib.get("originalTitle", "")),
                "album": item.attrib.get("parentTitle", ""),
                "duration": int(item.attrib.get("duration", "0") or 0) // 1000,
                "file": part.attrib.get("file", "") if part is not None else "",
            })
        return tracks


# =============================================================================
#                           Façade haut niveau
# =============================================================================

class PlaylistDetector:
    """Façade simple pour les usages courants."""

    def __init__(
        self,
        scan_root: Path | str | None = None,
        library_root: Path | str | None = None,
        *,
        path_mappings: list[tuple[str, str]] | None = None,
    ) -> None:
        self.scan_root = Path(scan_root) if scan_root else None
        self.library_root = Path(library_root) if library_root else None
        self.path_mappings = path_mappings or []
        self._index: MusicIndex | None = None

    @property
    def index(self) -> MusicIndex:
        if self._index is None:
            if not self.library_root:
                raise RuntimeError("library_root non défini")
            self._index = MusicIndex.build(self.library_root)
        return self._index

    def scan(self, **kwargs) -> list[Path]:
        if not self.scan_root:
            raise RuntimeError("scan_root non défini")
        return discover_playlists(self.scan_root, **kwargs)

    def parse_and_match(self, path: Path | str) -> tuple[ParsedPlaylist, list[MatchResult | None]]:
        pl = parse_playlist(path)
        matches = [
            self.index.match(
                e,
                playlist_path=pl.path,
                path_mappings=self.path_mappings,
            )
            for e in pl.entries
        ]
        return pl, matches


# =============================================================================
if __name__ == "__main__":
    # Mode diagnostic minimal — pour l'interface complète, utiliser
    # detect_playlists.py
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", help="Fichier playlist ou dossier à scanner")
    args = p.parse_args()

    target = Path(args.path)
    if target.is_dir():
        files = discover_playlists(target, progress=True)
        print(f"{len(files)} playlists trouvées :")
        for f in files:
            print(" ", f)
    else:
        pl = parse_playlist(target)
        print(f"Format    : {pl.format}")
        print(f"Encodage  : {pl.encoding}")
        print(f"Nom       : {pl.name}")
        print(f"Entrées   : {pl.track_count}")
        for i, e in enumerate(pl.entries[:10], 1):
            print(f"  {i:3}. {e.artist or '?'} — {e.title or Path(e.path_or_uri).name}")
        if pl.track_count > 10:
            print(f"  ... (+{pl.track_count - 10})")
