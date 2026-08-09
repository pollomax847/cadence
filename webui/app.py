#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportOptionalMemberAccess=false, reportUnusedImport=false, reportUnusedFunction=false, reportPrivateUsage=false
"""
app.py — Tableau de bord Flask + HTMX pour la suite de scripts Plex/iTunes/audio.

Fonctionnalités :
- Tuiles : Ratings, Workflows, Playlists, iTunes, Logs
- Lancement de jobs (scripts bash/python) avec capture de stdout/stderr en live
- Visualiseur de logs avec tail -f
- Outils playlists (scan/analyze/match/plex/stats) via playlist_detector
- Navigateur des playlists Plex

Lancement (dev) :
    cd /app && python3 webui/app.py
Lancement (prod, dans le conteneur) :
    gunicorn -b 0.0.0.0:8765 -w 2 webui.app:app

Variables d'env :
    WEBUI_PORT         Port d'écoute (défaut 8765)
    WEBUI_HOST         Interface (défaut 0.0.0.0)
    WEBUI_SECRET       Secret Flask (défaut aléatoire)
    WEBUI_TOKEN        Si défini, exige ?token=XXX sur toutes les routes
    PROJECT_ROOT       Racine du projet (défaut parent de webui/)
    LOGS_DIR           Répertoire des logs (défaut /data/logs ou $PROJECT_ROOT/logs)
    PLEX_URL / PLEX_TOKEN  Transmis aux commandes Plex
"""

from __future__ import annotations

