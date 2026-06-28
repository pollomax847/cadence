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


def _cover_to_bytes(cover_path):
    """Lit une image de pochette depuis un chemin. Retourne (bytes, mime) ou (None, None)."""
    if not cover_path:
        return None, None
    p = Path(cover_path)
    if not p.exists():
        return None, None
    ext = p.suffix.lower()
    mime = 'image/png' if ext == '.png' else 'image/jpeg'
    return p.read_bytes(), mime


def _mp3_write(filepath, td):
    """Écrit tous les champs OpenTagger dans un MP3 via ID3."""
    from mutagen.id3 import (
        ID3, ID3NoHeaderError,
        TIT2, TIT1, TIT3,
        TPE1, TPE2, TPE3, TPE4,
        TALB, TDRC, TCON, TRCK, TPOS,
        TCOM, TBPM, TKEY, TLAN, TSRC,
        TSOP, TSO2, TSOA, TSOT,
        TXXX, APIC, USLT, COMM, UFID,
        WOAS, WOAR, WORS,
    )

    try:
        tags = ID3(str(filepath))
    except ID3NoHeaderError:
        tags = ID3()

    if td.get('clearExisting'):
        tags.clear()

    def t(cls, val):
        if val:
            k = cls.__name__
            tags.delall(k)
            tags.add(cls(encoding=3, text=val))

    def txxx(desc, val):
        if val:
            tags.delall(f'TXXX:{desc}')
            tags.add(TXXX(encoding=3, desc=desc, text=val))

    def url(cls, val):
        if val:
            tags.delall(cls.__name__)
            tags.add(cls(url=val))

    # Champs texte standards
    t(TIT2, td.get('title'))
    t(TPE1, td.get('artist'))
    t(TPE2, td.get('albumArtist'))
    t(TALB, td.get('album'))
    t(TDRC, td.get('year'))
    t(TCON, td.get('genre'))
    t(TCOM, td.get('composer'))
    t(TBPM, td.get('bpm'))
    t(TKEY, td.get('initialKey'))
    t(TLAN, td.get('language'))
    t(TSRC, td.get('isrc'))
    t(TSOP, td.get('artistSort'))
    t(TSO2, td.get('albumArtistSort'))
    t(TSOA, td.get('albumSort'))
    t(TSOT, td.get('titleSort'))
    t(TIT1, td.get('grouping'))
    t(TPE3, td.get('conductor'))

    # Piste et disque  (format "N/Total")
    trk = td.get('track', '')
    trt = td.get('trackTotal', '')
    if trk:
        t(TRCK, f"{trk}/{trt}" if trt else trk)
    dsk = td.get('discNo', '')
    dst = td.get('discTotal', '')
    if dsk:
        t(TPOS, f"{dsk}/{dst}" if dst else dsk)

    # Paroles
    if td.get('lyrics'):
        tags.delall('USLT')
        tags.add(USLT(encoding=3, lang='   ', desc='', text=td['lyrics']))

    # Commentaire
    if td.get('comment'):
        tags.delall('COMM')
        tags.add(COMM(encoding=3, lang='   ', desc='', text=td['comment']))

    # MB Recording ID via UFID (standard Picard)
    if td.get('recordingMbid'):
        tags.delall('UFID')
        tags.add(UFID(owner='http://musicbrainz.org',
                      data=td['recordingMbid'].encode()))

    # URLs
    url(WOAR, td.get('artistOfficialUrl'))
    url(WORS, td.get('releaseDiscogsUrl'))
    if td.get('artistWikipediaUrl'):
        txxx('Wikipedia Artist Page', td['artistWikipediaUrl'])

    # TXXX — champs étendus (MusicBrainz, mood, credits, etc.)
    for desc, key in (
        ('MusicBrainz Track Id',              'recordingMbid'),
        ('MusicBrainz Artist Id',             'artistMbid'),
        ('MusicBrainz Album Id',              'releaseMbid'),
        ('MusicBrainz Release Group Id',      'releaseGroupMbid'),
        ('MusicBrainz Album Release Country', 'country'),
        ('MusicBrainz Album Type',            'releaseType'),
        ('Acoustid Id',                       'acoustidId'),
        ('MOOD',                              'mood'),
        ('ORIGINALYEAR',                      'originalYear'),
        ('BARCODE',                           'barcode'),
        ('CATALOGNUMBER',                     'catalogNo'),
        ('SCRIPT',                            'script'),
        ('ARRANGER',                          'arranger'),
        ('ARRANGER SORT',                     'arrangerSort'),
        ('PRODUCER',                          'producer'),
        ('PRODUCER SORT',                     'producerSort'),
        ('ENGINEER',                          'engineer'),
        ('MIXER',                             'mixer'),
        ('MIXER SORT',                        'mixerSort'),
        ('DJMIXER',                           'djMixer'),
        ('ORCHESTRA',                         'orchestra'),
        ('ORCHESTRA SORT',                    'orchestraSort'),
        ('ENSEMBLE',                          'ensemble'),
        ('ENSEMBLE SORT',                     'ensembleSort'),
        ('CHOIR',                             'choir'),
        ('CHOIR SORT',                        'choirSort'),
        ('WORK',                              'work'),
        ('MusicBrainz Work Id',               'workMbid'),
        ('LYRICIST',                          'lyricist'),
        ('LYRICIST SORT',                     'lyricistSort'),
        ('COMPOSER SORT',                     'composerSort'),
        ('CONDUCTOR SORT',                    'conductorSort'),
        ('DISCOGS_RELEASE_ID',                'discogsId'),
        ('REPLAYGAIN_TRACK_GAIN',             'replayGainTrackGain'),
        ('REPLAYGAIN_TRACK_PEAK',             'replayGainTrackPeak'),
        ('REPLAYGAIN_ALBUM_GAIN',             'replayGainAlbumGain'),
        ('REPLAYGAIN_ALBUM_PEAK',             'replayGainAlbumPeak'),
    ):
        txxx(desc, td.get(key))

    txxx('ARTISTS',          td.get('artists') or td.get('artist'))
    txxx('ARTISTS_SORT',     td.get('artistsSort') or td.get('artistSort'))
    txxx('ALBUM_ARTISTS',    td.get('albumArtist'))
    txxx('ALBUM_ARTISTS_SORT', td.get('albumArtistSort'))
    if td.get('isCompilation') == '1':
        txxx('COMPILATION', '1')
    if td.get('fbpm'):
        txxx('FBPM', td['fbpm'])

    # Pochette
    cover_bytes, cover_mime = _cover_to_bytes(td.get('coverPath'))
    if cover_bytes:
        tags.delall('APIC')
        tags.add(APIC(encoding=0, mime=cover_mime, type=3, desc='', data=cover_bytes))

    # Appliquer les valeurs préservées (tags que l'utilisateur a protégés)
    for k, v in (td.get('preservedValues') or {}).items():
        if v:
            txxx(k, v)   # simple : réécrit comme TXXX si on ne connaît pas le frame ID

    # Version ID3 : 2.3 (défaut Picard/Kodi/NAS) ou 2.4
    v2_ver = 4 if td.get('id3Version') == '2.4' else 3
    tags.save(str(filepath), v2_version=v2_ver)
    return True


