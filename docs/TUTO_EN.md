# 🎓 Step-by-step tutorial — Plex / iTunes / Audio scripts suite

This tutorial walks you from installation to automated production.
Expect **15–30 minutes** to get everything up and running.

> 💡 In a hurry? Run `./install_en.sh` then jump straight to [§ 4 — First workflow](#4-first-workflow).

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Automatic install](#2-automatic-install)
3. [Configuration](#3-configuration)
4. [First workflow](#4-first-workflow)
5. [Ratings, playlists and sync](#5-ratings-playlists-and-sync)
6. [systemd automation](#6-systemd-automation)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Prerequisites

- Linux (tested on **Linux Mint / Ubuntu / Debian**)
- A local **Plex Media Server** with at least one music library
- A locally accessible music collection (e.g. `/home/<user>/Music` or `/mnt/MyBook/Music`)
- `sudo` privileges for the initial package install

Quick check:

```bash
python3 --version          # >= 3.10 recommended
sqlite3 --version
```

---

## 2. Automatic install

From the project root:

```bash
cd ~/scripts
./install_en.sh
```

The script will:

1. Detect and install missing apt packages (`python3-venv`, `sqlite3`, `ffmpeg`, `libnotify-bin`, `jq`…)
2. Create the virtualenv [.venv/](.venv/) and install [requirements.txt](requirements.txt)
3. Make all `.sh` / `.py` files executable
4. Create working directories (`~/.plex/logs`, `~/songrec_queue`…)
5. Install and **enable** all user systemd timers
6. Enable `loginctl` lingering (optional sudo step)

Options:

```bash
./install_en.sh --interactive   # Ask before each step
./install_en.sh --no-apt        # Skip apt
./install_en.sh --no-systemd    # Skip systemd
./install_en.sh --no-linger     # Skip lingering
```

Then activate the venv in your shell:

```bash
source .venv/bin/activate
```

---

## 3. Configuration

### 3.1 Locate the Plex database

Most scripts read Plex's SQLite database directly. Typical locations:

| Plex install | Database path |
|---|---|
| `.deb` package | `/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db` |
| Snap | `~/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db` |
| Flatpak | `~/.var/app/tv.plex.PlexMediaServer/data/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db` |

Export the variable for your session:

```bash
export PLEX_DB="/path/to/com.plexapp.plugins.library.db"
```

💡 Add that line to `~/.bashrc` or `~/.zshrc` to make it permanent.

### 3.2 Audio library

Check / adjust `AUDIO_LIBRARY` at the top of the main scripts if your music
folder is not `/home/<user>/Music`:

- [workflows/plex_daily_workflow.sh](workflows/plex_daily_workflow.sh)
- [ratings/plex_daily_ratings_sync.sh](ratings/plex_daily_ratings_sync.sh)

### 3.3 Notifications (optional)

Desktop notifications use `notify-send`. Test:

```bash
./notifications/notification_demo.sh
```

Disable them by setting `NOTIFICATION_ENABLE_DESKTOP=false` in your env.

---

## 4. First workflow

### 4.1 Read-only analysis

Nothing is modified, this is just to get familiar:

```bash
# iTunes stats (if you have an iTunes Music Library.xml)
python3 itunes/itunes_analyzer.py --stats

# Plex ratings overview
./ratings/plex_ratings_helper.sh
```

### 4.2 Sync Plex ratings → audio tags (ID3/POPM)

Goal: write your Plex star ratings (0–5) into audio file tags so they survive
a reinstall.

```bash
# Dry-run: preview changes
python3 ratings/plex_rating_sync_complete.py --dry-run

# Actual run
python3 ratings/plex_rating_sync_complete.py
```

Written tags are standard ID3v2 `POPM` frames, compatible with Winamp,
foobar2000, MediaMonkey, Rhythmbox, etc.

### 4.3 Daily 1★ / 2★ workflow

This pipeline automatically:

- **1★** → deletes the file (deemed unusable)
- **2★** → queues for [SongRec](https://github.com/marin-m/SongRec) fingerprint re-identification

```bash
./workflows/plex_daily_workflow.sh
```

Logs are written to `~/.plex/logs/plex_daily/daily_sync_YYYYmmdd_HHMMSS.log`.

---

## 5. Ratings, playlists and sync

### 5.1 Export a Plex playlist to M3U

```bash
python3 playlists/export_plex_playlist.py --list
python3 playlists/export_plex_playlist.py --name "My Playlist" --out ./my_playlist.m3u
```

### 5.2 M3U ↔ Plex sync

```bash
python3 playlists/sync_m3u_playlists.py /path/to/m3u/folder/
```

### 5.3 Smart Plexamp playlists

```bash
python3 playlists/auto_playlists_plexamp.py --help
./playlists/generate_plexamp_playlists.sh
```

### 5.4 Copy playlists to MyBook (external drive)

```bash
./playlists/sync_plex_playlists_to_mybook.sh
```

---

## 6. systemd automation

If you accepted the systemd step during `install_en.sh`, units are already in
`~/.config/systemd/user/` **and enabled**. Otherwise:

```bash
cp systemd/*.service systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
```

### 6.1 Available timers

For the current Docker cron behavior (hourly daytime automation), see [AUTO_README.md](AUTO_README.md).

| Timer | Schedule | Role |
|---|---|---|
| `plex-ratings-sync.timer` | Daily 22:00 | Sync Plex ratings → ID3 |
| `plex-daily-workflow.timer` | Daily 02:00 | 1★/2★ workflow |
| `plex-auto-playlists.timer` | Daily 23:00 | Generate Plexamp playlists |
| `plex-export-playlists.timer` | Daily | Export M3U |
| `plex-playlists-mybook-sync.timer` | After export | Copy to MyBook |

### 6.2 Enable a timer manually

```bash
systemctl --user enable --now plex-ratings-sync.timer
systemctl --user list-timers
```

### 6.3 Monitor execution

```bash
# Service status
systemctl --user status plex-ratings-sync.service

# Live logs
journalctl --user -u plex-ratings-sync.service -f

# Force immediate run
systemctl --user start plex-ratings-sync.service
```

### 6.4 Keep timers running when logged out

```bash
sudo loginctl enable-linger "$USER"
```

The installer already does this unless you pass `--no-linger`.

---

## 7. Troubleshooting

### venv won't activate

```bash
rm -rf .venv
./install_en.sh
```

### "Module 'mutagen' required"

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### Plex DB is "locked"

Plex locks its DB while running. Scripts work on a **temporary copy** — this
is expected. If you still see the error:

```bash
sudo systemctl stop plexmediaserver
# ... run your script ...
sudo systemctl start plexmediaserver
```

### `notify-send: command not found`

```bash
sudo apt install libnotify-bin
```

### A systemd timer doesn't fire

```bash
systemctl --user list-timers --all
journalctl --user -u NAME.service -n 100
loginctl show-user "$USER" | grep Linger
```

### Restore an iTunes XML backup

```bash
ls -lt iTunes\ Music\ Library.xml.backup_*
cp "iTunes Music Library.xml.backup_YYYYMMDD_HHMMSS" "iTunes Music Library.xml"
```

---

## 🧭 Further reading

- [README_EN.md](README_EN.md) — Project overview
- [GUIDE_COMPLET.md](GUIDE_COMPLET.md) — Detailed iTunes XML guide (French)
- [notifications/audio_notifications_README.md](notifications/audio_notifications_README.md) — Notification system

Happy music sorting! 🎶
