# Playlists helpers

Ce dossier contient des scripts pour générer et importer des playlists vers Plex.

## import_deezer_to_plex.py

But: récupérer une playlist publique Deezer et l'importer dans Plex en utilisant
le pipeline de matching existant (`import_csv_to_plex.py`).

Usage rapide:

- Dry-run (ne modifie pas Plex):

```bash
python3 playlists/import_deezer_to_plex.py --url "https://www.deezer.com/playlist/908622995" --plex-token "<PLEX_TOKEN>" --dry-run
```

- Importer et fusionner (par défaut, n'écrase pas):

```bash
python3 playlists/import_deezer_to_plex.py --url "https://www.deezer.com/playlist/908622995" --plex-token "<PLEX_TOKEN>"
```

- Forcer le remplacement d'une playlist existante:

```bash
python3 playlists/import_deezer_to_plex.py --url "..." --plex-token "<PLEX_TOKEN>" --replace
```

Notes:
- Le script écrit un CSV temporaire puis appelle `import_csv_to_plex.py`.
- Par défaut, le comportement est `append`/merge pour éviter d'écraser des playlists
  existantes. Utilisez `--replace` pour remplacer explicitement.
- Pour obtenir automatiquement `PLEX_TOKEN`, utilisez `utils/detect_plex_token.py`.
