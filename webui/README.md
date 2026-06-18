# PulsePilot WebUI — Interface web Flask + HTMX

PulsePilot est le tableau de bord pour piloter la suite de scripts Plex/iTunes/audio.

## Démarrage rapide

### Avec Docker (recommandé)

```bash
cd ~/scripts
cp docker/.env.example .env           # adapte les chemins
docker compose -f docker-compose.yml up -d webui
# → http://localhost:8765
```

### En local (dev)

```bash
cd ~/scripts
source .venv/bin/activate
pip install flask
python3 webui/app.py
# → http://localhost:8765
```

## Fonctionnalités

- **Accueil** : tuiles par catégorie (ratings, workflows, playlists, iTunes) — 1 clic = lance le script, stream live du stdout
- **Jobs** : historique des runs, statut, durée, exit code
- **Playlists** : outils interactifs (scan/analyze/match/plex/stats) branchés sur `playlist_detector`
- **Logs** : navigation et tail -f en temps réel (SSE) des fichiers de `LOGS_DIR`

## Sécurité

Le minimum recommandé est :

```bash
# .env
WEBUI_TOKEN=un-secret-long-aleatoire
WEBUI_BIND=127.0.0.1
```

Puis accéder via `http://localhost:8765/?token=un-secret-long-aleatoire`
(ou envoyer le header `X-Token`).

Le webui est désormais durci sur plusieurs points :

- le `PLEX_TOKEN` d'environnement n'est plus injecté dans le HTML
- le token Plex n'est plus conservé dans `localStorage`
- des headers de sécurité HTTP sont ajoutés à chaque réponse
- le bind Docker par défaut est limité à `127.0.0.1`
- le contrôle Docker depuis l'UI est désactivé par défaut

Pour une connexion chiffrée, place le webui derrière un reverse proxy TLS
(Nginx, Caddy, Traefik) et active :

```bash
WEBUI_ENFORCE_HTTPS=1
WEBUI_COOKIE_SECURE=1
```

Dans ce projet, un proxy TLS local est prêt via Caddy :

```bash
docker compose up -d webui webui_tls
# puis ouvrir https://localhost:9443
```

Note : le certificat est signé par la CA interne de Caddy (`tls internal`).
Le navigateur peut demander une confirmation de confiance la première fois.

Si tu veux réactiver volontairement les actions Docker depuis l'interface :

```bash
WEBUI_ALLOW_DOCKER_CONTROL=1
```

Mode recommandé : garder `WEBUI_ALLOW_DOCKER_CONTROL=0` dans `.env`
et utiliser l'override opt-in uniquement quand nécessaire :

```bash
docker compose -f docker-compose.yml -f docker-compose.docker-control.yml up -d webui
```

⚠ N'expose jamais ce service directement sur Internet. Il permet
d'exécuter des scripts côté serveur et le montage du socket Docker reste très sensible.

## Variables d'environnement

| Variable | Défaut | Rôle |
|----------|--------|------|
| `WEBUI_PORT` | `8765` | Port d'écoute |
| `WEBUI_BIND` | `127.0.0.1` | Adresse d'exposition Docker |
| `WEBUI_TOKEN` | *(vide)* | Si défini, auth obligatoire |
| `WEBUI_ENFORCE_HTTPS` | `0` | Redirige vers HTTPS derrière un proxy TLS |
| `WEBUI_COOKIE_SECURE` | `1` | Marque les cookies comme sécurisés |
| `WEBUI_ALLOW_DOCKER_CONTROL` | `0` | Réactive explicitement les actions Docker depuis l'UI |
| `PROJECT_ROOT` | parent de `webui/` | Racine du projet |
| `LOGS_DIR` | `/data/logs` | Dossier de logs |
| `PLEX_URL` / `PLEX_TOKEN` | *(selon env)* | API Plex |
