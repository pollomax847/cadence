#!/bin/bash
# Script d'automatisation pour la synchronisation régulière des ratings Plex
# À utiliser avec cron pour un nettoyage automatique programmé

# Configuration
SCRIPT_DIR="$(dirname "$0")"
CONFIG_FILE="$HOME/.plex_ratings_sync.conf"
LOG_DIR="$HOME/.plex/logs/plex_ratings"
NOTIFICATION_EMAIL=""  # Optionnel : votre email pour les notifications

# Créer le répertoire de logs
mkdir -p "$LOG_DIR"

# Charger la configuration si elle existe
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
else
    echo "⚠️ Fichier de configuration non trouvé: $CONFIG_FILE"
    echo "Lancez d'abord: ./install_plex_ratings_sync.sh"
    exit 1
fi

# Fichiers de log avec horodatage
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOG_DIR/sync_$TIMESTAMP.log"
REPORT_FILE="$LOG_DIR/report_$TIMESTAMP.json"

# Fonction de logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Fonction d'envoi d'email (optionnelle)
send_notification() {
    local subject="$1"
    local message="$2"
    
    if [ -n "$NOTIFICATION_EMAIL" ] && command -v mail &> /dev/null; then
        echo "$message" | mail -s "$subject" "$NOTIFICATION_EMAIL"
    fi
}

# Début du script
log "🎵 Début de la synchronisation automatique Plex Ratings"
log "========================================================"

# Vérifier que Plex est accessible
if [ ! -f "$PLEX_DB_PATH" ]; then
    log "❌ ERREUR: Base Plex introuvable: $PLEX_DB_PATH"
    send_notification "Erreur Plex Ratings Sync" "Base de données Plex introuvable"
    exit 1
fi

log "💾 Sauvegarde automatique des suppressions: désactivée"

# Lancer la synchronisation
log "🔄 Lancement de la synchronisation..."

python3 "$SCRIPT_DIR/plex_ratings_sync.py" \
    --plex-db "$PLEX_DB_PATH" \
    --rating "$TARGET_RATING" \
    --delete \
    ${VERBOSE:+--verbose} \
    2>&1 | tee -a "$LOG_FILE"

# Vérifier le code de sortie
if [ ${PIPESTATUS[0]} -eq 0 ]; then
    log "✅ Synchronisation terminée avec succès"
    
    # Compter les fichiers supprimés
    deleted_count=$(grep "🗑️ Supprimé:" "$LOG_FILE" | wc -l)
    log "📊 Fichiers supprimés: $deleted_count"
    
    # Notification de succès
    if [ "$deleted_count" -gt 0 ]; then
        send_notification "Plex Ratings Sync - Succès" "$deleted_count fichiers supprimés lors de la synchronisation."
    fi
    
else
    log "❌ Erreur lors de la synchronisation"
    send_notification "Plex Ratings Sync - Erreur" "La synchronisation a échoué. Consultez les logs."
fi

# Nettoyage des anciens logs (garder seulement les 30 derniers)
log "🧹 Nettoyage des anciens logs..."
ls -t "$LOG_DIR"/sync_*.log | tail -n +31 | xargs rm -f 2>/dev/null || true
ls -t "$LOG_DIR"/report_*.json | tail -n +31 | xargs rm -f 2>/dev/null || true

# Aucun nettoyage de sauvegardes: la suppression se fait sans backup.

# Optionnel: Déclencher un scan de bibliothèque Plex
# Si vous avez configuré l'API Plex, vous pouvez ajouter ici une requête pour rafraîchir la bibliothèque

log "🎉 Script d'automatisation terminé"

# Afficher un résumé
echo
echo "📋 RÉSUMÉ DE L'EXÉCUTION"
echo "========================="
echo "🕒 Début: $(head -1 "$LOG_FILE" | cut -d']' -f1 | tr -d '[')"
echo "🕒 Fin: $(date '+%Y-%m-%d %H:%M:%S')"
echo "📁 Log: $LOG_FILE"
echo "💾 Sauvegarde: désactivée"
echo "🎯 Rating cible: $TARGET_RATING étoile(s)"

# Statistiques rapides
deleted_count=$(grep -c "🗑️ Supprimé:" "$LOG_FILE" 2>/dev/null || echo "0")
skipped_count=$(grep -c "⏭️" "$LOG_FILE" 2>/dev/null || echo "0")
error_count=$(grep -c "❌" "$LOG_FILE" 2>/dev/null || echo "0")

echo "📊 Statistiques:"
echo "   🗑️ Supprimés: $deleted_count"
echo "   ⏭️ Ignorés: $skipped_count"
echo "   ❌ Erreurs: $error_count"