def _m4a_write(filepath, td):
    """Écrit tous les champs OpenTagger dans un M4A via mutagen.mp4."""
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm, AtomDataType

    audio = MP4(str(filepath))

    if td.get('clearExisting'):
        audio.clear()

    def s(atom, val):
        if val:
            audio[atom] = [val]

    def ff(name, val):
        """Écrit un atome freeform iTunes (----:com.apple.iTunes:name)."""
        if val:
            audio[f'----:com.apple.iTunes:{name}'] = [
                MP4FreeForm(str(val).encode('utf-8'), AtomDataType.UTF8)
            ]

    # Atomes standard
    s('\xa9nam', td.get('title'))
    s('\xa9ART', td.get('artist'))
    s('aART',    td.get('albumArtist'))
    s('\xa9alb', td.get('album'))
    s('\xa9day', td.get('year'))
    s('\xa9gen', td.get('genre'))
    s('\xa9wrt', td.get('composer'))
    s('\xa9lyr', td.get('lyrics'))
    s('\xa9cmt', td.get('comment'))
    s('\xa9grp', td.get('grouping'))
    # Sort
    s('soal', td.get('albumSort'))
    s('soar', td.get('artistSort'))
    s('soaa', td.get('albumArtistSort'))
    s('sonm', td.get('titleSort'))
    s('soco', td.get('composerSort'))

    # Piste / disque
    try:
        trk = int(td.get('track') or 0)
        trt = int(td.get('trackTotal') or 0)
        if trk > 0:
            audio['trkn'] = [(trk, trt)]
    except (ValueError, TypeError):
        pass
    try:
        dsk = int(td.get('discNo') or 0)
        dst = int(td.get('discTotal') or 0)
        if dsk > 0:
            audio['disk'] = [(dsk, dst)]
    except (ValueError, TypeError):
        pass

    # BPM
    try:
        if td.get('bpm'):
            audio['tmpo'] = [int(td['bpm'])]
    except (ValueError, TypeError):
        pass

    # Compilation
    if td.get('isCompilation') == '1':
        audio['cpil'] = True

    # Pochette
    cover_bytes, _ = _cover_to_bytes(td.get('coverPath'))
    if cover_bytes:
        cp = td.get('coverPath', '')
        fmt = MP4Cover.FORMAT_PNG if cp.lower().endswith('.png') else MP4Cover.FORMAT_JPEG
        audio['covr'] = [MP4Cover(cover_bytes, imageformat=fmt)]

    # Atomes freeform MusicBrainz / credits / etc.
    for name, key in (
        ('MusicBrainz Track Id',              'recordingMbid'),
        ('MusicBrainz Artist Id',             'artistMbid'),
        ('MusicBrainz Album Id',              'releaseMbid'),
        ('MusicBrainz Release Group Id',      'releaseGroupMbid'),
        ('MusicBrainz Album Release Country', 'country'),
        ('MusicBrainz Album Type',            'releaseType'),
        ('Acoustid Id',                       'acoustidId'),
        ('ISRC',                              'isrc'),
        ('MOOD',                              'mood'),
        ('ORIGINALYEAR',                      'originalYear'),
        ('BARCODE',                           'barcode'),
        ('CATALOGNUMBER',                     'catalogNo'),
        ('SCRIPT',                            'script'),
        ('LANGUAGE',                          'language'),
        ('ARRANGER',                          'arranger'),
        ('ARRANGER SORT',                     'arrangerSort'),
        ('CONDUCTOR',                         'conductor'),
        ('CONDUCTOR SORT',                    'conductorSort'),
        ('PRODUCER',                          'producer'),
        ('PRODUCER SORT',                     'producerSort'),
        ('ENGINEER',                          'engineer'),
        ('MIXER',                             'mixer'),
        ('MIXER SORT',                        'mixerSort'),
        ('DJMIXER',                           'djMixer'),
        ('ORCHESTRA',                         'orchestra'),
        ('ORCHESTRA SORT',                    'orchestraSort'),
        ('ENSEMBLE',                          'ensemble'),
        ('ENSEMBLE SORT',                     'ensembleSort'),
        ('CHOIR',                             'choir'),
        ('CHOIR SORT',                        'choirSort'),
        ('LYRICIST',                          'lyricist'),
        ('LYRICIST SORT',                     'lyricistSort'),
        ('WORK',                              'work'),
        ('MusicBrainz Work Id',               'workMbid'),
        ('ARTISTS',                           'artists'),
        ('DISCOGS_RELEASE_ID',                'discogsId'),
        ('REPLAYGAIN_TRACK_GAIN',             'replayGainTrackGain'),
        ('REPLAYGAIN_TRACK_PEAK',             'replayGainTrackPeak'),
        ('REPLAYGAIN_ALBUM_GAIN',             'replayGainAlbumGain'),
        ('REPLAYGAIN_ALBUM_PEAK',             'replayGainAlbumPeak'),
        ('URL_OFFICIAL_ARTIST_SITE',          'artistOfficialUrl'),
        ('URL_WIKIPEDIA_ARTIST_SITE',         'artistWikipediaUrl'),
        ('URL_DISCOGS_RELEASE_SITE',          'releaseDiscogsUrl'),
    ):
        ff(name, td.get(key))

    if not td.get('artists') and td.get('artist'):
        ff('ARTISTS', td['artist'])

    audio.save()
    return True


