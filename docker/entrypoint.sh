#!/usr/bin/env bash
# entrypoint.sh — dispatcher du conteneur plex-scripts.
#
# Modes :
#   run <script> [args...]   Lance n'importe quel script du projet
#   cron                     Lance crond en foreground (schedules internes)
#   shell                    Ouvre un shell bash interactif
#   help                     Affiche l'aide

set -euo pipefail
cd /app

case "${1:-help}" in
    run)
        shift
        exec "$@"
        ;;
    cron)
        # Installer la crontab embarquée si fournie
        if [[ -f /app/docker/crontab ]]; then
            crontab /app/docker/crontab 2>/dev/null || true
        fi
        echo "[entrypoint] Démarrage cron en foreground"
        exec cron -f -L 15
        ;;
    shell|bash)
        exec /bin/bash
        ;;
    help|--help|-h|"")
        cat <<EOF
plex-scripts — conteneur Docker

Modes :
  docker run plex-scripts run python3 playlists/detect_playlists.py scan /music
  docker run plex-scripts run ./workflows/plex_daily_workflow.sh
  docker run plex-scripts cron        # démon avec crontab embarquée
  docker run plex-scripts shell       # shell interactif

Volumes attendus :
  /plex       (ro)  base Plex SQLite
  /music            bibliothèque musicale
  /playlists        dossier M3U (optionnel)
  /data             logs & queues
EOF
        ;;
    *)
        # N'importe quel autre argument est traité comme une commande à exécuter
        exec "$@"
        ;;
esac
