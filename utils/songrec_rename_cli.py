#!/usr/bin/env python3
"""
Renomme les fichiers audio via reconnaissance SongRec (Shazam).

Usage:
    songrec-rename [-r] [-i] [-m RACINE] [-s] [-n] [chemin]

Options:
    -r / --recursive         Traiter les sous-dossiers récursivement
    -i / --id3               Écrire les tags ID3/Vorbis/M4A (titre, artiste, album, année)
    -m / --move RACINE       Déplacer vers structure Lidarr sous RACINE (ex: -m /mnt/Music)
                             Format : Artiste/Album (Année)/Artiste - Album - NN - Titre.ext
                             (NN = n° de piste depuis les tags, ou 00 si inconnu)
                             Si SongRec échoue, utilise les tags ID3 existants.
    -s / --skip-organized    Ignorer les fichiers déjà dans une structure Artiste/Album/
                             (utile pour ne traiter que les orphelins dans une grande bibliothèque)
    -n / --dry-run           Simuler sans renommer, écrire les tags ni déplacer
    chemin                   Fichier ou dossier cible (défaut : répertoire courant)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

AUDIO_EXTENSIONS = {
    '.mp3', '.flac', '.wav', '.aif', '.aiff', '.ogg',
    '.m4a', '.dsf', '.wv', '.ape', '.opus', '.alac',
}
REQUEST_DELAY_OK = 1.0    # délai après reconnaissance réussie
REQUEST_DELAY_FAIL = 4.0  # délai après échec SongRec (probable rate-limit Shazam)

# Tokens qui signalent des tags placeholder/génériques à ignorer
_BOGUS_TOKENS = {'-artiste-', '-titre-', 'musique', 'unknown album', 'unknown artist', 'inconnu'}


def _is_bogus_tag(value: str) -> bool:
    lower = value.lower()
    return any(token in lower for token in _BOGUS_TOKENS) or lower in {'00', ''}


def sanitize(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def read_existing_tags(filepath):
    """Lit les tags ID3/Vorbis existants. Retourne un dict ou None."""
    try:
        import mutagen
        audio = mutagen.File(str(filepath), easy=True)
        if audio is None:
            return None
        tags = audio.tags
        if not tags:
            return None
        title = str(tags.get('title', [None])[0] or '').strip()
        artist = str(tags.get('artist', [None])[0] or '').strip()
        album = str(tags.get('album', [None])[0] or '').strip()
        date = str(tags.get('date', [None])[0] or tags.get('year', [None])[0] or '').strip()
        year = date[:4] if len(date) >= 4 else date
        tracknumber = str(tags.get('tracknumber', [None])[0] or '').strip()
        # "5/12" → "5"
        track = tracknumber.split('/')[0].strip() if tracknumber else ''
        try:
            track = f"{int(track):02d}" if track else ''
        except ValueError:
            track = ''
        if title and artist and not _is_bogus_tag(title) and not _is_bogus_tag(artist):
            return {
                'title': title,
                'artist': artist,
                'album': album if not _is_bogus_tag(album) else '',
                'year': year,
                'track': track,
            }
        return None
    except Exception:
        return None


def track_from_filename(filepath):
    """Extrait le numéro de piste depuis le début du nom de fichier (ex: '01 - Title' → '01')."""
    m = re.match(r'^(\d{1,3})\b', filepath.stem)
    if m:
        try:
            return f"{int(m.group(1)):02d}"
        except ValueError:
            pass
    return ''


def is_organized(filepath, root):
    """
    Retourne True si le fichier est déjà dans une structure Artiste/Album/fichier
    à l'intérieur de root (au moins 2 niveaux de dossier).
    """
    try:
        rel = filepath.relative_to(root)
        return len(rel.parts) >= 3  # Artiste / Album / fichier
    except ValueError:
        return False


def recognize(filepath):
    """Appelle SongRec. Retourne un dict de métadonnées ou None."""
    try:
        result = subprocess.run(
            ['songrec', 'audio-file-to-recognized-song', str(filepath)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        track = data.get('track', {})
        title = track.get('title', '').strip()
        artist = track.get('subtitle', '').strip()
        if not title or not artist:
            return None

        info = {'title': title, 'artist': artist, 'album': '', 'year': '', 'genre': ''}

        # Genre depuis track.genres.primary (Shazam API)
        genres = track.get('genres', {})
        if isinstance(genres, dict):
            info['genre'] = genres.get('primary', '').strip()

        for section in track.get('sections', []):
            for meta in section.get('metadata', []):
                key = meta.get('title', '').lower()
                val = meta.get('text', '').strip()
                if key == 'album' and not info['album']:
                    info['album'] = val
                elif key == 'released' and not info['year']:
                    info['year'] = val[:4] if len(val) >= 4 else val

        return info
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, OSError):
        return None


def recognize_acoustid(filepath):
    """Fallback AcoustID (Chromaprint + MusicBrainz). Retourne un dict ou None."""
    api_key = os.environ.get('ACOUSTID_API_KEY', '').strip()
    if not api_key:
        return None
    try:
        import acoustid
        result = acoustid.match(
            api_key, str(filepath),
            meta=['recordings', 'releases', 'releasegroups'],
            parse=False,
        )
        if result.get('status') != 'ok':
            return None
        for hit in result.get('results', []):
            if hit.get('score', 0) < 0.5:
                continue
            for rec in hit.get('recordings', []):
                title = rec.get('title', '').strip()
                artists = rec.get('artists', [])
                artist = ', '.join(a.get('name', '') for a in artists).strip()
                if not title or not artist:
                    continue
                album = ''
                year = ''
                for rg in rec.get('releasegroups', []):
                    if not album:
                        album = rg.get('title', '').strip()
                    for rel in rg.get('releases', []):
                        d = rel.get('date', {})
                        if not year and d.get('year'):
                            year = str(d['year'])
                return {'title': title, 'artist': artist, 'album': album, 'year': year, 'genre': ''}
        return None
    except Exception:
        return None


def write_tags(filepath, info, dry_run):
    """Écrit les tags ID3/Vorbis/M4A via mutagen. Retourne True si OK."""
    ext = filepath.suffix.lower()

    if dry_run:
        parts = [f"{info['artist']} / {info['title']}"]
        if info.get('album'):
            parts.append(f"album : {info['album']}")
        if info.get('year'):
            parts.append(f"année : {info['year']}")
        if info.get('genre'):
            parts.append(f"genre : {info['genre']}")
        print(f"  [dry-run] tags : {', '.join(parts)}")
        return True

    try:
        import mutagen

        if ext == '.mp3':
            from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TCON, ID3NoHeaderError
            try:
                tags = ID3(str(filepath))
            except ID3NoHeaderError:
                tags = ID3()
            tags.add(TIT2(encoding=3, text=info['title']))
            tags.add(TPE1(encoding=3, text=info['artist']))
            if info.get('album'):
                tags.add(TALB(encoding=3, text=info['album']))
            if info.get('year'):
                tags.add(TDRC(encoding=3, text=info['year']))
            if info.get('genre'):
                tags.add(TCON(encoding=3, text=info['genre']))
            tags.save(str(filepath))

        elif ext == '.flac':
            from mutagen.flac import FLAC
            audio = FLAC(str(filepath))
            audio['title'] = info['title']
            audio['artist'] = info['artist']
            if info.get('album'):
                audio['album'] = info['album']
            if info.get('year'):
                audio['date'] = info['year']
            if info.get('genre'):
                audio['genre'] = info['genre']
            audio.save()

        elif ext in ('.ogg', '.opus'):
            audio = mutagen.File(str(filepath))
            if audio is None:
                print("  ⚠ format non supporté pour les tags")
                return False
            audio['title'] = info['title']
            audio['artist'] = info['artist']
            if info.get('album'):
                audio['album'] = info['album']
            if info.get('year'):
                audio['date'] = info['year']
            if info.get('genre'):
                audio['genre'] = info['genre']
            audio.save()

        elif ext == '.m4a':
            from mutagen.mp4 import MP4
            audio = MP4(str(filepath))
            audio['\xa9nam'] = info['title']
            audio['\xa9ART'] = info['artist']
            if info.get('album'):
                audio['\xa9alb'] = info['album']
            if info.get('year'):
                audio['\xa9day'] = info['year']
            if info.get('genre'):
                audio['\xa9gen'] = info['genre']
            audio.save()

        else:
            audio = mutagen.File(str(filepath))
            if audio is None:
                print("  ⚠ format non supporté pour les tags")
                return False
            if audio.tags is None:
                audio.add_tags()
            audio['title'] = info['title']
            audio['artist'] = info['artist']
            if info.get('album'):
                audio['album'] = info['album']
            if info.get('genre'):
                audio['genre'] = info['genre']
            audio.save()

        parts = [f"{info['artist']} — {info['title']}"]
        if info.get('album'):
            parts.append(f"album : {info['album']}")
        if info.get('year'):
            parts.append(f"année : {info['year']}")
        if info.get('genre'):
            parts.append(f"genre : {info['genre']}")
        print(f"  ♬ {', '.join(parts)}")
        return True

    except ImportError:
        print("  ⚠ mutagen non installé (pip install mutagen)")
        return False
    except Exception as e:
        print(f"  ⚠ erreur tags : {e}")
        return False


def move_to_lidarr(filepath, info, root, dry_run):
    """
    Déplace le fichier vers structure Lidarr :
      {root}/{Artiste}/{Album} ({Année})/{Artiste} - {Album} - NN - {Titre}.ext
    Si album absent : {root}/{Artiste}/Singles/{Artiste} - NN - {Titre}.ext
    NN = numéro de piste depuis les tags, puis filename, puis 00.
    Retourne le nouveau Path ou None si échec/skipped.
    """
    artist = sanitize(info['artist'])
    title = sanitize(info['title'])
    album = sanitize(info.get('album', ''))
    year = info.get('year', '')
    ext = filepath.suffix.lower()

    # Numéro de piste : tags > filename > 00
    track = info.get('track', '') or track_from_filename(filepath) or '00'

    if album:
        album_folder = f"{album} ({year})" if year else album
        filename = f"{artist} - {album} - {track} - {title}{ext}"
        dest_dir = Path(root) / artist / album_folder
    else:
        filename = f"{artist} - {track} - {title}{ext}"
        dest_dir = Path(root) / artist / "Singles"

    dest = dest_dir / filename

    if dest == filepath:
        print("  = déjà à la bonne place")
        return None

    if dest.exists():
        print(f"  ⚠ conflit : {dest} existe déjà, déplacement ignoré")
        return None

    print(f"  → {dest}")

    if dry_run:
        print("  [dry-run, pas de déplacement]")
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(filepath), str(dest))

    # Supprimer le dossier source s'il est vide
    try:
        parent = filepath.parent
        while parent != Path(root) and parent != parent.parent:
            if not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            else:
                break
    except Exception:
        pass

    return dest


def collect_files(path, recursive):
    files = []
    if path.is_file():
        if path.suffix.lower() in AUDIO_EXTENSIONS:
            files.append(path)
    elif path.is_dir():
        pattern = '**/*' if recursive else '*'
        for f in sorted(path.glob(pattern)):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                files.append(f)
    return files


def do_rename(filepath, info, dry_run):
    """Renomme le fichier en 'Artiste - Titre.ext'. Retourne (new_path, was_renamed)."""
    new_name = f"{sanitize(info['artist'])} - {sanitize(info['title'])}{filepath.suffix.lower()}"
    new_path = filepath.parent / new_name

    if new_path == filepath:
        print("  = nom déjà correct")
        return filepath, False

    if new_path.exists():
        print(f"  ⚠ conflit : {new_name} existe déjà, ignoré")
        return filepath, False

    print(f"  {filepath.name}\n  → {new_name}")

    if dry_run:
        print("  [dry-run, pas de renommage]")
        return filepath, False

    filepath.rename(new_path)
    return new_path, True


def main():
    parser = argparse.ArgumentParser(
        description="Renomme et tague les fichiers audio via SongRec (Shazam)."
    )
    parser.add_argument('path', nargs='?', default='.',
                        help="Fichier ou dossier à traiter (défaut : .)")
    parser.add_argument('-r', '--recursive', action='store_true',
                        help="Traiter les sous-dossiers récursivement")
    parser.add_argument('-i', '--id3', action='store_true', dest='tag',
                        help="Écrire les tags ID3/Vorbis/M4A après reconnaissance")
    parser.add_argument('-m', '--move', metavar='RACINE',
                        help="Déplacer vers structure Lidarr sous RACINE (ex: /mnt/Music)")
    parser.add_argument('-s', '--skip-organized', action='store_true',
                        help="Ignorer les fichiers déjà dans Artiste/Album/ (nécessite -m)")
    parser.add_argument('-n', '--dry-run', action='store_true',
                        help="Simuler sans renommer, écrire les tags ni déplacer")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"Erreur : chemin introuvable : {path}", file=sys.stderr)
        sys.exit(1)

    if args.move and not Path(args.move).is_dir():
        print(f"Erreur : racine introuvable : {args.move}", file=sys.stderr)
        sys.exit(1)

    files = collect_files(path, args.recursive)
    if not files:
        print("Aucun fichier audio trouvé.")
        sys.exit(0)

    total = len(files)
    mode_parts = []
    if args.dry_run:
        mode_parts.append("dry-run")
    if args.tag:
        mode_parts.append("tags ID3")
    if args.move:
        mode_parts.append(f"→ Lidarr:{args.move}")
    print(f"{total} fichier(s) audio trouvé(s)." + (f"  [{', '.join(mode_parts)}]" if mode_parts else ""))
    print()

    renamed = tagged = moved = skipped = failed = 0

    for i, filepath in enumerate(files, 1):
        print(f"[{i}/{total}] {filepath.name}")
        if args.skip_organized and args.move and is_organized(filepath, args.move):
            print("  ↷ déjà organisé, ignoré")
            skipped += 1
            if i < total:
                time.sleep(REQUEST_DELAY)
            continue

        info = recognize(filepath)
        source = "SongRec"
        songrec_ok = info is not None

        if info is None:
            # Fallback 1 : AcoustID / MusicBrainz
            info = recognize_acoustid(filepath)
            if info:
                source = "AcoustID"

        if info is None:
            # Fallback 2 : tags existants (non-bogus)
            info = read_existing_tags(filepath)
            if info:
                source = "tags existants"
        else:
            # Compléter le numéro de piste depuis les tags existants si SongRec ne le donne pas
            if not info.get('track'):
                existing = read_existing_tags(filepath)
                if existing and existing.get('track'):
                    info['track'] = existing['track']

        if info is None:
            print("  ✗ non reconnu (ni SongRec ni tags)")
            failed += 1
        else:
            album_str = f" ({info['album']})" if info.get('album') else ""
            year_str = f" [{info['year']}]" if info.get('year') else ""
            print(f"  ♪ {info['artist']} — {info['title']}{album_str}{year_str}  [{source}]")

            if args.move:
                result = move_to_lidarr(filepath, info, args.move, args.dry_run)
                if result:
                    moved += 1
                    filepath = result
                else:
                    skipped += 1
            else:
                new_path, was_renamed = do_rename(filepath, info, args.dry_run)
                if was_renamed:
                    renamed += 1
                    filepath = new_path
                else:
                    skipped += 1

            if args.tag:
                if write_tags(filepath, info, args.dry_run):
                    tagged += 1

        if i < total:
            time.sleep(REQUEST_DELAY_OK if songrec_ok else REQUEST_DELAY_FAIL)

    summary_parts = []
    if args.move:
        summary_parts.append(f"{moved} déplacé(s)")
    else:
        summary_parts.append(f"{renamed} renommé(s)")
    if args.tag:
        summary_parts.append(f"{tagged} tag(s) écrits")
    summary_parts += [f"{skipped} ignoré(s)", f"{failed} non reconnu(s)"]
    print(f"\nTerminé : {', '.join(summary_parts)}.")


if __name__ == '__main__':
    main()
