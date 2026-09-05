#!/bin/bash
# Script d'automatisation des playlists PlexAmp
# Génère des playlists intelligentes basées sur les ratings et métadonnées

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$HOME/logs/plexamp_playlists"
SELECTED_NAMES_FILE_DEFAULT="$SCRIPT_DIR/../data/auto_selected_playlists.json"

# Recherche automatique de la base Plex (Docker-aware)
find_plex_db() {
    local candidates=(
        "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"
        "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
        "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
        "$HOME/.config/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
    )
    for p in "${candidates[@]}"; do
        [ -f "$p" ] && echo "$p" && return 0
    done
    return 1
}

PLEX_DB="$(find_plex_db)"

# Créer les répertoires nécessaires
mkdir -p "$LOG_DIR"

# Fonction de log
log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_DIR/auto_playlists.log"
}

# Vérifier les prérequis
check_requirements() {
    log "${BLUE}🔍 Vérification des prérequis...${NC}"
    
    # Vérifier Python
    if ! command -v python3 &> /dev/null; then
        log "${RED}❌ Python3 non trouvé${NC}"
        return 1
    fi
    
    # Vérifier la base Plex
    if [ ! -f "$PLEX_DB" ]; then
        log "${YELLOW}⚠️ Base Plex non trouvée: $PLEX_DB${NC}"
        log "   Vérifiez que Plex Media Server est installé et configuré"
        return 1
    fi
    
    # Vérifier le script Python
    if [ ! -f "$SCRIPT_DIR/auto_playlists_plexamp.py" ]; then
        log "${RED}❌ Script Python non trouvé${NC}"
        return 1
    fi
    
    log "${GREEN}✅ Prérequis vérifiés${NC}"
    return 0
}

# Créer les playlists automatiques
create_playlists() {
    local mode="$1"      # "create" ou "dry-run"
    local extra_flag="${2:-}"  # ex: "--update-existing"
    local extra_args=()
    [ -n "$extra_flag" ] && extra_args+=("$extra_flag")

    if [ "${PLEX_APPEND_EXISTING:-0}" = "1" ]; then
        extra_args+=("--append-existing")
    fi
    if [ -n "${PLEX_CUSTOM_PLAYLISTS_CONFIG:-}" ]; then
        extra_args+=("--custom-config" "$PLEX_CUSTOM_PLAYLISTS_CONFIG")
    fi
    local selected_names_file="${PLEX_AUTO_SELECTED_PLAYLISTS_FILE:-$SELECTED_NAMES_FILE_DEFAULT}"
    if [ -f "$selected_names_file" ]; then
        extra_args+=("--selected-names-file" "$selected_names_file")
        log "🎯 Mode selection: playlists limitees via $selected_names_file"

        local selected_style
        selected_style="$(python3 - "$selected_names_file" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    style = str((data or {}).get('poster_style') or '').strip()
    print(style)
except Exception:
    print('')
PY
)"
        if [ -n "$selected_style" ] && [ -f "$selected_style" ]; then
            extra_args+=("--poster-style-config" "$selected_style")
            log "🎨 Style posters selectionne: $selected_style"
        fi
    elif [ "${PLEX_ALLOW_ALL_AUTOPLAYLISTS:-0}" = "1" ]; then
        log "⚠️ Aucune selection sauvegardee: mode legacy autorise (sync globale)"
    else
        log "❌ Aucune selection sauvegardee ($selected_names_file)."
        log "   Ouvrez l'UI playlists, faites Previsualiser puis Importer la selection pour enregistrer la liste auto."
        return 1
    fi
    
    if [ "$mode" = "dry-run" ]; then
        log "${YELLOW}📋 Mode simulation - aperçu des playlists${NC}"
        python3 "$SCRIPT_DIR/auto_playlists_plexamp.py" --plex-db "$PLEX_DB" --dry-run --verbose "${extra_args[@]}"
    else
        log "${GREEN}🎵 Génération des playlists PlexAmp${NC}"
        python3 "$SCRIPT_DIR/auto_playlists_plexamp.py" --plex-db "$PLEX_DB" --verbose "${extra_args[@]}"
    fi
}

