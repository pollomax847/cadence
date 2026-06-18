# 🎓 Tutoriel pas-à-pas — Suite de scripts Plex / iTunes / Audio

Ce tutoriel t'accompagne de l'installation à la mise en production automatisée.
Compte environ **15–30 minutes** pour tout mettre en place.

> 💡 Si tu es pressé : exécute `./install.sh` puis saute directement à [§ 4 — Premier workflow](#4-premier-workflow).

---

## Table des matières

1. [Prérequis](#1-prérequis)
2. [Installation automatique](#2-installation-automatique)
3. [Configuration](#3-configuration)
4. [Premier workflow](#4-premier-workflow)
5. [Notes, playlists et synchronisation](#5-notes-playlists-et-synchronisation)
6. [Automatisation systemd](#6-automatisation-systemd)
7. [Dépannage](#7-dépannage)

---

## 1. Prérequis

- Linux (testé sur **Linux Mint / Ubuntu / Debian**)
- Un serveur **Plex Media Server** local avec au moins une bibliothèque musicale
- Une collection musicale accessible localement (ex. `/home/paulceline/Musiques` ou `/mnt/MyBook/Musiques`)
- Droits `sudo` pour l'installation initiale des paquets

Vérifie rapidement :

```bash
python3 --version          # >= 3.10 recommandé
sqlite3 --version
ls ~/.var/app/tv.plex.PlexMediaServer 2>/dev/null || \
  ls "$HOME/Library/Application Support/Plex Media Server" 2>/dev/null || \
  echo "Trouve où Plex stocke sa DB sur ta machine"
```

---

## 2. Installation automatique

Depuis la racine du projet :

```bash
cd ~/scripts
./install.sh
```

Le script va (tout, automatiquement) :

1. Détecter et installer les paquets système manquants (`python3-venv`, `sqlite3`, `ffmpeg`, `libnotify-bin`, `jq`…)
2. Créer le virtualenv [.venv/](.venv/) et installer [requirements.txt](requirements.txt)
3. Rendre tous les `.sh` / `.py` exécutables
4. Créer les répertoires de travail (`~/.plex/logs`, `~/songrec_queue`…)
5. Copier ET **activer** tous les timers systemd utilisateur
6. Activer le linger (`loginctl`) pour que les timers tournent hors session

Options :

```bash
./install.sh --interactive  # Demander confirmation à chaque étape
./install.sh --no-apt       # Sauter l'étape apt
./install.sh --no-systemd   # Ne pas installer les timers
./install.sh --no-linger    # Ne pas activer le linger
```

À la fin, active le venv dans ton shell :

```bash
source .venv/bin/activate
```

---

## 3. Configuration

### 3.1 Localiser la base Plex

La majorité des scripts lit directement la base SQLite de Plex.
Repère son emplacement (valeurs typiques) :

| Installation Plex | Chemin de la base |
|---|---|
| Paquet `.deb` | `/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db` |
| Snap | `~/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db` |
| Flatpak | `~/.var/app/tv.plex.PlexMediaServer/data/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db` |

Exporte la variable pour ta session :

```bash
export PLEX_DB="/chemin/vers/com.plexapp.plugins.library.db"
```

💡 Ajoute cette ligne à ton `~/.bashrc` ou `~/.zshrc` pour la rendre permanente.

### 3.2 Bibliothèque audio

Vérifie / ajuste la variable `AUDIO_LIBRARY` en tête des scripts principaux si
ton dossier musique n'est pas `/home/paulceline/Musiques` :

- [workflows/plex_daily_workflow.sh](workflows/plex_daily_workflow.sh)
- [ratings/plex_daily_ratings_sync.sh](ratings/plex_daily_ratings_sync.sh)

### 3.3 Notifications (optionnel)

Les notifications desktop utilisent `notify-send`. Teste :

```bash
./notifications/notification_demo.sh
```

Tu peux désactiver les notifications en posant `NOTIFICATION_ENABLE_DESKTOP=false`
dans ton environnement.

---

## 4. Premier workflow

### 4.1 Analyse « à blanc » (lecture seule)

Rien n'est modifié, c'est pour se familiariser :

```bash
# Statistiques iTunes (si tu as un iTunes Music Library.xml)
python3 itunes/itunes_analyzer.py --stats

# Aperçu des ratings Plex
./ratings/plex_ratings_helper.sh
```

### 4.2 Synchronisation des ratings Plex → fichiers audio (ID3/POPM)

Objectif : écrire tes notes Plex (0–5 étoiles) dans les tags des fichiers audio,
pour qu'elles survivent à une réinstallation.

```bash
# Dry-run : affiche ce qui serait écrit
python3 ratings/plex_rating_sync_complete.py --dry-run

# Vraie exécution
python3 ratings/plex_rating_sync_complete.py
```

Les tags écrits sont des frames `POPM` (standard ID3v2) compatibles avec
Winamp, foobar2000, MediaMonkey, Rhythmbox, etc.

### 4.3 Workflow quotidien 1⭐/2⭐

Ce script traite automatiquement :

- **1⭐** → suppression du fichier (jugé inaudible/à jeter)
- **2⭐** → mise en queue pour [SongRec](https://github.com/marin-m/SongRec) (réidentification via empreinte acoustique)

```bash
./workflows/plex_daily_workflow.sh
```

Les logs arrivent dans `~/.plex/logs/plex_daily/daily_sync_YYYYmmdd_HHMMSS.log`.

---

## 5. Notes, playlists et synchronisation

### 5.1 Exporter une playlist Plex en M3U

```bash
python3 playlists/export_plex_playlist.py --list
python3 playlists/export_plex_playlist.py --name "Ma Playlist" --out ./ma_playlist.m3u
```

### 5.2 Synchroniser M3U ↔ Plex

```bash
python3 playlists/sync_m3u_playlists.py /chemin/vers/dossier/m3u/
```

### 5.3 Générer des playlists intelligentes (Plexamp)

```bash
python3 playlists/auto_playlists_plexamp.py --help
./playlists/generate_plexamp_playlists.sh
```

### 5.4 Copier les playlists vers MyBook (disque externe)

```bash
./playlists/sync_plex_playlists_to_mybook.sh
```

---

## 6. Automatisation systemd

Après `install.sh`, les units sont déjà dans `~/.config/systemd/user/`
**et activées**. Pour réinstaller manuellement :

```bash
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
```

### 6.1 Timers disponibles

Pour le comportement actuel du cron Docker (automatisation horaire en journée), voir [AUTO_README.md](AUTO_README.md).

| Timer | Fréquence | Rôle |
|---|---|---|
| `plex-ratings-sync.timer` | Tous les jours 22h | Sync ratings Plex → ID3 |
| `plex-daily-workflow.timer` | Tous les jours 02h | Workflow 1⭐/2⭐ |
| `plex-auto-playlists.timer` | Tous les jours 23h | Régénère les playlists Plexamp |
| `plex-export-playlists.timer` | Quotidien | Exporte les playlists en M3U |
| `plex-playlists-mybook-sync.timer` | Après export | Copie vers MyBook |

### 6.2 Activer un timer

```bash
systemctl --user enable --now plex-ratings-sync.timer
systemctl --user list-timers
```

### 6.3 Suivre l'exécution

```bash
# Statut d'un service
systemctl --user status plex-ratings-sync.service

# Logs temps réel
journalctl --user -u plex-ratings-sync.service -f

# Forcer une exécution immédiate
systemctl --user start plex-ratings-sync.service
```

### 6.4 Garder les timers actifs quand tu es déconnecté

```bash
sudo loginctl enable-linger "$USER"
```

---

## 7. Dépannage

### Le venv ne s'active pas

```bash
rm -rf .venv
./install.sh
```

### « Module 'mutagen' requis »

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### La base Plex est « locked »

Plex verrouille sa DB pendant qu'il tourne. Les scripts travaillent sur une
**copie temporaire** — c'est normal et attendu. Si tu vois l'erreur quand
même :

```bash
# Stoppe Plex le temps d'un gros sync
sudo systemctl stop plexmediaserver
# ... lance ton script ...
sudo systemctl start plexmediaserver
```

### `notify-send: command not found`

```bash
sudo apt install libnotify-bin
```

### Un timer systemd ne se déclenche pas

```bash
systemctl --user list-timers --all
journalctl --user -u NOM.service -n 100
# Vérifier le linger pour les timers quand déconnecté
loginctl show-user "$USER" | grep Linger
```

### Restaurer une sauvegarde iTunes XML

```bash
ls -lt iTunes\ Music\ Library.xml.backup_*
cp "iTunes Music Library.xml.backup_YYYYMMDD_HHMMSS" "iTunes Music Library.xml"
```

---

## 🧭 Pour aller plus loin

- [README.md](README.md) — Vue d'ensemble du projet
- [GUIDE_COMPLET.md](GUIDE_COMPLET.md) — Guide détaillé iTunes XML
- [notifications/audio_notifications_README.md](notifications/audio_notifications_README.md) — Système de notifications
- [data/itunes_match_report.md](data/itunes_match_report.md) — Alternatives à iTunes Match sous Linux

Bon tri musical ! 🎶
