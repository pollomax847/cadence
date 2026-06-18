from __future__ import annotations

import os
from pathlib import Path


# Branding (single source of truth)
APP_NAME = os.environ.get("APP_NAME", "Cadence")
APP_MARK = os.environ.get("APP_MARK", "CA")
APP_TAGLINE = os.environ.get("APP_TAGLINE", "pilotage media en rythme")


# Paths and runtime configuration
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", HERE.parent)).resolve()

_DEFAULT_LOGS_DIR = (
    "/data/logs"
    if Path("/data/logs").exists() or Path("/data").exists()
    else str(PROJECT_ROOT / "logs")
)
LOGS_DIR = Path(os.environ.get("LOGS_DIR", _DEFAULT_LOGS_DIR))
LOGS_DIR.mkdir(parents=True, exist_ok=True)

RUNS_STATE_FILE = LOGS_DIR / "runs_state.json"
PLEX_LOGS_DIR = Path(os.environ.get("PLEX_LOGS_DIR", "/plex-logs"))
POSTER_STYLE_GLOB = "poster_style*.json"
AUTO_SELECTED_PLAYLISTS_FILE = PROJECT_ROOT / "data" / "auto_selected_playlists.json"

PLEX_DB_CANDIDATES = [
    "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db",
    "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db",
    "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db",
    str(Path("~/.config/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db").expanduser()),
]

HOST_ONLY_JOB_KEYS = {
    "daily",
    "ratings_sync_id3",
}
