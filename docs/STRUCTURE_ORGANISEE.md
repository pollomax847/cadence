# Structure Organisée du Dossier Audio

Ce dossier a été réorganisé par type d'application pour une meilleure maintenance.

## 📁 Structure des Dossiers

```
audio/
├── plex/              # Scripts liés à Plex (sync, ratings, notifications)
├── cleanup/           # Scripts de nettoyage automatique
├── conversion/        # Scripts de conversion audio (OPUS, etc.)
├── download/          # Scripts de téléchargement (Freyr, etc.)
├── import/            # Scripts d'import (Spotify, etc.)
├── organization/      # Scripts d'organisation (Lidarr, etc.)
├── maintenance/       # Scripts de maintenance mensuelle
├── playlists/         # Scripts de gestion des playlists
├── monitoring/        # Scripts de surveillance et rapports
├── utils/             # Utilitaires divers et scripts d'installation
├── config/            # Configurations cron
├── docs/              # Documentation complète
└── logs/              # Logs et rapports (existant)
```

## 🔧 Configurations Cron

Tous les fichiers de configuration cron ont été mis à jour avec les nouveaux chemins :

- `config/crontab_auto_cleanup_2_stars.conf` → appelle `cleanup/auto_cleanup_2_stars.sh`
- `config/crontab_daily_ratings_sync.conf` → appelle `plex/plex_daily_ratings_sync.sh`
- `config/crontab_daily_workflow.conf` → appelle `plex/plex_daily_workflow.sh`
- `config/crontab_monthly_workflow.conf` → appelle plusieurs scripts dans leurs nouveaux dossiers

## ⚠️ Points d'Attention

- Les chemins dans les scripts ont été mis à jour pour utiliser des chemins relatifs quand possible
- Les configurations cron utilisent des chemins absolus mis à jour
- Le fichier `ratings_stats.json` est maintenant cherché dans `logs/`

## 🚀 Utilisation

Pour installer une configuration cron :
```bash
crontab config/crontab_daily_ratings_sync.conf
```

Pour exécuter un script manuellement :
```bash
./plex/plex_rating_sync_complete.py --auto-find-db
```