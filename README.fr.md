# Cadence 🎵

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-requis-blue.svg)](https://docs.docker.com/engine/install/)

> Français | **[English](README.md)**

Outil de gestion de bibliothèque audio auto-hébergé : renomme automatiquement les fichiers via Shazam, synchronise les métadonnées depuis Plex, organise les dossiers au format Lidarr — le tout depuis un tableau de bord web.

---

## Fonctionnalités

- **Tableau de bord web** — lance et surveille les tâches depuis un navigateur (Flask + HTMX)
- **Renommage automatique via Shazam** — identifie les fichiers audio et les renomme `Artiste - Titre.mp3`
- **Fallback AcoustID** — quand Shazam échoue, interroge MusicBrainz par empreinte audio
- **Sync métadonnées Plex** — réécrit les tags corrigés (titre, artiste, album, année, genre) depuis ta bibliothèque Plex vers les fichiers
- **Organisation style Lidarr** — déplace les fichiers dans `Artiste/Album (Année)/01 - Artiste - Album - Titre.ext`
- **Intégration iTunes** — lit ta bibliothèque Apple Music / iTunes
- **mergerfs & multi-disques** — supporte les montages poolés ou individuels
- **Détection de tags placeholders** — ignore les tags génériques (`-Artiste-`, `-Titre-`, `Inconnu`) et force la reconnaissance Shazam

---

## Démarrage rapide

**Prérequis :** [Docker](https://docs.docker.com/engine/install/) avec Compose v2

```bash
git clone https://github.com/pollomax847/cadence.git
cd cadence
./setup.sh
```

L'assistant de configuration pose quelques questions (dossier musique, token Plex, configuration des disques) et génère automatiquement `.env` + `docker-compose.override.yml`. Aucune modification de YAML requise.

Après le setup :
```
Interface web → http://localhost:8900
```

---

## Assistant de configuration

```
── Bibliothèque musicale ──────────────────
  Comment est organisée ta musique ?
    1) Un seul dossier
    2) Plusieurs disques montés séparément
    3) mergerfs (pool de disques fusionnés)

  Choix [1] : 3
  Point de montage mergerfs : /mnt/music
  Ajouter les disques physiques ? [o/n] : o
  Disque physique 1 : /mnt/disk1
  Disque physique 2 : /mnt/disk2
```

Pour reconfigurer à tout moment :
```bash
./setup.sh --reset
```

---

## AcoustID (optionnel)

Pour le fallback MusicBrainz quand Shazam échoue, obtiens une clé gratuite sur [acoustid.org/new-application](https://acoustid.org/new-application) et saisis-la pendant le setup.

---

## Commandes Docker

```bash
# Démarrer l'interface web
docker compose up -d webui

# Arrêter
docker compose down

# Lancer un script ponctuel
docker compose run --rm scripts run python3 playlists/write_tags.py --all --apply

# Reconstruire après une modification du code
docker compose build && docker compose up -d webui
```

---

## Structure du projet

```
cadence/
├── setup.sh                     # assistant de configuration interactif
├── docker-compose.yml           # définitions de services de base
├── docker-compose.override.yml  # montages disques (généré par setup.sh)
├── .env                         # configuration (généré par setup.sh)
├── Dockerfile
├── webui/                       # interface web Flask + HTMX
├── playlists/
│   └── write_tags.py            # sync tags Plex → fichiers
├── ratings/                     # scripts de ratings Plex
├── utils/
├── docker/
│   ├── entrypoint.sh
│   └── Caddyfile                # proxy TLS optionnel
└── docker-data/                 # logs et état runtime
```

---

## Référence de configuration

Variables principales dans `.env` (généré par `setup.sh`) :

| Variable | Description |
|---|---|
| `MUSIC_HOST` | Chemin vers ta bibliothèque musicale |
| `PLEX_TOKEN` | Token d'authentification Plex |
| `PLEX_URL` | URL du serveur Plex |
| `ACOUSTID_API_KEY` | Clé API AcoustID (gratuite) |
| `WEBUI_PORT` | Port de l'interface web (défaut : 8900) |
| `AUDIO_SCRIPTS_HOST` | Chemin vers le dossier des scripts audio |
| `MUSIC_SORT_HOST` | Chemin vers le script d'organisation Lidarr |

---

## Dépannage

**Shazam ne reconnaît pas les fichiers en batch ?**
Normal — Shazam limite les requêtes en masse. Cadence applique un délai adaptatif (1s en cas de succès, 4s en cas d'échec) et bascule automatiquement sur AcoustID.

**Fichiers renommés en garbage (`-Artiste- - -Titre-.mp3`) ?**
Corrigé dans cette version — les tags placeholders sont maintenant détectés et ignorés.

**L'interface web ne démarre pas ?**
```bash
docker compose logs webui
```

---

## Licence

MIT — voir [LICENSE](LICENSE.LICENSE)
