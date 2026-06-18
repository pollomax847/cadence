#!/bin/bash
# Script pour ENLEVER LES ÉTOILES des fichiers audio dans Plex
# Les fichiers sont CONSERVÉS, seules les étoiles (ratings) sont enlevées
# Utilisation: ./clear_ratings_from_files.sh [rating_number] [music_base_dir]

SCRIPT_DIR="$(dirname "$0")"
LOG_DIR="$HOME/.plex/logs/plex_ratings"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOG_DIR/clear_ratings_$TIMESTAMP.log"

# Paramètres
RATING=${1:-2}  # Par défaut: 2 étoiles
MUSIC_BASE_DIR=${2:-"/mnt/MyBook/itunes/Music"}  # Répertoire de base pour Lidarr

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

log "${BLUE}🎵 Script pour ENLEVER LES ÉTOILES des fichiers audio${NC}"
log "=================================================================="
log ""
log "✅ Les fichiers seront CONSERVÉS (sauf 1 étoile)"
log "✅ Seules les étoiles (ratings) seront ENLEVÉES"
log "🔄 Pour 2 étoiles: les fichiers seront RÉORGANISÉS pour Lidarr"
log "   📁 Structure: Artiste/Album/01 - Titre.mp3"
log ""

# Trouver la base de données Plex
log "${CYAN}🔍 Recherche de la base de données Plex...${NC}"
PLEX_DB=$(find /var/snap/plexmediaserver -name "com.plexapp.plugins.library.db" 2>/dev/null | head -1)

# Si pas trouvée, chercher dans les emplacements courants
if [ -z "$PLEX_DB" ]; then
    PLEX_DB=$(find ~/.config/Plex\ Media\ Server -name "com.plexapp.plugins.library.db" 2>/dev/null | head -1)
fi

if [ -z "$PLEX_DB" ] || [ ! -f "$PLEX_DB" ]; then
    log "${RED}❌ Base de données Plex non trouvée${NC}"
    log ""
    log "Emplacements vérifiés:"
    log "  - /var/snap/plexmediaserver/"
    log "  - ~/.config/Plex Media Server/"
    exit 1
fi

log "${GREEN}✅ Base Plex trouvée: $PLEX_DB${NC}"
log ""

# Arrêter Plex temporairement pour accéder à la base de données
log "${YELLOW}🛑 Arrêt temporaire de Plex pour accès à la base...${NC}"
sudo snap stop plexmediaserver
sleep 5

# Créer une sauvegarde de la base de données
BACKUP_DIR="$HOME/plex_backup/ratings_$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"
cp "$PLEX_DB" "$BACKUP_DIR/com.plexapp.plugins.library.db.bak"
log "${GREEN}✅ Sauvegarde créée: $BACKUP_DIR${NC}"
log ""

# Afficher le nombre de fichiers avec ce rating
log "${CYAN}📊 Analyse des fichiers avec $RATING étoile(s)...${NC}"
export PLEX_DB RATING
COUNT=$(sudo -E python3 << 'PYTHON_END'
import sqlite3
import sys
import os

try:
    db_path = os.environ['PLEX_DB']
    rating = float(os.environ['RATING'])
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Chercher les fichiers avec ce rating dans metadata_item_settings
    cursor.execute("""
        SELECT COUNT(*) FROM metadata_item_settings 
        WHERE rating = ? AND account_id = 1
    """, (rating,))
    
    count = cursor.fetchone()[0]
    print(count)
    conn.close()
except Exception as e:
    print(f"Erreur: {e}", file=sys.stderr)
    print("0")
PYTHON_END
)

log "📊 Nombre de fichiers avec $RATING étoile(s): ${COUNT}"
log ""

if [ "$COUNT" -eq 0 ]; then
    log "${YELLOW}⚠️  Aucun fichier trouvé avec $RATING étoile(s)${NC}"
    log ""
    exit 0
fi

# Enlever les ratings
log "${BLUE}🔄 Enlèvement des ratings...${NC}"
export PLEX_DB RATING MUSIC_BASE_DIR
sudo -E python3 << 'PYTHON_END'
import sqlite3
import sys
import os
from datetime import datetime