# Nettoyer les anciennes playlists automatiques via l'API Plex
cleanup_old_playlists() {
    log "${BLUE}🧹 Nettoyage des anciennes playlists automatiques${NC}"

    PLEX_URL="${PLEX_URL:-http://127.0.0.1:32400}" \
    PLEX_TOKEN="${PLEX_TOKEN:-}" \
    python3 << 'EOF'
import os
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

PLEX_URL = os.environ.get("PLEX_URL", "http://127.0.0.1:32400").rstrip("/")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")

def plex_api(method, path):
    sep = "&" if "?" in path else "?"
    url = f"{PLEX_URL}{path}{sep}X-Plex-Token={PLEX_TOKEN}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/xml")
    with urllib.request.urlopen(req) as resp:
        return resp.read()

try:
    raw = plex_api("GET", "/playlists")
    root = ET.fromstring(raw)
    playlists = [(el.get("ratingKey"), el.get("title")) for el in root.findall(".//Playlist")]

    deleted = 0
    for rk, title in playlists:
        if title and title.startswith("[Auto] "):
            try:
                plex_api("DELETE", f"/playlists/{rk}")
                print(f"🗑️ Supprimée: {title}")
                deleted += 1
            except Exception as e:
                print(f"⚠️ Impossible de supprimer {title}: {e}")

    print(f"✅ {deleted} anciennes playlists supprimées")

except Exception as e:
    print(f"❌ Erreur nettoyage: {e}")
    sys.exit(1)
EOF
}

# Menu principal
main_menu() {
    echo ""
    echo -e "${BLUE}🎵 GÉNÉRATEUR DE PLAYLISTS PLEXAMP${NC}"
    echo "====================================="
    echo ""
    echo "1. 📋 Aperçu des playlists (simulation)"
    echo "2. 🎵 Créer toutes les playlists"
    echo "3. 🧹 Nettoyer les anciennes playlists"
    echo "4. 🔄 Nettoyer + Recréer toutes les playlists"
    echo "5. ❌ Quitter"
    echo ""
    read -p "Votre choix (1-5): " choice
    
    case $choice in
        1)
            create_playlists "dry-run"
            ;;
        2)
            create_playlists "create"
            ;;
        3)
            cleanup_old_playlists
            ;;
        4)
            log "${BLUE}🔄 Nettoyage + Recréation complète${NC}"
            cleanup_old_playlists
            sleep 2
            create_playlists "create"
            ;;
        5)
            log "${GREEN}👋 Au revoir !${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Choix invalide${NC}"
            main_menu
            ;;
    esac
}

# Traitement des arguments en ligne de commande
case "${1:-}" in
    "--preview"|"-p")
        check_requirements && create_playlists "dry-run"
        ;;
    "--create"|"-c")
        check_requirements && create_playlists "create"
        ;;
    "--clean"|"--cleanup")
        cleanup_old_playlists
        ;;
    "--refresh"|"-r")
        check_requirements && cleanup_old_playlists && create_playlists "create"
        ;;
    "--update"|"-u")
        check_requirements && create_playlists "create" "--update-existing"
        ;;
    "--help"|"-h")
        echo "Usage: $0 [option]"
        echo ""
        echo "Options:"
        echo "  -p, --preview     Aperçu des playlists (simulation)"
        echo "  -c, --create      Créer toutes les playlists"
        echo "  --clean           Nettoyer les anciennes playlists"
        echo "  -r, --refresh     Nettoyer + recréer toutes les playlists"
        echo "  -u, --update      Mettre à jour en place (ajoute/retire sans recréer)"
        echo "  -h, --help        Afficher cette aide"
        echo ""
        echo "Sans option: mode interactif"
        ;;
    "")
        check_requirements
        if [ $? -ne 0 ]; then
            log "${RED}❌ Prérequis non satisfaits${NC}"
            exit 1
        fi

        # Les jobs Docker/cron/web n'ont pas d'entrée interactive: lancer directement la génération.
        if [ ! -t 0 ]; then
            log "${BLUE}🤖 Mode non interactif détecté: génération automatique${NC}"
            create_playlists "create"
            exit $?
        fi

        # Mode interactif
        main_menu
        ;;
    *)
        echo -e "${RED}❌ Option inconnue: $1${NC}"
        echo "Utilisez --help pour voir les options disponibles"
        exit 1
        ;;
esac