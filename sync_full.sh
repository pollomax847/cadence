#!/usr/bin/env bash
# sync_full.sh — Synchronisation complète Cadence
#
# Étapes :
#   1. Détection token Plex + DB
#   2. Scan ratings 1★ → suppression fichiers
#   3. Scan ratings 2★ → songrec-rename + clear rating
#   4. Maj playlists auto (posters aléatoires)
#   5. Scan/vidage corbeille Plex
#   6. Rotation des logs (garde 30 derniers)
#
# Usage :
#   ./sync_full.sh                # run complet
#   ./sync_full.sh --dry-run      # simulation sans suppression ni création
#   ./sync_full.sh --skip-ratings # saute les étapes ratings (1★ / 2★)
#   ./sync_full.sh --skip-playlists
#   ./sync_full.sh --no-2star     # saute le pipeline songrec 2★

set -euo pipefail

# ─── Chemins ──────────────────────────────────────────────────────────────────
BASE="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$BASE/bin/.venv/bin/python3"
LOGS_DIR="$BASE/logs"
LOG_FILE="$LOGS_DIR/sync_full_$(date +%Y%m%d_%H%M%S).log"

PLEX_DB_CANDIDATES=(
    "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
    "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
    "$HOME/.config/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
    "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"
)

SELECTED_PLAYLISTS_FILE="$BASE/data/auto_selected_playlists.json"

# ─── Options ──────────────────────────────────────────────────────────────────
DRY_RUN=0
SKIP_RATINGS=0
SKIP_PLAYLISTS=0
NO_2STAR=0

for arg in "$@"; do
    case "$arg" in
        --dry-run)        DRY_RUN=1 ;;
        --skip-ratings)   SKIP_RATINGS=1 ;;
        --skip-playlists) SKIP_PLAYLISTS=1 ;;
        --no-2star)       NO_2STAR=1 ;;
        --help|-h)
            grep '^#' "$0" | sed 's/^# \?//'
            exit 0
            ;;
    esac
done

# ─── Logging ──────────────────────────────────────────────────────────────────
mkdir -p "$LOGS_DIR"

log() {
    local level="$1"; shift
    local msg="$*"
    local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] [$level] $msg" | tee -a "$LOG_FILE"
}
log_info()    { log "INFO " "$@"; }
log_ok()      { log "OK   " "$@"; }
log_warn()    { log "WARN " "$@"; }
log_error()   { log "ERROR" "$@"; }
log_section() {
    local bar="────────────────────────────────────────────────"
    log "     " ""
    log "     " "$bar"
    log "INFO " "▶  $*"
    log "     " "$bar"
}

# Capture exit code global
STEP_ERRORS=0
run_step() {
    local label="$1"; shift
    log_info "Lancement : $label"
    if "$@" >> "$LOG_FILE" 2>&1; then
        log_ok "$label terminé"
        return 0
    else
        local code=$?
        log_warn "$label a retourné le code $code (non bloquant)"
        STEP_ERRORS=$((STEP_ERRORS + 1))
        return 0  # on continue même en cas d'erreur partielle
    fi
}

# ─── En-tête ──────────────────────────────────────────────────────────────────
log_section "SYNC FULL CADENCE — $(date '+%A %d %B %Y %H:%M:%S')"
log_info "Log : $LOG_FILE"
[[ $DRY_RUN     -eq 1 ]] && log_warn "Mode --dry-run actif (aucune modification réelle)"
[[ $SKIP_RATINGS -eq 1 ]] && log_warn "--skip-ratings : étapes ratings ignorées"
[[ $SKIP_PLAYLISTS -eq 1 ]] && log_warn "--skip-playlists : génération playlists ignorée"
[[ $NO_2STAR    -eq 1 ]] && log_warn "--no-2star : pipeline 2★ ignoré"

# ─── Détection token Plex ─────────────────────────────────────────────────────
log_section "DÉTECTION TOKEN PLEX"

PLEX_TOKEN="${PLEX_TOKEN:-}"
PLEX_URL="${PLEX_URL:-http://127.0.0.1:32400}"

if [[ -z "$PLEX_TOKEN" ]]; then
    if [[ -f "$BASE/.env" ]]; then
        _env_token="$(grep -m1 '^PLEX_TOKEN=' "$BASE/.env" | cut -d'=' -f2- | tr -d '"' || true)"
        [[ -n "$_env_token" ]] && PLEX_TOKEN="$_env_token" && log_info "Token chargé depuis .env"
    fi
