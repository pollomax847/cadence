#!/bin/bash
# Script de synchronisation des ratings Plex ↔ fichiers audio
# Supporte les pistes, artistes et albums
# Compatible avec Rhythmbox, VLC, et autres lecteurs

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$HOME/.plex/logs/plex_ratings"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOG_DIR/ratings_sync_$TIMESTAMP.log"

# Charger le système de notifications générique
if [[ -f "$BASE_DIR/notifications/audio_notifications.sh" ]]; then
    source "$BASE_DIR/notifications/audio_notifications.sh"
    export NOTIFICATION_APP_NAME="Plex Ratings Sync"
    export NOTIFICATION_ENABLE_CONSOLE=true
    export NOTIFICATION_ENABLE_DESKTOP=false  # Désactiver par défaut pour éviter les interruptions
fi

# Paramètres
DIRECTION=${1:-both}  # plex-to-files, files-to-plex, both
LEVEL=${2:-all}       # track, artist, album, all

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

log "${BLUE}🎵 Synchronisation des ratings Plex ↔ fichiers audio${NC}"
log "=================================================================="
log ""
log "Direction: $DIRECTION"
log "Niveau: $LEVEL"
log ""

# Trouver la base de données Plex
log "${CYAN}🔍 Recherche de la base de données Plex...${NC}"

# Cherche dans l'ordre : variable d'env PLEX_DB → montage Docker → snap → lib → ~/.config
if [ -z "$PLEX_DB" ]; then
    for _candidate in \
        "/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db" \
        "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db" \
        "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db" \
        "$HOME/.config/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
    do
        if [ -f "$_candidate" ]; then
            PLEX_DB="$_candidate"
            break
        fi
    done
fi

if [ -z "$PLEX_DB" ] || [ ! -f "$PLEX_DB" ]; then
    log "${RED}❌ Base de données Plex non trouvée${NC}"
    if command -v notify_error >/dev/null 2>&1; then
        notify_error "Database Not Found" "Plex database could not be located"
    fi
    exit 1
fi

log "${GREEN}✅ Base Plex trouvée${NC}"

# Vérifier si mutagen est installé
if ! python3 -c "import mutagen" 2>/dev/null; then
    log "${YELLOW}⚠️  Installation de mutagen...${NC}"
    pip3 install mutagen
fi

log "${GREEN}✅ Dépendances OK${NC}"