def _flac_write(filepath, td):
    """Écrit tous les champs OpenTagger dans un FLAC via VorbisComment."""
    from mutagen.flac import FLAC, Picture

    audio = FLAC(str(filepath))

    if td.get('clearExisting'):
        audio.clear()
        audio.clear_pictures()

    def s(key, val):
        if val:
            audio[key] = [str(val)]

    s('title',                      td.get('title'))
    s('artist',                     td.get('artist'))
    s('albumartist',                td.get('albumArtist'))
    s('album',                      td.get('album'))
    s('date',                       td.get('year'))
    s('genre',                      td.get('genre'))
    s('composer',                   td.get('composer'))
    s('lyricist',                   td.get('lyricist'))
    s('conductor',                  td.get('conductor'))
    s('arranger',                   td.get('arranger'))
    s('producer',                   td.get('producer'))
    s('engineer',                   td.get('engineer'))
    s('mixer',                      td.get('mixer'))
    s('comment',                    td.get('comment'))
    s('bpm',                        td.get('bpm'))
    s('language',                   td.get('language'))
    s('isrc',                       td.get('isrc'))
    s('lyrics',                     td.get('lyrics'))
    s('grouping',                   td.get('grouping'))
    s('tracknumber',                td.get('track'))
    s('tracktotal',                 td.get('trackTotal'))
    s('discnumber',                 td.get('discNo'))
    s('disctotal',                  td.get('discTotal'))
    s('compilation',                td.get('isCompilation'))
    s('originalyear',               td.get('originalYear'))
    s('barcode',                    td.get('barcode'))
    s('catalognumber',              td.get('catalogNo'))
    s('script',                     td.get('script'))
    s('mood',                       td.get('mood'))
    s('work',                       td.get('work'))
    s('titlesort',                  td.get('titleSort'))
    s('artistsort',                 td.get('artistSort'))
    s('albumartistsort',            td.get('albumArtistSort'))
    s('albumsort',                  td.get('albumSort'))
    s('composersort',               td.get('composerSort'))
    s('replaygain_track_gain',      td.get('replayGainTrackGain'))
    s('replaygain_track_peak',      td.get('replayGainTrackPeak'))
    s('replaygain_album_gain',      td.get('replayGainAlbumGain'))
    s('replaygain_album_peak',      td.get('replayGainAlbumPeak'))
    s('musicbrainz_trackid',        td.get('recordingMbid'))
    s('musicbrainz_artistid',       td.get('artistMbid'))
    s('musicbrainz_albumid',        td.get('releaseMbid'))
    s('musicbrainz_releasegroupid', td.get('releaseGroupMbid'))
    s('musicbrainz_releasecountry', td.get('country'))
    s('musicbrainz_albumtype',      td.get('releaseType'))
    s('musicbrainz_workid',         td.get('workMbid'))
    s('acoustid_id',                td.get('acoustidId'))
    s('discogs_release_id',         td.get('discogsId'))
    s('artists',                    td.get('artists') or td.get('artist'))

    # Pochette
    cover_bytes, cover_mime = _cover_to_bytes(td.get('coverPath'))
    if cover_bytes:
        pic = Picture()
        pic.data = cover_bytes
        pic.type = 3
        pic.mime = cover_mime
        audio.clear_pictures()
        audio.add_picture(pic)

    audio.save()
    return True