fi

if [[ -z "$PLEX_TOKEN" ]]; then
    _detected="$("$PYTHON" "$BASE/utils/detect_plex_token.py" 2>/dev/null || true)"
    [[ -n "$_detected" ]] && PLEX_TOKEN="$_detected" && log_info "Token détecté automatiquement"
fi

if [[ -z "$PLEX_TOKEN" ]]; then
    log_warn "PLEX_TOKEN introuvable — les étapes API Plex seront ignorées"
else
    log_ok "Token Plex OK (${#PLEX_TOKEN} chars)"
fi

export PLEX_TOKEN
export PLEX_URL

# ─── Détection DB Plex ────────────────────────────────────────────────────────
PLEX_DB=""
for _c in "${PLEX_DB_CANDIDATES[@]}"; do
    if [[ -f "$_c" ]]; then
        PLEX_DB="$_c"
        break
    fi
done

if [[ -z "$PLEX_DB" ]]; then
    log_error "Base de données Plex introuvable. Abandon."
    exit 1
fi
log_ok "DB Plex : $PLEX_DB"

# ─── ÉTAPE 1 : Ratings 1★ → suppression ──────────────────────────────────────
if [[ $SKIP_RATINGS -eq 0 ]]; then
    log_section "ÉTAPE 1 — RATINGS 1★ (suppression fichiers)"

    ONE_STAR_ARGS=(
        --plex-db "$PLEX_DB"
        --rating 1
        --skip-db-cleanup
    )
    if [[ $DRY_RUN -eq 0 ]]; then
        ONE_STAR_ARGS+=(--delete --delete-artists)
        log_info "Mode réel : les fichiers 1★ seront supprimés"
    else
        ONE_STAR_ARGS+=(--dry-run)
        log_info "Simulation : aucune suppression"
    fi

    run_step "Ratings 1★" \
        "$PYTHON" "$BASE/ratings/plex_ratings_sync.py" "${ONE_STAR_ARGS[@]}"
else
    log_warn "ÉTAPE 1 ignorée (--skip-ratings)"
fi

# ─── ÉTAPE 2 : Ratings 2★ → songrec + clear ──────────────────────────────────
if [[ $SKIP_RATINGS -eq 0 && $NO_2STAR -eq 0 ]]; then
    log_section "ÉTAPE 2 — RATINGS 2★ (songrec-rename → clear)"

    if [[ -z "$PLEX_TOKEN" ]]; then
        log_warn "PLEX_TOKEN manquant — pipeline 2★ ignoré"
    else
        TWO_STAR_ARGS=(
            --summary-json "$LOGS_DIR/2star_summary_$(date +%Y%m%d_%H%M%S).json"
        )
        [[ $DRY_RUN -eq 1 ]] && TWO_STAR_ARGS+=(--dry-run)

        run_step "Pipeline 2★" \
            "$PYTHON" "$BASE/ratings/process_2star_pipeline.py" "${TWO_STAR_ARGS[@]}"
    fi
else
    [[ $SKIP_RATINGS -eq 1 ]] && log_warn "ÉTAPE 2 ignorée (--skip-ratings)" \
                               || log_warn "ÉTAPE 2 ignorée (--no-2star)"
fi

# ─── ÉTAPE 3 : Génération playlists auto + posters aléatoires ─────────────────
if [[ $SKIP_PLAYLISTS -eq 0 ]]; then
    log_section "ÉTAPE 3 — PLAYLISTS AUTO + POSTERS ALÉATOIRES"

    if [[ -z "$PLEX_TOKEN" ]]; then
        log_warn "PLEX_TOKEN manquant — génération playlists ignorée"
    else
        PLAYLIST_ARGS=(
            --plex-db "$PLEX_DB"
            --randomize-poster-styles
            --no-export
        )

        [[ $DRY_RUN -eq 1 ]] && PLAYLIST_ARGS+=(--dry-run)

        # Utilise le fichier de sélection s'il existe
        if [[ -f "$SELECTED_PLAYLISTS_FILE" ]]; then
            PLAYLIST_ARGS+=(--selected-names-file "$SELECTED_PLAYLISTS_FILE")
            log_info "Sélection playlists : $SELECTED_PLAYLISTS_FILE"
        else
            log_warn "Pas de fichier de sélection → toutes les playlists auto seront générées"
            export PLEX_ALLOW_ALL_AUTOPLAYLISTS=1
        fi

        (
            cd "$BASE/playlists"
            run_step "Playlists auto" \
                "$PYTHON" auto_playlists_plexamp.py "${PLAYLIST_ARGS[@]}"
        )
    fi
