# Automatisation reelle (systemd simple)

Ce fichier est la source de verite pour l'automatisation actuelle du repo.

## Ce qui tourne vraiment

Mode unique de prod: systemd (host).

- timers/services: [systemd/](systemd/)
- orchestration principale: [systemd/plex-daily-workflow.timer](systemd/plex-daily-workflow.timer)

Docker n'est plus utilise pour le cron runtime. Docker reste disponible pour l'UI/configuration.

## Planning actuel (systemd)

- 02:35: sync ratings Plex -> ID3 ([systemd/plex-ratings-sync.timer](systemd/plex-ratings-sync.timer))
- 02:00: workflow quotidien 1 etoile / 2 etoiles ([systemd/plex-daily-workflow.timer](systemd/plex-daily-workflow.timer))
- 23:00: regeneration playlists Plexamp ([systemd/plex-auto-playlists.timer](systemd/plex-auto-playlists.timer))
- export playlists: via [systemd/plex-export-playlists.timer](systemd/plex-export-playlists.timer)
- sync MyBook: via [systemd/plex-playlists-mybook-sync.timer](systemd/plex-playlists-mybook-sync.timer)

## Docker: usage conserve

Docker est reserve a:

- interface web et configuration ([docker-compose.yml](docker-compose.yml), services `webui` et `webui_tls`)
- commandes ponctuelles si besoin (`docker compose run --rm scripts ...`)

## Playlists auto: synchro uniquement sur selection

- La synchro auto de playlists lit la selection sauvegardee dans [data/auto_selected_playlists.json](data/auto_selected_playlists.json).
- Cette selection est enregistree automatiquement quand vous utilisez l'UI et cliquez sur Importer la selection.
- Si la selection est vide, aucune playlist n'est synchronisee automatiquement.

## Lancer une regeneration immediate

Sans Docker (recommande):

```bash
./playlists/generate_plexamp_playlists.sh --refresh
```

Optionnel avec Docker one-shot:

```bash
docker compose run --rm scripts run ./playlists/generate_plexamp_playlists.sh --refresh
```

## Bonnes pratiques

- Garder systemd comme source unique d'automatisation.
- Ne pas recreer un cron Docker permanent en parallele.
- Utiliser `--refresh` pour forcer une regeneration complete.
- Consulter les logs:
   - systemd user: `journalctl --user -u plex-auto-playlists.service -f`
   - logs fichiers: `~/.plex/logs/plex_daily/`