import atexit
import csv
import io
import json
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from flask import (
    Flask, Response, abort, jsonify, redirect, render_template,
    request, stream_with_context, url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from .config import (
        APP_MARK,
        APP_NAME,
        APP_TAGLINE,
        AUTO_SELECTED_PLAYLISTS_FILE,
        HERE,
        HOST_ONLY_JOB_KEYS,
        LOGS_DIR,
        PLEX_DB_CANDIDATES,
        PLEX_LOGS_DIR,
        POSTER_STYLE_GLOB,
        PROJECT_ROOT,
        RUNS_STATE_FILE,
    )
    from .jobs_runner import JobRun, Runner
    from .playlists_routes import register_playlists_routes
except ImportError:
    # Fallback for direct execution: `python3 webui/app.py`
    from config import (
        APP_MARK,
        APP_NAME,
        APP_TAGLINE,
        AUTO_SELECTED_PLAYLISTS_FILE,
        HERE,
        HOST_ONLY_JOB_KEYS,
        LOGS_DIR,
        PLEX_DB_CANDIDATES,
        PLEX_LOGS_DIR,
        POSTER_STYLE_GLOB,
        PROJECT_ROOT,
        RUNS_STATE_FILE,
    )
    from jobs_runner import JobRun, Runner
    from playlists_routes import register_playlists_routes

# ---------------------------------------------------------------------------
# Chemins & config
# ---------------------------------------------------------------------------
sys.path.insert(0, str(PROJECT_ROOT / "playlists"))

# Import optionnel du détecteur de playlists (peut être absent)
try:
    import playlist_detector as pld  # type: ignore[import-not-found]
except Exception as exc:  # pragma: no cover
    pld = None
    _pld_err = repr(exc)
else:
    _pld_err = None

try:
    from auto_playlists_plexamp import PlexAmpAutoPlaylist  # type: ignore[import-not-found]
except Exception as exc:  # pragma: no cover
    PlexAmpAutoPlaylist = None  # type: ignore[assignment]
    _auto_pl_err = repr(exc)
else:
    _auto_pl_err = None
    
from plex_api import (
    default_plex_url as shared_default_plex_url,
    plex_create_audio_playlist as shared_plex_create_audio_playlist,
    plex_delete_playlist as shared_plex_delete_playlist,
    plex_find_playlist_rating_key as shared_plex_find_playlist_rating_key,
    plex_get_playlist_track_ids as shared_plex_get_playlist_track_ids,
    plex_list_audio_playlists as shared_plex_list_audio_playlists,
    plex_machine_identifier as shared_plex_machine_identifier,
    plex_request as shared_plex_request,
)


def _default_plex_url() -> str:
    """Retourne l'URL Plex par défaut selon le contexte (Docker vs host)."""    
    return shared_default_plex_url()


# ---------------------------------------------------------------------------
# Catalogue des jobs : mapping nom → commande + description + tuile
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class JobDef:
    key: str
    title: str
    desc: str
    cmd: list[str]
    category: str
    icon: str = "▶"
    accepts_path: bool = False


JOBS: dict[str, JobDef] = {j.key: j for j in [
    # Ratings
    JobDef("ratings_sync", "Sync ratings Plex → ID3",
           "Synchronise les notes Plex vers les tags ID3",
           ["python3", "ratings/plex_rating_sync_complete.py"],
           "ratings", "⭐"),
    JobDef("ratings_sync_id3", "Sync ratings (script legacy)",
           "Version bash historique",
           ["bash", "ratings/plex_ratings_sync.sh"],
           "ratings", "⭐"),
        JobDef("ratings_2star_pipeline", "🎵 Pipeline 2★ (songrec → beet → clear)",
            "songrec-rename -i (ou fallback songrec en Docker) → beet import (classe dans Music) "
            "→ efface le rating 2★ dans Plex.",
            ["python3", "ratings/process_2star_pipeline.py"],
            "ratings", "🎵"),
        JobDef("ratings_2star_pipeline_sim", "🔎 Simuler pipeline 2★",
            "Affiche les tracks 2★ et les commandes qui seraient lancées (aucune modification).",
            ["python3", "ratings/process_2star_pipeline.py", "--dry-run"],
            "ratings", "🔎"),
        JobDef("ratings_2star_songrec_only", "🎤 2★ → songrec-rename -i uniquement",
            "Identification Shazam + tags ID3 sans beet ni effacement du rating "
            "(fallback songrec si songrec-rename absent).",
            ["python3", "ratings/process_2star_pipeline.py", "--no-beet", "--no-clear"],
            "ratings", "🎤"),
        JobDef("ratings_clear_2stars", "🧹 Effacer ratings 2★ (API seule)",
            "Efface uniquement les ratings 2★ dans Plex via API — à utiliser après songrec+beet manuels.",
            ["python3", "ratings/clear_2star_ratings_api.py"],
            "ratings", "🧹"),

    # Ratings — suppression & nettoyage
    JobDef("ratings_plex_sync_py", "Sync ratings Plex (supprime 1★)",
           "Supprime automatiquement les fichiers notés 1★ dans Plex/PlexAmp (tracks + albums + artistes). "
           "Tourne sur une copie de la base (le conteneur ne peut pas arrêter Plex) — "
           "lancer ensuite « Force scan + corbeille Plex » pour répercuter les suppressions.",
           ["python3", "ratings/plex_ratings_sync.py", "--delete-albums", "--delete-artists",
            "--skip-plex-stop", "--delete",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "ratings", "🗑"),
    JobDef("ratings_sync_id3_py", "Sync ratings → tags ID3",
           "Écrit les évaluations Plex directement dans les tags ID3 des fichiers",
           ["python3", "ratings/sync_ratings_to_id3.py"],
           "ratings", "🏷"),

    # Maintenance
    JobDef("plex_force_cleanup", "Force scan + corbeille Plex",
           "Force un scan et un vidage de corbeille sur toutes les bibliothèques audio",
           ["python3", "utils/plex_force_cleanup.py"],
           "maintenance", "🧹"),

    # Workflows
    JobDef("daily", "Sync ratings quotidienne",
           "Synchronisation quotidienne des ratings Plex (légère, sans SongRec)",
           ["bash", "ratings/plex_daily_ratings_sync.sh"],
           "workflows", "📅"),

    # Playlists
    JobDef("playlists_auto", "Générer playlists auto (Plexamp)",
           "Auto playlists basées sur ratings & écoutes (respecte la sélection enregistrée)",
           ["python3", "playlists/auto_playlists_plexamp.py",
            "--selected-names-file", "/app/data/auto_selected_playlists.json"],
           "playlists", "🎶"),
    JobDef("playlists_gen", "Shell playlists Plexamp",
           "Script bash d'orchestration Plexamp",
           ["bash", "playlists/generate_plexamp_playlists.sh"],
           "playlists", "🎶"),
    JobDef("playlists_sync_mybook", "Sync playlists → MyBook",
           "Copie les M3U vers /mnt/MyBook",
           ["bash", "playlists/sync_plex_playlists_to_mybook.sh"],
           "playlists", "💾"),
    JobDef("playlists_sync_m3u", "Sync M3U disque ↔ Plex",
           "Synchronise les M3U vers Plex",
           ["python3", "playlists/sync_m3u_playlists.py"],
           "playlists", "🔁"),
    JobDef("playlists_dedup", "Dédupliquer playlists",
           "Détecte et supprime les doublons (titre+artiste) dans les playlists générées",
           ["python3", "playlists/deduplicate_playlists.py"],
           "playlists", "🔍"),
    JobDef("playlists_diagnose", "Diagnostiquer playlists",
           "Analyse les playlists générées et exporte un rapport CSV des anomalies",
           ["python3", "playlists/diagnose_all_playlists.py"],
           "playlists", "🩺"),

    # Soulseek — téléchargements manquants
    JobDef("slskd_dl_all", "⬇️ Soulseek — Tout télécharger (manquants)",
           "Toutes les playlists Last.fm : cherche et télécharge via Soulseek les tracks absentes",
           ["python3", "playlists/slskd_download_missing.py", "--all", "--max-dl", "50",
            "--slskd-url", "http://host.docker.internal:5032",
            "--slskd-key", "***REMOVED***",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "⬇️"),
    JobDef("slskd_dl_france80s", "⬇️ Soulseek — Top France 80s",
           "Manquants du top Last.fm France 80s",
           ["python3", "playlists/slskd_download_missing.py", "--decade", "1980", "--max-dl", "50",
            "--slskd-url", "http://host.docker.internal:5032",
            "--slskd-key", "***REMOVED***",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "⬇️"),
    JobDef("slskd_dl_france90s", "⬇️ Soulseek — Top France 90s",
           "Manquants du top Last.fm France 90s",
           ["python3", "playlists/slskd_download_missing.py", "--decade", "1990", "--max-dl", "50",
            "--slskd-url", "http://host.docker.internal:5032",
            "--slskd-key", "***REMOVED***",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "⬇️"),
    JobDef("slskd_dl_france2000s", "⬇️ Soulseek — Top France 2000s",
           "Manquants du top Last.fm France 2000s",
           ["python3", "playlists/slskd_download_missing.py", "--decade", "2000", "--max-dl", "50",
            "--slskd-url", "http://host.docker.internal:5032",
            "--slskd-key", "***REMOVED***",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "⬇️"),
    JobDef("slskd_dl_monde70s", "⬇️ Soulseek — Top Monde 70s",
           "Manquants du top Last.fm mondial 70s",
           ["python3", "playlists/slskd_download_missing.py", "--global-decade", "1970", "--max-dl", "50",
            "--slskd-url", "http://host.docker.internal:5032",
            "--slskd-key", "***REMOVED***",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "⬇️"),
    JobDef("slskd_dl_monde80s", "⬇️ Soulseek — Top Monde 80s",
           "Manquants du top Last.fm mondial 80s",
           ["python3", "playlists/slskd_download_missing.py", "--global-decade", "1980", "--max-dl", "50",
            "--slskd-url", "http://host.docker.internal:5032",
            "--slskd-key", "***REMOVED***",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "⬇️"),
    JobDef("slskd_dl_monde90s", "⬇️ Soulseek — Top Monde 90s",
           "Manquants du top Last.fm mondial 90s",
           ["python3", "playlists/slskd_download_missing.py", "--global-decade", "1990", "--max-dl", "50",
            "--slskd-url", "http://host.docker.internal:5032",
            "--slskd-key", "***REMOVED***",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "⬇️"),
    JobDef("slskd_dl_monde2000s", "⬇️ Soulseek — Top Monde 2000s",
           "Manquants du top Last.fm mondial 2000s",
           ["python3", "playlists/slskd_download_missing.py", "--global-decade", "2000", "--max-dl", "50",
            "--slskd-url", "http://host.docker.internal:5032",
            "--slskd-key", "***REMOVED***",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "⬇️"),
    JobDef("slskd_dl_monde2010s", "⬇️ Soulseek — Top Monde 2010s",
           "Manquants du top Last.fm mondial 2010s",
           ["python3", "playlists/slskd_download_missing.py", "--global-decade", "2010", "--max-dl", "50",
            "--slskd-url", "http://host.docker.internal:5032",
            "--slskd-key", "***REMOVED***",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "⬇️"),
    JobDef("slskd_dl_genres", "⬇️ Soulseek — Genres (Jazz, Rock, Hip-Hop…)",
           "Manquants de toutes les playlists genre Last.fm",
           ["python3", "playlists/slskd_download_missing.py", "--tags", "jazz,blues,rock,hip-hop,pop,reggae,soul,funk,electronic,country,folk,latin", "--max-dl", "30",
            "--slskd-url", "http://host.docker.internal:5032",
            "--slskd-key", "***REMOVED***",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "⬇️"),

    # Last.fm playlists
    JobDef("lastfm_france_genres", "🇫🇷 Last.fm — Top FR par genres",
           "Top France réel (geo.getTopTracks) groupé par genre Plex → Top FR Pop, Hip-Hop, Jazz…",
           ["python3", "playlists/generate_lastfm_playlists.py",
            "--mode", "france-genre", "--limit", "200", "--push",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "🇫🇷"),
    JobDef("lastfm_genres", "🎸 Last.fm — Top par genres",
           "Génère Top Jazz, Metal, Hip-Hop, Chanson Française, Reggae… depuis Last.fm",
           ["python3", "playlists/generate_lastfm_playlists.py",
            "--mode", "genre", "--all-genres", "--limit", "200", "--push",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "🎸"),
    JobDef("lastfm_decades_world", "🌍 Last.fm — Top Monde décennies",
           "Génère Top Monde 70s, 80s, 90s, 2000s depuis Last.fm (sans filtre pays)",
           ["python3", "playlists/generate_lastfm_playlists.py",
            "--mode", "decade", "--decades", "1970,1980,1990,2000", "--limit", "200", "--push",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "🌍"),
    JobDef("lastfm_chart", "📈 Last.fm — Top Monde (temps réel)",
           "Top Charts mondial en temps réel depuis Last.fm chart.getTopTracks",
           ["python3", "playlists/generate_lastfm_playlists.py",
            "--mode", "chart", "--limit", "100", "--push",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "📈"),
    JobDef("lastfm_user_top", "👤 Last.fm — Mon Top (12 mois)",
           "Top tracks personnels Last.fm sur 12 mois (nécessite LASTFM_USER en env)",
           ["python3", "playlists/generate_lastfm_playlists.py",
            "--mode", "user-top", "--period", "12month", "--limit", "200", "--push",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "👤"),
    JobDef("lastfm_user_loved", "❤️ Last.fm — Mes Loved",
           "Pistes aimées (loved) sur Last.fm (nécessite LASTFM_USER en env)",
           ["python3", "playlists/generate_lastfm_playlists.py",
            "--mode", "user-loved", "--limit", "500", "--push",
            "--plex-db", "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"],
           "playlists", "❤️"),

    # Tagging
    JobDef("beet_import", "beet import",
           "Importe et tague un dossier dans beets via MusicBrainz",
           ["beet", "--config", "/beets-config/config-docker.yaml", "import", "-A"],
           "tagging", "🎵", accepts_path=True),
    JobDef("beet_import_dry", "beet import (simulation)",
           "Aperçu de l'import sans modifications (--timid)",
           ["beet", "--config", "/beets-config/config-docker.yaml", "import", "--timid", "-A"],
           "tagging", "🔎", accepts_path=True),
    JobDef("beet_bad", "beet bad (fichiers corrompus)",
           "Détecte les fichiers audio corrompus dans la bibliothèque",
           ["beet", "--config", "/beets-config/config-docker.yaml", "bad"],
           "tagging", "🩺"),
    JobDef("auto_tag", "Auto-tag (kid3 + AcoustID)",
           "Identification + tagging via empreinte acoustique",
           ["bash", "/audio-scripts/auto_tag_music_kid3only.sh"],
           "tagging", "🏷", accepts_path=True),
    JobDef("auto_tag_dry", "Auto-tag simulation",
           "Aperçu du tagging sans modifications",
           ["bash", "/audio-scripts/auto_tag_music_kid3only.sh", "--dry-run"],
           "tagging", "🔎", accepts_path=True),
    JobDef("auto_tag_dedup", "Doublons audio (fingerprint)",
           "Détecte les doublons par empreinte acoustique (--remove-duplicates)",
           ["bash", "/audio-scripts/auto_tag_music_kid3only.sh", "--remove-duplicates"],
           "tagging", "🔍", accepts_path=True),
    # Tagging — détection & pipeline
    JobDef("scan_problematic", "🔍 Scanner fichiers à tagger",
           "Détecte les fichiers avec tags manquants/génériques/sans MusicBrainz ID",
           ["python3", "-u", "/audio-scripts/detect_problematic_audio.py"],
           "tagging", "🔍", accepts_path=True),
    JobDef("tag_pipeline", "🚀 Pipeline Shazam → beets",
           "songrec-rename (identification Shazam) → beet import (tags MusicBrainz complets)",
           ["python3", "-u", "/audio-scripts/tag_pipeline.py"],
           "tagging", "🚀", accepts_path=True),
    JobDef("tag_pipeline_dry", "🔎 Pipeline simulation",
           "Aperçu sans modifications: quels fichiers seraient renommés et tagués",
           ["python3", "-u", "/audio-scripts/tag_pipeline.py", "--dry-run"],
           "tagging", "🔎", accepts_path=True),

    # Sync tags Plex → fichiers audio
    JobDef("write_tags_dry", "🔍 Sync tags Plex → fichiers (simulation)",
           "Aperçu des tags que Plex pousserait vers tes fichiers audio (titre, artiste, album, année, genre si vide)",
           ["python3", "playlists/write_tags.py", "--all", "--limit", "200"],
           "tagging", "🔍"),
    JobDef("write_tags_apply", "✏️ Sync tags Plex → fichiers (tranche 5 000)",
           "Écrit max 5 000 corrections par run — à relancer chaque nuit jusqu'à épuisement",
           ["python3", "playlists/write_tags.py", "--all", "--max-writes", "5000", "--apply"],
           "tagging", "✏️"),

    # Organisation structure Lidarr
    JobDef("sort_lidarr_dry", "🗂️ Organiser Lidarr (simulation)",
           "Prévisualise la structure Artiste/Album (Année)/01 - Artiste - Album - Titre sans déplacer",
           ["bash", "/music-sort/music_sort_lidarr.sh", "--source", "/music", "--dry-run", "--tui"],
           "tagging", "🗂️"),
    JobDef("sort_lidarr_apply", "✅ Organiser Lidarr (réel)",
           "Classe les fichiers audio en structure Artiste/Album (Année)/01 - Artiste - Album - Titre",
           ["bash", "/music-sort/music_sort_lidarr.sh", "--source", "/music", "--tui"],
           "tagging", "🗂️"),

    # Tagging — disques réels (bypass mergerfs)
    JobDef("tag_pipeline_mybook", "🚀 Tagging disque réel — MyBook/Music",
           "Pipeline Shazam→beets directement sur /mnt/MyBook/Music (bypass mergerfs)",
           ["python3", "-u", "/audio-scripts/tag_pipeline.py", "/real-mybook"],
           "tagging", "💽"),
    JobDef("tag_pipeline_ssd", "🚀 Tagging disque réel — SSD/Musiques",
           "Pipeline Shazam→beets directement sur /mnt/ssd/Musiques (bypass mergerfs)",
           ["python3", "-u", "/audio-scripts/tag_pipeline.py", "/real-ssd"],
           "tagging", "⚡"),
    JobDef("tag_pipeline_itunes", "🚀 Tagging disque réel — iTunes/Music",
           "Pipeline Shazam→beets directement sur /mnt/MyBook/itunes/Music (bypass mergerfs)",
           ["python3", "-u", "/audio-scripts/tag_pipeline.py", "/itunes/Music"],
           "tagging", "🍎"),
    JobDef("tag_pipeline_home", "🚀 Tagging disque réel — ~/Musique",
           "Pipeline Shazam→beets directement sur /home/paulceline/Musique (bypass mergerfs)",
           ["python3", "-u", "/audio-scripts/tag_pipeline.py", "/real-home"],
           "tagging", "🏠"),
    JobDef("beet_update_mybook", "🔄 beet update — MyBook/Music",
           "Met à jour les tags MusicBrainz des fichiers déjà connus dans beets (via MBID)",
           ["beet", "--config", "/beets-config/config-docker.yaml",
            "update", "--yes", "/real-mybook"],
           "tagging", "🔄"),
    JobDef("beet_update_ssd", "🔄 beet update — SSD/Musiques",
           "Met à jour les tags MusicBrainz des fichiers déjà connus dans beets (via MBID)",
           ["beet", "--config", "/beets-config/config-docker.yaml",
            "update", "--yes", "/real-ssd"],
           "tagging", "🔄"),
]}


# ---------------------------------------------------------------------------
# Runner : exécute un job en arrière-plan et stream son output
# ---------------------------------------------------------------------------
runner = Runner(
    logs_dir=LOGS_DIR,
    project_root=PROJECT_ROOT,
    jobs_map=JOBS,
    state_file=RUNS_STATE_FILE,
)


def _cleanup():
    runner.cleanup_running()


atexit.register(_cleanup)


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder=str(HERE / "templates"),
            static_folder=str(HERE / "static"))
app.secret_key = os.environ.get("WEBUI_SECRET") or secrets.token_hex(16)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # type: ignore[assignment]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("WEBUI_COOKIE_SECURE", "1") != "0",
)


@app.template_filter("fmt_ts")
def fmt_ts(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(value)


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "…"
    return f"{value[:4]}…{value[-4:]}"


def _path_snapshot(path_str: str) -> dict[str, Any]:
    path = Path(path_str).expanduser()
    exists = path.exists()
    info: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "kind": "missing",
        "readable": False,
        "writable": False,
        "size": None,
        "entries": None,
    }
    if not exists:
        return info
    info["kind"] = "dir" if path.is_dir() else "file"
    info["readable"] = os.access(path, os.R_OK)
    info["writable"] = os.access(path, os.W_OK)
    try:
        stat = path.stat()
        info["size"] = stat.st_size
    except OSError:
        pass
    if path.is_dir():
        try:
            info["entries"] = len(list(path.iterdir()))
        except OSError:
            info["entries"] = None
    return info


def _tool_snapshot(command: str) -> dict[str, Any]:
    resolved = shutil.which(command)
    return {
        "command": command,
        "available": bool(resolved),
        "path": resolved or "",
    }


def _scan_plex_preferences() -> dict[str, Any]:
    # Si le token est déjà injecté via variable d'environnement, on l'utilise directement
    env_token = os.environ.get("PLEX_TOKEN", "").strip()
    if env_token:
        return {
            "found": True,
            "token": env_token,
            "url": os.environ.get("PLEX_URL", _default_plex_url()),
            "source": "variable d'environnement PLEX_TOKEN",
            "tried": [],
        }

    candidates = [
        "/plex/Preferences.xml",
        "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Preferences.xml",
        "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml",
        "/config/Library/Application Support/Plex Media Server/Preferences.xml",
        str(Path("~/.var/app/tv.plex.PlexMediaServer/config/Library/Application Support/Plex Media Server/Preferences.xml").expanduser()),
        str(Path("~/Library/Application Support/Plex Media Server/Preferences.xml").expanduser()),
    ]
    token_re = re.compile(r'PlexOnlineToken="([^"]+)"')
    url_re = re.compile(r'customConnections="([^"]+)"')
    tried: list[dict[str, Any]] = []
    for path in candidates:
        p = Path(path)
        info: dict[str, Any] = {"path": path, "exists": p.exists()}
        try:
            if p.is_file():
                txt = p.read_text(encoding="utf-8", errors="replace")
                m = token_re.search(txt)
                if m:
                    mu = url_re.search(txt)
                    url = (mu.group(1).split(",")[0] if mu else
                           os.environ.get("PLEX_URL", _default_plex_url()))
                    return {
                        "found": True,
                        "token": m.group(1),
                        "url": url,
                        "source": path,
                        "tried": tried,
                    }
                info["note"] = "fichier lu, mais pas de PlexOnlineToken"
        except PermissionError:
            info["note"] = "permission refusée (fichier appartient à root)"
        except Exception as exc:
            info["note"] = f"erreur: {exc}"
        tried.append(info)
    compose_refresh_cmd = (
        "cd "
        + shlex.quote(str(PROJECT_ROOT))
        + " && sudo python3 utils/detect_plex_token.py --write-env .env"
        + " && docker compose up -d cadence-webui"
    )
    return {
        "found": False,
        "tried": tried,
        "fix_steps": [
            "Si Plex est sur l'hôte: exécute la détection avec sudo puis recharge le service webui.",
            compose_refresh_cmd,
            "Alternative: monte le dossier 'Plex Media Server' en /plex:ro (ou ajuste PLEX_CONFIG_HOST).",
        ],
        "hint": "Monte Preferences.xml dans le conteneur, ex: "
                "-v '/var/snap/plexmediaserver/common/.../Plex Media Server':/plex:ro "
                "(ou utilise sudo utils/detect_plex_token.py --write-env .env sur l'hôte)",
    }


def _find_plex_db() -> Optional[str]:
    for candidate in PLEX_DB_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _summarize_run(run: JobRun) -> dict[str, Any]:
    lines = list(run.lines)
    lowered_error = ("error", "erreur", "failed", "traceback", "permission denied", "❌")
    lowered_success = ("success", "succès", "terminé", "completed", "done", "✓", "✅")
    error_lines = [line for line in lines if any(marker in line.lower() for marker in lowered_error if marker.isascii()) or any(marker in line for marker in ("❌",))]
    success_lines = [line for line in lines if any(marker in line.lower() for marker in lowered_success if marker.isascii()) or any(marker in line for marker in ("✓", "✅"))]
    return {
        "line_count": len(lines),
        "last_line": lines[-1] if lines else None,
        "error_lines": error_lines[-3:],
        "success_lines": success_lines[-3:],
    }


@app.before_request
def _auth_gate():
    tok = os.environ.get("WEBUI_TOKEN")
    if not tok:
        return None
    if request.path.startswith("/static"):
        return None
    query_token = request.args.get("token", "")
    header_token = request.headers.get("X-Token", "")
    auth_header = request.headers.get("Authorization", "")
    bearer_token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
    if any(secrets.compare_digest(candidate, tok) for candidate in (query_token, header_token, bearer_token) if candidate):
        return None
    abort(401)


@app.before_request
def _force_https():
    if os.environ.get("WEBUI_ENFORCE_HTTPS", "0") != "1":
        return None
    if request.path.startswith("/static"):
        return None
    if request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https":
        return None
    https_url = request.url.replace("http://", "https://", 1)
    return redirect(https_url, code=301)


@app.after_request
def _security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    response.headers.setdefault("Cache-Control", "no-store")
    if request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.context_processor
def _ctx() -> dict[str, Any]:
    categories = sorted({j.category for j in JOBS.values()})
    return {
        "app_name": APP_NAME,
        "app_mark": APP_MARK,
        "app_tagline": APP_TAGLINE,
        "categories": categories,
        "jobs_by_cat": {c: [j for j in JOBS.values() if j.category == c] for c in categories},
        "jobs_map": JOBS,
        "pld_available": pld is not None,
        "pld_err": _pld_err,
        "project_root": str(PROJECT_ROOT),
        "docker_mode": Path("/.dockerenv").exists(),
    }


register_playlists_routes(
    app,
    {
        "runner": runner,
        "project_root": PROJECT_ROOT,
        "poster_style_glob": POSTER_STYLE_GLOB,
        "default_plex_url": _default_plex_url,
        "tool_snapshot": _tool_snapshot,
        "summarize_run": _summarize_run,
        "pld": pld,
        "pld_err": _pld_err,
    },
)


# -------------------- Pages --------------------
@app.route("/")
def index():
    # Derniers runs, agrégats
    runs = runner.list_runs()[:10]
    stats = {
        "running": sum(1 for r in runner.runs.values() if not r.done.is_set()),
        "success": sum(1 for r in runner.runs.values() if r.status == "success"),
        "failed": sum(1 for r in runner.runs.values() if r.status == "failed"),
        "total_jobs": len(JOBS),
    }
    return render_template("index.html", runs=runs, stats=stats)


@app.route("/jobs")
def jobs_page():
    return render_template("jobs.html", runs=runner.list_runs())


@app.route("/job/<run_id>")
def job_page(run_id: str):
    run = runner.get(run_id)
    if not run:
        return redirect(url_for("jobs_page", missing_run=run_id))
    return render_template("job.html", run=run, job=JOBS.get(run.job_key), summary=_summarize_run(run))


@app.route("/config")
def config_page():
    plex_token_set = bool(os.environ.get("PLEX_TOKEN", "").strip())
    slskd_key_set = bool(os.environ.get("SLSKD_API_KEY", "").strip())
    config_rows = [
        {"label": "Mode d'exécution", "value": "Docker" if Path("/.dockerenv").exists() else "Host", "secret": False},
        {"label": "PLEX_URL", "value": os.environ.get("PLEX_URL", ""), "secret": False},
        {"label": "PLEX_TOKEN", "value": "configuré" if plex_token_set else "", "secret": True},
        {"label": "SLSKD_URL", "value": os.environ.get("SLSKD_URL", ""), "secret": False},
        {"label": "SLSKD_API_KEY", "value": "configuré" if slskd_key_set else "", "secret": True},
        {"label": "AUDIO_LIBRARY", "value": os.environ.get("AUDIO_LIBRARY", "/music"), "secret": False},
        {"label": "PLAYLISTS_DIR", "value": os.environ.get("PLAYLISTS_DIR", "/playlists"), "secret": False},
        {"label": "LOGS_DIR", "value": str(LOGS_DIR), "secret": False},
        {"label": "PROJECT_ROOT", "value": str(PROJECT_ROOT), "secret": False},
    ]
    mounts = [
        _path_snapshot("/plex"),
        _path_snapshot("/plex/Preferences.xml"),
        _path_snapshot(_find_plex_db() or PLEX_DB_CANDIDATES[0]),
        _path_snapshot(os.environ.get("AUDIO_LIBRARY", "/music")),
        _path_snapshot(os.environ.get("PLAYLISTS_DIR", "/playlists")),
        _path_snapshot(str(LOGS_DIR)),
    ]
    tools = [
        _tool_snapshot("python3"),
        _tool_snapshot("beet"),
        _tool_snapshot("songrec"),
        _tool_snapshot("songrec-rename"),
    ]
    return render_template(
        "config.html",
        config_rows=config_rows,
        mounts=mounts,
        tools=tools,
        plex_detect=_scan_plex_preferences(),
    )


@app.route("/tagging")
def tagging_page():
    tagging_jobs = [j for j in JOBS.values() if j.category == "tagging"]
    tools = [
        _tool_snapshot("beet"),
        _tool_snapshot("fpcalc"),
        _tool_snapshot("kid3-cli"),
    ]
    music_dir = os.environ.get("AUDIO_LIBRARY", "/music")
    return render_template("tagging.html", tagging_jobs=tagging_jobs, tools=tools, music_dir=music_dir)


@app.route("/logs")
def logs_page():
    log_files = sorted(LOGS_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Logs systemd depuis ~/.plex/logs/plex_daily (montés en /plex-logs/plex_daily)
    plex_daily_dir = PLEX_LOGS_DIR / "plex_daily"
    if plex_daily_dir.is_dir():
        plex_logs = sorted(plex_daily_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        plex_logs = []
    return render_template(
        "logs.html",
        logs=log_files[:200],
        plex_logs=plex_logs[:200],
        plex_logs_dir=plex_daily_dir,
        logs_dir=LOGS_DIR,
        docker_control_enabled=_docker_control_enabled(),
    )


# -------------------- Documentation guides ----------------
@app.route("/docs/lastfm")
def guide_lastfm():
    """Serve Last.fm setup guide as HTML."""
    guide_path = PROJECT_ROOT / "LASTFM_SETUP.md"
    if not guide_path.exists():
        return '<p>Guide Last.fm not found</p>', 404
    
    # Read markdown and convert to basic HTML
    content = guide_path.read_text(encoding='utf-8')
    
    # Very basic markdown → HTML conversion
    lines = content.split('\n')
    html_lines = []
    in_code_block = False
    in_list = False
    in_table = False
    
    for line in lines:
        # Code blocks
        if line.startswith('```'):
            if in_code_block:
                html_lines.append('</pre>')
                in_code_block = False
            else:
                html_lines.append('<pre class="code-block">')
                in_code_block = True
            continue
        
        if in_code_block:
            html_lines.append(line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
            continue
        
        # Links
        line = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank">\1</a>', line)
        
        # Code/backticks
        line = re.sub(r'`([^`]+)`', r'<code>\1</code>', line)
        
        # Bold
        line = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', line)
        line = re.sub(r'__([^_]+)__', r'<strong>\1</strong>', line)
        
        # Italic
        line = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', line)
        line = re.sub(r'_([^_]+)_', r'<em>\1</em>', line)
        
        # Headings
        if line.startswith('### '):
            html_lines.append(f'<h3>{line[4:]}</h3>')
        elif line.startswith('## '):
            html_lines.append(f'<h2>{line[3:]}</h2>')
        elif line.startswith('# '):
            html_lines.append(f'<h1>{line[2:]}</h1>')
        # Lists
        elif line.startswith('- '):
            if not in_list:
                html_lines.append('<ul>')
                in_list = True
            html_lines.append(f'<li>{line[2:]}</li>')
        elif line.startswith('  - '):
            html_lines.append(f'<li style="margin-left:2rem">{line[4:]}</li>')
        elif line.strip().startswith('|'):
            if not in_table:
                html_lines.append('<table class="guide-table">')
                in_table = True
            # Minimal table row parsing
            cells = [c.strip() for c in line.split('|')[1:-1]]
            html_lines.append('<tr>')
            for cell in cells:
                html_lines.append(f'<td>{cell}</td>')
            html_lines.append('</tr>')
        else:
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            if in_table and not line.strip().startswith('|'):
                html_lines.append('</table>')
                in_table = False
            
            if line.strip():
                html_lines.append(f'<p>{line}</p>')
            else:
                html_lines.append('')
    
    if in_list:
        html_lines.append('</ul>')
    if in_table:
        html_lines.append('</table>')
    
    html_content = '\n'.join(html_lines)
    
    # Wrap in a styled container
    full_html = f"""
    <section class="guide-page">
        <style>
            .guide-page {{ max-width: 900px; margin: 2rem auto; padding: 0 1rem; font-size: 1rem; line-height: 1.6; }}
            .guide-page h1, .guide-page h2, .guide-page h3 {{ margin-top: 2rem; margin-bottom: 1rem; }}
            .guide-page h1 {{ font-size: 2.5em; border-bottom: 3px solid var(--primary); padding-bottom: 0.5rem; }}
            .guide-page h2 {{ font-size: 1.8em; color: var(--primary); }}
            .guide-page h3 {{ font-size: 1.3em; }}
            .guide-page ul {{ margin: 1rem 0; padding-left: 2rem; }}
            .guide-page li {{ margin: 0.5rem 0; }}
            .guide-page code {{ background: var(--bg-muted); padding: 0.2em 0.4em; border-radius: 3px; font-family: monospace; }}
            .guide-page pre {{ background: #1e1e1e; color: #d4d4d4; padding: 1rem; border-radius: 5px; overflow-x: auto; }}
            .guide-page a {{ color: var(--link-color); text-decoration: none; }}
            .guide-page a:hover {{ text-decoration: underline; }}
            .guide-page table {{ border-collapse: collapse; margin: 1rem 0; width: 100%; }}
            .guide-page table td, .guide-page table th {{ border: 1px solid var(--border-color); padding: 0.5rem; text-align: left; }}
            .guide-page table th {{ background: var(--bg-secondary); font-weight: bold; }}
        </style>
        {html_content}
    </section>
    """
    
    return full_html, 200, {'Content-Type': 'text/html; charset=utf-8'}


# -------------------- API : détection automatique du token Plex --------------
@app.get("/api/plex/detect")
def api_plex_detect():
    """Tente de lire PlexOnlineToken depuis Preferences.xml (plusieurs chemins)."""
    detected = _scan_plex_preferences()
    if detected.get("found"):
        # Ne jamais renvoyer le token brut au navigateur.
        detected.pop("token", None)
    return jsonify(detected), 200


# -------------------- API : jobs --------------------
@app.post("/api/run/<job_key>")
def api_run(job_key: str):
    if job_key not in JOBS:
        return jsonify(error="unknown job"), 404
    if job_key in HOST_ONLY_JOB_KEYS and Path("/.dockerenv").exists():
        job = JOBS[job_key]
        return jsonify(
            error=(
                f"Le job '{job.title}' est desormais reserve au host/systemd. "
                "Utilise systemd ou un shell sur l'hote au lieu du webui Docker."
            )
        ), 409
    extra = request.json.get("args", []) if request.is_json else []
    run = runner.launch(job_key, extra)
    return jsonify(run.to_public())


@app.post("/api/kill/<run_id>")
def api_kill(run_id: str):
    ok = runner.kill(run_id)
    return jsonify(killed=ok)


@app.get("/api/runs")
def api_runs():
    return jsonify([r.to_public() for r in runner.list_runs()])


@app.get("/api/run/<run_id>")
def api_run_status(run_id: str):
    run = runner.get(run_id)
    if not run:
        return jsonify(error="unknown"), 404
    return jsonify(run.to_public())


@app.get("/api/run/<run_id>/output")
def api_run_output(run_id: str):
    run = runner.get(run_id)
    if not run:
        return jsonify(error="unknown"), 404
    offset = int(request.args.get("offset", 0))
    lines = list(run.lines)[offset:]
    return jsonify({
        "lines": lines,
        "next_offset": offset + len(lines),
        "status": run.status,
        "exit_code": run.exit_code,
        "duration": round((run.ended_at or time.time()) - run.started_at, 1),
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "line_count": len(run.lines),
        "summary": _summarize_run(run),
    })


# -------------------- HTMX partials --------------------
@app.get("/htmx/run/<run_id>/tail")
def htmx_run_tail(run_id: str):
    run = runner.get(run_id)
    if not run:
        return "<div class='err'>Run introuvable</div>", 404
    return render_template("_tail.html", run=run)


@app.get("/htmx/runs")
def htmx_runs():
    return render_template("_runs_table.html", runs=runner.list_runs())


# -------------------- Playlists (detector) --------------------
def _require_pld():
    if pld is None:
        abort(503, description=f"playlist_detector indisponible: {_pld_err}")


def _entry_title_key(entry: Any) -> str:
    artist = (entry.artist or "").strip().casefold()
    title = (entry.title or "").strip().casefold()
    if artist or title:
        return f"{artist}::{title}"
    return ""


def _entry_path_key(entry: Any, playlist_path: Path) -> str:
    if pld is None:
        return str(entry.path_or_uri or "").strip().casefold()
    resolved = getattr(pld, "resolve_entry_path")(entry.path_or_uri, playlist_path)
    if resolved:
        try:
            return str(resolved.resolve()).casefold()
        except Exception:
            return str(resolved).casefold()
    raw = str(entry.path_or_uri or "").strip()
    return raw.casefold()


def _entry_signature(entry: Any, playlist_path: Path) -> tuple[str, str]:
    return (
        _entry_path_key(entry, playlist_path),
        _entry_title_key(entry),
    )


def _plex_delete_playlist(url: str, token: str, rating_key: str) -> None:
    shared_plex_delete_playlist(url, token, rating_key)


def _plex_request(method: str, url: str, token: str) -> bytes:
    return shared_plex_request(method, url, token)


def _plex_machine_identifier(url: str, token: str) -> str:
    return shared_plex_machine_identifier(url, token)


def _plex_find_playlist_rating_key(url: str, token: str, title: str) -> str | None:
    return shared_plex_find_playlist_rating_key(url, token, title)


def _plex_list_audio_playlists(url: str, token: str) -> list[dict[str, str]]:
    return shared_plex_list_audio_playlists(url, token)


def _playlist_name_aliases(name: str) -> set[str]:
    """Construit des alias de comparaison pour matcher un nom saisi par l'utilisateur.

    Exemple pris en charge:
    - "Auto_Holiday_209_titres"
    - "[Auto] 🎵 Holiday (209 titres)"
    """
    raw = (name or "").strip()
    if not raw:
        return set()

    variants = {
        raw,
        Path(raw).stem,
        raw.replace("_", " "),
        Path(raw).stem.replace("_", " "),
    }

    aliases: set[str] = set()
    for v in variants:
        s = " ".join(v.split())
        if not s:
            continue
        aliases.add(s.casefold())

        no_auto = re.sub(r"^\[?auto\]?\s*", "", s, flags=re.IGNORECASE)
        no_count = re.sub(r"\(\s*\d+\s*titres?\s*\)\s*$", "", no_auto, flags=re.IGNORECASE)
        simplified = re.sub(r"[^\w\s]", " ", no_count, flags=re.UNICODE)
        simplified = " ".join(simplified.split())
        if simplified:
            aliases.add(simplified.casefold())

    return aliases


def _plex_delete_matching_audio_playlists(url: str, token: str, candidate_names: list[str]) -> list[str]:
    """Supprime les playlists audio Plex dont le titre match un nom candidat (tolérant aux variantes)."""
    candidates = [str(x or "").strip() for x in candidate_names if str(x or "").strip()]
    if not candidates:
        return []

    candidate_aliases = [_playlist_name_aliases(name) for name in candidates]
    existing = _plex_list_audio_playlists(url, token)
    deleted_titles: list[str] = []

    for row in existing:
        title = row.get("title", "")
        rating_key = row.get("rating_key", "")
        if not title or not rating_key:
            continue

        title_aliases = _playlist_name_aliases(title)
        if not title_aliases:
            continue

        if any(title_aliases.intersection(c_aliases) for c_aliases in candidate_aliases):
            _plex_delete_playlist(url, token, rating_key)
            deleted_titles.append(title)

    return deleted_titles


def _plex_create_audio_playlist(url: str, token: str, title: str, track_ids: list[int], replace: bool) -> None:
    if not track_ids:
        raise RuntimeError("Aucune piste matchée pour créer la playlist")
    shared_plex_create_audio_playlist(url, token, title, track_ids, replace=replace)


def _plex_get_playlist_track_ids(url: str, token: str, rating_key: str) -> list[int]:
    return shared_plex_get_playlist_track_ids(url, token, rating_key)


def _normalize_row_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {
        re.sub(r"[^a-z0-9]+", "", str(k).strip().casefold()): v
        for k, v in row.items()
    }


def _extract_track_from_row(row: dict[str, Any]) -> dict[str, str] | None:
    normalized = _normalize_row_keys(row)

    title = str(
        normalized.get("title")
        or normalized.get("track")
        or normalized.get("trackname")
        or normalized.get("song")
        or normalized.get("name")
        or ""
    ).strip()
    artist = str(
        normalized.get("artist")
        or normalized.get("artists")
        or normalized.get("mainartist")
        or normalized.get("creator")
        or ""
    ).strip()
    album = str(
        normalized.get("album")
        or normalized.get("release")
        or ""
    ).strip()

    if not title:
        return None
    return {"title": title, "artist": artist, "album": album}


def _parse_soundiiz_content(filename: str, content: str) -> tuple[str, list[dict[str, str]]]:
    suffix = Path(filename or "playlist.csv").suffix.lower()
    default_name = Path(filename or "playlist").stem or "playlist"

    if suffix == ".json":
        raw = json.loads(content)
        items: list[Any]
        if isinstance(raw, dict):
            items = raw.get("tracks") or raw.get("items") or raw.get("songs") or []
            playlist_name = str(raw.get("name") or raw.get("playlist") or default_name)
        elif isinstance(raw, list):
            items = raw
            playlist_name = default_name
        else:
            raise ValueError("Format JSON non supporté")

        tracks: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            track = _extract_track_from_row(item)
            if track:
                tracks.append(track)
        return playlist_name, tracks

    # CSV par défaut
    lines = content.splitlines()
    if lines and lines[0].strip().lower().startswith("sep="):
        lines = lines[1:]
    cleaned = "\n".join(lines)

    try:
        dialect = csv.Sniffer().sniff(cleaned[:2048], delimiters=",;")
    except Exception:
        class _Fallback(csv.excel):
            delimiter = ";" if cleaned.count(";") > cleaned.count(",") else ","
        dialect = _Fallback

    reader = csv.DictReader(io.StringIO(cleaned), dialect=dialect)
    tracks = []
    for row in reader:
        if not row:
            continue
        track = _extract_track_from_row(row)
        if track:
            tracks.append(track)
    return default_name, tracks


def _lookup_track_ids_in_plex_db(
    plex_db: str, tracks: list[dict[str, str]]
) -> tuple[list[int], int, list[dict[str, str]]]:
    conn = sqlite3.connect(plex_db)
    conn.create_collation('icu_root', lambda a, b: (a > b) - (a < b))
    conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
    matched_ids: list[int] = []
    unmatched = 0
    unmatched_rows: list[dict[str, str]] = []
    try:
        cur = conn.cursor()
        for track in tracks:
            title = track.get("title", "").strip()
            artist = track.get("artist", "").strip()
            album = track.get("album", "").strip()
            if not title:
                unmatched += 1
                unmatched_rows.append({"title": title, "artist": artist, "album": album})
                continue

            row = None
            if artist:
                cur.execute(
                    """
                    SELECT mi.id
                    FROM metadata_items mi
                    LEFT JOIN metadata_items parent_mi ON mi.parent_id = parent_mi.id
                    LEFT JOIN metadata_items grandparent_mi ON parent_mi.parent_id = grandparent_mi.id
                    WHERE mi.metadata_type = 10
                      AND LOWER(mi.title) = LOWER(?)
                      AND LOWER(grandparent_mi.title) = LOWER(?)
                    LIMIT 1
                    """,
                    (title, artist),
                )
                row = cur.fetchone()

            if row is None:
                cur.execute(
                    """
                    SELECT mi.id
                    FROM metadata_items mi
                    WHERE mi.metadata_type = 10
                      AND LOWER(mi.title) = LOWER(?)
                    LIMIT 1
                    """,
                    (title,),
                )
                row = cur.fetchone()

            if row is None and artist:
                cur.execute(
                    """
                    SELECT mi.id
                    FROM metadata_items mi
                    LEFT JOIN metadata_items parent_mi ON mi.parent_id = parent_mi.id
                    LEFT JOIN metadata_items grandparent_mi ON parent_mi.parent_id = grandparent_mi.id
                    WHERE mi.metadata_type = 10
                      AND LOWER(mi.title) LIKE LOWER(?)
                      AND LOWER(grandparent_mi.title) LIKE LOWER(?)
                    LIMIT 1
                    """,
                    (f"%{title}%", f"%{artist}%"),
                )
                row = cur.fetchone()

            if row is None:
                unmatched += 1
                unmatched_rows.append({"title": title, "artist": artist, "album": album})
            else:
                matched_ids.append(int(row[0]))
    finally:
        conn.close()

    return matched_ids, unmatched, unmatched_rows


def _queue_missing_tracks_via_slskd(missing_tracks: list[dict[str, str]]) -> dict[str, Any]:
    if not missing_tracks:
        return {"enabled": True, "missing_count": 0, "queued": 0, "no_results": 0, "failed": 0}

    slskd_url = os.environ.get("SLSKD_URL", "http://host.docker.internal:5032").strip()
    slskd_key = os.environ.get("SLSKD_API_KEY", "").strip()
    if not slskd_key:
        return {
            "enabled": False,
            "error": "SLSKD_API_KEY manquant (configurer l'environnement Docker du webui).",
            "missing_count": len(missing_tracks),
            "queued": 0,
            "no_results": 0,
            "failed": 0,
        }

    try:
        from slskd_downloader import SlskdClient  # type: ignore[import-not-found]
    except Exception as exc:
        return {
            "enabled": False,
            "error": f"Import slskd_downloader impossible: {exc}",
            "missing_count": len(missing_tracks),
            "queued": 0,
            "no_results": 0,
            "failed": 0,
        }

    try:
        client = SlskdClient(url=slskd_url, api_key=slskd_key)
    except Exception as exc:
        return {
            "enabled": False,
            "error": f"Connexion slskd impossible: {exc}",
            "missing_count": len(missing_tracks),
            "queued": 0,
            "no_results": 0,
            "failed": 0,
        }

    queued = 0
    no_results = 0
    failed = 0
    for track in missing_tracks:
        title = str(track.get("title") or "").strip()
        artist = str(track.get("artist") or "").strip()
        if not title:
            failed += 1
            continue
        try:
            candidates = client.search(title, artist)
            if not candidates:
                no_results += 1
                continue
            result = client.download(candidates[0])
            if result.queued:
                queued += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    return {
        "enabled": True,
        "url": slskd_url,
        "missing_count": len(missing_tracks),
        "queued": queued,
        "no_results": no_results,
        "failed": failed,
    }


@app.post("/api/playlists/plex/delete")
def api_pl_plex_delete():
    _require_pld()
    data = request.get_json(force=True)
    url = (data.get("url") or os.environ.get("PLEX_URL", _default_plex_url())).strip()
    token = (data.get("token") or os.environ.get("PLEX_TOKEN", "")).strip()
    rating_key = str(data.get("rating_key") or "").strip()
    playlist_name = str(data.get("playlist_name") or "").strip()

    if not token:
        return jsonify(error="PLEX_TOKEN manquant"), 400
    if not rating_key and not playlist_name:
        return jsonify(error="rating_key ou playlist_name requis"), 400

    try:
        if not rating_key:
            client = pld.PlexPlaylistClient(url, token)
            playlists = client.list_all(types=("audio", "video", "photo"), include_smart=True)

            # 1) Match strict
            matches = [p for p in playlists if p.title.casefold() == playlist_name.casefold()]

            # 2) Match tolérant (slug/underscore/sans emoji/sans compteur)
            if not matches:
                wanted_aliases = _playlist_name_aliases(playlist_name)
                fuzzy: list[Any] = []
                for p in playlists:
                    if wanted_aliases & _playlist_name_aliases(p.title):
                        fuzzy.append(p)
                matches = fuzzy

            if not matches:
                return jsonify(error=f"Playlist introuvable: {playlist_name}"), 404
            if len(matches) > 1:
                return jsonify(
                    error=f"Nom ambigu: {playlist_name}",
                    candidates=[asdict(p) for p in matches],
                ), 409
            rating_key = matches[0].rating_key
            playlist_name = matches[0].title

        _plex_delete_playlist(url, token, rating_key)
    except Exception as exc:
        return jsonify(error=str(exc)), 500

    return jsonify(ok=True, rating_key=rating_key, playlist_name=playlist_name)


@app.post("/playlists/delete")
def playlists_delete_form():
    """Fallback HTML form deletion for browsers/UI states where JS deletion fails."""
    url = (request.form.get("url") or os.environ.get("PLEX_URL", _default_plex_url())).strip()
    token = (request.form.get("token") or os.environ.get("PLEX_TOKEN", "")).strip()
    rating_key = str(request.form.get("rating_key") or "").strip()
    playlist_name = str(request.form.get("playlist_name") or "").strip()
    tab = str(request.form.get("tab") or "plex").strip() or "plex"

    query_args: dict[str, str] = {}
    if not token:
        query_args["delete_error"] = "PLEX_TOKEN manquant"
        return redirect(url_for("playlists_page", **query_args) + f"#pl-{tab}")

    try:
        if not rating_key and playlist_name:
            client = pld.PlexPlaylistClient(url, token)
            playlists = client.list_all(types=("audio", "video", "photo"), include_smart=True)
            matches = [p for p in playlists if p.title.casefold() == playlist_name.casefold()]
            if not matches:
                wanted_aliases = _playlist_name_aliases(playlist_name)
                matches = [p for p in playlists if wanted_aliases & _playlist_name_aliases(p.title)]
            if not matches:
                raise RuntimeError(f"Playlist introuvable: {playlist_name}")
            if len(matches) > 1:
                raise RuntimeError(f"Nom ambigu: {playlist_name}")
            rating_key = str(matches[0].rating_key)
            playlist_name = matches[0].title

        if not rating_key:
            raise RuntimeError("rating_key ou playlist_name requis")

        _plex_delete_playlist(url, token, rating_key)
        query_args["deleted"] = playlist_name or rating_key
    except Exception as exc:
        query_args["delete_error"] = str(exc)

    return redirect(url_for("playlists_page", **query_args) + f"#pl-{tab}")


@app.post("/api/playlists/import/run")
def api_pl_import_run():
    data = request.get_json(force=True)
    source = str(data.get("source") or "spotify").strip().lower()
    playlist_url = str(data.get("playlist_url") or "").strip()
    destination = str(data.get("destination") or "").strip()
    mode = str(data.get("mode") or "replace").strip().lower()
    download_missing = bool(data.get("download_missing", False))
    url = (data.get("url") or os.environ.get("PLEX_URL", _default_plex_url())).strip()
    token = (data.get("token") or os.environ.get("PLEX_TOKEN", "")).strip()

    if playlist_url and not playlist_url.lower().startswith(("http://", "https://")):
        return jsonify(error="Lien playlist invalide: utilisez http:// ou https://"), 400

    # Auto-détection simple de source si un lien brut est fourni.
    if playlist_url:
        lower_url = playlist_url.lower()
        if "open.spotify.com" in lower_url:
            source = "spotify"
        elif "youtube.com" in lower_url or "youtu.be" in lower_url:
            source = "youtube"
        elif "deezer.com" in lower_url:
            source = "deezer"
        elif "music.apple.com" in lower_url:
            source = "apple_music"
        elif "tidal.com" in lower_url:
            source = "tidal"

    if not destination:
        return jsonify(error="Dossier de destination requis"), 400
    if not token:
        return jsonify(error="PLEX_TOKEN manquant"), 400

    dest_path = Path(destination).expanduser()
    if not dest_path.exists() or not dest_path.is_dir():
        return jsonify(error=f"Dossier introuvable: {dest_path}"), 400

    if mode not in {"replace", "merge"}:
        return jsonify(error="Mode invalide (replace ou merge)"), 400

    plex_db = os.environ.get("PLEX_DB") or _find_plex_db()
    if not plex_db:
        return jsonify(error="Base Plex introuvable (configure PLEX_DB ou monte /plex)"), 500

    # Default importer: iTunes/Folder importer for generic uploads.
    cmd = [
        "python3",
        "playlists/import_itunes_playlists_to_plex.py",
        "--source-dir", str(dest_path),
        "--plex-db", str(plex_db),
        "--plex-url", url,
        "--plex-token", token,
        "--map-by-basename",
        "--apply",
    ]
    # If the source is a Deezer URL, use the Deezer importer script instead
    if source == "deezer" and playlist_url:
        cmd = [
            "python3",
            "playlists/import_deezer_to_plex.py",
            "--url",
            playlist_url,
            "--plex-url",
            url,
            "--plex-token",
            token,
        ]
    audio_library = os.environ.get("AUDIO_LIBRARY", "").strip()
    if audio_library:
        cmd.extend(["--entries-base-dir", audio_library])
    if mode == "replace":
        # pass replace flag to importer
        cmd.append("--replace")
    else:
        # merge/append semantics: for folder importer we request cleanup switches,
        # for Deezer importer the default is append so no-op here
        if source != "deezer":
            cmd.extend(["--cleanup-plex-similar", "--cleanup-plex-duplicates"])

    run = runner.launch_custom(f"playlist-import-{source}", cmd)
    return jsonify(
        ok=True,
        run_id=run.run_id,
        source=source,
        mode=mode,
        download_missing=download_missing,
        playlist_url=playlist_url,
        destination=str(dest_path),
        cmd=cmd,
        note=(
            "Lien brut reçu. Le workflow doit être configuré côté source pour pouvoir importer. "
            "Option slskd appliquée seulement à l'import Soundiiz (fichier), pas au workflow sans fichier."
        ),
    )


@app.post("/api/playlists/import/soundiiz")
def api_pl_import_soundiiz():
    data = request.get_json(force=True)
    filename = str(data.get("filename") or "playlist.csv").strip()
    content = str(data.get("content") or "")
    source = str(data.get("source") or "soundiiz").strip().lower()
    mode = str(data.get("mode") or "replace").strip().lower()
    download_missing = bool(data.get("download_missing", False))
    playlist_name_override = str(data.get("playlist_name") or "").strip()
    url = (data.get("url") or os.environ.get("PLEX_URL", _default_plex_url())).strip()
    token = (data.get("token") or os.environ.get("PLEX_TOKEN", "")).strip()

    if not content:
        return jsonify(error="Fichier vide"), 400
    if not token:
        return jsonify(error="PLEX_TOKEN manquant"), 400
    if mode not in {"replace", "merge"}:
        return jsonify(error="Mode invalide (replace ou merge)"), 400

    plex_db = _find_plex_db()
    if not plex_db:
        return jsonify(error="Base Plex introuvable (monte /plex dans le conteneur)"), 500

    try:
        parsed_name, tracks = _parse_soundiiz_content(filename, content)
    except Exception as exc:
        return jsonify(error=f"Format non lisible: {exc}"), 400

    if not tracks:
        return jsonify(error="Aucune piste détectée dans le fichier"), 400

    playlist_name = playlist_name_override or parsed_name or "Playlist importée"

    try:
        matched_ids, unmatched, unmatched_rows = _lookup_track_ids_in_plex_db(plex_db, tracks)
        final_ids = list(matched_ids)

        if mode == "merge":
            existing_key = _plex_find_playlist_rating_key(url, token, playlist_name)
            if existing_key:
                existing_ids = _plex_get_playlist_track_ids(url, token, existing_key)
                seen: set[int] = set()
                merged: list[int] = []
                for track_id in existing_ids + matched_ids:
                    if track_id in seen:
                        continue
                    seen.add(track_id)
                    merged.append(track_id)
                final_ids = merged

        _plex_create_audio_playlist(url, token, playlist_name, final_ids, replace=(mode in {"replace", "merge"}))
    except Exception as exc:
        return jsonify(error=str(exc)), 500

    slskd_result: dict[str, Any] | None = None
    if download_missing and unmatched_rows:
        slskd_result = _queue_missing_tracks_via_slskd(unmatched_rows)

    return jsonify(
        ok=True,
        source=source,
        playlist_name=playlist_name,
        total_tracks=len(tracks),
        matched=len(final_ids),
        unmatched=unmatched,
        mode=mode,
        download_missing=download_missing,
        slskd=slskd_result,
        note="Mode merge: fusion avec playlist existante + déduplication des pistes par ratingKey.",
    )


@app.post("/api/playlists/stats")
def api_pl_stats():
    _require_pld()
    data = request.get_json(force=True)
    raw_root = (data.get("path") or "").strip()
    if not raw_root:
        return jsonify(error="Chemin vide. Saisissez le dossier contenant vos playlists."), 400
    root = Path(raw_root).expanduser()
    if not root.is_dir():
        return jsonify(error=f"Dossier introuvable : {root}"), 400
    files = pld.discover_playlists(root)
    from collections import Counter
    by_format, by_enc = Counter(), Counter()
    total_entries, total_broken, errored = 0, 0, []
    index = None
    lib = data.get("library")
    if lib:
        index = pld.MusicIndex.build(Path(lib).expanduser())
    total_matched = 0
    for f in files:
        try:
            pl = pld.parse_playlist(f)
        except Exception as exc:
            errored.append({"path": str(f), "error": str(exc)})
            continue
        by_format[pl.format] += 1
        by_enc[pl.encoding] += 1
        total_entries += pl.track_count
        for e in pl.entries:
            p = pld.resolve_entry_path(e.path_or_uri, pl.path)
            if p and not p.exists():
                total_broken += 1
            if index and index.match(e, playlist_path=pl.path):
                total_matched += 1
    return jsonify({
        "playlist_count": len(files),
        "by_format": by_format.most_common(),
        "by_encoding": by_enc.most_common(),
        "total_entries": total_entries,
        "broken": total_broken,
        "matched": total_matched if index else None,
        "match_rate": (100 * total_matched / total_entries) if (index and total_entries) else None,
        "errored": errored,
    })


@app.post("/api/playlists/diff")
def api_pl_diff():
    _require_pld()
    data = request.get_json(force=True)
    left_raw = (data.get("left") or "").strip()
    right_raw = (data.get("right") or "").strip()
    if not left_raw or not right_raw:
        return jsonify(error="Renseignez les deux playlists à comparer."), 400

    left_path = Path(left_raw).expanduser()
    right_path = Path(right_raw).expanduser()
    if not left_path.is_file():
        return jsonify(error=f"Playlist A introuvable : {left_path}"), 400
    if not right_path.is_file():
        return jsonify(error=f"Playlist B introuvable : {right_path}"), 400

    left_pl = pld.parse_playlist(left_path)
    right_pl = pld.parse_playlist(right_path)

    left_rows: list[dict[str, str]] = []
    right_rows: list[dict[str, str]] = []
    left_set: set[str] = set()
    right_set: set[str] = set()

    for entry in left_pl.entries:
        path_key, title_key = _entry_signature(entry, left_pl.path)
        key = path_key or title_key
        left_set.add(key)
        left_rows.append({
            "key": key,
            "path": str(entry.path_or_uri or ""),
            "artist": str(entry.artist or ""),
            "title": str(entry.title or ""),
        })

    for entry in right_pl.entries:
        path_key, title_key = _entry_signature(entry, right_pl.path)
        key = path_key or title_key
        right_set.add(key)
        right_rows.append({
            "key": key,
            "path": str(entry.path_or_uri or ""),
            "artist": str(entry.artist or ""),
            "title": str(entry.title or ""),
        })

    added_keys = right_set - left_set
    removed_keys = left_set - right_set

    added = [row for row in right_rows if row["key"] in added_keys][:300]
    removed = [row for row in left_rows if row["key"] in removed_keys][:300]

    return jsonify({
        "left_name": left_pl.name,
        "right_name": right_pl.name,
        "left_count": left_pl.track_count,
        "right_count": right_pl.track_count,
        "same": len(left_set & right_set),
        "added": added,
        "removed": removed,
        "added_count": len(added_keys),
        "removed_count": len(removed_keys),
        "truncated": len(added_keys) > 300 or len(removed_keys) > 300,
    })


@app.post("/api/playlists/dedupe")
def api_pl_dedupe():
    _require_pld()
    data = request.get_json(force=True)
    raw_path = (data.get("path") or "").strip()
    if not raw_path:
        return jsonify(error="Renseignez un fichier playlist."), 400

    path = Path(raw_path).expanduser()
    if not path.is_file():
        return jsonify(error=f"Playlist introuvable : {path}"), 400

    playlist = pld.parse_playlist(path)
    path_groups: dict[str, list[dict[str, str]]] = {}
    title_groups: dict[str, list[dict[str, str]]] = {}

    for entry in playlist.entries:
        path_key, title_key = _entry_signature(entry, playlist.path)
        row = {
            "path": str(entry.path_or_uri or ""),
            "artist": str(entry.artist or ""),
            "title": str(entry.title or ""),
        }
        if path_key:
            path_groups.setdefault(path_key, []).append(row)
        if title_key:
            title_groups.setdefault(title_key, []).append(row)

    exact_dupes = [
        {"key": key, "count": len(rows), "examples": rows[:5]}
        for key, rows in path_groups.items() if len(rows) > 1
    ]
    meta_dupes = [
        {"key": key, "count": len(rows), "examples": rows[:5]}
        for key, rows in title_groups.items() if len(rows) > 1
    ]

    exact_dupes.sort(key=lambda item: item["count"], reverse=True)
    meta_dupes.sort(key=lambda item: item["count"], reverse=True)

    return jsonify({
        "name": playlist.name,
        "track_count": playlist.track_count,
        "exact_groups": exact_dupes[:150],
        "meta_groups": meta_dupes[:150],
        "exact_group_count": len(exact_dupes),
        "meta_group_count": len(meta_dupes),
        "truncated": len(exact_dupes) > 150 or len(meta_dupes) > 150,
    })


# -------------------- Playlists auto — règles custom --------------------
CUSTOM_PLAYLISTS_CONFIG = Path(os.environ.get(
    "PLEX_CUSTOM_PLAYLISTS_CONFIG",
    str(PROJECT_ROOT / "playlists" / "custom_auto_playlists.json"),
))

LASTFM_BUILTIN_PLAYLISTS = [
    {
        "name": "📈 Last.fm Top écoutes",
        "source": "lastfm",
        "description": "Top 10% des titres les plus écoutés sur Last.fm.",
        "requires_lastfm": True,
    },
    {
        "name": "🔥 Last.fm Rotation forte",
        "source": "lastfm",
        "description": "Titres dans la tranche d'écoute 75-90 percentile.",
        "requires_lastfm": True,
    },
    {
        "name": "🌊 Last.fm Rotation moyenne",
        "source": "lastfm",
        "description": "Titres dans la tranche d'écoute 50-75 percentile.",
        "requires_lastfm": True,
    },
    {
        "name": "🌱 Last.fm À pousser",
        "source": "lastfm",
        "description": "Titres moins écoutés mais bien notés à remettre en avant.",
        "requires_lastfm": True,
    },
]


@app.get("/api/playlists/custom/rules")
def api_custom_rules_get():
    """Retourne les règles personnalisées (playlists_custom.json)."""
    if not CUSTOM_PLAYLISTS_CONFIG.is_file():
        return jsonify({
            "playlists": [],
            "builtin_playlists": LASTFM_BUILTIN_PLAYLISTS,
            "path": str(CUSTOM_PLAYLISTS_CONFIG),
        })
    try:
        raw = json.loads(CUSTOM_PLAYLISTS_CONFIG.read_text(encoding="utf-8"))
        raw["builtin_playlists"] = LASTFM_BUILTIN_PLAYLISTS
        raw["path"] = str(CUSTOM_PLAYLISTS_CONFIG)
        return jsonify(raw)
    except Exception as exc:
        return jsonify(error=f"Lecture impossible : {exc}"), 500


@app.post("/api/playlists/custom/rules")
def api_custom_rules_save():
    """Sauvegarde les règles personnalisées dans custom_auto_playlists.json."""
    data = request.get_json(force=True) or {}
    playlists = data.get("playlists", [])
    if not isinstance(playlists, list):
        return jsonify(error="'playlists' doit être une liste"), 400
    for i, rule in enumerate(playlists):
        if not isinstance(rule, dict):
            return jsonify(error=f"Règle #{i + 1} : doit être un objet"), 400
        if not str(rule.get("name", "")).strip():
            return jsonify(error=f"Règle #{i + 1} : 'name' est requis"), 400
    payload = {"playlists": playlists}
    CUSTOM_PLAYLISTS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_PLAYLISTS_CONFIG.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return jsonify(ok=True, path=str(CUSTOM_PLAYLISTS_CONFIG), count=len(playlists))


@app.post("/api/playlists/custom/generate")
def api_custom_generate():
    """Lance auto_playlists_plexamp.py avec la config custom (optionnel : dry_run)."""
    data = request.get_json(force=True) or {}
    dry_run = bool(data.get("dry_run", False))
    randomize_poster_styles = bool(data.get("randomize_poster_styles", True))
    lastfm_user = str(data.get("lastfm_user") or "").strip()
    lastfm_api_key = str(data.get("lastfm_api_key") or "").strip()
    lastfm_period = str(data.get("lastfm_period") or "overall").strip() or "overall"
    lastfm_max_pages_raw = data.get("lastfm_max_pages", 5)
    try:
        lastfm_max_pages = max(1, int(lastfm_max_pages_raw))
    except Exception:
        lastfm_max_pages = 5

    plex_db = _find_plex_db()
    cmd = ["python3", "playlists/auto_playlists_plexamp.py", "--verbose"]
    if plex_db:
        cmd += ["--plex-db", plex_db]
    if CUSTOM_PLAYLISTS_CONFIG.is_file():
        cmd += ["--custom-config", str(CUSTOM_PLAYLISTS_CONFIG)]
    if AUTO_SELECTED_PLAYLISTS_FILE.is_file():
        cmd += ["--selected-names-file", str(AUTO_SELECTED_PLAYLISTS_FILE)]
    update_only = bool(data.get("update_only", False))
    if dry_run:
        cmd += ["--dry-run"]
    elif update_only:
        cmd += ["--update-existing"]
    if randomize_poster_styles:
        cmd += ["--randomize-poster-styles"]

    if lastfm_user:
        cmd += ["--lastfm-user", lastfm_user]
    if lastfm_period:
        cmd += ["--lastfm-period", lastfm_period]
    cmd += ["--lastfm-max-pages", str(lastfm_max_pages)]

    extra_env: dict[str, str] = {}
    if lastfm_api_key:
        extra_env["LASTFM_API_KEY"] = lastfm_api_key

    run = runner.launch_custom("playlists_auto_custom", cmd, extra_env=extra_env or None)
    return jsonify(ok=True, run_id=run.run_id, dry_run=dry_run, cmd=cmd, randomize_poster_styles=randomize_poster_styles)


@app.post("/api/playlists/posters/generate")
def api_generate_posters_only():
    """Lance la régénération des posters auto depuis l'UI."""
    data = request.get_json(force=True) or {}
    style_config_raw = str(data.get("style_config") or "").strip()
    randomize_poster_styles = bool(data.get("randomize_poster_styles", True))

    plex_db = _find_plex_db()
    cmd = ["python3", "playlists/auto_playlists_plexamp.py", "--verbose", "--posters-only"]
    if plex_db:
        cmd += ["--plex-db", plex_db]
    if AUTO_SELECTED_PLAYLISTS_FILE.is_file():
        cmd += ["--selected-names-file", str(AUTO_SELECTED_PLAYLISTS_FILE)]
    if randomize_poster_styles:
        cmd += ["--randomize-poster-styles"]

    if style_config_raw:
        style_path = Path(style_config_raw).expanduser()
        if not style_path.is_file():
            return jsonify(error=f"Style poster introuvable: {style_path}"), 400
        # Compatibilité: certaines versions du script ne supportent pas
        # --poster-style-config mais lisent déjà PLEX_POSTER_STYLE_CONFIG.
        os.environ["PLEX_POSTER_STYLE_CONFIG"] = str(style_path)
    else:
        os.environ.pop("PLEX_POSTER_STYLE_CONFIG", None)

    run = runner.launch_custom("playlists_posters_only", cmd)
    return jsonify(ok=True, run_id=run.run_id, cmd=cmd, style_config=style_config_raw, randomize_poster_styles=randomize_poster_styles)


def _make_gradient(size: int, c1: list, c2: list):
    """Dégradé diagonal rapide via PIL (sans numpy, ~50ms pour 600px)."""
    from PIL import Image
    n = size * 2 - 1
    row_data = bytes(
        int(c1[i % 3] + (c2[i % 3] - c1[i % 3]) * (i // 3) / (n - 1))
        for i in range(n * 3)
    )
    row = Image.frombytes('RGB', (n, 1), row_data)
    out = Image.new('RGB', (size, size))
    for y in range(size):
        strip = row.crop((y, 0, y + size, 1)).resize((size, 1))
        out.paste(strip, (0, y))
    return out


def _find_poster_background(title: str, style: dict) -> Optional[Path]:
    """Cherche une image de fond dans poster_backgrounds/ correspondant au titre."""
    import re as _re
    bg_dir_raw = str(style.get("backgrounds_dir", "")).strip()
    if bg_dir_raw:
        bg_dir = Path(bg_dir_raw).expanduser()
    else:
        bg_dir = PROJECT_ROOT / "playlists" / "poster_backgrounds"

    if not bg_dir.is_dir():
        return None

    title_lower = title.lower()
    title_normalized = _re.sub(r"['''\(\)\[\]&,!?]", " ", title_lower)
    title_words = set(title_normalized.split())

    EXTS = ('.jpg', '.jpeg', '.png', '.webp')
    best: Optional[Path] = None
    best_score = 0

    for img_file in sorted(bg_dir.iterdir()):
        if img_file.suffix.lower() not in EXTS:
            continue
        stem = img_file.stem.lower().replace('_', ' ').replace('-', ' ')
        stem_words = set(stem.split())
        score = len(stem_words & title_words)
        if stem in title_lower:
            score += len(stem_words)
        if score > best_score:
            best_score = score
            best = img_file

    return best if best_score > 0 else None


def _make_poster_preview_png(title: str, count: Optional[int], style: dict) -> bytes:
    """Génère un poster de prévisualisation et retourne les octets PNG."""
    import colorsys
    from PIL import Image, ImageDraw, ImageFont

    SIZE = int(style.get("size", 600))
    OVERLAY_ALPHA = int(style.get("overlay_alpha", 100))
    TITLE_SIZE = int(style.get("title_size", 44))
    SUBTITLE_SIZE = int(style.get("subtitle_size", 24))
    EMOJI_SIZE = int(style.get("emoji_size", 80))
    TITLE_START_Y = int(style.get("title_start_y", 300))
    TITLE_LINE_STEP = int(style.get("title_line_step", 50))
    TEXT_PADDING = int(style.get("text_padding", 60))
    TITLE_COLOR = tuple(style.get("title_color", [255, 255, 255, 255]))
    TITLE_SHADOW_COLOR = tuple(style.get("title_shadow_color", [0, 0, 0, 180]))
    TITLE_STROKE_WIDTH = int(style.get("title_stroke_width", 0))
    TITLE_STROKE_COLOR = tuple(style.get("title_stroke_color", [0, 0, 0, 255]))
    SUBTITLE_COLOR = tuple(style.get("subtitle_color", [200, 200, 200, 220]))
    LINE_COLOR = tuple(style.get("line_color", [255, 255, 255, 80]))
    FONT_PATH = str(style.get("font_path", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
    EMOJI_FONT_PATH = str(style.get("emoji_font_path", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"))

    # Trouver le thème (emoji + couleurs)
    title_lower = title.lower()
    emoji = str(style.get("default_emoji", "🎵"))
    default_colors = style.get("default_colors", [[70, 70, 140], [120, 120, 220]])
    c1, c2 = list(default_colors[0]), list(default_colors[1])

    matched = False
    for theme in style.get("themes", []):
        keywords = theme.get("keywords", [])
        if not keywords:
            continue
        match_mode = theme.get("match", "all")
        is_match = any(k in title_lower for k in keywords) if match_mode == "any" else all(k in title_lower for k in keywords)
        if is_match:
            colors = theme.get("colors", [[80, 80, 160], [150, 150, 230]])
            emoji = theme.get("emoji", "🎵")
            c1, c2 = list(colors[0]), list(colors[1])
            matched = True
            break

    if not matched and not style.get("default_colors"):
        h = hash(title) % 360
        r1, g1, b1 = [int(c * 255) for c in colorsys.hsv_to_rgb(h / 360, 0.7, 0.6)]
        r2, g2, b2 = [int(c * 255) for c in colorsys.hsv_to_rgb(((h + 60) % 360) / 360, 0.7, 0.8)]
        c1, c2 = [r1, g1, b1], [r2, g2, b2]

    # Fond : image custom ou dégradé diagonal de fallback
    bg_path = _find_poster_background(title, style)
    if bg_path:
        try:
            img = Image.open(bg_path).convert('RGB').resize((SIZE, SIZE), Image.LANCZOS)
        except Exception:
            bg_path = None

    if not bg_path:
        img = _make_gradient(SIZE, c1, c2)

    overlay = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, OVERLAY_ALPHA))
    img = img.convert('RGBA')
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Emoji
    try:
        emoji_font = ImageFont.truetype(EMOJI_FONT_PATH, EMOJI_SIZE)
        bbox = draw.textbbox((0, 0), emoji, font=emoji_font)
        ew = bbox[2] - bbox[0]
        draw.text(((SIZE - ew) // 2, 140), emoji, font=emoji_font, fill=(255, 255, 255, 255))
    except Exception:
        pass

    # Titre
    display_title = title
    if bool(style.get("strip_auto_prefix", True)):
        display_title = display_title.replace('[Auto]', '').strip()
    if bool(style.get("strip_count_suffix", True)):
        display_title = re.sub(r'\s*\(\d+ titres?\)\s*$', '', display_title)

    try:
        font_title = ImageFont.truetype(FONT_PATH, TITLE_SIZE)
        font_sub = ImageFont.truetype(FONT_PATH, SUBTITLE_SIZE)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    words = display_title.split()
    lines: list[str] = []
    line = ""
    for w in words:
        test = f"{line} {w}".strip()
        bbox = draw.textbbox((0, 0), test, font=font_title)
        if bbox[2] - bbox[0] > SIZE - TEXT_PADDING:
            if line:
                lines.append(line)
            line = w
        else:
            line = test
    if line:
        lines.append(line)

    y_pos = TITLE_START_Y
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font_title, stroke_width=TITLE_STROKE_WIDTH)
        tw = bbox[2] - bbox[0]
        if TITLE_STROKE_WIDTH > 0:
            draw.text(((SIZE - tw) // 2, y_pos), ln, fill=TITLE_COLOR, font=font_title,
                      stroke_width=TITLE_STROKE_WIDTH, stroke_fill=TITLE_STROKE_COLOR)
        else:
            draw.text(((SIZE - tw) // 2 + 2, y_pos + 2), ln, fill=TITLE_SHADOW_COLOR, font=font_title)
            draw.text(((SIZE - tw) // 2, y_pos), ln, fill=TITLE_COLOR, font=font_title)
        y_pos += TITLE_LINE_STEP

    draw.line([(TEXT_PADDING, y_pos + 10), (SIZE - TEXT_PADDING, y_pos + 10)], fill=LINE_COLOR, width=2)

    if count is not None:
        sub = f"{count} titres"
        bbox = draw.textbbox((0, 0), sub, font=font_sub)
        sw = bbox[2] - bbox[0]
        draw.text(((SIZE - sw) // 2, y_pos + 20), sub, fill=SUBTITLE_COLOR, font=font_sub)

    img = img.convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


@app.get("/api/playlists/poster/preview")
def api_poster_preview():
    """Génère et retourne une image PNG de prévisualisation d'un poster de playlist."""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return Response("Pillow non installé (pip install Pillow)", status=503, mimetype="text/plain")

    name = request.args.get("name", "Ma Playlist").strip() or "Ma Playlist"
    count_raw = request.args.get("count", "").strip()
    count: Optional[int] = int(count_raw) if count_raw.isdigit() else None
    style_path_raw = request.args.get("style", "").strip()

    style: dict = {}
    if style_path_raw:
        sp = Path(style_path_raw).expanduser()
        if sp.is_file():
            try:
                style = json.loads(sp.read_text())
            except Exception:
                pass

    # Style par défaut si rien de fourni
    if not style:
        default_sp = PROJECT_ROOT / "playlists" / "poster_style.default.json"
        if default_sp.is_file():
            try:
                style = json.loads(default_sp.read_text())
            except Exception:
                pass

    try:
        png = _make_poster_preview_png(name, count, style)
    except Exception as exc:
        return Response(f"Erreur génération: {exc}", status=500, mimetype="text/plain")

    return Response(png, mimetype="image/png", headers={
        "Cache-Control": "no-store",
    })


def _build_auto_playlists_snapshot(
    *,
    lastfm_user: str = "",
    lastfm_api_key: str = "",
    lastfm_period: str = "overall",
    lastfm_max_pages: int = 5,
) -> tuple[PlexAmpAutoPlaylist, dict[str, list[dict[str, Any]]], list[dict[str, Any]], str]:
    """Construit un snapshot des playlists auto générées pour preview/import sélectif."""
    if PlexAmpAutoPlaylist is None:
        raise RuntimeError(f"Moteur playlists auto indisponible: {_auto_pl_err}")

    plex_db = _find_plex_db()
    if not plex_db:
        raise RuntimeError("Base Plex introuvable")

    generator = PlexAmpAutoPlaylist(
        plex_db_path=plex_db,
        verbose=False,
        lastfm_user=lastfm_user,
        lastfm_api_key=lastfm_api_key,
        lastfm_period=lastfm_period,
        lastfm_max_pages=lastfm_max_pages,
    )
    tracks = generator.get_track_data()
    if not tracks:
        raise RuntimeError("Aucune piste détectée dans la base Plex")

    playlists = generator._build_all_playlists(  # pylint: disable=protected-access
        tracks,
        custom_config=str(CUSTOM_PLAYLISTS_CONFIG) if CUSTOM_PLAYLISTS_CONFIG.is_file() else None,
    )
    return generator, playlists, tracks, plex_db


@app.post("/api/playlists/auto/preview")
def api_auto_preview():
    """Prévisualise toutes les playlists auto candidates avec nombre de titres."""
    data = request.get_json(silent=True) or {}
    lastfm_user = str(data.get("lastfm_user") or "").strip()
    lastfm_api_key = str(data.get("lastfm_api_key") or "").strip()
    lastfm_period = str(data.get("lastfm_period") or "overall").strip() or "overall"
    try:
        lastfm_max_pages = max(1, int(data.get("lastfm_max_pages", 5)))
    except Exception:
        lastfm_max_pages = 5

    try:
        _, playlists, tracks, plex_db = _build_auto_playlists_snapshot(
            lastfm_user=lastfm_user,
            lastfm_api_key=lastfm_api_key,
            lastfm_period=lastfm_period,
            lastfm_max_pages=lastfm_max_pages,
        )
    except Exception as exc:
        return jsonify(error=str(exc)), 500

    items = [
        {"name": name, "count": len(entries)}
        for name, entries in sorted(playlists.items(), key=lambda kv: kv[0].lower())
        if entries
    ]
    return jsonify(
        ok=True,
        playlist_count=len(items),
        track_count=len(tracks),
        plex_db=plex_db,
        items=items,
    )


@app.post("/api/playlists/auto/apply")
def api_auto_apply_selected():
    """Crée uniquement les playlists cochées par l'utilisateur dans Plex."""
    data = request.get_json(force=True) or {}
    selected_names_raw = data.get("selected_names") or []
    append_existing = bool(data.get("append_existing", False))
    replace_all_existing = bool(data.get("replace_all_existing", False))
    style_config = str(data.get("style_config") or "").strip()
    lastfm_user = str(data.get("lastfm_user") or "").strip()
    lastfm_api_key = str(data.get("lastfm_api_key") or "").strip()
    lastfm_period = str(data.get("lastfm_period") or "overall").strip() or "overall"
    try:
        lastfm_max_pages = max(1, int(data.get("lastfm_max_pages", 5)))
    except Exception:
        lastfm_max_pages = 5

    if not isinstance(selected_names_raw, list):
        return jsonify(error="selected_names doit être une liste"), 400

    if style_config:
        style_path = Path(style_config).expanduser()
        if not style_path.is_file():
            return jsonify(error=f"Style poster introuvable: {style_path}"), 400
        os.environ["PLEX_POSTER_STYLE_CONFIG"] = str(style_path)

    selected_names = [str(x).strip() for x in selected_names_raw if str(x).strip()]
    if not selected_names:
        return jsonify(error="Aucune playlist sélectionnée"), 400
    selected_names = sorted(set(selected_names))

    try:
        generator, playlists, tracks, plex_db = _build_auto_playlists_snapshot(
            lastfm_user=lastfm_user,
            lastfm_api_key=lastfm_api_key,
            lastfm_period=lastfm_period,
            lastfm_max_pages=lastfm_max_pages,
        )
    except Exception as exc:
        return jsonify(error=str(exc)), 500

    available = set(playlists.keys())
    unknown = [name for name in selected_names if name not in available]
    to_apply = [name for name in selected_names if name in available]

    AUTO_SELECTED_PLAYLISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTO_SELECTED_PLAYLISTS_FILE.write_text(
        json.dumps(
            {
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "selected_names": to_apply,
                "poster_style": os.environ.get("PLEX_POSTER_STYLE_CONFIG", ""),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    deleted_before: list[str] = []
    if replace_all_existing:
        url = os.environ.get("PLEX_URL", _default_plex_url())
        token = os.environ.get("PLEX_TOKEN", "")
        if not token:
            return jsonify(error="PLEX_TOKEN manquant: impossible de supprimer les playlists existantes"), 400
        try:
            deleted_before = _plex_delete_matching_audio_playlists(url, token, list(playlists.keys()))
        except Exception as exc:
            return jsonify(error=f"Suppression des playlists existantes impossible: {exc}"), 500

    effective_append_existing = append_existing and not replace_all_existing

    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for name in to_apply:
        entries = playlists.get(name) or []
        ok = generator.save_playlist_to_plex(name, entries, append_existing=effective_append_existing)
        if ok:
            created.append({"name": name, "count": len(entries)})
        else:
            failed.append({"name": name, "count": len(entries)})

    poster_error = ""
    if created:
        try:
            generator.generate_playlist_posters(target_names=to_apply)
        except Exception as exc:
            poster_error = str(exc)

    return jsonify(
        ok=len(failed) == 0,
        plex_db=plex_db,
        track_count=len(tracks),
        requested=len(selected_names),
        applied=len(to_apply),
        created_count=len(created),
        failed_count=len(failed),
        unknown_count=len(unknown),
        created=created,
        failed=failed,
        unknown=unknown,
        replace_all_existing=replace_all_existing,
        deleted_before_count=len(deleted_before),
        deleted_before=deleted_before,
        selected_file=str(AUTO_SELECTED_PLAYLISTS_FILE),
        poster_error=poster_error,
    )


# -------------------- Logs --------------------
@app.get("/log/<path:name>")
def view_log(name: str):
    log_path = (LOGS_DIR / name).resolve()
    try:
        log_path.relative_to(LOGS_DIR.resolve())  # protection traversal
    except ValueError:
        abort(403)
    if not log_path.is_file():
        abort(404)
    size = log_path.stat().st_size
    # Lire les derniers ~128 Ko
    with log_path.open("rb") as f:
        if size > 131072:
            f.seek(-131072, 2)
            f.readline()  # ignore partial
        content = f.read().decode("utf-8", errors="replace")
    return render_template("log_view.html", name=name, content=content, size=size)


@app.get("/log/<path:name>/stream")
def stream_log(name: str):
    log_path = (LOGS_DIR / name).resolve()
    try:
        log_path.relative_to(LOGS_DIR.resolve())
    except ValueError:
        abort(403)
    if not log_path.is_file():
        abort(404)

    def gen():
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            idle = 0
            while idle < 600:  # coupe après 10 min d'inactivité
                line = f.readline()
                if not line:
                    idle += 1
                    time.sleep(1)
                    yield ": keepalive\n\n"
                    continue
                idle = 0
                yield f"data: {json.dumps(line.rstrip())}\n\n"

    return Response(stream_with_context(gen()), mimetype="text/event-stream")


# -------------------- Plex logs (systemd, read-only) --------------------
def _plex_log_path(name: str) -> Path:
    """Résout et valide un chemin dans PLEX_LOGS_DIR/plex_daily."""
    base = (PLEX_LOGS_DIR / "plex_daily").resolve()
    log_path = (base / name).resolve()
    try:
        log_path.relative_to(base)
    except ValueError:
        abort(403)
    if not log_path.is_file():
        abort(404)
    return log_path


@app.get("/plog/<path:name>")
def view_plex_log(name: str):
    log_path = _plex_log_path(name)
    size = log_path.stat().st_size
    with log_path.open("rb") as f:
        if size > 131072:
            f.seek(-131072, 2)
            f.readline()
        content = f.read().decode("utf-8", errors="replace")
    return render_template("log_view.html", name=name, content=content, size=size)


@app.get("/plog/<path:name>/stream")
def stream_plex_log(name: str):
    log_path = _plex_log_path(name)

    def gen():
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            idle = 0
            while idle < 600:
                line = f.readline()
                if not line:
                    idle += 1
                    time.sleep(1)
                    yield ": keepalive\n\n"
                    continue
                idle = 0
                yield f"data: {json.dumps(line.rstrip())}\n\n"

    return Response(stream_with_context(gen()), mimetype="text/event-stream")


@app.get("/health")
def health():
    return jsonify(ok=True, project_root=str(PROJECT_ROOT),
                   logs_dir=str(LOGS_DIR), pld=pld is not None)


# -------------------- Contrôle Docker --------------------
def _docker_compose_cmd(action_args: list[str]) -> tuple[list[str], list[str], dict[str, str]]:
    """Construit la commande docker compose/docker-compose selon ce qui est dispo.
    Retourne (cmd_v2, cmd_v1, extra_env)."""
    # Cherche le fichier docker-compose.yml (monté à /app ou parent)
    compose_file: Optional[str] = None
    for candidate in (Path("/app"), PROJECT_ROOT, PROJECT_ROOT.parent):
        cf = candidate / "docker-compose.yml"
        if cf.exists():
            compose_file = str(cf)
            break

    base_env: list[str] = []
    if compose_file:
        base_env = ["-f", compose_file]

    # Nom de projet = nom du dossier hôte (déduit du compose file ou env)
    project_name = os.environ.get("COMPOSE_PROJECT_NAME", "scripts")

    # docker compose (plugin v2)
    cmd_v2 = ["docker", "compose"] + base_env + action_args
    # docker-compose (standalone v1)
    cmd_v1 = ["docker-compose"] + base_env + action_args

    extra_env = {"COMPOSE_PROJECT_NAME": project_name}
    return cmd_v2, cmd_v1, extra_env


def _run_compose(action_args: list[str], timeout: int = 120) -> tuple[bool, subprocess.CompletedProcess[str], str]:
    """Exécute docker compose avec fallback docker-compose.
    Retourne (ok, result, cmd_as_string)."""
    cmd_v2, cmd_v1, extra_env = _docker_compose_cmd(action_args)
    merged_env = {**os.environ, **extra_env}

    last_result: Optional[subprocess.CompletedProcess[str]] = None
    last_cmd = ""
    for cmd in (cmd_v2, cmd_v1):
        last_cmd = " ".join(cmd)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=merged_env,
        )
        last_result = result
        # v2 indisponible dans l'image: on bascule sur docker-compose
        if result.returncode != 0 and cmd is cmd_v2 and (
            "is not a docker command" in result.stderr
            or "is not a docker command" in result.stdout
            or result.returncode == 125
        ):
            continue
        return (result.returncode == 0, result, last_cmd)

    assert last_result is not None
    return (False, last_result, last_cmd)


_DOCKER_ACTION_ARGS: dict[str, list[str]] = {
    "restart": ["restart", "webui"],
    "stop":    ["stop",    "webui"],
    "rebuild": ["up", "-d", "--build", "webui"],
}


def _docker_control_enabled() -> bool:
    return os.environ.get("WEBUI_ALLOW_DOCKER_CONTROL", "0") == "1"


def _docker_disabled_response():
    return jsonify(ok=False, error="Contrôle Docker désactivé par sécurité (WEBUI_ALLOW_DOCKER_CONTROL=0)"), 403

@app.post("/api/docker/<action>")
def api_docker_action(action: str):
    if not _docker_control_enabled():
        return _docker_disabled_response()
    if action not in _DOCKER_ACTION_ARGS:
        return jsonify(ok=False, error="Action inconnue"), 400

    # Evite d'interrompre un run actif via restart/rebuild/stop du webui.
    if action in {"restart", "rebuild", "stop"}:
        active = runner.running_runs()
        if active:
            jobs = [f"{r.job_key} ({r.run_id})" for r in active]
            return jsonify(
                ok=False,
                error="Un ou plusieurs runs sont en cours. Attendez la fin ou arrêtez-les avant cette action.",
                running=jobs,
            ), 409

    try:
        ok, result, cmd = _run_compose(_DOCKER_ACTION_ARGS[action], timeout=120)
    except FileNotFoundError:
        return jsonify(ok=False, error="docker / docker-compose introuvable dans le conteneur — vérifiez le montage /var/run/docker.sock"), 503
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, error="Timeout (120s)"), 504

    return jsonify(
        ok=ok,
        stdout=result.stdout[-2000:],
        stderr=result.stderr[-500:],
        returncode=result.returncode,
        cmd=cmd,
    )


@app.get("/api/docker/services")
def api_docker_services():
    """Liste des services docker-compose disponibles pour consultation des logs."""
    if not _docker_control_enabled():
        return _docker_disabled_response()
    try:
        ok, result, cmd = _run_compose(["config", "--services"], timeout=30)
    except FileNotFoundError:
        return jsonify(ok=False, error="docker / docker-compose introuvable"), 503
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, error="Timeout (30s)"), 504

    services = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return jsonify(ok=ok, services=services, cmd=cmd, stderr=result.stderr[-500:])


@app.get("/api/docker/logs")
def api_docker_logs():
    """Lit les logs docker d'un service (ou tous les services si vide)."""
    if not _docker_control_enabled():
        return _docker_disabled_response()

    service = (request.args.get("service") or "").strip()
    tail = int(request.args.get("tail", 300))
    tail = min(max(tail, 20), 5000)

    args = ["logs", "--no-color", "--tail", str(tail)]
    if service:
        args.append(service)

    try:
        ok, result, cmd = _run_compose(args, timeout=60)
    except FileNotFoundError:
        return jsonify(ok=False, error="docker / docker-compose introuvable"), 503
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, error="Timeout (60s)"), 504

    return jsonify(
        ok=ok,
        cmd=cmd,
        service=service or "all",
        tail=tail,
        output=result.stdout[-200000:],
        stderr=result.stderr[-2000:],
        returncode=result.returncode,
    )


@app.post("/api/docker/logs/cleanup")
def api_logs_cleanup():
    """Nettoyage des logs applicatifs ou des ressources docker inutilisées."""
    payload = request.get_json(silent=True) or {}
    scope = (payload.get("scope") or "app").strip().lower()

    if scope == "app":
        deleted = []
        skipped = []
        with runner._lock:
            running_logs = {
                Path(r.log_path)
                for r in runner.runs.values()
                if r.log_path and not r.done.is_set()
            }
            for p in LOGS_DIR.glob("*.log"):
                if p in running_logs:
                    skipped.append(p.name)
                    continue
                try:
                    p.unlink(missing_ok=True)
                    deleted.append(p.name)
                except Exception:
                    skipped.append(p.name)

            try:
                RUNS_STATE_FILE.unlink(missing_ok=True)
                deleted.append(RUNS_STATE_FILE.name)
            except Exception:
                skipped.append(RUNS_STATE_FILE.name)

            # Conserve seulement les runs en cours (si présents)
            runner.runs = {
                r.run_id: r
                for r in runner.runs.values()
                if not r.done.is_set()
            }
            runner._save_state_locked()

        return jsonify(ok=True, scope="app", deleted=deleted, skipped=skipped)

    if scope == "docker":
        if not _docker_control_enabled():
            return _docker_disabled_response()

        # Prune uniquement les ressources inutilisées, sans arrêter les services actifs.
        try:
            res = subprocess.run(
                ["docker", "system", "prune", "-f"],
                capture_output=True,
                text=True,
                timeout=90,
                env=os.environ.copy(),
            )
        except FileNotFoundError:
            return jsonify(ok=False, error="docker introuvable"), 503
        except subprocess.TimeoutExpired:
            return jsonify(ok=False, error="Timeout (90s)"), 504
        return jsonify(
            ok=res.returncode == 0,
            scope="docker",
            stdout=res.stdout[-4000:],
            stderr=res.stderr[-1000:],
            returncode=res.returncode,
            cmd="docker system prune -f",
        )

    return jsonify(ok=False, error="scope invalide (app|docker)"), 400


# -------------------- Docker — vue globale --------------------

def _docker_ps() -> list[dict]:
    """Retourne tous les containers (docker ps -a), ports nettoyés."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    containers = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Déduplique les ports IPv6 (garde seulement 0.0.0.0 ou 127.x.x.x)
        raw_ports = c.get("Ports", "")
        cleaned: list[str] = []
        seen: set[str] = set()
        for p in raw_ports.split(", "):
            p = p.strip()
            # Filtre les variantes IPv6 : [::]:port  :::port  ::port
            if not p or p.startswith("[::") or re.match(r'^:{2,}', p):
                continue
            if p not in seen:
                seen.add(p)
                cleaned.append(p)
        c["PortsList"] = cleaned
        containers.append(c)
    return containers


@app.route("/docker")
def docker_page():
    return render_template("docker.html", docker_control_enabled=_docker_control_enabled())


@app.get("/htmx/docker/containers")
def htmx_docker_containers():
    containers = _docker_ps()
    return render_template(
        "_containers.html",
        containers=containers,
        docker_control_enabled=_docker_control_enabled(),
    )


@app.get("/api/docker/containers")
def api_docker_containers():
    return jsonify(ok=True, containers=_docker_ps())


@app.post("/api/docker/container/<name>/<action>")
def api_docker_container_action(name: str, action: str):
    if not _docker_control_enabled():
        return _docker_disabled_response()
    if action not in {"start", "stop", "restart"}:
        return jsonify(ok=False, error="Action invalide"), 400
    if not re.match(r'^[a-zA-Z0-9_\-\.]+$', name):
        return jsonify(ok=False, error="Nom de container invalide"), 400
    try:
        result = subprocess.run(
            ["docker", action, name],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return jsonify(ok=False, error="docker introuvable"), 503
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, error="Timeout (60s)"), 504
    return jsonify(
        ok=result.returncode == 0,
        stdout=result.stdout[-2000:],
        stderr=result.stderr[-500:],
    )


@app.get("/api/docker/container/<name>/logs")
def api_docker_container_logs(name: str):
    if not re.match(r'^[a-zA-Z0-9_\-\.]+$', name):
        return jsonify(ok=False, error="Nom invalide"), 400
    tail = int(request.args.get("tail", 200))
    tail = min(max(tail, 20), 5000)
    try:
        result = subprocess.run(
            ["docker", "logs", "--no-color", "--tail", str(tail), name],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return jsonify(ok=False, error="docker introuvable"), 503
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, error="Timeout (30s)"), 504
    combined = (result.stdout + "\n" + result.stderr).strip()
    return jsonify(ok=True, logs=combined[-100_000:], name=name)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("WEBUI_PORT", 8765))
    host = os.environ.get("WEBUI_HOST", "0.0.0.0")
    debug = bool(int(os.environ.get("WEBUI_DEBUG", "0")))
    print(f"🌐 UI sur http://{host}:{port}")
    print(f"   PROJECT_ROOT={PROJECT_ROOT}")
    print(f"   LOGS_DIR={LOGS_DIR}")
    app.run(host=host, port=port, debug=debug, threaded=True)
