# 🎵 scripts — Plex / iTunes / Audio automation suite

Python & Bash scripts to manage a **Plex Media Server** music library
(+ legacy iTunes XML) on Linux: ratings, playlists, cleanup, SongRec
identification, external-drive sync — all orchestrated by **systemd timers**.

> 📖 **Quick start**: [TUTO_EN.md](TUTO_EN.md)
> 🇫🇷 **Version française**: [README.md](README.md) — [TUTO.md](TUTO.md)

---

## 🚀 Quick install

```bash
git clone <your-repo> scripts && cd scripts
./install_en.sh
source .venv/bin/activate
```

[install_en.sh](install_en.sh) detects your OS, installs required apt packages
(`python3-venv`, `sqlite3`, `ffmpeg`, `libnotify-bin`, `jq`…), creates the
virtualenv, installs [requirements.txt](requirements.txt), makes scripts
executable, and **installs + enables** all systemd user timers.

Options:

```bash
./install_en.sh --interactive   # Ask before each step
./install_en.sh --no-apt        # Skip apt
./install_en.sh --no-systemd    # Skip systemd
./install_en.sh --no-linger     # Skip lingering
./install_en.sh --help
```

---

## 🗂️ Repository layout

```
scripts/
├── install.sh / install_en.sh  # Full project setup (FR / EN)
├── README.md / README_EN.md    # Project overview (FR / EN)
├── TUTO.md / TUTO_EN.md        # Step-by-step tutorial (FR / EN)
├── GUIDE_COMPLET.md            # Detailed iTunes XML guide (FR)
├── requirements.txt            # Python deps (mutagen, plexapi, requests)
│
├── audio/                      # Audio sorting, filtering, renaming
├── data/                       # Reports, exports, JSON dumps
├── diagnostics/                # Read-only Plex DB inspections
├── itunes/                     # iTunes XML library manipulation
├── maintenance/                # Log rotation, systemd reload
├── notifications/              # notify-send, desktop presets
├── playlists/                  # Export/import, M3U, Plexamp, MyBook sync
├── ratings/                    # Plex ratings ↔ ID3 (POPM) sync
├── systemd/                    # .service + .timer units (--user)
├── utils/                      # Misc helpers
└── workflows/                  # High-level multi-step pipelines
```

---

## 🧩 Main components

### ⭐ Plex ratings ↔ ID3 (POPM)

| Script | Purpose |
|---|---|
| [ratings/plex_rating_sync_complete.py](ratings/plex_rating_sync_complete.py) | Full sync Plex → files (MP3/MP4/FLAC/Ogg) |
| [ratings/plex_ratings_sync.py](ratings/plex_ratings_sync.py) | Incremental sync |
| [ratings/sync_ratings_to_id3.py](ratings/sync_ratings_to_id3.py) | ID3-only export |
| [ratings/plex_daily_ratings_sync.sh](ratings/plex_daily_ratings_sync.sh) | Daily wrapper (systemd) |
| [ratings/plex_ratings_helper.sh](ratings/plex_ratings_helper.sh) | Interactive overview |

### 🎶 1★ / 2★ workflows

| Script | Purpose |
|---|---|
| [workflows/plex_daily_workflow.sh](workflows/plex_daily_workflow.sh) | Full pipeline: 1★ → delete, 2★ → SongRec queue |
| [audio/auto_cleanup_2_stars.sh](audio/auto_cleanup_2_stars.sh) | Process completed SongRec queues |
| [audio/rename_1_star_with_songrec.py](audio/rename_1_star_with_songrec.py) | Acoustic re-identification |
| [audio/find_1_star_audios.py](audio/find_1_star_audios.py) | List 1★ tracks in DB |
| [audio/filter_audio_by_genre.py](audio/filter_audio_by_genre.py) | Filter by ID3 genre |

### 📝 Playlists