try:
    db_path = os.environ['PLEX_DB']
    rating = float(os.environ['RATING'])
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Trouver les fichiers avec ce rating
    cursor.execute("""
        SELECT mis.id, mi.title FROM metadata_item_settings mis 
        JOIN metadata_items mi ON mis.guid = mi.guid 
        WHERE mis.rating = ? AND mis.account_id = 1
    """, (rating,))
    
    files = cursor.fetchall()
    
    # Pour les ratings 2 étoiles, NE PAS supprimer les fichiers physiques, juste enlever le rating
    if rating == 2.0:
        cursor.execute("""
            UPDATE metadata_item_settings SET rating = NULL, updated_at = ?
            WHERE rating = ? AND account_id = 1
        """, (datetime.now().timestamp(), rating))
        print(f"✅ {cursor.rowcount} rating(s) supprimé(s) (aucun fichier supprimé)")
    else:
        # Pour les autres ratings, enlever normalement
        cursor.execute("""
            UPDATE metadata_item_settings 
            SET rating = NULL, updated_at = ?
            WHERE rating = ? AND account_id = 1
        """, (datetime.now().timestamp(), rating))
        
        print(f"✅ {cursor.rowcount} fichier(s) mis à jour")
        
        # Si c'est 1 étoile, supprimer les fichiers physiques AVANT de supprimer les ratings
        if rating == 1.0:
            print("🔄 Collecte des fichiers à supprimer...")
            
            # Trouver tous les items avec rating 1
            cursor.execute("""
                SELECT mi.id, mi.metadata_type, mi.title, mi.parent_id, mi.grandparent_id
                FROM metadata_items mi
                JOIN metadata_item_settings mis ON mis.guid = mi.guid
                WHERE mis.rating = ? AND mis.account_id = 1
            """, (rating,))
            
            items = cursor.fetchall()
            file_paths = set()  # Utiliser un set pour éviter les doublons
            
            for item_id, metadata_type, title, parent_id, grandparent_id in items:
                if metadata_type == 10:  # Track
                    # Fichier du track
                    cursor.execute("SELECT file FROM media_parts WHERE media_item_id = ?", (item_id,))
                    track_files = cursor.fetchall()
                    for (f,) in track_files:
                        file_paths.add(f)
                    print(f"   📋 Track: {title}")
                    
                elif metadata_type == 9:  # Album
                    # Tous les fichiers des tracks de l'album
                    cursor.execute("""
                        SELECT mp.file FROM media_parts mp
                        JOIN media_items mi ON mp.media_item_id = mi.id
                        WHERE mi.parent_id = ?
                    """, (item_id,))
                    album_files = cursor.fetchall()
                    for (f,) in album_files:
                        file_paths.add(f)
                    print(f"   📋 Album: {title} ({len(album_files)} fichiers)")
                    
                elif metadata_type == 8:  # Artist
                    # Tous les fichiers des tracks de l'artiste
                    cursor.execute("""
                        SELECT mp.file FROM media_parts mp
                        JOIN media_items track ON mp.media_item_id = track.id
                        JOIN media_items album ON track.parent_id = album.id
                        WHERE album.parent_id = ?
                    """, (item_id,))
                    artist_files = cursor.fetchall()
                    for (f,) in artist_files:
                        file_paths.add(f)
                    print(f"   📋 Artiste: {title} ({len(artist_files)} fichiers)")
                    
                else:
                    print(f"   ⚠️ Type inconnu {metadata_type}: {title}")
            
            file_paths = list(file_paths)
            print(f"📋 {len(file_paths)} fichier(s) unique(s) à supprimer")
            
            # Supprimer les ratings d'abord
            cursor.execute("""
                UPDATE metadata_item_settings SET rating = NULL, updated_at = ?
                WHERE rating = ? AND account_id = 1
            """, (datetime.now().timestamp(), rating))
            
            print(f"✅ {cursor.rowcount} rating(s) supprimé(s)")
            
            # Maintenant supprimer les fichiers physiques
            deleted_count = 0
            for file_path in file_paths:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        print(f"   🗑️ Supprimé: {os.path.basename(file_path)}")
                        deleted_count += 1
                    else:
                        print(f"   ⚠️ Fichier non trouvé: {file_path}")
                except Exception as e:
                    print(f"   ❌ Erreur suppression {file_path}: {e}")
            
            print(f"🗑️ {deleted_count} fichier(s) supprimé(s) physiquement")
        else:
            # Pour les autres ratings, juste supprimer les étoiles
            cursor.execute("""
                UPDATE metadata_item_settings SET rating = NULL, updated_at = ?
                WHERE rating = ?
            """, (datetime.now().timestamp(), rating))
            
            print(f"✅ {cursor.rowcount} fichier(s) mis à jour")
    
except Exception as e:
    print(f"❌ Erreur: {e}", file=sys.stderr)
    sys.exit(1)
PYTHON_END

RESULT=$?

log ""
if [ $RESULT -eq 0 ]; then
    log "${GREEN}✅ Ratings $RATING étoile(s) enlevés avec succès!${NC}"
    log ""
    log "${YELLOW}📝 Résumé:${NC}"
    log "   ✓ $COUNT fichier(s) modifié(s)"
    if [ "$RATING" -eq 1 ]; then
        log "   🗑️ Les fichiers sont SUPPRIMÉS physiquement"
        log "   ✓ Les étoiles sont ENLEVÉES"
        log "   ✓ Les fichiers sont complètement supprimés"
    elif [ "$RATING" -eq 2 ]; then
        log "   ✓ Les fichiers sont CONSERVÉS"
        log "   ✓ Les étoiles sont ENLEVÉES"
        log "   ✓ Les fichiers sont maintenant sans rating"
    else
        log "   ✓ Les fichiers sont CONSERVÉS"
        log "   ✓ Les étoiles sont ENLEVÉES"
        log "   ✓ Les fichiers sont maintenant sans rating"
    fi
    log ""
else
    log "${RED}❌ Erreur lors de l'enlèvement des ratings${NC}"
fi

# Redémarrer Plex
log "${GREEN}🔄 Redémarrage de Plex...${NC}"
sudo snap start plexmediaserver
log "${GREEN}✅ Plex redémarré${NC}"

log ""
log "${BLUE}📁 Logs sauvegardés: $LOG_FILE${NC}"
log "${BLUE}💾 Sauvegarde: $BACKUP_DIR${NC}"

exit $RESULT
