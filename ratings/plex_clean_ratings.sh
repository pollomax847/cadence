#!/bin/bash
# Script pour nettoyer automatiquement les ratings des fichiers inexistants dans Plex
# Supprime les ratings des fichiers qui ont été supprimés du disque

SCRIPT_DIR="$(dirname "$0")"
LOG_DIR="$HOME/.plex/logs/plex_ratings"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOG_DIR/clean_ratings_$TIMESTAMP.log"

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "${BLUE}🧹 Nettoyage automatique des ratings Plex${NC}"
log "=================================================================="
log ""
log "Suppression des ratings des fichiers inexistants"
log ""

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
    log "${YELLOW}ℹ️ Mode simulation activé (--dry-run)${NC}"
fi

# Trouver la base de données Plex
log "${CYAN}🔍 Recherche de la base de données Plex...${NC}"
find_plex_db() {
    local possible_paths=(
        "${PLEX_DB:-}"
        "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"
        "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
        "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
        "$HOME/.config/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
        "$HOME/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
    )

    for path in "${possible_paths[@]}"; do
        if [ -n "$path" ] && [ -f "$path" ]; then
            echo "$path"
            return 0
        fi
    done

    find /plex /var/snap/plexmediaserver "$HOME/.config/Plex Media Server" \
        -name "com.plexapp.plugins.library.db" 2>/dev/null | head -1
}

PLEX_DB=$(find_plex_db)

if [ -z "$PLEX_DB" ] || [ ! -f "$PLEX_DB" ]; then
    log "${RED}❌ Base de données Plex non trouvée${NC}"
    exit 1
fi

log "${GREEN}✅ Base Plex trouvée${NC}: $PLEX_DB"

# Arrêter Plex temporairement seulement en mode écriture sur l'hôte
if [ "$DRY_RUN" -eq 0 ]; then
    if [ ! -w "$PLEX_DB" ]; then
        log "${RED}❌ Base Plex non modifiable: $PLEX_DB${NC}"
        log "${YELLOW}ℹ️ Dans Docker, /plex est monté en lecture seule. Utilise --dry-run ou lance le script sur l'hôte.${NC}"
        exit 1
    fi

    if command -v snap >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
        log "${YELLOW}🛑 Arrêt temporaire de Plex...${NC}"
        sudo snap stop plexmediaserver
        PLEX_STOPPED=1
    else
        log "${RED}❌ Impossible d'arrêter Plex automatiquement (snap/sudo indisponible)${NC}"
        log "${YELLOW}ℹ️ Lance ce script sur l'hôte Plex ou utilise --dry-run dans Docker.${NC}"
        exit 1
    fi
fi

# Script Python pour nettoyer les ratings
export PLEX_DB DRY_RUN
python3 << 'PYTHON_EOF'
import sqlite3
import os
import sys

def clean_orphaned_ratings():
    """Supprime les ratings des fichiers qui n'existent plus"""
    try:
        dry_run = os.environ.get('DRY_RUN', '0') == '1'
        conn = sqlite3.connect(f"file:{os.environ['PLEX_DB']}?mode={'ro' if dry_run else 'rw'}", uri=True)
        cursor = conn.cursor()
        
        # Trouver tous les fichiers avec ratings
        cursor.execute("""
            SELECT mp.file, mis.rating, mi.title, mis.id as setting_id
            FROM media_parts mp
            JOIN media_items media ON mp.media_item_id = media.id
            JOIN metadata_items mi ON media.metadata_item_id = mi.id
            JOIN metadata_item_settings mis ON mis.guid = mi.guid
            WHERE mis.rating IS NOT NULL
        """)
        
        ratings = cursor.fetchall()
        cleaned = 0
        total = len(ratings)
        
        print(f"📊 Analyse de {total} fichiers avec ratings...")
        
        for file_path, rating, title, setting_id in ratings:
            if not os.path.exists(file_path):
                if dry_run:
                    print(f"🔎 Rating à supprimer pour fichier inexistant: {os.path.basename(file_path)} (était {rating}⭐)")
                else:
                    cursor.execute("DELETE FROM metadata_item_settings WHERE id = ?", (setting_id,))
                    print(f"🗑️ Rating supprimé pour fichier inexistant: {os.path.basename(file_path)} (était {rating}⭐)")
                cleaned += 1
        
        if dry_run:
            print(f"\nℹ️ Simulation terminée: {cleaned} ratings seraient supprimés")
        else:
            conn.commit()
            print(f"\n✅ Nettoyage terminé: {cleaned} ratings supprimés")
        conn.close()
        return cleaned
        
    except Exception as e:
        print(f"❌ Erreur: {e}", file=sys.stderr)
        return 0

# Exécuter le nettoyage
cleaned = clean_orphaned_ratings()
PYTHON_EOF

RESULT=$?

if [ "${PLEX_STOPPED:-0}" -eq 1 ]; then
    log "${GREEN}🔄 Redémarrage de Plex...${NC}"
    sudo snap start plexmediaserver
    log "${GREEN}✅ Plex redémarré${NC}"
fi

if [ $RESULT -eq 0 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        log "${GREEN}✅ Simulation terminée avec succès${NC}"
    else
        log "${GREEN}✅ Nettoyage terminé avec succès${NC}"
    fi
else
    log "${RED}❌ Erreur lors du nettoyage${NC}"
fi

log "${BLUE}📁 Logs: $LOG_FILE${NC}"

exit $RESULT
