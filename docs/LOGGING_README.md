# Système de Logging Centralisé pour Scripts Audio

Tous les scripts audio utilisent maintenant un système de logging centralisé qui sauvegarde automatiquement les logs et rapports dans un dossier dédié avec rotation automatique.

## Fonctionnalités

- **Logs rotatifs** : Les fichiers de log sont automatiquement archivés chaque jour à minuit
- **Nettoyage automatique** : Suppression des anciens logs après un nombre configurable de jours
- **Rapports JSON** : Tous les rapports (ratings, analyses, etc.) sont sauvegardés dans le dossier logs
- **Console + fichiers** : Les logs sont affichés à l'écran ET sauvegardés dans des fichiers

## Structure des dossiers

```
logs/
├── album_ratings_manager.log          # Logs du script de ratings
├── album_ratings_manager.log.2025-11-25  # Archive du 25 novembre
├── album_ratings_manager.log.2025-11-26  # Archive du 26 novembre
├── detect_missing_audio.log           # Logs du script de détection
├── albums_1_star.json                 # Rapport albums 1 étoile
├── albums_2_star.json                 # Rapport albums 2 étoiles
├── files_1_star.json                  # Rapport fichiers 1 étoile
├── files_2_star.json                  # Rapport fichiers 2 étoiles
├── ratings_stats.json                 # Statistiques des ratings
└── ...
```

## Configuration

### Paramètres communs à tous les scripts

```bash
--log-dir logs              # Dossier où stocker les logs (défaut: logs)
--retention-days 30         # Nombre de jours à garder les logs (défaut: 30)
```

### Exemples d'utilisation

```bash
# Script de détection d'orphelins avec logs
python3 detect_missing_audio.py /source /target /orphans --log-dir logs --retention-days 7

# Script de ratings avec logs
python3 album_ratings_manager.py /path/to/plex.db --log-dir logs --retention-days 30

# Nettoyage manuel des anciens logs
python3 cleanup_logs.py --log-dir logs --retention-days 7
```

## Nettoyage automatique

### Via Crontab

Ajoutez cette ligne à votre crontab pour un nettoyage automatique hebdomadaire :

```bash
# Nettoyer les logs audio tous les dimanches à 2h du matin
0 2 * * 0 /home/paulceline/bin/audio/cleanup_logs.py --log-dir logs --retention-days 30
```

### Commande pour éditer la crontab

```bash
crontab -e
```

## Scripts mis à jour

Les scripts suivants utilisent maintenant le système de logging centralisé :

- `detect_missing_audio.py` : Détection d'orphelins audio
- `album_ratings_manager.py` : Analyse des ratings Plex
- `cleanup_logs.py` : Nettoyage manuel des logs

## Avantages

1. **Centralisation** : Tous les logs et rapports au même endroit
2. **Archivage automatique** : Historique conservé sans prendre trop d'espace
3. **Maintenance facile** : Nettoyage automatique des anciens fichiers
4. **Traçabilité** : Historique complet des opérations
5. **Performance** : Pas de logs qui s'accumulent dans le home

## Module logging_utils.py

Le module `logging_utils.py` fournit :

- `get_audio_logger(name, log_dir, retention_days)` : Logger configuré pour un script
- `cleanup_all_logs(log_dir, retention_days)` : Nettoyage des anciens logs
- `AudioLogger` : Classe pour une gestion avancée du logging
- `save_json_report(data, filename)` : Sauvegarde de rapports JSON dans le dossier logs