| Script | Purpose |
|---|---|
| [playlists/export_plex_playlist.py](playlists/export_plex_playlist.py) | Plex → M3U export |
| [playlists/sync_m3u_playlists.py](playlists/sync_m3u_playlists.py) | Bidirectional M3U sync |
| [playlists/auto_playlists_plexamp.py](playlists/auto_playlists_plexamp.py) | Smart generation (Plexamp) |
| [playlists/import_itunes_playlists_to_plex.py](playlists/import_itunes_playlists_to_plex.py) | Import from iTunes XML |
| [playlists/sync_plex_playlists_to_mybook.sh](playlists/sync_plex_playlists_to_mybook.sh) | Copy to MyBook drive |

### 💿 iTunes XML

| Script | Purpose |
|---|---|
| [itunes/itunes_complete_manager.py](itunes/itunes_complete_manager.py) | Interactive menu UI |
| [itunes/itunes_analyzer.py](itunes/itunes_analyzer.py) | Stats, formats, genres, paths |
| [itunes/itunes_path_updater.py](itunes/itunes_path_updater.py) | Fix Windows → Linux paths |
| [itunes/itunes_editor.py](itunes/itunes_editor.py) | Bulk edits (regex, replace) |

See [GUIDE_COMPLET.md](GUIDE_COMPLET.md) for details (French).

### 🔍 Diagnostics

Read-only scripts to audit Plex's DB: schema, item presence, ratings
structure, etc. — see the [diagnostics](diagnostics) folder.

### 🔔 Notifications

[notifications/audio_notifications.sh](notifications/audio_notifications.sh) — unified system (desktop + console + sound)
used by workflows. Configurable via env variables (`NOTIFICATION_APP_NAME`,
`NOTIFICATION_ENABLE_DESKTOP`, …). See
[notifications/audio_notifications_README.md](notifications/audio_notifications_README.md).

---

## ⏰ Automation (systemd --user)

Units in [systemd/](systemd/) are copied to `~/.config/systemd/user/` and
**enabled** by `install_en.sh`.

Runtime automation is systemd-only. Docker is kept for UI/configuration and optional one-shot runs.
See [AUTO_README.md](AUTO_README.md) for the current policy.

| Timer | Schedule | Service |
|---|---|---|
| `plex-ratings-sync.timer` | Daily 22:00 | Plex ratings → ID3 |
| `plex-daily-workflow.timer` | Daily 02:00 | 1★/2★ workflow |
| `plex-auto-playlists.timer` | Daily 23:00 | Plexamp playlists |
| `plex-export-playlists.timer` | Daily | M3U export |
| `plex-playlists-mybook-sync.timer` | After export | MyBook copy |

Manual management:

```bash
systemctl --user list-timers
systemctl --user status plex-ratings-sync.timer
journalctl --user -u plex-ratings-sync.service -f
sudo loginctl enable-linger "$USER"   # stay active when logged out
```

---

## 📦 Dependencies

**System** (apt): `python3 python3-venv sqlite3 ffmpeg libnotify-bin jq curl rsync git`
+ [songrec](https://github.com/marin-m/SongRec) (optional, 2★ workflow).

**Python** (see [requirements.txt](requirements.txt)): `mutagen`, `plexapi`, `requests`.

---

## ⚙️ Configuration

Adjust once in your shell rc file:

```bash
export PLEX_DB="$HOME/.var/app/tv.plex.PlexMediaServer/data/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
export AUDIO_LIBRARY="$HOME/Music"
```

See [TUTO_EN.md § 3](TUTO_EN.md#3-configuration) for all possible paths.

---

## 🛟 Safety & best practices

- **No direct writes** to Plex DB: scripts operate on a temporary copy.
- **Automatic backups** before any file modification (`.backup_YYYYMMDD_HHMMSS`).
- **`--dry-run` mode** on mutating scripts — **always use it first**.
- **Centralized logs** in `~/.plex/logs/` and `~/logs/plex_ratings/`.

---

## 🆘 Troubleshooting

See [TUTO_EN.md § 7](TUTO_EN.md#7-troubleshooting).

```bash
systemctl --user status plex-ratings-sync.timer
journalctl --user -u plex-ratings-sync.service -n 200
./install_en.sh      # re-run idempotently
```

---

## 📄 License

Personal use. Adapt paths and configs to your environment.