def _ogg_write(filepath, td):
    """Écrit tous les champs OpenTagger dans un OGG/Opus via VorbisComment."""
    import mutagen
    audio = mutagen.File(str(filepath))
    if audio is None:
        return False
    if audio.tags is None:
        audio.add_tags()

    def s(key, val):
        if val:
            audio[key] = [str(val)]

    s('title',   td.get('title'))
    s('artist',  td.get('artist'))
    s('album',   td.get('album'))
    s('date',    td.get('year'))
    s('genre',   td.get('genre'))
    s('comment', td.get('comment'))
    s('bpm',     td.get('bpm'))
    s('isrc',    td.get('isrc'))
    s('mood',    td.get('mood'))
    s('replaygain_track_gain', td.get('replayGainTrackGain'))
    s('replaygain_track_peak', td.get('replayGainTrackPeak'))
    s('musicbrainz_trackid',   td.get('recordingMbid'))
    s('musicbrainz_artistid',  td.get('artistMbid'))
    s('musicbrainz_albumid',   td.get('releaseMbid'))

    audio.save()
    return True


def write_tags_full(filepath, td, dry_run=False):
    """
    Écrit l'intégralité du TagInfo OpenTagger dans un fichier audio via mutagen.
    Utilisé par OpenTagger (appelé via --write-json) pour remplacer jaudiotagger.
    Supporte MP3, M4A, FLAC, OGG/Opus.
    """
    if dry_run:
        print(f"  [dry-run] {td.get('artist', '?')} — {td.get('title', '?')}")
        return True

    ext = Path(str(filepath)).suffix.lower().lstrip('.')
    try:
        if ext == 'mp3':
            return _mp3_write(Path(str(filepath)), td)
        elif ext == 'm4a':
            return _m4a_write(Path(str(filepath)), td)
        elif ext == 'flac':
            return _flac_write(Path(str(filepath)), td)
        elif ext in ('ogg', 'opus', 'oga'):
            return _ogg_write(Path(str(filepath)), td)
        else:
            # Fallback générique (write_tags de base)
            info = {k: td.get(k, '') for k in ('title', 'artist', 'album', 'year', 'genre')}
            return write_tags(Path(str(filepath)), info, dry_run)
    except ImportError as e:
        print(f"  ⚠ mutagen manquant: {e} (pip install mutagen)")
        return False
    except Exception as e:
        print(f"  ⚠ write_tags_full [{ext}]: {e}")
        import traceback; traceback.print_exc()
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
    parser.add_argument('--write-json', metavar='JSON_PATH',
                        help="Écrire les tags depuis un fichier JSON OpenTagger (mode intégré)")
    args = parser.parse_args()

    # ── Mode intégré OpenTagger : écriture de tags depuis JSON ──────────────
    if args.write_json:
        with open(args.write_json, 'r', encoding='utf-8') as f:
            td = json.load(f)
        fp = Path(td['file'])
        if not fp.exists():
            print(f"ERREUR: fichier introuvable: {fp}", file=sys.stderr)
            sys.exit(1)
        ok = write_tags_full(fp, td, args.dry_run)
        sys.exit(0 if ok else 1)

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
