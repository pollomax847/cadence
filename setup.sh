#!/usr/bin/env bash
# =============================================================================
# setup.sh — Assistant de configuration de Cadence
#
# Usage :
#   ./setup.sh          Configuration guidée (génère .env)
#   ./setup.sh --reset  Repart de zéro (efface l'ancien .env)
# =============================================================================

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
BLU='\033[0;34m'; CYN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()      { echo -e "${GRN}✅ $*${NC}"; }
info()    { echo -e "${BLU}ℹ️  $*${NC}"; }
warn()    { echo -e "${YLW}⚠️  $*${NC}"; }
err()     { echo -e "${RED}❌ $*${NC}" >&2; }
section() { echo -e "\n${BOLD}${CYN}── $* ──────────────────────────────────${NC}"; }

ask() {
    local prompt="$1" default="$2"
    local val
    if [[ -n "$default" ]]; then
        read -r -p "  ${prompt} [${default}] : " val
        echo "${val:-$default}"
    else
        read -r -p "  ${prompt} : " val
        echo "$val"
    fi
}

ask_dir() {
    local prompt="$1" default="$2"
    local val
    val=$(ask "$prompt" "$default")
    val="${val%/}"   # retire le slash final
    echo "$val"
}

ask_yn() {
    local prompt="$1" default="${2:-o}"
    local val
    read -r -p "  ${prompt} [${default}/n] : " val
    val="${val:-$default}"
    [[ "${val,,}" == "o" || "${val,,}" == "y" ]]
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYN}╔════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYN}║         🎵  Cadence — Setup               ║${NC}"
echo -e "${BOLD}${CYN}╚════════════════════════════════════════════╝${NC}"
echo ""
info "Ce script configure Cadence en quelques questions."
info "Un fichier .env sera généré automatiquement."
echo ""

[[ "$*" == *--reset* ]] && rm -f .env && info "Ancien .env supprimé."

if [[ -f .env ]]; then
    warn "Un fichier .env existe déjà."
    if ! ask_yn "Écraser et reconfigurer ?" "n"; then
        info "Configuration annulée. Modifie .env manuellement si besoin."
        exit 0
    fi
fi

# ── Vérification Docker ───────────────────────────────────────────────────────
section "Prérequis"
if ! command -v docker &>/dev/null; then
    err "Docker n'est pas installé."
    echo "  → https://docs.docker.com/engine/install/"
    exit 1
fi
ok "Docker $(docker --version | grep -oP '\d+\.\d+\.\d+' | head -1) détecté"

if ! docker compose version &>/dev/null 2>&1; then
    err "docker compose (v2) introuvable. Mets Docker à jour."
    exit 1
fi
ok "docker compose v2 détecté"

# ── Musique ───────────────────────────────────────────────────────────────────
section "Bibliothèque musicale"

echo ""
echo -e "  ${BOLD}Comment est organisée ta musique ?${NC}"
echo "    1) Un seul dossier (ex: /mnt/disque/Music)"
echo "    2) Plusieurs disques montés séparément"
echo "    3) mergerfs (pool de disques fusionnés)"
echo ""
DISK_MODE=$(ask "Choix" "1")

MUSIC_HOST=""
EXTRA_DISKS=()   # tableau de "chemin:nom_interne"

case "$DISK_MODE" in
    1)
        MUSIC_HOST=$(ask_dir "Dossier musique" "/mnt/Music")
        ;;
    2)
        info "Tu peux ajouter jusqu'à 5 disques. Le premier sera aussi la bibliothèque principale (/music)."
        i=1
        while [[ $i -le 5 ]]; do
            local_path=$(ask_dir "Disque $i (laisser vide pour terminer)" "")
            [[ -z "$local_path" ]] && break
            mount_name="real-disk${i}"
            EXTRA_DISKS+=("${local_path}:/${mount_name}")
            [[ $i -eq 1 ]] && MUSIC_HOST="$local_path"
            ok "Disque $i : $local_path → /${mount_name}"
            i=$(( i + 1 ))
        done
        [[ -z "$MUSIC_HOST" ]] && MUSIC_HOST="/mnt/Music"
        ;;
    3)
        info "mergerfs fusionne plusieurs disques en un seul point de montage."
        MUSIC_HOST=$(ask_dir "Point de montage mergerfs (bibliothèque fusionnée)" "/mnt/music")
        info "Pour un tagging direct sur les disques physiques (bypass mergerfs), ajoute les disques sous-jacents :"
        if ask_yn "Ajouter les disques physiques individuels ?" "o"; then
            i=1
            while [[ $i -le 5 ]]; do
                local_path=$(ask_dir "Disque physique $i (laisser vide pour terminer)" "")
                [[ -z "$local_path" ]] && break
                mount_name="real-disk${i}"
                EXTRA_DISKS+=("${local_path}:/${mount_name}")
                ok "Disque physique $i : $local_path → /${mount_name}"
                i=$(( i + 1 ))
            done
        fi
        ;;
    *)
        MUSIC_HOST=$(ask_dir "Dossier musique" "/mnt/Music")
        ;;