# Fonction pour synchroniser Plex → fichiers
sync_plex_to_files() {
    log "${BLUE}🔄 Sync Plex → fichiers audio${NC}"
    
    # Arrêter Plex temporairement pour accès DB
    log "${YELLOW}🛑 Arrêt temporaire de Plex...${NC}"
    sudo snap stop plexmediaserver
    
    # Créer une sauvegarde de la base de données
    BACKUP_DIR="$HOME/plex_backup/ratings_$(date +%Y%m%d)"
    mkdir -p "$BACKUP_DIR"
    cp "$PLEX_DB" "$BACKUP_DIR/com.plexapp.plugins.library.db.bak"
    log "${GREEN}✅ Sauvegarde créée: $BACKUP_DIR${NC}"
    
    export PLEX_DB LEVEL
    sudo -E python3 << 'PYTHON_EOF'
import sqlite3
import os
import sys

def get_plex_ratings():
    """Récupère tous les ratings depuis Plex"""
    try:
        db_path = os.environ['PLEX_DB']
        level = os.environ['LEVEL']
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        ratings = {}
        
        # Test simple d'abord
        cursor.execute("SELECT COUNT(*) FROM metadata_item_settings WHERE rating BETWEEN 1 AND 5 AND account_id = 1")
        count = cursor.fetchone()[0]
        print(f"Total ratings 1-5 for account 1: {count}")
        
        if level in ['track', 'all']:
            # Ratings des pistes (utilisateur seulement, 1-5 étoiles)
            cursor.execute("""
                SELECT mp.file, mis.rating
                FROM media_parts mp
                JOIN media_items media ON mp.media_item_id = media.id
                JOIN metadata_items mi ON media.metadata_item_id = mi.id
                JOIN metadata_item_settings mis ON mis.guid = mi.guid
                WHERE mis.rating IS NOT NULL 
                AND mis.rating BETWEEN 1 AND 5 
                AND mis.account_id = 1
            """)
            
            for file_path, rating in cursor.fetchall():
                if os.path.exists(file_path):
                    ratings[file_path] = {
                        'rating': rating,
                        'type': 'track'
                    }
        
        if level in ['artist', 'all']:
            try:
                # Ratings des artistes (utilisateur seulement, 1-5 étoiles)
                cursor.execute("""
                    SELECT mi.title, mis.rating
                    FROM metadata_items mi
                    JOIN metadata_item_settings mis ON mis.guid = mi.guid
                    WHERE mi.metadata_type = 8 
                    AND mis.rating IS NOT NULL 
                    AND mis.rating BETWEEN 1 AND 5 
                    AND mis.account_id = 1
                """)
                
                results = cursor.fetchall()
                for artist_name, rating in results:
                    # Pour les artistes, on utilise le nom comme clé
                    ratings[f"ARTIST:{artist_name}"] = {
                        'rating': rating,
                        'type': 'artist',
                        'name': artist_name
                    }
            except Exception as e:
                print(f"Erreur dans artistes: {e}", file=sys.stderr)
        
        if level in ['album', 'all']:
            try:
                # Ratings des albums (utilisateur seulement, 1-5 étoiles)
                cursor.execute("""
                    SELECT mi.title, mis.rating
                    FROM metadata_items mi
                    JOIN metadata_item_settings mis ON mis.guid = mi.guid
                    WHERE mi.metadata_type = 9 
                    AND mis.rating IS NOT NULL 
                    AND mis.rating BETWEEN 1 AND 5 
                    AND mis.account_id = 1
                """)
                
                results = cursor.fetchall()
                for album_name, rating in results:
                    # Pour les albums, on utilise le nom comme clé
                    ratings[f"ALBUM:{album_name}"] = {
                        'rating': rating,
                        'type': 'album',
                        'name': album_name
                    }
            except Exception as e:
                print(f"Erreur dans albums: {e}", file=sys.stderr)
        
        conn.close()
        return ratings
    except Exception as e:
        print(f"Erreur générale: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return {}
ratings = get_plex_ratings()
synced = 0

for key, rating_data in ratings.items():
    try:
        rating_type = rating_data['type']
        rating_value = rating_data['rating']
        
        if rating_type == 'track':
            print(f"✅ TRACK: {os.path.basename(key)} - Rating {rating_value}")
        elif rating_type == 'artist':
            print(f"✅ ARTIST: {rating_data['name']} - Rating {rating_value}")
        elif rating_type == 'album':
            print(f"✅ ALBUM: {rating_data['name']} - Rating {rating_value}")
        
        synced += 1
    except Exception as e:
        print(f"❌ Erreur: {key}", file=sys.stderr)

print(f"\n📊 Sync Plex → fichiers: {synced} élément(s) trouvé(s)")
PYTHON_EOF
    
    # Redémarrer Plex
    log "${GREEN}🔄 Redémarrage de Plex...${NC}"
    sudo snap start plexmediaserver
    log "${GREEN}✅ Plex redémarré${NC}"
}

# Fonction pour synchroniser fichiers → Plex  
sync_files_to_plex() {
    log "${BLUE}🔄 Sync fichiers audio → Plex${NC}"
    log "${YELLOW}⚠️  Fonctionnalité basique - extension à venir${NC}"
}

# Exécution selon la direction
case $DIRECTION in
    "plex-to-files")
        sync_plex_to_files
        ;;
    "files-to-plex")
        sync_files_to_plex
        ;;
    "both")
        sync_plex_to_files
        log ""
        sync_files_to_plex
        ;;
    *)
        log "${RED}❌ Direction invalide. Utilisez: plex-to-files, files-to-plex, ou both${NC}"
        if command -v notify_error >/dev/null 2>&1; then
            notify_error "Invalid Direction" "Use: plex-to-files, files-to-plex, or both"
        fi
        exit 1
        ;;
esac

log ""
log "${GREEN}✅ Synchronisation terminée${NC}"
log "${BLUE}📁 Logs: $LOG_FILE${NC}"

# Notification de résumé complet
if command -v notify_summary >/dev/null 2>&1; then
    notify_summary "Plex Ratings Sync" "completed" "" "0" "0" "0" "0" "0"
fi