else
    log_warn "ÉTAPE 3 ignorée (--skip-playlists)"
fi

# ─── ÉTAPE 4 : Scan Plex + vidage corbeille ───────────────────────────────────
log_section "ÉTAPE 4 — SCAN PLEX + VIDAGE CORBEILLE"

_plex_scan() {
    "$PYTHON" - <<'PY'
import os, sys, urllib.request, urllib.error, xml.etree.ElementTree as ET

token = os.environ.get("PLEX_TOKEN", "")
base  = os.environ.get("PLEX_URL", "http://127.0.0.1:32400").rstrip("/")

def req(method, path):
    r = urllib.request.Request(f"{base}{path}", method=method)
    r.add_header("X-Plex-Token", token)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return resp.read()

root  = ET.fromstring(req("GET", "/library/sections"))
audio = [(n.get("key"), n.get("title", "?"))
         for n in root.findall("Directory") if n.get("type") == "artist"]
if not audio:
    print("Aucune section audio trouvée"); sys.exit(0)

for key, title in audio:
    req("GET", f"/library/sections/{key}/refresh")
    print(f"  Refresh : {title}")
for key, title in audio:
    for method in ("PUT", "POST"):
        try:
            req(method, f"/library/sections/{key}/emptyTrash")
            print(f"  Corbeille vidée : {title}"); break
        except urllib.error.HTTPError:
            continue
print("Scan + corbeille OK")
PY
}

if [[ -z "$PLEX_TOKEN" ]]; then
    log_warn "PLEX_TOKEN manquant — scan Plex ignoré"
elif [[ $DRY_RUN -eq 1 ]]; then
    log_info "[sim] Scan Plex + vidage corbeille ignoré en dry-run"
else
    run_step "Scan + corbeille Plex" _plex_scan
fi

# ─── ÉTAPE 5 : Rotation des logs ─────────────────────────────────────────────
log_section "ÉTAPE 5 — ROTATION DES LOGS"

# Garde les 30 derniers sync_full_*.log
mapfile -t old_logs < <(ls -t "$LOGS_DIR"/sync_full_*.log 2>/dev/null | tail -n +31)
if [[ ${#old_logs[@]} -gt 0 ]]; then
    rm -f "${old_logs[@]}"
    log_ok "Supprimé ${#old_logs[@]} ancien(s) log(s) sync_full"
else
    log_ok "Pas d'anciens logs à supprimer"
fi

# Garde les 10 derniers résumés 2★
mapfile -t old_2star < <(ls -t "$LOGS_DIR"/2star_summary_*.json 2>/dev/null | tail -n +11)
if [[ ${#old_2star[@]} -gt 0 ]]; then
    rm -f "${old_2star[@]}"
    log_ok "Supprimé ${#old_2star[@]} ancien(s) résumé(s) 2★"
fi

# Tronque spotify_daily.log à 5000 lignes (log append-only de run_daily.sh)
SPOTIFY_LOG="$LOGS_DIR/spotify_daily.log"
if [[ -f "$SPOTIFY_LOG" ]]; then
    _lines=$(wc -l < "$SPOTIFY_LOG")
    if [[ $_lines -gt 5000 ]]; then
        tail -n 5000 "$SPOTIFY_LOG" > "${SPOTIFY_LOG}.tmp" && mv "${SPOTIFY_LOG}.tmp" "$SPOTIFY_LOG"
        log_ok "spotify_daily.log tronqué à 5000 lignes (était $_lines)"
    fi
fi

# ─── Résumé final ─────────────────────────────────────────────────────────────
log_section "RÉSUMÉ FINAL"
log_info "Fin : $(date '+%Y-%m-%d %H:%M:%S')"
log_info "Log complet : $LOG_FILE"

if [[ $STEP_ERRORS -eq 0 ]]; then
    log_ok "Toutes les étapes OK"
else
    log_warn "$STEP_ERRORS étape(s) avec erreur(s) — vérifier le log"
fi

exit 0