esac

if [[ ! -d "$MUSIC_HOST" ]]; then
    warn "$MUSIC_HOST n'existe pas encore — il sera monté quand même."
fi

# ── Plex ─────────────────────────────────────────────────────────────────────
section "Plex Media Server (optionnel)"
USE_PLEX=false
if ask_yn "Utilises-tu Plex ?" "o"; then
    USE_PLEX=true
    info "Token Plex : ouvre Plex Web → compte → XML → X-Plex-Token dans l'URL"
    PLEX_TOKEN=$(ask "Token Plex" "")
    PLEX_CONFIG_HOST=$(ask_dir "Dossier config Plex" "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server")
    PLEX_URL=$(ask "URL Plex" "http://localhost:32400")
else
    PLEX_TOKEN=""
    PLEX_CONFIG_HOST="/tmp/plex-unused"
    PLEX_URL="http://localhost:32400"
fi

# ── iTunes ────────────────────────────────────────────────────────────────────
section "iTunes / Music.app (optionnel)"
ITUNES_HOST=""
if ask_yn "Utilises-tu iTunes / Apple Music ?" "n"; then
    ITUNES_HOST=$(ask_dir "Dossier iTunes" "$HOME/Music/iTunes")
fi
[[ -z "$ITUNES_HOST" ]] && ITUNES_HOST="/tmp/itunes-unused"

# ── AcoustID ──────────────────────────────────────────────────────────────────
section "Reconnaissance musicale (AcoustID)"
info "Clé gratuite sur : https://acoustid.org/new-application"
ACOUSTID_API_KEY=$(ask "Clé API AcoustID" "")
if [[ -z "$ACOUSTID_API_KEY" ]]; then
    warn "Sans clé AcoustID, la reconnaissance de secours sera désactivée."
fi

# ── Interface web ─────────────────────────────────────────────────────────────
section "Interface web"
WEBUI_PORT=$(ask "Port de l'interface web" "8900")
info "L'interface sera accessible sur http://localhost:${WEBUI_PORT}"

# ── Scripts audio (songrec-rename, music-sort) ───────────────────────────────
section "Scripts audio"
SONGREC_BIN=""
SONGREC_RENAME_BIN=""
PLEX_SCRIPTS_HOST=""
AUDIO_SCRIPTS_HOST=""
MUSIC_SORT_HOST=""

if command -v songrec &>/dev/null; then
    SONGREC_BIN=$(command -v songrec)
    ok "songrec trouvé : $SONGREC_BIN"
else
    warn "songrec non installé — renommage Shazam désactivé."
    warn "  → sudo apt install songrec  (ou voir https://github.com/marin-m/SongRec)"
fi

if command -v songrec-rename &>/dev/null; then
    SONGREC_RENAME_BIN=$(command -v songrec-rename)
    ok "songrec-rename trouvé : $SONGREC_RENAME_BIN"
else
    warn "songrec-rename non trouvé. Indique le chemin (ou laisse vide) :"
    SONGREC_RENAME_BIN=$(ask "Chemin de songrec-rename" "")
fi

# Dossiers scripts optionnels
PLEX_SCRIPTS_HOST=$(ask_dir "Dossier plex-scripts (laisser vide si absent)" "")
AUDIO_SCRIPTS_HOST=$(ask_dir "Dossier audio-scripts (laisser vide si absent)" "")
MUSIC_SORT_HOST=$(ask_dir "Dossier music-sort/Lidarr (laisser vide si absent)" "")

