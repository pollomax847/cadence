#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(dirname "$SCRIPT_DIR")/.venv/bin/python"
SYNC_SCRIPT="$SCRIPT_DIR/import_itunes_playlists_to_plex.py"
CONFIG_FILE="$HOME/.plex_playlist_export.conf"
LOG_DIR="$HOME/.plex/logs/plex_playlist_export"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
LOG_FILE="$LOG_DIR/export_$TIMESTAMP.log"

PLEX_URL="${PLEX_URL:-http://127.0.0.1:32400}"
PLEX_TOKEN="${PLEX_TOKEN:-***REMOVED***}"
PLEX_DB="${PLEX_DB:-/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db}"
EXPORT_DIR="${EXPORT_DIR:-/mnt/MyBook/itunes/plex_from_plex}"

mkdir -p "$LOG_DIR"

if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python virtuel introuvable: $PYTHON_BIN" | tee -a "$LOG_FILE"
    exit 1
fi

if [[ ! -f "$SYNC_SCRIPT" ]]; then
    echo "Script introuvable: $SYNC_SCRIPT" | tee -a "$LOG_FILE"
    exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Nettoyage playlists Plex (vides, doublons de nom, doublons de contenu)..." | tee -a "$LOG_FILE"

"$PYTHON_BIN" "$SYNC_SCRIPT" \
    --plex-url "$PLEX_URL" \
    --plex-token "$PLEX_TOKEN" \
    --plex-db "$PLEX_DB" \
    --cleanup-plex-empty \
    --cleanup-plex-duplicates \
    --cleanup-plex-identical \
    --cleanup-plex-similar \
    --skip-import \
    --apply 2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Export Plex -> MyBook vers $EXPORT_DIR" | tee -a "$LOG_FILE"

"$PYTHON_BIN" "$SYNC_SCRIPT" \
    --plex-url "$PLEX_URL" \
    --plex-token "$PLEX_TOKEN" \
    --plex-db "$PLEX_DB" \
    --export-plex-dir "$EXPORT_DIR" \
    --prune-export-dir \
    --skip-import \
    --apply 2>&1 | tee -a "$LOG_FILE"