# Valeurs par défaut si vides
[[ -z "$SONGREC_BIN" ]]         && SONGREC_BIN="/usr/bin/songrec"
[[ -z "$SONGREC_RENAME_BIN" ]]  && SONGREC_RENAME_BIN="/usr/local/bin/songrec-rename"
[[ -z "$PLEX_SCRIPTS_HOST" ]]   && PLEX_SCRIPTS_HOST="/home/$USER/Script/plex-scripts"
[[ -z "$AUDIO_SCRIPTS_HOST" ]]  && AUDIO_SCRIPTS_HOST="/home/$USER/Script/audio"
[[ -z "$MUSIC_SORT_HOST" ]]     && MUSIC_SORT_HOST="/home/$USER/Script/music-tagging"

# ── Écriture du .env ──────────────────────────────────────────────────────────
section "Génération des fichiers de configuration"

cat > .env <<EOF
# Généré par setup.sh — $(date '+%Y-%m-%d %H:%M')
# Modifiable à tout moment, puis : docker compose up -d --force-recreate webui

# ── Interface web ──────────────────────────────────────────────────────────────
WEBUI_PORT=${WEBUI_PORT}
WEBUI_ALLOW_DOCKER_CONTROL=1
COMPOSE_PROJECT_NAME=cadence

# ── Musique ────────────────────────────────────────────────────────────────────
MUSIC_HOST=${MUSIC_HOST}

# ── Plex ──────────────────────────────────────────────────────────────────────
PLEX_TOKEN=${PLEX_TOKEN}
PLEX_URL=${PLEX_URL}
PLEX_CONFIG_HOST=${PLEX_CONFIG_HOST}

# ── iTunes ─────────────────────────────────────────────────────────────────────
ITUNES_HOST=${ITUNES_HOST}

# ── Reconnaissance musicale ────────────────────────────────────────────────────
ACOUSTID_API_KEY=${ACOUSTID_API_KEY}

# ── Binaires et scripts ────────────────────────────────────────────────────────
SONGREC_BIN_HOST=${SONGREC_BIN}
SONGREC_RENAME_BIN_HOST=${SONGREC_RENAME_BIN}
PLEX_SCRIPTS_HOST=${PLEX_SCRIPTS_HOST}
AUDIO_SCRIPTS_HOST=${AUDIO_SCRIPTS_HOST}
MUSIC_SORT_HOST=${MUSIC_SORT_HOST}
EOF

ok ".env créé !"

# ── Génération docker-compose.override.yml pour les disques ──────────────────
if [[ ${#EXTRA_DISKS[@]} -gt 0 ]]; then
    {
        echo "# Généré par setup.sh — $(date '+%Y-%m-%d %H:%M')"
        echo "# Contient les montages de disques spécifiques à cette machine."
        echo "# Ne pas commiter dans git."
        echo "services:"
        for svc in scripts webui; do
            echo "  ${svc}:"
            echo "    volumes:"
            for disk in "${EXTRA_DISKS[@]}"; do
                src="${disk%%:*}"
                dst="${disk#*:}"
                echo "      - \"${src}:${dst}:rw\""
            done
        done
    } > docker-compose.override.yml
    ok "docker-compose.override.yml créé avec ${#EXTRA_DISKS[@]} disque(s)."
    info "Les disques sont accessibles dans le container sous /real-disk1, /real-disk2, etc."
else
    # Pas de disques extra → supprimer un éventuel ancien override
    rm -f docker-compose.override.yml
fi

# ── Build & démarrage ─────────────────────────────────────────────────────────
section "Démarrage"
if ask_yn "Construire l'image Docker et démarrer l'interface web maintenant ?" "o"; then
    echo ""
    info "Build en cours (peut prendre 1-2 minutes la première fois)..."
    docker compose build --quiet
    ok "Image construite."
    docker compose up -d webui
    echo ""
    ok "Cadence est lancé sur http://localhost:${WEBUI_PORT}"
else
    echo ""
    info "Pour démarrer plus tard :"
    echo "    docker compose up -d webui"
fi

echo ""
echo -e "${BOLD}${GRN}════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GRN} ✅  Configuration terminée !${NC}"
echo -e "${BOLD}${GRN}════════════════════════════════════════════${NC}"
echo ""
echo -e "  Interface web : ${BLU}http://localhost:${WEBUI_PORT}${NC}"
echo -e "  Reconfigurer  : ${BLU}./setup.sh --reset${NC}"
echo -e "  Arrêter       : ${BLU}docker compose down${NC}"
echo ""
