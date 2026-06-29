#!/home/paulceline/bin/plex-ratings-sync/.venv/bin/python3
"""
Générateur de playlists automatiques pour PlexAmp
Crée des playlists intelligentes basées sur différents critères
"""

import sqlite3
import json
import sys
import argparse
import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import logging
import random
import time
import os
import unicodedata
import re
import urllib.request
import urllib.parse
import io
import hashlib
import xml.etree.ElementTree as ET

from plex_api import default_plex_url

# Préfixe d'affichage des playlists générées.
# Laisse vide pour ne rien afficher devant le nom des playlists.
AUTO_PLAYLIST_PREFIX = ""

# Plex sait déjà très bien filtrer les grands genres. On évite donc de dupliquer
# des playlists trop évidentes comme Blues/Jazz/Rock/Pop.
GENERIC_PLEX_GENRES = {
    'alternative', 'blues', 'classical', 'country', 'easy listening',
    'electronic', 'folk', 'hip-hop', 'jazz', 'latin', 'metal', 'new age',
    'pop', 'pop/rock', 'r&b', 'rap', 'reggae', 'rock', 'soundtrack',
    'vocal', 'world',
}

# Configuration API Plex
def _detect_default_plex_url() -> str:
    """Retourne l'URL Plex par défaut selon le contexte d'exécution.
    Dans Docker (/.dockerenv présent) → host.docker.internal, sinon localhost."""
    return default_plex_url()

PLEX_URL = os.getenv("PLEX_URL", _detect_default_plex_url()).rstrip("/")
PLEX_TOKEN = os.getenv("PLEX_TOKEN", "***REMOVED***")
PLEX_MACHINE_ID = os.getenv("PLEX_MACHINE_ID", "e0c0f73d4bbd7109a0aad8c16b20db9da5ffa4c4")
LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"

# Supprime les pictogrammes/emoji des noms de playlists générés.
EMOJI_CHARS_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"  # drapeaux
    "\U0001F300-\U0001F5FF"  # symboles et pictogrammes
    "\U0001F600-\U0001F64F"  # émoticônes
    "\U0001F680-\U0001F6FF"  # transport/cartes
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"  # symboles supplémentaires
    "\U0001FA00-\U0001FAFF"
    "\u2600-\u26FF"          # symboles divers
    "\u2700-\u27BF"          # dingbats
    "]+",
    flags=re.UNICODE,
)


class PlexAmpAutoPlaylist:
    def report_overlap(self, tracks: Optional[List[Dict]] = None, output_file: Optional[str] = None, threshold: float = 0.6, selected_names_file: Optional[str] = None) -> list:
        """Rapport des groupes de playlists avec >threshold de morceaux en commun, option sélection."""
        import csv as _csv
        selected_names = None
        if selected_names_file:
            try:
                with open(selected_names_file, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                file_names = payload.get("selected_names") if isinstance(payload, dict) else None
                if isinstance(file_names, list):
                    def _norm(n: str) -> str:
                        s = PlexAmpAutoPlaylist._strip_emojis(str(n).strip())
                        return re.sub(r'\s*\(\d+\s+titres\)\s*$', '', s, flags=re.IGNORECASE).strip()
                    selected_names = {_norm(x) for x in file_names if str(x).strip()}
            except Exception:
                pass
        if tracks is None:
            tracks = self.get_track_data()
        all_playlists = self._build_all_playlists(tracks)
        if selected_names:
            all_playlists = {name: entries for name, entries in all_playlists.items() if name in selected_names}
        # Index: playlist name -> set of track ids
        playlist_tracks = {name: set(t['id'] for t in entries) for name, entries in all_playlists.items()}
        names = list(playlist_tracks.keys())
        n = len(names)
        groups = []
        visited = set()
        for i in range(n):
            if names[i] in visited:
                continue
            base = names[i]
            base_ids = playlist_tracks[base]
            group = [base]
            for j in range(i+1, n):
                other = names[j]
                if other in visited:
                    continue
                other_ids = playlist_tracks[other]
                if not base_ids or not other_ids:
                    continue
                overlap = len(base_ids & other_ids) / min(len(base_ids), len(other_ids))
                if overlap > threshold:
                    group.append(other)
                    visited.add(other)
            if len(group) > 1:
                groups.append(group)
                visited.update(group)
        # Rapport
        if output_file:
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = _csv.writer(f)
                writer.writerow(['Group', 'Playlists'])
                for idx, group in enumerate(groups, 1):
                    writer.writerow([idx, '; '.join(group)])
        else:
            print("\n🎲 GROUPES DE PLAYLISTS (>60% de morceaux en commun):")
            for idx, group in enumerate(groups, 1):
                print(f"  Groupe {idx}: {', '.join(group)}")
        return groups

    DEFAULT_POSTER_STYLE: Dict[str, Any] = {
        "size": 600,
        "overlay_alpha": 180,
        "title_size": 70,
        "subtitle_size": 40,
        "emoji_size": 140,
        "title_start_y": 280,
        "title_line_step": 60,
        "text_padding": 40,
        "title_color": [255, 255, 255, 255],
        "title_shadow_color": [0, 0, 0, 220],
        "title_stroke_width": 2,
        "subtitle_color": [220, 220, 220, 240],
        "line_color": [255, 255, 255, 80],
        "font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "emoji_font_path": "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
        "strip_auto_prefix": True,
        "strip_count_suffix": True,
        "default_emoji": "🎵",
        "default_colors": [[70, 70, 140], [120, 120, 220]],
        "themes": [],
    }

    def _safe_int(self, value: Any, fallback: int, low: int, high: int) -> int:
        try:
            out = int(value)
        except Exception:
            return fallback
        return max(low, min(high, out))

    def _parse_rgba(self, value: Any, fallback: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
            return fallback
        vals = [self._safe_int(x, fallback[i], 0, 255) for i, x in enumerate(value[:4])]
        if len(vals) == 3:
            vals.append(fallback[3])
        return (vals[0], vals[1], vals[2], vals[3])

    def _parse_rgb(self, value: Any, fallback: Tuple[int, int, int]) -> Tuple[int, int, int]:
        rgba = self._parse_rgba(value, (fallback[0], fallback[1], fallback[2], 255))
        return rgba[0], rgba[1], rgba[2]

    def _normalize_style(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        style = dict(self.DEFAULT_POSTER_STYLE)
        style.update(raw or {})

        style["title_color"] = self._parse_rgba(style.get("title_color"), (255, 255, 255, 255))
        style["title_shadow_color"] = self._parse_rgba(style.get("title_shadow_color"), (0, 0, 0, 180))
        style["subtitle_color"] = self._parse_rgba(style.get("subtitle_color"), (200, 200, 200, 220))
        style["line_color"] = self._parse_rgba(style.get("line_color"), (255, 255, 255, 80))

        default_colors = style.get("default_colors", self.DEFAULT_POSTER_STYLE["default_colors"])
        if isinstance(default_colors, (list, tuple)) and len(default_colors) == 2:
            style["default_colors"] = [
                self._parse_rgb(default_colors[0], (80, 80, 160)),
                self._parse_rgb(default_colors[1], (150, 150, 230)),
            ]
        else:
            style["default_colors"] = [(70, 70, 140), (120, 120, 220)]

        style["size"] = self._safe_int(style.get("size"), 600, 256, 2000)
        style["overlay_alpha"] = self._safe_int(style.get("overlay_alpha"), 180, 0, 255)
        style["title_size"] = self._safe_int(style.get("title_size"), 70, 14, 200)
        style["subtitle_size"] = self._safe_int(style.get("subtitle_size"), 40, 10, 120)
        style["emoji_size"] = self._safe_int(style.get("emoji_size"), 140, 16, 300)
        style["title_start_y"] = self._safe_int(style.get("title_start_y"), 280, 20, 1800)
        style["title_line_step"] = self._safe_int(style.get("title_line_step"), 60, 10, 200)
        style["text_padding"] = self._safe_int(style.get("text_padding"), 40, 10, 400)
        style["title_stroke_width"] = self._safe_int(style.get("title_stroke_width"), 2, 0, 10)

        themes = style.get("themes", [])
        norm_themes = []
        if isinstance(themes, list):
            for t in themes:
                if not isinstance(t, dict):
                    continue
                keywords = t.get("keywords", [])
                colors = t.get("colors", [])
                if not (isinstance(keywords, list) and keywords and isinstance(colors, list) and len(colors) == 2):
                    continue
                norm_themes.append({
                    "keywords": [str(k).lower() for k in keywords if str(k).strip()],
                    "match": str(t.get("match", "all")).lower(),
                    "emoji": str(t.get("emoji", "🎵")),
                    "colors": [
                        self._parse_rgb(colors[0], (80, 80, 160)),
                        self._parse_rgb(colors[1], (150, 150, 230)),
                    ],
                })
        style["themes"] = norm_themes
        return style

    def _load_poster_style(self) -> Dict[str, Any]:
        path = os.getenv("PLEX_POSTER_STYLE_CONFIG", "").strip()
        if not path:
            return self._normalize_style({})
        cfg_path = Path(path)
        if not cfg_path.exists():
            self.logger.warning(f"⚠️ Fichier style posters introuvable: {cfg_path} (style par défaut utilisé)")
            return self._normalize_style({})
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("le JSON doit être un objet")
            self.logger.info(f"🎨 Style posters chargé: {cfg_path}")
            return self._normalize_style(raw)
        except Exception as e:
            self.logger.warning(f"⚠️ Style posters invalide ({cfg_path}): {e} (style par défaut utilisé)")
            return self._normalize_style({})

    def _build_all_playlists(self, tracks: List[Dict], custom_config: Optional[str] = None) -> Dict[str, List[Dict]]:
        """Construit le dictionnaire complet des playlists à générer."""
        all_playlists: Dict[str, List[Dict]] = {}

        # Playlists natives
        all_playlists.update(self.create_rating_playlists(tracks))
        all_playlists.update(self.create_year_playlists(tracks))
        all_playlists.update(self.create_genre_playlists(tracks))
        all_playlists.update(self.create_smart_playlists(tracks))
        all_playlists.update(self.create_lastfm_default_playlists(tracks))
        all_playlists.update(self.create_discovery_playlists(tracks))
        all_playlists.update(self.create_top_this_month(tracks))
        all_playlists.update(self.create_to_review_playlists(tracks))
        all_playlists.update(self.create_cleanup_playlists(tracks))
        all_playlists.update(self.create_radio_playlists(tracks))
        all_playlists.update(self.create_stars80_playlist(tracks))
        all_playlists.update(self.create_top50_playlist(tracks))

        # Playlists thématiques par mots-clés (recherche dans tous les genres)
        def _match_keywords(track, keywords, _cache={}):
            key = tuple(keywords)
            if key not in _cache:
                _cache[key] = re.compile('|'.join(r'\b' + re.escape(k) + r'\b' for k in keywords))
            searchable = ' '.join(track.get('genres', [track['genre']])).lower()
            searchable += ' ' + track['album'].lower() + ' ' + track['title'].lower()
            return bool(_cache[key].search(searchable))

        funk_tracks = [t for t in tracks if _match_keywords(t, ['funk', 'disco', 'groove', 'boogie', 'soul'])]
        if funk_tracks:
            all_playlists[f'{AUTO_PLAYLIST_PREFIX}Funk & Disco ({len(funk_tracks)} titres)'] = funk_tracks

        workout_tracks = [t for t in tracks if _match_keywords(t, ['workout', 'running', 'cardio', 'power', 'training'])]
        if workout_tracks:
            all_playlists[f'{AUTO_PLAYLIST_PREFIX}Running & Workout ({len(workout_tracks)} titres)'] = workout_tracks

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Playlists thématiques additionnelles (moods, activités, ambiances)
        # Chaque entrée: (nom, mots-clés). Crée la playlist seulement si non vide.
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        EXTRA_THEMES: List[Tuple[str, List[str]]] = [
            # — Moods & ambiances —
            ('Chill & Relax',          ['chill', 'lofi', 'lo-fi', 'downtempo', 'relax', 'mellow', 'calm']),
            ('Rage & Énergie',         ['rage', 'angry', 'aggressive', 'fury', 'hardcore', 'thrash', 'speed']),
            ('Mélancolie',             ['sad', 'melancholy', 'tristesse', 'lonely', 'blue', 'tears']),
            ('Romantique',             ['love', 'romance', 'romantic', 'amour', 'heart', 'kiss']),
            ('Joie & Bonheur',         ['happy', 'joy', 'sunshine', 'smile', 'feel good', 'feelgood', 'good vibes']),
            ('Nostalgie',              ['nostalgia', 'memories', 'remember', 'souvenir', 'yesterday']),
            ('Mystique & Spirituel',   ['mystic', 'spiritual', 'meditation', 'zen', 'mantra', 'sacred', 'divine']),
            ('Épique & Cinématique',   ['epic', 'cinematic', 'soundtrack', 'theme', 'orchestra', 'score']),
            ('Dark & Intense',         ['dark', 'sombre', 'noir', 'gothic', 'doom', 'industrial']),
            ('Dreamy & Éthéré',        ['dream', 'dreamy', 'ethereal', 'shoegaze', 'atmospheric', 'reverb']),

            # — Activités —
            ('Soirée & Fête',          ['party', 'fête', 'fete', 'club', 'dance floor', 'dancing', 'dancefloor']),
            ('Apéro Chill',            ['lounge', 'bossa', 'cocktail', 'apero', 'apéro', 'jazzy']),
            ('Travail & Focus',        ['focus', 'study', 'concentration', 'instrumental', 'work', 'coding']),
            ('Conduite Auto',          ['drive', 'highway', 'road', 'cruising', 'route', 'voiture', 'car']),
            ('Voyage & Découverte',    ['travel', 'voyage', 'world', 'world music', 'globetrotter']),
            ('Sieste & Sommeil',       ['sleep', 'sommeil', 'lullaby', 'berceuse', 'dream', 'night ambient']),
            ('Cuisine & Recette',      ['cooking', 'kitchen', 'cuisine', 'feel good', 'happy']),
            ('Yoga & Méditation',      ['yoga', 'meditation', 'meditate', 'breathe', 'om', 'relaxation']),
            ('Gaming Session',         ['gaming', 'game', 'epic', 'electronic', 'synthwave', 'cyber']),
            ('Lecture & Détente',      ['piano', 'classical', 'instrumental', 'cello', 'violin', 'guitar']),

            # — Saisons & météo —
            ('Été & Soleil',           ['summer', 'été', 'ete', 'sun', 'beach', 'plage', 'tropical', 'caribbean']),
            ('Hiver & Cocooning',      ['winter', 'hiver', 'snow', 'neige', 'cozy', 'fireplace']),
            ('Printemps Frais',        ['spring', 'printemps', 'fresh', 'morning', 'matin', 'sunrise']),
            ('Automne Doré',           ['autumn', 'fall', 'automne', 'rain', 'pluie', 'misty', 'golden']),
            ('Pluie & Mélancolie',     ['rain', 'pluie', 'storm', 'orage', 'thunder', 'tears']),

            # — Moments de la journée —
            ('Réveil Doux',            ['morning', 'matin', 'sunrise', 'wake up', 'reveil', 'réveil']),
            ('Brunch & Repas',         ['brunch', 'sunday', 'dimanche', 'jazz', 'bossa', 'lounge', 'repas', 'diner', 'lunch']),
            ('Crépuscule',             ['sunset', 'dusk', 'twilight', 'crepuscule', 'crépuscule', 'evening']),
            ('Nuit Profonde',          ['night', 'midnight', 'minuit', 'nuit', 'late', 'after hours']),
            ('After Party',            ['after', 'after hours', 'late night', 'deep house', 'minimal']),

            # — Décennies / époques (si non couvertes par year_playlists) —
            ('Vibes 60s',              ['1960', 'sixties', '60s']),
            ('Vibes 70s',              ['1970', 'seventies', '70s']),
            ('Vibes 80s',              ['1980', 'eighties', '80s', 'new wave', 'synthpop']),
            ('Vibes 90s',              ['1990', 'nineties', '90s', 'grunge', 'eurodance']),
            ('Vibes 2000s',            ['2000', '2000s', 'noughties', 'y2k']),
            ('Vibes 2010s',            ['2010', '2010s', 'edm']),
            ('Vibes 2020s',            ['2020', '2020s', 'hyperpop', 'afrobeats', 'drill']),

            # — Genres & sous-cultures —
            ('Rap & Hip-Hop',          ['rap', 'hip hop', 'hip-hop', 'trap', 'gangsta', 'boom bap']),
            ('Rock Classique',         ['classic rock', 'rock and roll', 'led', 'stones', 'beatles']),
            ('Hard Rock & Metal',      ['metal', 'hard rock', 'heavy', 'thrash', 'doom', 'metalcore']),
            ('Punk & Garage',          ['punk', 'garage', 'oi', 'hardcore punk', 'pop punk', 'skate']),
            ('Indie & Alternatif',     ['indie', 'alternative', 'alt rock', 'lo-fi', 'bedroom']),
            ('Pop FR',                 ['variete', 'variété', 'french pop', 'musique française']),
            ('Chanson Française',      ['chanson', 'francaise', 'française', 'brel', 'piaf', 'brassens', 'aznavour', 'gainsbourg', 'variet']),
            ('Pop US/UK',              ['pop', 'mainstream', 'top 40', 'billboard', 'chart']),
            ('Jazz & Blues',           ['jazz', 'blues', 'swing', 'bebop', 'cool jazz']),
            ('Reggae & Roots',         ['reggae', 'roots', 'rasta', 'dub', 'ska', 'rocksteady']),
            ('Latin Vibes',            ['latin', 'salsa', 'bachata', 'reggaeton', 'cumbia', 'latino']),
            ('K-Pop & J-Pop',          ['kpop', 'k-pop', 'k pop', 'jpop', 'j-pop', 'j pop', 'korean', 'japanese']),
            ('Country & Folk',         ['country', 'folk', 'americana', 'bluegrass', 'cowboy']),
            ('Classical & Orchestre',  ['classical', 'classique', 'orchestra', 'symphony', 'concerto', 'baroque', 'chopin', 'mozart', 'bach', 'beethoven']),
            ('Electronic & Techno',    ['techno', 'house', 'electronic', 'edm', 'trance', 'dubstep']),
            ('Synthwave & Retrowave',  ['synthwave', 'retrowave', 'vaporwave', 'outrun', 'synth pop']),
            ('Ambient & Drone',        ['ambient', 'drone', 'soundscape', 'atmospheric', 'new age']),
            ('Funk & Soul',            ['funk', 'soul', 'motown', 'rhythm and blues']),
            ('R&B',                    ['r&b', 'rnb', 'neo-soul', 'neo soul', 'contemporary r&b', 'quiet storm']),
            ('Reggaeton & Urban',      ['reggaeton', 'urban', 'latin urban', 'dembow']),

            # — Cas spéciaux —
            ('Instrumentaux',          ['instrumental', 'no vocals', 'sans paroles', 'piano solo', 'guitar solo']),
            ('Live & Concerts',        ['live', 'concert', 'unplugged', 'mtv', 'session', 'au zenith']),
            ('Acoustique',             ['acoustic', 'unplugged', 'acoustique', 'mtv unplugged']),
            ('Remixes & Edits',        ['remix', 'edit', 'rework', 'bootleg', 'mashup']),
            ('Covers & Reprises',      ['cover', 'reprise', 'tribute', 'version']),
            ('Bandes Originales',      ['soundtrack', 'ost', 'bo', 'b.o.', 'theme', 'cinema']),
            ('Karaoké',                ['karaoke', 'karaoké', 'sing along', 'singalong']),
            ('Génériques TV/Films',    ['theme', 'tv', 'générique', 'generique', 'opening', 'ending']),
            ('Christmas & Fêtes',      ['christmas', 'noel', 'noël', 'holiday', 'xmas', 'jingle']),
            ('Anniversaire',           ['birthday', 'anniversaire', 'happy birthday', 'celebrate']),
            ('Mariage',                ['wedding', 'mariage', 'marry', 'first dance', 'bridal']),
        ]

        # Pré-calcul des blobs de recherche pour accélérer le scan multi-thèmes
        # (évite 66 × 204k recomputations de la chaîne de recherche)
        track_blobs: List[str] = []
        for t in tracks:
            blob = ' '.join(t.get('genres', [t.get('genre', '')]))
            blob += ' ' + (t.get('album', '') or '') + ' ' + (t.get('title', '') or '')
            track_blobs.append(blob.lower())

        for theme_name, theme_keywords in EXTRA_THEMES:
            full_key = f'{AUTO_PLAYLIST_PREFIX}{theme_name}'
            # Évite d'écraser une playlist déjà créée par une étape précédente
            if any(k.startswith(full_key) for k in all_playlists.keys()):
                continue
            pattern = re.compile('|'.join(r'\b' + re.escape(k) + r'\b' for k in theme_keywords))
            matched = [tracks[i] for i, blob in enumerate(track_blobs) if pattern.search(blob)]
            # Seuil mini de 5 titres pour éviter les playlists ridicules
            if len(matched) >= 5:
                # Limite à 500 titres triés par lectures + rating
                matched.sort(key=lambda t: (t.get('play_count', 0) or 0, t.get('rating', 0) or 0), reverse=True)
                matched = matched[:500]
                all_playlists[f'{full_key} ({len(matched)} titres)'] = matched
        all_playlists.update(self._load_custom_playlists_from_json(tracks, custom_config))
        # Charger automatiquement les playlists manuelles persistantes si elles existent
        try:
            manual_path = Path(__file__).parent / 'manual_playlists.json'
            if manual_path.exists():
                all_playlists.update(self._load_custom_playlists_from_json(tracks, str(manual_path)))
        except Exception:
            pass
        return self._normalize_playlist_names(all_playlists)

    @staticmethod
    def _strip_emojis(text: str) -> str:
        """Retire les emoji des noms tout en conservant accents, chiffres et ponctuation utile."""
        cleaned = EMOJI_CHARS_RE.sub('', text)
        cleaned = cleaned.replace('\uFE0F', '').replace('\u200D', '')
        cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
        return cleaned

    def _normalize_playlist_names(self, playlists: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        """Normalise les noms de playlists : supprime les emoji et le compteur de titres."""
        normalized: Dict[str, List[Dict]] = {}
        renamed = 0

        for name, tracks in playlists.items():
            clean_name = self._strip_emojis(name)
            clean_name = re.sub(r'\s*\(\d+\s+titres\)\s*$', '', clean_name, flags=re.IGNORECASE).strip()
            if clean_name != name:
                renamed += 1

            if clean_name in normalized:
                seen_ids = {int(t.get('id')) for t in normalized[clean_name] if t.get('id')}
                for track in tracks:
                    track_id = track.get('id')
                    if track_id is None:
                        normalized[clean_name].append(track)
                        continue
                    try:
                        tid = int(track_id)
                    except Exception:
                        normalized[clean_name].append(track)
                        continue
                    if tid not in seen_ids:
                        normalized[clean_name].append(track)
                        seen_ids.add(tid)
            else:
                normalized[clean_name] = tracks

        if renamed:
            self.logger.info(f"🧹 Emojis retirés de {renamed} noms de playlists")

        return normalized

    def _track_search_blob(self, track: Dict) -> str:
        parts = [
            track.get('title', ''),
            track.get('artist', ''),
            track.get('album', ''),
            track.get('genre', ''),
            ' '.join(track.get('genres', [])),
        ]
        return ' '.join(parts).lower()

    def _filter_tracks_with_rule(self, tracks: List[Dict], rule: Dict) -> List[Dict]:
        """Filtre des pistes via une règle JSON simple."""
        include_any = [str(v).lower() for v in rule.get('include_any', [])]
        include_all = [str(v).lower() for v in rule.get('include_all', [])]
        exclude_any = [str(v).lower() for v in rule.get('exclude_any', [])]
        genres_any = [str(v).lower() for v in rule.get('genres_any', [])]

        min_rating = rule.get('min_rating')
        max_rating = rule.get('max_rating')
        min_plays = rule.get('min_play_count')
        max_plays = rule.get('max_play_count')
        limit = rule.get('limit')
        sort_by = str(rule.get('sort_by', 'play_count')).lower()

        out: List[Dict] = []
        for track in tracks:
            blob = self._track_search_blob(track)
            track_genres = [g.lower() for g in track.get('genres', [track.get('genre', 'Unknown')])]
            rating = track.get('rating', 0) or 0
            plays = track.get('play_count', 0) or 0

            if include_any and not any(tok in blob for tok in include_any):
                continue
            if include_all and not all(tok in blob for tok in include_all):
                continue
            if exclude_any and any(tok in blob for tok in exclude_any):
                continue
            if genres_any and not any(g in track_genres for g in genres_any):
                continue

            if min_rating is not None and rating < float(min_rating):
                continue
            if max_rating is not None and rating > float(max_rating):
                continue
            if min_plays is not None and plays < int(min_plays):
                continue
            if max_plays is not None and plays > int(max_plays):
                continue

            out.append(track)

        if sort_by == 'recent':
            out.sort(key=lambda t: t.get('added_at', ''), reverse=True)
        elif sort_by == 'rating':
            out.sort(key=lambda t: (t.get('rating', 0) or 0, t.get('play_count', 0) or 0), reverse=True)
        else:
            out.sort(key=lambda t: (t.get('play_count', 0) or 0, t.get('rating', 0) or 0), reverse=True)

        if isinstance(limit, int) and limit > 0:
            out = out[:limit]
        return out

    def _load_custom_playlists_from_json(self, tracks: List[Dict], config_path: Optional[str]) -> Dict[str, List[Dict]]:
        """Charge des playlists personnalisées depuis un fichier JSON.

        Format attendu:
        {
          "playlists": [
            {
              "name": "Nom playlist",
              "prefix_auto": true,
              "include_any": ["token1", "token2"],
              "exclude_any": ["live"],
              "genres_any": ["electronic", "pop/rock"],
              "min_rating": 3,
              "min_play_count": 5,
              "limit": 200,
              "sort_by": "play_count"
            }
          ]
        }
        """
        if not config_path:
            return {}

        path = Path(config_path)
        if not path.exists():
            self.logger.warning(f"⚠️ Config playlists personnalisées introuvable: {path}")
            return {}

        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception as e:
            self.logger.error(f"❌ Impossible de lire {path}: {e}")
            return {}

        playlists = payload.get('playlists', [])
        if not isinstance(playlists, list):
            self.logger.error(f"❌ Format invalide dans {path}: 'playlists' doit être une liste")
            return {}

        result: Dict[str, List[Dict]] = {}
        for rule in playlists:
            if not isinstance(rule, dict):
                continue
            name = str(rule.get('name', '')).strip()
            if not name:
                continue
            # Support explicit id lists for manual/persistent playlists
            if isinstance(rule.get('ids'), list) and rule.get('ids'):
                ids_set = set(int(x) for x in rule.get('ids') if x)
                filtered = [t for t in tracks if int(t.get('id') or 0) in ids_set]
            else:
                filtered = self._filter_tracks_with_rule(tracks, rule)
            if not filtered:
                continue

            prefix_auto = bool(rule.get('prefix_auto', True))
            full_name = f"{AUTO_PLAYLIST_PREFIX if prefix_auto else ''}{name} ({len(filtered)} titres)"
            result[full_name] = filtered

        if result:
            self.logger.info(f"🧩 {len(result)} playlists personnalisées chargées depuis {path}")
        return result

    @staticmethod
    def _safe_filename(name: str) -> str:
        """Convertit un nom de playlist en nom de fichier sûr."""
        # Supprimer les emojis et caractères spéciaux via unicodedata
        cleaned = unicodedata.normalize('NFKD', name)
        cleaned = re.sub(r'[^\w\s-]', '', cleaned, flags=re.UNICODE)
        cleaned = re.sub(r'[\s]+', '_', cleaned.strip())
        return cleaned or 'playlist'

    def _connect_db(self):
        """Crée une connexion DB avec collation icu_root et text_factory."""
        # La DB Plex est souvent montée en lecture seule dans Docker.
        db_uri = f"file:{urllib.parse.quote(str(self.plex_db_path), safe='/')}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        conn.create_collation('icu_root', lambda a, b: (a > b) - (a < b))
        conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
        return conn

    def _query_musicbrainz_year(self, title: str, artist: str, album: Optional[str] = None) -> Optional[int]:
        """Query MusicBrainz for a recording/release year. Returns first found year or None.

        Note: limited, best-effort. Respects basic User-Agent requirement.
        """
        try:
            base = 'https://musicbrainz.org/ws/2/recording'
            q = f'recording:"{title}" AND artist:"{artist}"'
            if album:
                q += f' AND release:"{album}"'
            params = {'query': q, 'fmt': 'json', 'limit': '3'}
            url = base + '?' + urllib.parse.urlencode(params)
            ua_contact = os.getenv('PLEX_CONTACT_EMAIL', 'noreply@example.com')
            headers = {'User-Agent': f'plex-playlists/1.0 ({ua_contact})'}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8', errors='replace'))
            recs = data.get('recordings') or []
            for r in recs:
                releases = r.get('releases') or []
                for rel in releases:
                    date = rel.get('date')
                    if date:
                        try:
                            year = int(str(date)[:4])
                            if 1900 <= year <= datetime.datetime.now().year:
                                return year
                        except Exception:
                            continue
            return None
        except Exception:
            return None

    def enrich_tracks_years(self, tracks: List[Dict], artists_filter: Optional[List[str]] = None, max_calls: int = 50) -> int:
        """Enrichit en mémoire le champ 'year' des pistes sans année via MusicBrainz.

        - `artists_filter` : si fourni, n'enrichit que les pistes dont l'artiste contient l'une des chaînes.
        - `max_calls` : nombre maximum d'appels HTTP (pour éviter de spammer l'API).
        Retourne le nombre de pistes enrichies.
        """
        calls = 0
        enriched = 0
        filters = [a.lower() for a in (artists_filter or [])]
        for t in tracks:
            if calls >= max_calls:
                break
            year = None
            try:
                year = int(t.get('year') or 0) or None
            except Exception:
                year = None
            if year:
                continue
            artist = (t.get('artist') or '').lower()
            if filters and not any(f in artist for f in filters):
                continue
            title = t.get('title') or ''
            album = t.get('album') or None
            y = self._query_musicbrainz_year(title, artist, album)
            calls += 1
            time.sleep(1.0)
            if y:
                try:
                    t['year'] = int(y)
                    enriched += 1
                except Exception:
                    pass
        try:
            self.logger.info(f"🔎 Enrichissement MusicBrainz: appels={calls}, enrichies={enriched}")
        except Exception:
            pass
        return enriched

    def export_playlists_m3u(self, playlists: Dict[str, List[Dict]], export_dir: str):
        """Exporte chaque playlist au format M3U étendu dans le dossier export_dir."""
        export_path = Path(export_dir)
        export_path.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"📤 Export M3U de {len(playlists)} playlists vers {export_path}")
        exported = 0
        for name, tracks in playlists.items():
            file_name = self._safe_filename(name)
            m3u_path = export_path / f"{file_name}.m3u"
            with open(m3u_path, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                f.write(f"#PLAYLIST:{name}\n\n")
                for track in tracks:
                    fp = track.get('file_path')
                    if not fp:
                        continue
                    duration = int(track.get('duration_ms', 0) / 1000) if track.get('duration_ms') else -1
                    artist = track.get('artist', 'Unknown')
                    title = track.get('title', 'Unknown')
                    f.write(f"#EXTINF:{duration},{artist} - {title}\n")
                    # Chemin relatif si possible
                    try:
                        rel_path = str(Path(fp).relative_to(export_path.parent))
                    except Exception:
                        rel_path = fp
                    f.write(rel_path + "\n")
            exported += 1
        self.logger.info(f"✅ {exported} playlists exportées vers {export_path}")
    def __init__(
        self,
        plex_db_path: str,
        verbose: bool = False,
        lastfm_user: str = "",
        lastfm_api_key: str = "",
        lastfm_period: str = "overall",
        lastfm_max_pages: int = 5,
        randomize_poster_styles: bool = True,
    ):
        self.plex_db_path = Path(plex_db_path)
        self.verbose = verbose
        self._last_tracks: List[Dict] = []
        self._last_playlists: Dict[str, List[Dict]] = {}
        self.lastfm_user = (lastfm_user or os.getenv("LASTFM_USER", "")).strip()
        self.lastfm_api_key = (lastfm_api_key or os.getenv("LASTFM_API_KEY", "")).strip()
        self.lastfm_period = (lastfm_period or os.getenv("LASTFM_PERIOD", "overall")).strip() or "overall"
        self.lastfm_max_pages = max(1, int(lastfm_max_pages or os.getenv("LASTFM_MAX_PAGES", 5)))
        self.randomize_poster_styles = randomize_poster_styles or os.getenv("PLEX_RANDOMIZE_POSTER_STYLES", "").lower() in ("1", "true", "yes")
        self.setup_logging()
        self.poster_style = self._load_poster_style()
        
    def setup_logging(self):
        log_format = '%(asctime)s - %(levelname)s - %(message)s'
        level = logging.DEBUG if self.verbose else logging.INFO
        logging.basicConfig(
            level=level,
            format=log_format,
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        self.logger = logging.getLogger(__name__)

    def get_track_data(self) -> List[Dict]:
        """Récupère toutes les données des pistes avec métadonnées enrichies.
        Déduplique par id (une piste peut avoir plusieurs genres)."""
        try:
            with self._connect_db() as conn:
                cursor = conn.cursor()
                
                # Récupère toutes les pistes avec tous leurs genres
                query = """
                SELECT
                    mi.id,
                    mi.title as track_title,
                    mi.year,
                    mi.originally_available_at,
                    mitem.duration,
                    mis.rating as user_rating,
                    mis.view_count as play_count,
                    mis.last_viewed_at,
                    mis.created_at as added_at,
                    albums.title as album_title,
                    artists.title as artist_name,
                    GROUP_CONCAT(DISTINCT genres.tag) as genres,
                    mp.file as file_path,
                    albums.year as album_year
                FROM metadata_items mi
                LEFT JOIN metadata_item_settings mis ON mi.guid = mis.guid
                LEFT JOIN metadata_items albums ON mi.parent_id = albums.id
                LEFT JOIN metadata_items artists ON albums.parent_id = artists.id
                LEFT JOIN taggings ON mi.id = taggings.metadata_item_id
                LEFT JOIN tags genres ON taggings.tag_id = genres.id AND genres.tag_type = 1
                JOIN media_items mitem ON mi.id = mitem.metadata_item_id
                JOIN media_parts mp ON mitem.id = mp.media_item_id
                WHERE mi.metadata_type = 10
                AND mp.file IS NOT NULL
                GROUP BY mi.id
                ORDER BY mi.title
                """
                
                cursor.execute(query)
                rows = cursor.fetchall()
                
                tracks = []
                for row in rows:
                    all_genres = (row[11] or 'Unknown').split(',')
                    track = {
                        'id': row[0],
                        'title': row[1] or 'Unknown',
                        'year': row[2] or row[13],   # track year, fallback sur album year
                        'release_date': row[3],
                        'duration_ms': row[4] or 0,
                        'rating': row[5] or 0,
                        'play_count': row[6] or 0,
                        'last_played': row[7],
                        'date_added': row[8],
                        'album': row[9] or 'Unknown Album',
                        'artist': row[10] or 'Unknown Artist',
                        'genre': all_genres[0].strip(),  # Genre principal
                        'genres': [g.strip() for g in all_genres],  # Tous les genres
                        'file_path': row[12]
                    }
                    tracks.append(track)
                
                self.logger.info(f"📊 {len(tracks)} pistes chargées (dédupliquées)")

                # Charger liste d'exclusions persistante (IDs de pistes à exclure de la bank)
                try:
                    excl_path = Path(__file__).parent / 'excluded_tracks.json'
                    if 'PLEX_EXCLUDED_TRACKS' in os.environ:
                        env_path = os.environ.get('PLEX_EXCLUDED_TRACKS')
                        if env_path:
                            excl_path = Path(env_path)
                    if excl_path.exists():
                        raw = json.loads(excl_path.read_text(encoding='utf-8'))
                        if isinstance(raw, list):
                            excluded_ids = set(int(x) for x in raw if str(x).strip())
                        else:
                            excluded_ids = set()
                    else:
                        excluded_ids = set()
                except Exception:
                    excluded_ids = set()

                if excluded_ids:
                    before = len(tracks)
                    tracks = [t for t in tracks if int(t.get('id') or 0) not in excluded_ids]
                    self.logger.info(f"🛡️ Excluded {before - len(tracks)} tracks from bank via excluded_tracks.json")

                return tracks
                
        except Exception as e:
            self.logger.error(f"❌ Erreur base de données: {e}")
            return []

    def create_rating_playlists(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Crée des playlists par note (5★, 4★, etc.)"""
        rating_playlists = {}
        
        for rating in [5, 4, 3, 2, 1]:
            rated_tracks = [t for t in tracks if t['rating'] == rating * 2]  # Plex utilise 1-10
            if rated_tracks:
                playlist_name = f"{AUTO_PLAYLIST_PREFIX}⭐ {rating} étoiles ({len(rated_tracks)} titres)"
                rating_playlists[playlist_name] = rated_tracks
                
        return rating_playlists

    def create_year_playlists(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Crée des playlists par décennie/année"""
        year_playlists = {}
        
        # Par décennie
        decades = {}
        for track in tracks:
            if track['year'] and track['year'] > 0:
                decade = (track['year'] // 10) * 10
                if decade not in decades:
                    decades[decade] = []
                decades[decade].append(track)
        
        for decade, decade_tracks in decades.items():
            if len(decade_tracks) >= 10:  # Au moins 10 titres
                playlist_name = f"{AUTO_PLAYLIST_PREFIX}🕰️ Années {decade}s ({len(decade_tracks)} titres)"
                year_playlists[playlist_name] = decade_tracks
        
        # Années récentes (5 dernières années)
        current_year = datetime.datetime.now().year
        recent_tracks = [t for t in tracks if t['year'] and t['year'] >= current_year - 5]
        if recent_tracks:
            year_playlists[f"{AUTO_PLAYLIST_PREFIX}🆕 Récents ({len(recent_tracks)} titres)"] = recent_tracks
        
        return year_playlists

    def create_genre_playlists(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Crée des playlists de genre *curatées*.

        But: éviter les doublons peu utiles avec les filtres natifs de Plex.
        On ignore donc les genres trop larges et on ne garde que des sélections
        plus qualitatives sur les genres plus spécifiques.
        """
        genre_playlists = {}

        def _normalize_genre(value: str) -> str:
            return ' '.join((value or '').strip().lower().split())

        def _track_score(track: Dict) -> int:
            rating = int(track.get('rating') or 0)
            play_count = int(track.get('play_count') or 0)
            return (rating * 100) + play_count
        
        # Grouper par genre (une piste peut apparaître dans plusieurs genres)
        genres = {}
        for track in tracks:
            for genre in track.get('genres', [track['genre']]):
                genre = genre.strip()
                if genre and genre != 'Unknown':
                    if genre not in genres:
                        genres[genre] = []
                    genres[genre].append(track)
        
        # Créer uniquement des playlists de genres spécifiques et vraiment utiles.
        for genre, genre_tracks in sorted(genres.items()):
            normalized = _normalize_genre(genre)

            # Plex couvre déjà très bien les grands genres basiques.
            if normalized in GENERIC_PLEX_GENRES:
                continue

            # Éviter les tags trop vagues ou trop courts.
            if len(normalized) < 5:
                continue

            # Garder des pistes un minimum qualifiées, puis trier par affinité.
            curated_tracks = [
                track for track in genre_tracks
                if (track.get('rating') or 0) >= 6 or (track.get('play_count') or 0) >= 2
            ]
            curated_tracks.sort(key=_track_score, reverse=True)

            # Pas assez de matière => pas de playlist dédiée.
            if len(curated_tracks) < 20:
                continue

            selected_tracks = curated_tracks[:150]
            playlist_name = f"{AUTO_PLAYLIST_PREFIX}🎯 {genre} essentiels ({len(selected_tracks)} titres)"
            genre_playlists[playlist_name] = selected_tracks
        
        return genre_playlists

    def create_smart_playlists(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Crée des playlists intelligentes basées sur l'écoute"""
        smart_playlists = {}

        def _search_blob(track: Dict) -> str:
            parts = [
                track.get('title', ''),
                track.get('artist', ''),
                track.get('album', ''),
                track.get('genre', ''),
                ' '.join(track.get('genres', [])),
            ]
            return ' '.join(parts).lower()

        def _has_any_keyword(track: Dict, keywords: List[str], _cache={}) -> bool:
            blob = _search_blob(track)
            key = tuple(keywords)
            if key not in _cache:
                _cache[key] = re.compile('|'.join(r'\b' + re.escape(k) + r'\b' for k in keywords))
            return bool(_cache[key].search(blob))

        def _genres_contain(track: Dict, keywords: List[str]) -> bool:
            genres = [g.lower() for g in track.get('genres', [track.get('genre', '')])]
            return any(keyword in genre for genre in genres for keyword in keywords)

        def _score(track: Dict) -> tuple[int, int, int]:
            return (
                int(track.get('rating') or 0),
                int(track.get('play_count') or 0),
                int(track.get('duration_ms') or 0),
            )

        # Calibrage automatique global selon la qualité des métadonnées et
        # le niveau d'activité de la bibliothèque utilisateur.
        track_count = len(tracks)
        rated_count = sum(1 for t in tracks if int(t.get('rating') or 0) > 0)
        plays_non_zero = [int(t.get('play_count') or 0) for t in tracks if int(t.get('play_count') or 0) > 0]
        rated_ratio = (rated_count / track_count) if track_count else 0.0
        median_play_non_zero = sorted(plays_non_zero)[len(plays_non_zero) // 2] if plays_non_zero else 0
        rating_shift = -2 if rated_ratio < 0.15 else (-1 if rated_ratio < 0.30 else 0)
        play_shift = -2 if median_play_non_zero < 2 else (-1 if median_play_non_zero < 4 else 0)
        min_tracks_floor = 12 if track_count < 1200 else 20

        def _build_preset_playlist(
            name: str,
            include_keywords: List[str],
            *,
            exclude_keywords: Optional[List[str]] = None,
            min_duration_ms: int = 120000,
            max_duration_ms: int = 480000,
            min_rating: int = 5,
            min_play_count: int = 1,
            limit: int = 100,
            min_tracks: int = 20,
            max_per_artist: int = 0,
            fallback_min_rating: int = 5,
            fallback_min_play_count: int = 0,
        ) -> None:
            """Construit une playlist preset avec fallback et diversification artiste."""
            excludes = exclude_keywords or []
            effective_min_rating = max(0, min(10, int(min_rating) + rating_shift))
            effective_fallback_rating = max(0, min(10, int(fallback_min_rating) + rating_shift))
            effective_min_play_count = max(0, int(min_play_count) + play_shift)
            effective_fallback_play_count = max(0, int(fallback_min_play_count) + play_shift)
            effective_min_tracks = max(min_tracks_floor, int(min_tracks))

            def _match(track: Dict, rating_threshold: int, play_threshold: int) -> bool:
                duration = int(track.get('duration_ms') or 0)
                if duration < min_duration_ms or duration > max_duration_ms:
                    return False
                if (track.get('rating') or 0) < rating_threshold and (track.get('play_count') or 0) < play_threshold:
                    return False
                if not (_genres_contain(track, include_keywords) or _has_any_keyword(track, include_keywords)):
                    return False
                if excludes and (_genres_contain(track, excludes) or _has_any_keyword(track, excludes)):
                    return False
                return True

            selected = [t for t in tracks if _match(t, effective_min_rating, effective_min_play_count)]
            if len(selected) < effective_min_tracks:
                selected = [t for t in tracks if _match(t, effective_fallback_rating, effective_fallback_play_count)]

            selected.sort(key=_score, reverse=True)
            if max_per_artist > 0:
                diversified: List[Dict] = []
                by_artist: Dict[str, int] = {}
                for track in selected:
                    artist_name = str(track.get('artist') or 'Unknown Artist').strip()
                    count = by_artist.get(artist_name, 0)
                    if count >= max_per_artist:
                        continue
                    by_artist[artist_name] = count + 1
                    diversified.append(track)
                    if len(diversified) >= limit:
                        break
                selected = diversified
            else:
                selected = selected[:limit]

            if len(selected) >= effective_min_tracks:
                smart_playlists[f"{AUTO_PLAYLIST_PREFIX}{name} ({len(selected)} titres)"] = selected

        # Mots-clés de fallback par décennie (utilisés quand le tag year est vide)
        _decade_kw_defaults: Dict[int, List[str]] = {
            1980: ['80s', "80's", 'années 80', 'annees 80', '80er'],
            1990: ['90s', "90's", 'années 90', 'annees 90', '90er'],
            2000: ['2000s', "2000's", 'années 2000', 'annees 2000', '00s'],
            2010: ['2010s', "2010's", 'années 2010', 'annees 2010', '10s'],
        }

        def _build_decade_daily_mix(
            name: str,
            start_year: int,
            end_year: int,
            *,
            decade_keywords: Optional[List[str]] = None,
            limit: int = 120,
            min_tracks: int = 20,
            max_per_artist: int = 2,
        ) -> None:
            """Construit un Daily Mix ciblé sur une décennie.

            Utilise le tag year en priorité ; en fallback, cherche des mots-clés
            de la décennie (ex. '80s', 'années 90') dans titre/album/artiste/genres
            pour les pistes sans année renseignée.
            """
            decade = (start_year // 10) * 10
            kw = decade_keywords or _decade_kw_defaults.get(decade, [])

            def _track_matches_decade(t: Dict) -> bool:
                year = int(t.get('year') or 0)
                if year:
                    return start_year <= year <= end_year
                if not kw:
                    return False
                blob = ' '.join([
                    t.get('title', ''),
                    t.get('artist', ''),
                    t.get('album', ''),
                    ' '.join(t.get('genres', [t.get('genre', '')])),
                ]).lower()
                return any(k in blob for k in kw)

            selected = [
                t for t in tracks
                if _track_matches_decade(t)
                and 120000 <= int(t.get('duration_ms') or 0) <= 420000
                and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 1)
            ]
            selected.sort(key=lambda t: (
                int(t.get('rating') or 0),
                int(t.get('play_count') or 0),
                int(t.get('last_played') or t.get('date_added') or 0),
            ), reverse=True)

            diversified: List[Dict] = []
            by_artist: Dict[str, int] = {}
            for track in selected:
                artist_name = str(track.get('artist') or 'Unknown Artist').strip()
                count = by_artist.get(artist_name, 0)
                if max_per_artist > 0 and count >= max_per_artist:
                    continue
                by_artist[artist_name] = count + 1
                diversified.append(track)
                if len(diversified) >= limit:
                    break

            effective_min_tracks = max(min_tracks_floor, int(min_tracks))
            if len(diversified) >= effective_min_tracks:
                smart_playlists[f"{AUTO_PLAYLIST_PREFIX}{name} ({len(diversified)} titres)"] = diversified
        
        # Les plus écoutés
        most_played = sorted([t for t in tracks if t['play_count'] > 0], 
                            key=lambda x: x['play_count'], reverse=True)[:100]
        if most_played:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🔥 Top 100 plus écoutés"] = most_played
        
        # Favoris (5★ + beaucoup écoutés)
        favorites = [t for t in tracks if t['rating'] >= 8 and t['play_count'] >= 3]
        if favorites:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}❤️ Mes favoris ({len(favorites)} titres)"] = favorites
        
        # Découverte (peu/pas écoutés, bien notés)
        discovery = [t for t in tracks if t['play_count'] <= 1 and t['rating'] >= 6]
        if discovery:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🔍 À redécouvrir ({len(discovery)} titres)"] = discovery
        
        # Récemment ajoutés (30 derniers jours)
        now = int(time.time())
        thirty_days = 30 * 24 * 60 * 60
        recently_added = [t for t in tracks if t['date_added'] and 
                         (now - t['date_added']) <= thirty_days]
        if recently_added:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🆕 Ajoutés récemment ({len(recently_added)} titres)"] = recently_added
        
        # Mix de l'humeur - titres longs pour se concentrer
        long_tracks = [t for t in tracks if t['duration_ms'] >= 300000 and t['rating'] >= 6]  # >5min
        if long_tracks:
            random.shuffle(long_tracks)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🧘 Mix concentration ({len(long_tracks[:50])} titres)"] = long_tracks[:50]

        # Mix énergique - titres courts et bien notés
        energy_tracks = [t for t in tracks if t['duration_ms'] <= 240000 and t['rating'] >= 8]  # <4min
        if energy_tracks:
            random.shuffle(energy_tracks)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}⚡ Mix énergique ({len(energy_tracks[:50])} titres)"] = energy_tracks[:50]

        # Playlists randomisées pour toutes les ambiances, moods, focus, chill, etc.
        def _randomize_and_slice(tracks, n):
            random.shuffle(tracks)
            return tracks[:n]

        # 🎉 Soirée
        party_keywords = [
            'party', 'fiesta', 'dance', 'club', 'disco', 'funk', 'groove',
            'boogie', 'house', 'edm', 'electro', 'remix', 'soirée', 'soiree'
        ]
        party_tracks = [
            t for t in tracks
            if ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            and ((t.get('duration_ms') or 0) >= 150000)
            and (_genres_contain(t, party_keywords) or _has_any_keyword(t, party_keywords))
        ]
        if len(party_tracks) >= 25:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🎉 Soirée ({len(party_tracks[:120])} titres)"] = _randomize_and_slice(party_tracks, 120)

        # 🌃 Conduite de nuit
        night_keywords = [
            'night', 'midnight', 'moon', 'drive', 'road', 'highway', 'nocturne',
            'synth', 'dream', 'ambient', 'downtempo', 'trip-hop', 'chill'
        ]
        night_tracks = [
            t for t in tracks
            if (t.get('rating') or 0) >= 6
            and 180000 <= (t.get('duration_ms') or 0) <= 420000
            and (_genres_contain(t, ['synth', 'electronic', 'ambient', 'trip-hop', 'downtempo', 'new wave'])
                 or _has_any_keyword(t, night_keywords))
        ]
        if len(night_tracks) >= 20:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🌃 Conduite de nuit ({len(night_tracks[:80])} titres)"] = _randomize_and_slice(night_tracks, 80)

        # 🧠 Focus sans voix
        no_vocals_keywords = [
            'instrumental', 'ambient', 'classical', 'modern classical', 'soundtrack',
            'post-rock', 'drone', 'downtempo', 'piano', 'lofi', 'lo-fi', 'study'
        ]
        vocals_excluded_keywords = ['vocal', 'karaoke', 'feat.', 'featuring', 'version chant']
        focus_tracks = [
            t for t in tracks
            if (t.get('rating') or 0) >= 6
            and (t.get('duration_ms') or 0) >= 120000
            and (_genres_contain(t, no_vocals_keywords) or _has_any_keyword(t, no_vocals_keywords))
            and not _genres_contain(t, vocals_excluded_keywords)
            and not _has_any_keyword(t, vocals_excluded_keywords)
        ]
        if len(focus_tracks) >= 20:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🧠 Focus sans voix ({len(focus_tracks[:100])} titres)"] = _randomize_and_slice(focus_tracks, 100)

        # 💎 Redécouvertes notées
        rediscovery_tracks = [
            t for t in tracks
            if (t.get('rating') or 0) >= 8 and (t.get('play_count') or 0) <= 2
        ]
        if rediscovery_tracks:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}💎 Redécouvertes notées ({len(rediscovery_tracks[:80])} titres)"] = _randomize_and_slice(rediscovery_tracks, 80)

        # 🪐 Deep cuts favoris
        deep_cuts_tracks = [
            t for t in tracks
            if (t.get('rating') or 0) >= 8 and 1 <= (t.get('play_count') or 0) <= 8
        ]
        if len(deep_cuts_tracks) >= 20:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🪐 Deep cuts favoris ({len(deep_cuts_tracks[:100])} titres)"] = _randomize_and_slice(deep_cuts_tracks, 100)

        # 🛋️ Relaxation
        relax_keywords = [
            'chill', 'ambient', 'downtempo', 'lounge', 'acoustic', 'soft',
            'calm', 'piano', 'instrumental', 'neo soul', 'trip-hop', 'bossa',
            'jazz', 'smooth', 'relax'
        ]
        relaxation = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 150000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (
                _genres_contain(t, relax_keywords)
                or _has_any_keyword(t, relax_keywords)
            )
        ]
        if len(relaxation) < 20:
            relaxation = [
                t for t in tracks
                if (t.get('duration_ms') or 0) >= 150000
                and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            ]
        if len(relaxation) >= 20:
            selected = _randomize_and_slice(relaxation, 120)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🛋️ Relaxation ({len(selected)} titres)"] = selected

        # 🍽️ Repas entre amis
        dinner_keywords = [
            'soul', 'funk', 'groove', 'nu disco', 'disco', 'r&b', 'pop',
            'indie', 'latin', 'afro', 'bossa', 'chanson', 'jazz', 'lounge',
            'reggae', 'samba', 'friendly', 'dinner'
        ]
        dinner_exclude = ['metal', 'hardcore', 'death', 'black metal', 'grindcore']
        dinner = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 360000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            and (_genres_contain(t, dinner_keywords) or _has_any_keyword(t, dinner_keywords))
            and not (_genres_contain(t, dinner_exclude) or _has_any_keyword(t, dinner_exclude))
        ]
        if len(dinner) >= 20:
            selected = _randomize_and_slice(dinner, 120)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🍽️ Repas entre amis ({len(selected)} titres)"] = selected

        # 🍹 Mojito sunset (tighter keywords + exclusion list)
        sunset_keywords = [
            'sunset', 'summer', 'beach', 'tropical', 'balearic', 'deep house',
            'nu disco', 'lounge', 'chill', 'downtempo'
        ]
        sunset_exclude = ['rap', 'r&b', 'metal', 'hardcore', 'drill', 'trap', 'grime']
        sunset = [
            t for t in tracks
            if 160000 <= (t.get('duration_ms') or 0) <= 420000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, sunset_keywords) or _has_any_keyword(t, sunset_keywords))
            and not (_genres_contain(t, sunset_exclude) or _has_any_keyword(t, sunset_exclude))
        ]
        if len(sunset) < 20:
            sunset = [
                t for t in tracks
                if 160000 <= (t.get('duration_ms') or 0) <= 420000
                and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
                and (_genres_contain(t, ['deep house', 'lounge', 'nu disco', 'balearic', 'tropical'])
                     or _has_any_keyword(t, ['sun', 'summer', 'beach', 'chill', 'sunset']))
                and not (_genres_contain(t, sunset_exclude) or _has_any_keyword(t, sunset_exclude))
            ]
        if len(sunset) >= 20:
            selected = _randomize_and_slice(sunset, 100)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🍹 Mojito sunset ({len(selected)} titres)"] = selected

        # 🎊 Fête
        party_plus_keywords = [
            'party', 'dance', 'club', 'edm', 'electro', 'house', 'disco',
            'funk', 'hip-hop', 'rap', 'reggaeton', 'anthem', 'festival', 'remix'
        ]
        party_plus = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 150000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 4)
            and (_genres_contain(t, party_plus_keywords) or _has_any_keyword(t, party_plus_keywords))
        ]
        if len(party_plus) >= 20:
            selected = _randomize_and_slice(party_plus, 140)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🎊 Fête ({len(selected)} titres)"] = selected

        # 🚗 Road trip
        roadtrip_keywords = [
            'road', 'drive', 'highway', 'travel', 'anthem', 'indie', 'rock',
            'pop', 'electro', 'summer', 'route'
        ]
        roadtrip = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 360000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            and (_genres_contain(t, roadtrip_keywords) or _has_any_keyword(t, roadtrip_keywords))
        ]
        if len(roadtrip) >= 20:
            selected = _randomize_and_slice(roadtrip, 120)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🚗 Road trip ({len(selected)} titres)"] = selected

        # 🍎 Brunch weekend
        brunch_keywords = [
            'soul', 'funk', 'jazz', 'bossa', 'lounge', 'chill', 'acoustic',
            'neo soul', 'r&b', 'groove', 'brunch', 'weekend'
        ]
        brunch = [
            t for t in tracks
            if 140000 <= (t.get('duration_ms') or 0) <= 330000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, brunch_keywords) or _has_any_keyword(t, brunch_keywords))
        ]
        if len(brunch) >= 20:
            selected = _randomize_and_slice(brunch, 100)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🥐 Brunch weekend ({len(selected)} titres)"] = selected

        # 🍸 Afterwork chill
        afterwork_keywords = [
            'chill', 'downtempo', 'ambient', 'lounge', 'indie', 'pop',
            'deep house', 'nu disco', 'soft', 'afterwork', 'sunset'
        ]
        afterwork = [
            t for t in tracks
            if 160000 <= (t.get('duration_ms') or 0) <= 380000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, afterwork_keywords) or _has_any_keyword(t, afterwork_keywords))
        ]
        if len(afterwork) >= 20:
            selected = _randomize_and_slice(afterwork, 120)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🍸 Afterwork chill ({len(selected)} titres)"] = selected

        # 🌙 Nuit calme
        calm_night_keywords = [
            'ambient', 'piano', 'instrumental', 'classical', 'drone',
            'downtempo', 'trip-hop', 'nocturne', 'night', 'calm'
        ]
        calm_night = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 180000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 1)
            and (_genres_contain(t, calm_night_keywords) or _has_any_keyword(t, calm_night_keywords))
        ]
        if len(calm_night) >= 20:
            selected = _randomize_and_slice(calm_night, 100)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🌙 Nuit calme ({len(selected)} titres)"] = selected

        # 🏋️ Workout intense
        workout_keywords = [
            'workout', 'running', 'cardio', 'gym', 'edm', 'electro',
            'house', 'techno', 'drum and bass', 'trap', 'hip-hop', 'power'
        ]
        workout = [
            t for t in tracks
            if 130000 <= (t.get('duration_ms') or 0) <= 320000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 4)
            and (_genres_contain(t, workout_keywords) or _has_any_keyword(t, workout_keywords))
        ]
        if len(workout) >= 20:
            selected = _randomize_and_slice(workout, 120)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🏋️ Workout intense ({len(selected)} titres)"] = selected

        # 📚 Lecture & focus
        reading_keywords = [
            'instrumental', 'ambient', 'neo classical', 'classical', 'piano',
            'lofi', 'lo-fi', 'study', 'focus', 'soundtrack'
        ]
        reading = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 120000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 1)
            and (_genres_contain(t, reading_keywords) or _has_any_keyword(t, reading_keywords))
        ]
        if len(reading) >= 20:
            selected = _randomize_and_slice(reading, 120)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}📚 Lecture & focus ({len(selected)} titres)"] = selected

        # 🪩 Dancefloor rétro
        retro_party_keywords = [
            'disco', 'funk', '80s', 'synthwave', 'new wave', 'italo',
            'boogie', 'retro', 'dance'
        ]
        retro_party = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 360000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            and (_genres_contain(t, retro_party_keywords) or _has_any_keyword(t, retro_party_keywords))
        ]
        if len(retro_party) >= 20:
            selected = _randomize_and_slice(retro_party, 120)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🪩 Dancefloor rétro ({len(selected)} titres)"] = selected

        # 🌧️ Pluie du soir
        rain_keywords = [
            'rain', 'piano', 'ambient', 'acoustic', 'indie', 'sad',
            'melancholy', 'nocturne', 'slow jam', 'soir'
        ]
        rain_evening = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 150000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, rain_keywords) or _has_any_keyword(t, rain_keywords))
        ]
        if len(rain_evening) >= 20:
            selected = _randomize_and_slice(rain_evening, 100)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🌧️ Pluie du soir ({len(selected)} titres)"] = selected

        # ☀️ Matin good vibes
        morning_keywords = [
            'morning', 'sunrise', 'sunshine', 'feel good', 'pop', 'indie', 'funk',
            'soul', 'acoustic', 'happy', 'good vibes'
        ]
        morning = [
            t for t in tracks
            if 140000 <= (t.get('duration_ms') or 0) <= 320000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, morning_keywords) or _has_any_keyword(t, morning_keywords))
        ]
        if len(morning) >= 20:
            selected = _randomize_and_slice(morning, 100)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}☀️ Matin good vibes ({len(selected)} titres)"] = selected

        # 🍳 Cuisine en rythme
        cooking_keywords = [
            'groove', 'funk', 'soul', 'latin', 'samba', 'bossa', 'jazz',
            'lounge', 'chill', 'kitchen', 'cooking'
        ]
        cooking = [
            t for t in tracks
            if 140000 <= (t.get('duration_ms') or 0) <= 340000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, cooking_keywords) or _has_any_keyword(t, cooking_keywords))
        ]
        if len(cooking) >= 20:
            selected = _randomize_and_slice(cooking, 100)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🍳 Cuisine en rythme ({len(selected)} titres)"] = selected

        # 🧹 Ménage boost
        cleaning_keywords = [
            'dance', 'electro', 'house', 'pop', 'rock', 'workout', 'power',
            'energy', 'boost', 'remix'
        ]
        cleaning = [
            t for t in tracks
            if 130000 <= (t.get('duration_ms') or 0) <= 330000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 3)
            and (_genres_contain(t, cleaning_keywords) or _has_any_keyword(t, cleaning_keywords))
        ]
        if len(cleaning) >= 20:
            selected = _randomize_and_slice(cleaning, 120)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🧹 Ménage boost ({len(selected)} titres)"] = selected

        # 💻 Bureau sans stress
        office_keywords = [
            'ambient', 'instrumental', 'chill', 'lofi', 'lo-fi', 'piano',
            'downtempo', 'focus', 'study', 'soundtrack'
        ]
        office = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 120000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 1)
            and (_genres_contain(t, office_keywords) or _has_any_keyword(t, office_keywords))
        ]
        if len(office) >= 20:
            selected = _randomize_and_slice(office, 120)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}💻 Bureau sans stress ({len(selected)} titres)"] = selected

        # 🌅 Golden hour
        golden_hour_keywords = [
            'sunset', 'golden', 'chill', 'indie', 'deep house', 'lounge',
            'neo soul', 'ambient', 'evening'
        ]
        golden_hour = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 400000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, golden_hour_keywords) or _has_any_keyword(t, golden_hour_keywords))
        ]
        if len(golden_hour) >= 20:
            selected = _randomize_and_slice(golden_hour, 100)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🌅 Golden hour ({len(selected)} titres)"] = selected

        # 🚿 Douche énergique
        shower_keywords = [
            'pop', 'dance', 'electro', 'house', 'remix', 'hit', 'anthem',
            'energy', 'party'
        ]
        shower = [
            t for t in tracks
            if 120000 <= (t.get('duration_ms') or 0) <= 280000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            and (_genres_contain(t, shower_keywords) or _has_any_keyword(t, shower_keywords))
        ]
        if len(shower) >= 20:
            selected = _randomize_and_slice(shower, 80)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🚿 Douche énergique ({len(selected)} titres)"] = selected

        # ❤️ Slow love
        slow_love_keywords = [
            'love', 'ballad', 'soul', 'r&b', 'acoustic', 'piano',
            'romantic', 'slow jam', 'chanson', 'jazz'
        ]
        slow_love = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 420000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, slow_love_keywords) or _has_any_keyword(t, slow_love_keywords))
        ]
        if len(slow_love) >= 20:
            selected = _randomize_and_slice(slow_love, 100)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}❤️ Slow love ({len(selected)} titres)"] = selected

        # 🌍 World vibes
        world_keywords = [
            'world', 'afro', 'latin', 'reggae', 'samba', 'bossa',
            'raï', 'rai', 'oriental', 'cumbia', 'dancehall', 'tropical'
        ]
        world_vibes = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 420000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, world_keywords) or _has_any_keyword(t, world_keywords))
        ]
        if len(world_vibes) >= 20:
            selected = _randomize_and_slice(world_vibes, 120)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🌍 World vibes ({len(selected)} titres)"] = selected

        # Soirée - morceaux dansants/festifs avec un minimum de traction
        party_keywords = [
            'party', 'fiesta', 'dance', 'club', 'disco', 'funk', 'groove',
            'boogie', 'house', 'edm', 'electro', 'remix', 'soirée', 'soiree'
        ]
        party_tracks = [
            t for t in tracks
            if ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            and ((t.get('duration_ms') or 0) >= 150000)
            and (_genres_contain(t, party_keywords) or _has_any_keyword(t, party_keywords))
        ]
        party_tracks.sort(key=_score, reverse=True)
        if len(party_tracks) >= 25:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🎉 Soirée ({len(party_tracks[:120])} titres)"] = party_tracks[:120]

        # Conduite de nuit - morceaux plus atmosphériques, synth/electro/road vibes
        night_keywords = [
            'night', 'midnight', 'moon', 'drive', 'road', 'highway', 'nocturne',
            'synth', 'dream', 'ambient', 'downtempo', 'trip-hop', 'chill'
        ]
        night_tracks = [
            t for t in tracks
            if (t.get('rating') or 0) >= 6
            and 180000 <= (t.get('duration_ms') or 0) <= 420000
            and (_genres_contain(t, ['synth', 'electronic', 'ambient', 'trip-hop', 'downtempo', 'new wave'])
                 or _has_any_keyword(t, night_keywords))
        ]
        night_tracks.sort(key=_score, reverse=True)
        if len(night_tracks) >= 20:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🌃 Conduite de nuit ({len(night_tracks[:80])} titres)"] = night_tracks[:80]

        # Focus sans voix - instrumentaux/ambient/classique/jazz doux sans vocal explicite
        no_vocals_keywords = [
            'instrumental', 'ambient', 'classical', 'modern classical', 'soundtrack',
            'post-rock', 'drone', 'downtempo', 'piano', 'lofi', 'lo-fi', 'study'
        ]
        vocals_excluded_keywords = ['vocal', 'karaoke', 'feat.', 'featuring', 'version chant']
        focus_tracks = [
            t for t in tracks
            if (t.get('rating') or 0) >= 6
            and (t.get('duration_ms') or 0) >= 120000
            and (_genres_contain(t, no_vocals_keywords) or _has_any_keyword(t, no_vocals_keywords))
            and not _genres_contain(t, vocals_excluded_keywords)
            and not _has_any_keyword(t, vocals_excluded_keywords)
        ]
        focus_tracks.sort(key=_score, reverse=True)
        if len(focus_tracks) >= 20:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🧠 Focus sans voix ({len(focus_tracks[:100])} titres)"] = focus_tracks[:100]

        # Redécouvertes notées - bien notées mais très peu écoutées, plus strict que le simple redécouvrir
        rediscovery_tracks = [
            t for t in tracks
            if (t.get('rating') or 0) >= 8 and (t.get('play_count') or 0) <= 2
        ]
        rediscovery_tracks.sort(key=_score, reverse=True)
        if rediscovery_tracks:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}💎 Redécouvertes notées ({len(rediscovery_tracks[:80])} titres)"] = rediscovery_tracks[:80]

        # Deep cuts favoris - très bien notés mais peu exposés dans l'historique d'écoute
        deep_cuts_tracks = [
            t for t in tracks
            if (t.get('rating') or 0) >= 8 and 1 <= (t.get('play_count') or 0) <= 8
        ]
        deep_cuts_tracks.sort(key=_score, reverse=True)
        if len(deep_cuts_tracks) >= 20:
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🪐 Deep cuts favoris ({len(deep_cuts_tracks[:100])} titres)"] = deep_cuts_tracks[:100]

        # ADN bibliothèque - playlists intelligentes orientées usage réel.
        # Objectif: éviter les doublons de genres Plex et proposer des sélections
        # utiles pour (re)découvrir, consolider et varier l'écoute.
        now = int(time.time())
        forty_five_days = 45 * 24 * 60 * 60
        one_hundred_twenty_days = 120 * 24 * 60 * 60

        # 1) Sweet spot perso: titres bien notés, assez joués pour être validés,
        # mais pas surexposés.
        sweet_spot = [
            t for t in tracks
            if (t.get('rating') or 0) >= 7
            and 2 <= (t.get('play_count') or 0) <= 20
            and 150000 <= (t.get('duration_ms') or 0) <= 420000
        ]
        sweet_spot.sort(key=lambda t: (
            int(t.get('rating') or 0),
            int(t.get('play_count') or 0),
            int(t.get('last_played') or t.get('date_added') or 0),
        ), reverse=True)
        if len(sweet_spot) >= 20:
            selected = sweet_spot[:120]
            smart_playlists[
                f"{AUTO_PLAYLIST_PREFIX}🧬 Sweet spot perso ({len(selected)} titres)"
            ] = selected

        # 2) Rebond oublié: très bons titres oubliés depuis longtemps.
        forgotten_gems = [
            t for t in tracks
            if (t.get('rating') or 0) >= 7
            and (t.get('play_count') or 0) >= 3
            and (
                (t.get('last_played') and (now - t['last_played']) >= one_hundred_twenty_days)
                or (
                    not t.get('last_played')
                    and t.get('date_added')
                    and (now - t['date_added']) >= one_hundred_twenty_days
                )
            )
        ]
        if len(forgotten_gems) < 20:
            forgotten_gems = [
                t for t in tracks
                if (t.get('rating') or 0) >= 6
                and (t.get('play_count') or 0) >= 2
                and (
                    (t.get('last_played') and (now - t['last_played']) >= (90 * 24 * 60 * 60))
                    or (
                        not t.get('last_played')
                        and t.get('date_added')
                        and (now - t['date_added']) >= (120 * 24 * 60 * 60)
                    )
                )
            ]
        if len(forgotten_gems) < 20:
            forgotten_gems = [
                t for t in tracks
                if (t.get('rating') or 0) >= 6 and (t.get('play_count') or 0) >= 2
            ]
        forgotten_gems.sort(key=lambda t: (
            int(t.get('rating') or 0),
            -int(t.get('play_count') or 0),
            int(t.get('last_played') or 0),
        ), reverse=True)
        if len(forgotten_gems) >= 20:
            selected = forgotten_gems[:100]
            smart_playlists[
                f"{AUTO_PLAYLIST_PREFIX}🧬 Rebond oublié ({len(selected)} titres)"
            ] = selected

        # 3) Nouvelles prometteuses: ajout récent avec signaux positifs.
        rising_new = [
            t for t in tracks
            if t.get('date_added')
            and (now - t['date_added']) <= forty_five_days
            and (t.get('rating') or 0) >= 6
            and (t.get('play_count') or 0) <= 5
        ]
        if len(rising_new) < 15:
            rising_new = [
                t for t in tracks
                if t.get('date_added')
                and (now - t['date_added']) <= (90 * 24 * 60 * 60)
                and (t.get('rating') or 0) >= 5
                and (t.get('play_count') or 0) <= 8
            ]
        rising_new.sort(key=lambda t: (
            int(t.get('rating') or 0),
            int(t.get('play_count') or 0),
            int(t.get('date_added') or 0),
        ), reverse=True)
        if len(rising_new) >= 15:
            selected = rising_new[:100]
            smart_playlists[
                f"{AUTO_PLAYLIST_PREFIX}🧬 Nouvelles prometteuses ({len(selected)} titres)"
            ] = selected

        # 4) Albums pépites: albums avec plusieurs morceaux très bien notés,
        # mais encore sous-exploités en nombre d'écoutes.
        album_stats: Dict[str, Dict[str, Any]] = {}
        for track in tracks:
            album_name = str(track.get('album') or '').strip()
            if not album_name or album_name.lower() == 'unknown album':
                continue
            stat = album_stats.setdefault(album_name, {
                'tracks': [],
                'liked_count': 0,
                'total_plays': 0,
            })
            stat['tracks'].append(track)
            stat['total_plays'] += int(track.get('play_count') or 0)
            if int(track.get('rating') or 0) >= 8:
                stat['liked_count'] += 1

        selected_albums = {
            album_name for album_name, stat in album_stats.items()
            if len(stat['tracks']) >= 3
            and stat['liked_count'] >= 2
            and stat['total_plays'] <= max(45, len(stat['tracks']) * 8)
        }
        album_gems = [
            t for t in tracks
            if str(t.get('album') or '').strip() in selected_albums
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 1)
        ]
        album_gems.sort(key=_score, reverse=True)
        if len(album_gems) >= 20:
            selected = album_gems[:120]
            smart_playlists[
                f"{AUTO_PLAYLIST_PREFIX}🧬 Albums pépites ({len(selected)} titres)"
            ] = selected

        # 5) Artistes à creuser: artistes plutôt bien notés mais peu explorés.
        artist_stats: Dict[str, Dict[str, Any]] = {}
        for track in tracks:
            artist_name = str(track.get('artist') or '').strip()
            if not artist_name or artist_name.lower() == 'unknown artist':
                continue
            stat = artist_stats.setdefault(artist_name, {
                'count': 0,
                'sum_rating': 0,
                'total_plays': 0,
            })
            stat['count'] += 1
            stat['sum_rating'] += int(track.get('rating') or 0)
            stat['total_plays'] += int(track.get('play_count') or 0)

        artists_to_dig = {
            artist_name for artist_name, stat in artist_stats.items()
            if stat['count'] >= 4
            and (stat['sum_rating'] / max(1, stat['count'])) >= 6
            and stat['total_plays'] <= stat['count'] * 6
        }
        if len(artists_to_dig) < 5:
            artists_to_dig = {
                artist_name for artist_name, stat in artist_stats.items()
                if stat['count'] >= 3
                and (stat['sum_rating'] / max(1, stat['count'])) >= 5.5
                and stat['total_plays'] <= stat['count'] * 10
            }
        artist_digs = [
            t for t in tracks
            if str(t.get('artist') or '').strip() in artists_to_dig
            and (t.get('rating') or 0) >= 5
            and (t.get('play_count') or 0) <= 12
        ]
        artist_digs.sort(key=_score, reverse=True)
        if len(artist_digs) >= 20:
            selected = artist_digs[:120]
            smart_playlists[
                f"{AUTO_PLAYLIST_PREFIX}🧬 Artistes à creuser ({len(selected)} titres)"
            ] = selected

        # 6) Diversité intelligente: garder la qualité mais limiter la répétition
        # d'un même artiste pour une écoute plus variée.
        diverse_candidates = [
            t for t in tracks
            if (t.get('rating') or 0) >= 6 and (t.get('play_count') or 0) >= 1
        ]
        diverse_candidates.sort(key=_score, reverse=True)
        by_artist: Dict[str, int] = {}
        diverse_selected: List[Dict] = []
        for track in diverse_candidates:
            artist_name = str(track.get('artist') or 'Unknown Artist').strip()
            current = by_artist.get(artist_name, 0)
            if current >= 2:
                continue
            by_artist[artist_name] = current + 1
            diverse_selected.append(track)
            if len(diverse_selected) >= 120:
                break

        if len(diverse_selected) >= 20:
            smart_playlists[
                f"{AUTO_PLAYLIST_PREFIX}🧬 Diversité intelligente ({len(diverse_selected)} titres)"
            ] = diverse_selected

        # 7) Relaxation - ambiance douce, tempos modérés, écoute confortable.
        relax_keywords = [
            'chill', 'ambient', 'downtempo', 'lounge', 'acoustic', 'soft',
            'calm', 'piano', 'instrumental', 'neo soul', 'trip-hop', 'bossa',
            'jazz', 'smooth', 'relax'
        ]
        relaxation = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 150000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (
                _genres_contain(t, relax_keywords)
                or _has_any_keyword(t, relax_keywords)
            )
        ]
        if len(relaxation) < 20:
            relaxation = [
                t for t in tracks
                if (t.get('duration_ms') or 0) >= 150000
                and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            ]
        relaxation.sort(key=_score, reverse=True)
        if len(relaxation) >= 20:
            selected = relaxation[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🛋️ Relaxation ({len(selected)} titres)"] = selected

        # 8) Repas entre amis - feel-good varié, pas trop agressif.
        dinner_keywords = [
            'soul', 'funk', 'groove', 'nu disco', 'disco', 'r&b', 'pop',
            'indie', 'latin', 'afro', 'bossa', 'chanson', 'jazz', 'lounge',
            'reggae', 'samba', 'friendly', 'dinner'
        ]
        dinner_exclude = ['metal', 'hardcore', 'death', 'black metal', 'grindcore']
        dinner = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 360000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            and (_genres_contain(t, dinner_keywords) or _has_any_keyword(t, dinner_keywords))
            and not (_genres_contain(t, dinner_exclude) or _has_any_keyword(t, dinner_exclude))
        ]
        dinner.sort(key=_score, reverse=True)
        if len(dinner) >= 20:
            selected = dinner[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🍽️ Repas entre amis ({len(selected)} titres)"] = selected

        # 9) Mojito sunset - mood warm/chill tropical/electro doux.
        sunset_keywords = [
            'sunset', 'summer', 'beach', 'tropical', 'balearic', 'house',
            'deep house', 'nu disco', 'latin', 'reggaeton', 'afro', 'chill',
            'lounge', 'ibiza', 'mojito', 'cocktail'
        ]
        sunset = [
            t for t in tracks
            if 160000 <= (t.get('duration_ms') or 0) <= 420000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, sunset_keywords) or _has_any_keyword(t, sunset_keywords))
        ]
        if len(sunset) < 20:
            sunset = [
                t for t in tracks
                if 160000 <= (t.get('duration_ms') or 0) <= 420000
                and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
                and (_genres_contain(t, ['house', 'electronic', 'latin', 'lounge', 'pop'])
                     or _has_any_keyword(t, ['sun', 'summer', 'beach', 'chill']))
            ]
        if len(sunset) >= 20:
            selected = sunset[:100]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🍹 Mojito sunset ({len(selected)} titres)"] = selected

        # 10) Fête - énergie haute, titres fédérateurs et dansants.
        party_plus_keywords = [
            'party', 'dance', 'club', 'edm', 'electro', 'house', 'disco',
            'funk', 'hip-hop', 'rap', 'reggaeton', 'anthem', 'festival', 'remix'
        ]
        party_plus = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 150000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 4)
            and (_genres_contain(t, party_plus_keywords) or _has_any_keyword(t, party_plus_keywords))
        ]
        party_plus.sort(key=_score, reverse=True)
        if len(party_plus) >= 20:
            selected = party_plus[:140]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🎊 Fête ({len(selected)} titres)"] = selected

        # 11) Road trip - énergie stable et titres fédérateurs.
        roadtrip_keywords = [
            'road', 'drive', 'highway', 'travel', 'anthem', 'indie', 'rock',
            'pop', 'electro', 'summer', 'route'
        ]
        roadtrip = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 360000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            and (_genres_contain(t, roadtrip_keywords) or _has_any_keyword(t, roadtrip_keywords))
        ]
        roadtrip.sort(key=_score, reverse=True)
        if len(roadtrip) >= 20:
            selected = roadtrip[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🚗 Road trip ({len(selected)} titres)"] = selected

        # 12) Brunch weekend - ambiance solaire, groove léger.
        brunch_keywords = [
            'soul', 'funk', 'jazz', 'bossa', 'lounge', 'chill', 'acoustic',
            'neo soul', 'r&b', 'groove', 'brunch', 'weekend'
        ]
        brunch = [
            t for t in tracks
            if 140000 <= (t.get('duration_ms') or 0) <= 330000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, brunch_keywords) or _has_any_keyword(t, brunch_keywords))
        ]
        brunch.sort(key=_score, reverse=True)
        if len(brunch) >= 20:
            selected = brunch[:100]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🥐 Brunch weekend ({len(selected)} titres)"] = selected

        # 13) Afterwork chill - transition douce fin de journée.
        afterwork_keywords = [
            'chill', 'downtempo', 'ambient', 'lounge', 'indie', 'pop',
            'deep house', 'nu disco', 'soft', 'afterwork', 'sunset'
        ]
        afterwork = [
            t for t in tracks
            if 160000 <= (t.get('duration_ms') or 0) <= 380000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, afterwork_keywords) or _has_any_keyword(t, afterwork_keywords))
        ]
        afterwork.sort(key=_score, reverse=True)
        if len(afterwork) >= 20:
            selected = afterwork[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🍸 Afterwork chill ({len(selected)} titres)"] = selected

        # 14) Nuit calme - écouter tard sans casser l'ambiance.
        calm_night_keywords = [
            'ambient', 'piano', 'instrumental', 'classical', 'drone',
            'downtempo', 'trip-hop', 'nocturne', 'night', 'calm'
        ]
        calm_night = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 180000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 1)
            and (_genres_contain(t, calm_night_keywords) or _has_any_keyword(t, calm_night_keywords))
        ]
        calm_night.sort(key=_score, reverse=True)
        if len(calm_night) >= 20:
            selected = calm_night[:100]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🌙 Nuit calme ({len(selected)} titres)"] = selected

        # 15) Workout intense - haut impact, haut tempo perçu.
        workout_keywords = [
            'workout', 'running', 'cardio', 'gym', 'edm', 'electro',
            'house', 'techno', 'drum and bass', 'trap', 'hip-hop', 'power'
        ]
        workout = [
            t for t in tracks
            if 130000 <= (t.get('duration_ms') or 0) <= 320000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 4)
            and (_genres_contain(t, workout_keywords) or _has_any_keyword(t, workout_keywords))
        ]
        workout.sort(key=_score, reverse=True)
        if len(workout) >= 20:
            selected = workout[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🏋️ Workout intense ({len(selected)} titres)"] = selected

        # 16) Lecture & focus - peu intrusive, stable, répétable.
        reading_keywords = [
            'instrumental', 'ambient', 'neo classical', 'classical', 'piano',
            'lofi', 'lo-fi', 'study', 'focus', 'soundtrack'
        ]
        reading = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 120000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 1)
            and (_genres_contain(t, reading_keywords) or _has_any_keyword(t, reading_keywords))
        ]
        reading.sort(key=_score, reverse=True)
        if len(reading) >= 20:
            selected = reading[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}📚 Lecture & focus ({len(selected)} titres)"] = selected

        # 17) Dancefloor rétro - sélections festives old-school.
        retro_party_keywords = [
            'disco', 'funk', '80s', 'synthwave', 'new wave', 'italo',
            'boogie', 'retro', 'dance'
        ]
        retro_party = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 360000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            and (_genres_contain(t, retro_party_keywords) or _has_any_keyword(t, retro_party_keywords))
        ]
        retro_party.sort(key=_score, reverse=True)
        if len(retro_party) >= 20:
            selected = retro_party[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🪩 Dancefloor rétro ({len(selected)} titres)"] = selected

        # 18) Pluie du soir - mood introspectif doux.
        rain_keywords = [
            'rain', 'piano', 'ambient', 'acoustic', 'indie', 'sad',
            'melancholy', 'nocturne', 'slow jam', 'soir'
        ]
        rain_evening = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 150000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, rain_keywords) or _has_any_keyword(t, rain_keywords))
        ]
        rain_evening.sort(key=_score, reverse=True)
        if len(rain_evening) >= 20:
            selected = rain_evening[:100]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🌧️ Pluie du soir ({len(selected)} titres)"] = selected

        # 19) Matin good vibes - lancer la journée avec énergie positive.
        morning_keywords = [
            'morning', 'sunrise', 'sunshine', 'feel good', 'pop', 'indie', 'funk',
            'soul', 'acoustic', 'happy', 'good vibes'
        ]
        morning = [
            t for t in tracks
            if 140000 <= (t.get('duration_ms') or 0) <= 320000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, morning_keywords) or _has_any_keyword(t, morning_keywords))
        ]
        morning.sort(key=_score, reverse=True)
        if len(morning) >= 20:
            selected = morning[:100]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}☀️ Matin good vibes ({len(selected)} titres)"] = selected

        # 20) Cuisine en rythme - groove léger, convivial.
        cooking_keywords = [
            'groove', 'funk', 'soul', 'latin', 'samba', 'bossa', 'jazz',
            'lounge', 'chill', 'kitchen', 'cooking'
        ]
        cooking = [
            t for t in tracks
            if 140000 <= (t.get('duration_ms') or 0) <= 340000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, cooking_keywords) or _has_any_keyword(t, cooking_keywords))
        ]
        cooking.sort(key=_score, reverse=True)
        if len(cooking) >= 20:
            selected = cooking[:100]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🍳 Cuisine en rythme ({len(selected)} titres)"] = selected

        # 21) Ménage boost - tempo soutenu et tracks efficaces.
        cleaning_keywords = [
            'dance', 'electro', 'house', 'pop', 'rock', 'workout', 'power',
            'energy', 'boost', 'remix'
        ]
        cleaning = [
            t for t in tracks
            if 130000 <= (t.get('duration_ms') or 0) <= 330000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 3)
            and (_genres_contain(t, cleaning_keywords) or _has_any_keyword(t, cleaning_keywords))
        ]
        cleaning.sort(key=_score, reverse=True)
        if len(cleaning) >= 20:
            selected = cleaning[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🧹 Ménage boost ({len(selected)} titres)"] = selected

        # 22) Bureau sans stress - concentration douce sans agressivité.
        office_keywords = [
            'ambient', 'instrumental', 'chill', 'lofi', 'lo-fi', 'piano',
            'downtempo', 'focus', 'study', 'soundtrack'
        ]
        office = [
            t for t in tracks
            if (t.get('duration_ms') or 0) >= 120000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 1)
            and (_genres_contain(t, office_keywords) or _has_any_keyword(t, office_keywords))
        ]
        office.sort(key=_score, reverse=True)
        if len(office) >= 20:
            selected = office[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}💻 Bureau sans stress ({len(selected)} titres)"] = selected

        # 23) Golden hour - fin de journée chaude et mélodique.
        golden_hour_keywords = [
            'sunset', 'golden', 'chill', 'indie', 'deep house', 'lounge',
            'neo soul', 'ambient', 'evening'
        ]
        golden_hour = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 400000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, golden_hour_keywords) or _has_any_keyword(t, golden_hour_keywords))
        ]
        golden_hour.sort(key=_score, reverse=True)
        if len(golden_hour) >= 20:
            selected = golden_hour[:100]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🌅 Golden hour ({len(selected)} titres)"] = selected

        # 24) Douche énergique - courtes rafales dynamiques.
        shower_keywords = [
            'pop', 'dance', 'electro', 'house', 'remix', 'hit', 'anthem',
            'energy', 'party'
        ]
        shower = [
            t for t in tracks
            if 120000 <= (t.get('duration_ms') or 0) <= 280000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 3)
            and (_genres_contain(t, shower_keywords) or _has_any_keyword(t, shower_keywords))
        ]
        shower.sort(key=_score, reverse=True)
        if len(shower) >= 20:
            selected = shower[:80]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🚿 Douche énergique ({len(selected)} titres)"] = selected

        # 25) Slow love - ambiance romantique et douce.
        slow_love_keywords = [
            'love', 'ballad', 'soul', 'r&b', 'acoustic', 'piano',
            'romantic', 'slow jam', 'chanson', 'jazz'
        ]
        slow_love = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 420000
            and ((t.get('rating') or 0) >= 6 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, slow_love_keywords) or _has_any_keyword(t, slow_love_keywords))
        ]
        slow_love.sort(key=_score, reverse=True)
        if len(slow_love) >= 20:
            selected = slow_love[:100]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}❤️ Slow love ({len(selected)} titres)"] = selected

        # 26) World vibes - ouverture internationale groovy/chill.
        world_keywords = [
            'world', 'afro', 'latin', 'reggae', 'samba', 'bossa',
            'raï', 'rai', 'oriental', 'cumbia', 'dancehall', 'tropical'
        ]
        world_vibes = [
            t for t in tracks
            if 150000 <= (t.get('duration_ms') or 0) <= 420000
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 2)
            and (_genres_contain(t, world_keywords) or _has_any_keyword(t, world_keywords))
        ]
        world_vibes.sort(key=_score, reverse=True)
        if len(world_vibes) >= 20:
            selected = world_vibes[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🌍 World vibes ({len(selected)} titres)"] = selected

        # 27) Packs type plateformes streaming: mixes quotidiens, radar, rewind.
        sixty_days = 60 * 24 * 60 * 60
        one_year = 365 * 24 * 60 * 60

        release_radar = [
            t for t in tracks
            if t.get('date_added')
            and (now - int(t.get('date_added') or 0)) <= sixty_days
            and ((t.get('rating') or 0) >= 5 or (t.get('play_count') or 0) >= 1)
        ]
        release_radar.sort(key=lambda t: (
            int(t.get('date_added') or 0),
            int(t.get('rating') or 0),
            int(t.get('play_count') or 0),
        ), reverse=True)
        if len(release_radar) >= 12:
            selected = release_radar[:150]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🛰️ Release radar perso ({len(selected)} titres)"] = selected

        discover_weekly = [
            t for t in tracks
            if (t.get('play_count') or 0) <= 2
            and ((t.get('rating') or 0) >= 6 or (t.get('date_added') and (now - int(t.get('date_added') or 0)) <= one_year))
        ]
        discover_weekly.sort(key=lambda t: (
            int(t.get('rating') or 0),
            int(t.get('date_added') or 0),
            -int(t.get('play_count') or 0),
        ), reverse=True)
        if len(discover_weekly) >= 20:
            selected = discover_weekly[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🧪 Découvertes de la semaine ({len(selected)} titres)"] = selected

        repeat_rewind = [
            t for t in tracks
            if (t.get('rating') or 0) >= 6
            and (t.get('play_count') or 0) >= 6
            and t.get('last_played')
            and (now - int(t.get('last_played') or 0)) <= one_hundred_twenty_days
        ]
        repeat_rewind.sort(key=lambda t: (
            int(t.get('play_count') or 0),
            int(t.get('rating') or 0),
            int(t.get('last_played') or 0),
        ), reverse=True)
        if len(repeat_rewind) < 20:
            repeat_rewind = [
                t for t in tracks
                if (t.get('rating') or 0) >= 5
                and (t.get('play_count') or 0) >= 4
                and t.get('last_played')
                and (now - int(t.get('last_played') or 0)) <= (180 * 24 * 60 * 60)
            ]
            repeat_rewind.sort(key=lambda t: (
                int(t.get('play_count') or 0),
                int(t.get('rating') or 0),
                int(t.get('last_played') or 0),
            ), reverse=True)
        if len(repeat_rewind) >= 15:
            selected = repeat_rewind[:120]
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🔁 Repeat rewind ({len(selected)} titres)"] = selected

        _build_preset_playlist(
            '🎯 Daily mix energie',
            ['dance', 'electro', 'house', 'hip-hop', 'pop', 'running', 'workout', 'power'],
            min_duration_ms=120000,
            max_duration_ms=320000,
            min_rating=6,
            min_play_count=2,
            limit=120,
            min_tracks=20,
            max_per_artist=2,
            fallback_min_rating=5,
            fallback_min_play_count=1,
        )

        _build_preset_playlist(
            '😌 Daily mix chill',
            ['chill', 'lofi', 'ambient', 'acoustic', 'lounge', 'downtempo', 'neo soul'],
            min_duration_ms=140000,
            max_duration_ms=420000,
            min_rating=5,
            min_play_count=1,
            limit=120,
            min_tracks=20,
            max_per_artist=2,
            fallback_min_rating=4,
            fallback_min_play_count=0,
        )

        _build_preset_playlist(
            '🌃 Daily mix night drive',
            ['night', 'drive', 'synthwave', 'electronic', 'trip-hop', 'deep house', 'indie'],
            min_duration_ms=160000,
            max_duration_ms=420000,
            min_rating=5,
            min_play_count=1,
            limit=120,
            min_tracks=20,
            max_per_artist=2,
            fallback_min_rating=4,
            fallback_min_play_count=0,
        )

        # Daily Mix par décennie
        _build_decade_daily_mix('🪩 Daily Mix 80s', 1980, 1989)
        _build_decade_daily_mix('📼 Daily Mix 90s', 1990, 1999)
        _build_decade_daily_mix('💿 Daily Mix 2000s', 2000, 2009)
        _build_decade_daily_mix('📱 Daily Mix 2010s', 2010, 2019)

        # Mix humeur avancés
        _build_preset_playlist(
            '😊 Mix humeur happy',
            ['happy', 'good vibes', 'feel good', 'sunny', 'upbeat', 'joy', 'funk', 'disco'],
            exclude_keywords=['sad', 'melancholy', 'dark', 'doom'],
            min_duration_ms=120000,
            max_duration_ms=360000,
            min_rating=5,
            min_play_count=1,
            limit=120,
            min_tracks=20,
            max_per_artist=2,
            fallback_min_rating=4,
            fallback_min_play_count=0,
        )

        _build_preset_playlist(
            '🥀 Mix humeur sad',
            ['sad', 'melancholy', 'heartbreak', 'blues', 'acoustic', 'ballad', 'piano', 'nostalgia'],
            exclude_keywords=['party', 'workout', 'hardcore'],
            min_duration_ms=140000,
            max_duration_ms=460000,
            min_rating=5,
            min_play_count=0,
            limit=120,
            min_tracks=20,
            max_per_artist=2,
            fallback_min_rating=4,
            fallback_min_play_count=0,
        )

        _build_preset_playlist(
            '🔥 Mix humeur intense',
            ['intense', 'power', 'workout', 'metal', 'hard rock', 'drum and bass', 'electro'],
            min_duration_ms=110000,
            max_duration_ms=360000,
            min_rating=6,
            min_play_count=2,
            limit=120,
            min_tracks=20,
            max_per_artist=2,
            fallback_min_rating=5,
            fallback_min_play_count=1,
        )

        _build_preset_playlist(
            '💋 Mix humeur sensuel',
            ['sensual', 'sensuel', 'r&b', 'neo soul', 'slow jam', 'intimate', 'velvet'],
            exclude_keywords=['hardcore', 'metal', 'workout'],
            min_duration_ms=150000,
            max_duration_ms=460000,
            min_rating=5,
            min_play_count=1,
            limit=100,
            min_tracks=20,
            max_per_artist=2,
            fallback_min_rating=4,
            fallback_min_play_count=0,
        )

        _build_preset_playlist(
            '🧠 Mix humeur deep focus',
            ['deep focus', 'focus', 'study', 'instrumental', 'ambient', 'minimal', 'piano', 'classical'],
            exclude_keywords=['vocal', 'karaoke', 'feat.', 'featuring'],
            min_duration_ms=140000,
            max_duration_ms=520000,
            min_rating=5,
            min_play_count=0,
            limit=140,
            min_tracks=20,
            max_per_artist=3,
            fallback_min_rating=4,
            fallback_min_play_count=0,
        )

        _build_preset_playlist(
            '🌙 Mix humeur sleep',
            ['sleep', 'night', 'calm', 'ambient', 'meditation', 'lullaby', 'soft', 'dream'],
            exclude_keywords=['workout', 'intense', 'hardcore', 'party'],
            min_duration_ms=150000,
            max_duration_ms=600000,
            min_rating=4,
            min_play_count=0,
            limit=140,
            min_tracks=20,
            max_per_artist=3,
            fallback_min_rating=3,
            fallback_min_play_count=0,
        )

        # 28+) Banque premium: presets supplémentaires avec fallback intelligent.
        premium_presets = [
            {
                'name': '🌆 Terrasse chill',
                'include': ['chill', 'lounge', 'nu disco', 'deep house', 'sunset', 'indie', 'soul'],
                'exclude': ['grindcore', 'black metal', 'death metal'],
                'min_d': 150000,
                'max_d': 420000,
                'min_r': 5,
                'min_p': 1,
                'limit': 120,
                'min_t': 20,
                'max_artist': 2,
            },
            {
                'name': '🌌 Late night electronic',
                'include': ['electronic', 'ambient', 'downtempo', 'trip-hop', 'synth', 'deep house', 'indie'],
                'min_d': 170000,
                'max_d': 480000,
                'min_r': 6,
                'min_p': 1,
                'limit': 110,
                'min_t': 20,
                'max_artist': 2,
            },
            {
                'name': '🕯️ Diner romantique',
                'include': ['ballad', 'soul', 'r&b', 'jazz', 'acoustic', 'piano', 'chanson', 'romantic'],
                'exclude': ['hardcore', 'metal'],
                'min_d': 150000,
                'max_d': 420000,
                'min_r': 6,
                'min_p': 1,
                'limit': 100,
                'min_t': 20,
                'max_artist': 2,
            },
            {
                'name': '🎮 Gaming focus',
                'include': ['electro', 'synthwave', 'instrumental', 'soundtrack', 'ambient', 'lofi'],
                'min_d': 120000,
                'max_d': 420000,
                'min_r': 5,
                'min_p': 0,
                'limit': 130,
                'min_t': 20,
                'max_artist': 3,
                'fallback_r': 4,
            },
            {
                'name': '🧾 Deep work sans parole',
                'include': ['instrumental', 'ambient', 'classical', 'neo classical', 'piano', 'study'],
                'exclude': ['vocal', 'karaoke', 'feat.', 'featuring'],
                'min_d': 140000,
                'max_d': 500000,
                'min_r': 5,
                'min_p': 0,
                'limit': 140,
                'min_t': 20,
                'max_artist': 3,
                'fallback_r': 4,
            },
            {
                'name': '🧼 Dimanche reset',
                'include': ['chill', 'acoustic', 'indie', 'soul', 'jazz', 'downtempo', 'folk'],
                'min_d': 140000,
                'max_d': 420000,
                'min_r': 5,
                'min_p': 1,
                'limit': 120,
                'min_t': 20,
                'max_artist': 2,
            },
            {
                'name': '🧳 Evasion urbaine',
                'include': ['indie', 'electro', 'trip-hop', 'world', 'latin', 'afro', 'city', 'urban'],
                'min_d': 140000,
                'max_d': 420000,
                'min_r': 5,
                'min_p': 1,
                'limit': 120,
                'min_t': 20,
                'max_artist': 2,
            },
            {
                'name': '🏖️ Pool party',
                'include': ['summer', 'house', 'dance', 'latin', 'tropical', 'reggaeton', 'party'],
                'min_d': 140000,
                'max_d': 360000,
                'min_r': 5,
                'min_p': 2,
                'limit': 120,
                'min_t': 20,
                'max_artist': 2,
                'fallback_p': 1,
            },
            {
                'name': '🚦 Before going out',
                'include': ['dance', 'house', 'electro', 'pop', 'hip-hop', 'party', 'remix'],
                'min_d': 130000,
                'max_d': 320000,
                'min_r': 6,
                'min_p': 2,
                'limit': 100,
                'min_t': 20,
                'max_artist': 2,
                'fallback_r': 5,
            },
            {
                'name': '👟 Marche active',
                'include': ['pop', 'indie', 'electro', 'funk', 'hip-hop', 'walking', 'groove'],
                'min_d': 140000,
                'max_d': 360000,
                'min_r': 5,
                'min_p': 2,
                'limit': 120,
                'min_t': 20,
                'max_artist': 2,
            },
            {
                'name': '🎤 Karaoke entre amis',
                'include': ['pop', 'rock', 'chanson', 'hit', 'anthem', 'karaoke'],
                'min_d': 150000,
                'max_d': 420000,
                'min_r': 5,
                'min_p': 2,
                'limit': 120,
                'min_t': 20,
                'max_artist': 2,
            },
            {
                'name': '🕺 Hits sans pause',
                'include': ['hit', 'pop', 'dance', 'electro', 'r&b', 'rap', 'remix'],
                'min_d': 120000,
                'max_d': 300000,
                'min_r': 6,
                'min_p': 3,
                'limit': 140,
                'min_t': 20,
                'max_artist': 2,
                'fallback_r': 5,
            },
            {
                'name': '🏃 Running',
                'include': ['running', 'cardio', 'workout', 'power', 'edm', 'electro', 'drum and bass', 'hip-hop'],
                'min_d': 120000,
                'max_d': 320000,
                'min_r': 6,
                'min_p': 3,
                'limit': 140,
                'min_t': 20,
                'max_artist': 2,
                'fallback_r': 5,
                'fallback_p': 2,
            },
            {
                'name': '🏋️ Workout performance',
                'include': ['workout', 'gym', 'training', 'cardio', 'crossfit', 'power', 'edm', 'electro', 'house', 'hip-hop'],
                'min_d': 120000,
                'max_d': 320000,
                'min_r': 5,
                'min_p': 2,
                'limit': 140,
                'min_t': 20,
                'max_artist': 2,
                'fallback_r': 4,
                'fallback_p': 1,
            },
            {
                'name': '🧘 Yoga',
                'include': ['yoga', 'meditation', 'ambient', 'piano', 'instrumental', 'new age', 'calm', 'healing', 'zen'],
                'exclude': ['metal', 'hardcore', 'grindcore'],
                'min_d': 170000,
                'max_d': 600000,
                'min_r': 5,
                'min_p': 0,
                'limit': 120,
                'min_t': 20,
                'max_artist': 3,
                'fallback_r': 4,
            },
            {
                'name': '💆 Session massage tantrique',
                'include': ['massage', 'tantra', 'tantric', 'sensual', 'downtempo', 'ambient', 'meditation', 'healing', 'slow jam', 'tibetan', 'world music'],
                'exclude': ['hardcore', 'metal', 'grindcore'],
                'min_d': 180000,
                'max_d': 600000,
                'min_r': 5,
                'min_p': 0,
                'limit': 100,
                'min_t': 20,
                'max_artist': 2,
                'fallback_r': 4,
            },
        ]

        for preset in premium_presets:
            _build_preset_playlist(
                preset['name'],
                preset['include'],
                exclude_keywords=preset.get('exclude', []),
                min_duration_ms=int(preset.get('min_d', 120000)),
                max_duration_ms=int(preset.get('max_d', 600000)),
                min_rating=int(preset.get('min_r', 5)),
                min_play_count=int(preset.get('min_p', 1)),
                limit=int(preset.get('limit', 100)),
                min_tracks=int(preset.get('min_t', 20)),
                max_per_artist=int(preset.get('max_artist', 0)),
                fallback_min_rating=int(preset.get('fallback_r', preset.get('min_r', 5))),
                fallback_min_play_count=int(preset.get('fallback_p', 0))
            )
        
        # ── Guilty Pleasures : eurodance, pop cheesy, dance kitsch ────────────
        guilty_artists = {
            'aqua', 'vengaboys', 'haddaway', 'corona', 'double you', '2 unlimited',
            'ace of base', 'la bouche', 'livin joy', 'whigfield', 'snap', 'rednex',
            'scatman john', 'culture beat', 'mr. president', 'toy-box', 'captain jack',
            'dj bobo', 'fun factory', 'magic affair', 'alexia', 'amber',
            'twenty 4 seven', 'e-rotic', 'basic element', 'novaspace', 'lou bega',
            'los del rio', 'cartoons', 'molella', 'robert miles',
        }
        guilty_keywords = ['eurodance', 'dance pop', 'bubblegum', 'happy hardcore', 'eurobeat', 'italo dance']
        guilty = [
            t for t in tracks
            if (t.get('artist') or '').lower() in guilty_artists
            or any(kw in (t.get('genre') or '').lower() for kw in guilty_keywords)
            or any(kw in ' '.join(t.get('genres', [])).lower() for kw in guilty_keywords)
        ]
        if len(guilty) >= 10:
            guilty.sort(key=lambda t: (int(t.get('play_count') or 0), int(t.get('rating') or 0)), reverse=True)
            smart_playlists[f"{AUTO_PLAYLIST_PREFIX}🎉 Guilty Pleasures ({len(guilty)} titres)"] = guilty

        return smart_playlists

    def _fetch_lastfm_top_tracks(self) -> List[Dict[str, Any]]:
        """Récupère les tops tracks Last.fm de l'utilisateur configuré."""
        if not self.lastfm_user or not self.lastfm_api_key:
            self.logger.info("ℹ️ Last.fm non configuré (LASTFM_USER / LASTFM_API_KEY absents)")
            return []

        merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
        page = 1
        while page <= self.lastfm_max_pages:
            params = {
                "method": "user.gettoptracks",
                "user": self.lastfm_user,
                "api_key": self.lastfm_api_key,
                "format": "json",
                "period": self.lastfm_period,
                "limit": 1000,
                "page": page,
            }
            url = f"{LASTFM_API_URL}?{urllib.parse.urlencode(params)}"

            try:
                with urllib.request.urlopen(url, timeout=25) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            except Exception as exc:
                self.logger.warning(f"⚠️ Last.fm: erreur API page {page}: {exc}")
                break

            toptracks = payload.get("toptracks") if isinstance(payload, dict) else None
            chunk = toptracks.get("track") if isinstance(toptracks, dict) else []
            if isinstance(chunk, dict):
                chunk = [chunk]
            if not isinstance(chunk, list) or not chunk:
                break

            for item in chunk:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("name") or "").strip()
                artist_obj = item.get("artist")
                if isinstance(artist_obj, dict):
                    artist = str(artist_obj.get("name") or artist_obj.get("#text") or "").strip()
                else:
                    artist = str(artist_obj or "").strip()
                if not title or not artist:
                    continue
                try:
                    plays = int(item.get("playcount") or 0)
                except Exception:
                    plays = 0
                key = (artist.lower(), title.lower())
                prev = merged.get(key)
                if prev is None or plays > int(prev.get("lastfm_play_count") or 0):
                    merged[key] = {
                        "artist": artist,
                        "title": title,
                        "lastfm_play_count": plays,
                    }

            attr = toptracks.get("@attr") if isinstance(toptracks, dict) else {}
            total_pages = 1
            try:
                total_pages = int(attr.get("totalPages") or 1)
            except Exception:
                total_pages = 1
            if page >= total_pages:
                break
            page += 1

        rows = sorted(merged.values(), key=lambda t: int(t.get("lastfm_play_count") or 0), reverse=True)
        self.logger.info(f"📡 Last.fm: {len(rows)} top tracks récupérés ({self.lastfm_period})")
        return rows

    def create_lastfm_default_playlists(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Crée des playlists Last.fm à partir des écoutes réelles du compte utilisateur."""
        playlists: Dict[str, List[Dict]] = {}
        lastfm_tracks = self._fetch_lastfm_top_tracks()
        if not lastfm_tracks:
            return playlists

        def _norm(value: str) -> str:
            txt = unicodedata.normalize('NFKD', str(value or ''))
            txt = ''.join(ch for ch in txt if not unicodedata.combining(ch))
            txt = txt.lower()
            txt = re.sub(r"[^a-z0-9]+", " ", txt)
            return ' '.join(txt.split())

        by_artist_title: Dict[Tuple[str, str], List[Dict]] = {}
        by_title: Dict[str, List[Dict]] = {}
        for track in tracks:
            n_artist = _norm(track.get('artist') or '')
            n_title = _norm(track.get('title') or '')
            if not n_title:
                continue
            by_title.setdefault(n_title, []).append(track)
            if n_artist:
                by_artist_title.setdefault((n_artist, n_title), []).append(track)

        matched_by_id: Dict[int, Dict] = {}
        for lf in lastfm_tracks:
            n_artist = _norm(lf.get('artist') or '')
            n_title = _norm(lf.get('title') or '')
            candidates = by_artist_title.get((n_artist, n_title), [])
            if not candidates:
                title_only = by_title.get(n_title, [])
                if len(title_only) == 1:
                    candidates = title_only
            if not candidates:
                continue

            best = sorted(
                candidates,
                key=lambda t: (
                    int(t.get('rating') or 0),
                    int(t.get('play_count') or 0),
                    int(t.get('duration_ms') or 0),
                ),
                reverse=True,
            )[0]

            merged = dict(best)
            merged['lastfm_play_count'] = int(lf.get('lastfm_play_count') or 0)
            track_id = int(merged.get('id') or 0)
            prev = matched_by_id.get(track_id)
            if prev is None or merged['lastfm_play_count'] > int(prev.get('lastfm_play_count') or 0):
                matched_by_id[track_id] = merged

        matched = list(matched_by_id.values())
        if len(matched) < 20:
            self.logger.info(f"ℹ️ Last.fm: matching insuffisant ({len(matched)} titres liés à Plex)")
            return playlists

        matched.sort(key=lambda t: int(t.get('lastfm_play_count') or 0), reverse=True)
        play_values = sorted(int(t.get('lastfm_play_count') or 0) for t in matched)

        def _percentile(values: List[int], ratio: float) -> int:
            if not values:
                return 0
            idx = int((len(values) - 1) * ratio)
            idx = max(0, min(len(values) - 1, idx))
            return int(values[idx])

        p50 = max(1, _percentile(play_values, 0.50))
        p75 = max(p50 + 1, _percentile(play_values, 0.75))
        p90 = max(p75 + 1, _percentile(play_values, 0.90))

        top_heavy = [t for t in matched if int(t.get('lastfm_play_count') or 0) >= p90]
        strong_rotation = [t for t in matched if p75 <= int(t.get('lastfm_play_count') or 0) < p90]
        medium_rotation = [t for t in matched if p50 <= int(t.get('lastfm_play_count') or 0) < p75]
        to_push = [t for t in matched if int(t.get('lastfm_play_count') or 0) < p50 and int(t.get('rating') or 0) >= 6]

        if len(top_heavy) >= 15:
            sel = top_heavy[:300]
            playlists[f"{AUTO_PLAYLIST_PREFIX}📈 Last.fm Top écoutes ({len(sel)} titres)"] = sel
        if len(strong_rotation) >= 15:
            sel = strong_rotation[:350]
            playlists[f"{AUTO_PLAYLIST_PREFIX}🔥 Last.fm Rotation forte ({len(sel)} titres)"] = sel
        if len(medium_rotation) >= 15:
            sel = medium_rotation[:350]
            playlists[f"{AUTO_PLAYLIST_PREFIX}🌊 Last.fm Rotation moyenne ({len(sel)} titres)"] = sel
        if len(to_push) >= 20:
            sel = to_push[:300]
            playlists[f"{AUTO_PLAYLIST_PREFIX}🌱 Last.fm À pousser ({len(sel)} titres)"] = sel

        self.logger.info(
            "📻 Last.fm playlists: "
            f"{len(playlists)} créées (match Plex: {len(matched)}/{len(lastfm_tracks)})"
        )
        return playlists

    def create_discovery_playlists(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Crée une playlist 'Découvertes' pour les pistes 2★ peu écoutées"""
        discovery_playlists = {}
        # Plex uses 1-10 scale in DB; 2★ ~ 4
        discoveries = [t for t in tracks if t['rating'] == 4 and t['play_count'] <= 1]
        if discoveries:
            discovery_playlists[f"{AUTO_PLAYLIST_PREFIX}🔎 Découvertes (2★) ({len(discoveries)} titres)"] = discoveries
        return discovery_playlists

    def create_top_this_month(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Créer une playlist 'Top This Month' pour nouveaux 4-5★"""
        top_playlists = {}
        now = int(time.time())
        thirty_days = 30 * 24 * 60 * 60
        recent_high_rated = [t for t in tracks if t['date_added'] and (now - t['date_added']) <= thirty_days and t['rating'] >= 8]
        if recent_high_rated:
            top_playlists[f"{AUTO_PLAYLIST_PREFIX}🏆 Top du mois ({len(recent_high_rated)} titres)"] = recent_high_rated
        return top_playlists

    def create_to_review_playlists(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Créer une playlist 'À réévaluer' pour pistes 3★ avec beaucoup d'écoutes"""
        to_review = [t for t in tracks if t['rating'] == 6 and t['play_count'] >= 20]
        playlists = {}
        if to_review:
            playlists[f"{AUTO_PLAYLIST_PREFIX}🔁 À réévaluer (3★, >20 plays) ({len(to_review)} titres)"] = to_review
        return playlists

    def create_cleanup_playlists(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Créer une playlist de nettoyage (0 plays en 6 mois)"""
        cleanup_playlists = {}
        six_months = 6 * 30 * 24 * 60 * 60
        now = int(time.time())
        candidates = []
        for t in tracks:
            last_played = t.get('last_played')
            if last_played is None and t.get('date_added') and (now - t['date_added']) >= six_months:
                candidates.append(t)
            elif last_played and (now - last_played) >= six_months:
                candidates.append(t)
        if candidates:
            cleanup_playlists[f"{AUTO_PLAYLIST_PREFIX}🧹 Nettoyage (0 plays, 6+ mois) ({len(candidates)} titres)"] = candidates
        return cleanup_playlists

    def create_radio_playlists(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Crée des playlists imitant le style des radios françaises (RFM, NRJ, Fun Radio).
        Note: la plupart des pistes n'ont pas d'année renseignée → pas de filtre year."""
        radio_playlists = {}

        def _genres_match(track, genre_keywords):
            return any(k in g.lower() for g in track.get('genres', [track['genre']]) for k in genre_keywords)

        def _text_match(track, keywords):
            text = (track['title'] + ' ' + track['album'] + ' ' + track['artist']).lower()
            return any(k in text for k in keywords)

        # --- RFM : pop-rock adulte, variétés, soft, ballades, soul ---
        # Pas de filtre année (la majorité des pistes n'en ont pas)
        rfm_tracks = [
            t for t in tracks
            if _genres_match(t, ['pop/rock', 'vocal', 'easy listening', 'folk', 'blues', 'country'])
            and not _genres_match(t, ['rap', 'electronic'])
            and t.get('duration_ms', 0) >= 150000  # > 2m30 (pas de jingles)
        ]
        if rfm_tracks:
            # Mix aléatoire pondéré par les plus écoutés
            rfm_tracks.sort(key=lambda x: x['play_count'] + (x['rating'] or 0), reverse=True)
            rfm_selection = rfm_tracks[:300]
            radio_playlists[f'{AUTO_PLAYLIST_PREFIX}📻 Top Radio RFM ({len(rfm_selection)} titres)'] = rfm_selection

        # --- NRJ : hits pop mainstream, dance, R&B, rap ---
        nrj_tracks = [
            t for t in tracks
            if _genres_match(t, ['pop/rock', 'r&b', 'rap', 'electronic', 'latin'])
            and t.get('duration_ms', 0) >= 120000  # > 2 min
        ]
        if nrj_tracks:
            # Favoriser les plus écoutés (= les hits)
            nrj_tracks.sort(key=lambda x: x['play_count'], reverse=True)
            nrj_selection = nrj_tracks[:300]
            radio_playlists[f'{AUTO_PLAYLIST_PREFIX}📻 Top NRJ ({len(nrj_selection)} titres)'] = nrj_selection

        # --- Fun Radio : pistes issues des compilations Fun Radio en priorité,
        #     complétées par dance/electro/EDM si nécessaire ---
        fun_radio_tracks = [
            t for t in tracks
            if _text_match(t, ['fun radio', 'dancefloor', 'le son dancefloor', 'ibiza experience',
                               'remix club', 'bruno dans la radio'])
        ]
        fun_style_tracks = [
            t for t in tracks
            if t not in fun_radio_tracks
            and (_genres_match(t, ['electronic'])
                 or _text_match(t, ['remix', 'mix', 'dj ', 'club', 'dance', 'house', 'techno', 'trance', 'edm', 'rave']))
        ]
        random.shuffle(fun_style_tracks)
        fun_tracks = fun_radio_tracks + fun_style_tracks
        if fun_tracks:
            fun_selection = fun_tracks[:300]
            radio_playlists[f'{AUTO_PLAYLIST_PREFIX}📻 Top Fun Radio ({len(fun_selection)} titres)'] = fun_selection

        return radio_playlists

    def create_stars80_playlist(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Crée une playlist Stars 80 : tubes francophones et américains/internationaux des années 80.
        Cherche par artistes connus, mots-clés albums, et genres pop/rock."""
        playlists = {}

        # Artistes francophones emblématiques des années 80
        artistes_fr_80 = [
            'indochine', 'téléphone', 'jean-jacques goldman', 'goldman', 'francis cabrel',
            'michel sardou', 'daniel balavoine', 'mylène farmer', 'alain souchon',
            'véronique sanson', 'laurent voulzy', 'michel berger', 'france gall',
            'desireless', 'niagara', 'étienne daho', 'etienne daho', 'marc lavoine',
            'jean-pierre mader', 'cookie dingler', 'jeanne mas', 'gold', 'images',
            'début de soirée', 'caroline loeb', 'rose laurens', 'peter et sloane',
            'peter & sloane', 'karen cheryl', 'bandolero', 'bibi flash', 'lio',
            'plastic bertrand', 'partenaire particulier', 'julien clerc', 'patrick hernandez',
            'gilbert montagné', 'catherine lara', 'jean-luc lahaye', 'stéphanie',
            'elsa', 'les rita mitsouko', 'rita mitsouko', 'émile et images',
            'vanessa paradis', 'jean-jacques lafon', 'gérard blanc',
            'nino ferrer', 'michel polnareff', 'jacques dutronc', 'hervé villard', 'salvatore adamo',
            'los machucambos', 'nancy sinatra', 'joe dassin', 'peter et sloane',
            'patrick bruel', 'renaud', 'alain bashung', 'serge gainsbourg',
            'jacques dutronc', 'patrick coutin', 'pauline ester', 'sabine paturel',
            'jean schultheis', 'kim wilde', 'laroche valmont', 'philippe lavil',
            'yves simon', 'bernard lavilliers', 'herbert léonard', 'louis bertignac',
            'corynne charby', 'début de soirée', 'les avions', 'imagination',
            'ottawan', 'dalida', 'claude françois', 'mike brant',
        ]

        # Artistes américains/internationaux emblématiques des années 80
        artistes_us_80 = [
            'michael jackson', 'prince', 'madonna', 'cyndi lauper', 'tina turner',
            'whitney houston', 'bruce springsteen', 'bonnie tyler', 'phil collins',
            'duran duran', 'depeche mode', 'a-ha', 'eurythmics', 'culture club',
            'tears for fears', 'the cure', 'simple minds', 'wham', 'george michael',
            'queen', 'u2', 'pat benatar', 'blondie', 'toto', 'foreigner',
            'journey', 'bon jovi', 'def leppard', 'van halen', 'billy idol',
            'rick astley', 'kenny loggins', 'survivor', 'irene cara', 'berlin',
            'pet shop boys', 'new order', 'talk talk', 'the human league',
            'soft cell', 'abc', 'spandau ballet', 'alphaville', 'falco',
            'sabrina', 'sandra', 'samantha fox', 'gloria estefan', 'belinda carlisle',
            'bananarama', 'michael sembello', 'ray parker jr', 'glenn frey',
            'hall & oates', 'dire straits', 'sting', 'the police', 'inxs',
            'dead or alive', 'laura branigan', 'men at work', 'limahl',
            'baltimora', 'nena', 'scorpions', 'europe', 'white snake',
            'robert palmer', 'billy joel', 'lionel richie', 'stevie wonder',
            'donna summer', 'kool & the gang', 'earth wind', 'chaka khan',
        ]

        all_artists = artistes_fr_80 + artistes_us_80

        # Mots-clés albums typiques
        album_keywords_80 = [
            'stars 80', 'années 80', 'annees 80', 'hits 80', '80s',
            'top 50', 'soirées années 80', 'best of 80', 'decade 80',
            'délire 80', 'la bande à basile', 'le meilleur des années 80',
            'best of', 'greatest hits', 'remaster', 'remasterisé', 'deluxe', 'singles',
        ]

        selected = set()
        result = []
        known_80 = 0
        unknown_year = 0
        excluded_by_year = 0

        for t in tracks:
            if t['id'] in selected:
                continue
            artist_lower = (t.get('artist') or '').lower()
            album_lower = (t.get('album') or '').lower()
            title_lower = (t.get('title') or '').lower()

            # Match artiste
            artist_match = any(a in artist_lower for a in all_artists)

            # Match album
            album_match = any(kw in album_lower for kw in album_keywords_80)

            if artist_match or album_match:
                # Filtrer les pistes trop courtes (jingles, intros)
                if t.get('duration_ms', 0) < 120000:
                    continue

                # Déterminer l'année si possible
                year = None
                try:
                    year = int(t.get('year') or 0) or None
                except Exception:
                    year = None
                if not year and t.get('release_date'):
                    try:
                        year = int(str(t.get('release_date'))[:4])
                    except Exception:
                        year = None

                # Si artiste connu = inclus sans condition d'année (liste curatée = fiable)
                # Si match album seulement = nécessite une année connue dans les 80s
                if artist_match:
                    if year is not None:
                        if year < 1980 or year > 1989:
                            excluded_by_year += 1
                            continue
                        known_80 += 1
                    else:
                        unknown_year += 1
                        known_80 += 1  # artiste 80s fiable, on inclut sans année
                else:
                    # match album seulement: exiger année connue 1980-1989
                    if year is None:
                        unknown_year += 1
                        continue
                    if year < 1980 or year > 1989:
                        excluded_by_year += 1
                        continue
                    known_80 += 1

                selected.add(t['id'])
                result.append(t)

        # Trier par popularité (play_count + rating)
        result.sort(key=lambda x: x['play_count'] + (x['rating'] or 0), reverse=True)

        if result:
            # Rapide statistique pour diagnostic
            try:
                self.logger.info(f"⭐ Stars 80: {len(result)} titres (known_80={known_80}, unknown_year={unknown_year}, excluded_by_year={excluded_by_year})")
            except Exception:
                pass
            playlists[f'{AUTO_PLAYLIST_PREFIX}⭐ Stars 80 FR & US ({len(result)} titres)'] = result

        return playlists

    def create_top50_playlist(self, tracks: List[Dict]) -> Dict[str, List[Dict]]:
        """Crée une playlist Génération Top 50 : les tubes du Top 50 français (1984-1993+).
        Mix de variété française, pop internationale, dance/eurodance des compils Top 50."""
        playlists = {}

        # Artistes emblématiques du Top 50 français
        artistes_top50 = [
            # Variété française Top 50
            'jean-jacques goldman', 'goldman', 'daniel balavoine', 'france gall',
            'mylène farmer', 'indochine', 'laurent voulzy', 'michel berger',
            'alain souchon', 'patrick bruel', 'marc lavoine', 'julien clerc',
            'francis cabrel', 'michel sardou', 'renaud', 'alain bashung',
            'étienne daho', 'etienne daho', 'jean-pierre mader', 'niagara',
            'les rita mitsouko', 'rita mitsouko', 'jeanne mas', 'lio',
            'desireless', 'images', 'gold', 'cookie dingler', 'pauline ester',
            'elsa', 'stéphanie', 'vanessa paradis', 'patricia kaas',
            'florent pagny', 'pascal obispo', 'liane foly', 'khaled',
            'jordy', 'les inconnus', 'lagaf', 'hélène', 'hélène ségara',
            # Pop internationale Top 50
            'michael jackson', 'madonna', 'prince', 'george michael',
            'phil collins', 'whitney houston', 'a-ha', 'duran duran',
            'rick astley', 'sabrina', 'samantha fox', 'kylie minogue',
            'jason donovan', 'bros', 'milli vanilli', 'mc hammer',
            'vanilla ice', 'snap', 'technotronic', 'black box',
            'roxette', 'ace of base', 'la bouche', 'dr alban',
            'haddaway', 'corona', 'double you', '2 unlimited',
            # Dance / Eurodance
            'début de soirée', 'caroline loeb', 'laroche valmont', 'cock robin',
            'patrick hernandez', 'plastic bertrand', 'ottawan', 'peter et sloane',
            'peter & sloane', 'jean schultheis', 'bandolero', 'bibi flash',
            'corynne charby', 'philippe lavil', 'gilbert montagné',
            'karen cheryl', 'rose laurens', 'herbert léonard', 'partenaire particulier',
        ]

        # Mots-clés albums Top 50
        album_keywords_top50 = [
            'top 50', 'génération top', 'generation top', 'top 50 30 ans',
            'stars 80', 'années 80', 'annees 80', 'hits 80', 'tubes 80',
            'nuit de folie', 'soirées années 80', 'best of 80',
            'fan des années 80', 'fans des années 80',
            'anthologie', 'chanson française',
        ]

        selected = set()
        result = []

        for t in tracks:
            if t['id'] in selected:
                continue
            artist_lower = t['artist'].lower()
            album_lower = t['album'].lower()

            artist_match = any(a in artist_lower for a in artistes_top50)
            album_match = any(kw in album_lower for kw in album_keywords_top50)

            if artist_match or album_match:
                if t.get('duration_ms', 0) >= 120000:
                    selected.add(t['id'])
                    result.append(t)

        result.sort(key=lambda x: x['play_count'] + (x['rating'] or 0), reverse=True)

        if result:
            playlists[f'{AUTO_PLAYLIST_PREFIX}🎤 Génération Top 50 ({len(result)} titres)'] = result

        return playlists

    def export_playlists_to_usb(self, playlist_names: List[str], usb_path: str = '/media/paulceline/MUSIC'):
        """Copie les fichiers audio des playlists données vers la clé USB.
        Crée un dossier par playlist et un fichier .m3u à la racine."""
        import shutil

        if not os.path.isdir(usb_path):
            self.logger.error(f"❌ Clé USB non trouvée: {usb_path}")
            return

        # Récupérer les playlists depuis Plex API
        try:
            url = f"{PLEX_URL}/playlists?X-Plex-Token={PLEX_TOKEN}"
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req)
            root = ET.fromstring(resp.read())
        except Exception as e:
            self.logger.error(f"❌ Impossible de lister les playlists: {e}")
            return

        for pname in playlist_names:
            # Trouver la playlist par titre (match partiel)
            playlist_el = None
            for p in root.findall('.//Playlist'):
                if pname.lower() in p.get('title', '').lower():
                    playlist_el = p
                    break

            if not playlist_el:
                self.logger.warning(f"⚠️ Playlist '{pname}' non trouvée dans Plex")
                continue

            pl_title = playlist_el.get('title')
            rk = playlist_el.get('ratingKey')
            self.logger.info(f"📀 Export USB: {pl_title}")

            # Récupérer les pistes
            url = f"{PLEX_URL}/playlists/{rk}/items?X-Plex-Token={PLEX_TOKEN}"
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req)
            items_root = ET.fromstring(resp.read())

            # Dossier sur USB
            safe_name = self._safe_filename(pl_title or 'playlist')
            dest_dir = os.path.join(usb_path, safe_name)
            os.makedirs(dest_dir, exist_ok=True)

            m3u_lines = ['#EXTM3U', f'#PLAYLIST:{pl_title}', '']
            copied = 0
            skipped = 0

            for track in items_root.findall('.//Track'):
                title = track.get('title', 'Unknown')
                artist = track.get('grandparentTitle', 'Unknown')
                duration = int(track.get('duration', '0')) // 1000

                # Trouver le fichier source
                media = track.find('.//Part')
                if media is None:
                    skipped += 1
                    continue
                src_file = media.get('file', '')
                if not src_file or not os.path.isfile(src_file):
                    skipped += 1
                    continue

                # Nom du fichier destination
                ext = os.path.splitext(src_file)[1]
                # Numéroter pour garder l'ordre
                dest_name = f"{copied + 1:03d} - {artist} - {title}"
                # Nettoyer le nom
                dest_name = re.sub(r'[<>:"/\\|?*]', '', dest_name)[:200]
                dest_name += ext
                dest_file = os.path.join(dest_dir, dest_name)

                # Copier si pas déjà présent ou taille différente
                if not os.path.exists(dest_file) or os.path.getsize(dest_file) != os.path.getsize(src_file):
                    try:
                        shutil.copy2(src_file, dest_file)
                        copied += 1
                    except Exception as e:
                        self.logger.warning(f"  ⚠️ {dest_name}: {e}")
                        skipped += 1
                        continue
                else:
                    copied += 1  # Already there

                m3u_lines.append(f"#EXTINF:{duration},{artist} - {title}")
                m3u_lines.append(f"{safe_name}/{dest_name}")
                m3u_lines.append('')

            # Écrire le fichier M3U à la racine USB
            m3u_path = os.path.join(usb_path, f"{safe_name}.m3u")
            with open(m3u_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(m3u_lines))

            self.logger.info(f"  ✅ {copied} fichiers copiés, {skipped} ignorés → {dest_dir}")

    def _plex_api(self, method: str, path: str) -> dict:
        """Appel API Plex. Retourne le JSON parsé ou {} si pas de body."""
        url = f"{PLEX_URL}{path}"
        if '?' in url:
            url += f"&X-Plex-Token={PLEX_TOKEN}"
        else:
            url += f"?X-Plex-Token={PLEX_TOKEN}"
        req = urllib.request.Request(url, method=method)
        req.add_header('Accept', 'application/json')
        resp = urllib.request.urlopen(req)
        body = resp.read()
        return json.loads(body) if body else {}

    def cleanup_old_auto_playlists(self, new_playlist_names: Optional[List[str]] = None):
        """Supprime uniquement les playlists Plex dont le nom (sans le compteur de titres) correspond
        à une playlist qui va être régénérée."""
        import re as _re
        def _base(name: str) -> str:
            s = EMOJI_CHARS_RE.sub('', str(name or ''))
            s = s.replace('️', '').replace('‍', '')
            s = _re.sub(r'\s*\(\d+\s+titres\)', '', s, flags=_re.IGNORECASE)
            s = _re.sub(r'\s*\[fusion\]\s*$', '', s, flags=_re.IGNORECASE)
            s = _re.sub(r'\s*\(deduped\)\s*$', '', s, flags=_re.IGNORECASE)
            s = _re.sub(r'\s+', ' ', s).strip()
            return s

        PLEX_PROTECTED = {
            'library tracks', 'loved tracks', 'recently added', 'recently played',
            'all music', 'fresh ❤️', '❤️ tracks', 'recent tracks',
            'weekly track chart', 'top tracks (last 7 days)', 'top tracks (last 30 days)',
            'top tracks (last 3  months)', 'top tracks (last 6  months)',
            'top tracks (last 12  months)', 'top tracks (all time)',
        }
        try:
            data = self._plex_api('GET', '/playlists')
            playlists = data.get('MediaContainer', {}).get('Metadata', [])
            playlists = [p for p in playlists if p.get('title', '').lower() not in PLEX_PROTECTED]
            if new_playlist_names:
                # Supprimer uniquement les playlists dont la base correspond à une playlist régénérée
                new_bases = {_base(n) for n in new_playlist_names}
                to_delete = [p for p in playlists if _base(p.get('title', '')) in new_bases]
            else:
                # Fallback: supprimer tout (comportement original, déconseillé si préfixe vide)
                to_delete = [p for p in playlists if p.get('title', '').startswith(AUTO_PLAYLIST_PREFIX)] if AUTO_PLAYLIST_PREFIX else []
            count = 0
            for p in to_delete:
                rk = p['ratingKey']
                try:
                    self._plex_api('DELETE', f'/playlists/{rk}')
                    count += 1
                    self.logger.debug(f"  Supprimé: {p['title']}")
                except Exception as e:
                    self.logger.warning(f"  ⚠️ Impossible de supprimer {p['title']}: {e}")
            if count:
                self.logger.info(f"🧹 {count} anciennes playlists auto supprimées")
        except Exception as e:
            self.logger.error(f"❌ Erreur nettoyage: {e}")



    def save_playlist_to_plex(self, playlist_name: str, tracks: List[Dict], append_existing: bool = False) -> bool:
        """Sauvegarde une playlist dans Plex via l'API HTTP."""
        def _base(name: str) -> str:
            s = EMOJI_CHARS_RE.sub('', str(name or ''))
            s = s.replace('️', '').replace('‍', '')
            s = re.sub(r'\s*\(\d+\s+titres\)', '', s, flags=re.IGNORECASE)
            s = re.sub(r'\s*\[fusion\]\s*$', '', s, flags=re.IGNORECASE)
            s = re.sub(r'\s*\(deduped\)\s*$', '', s, flags=re.IGNORECASE)
            s = re.sub(r'\s+', ' ', s).strip()
            return s

        def _consolidate_homonyms(target_base: str, keep_rk: str) -> None:
            """Supprime les playlists homonymes restantes en conservant keep_rk."""
            try:
                latest = self._plex_api('GET', '/playlists')
                for candidate in latest.get('MediaContainer', {}).get('Metadata', []):
                    rk = str(candidate.get('ratingKey') or '')
                    if not rk or rk == str(keep_rk):
                        continue
                    if _base(candidate.get('title', '')) != target_base:
                        continue
                    self._plex_api('DELETE', f'/playlists/{rk}')
                    self.logger.warning(
                        f"  🧹 Playlist doublon supprimée: {candidate.get('title', '')} (rk={rk})"
                    )
            except Exception as cleanup_exc:
                self.logger.warning(f"  ⚠️ Consolidation des doublons impossible pour {playlist_name}: {cleanup_exc}")

        # Dédupliquer les pistes par ID (conserver l'ordre, première occurrence)
        seen_ids: set = set()
        deduped = []
        for t in tracks:
            tid = t['id']
            if tid not in seen_ids:
                seen_ids.add(tid)
                deduped.append(t)
        if len(deduped) < len(tracks):
            self.logger.debug(f"  🔁 {len(tracks) - len(deduped)} doublons retirés de {playlist_name}")
        tracks = deduped
        if not tracks:
            self.logger.debug(f"⏭️ Playlist vide ignorée: {playlist_name}")
            return False
        try:
            data = self._plex_api('GET', '/playlists')
            target_base = _base(playlist_name)
            matching_playlists = [
                p for p in data.get('MediaContainer', {}).get('Metadata', [])
                if p.get('ratingKey')
                and _base(p.get('title', '')) == target_base
            ]
            existing_rks = [p['ratingKey'] for p in matching_playlists]

            # Favoriser une correspondance exacte si présente, sinon prendre la première homonyme.
            exact_match = next((p for p in matching_playlists if p.get('title') == playlist_name), None)
            existing_rk = exact_match['ratingKey'] if exact_match else (existing_rks[0] if existing_rks else None)

            if len(existing_rks) > 1:
                self.logger.warning(
                    f"  🧹 {len(existing_rks)} playlists homonymes détectées pour {playlist_name}; consolidation en cours"
                )

            # Mode ajout: conserve la playlist existante et ajoute uniquement les nouvelles pistes.
            if append_existing and existing_rk is not None:
                # Si seul le suffixe "(N titres)" a changé, on renomme la playlist existante
                # pour garder un libellé cohérent sans créer de nouvel objet Plex.
                current_title = next((p.get('title', '') for p in matching_playlists if p.get('ratingKey') == existing_rk), '')
                if current_title and current_title != playlist_name:
                    try:
                        rename_params = urllib.parse.urlencode({'title': playlist_name})
                        self._plex_api('PUT', f'/playlists/{existing_rk}?{rename_params}')
                        self.logger.debug(f"  ✏️ Playlist renommée: '{current_title}' → '{playlist_name}'")
                    except Exception as rename_exc:
                        self.logger.warning(
                            f"  ⚠️ Impossible de renommer '{current_title}' vers '{playlist_name}': {rename_exc}"
                        )

                meta = self._plex_api('GET', f'/playlists/{existing_rk}/items')
                existing_items = meta.get('MediaContainer', {}).get('Metadata', [])
                existing_ids = {int(i.get('ratingKey')) for i in existing_items if i.get('ratingKey')}
                duplicate_ids: List[int] = []
                duplicate_seen: set[int] = set()
                for duplicate_rk in existing_rks[1:]:
                    duplicate_meta = self._plex_api('GET', f'/playlists/{duplicate_rk}/items')
                    duplicate_items = duplicate_meta.get('MediaContainer', {}).get('Metadata', [])
                    for item in duplicate_items:
                        rating_key = item.get('ratingKey')
                        if not rating_key:
                            continue
                        item_id = int(rating_key)
                        if item_id not in existing_ids and item_id not in duplicate_seen:
                            duplicate_seen.add(item_id)
                            duplicate_ids.append(item_id)

                to_add = []
                queued_ids = set()
                for item_id in duplicate_ids + [int(t['id']) for t in tracks]:
                    if item_id not in existing_ids and item_id not in queued_ids:
                        queued_ids.add(item_id)
                        to_add.append(item_id)
                if not to_add:
                    for duplicate_rk in existing_rks[1:]:
                        self._plex_api('DELETE', f'/playlists/{duplicate_rk}')
                    _consolidate_homonyms(target_base, str(existing_rk))
                    self.logger.info(f"✅ Playlist inchangée: {playlist_name} (aucune nouvelle piste)")
                    return True

                BATCH = 200
                for i in range(0, len(to_add), BATCH):
                    batch = to_add[i:i+BATCH]
                    ids_str = ','.join(str(item_id) for item_id in batch)
                    uri = f'server://{PLEX_MACHINE_ID}/com.plexapp.plugins.library/library/metadata/{ids_str}'
                    params = urllib.parse.urlencode({'uri': uri})
                    self._plex_api('PUT', f'/playlists/{existing_rk}/items?{params}')

                for duplicate_rk in existing_rks[1:]:
                    self._plex_api('DELETE', f'/playlists/{duplicate_rk}')

                _consolidate_homonyms(target_base, str(existing_rk))

                self.logger.info(f"✅ Playlist mise à jour: {playlist_name} (+{len(to_add)} pistes)")
                return True

            # Mode remplacement: supprimer puis recréer si la playlist existe déjà
            for rating_key in existing_rks:
                self._plex_api('DELETE', f'/playlists/{rating_key}')

            # Créer la playlist avec le 1er item
            first_uri = f'server://{PLEX_MACHINE_ID}/com.plexapp.plugins.library/library/metadata/{tracks[0]["id"]}'
            params = urllib.parse.urlencode({
                'type': 'audio', 'title': playlist_name,
                'smart': 0, 'uri': first_uri
            })
            resp = self._plex_api('POST', f'/playlists?{params}')
            rk = resp['MediaContainer']['Metadata'][0]['ratingKey']

            # Ajouter les items restants par lots de 200
            BATCH = 200
            remaining = tracks[1:]
            for i in range(0, len(remaining), BATCH):
                batch = remaining[i:i+BATCH]
                ids_str = ','.join(str(t['id']) for t in batch)
                uri = f'server://{PLEX_MACHINE_ID}/com.plexapp.plugins.library/library/metadata/{ids_str}'
                params = urllib.parse.urlencode({'uri': uri})
                self._plex_api('PUT', f'/playlists/{rk}/items?{params}')

            _consolidate_homonyms(target_base, str(rk))

            self.logger.info(f"✅ Playlist créée: {playlist_name} ({len(tracks)} pistes)")
            return True

        except Exception as e:
            self.logger.error(f"❌ Erreur playlist {playlist_name}: {e}")
            return False

    # --- Définitions visuelles pour les posters de playlists ---
    # Chaque entrée: (mots-clés à chercher dans le titre, emoji, couleurs gradient, sous-titre optionnel)
    POSTER_THEMES = [
        # Auto playlists
        (['⭐', 'étoile'], '⭐', [(255, 180, 0), (200, 100, 0)], None),
        (['📈', 'last.fm', 'lastfm'], '📈', [(120, 0, 180), (255, 80, 120)], None),
        (['🔥', 'top 100', 'plus écoutés'], '🔥', [(200, 50, 0), (255, 150, 0)], None),
        (['❤️', 'favoris'], '❤️', [(180, 0, 50), (255, 50, 100)], None),
        (['🔍', 'redécouvrir'], '🔍', [(0, 100, 150), (0, 200, 200)], None),
        (['🔎', 'découvertes'], '🔎', [(50, 100, 180), (0, 200, 150)], None),
        (['⚡', 'énergique'], '⚡', [(200, 150, 0), (255, 50, 0)], None),
        (['🧘', 'concentration'], '🧘', [(0, 80, 120), (0, 180, 180)], None),
        (['🎵', 'blues'], '🎵', [(0, 0, 120), (50, 50, 200)], None),
        (['🎵', 'electronic'], '🎛️', [(0, 0, 150), (150, 0, 255)], None),
        (['🎵', 'pop/rock'], '🎸', [(200, 0, 50), (255, 100, 0)], None),
        (['🎵', 'r&b'], '🎤', [(100, 0, 150), (200, 50, 200)], None),
        (['🎵', 'rap'], '🎤', [(40, 40, 40), (100, 0, 0)], None),
        (['🎵', 'jazz'], '🎷', [(50, 30, 0), (150, 100, 0)], None),
        (['🎵', 'latin'], '💃', [(200, 50, 0), (255, 200, 0)], None),
        (['🎵', 'country'], '🤠', [(100, 80, 0), (200, 150, 50)], None),
        (['🎵', 'reggae'], '🟢', [(0, 120, 0), (200, 200, 0)], None),
        (['🎵', 'folk'], '🪕', [(80, 60, 20), (180, 140, 60)], None),
        (['🎵', 'holiday'], '🎄', [(150, 0, 0), (0, 100, 0)], None),
        (['📻', 'rfm'], '📻', [(200, 0, 50), (255, 100, 0)], None),
        (['📻', 'nrj'], '📻', [(0, 80, 200), (0, 200, 100)], None),
        (['📻', 'fun radio'], '📻', [(150, 0, 200), (0, 100, 255)], None),
        (['funk', 'disco'], '🕺', [(220, 20, 180), (255, 140, 0)], None),
        (['running', 'workout'], '🏃', [(0, 150, 50), (0, 200, 150)], None),
        (['🕰️', 'années'], '📅', [(80, 0, 150), (0, 100, 200)], None),
        (['🆕', 'récent', 'ajouté'], '🆕', [(0, 150, 100), (0, 200, 200)], None),
        (['🏆', 'top du mois'], '🏆', [(180, 130, 0), (255, 200, 0)], None),
        (['🔁', 'réévaluer'], '🔁', [(100, 100, 0), (200, 150, 0)], None),
        (['🧹', 'nettoyage'], '🧹', [(80, 80, 80), (150, 150, 150)], None),
        (['daily mix 80s'], '🪩', [(255, 100, 50), (255, 200, 0)], None),
        (['daily mix 90s'], '📼', [(40, 120, 255), (0, 220, 200)], None),
        (['daily mix 2000s'], '💿', [(170, 80, 255), (80, 180, 255)], None),
        (['daily mix 2010s'], '📱', [(20, 150, 160), (0, 220, 120)], None),
        (['mix humeur happy'], '😊', [(255, 170, 0), (255, 90, 0)], None),
        (['mix humeur sad'], '🥀', [(70, 90, 160), (20, 40, 100)], None),
        (['mix humeur intense'], '🔥', [(200, 40, 0), (255, 120, 0)], None),
        (['mix humeur sensuel'], '💋', [(180, 20, 80), (255, 80, 140)], None),
        (['mix humeur deep focus'], '🧠', [(0, 90, 150), (0, 170, 220)], None),
        (['mix humeur sleep'], '🌙', [(30, 50, 120), (60, 100, 180)], None),
        # Manual playlists (partial match on title)
        (['disco', '2007'], '🕺', [(220, 20, 180), (255, 140, 0)], None),
        (["90s", "'90s", "90"], '💿', [(0, 100, 200), (0, 200, 150)], None),
        (['dance', '2008'], '💃', [(200, 0, 100), (255, 100, 0)], None),
        (['2000s'], '🎧', [(80, 0, 180), (0, 150, 255)], None),
        (['soundiiz', 'ai '], '🤖', [(0, 80, 160), (0, 200, 200)], None),
        (['move your body', 'absolute'], '🏋️', [(200, 50, 0), (255, 200, 0)], None),
        (['apéro', 'aperitif'], '🍹', [(255, 100, 0), (255, 200, 50)], None),
        (['car music'], '🚗', [(40, 40, 40), (180, 0, 0)], None),
        (['chill', 'relax', 'massage'], '🧘', [(0, 100, 150), (100, 200, 180)], None),
        (['club', 'electro'], '🎛️', [(0, 0, 150), (150, 0, 255)], None),
        (['favorite', 'liked', 'loved'], '💜', [(100, 0, 200), (200, 50, 255)], None),
        (['fiesta', 'party'], '🎉', [(255, 50, 0), (255, 200, 0)], None),
        (['discothèque', 'la plus grande'], '🌍', [(0, 50, 150), (200, 0, 100)], None),
        (['moins écoutés'], '🔇', [(60, 60, 80), (120, 120, 140)], None),
        (['plus écoutés'], '🔊', [(200, 150, 0), (255, 50, 0)], None),
        (['classement', 'meilleur'], '🏆', [(180, 130, 0), (255, 200, 0)], None),
        (['rfm party', '80-90'], '📻', [(200, 0, 50), (255, 100, 0)], None),
        (['running', 'hits 2025'], '🏃', [(0, 150, 50), (0, 200, 150)], None),
        (['stars 80', 'soirées'], '⭐', [(50, 0, 150), (200, 0, 200)], None),
        (['rhythm', 'the rhythm'], '🎶', [(0, 80, 200), (0, 180, 255)], None),
        (['itunes', 'hot tracks'], '🔥', [(200, 50, 0), (255, 150, 0)], None),
        (['stars 80'], '⭐', [(180, 0, 220), (255, 100, 0)], None),
        (['génération top 50', 'generation top 50'], '🎤', [(0, 50, 150), (200, 50, 200)], None),
    ]

    def _pick_style_for_playlist(self, title: str) -> Dict[str, Any]:
        """Sélectionne le style à appliquer pour une playlist donnée.

        Si ``self.poster_style`` contient une clé ``rotate`` (liste de chemins
        ou objets {file, keywords}), on choisit un style :
          1) prioritaire si ses ``keywords`` matchent le titre
          2) sinon:
             - mode aléatoire: choix aléatoire
             - mode déterministe: hash(title) pour un choix stable mais varié
        Sinon retourne le style global.
        """
        rotate = self.poster_style.get("rotate")
        if not rotate or not isinstance(rotate, list):
            return self.poster_style

        title_lower = title.lower()
        # 1) Recherche d'un style avec keyword qui matche
        keyword_matches: List[str] = []
        plain_paths: List[str] = []
        for entry in rotate:
            if isinstance(entry, str):
                plain_paths.append(entry)
            elif isinstance(entry, dict):
                fpath = str(entry.get("file", "")).strip()
                if not fpath:
                    continue
                kws = entry.get("keywords") or []
                if kws and any(str(k).lower() in title_lower for k in kws):
                    keyword_matches.append(fpath)
                else:
                    plain_paths.append(fpath)

        candidates = keyword_matches if keyword_matches else plain_paths
        if not candidates:
            return self.poster_style

        # Choix aléatoire ou déterministe par hash du titre
        if self.randomize_poster_styles:
            chosen_path = random.choice(candidates)
        else:
            import hashlib
            h = int(hashlib.md5(title.encode("utf-8")).hexdigest(), 16)
            chosen_path = candidates[h % len(candidates)]

        # Charge le style choisi (avec fallback sur le style global en cas d'erreur)
        cfg_path = Path(chosen_path)
        if not cfg_path.is_absolute():
            cfg_path = (Path(__file__).parent / chosen_path).resolve()
            if not cfg_path.exists():
                cfg_path = (Path(__file__).parent.parent / chosen_path).resolve()
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("non-objet")
            return self._normalize_style(raw)
        except Exception as e:
            self.logger.debug(f"  ⚠️ Style rotatif {cfg_path} invalide: {e}")
            return self.poster_style

    def _find_background_image(self, title: str) -> Optional[Path]:
        """Cherche une image de fond dans poster_backgrounds/ correspondant au titre.

        Les fichiers peuvent être nommés par mot-clé : ``rap.jpg``, ``jazz.png``,
        ``90s_rap.jpg``, ``chill.webp``, etc.  Le fichier avec le meilleur score
        de correspondance est retourné.  Retourne None si aucune image ne correspond.
        """
        bg_dir_raw = str(self.poster_style.get("backgrounds_dir", "")).strip()
        if bg_dir_raw:
            bg_dir = Path(bg_dir_raw).expanduser()
        else:
            bg_dir = Path(__file__).parent / "poster_backgrounds"

        if not bg_dir.is_dir():
            return None

        title_lower = title.lower()
        title_normalized = re.sub(r"['''\(\)\[\]&,!?]", " ", title_lower)
        title_words = set(title_normalized.split())

        EXTS = ('.jpg', '.jpeg', '.png', '.webp')
        best: Optional[Path] = None
        best_score = 0

        for img_file in sorted(bg_dir.iterdir()):
            if img_file.suffix.lower() not in EXTS:
                continue
            stem = img_file.stem.lower().replace('_', ' ').replace('-', ' ')
            stem_words = set(stem.split())
            score = len(stem_words & title_words)
            if stem in title_lower:
                score += len(stem_words)
            if score > best_score:
                best_score = score
                best = img_file

        return best if best_score > 0 else None

    def _find_poster_theme(self, title: str) -> Tuple[str, Any, Any]:
        """Trouve le thème visuel pour un titre de playlist. Retourne (emoji, [c1], [c2])."""
        title_lower = title.lower()

        # Priorité aux thèmes custom définis dans le JSON de style.
        for theme in self.poster_style.get("themes", []):
            keywords = theme.get("keywords", [])
            if not keywords:
                continue
            match_mode = theme.get("match", "all")
            is_match = any(k in title_lower for k in keywords) if match_mode == "any" else all(k in title_lower for k in keywords)
            if is_match:
                colors = theme.get("colors", [(80, 80, 160), (150, 150, 230)])
                return theme.get("emoji", "🎵"), colors[0], colors[1]

        for keywords, emoji, colors, _ in self.POSTER_THEMES:
            if all(k.lower() in title_lower for k in keywords):
                return emoji, colors[0], colors[1]

        default_emoji = str(self.poster_style.get("default_emoji", "🎵"))
        default_colors = self.poster_style.get("default_colors", [(70, 70, 140), (120, 120, 220)])
        if isinstance(default_colors, list) and len(default_colors) == 2:
            return default_emoji, default_colors[0], default_colors[1]

        # Fallback: couleur basée sur le hash du titre
        h = hash(title) % 360
        import colorsys
        r1, g1, b1 = [int(c * 255) for c in colorsys.hsv_to_rgb(h / 360, 0.7, 0.6)]
        r2, g2, b2 = [int(c * 255) for c in colorsys.hsv_to_rgb(((h + 60) % 360) / 360, 0.7, 0.8)]
        return '🎵', (r1, g1, b1), (r2, g2, b2)

    def generate_playlist_posters(self):
        """Génère et applique des images de poster pour toutes les playlists Plex.

        Design moderne : mesh gradient (blobs colorés flous) + film grain + typographie
        affiche (kicker en haut, gros titre serré, métadonnées en bas).
        """
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
            import math, random, hashlib, colorsys
            try:
                RESAMPLING_LANCZOS = Image.Resampling.LANCZOS
            except AttributeError:
                RESAMPLING_LANCZOS = Image.LANCZOS
        except ImportError:
            self.logger.warning("⚠️ Pillow non installé, pas de génération de posters (pip install Pillow)")
            return

        def _clamp(v, lo=0, hi=255):
            return max(lo, min(hi, int(v)))

        def _hex_to_rgb(c):
            if isinstance(c, (list, tuple)):
                return (_clamp(c[0]), _clamp(c[1]), _clamp(c[2]))
            s = str(c).lstrip('#')
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))

        def _adjust(rgb, dl=0.0, ds=0.0):
            """Ajuste luminosité (dl) et saturation (ds) en HLS."""
            r, g, b = rgb
            h, l, s = colorsys.rgb_to_hls(r/255, g/255, b/255)
            l = max(0.0, min(1.0, l + dl))
            s = max(0.0, min(1.0, s + ds))
            r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
            return (int(r2*255), int(g2*255), int(b2*255))

        def _seeded_rng(seed_str: str) -> random.Random:
            h = hashlib.md5(seed_str.encode('utf-8')).hexdigest()
            return random.Random(int(h[:8], 16))

        def _build_palette(c1, c2, rng: random.Random):
            """4-5 couleurs cohérentes pour le mesh gradient."""
            base = [_hex_to_rgb(c1), _hex_to_rgb(c2)]
            # Mélange + variations claires/foncées
            mix = tuple((base[0][i] + base[1][i]) // 2 for i in range(3))
            palette = [
                _adjust(base[0], dl=-0.10, ds=+0.10),
                _adjust(base[1], dl=+0.05, ds=+0.05),
                _adjust(mix, dl=+0.15, ds=+0.15),
                _adjust(base[0], dl=-0.25, ds=-0.05),
                _adjust(base[1], dl=+0.20, ds=+0.10),
            ]
            rng.shuffle(palette)
            return palette

        def _make_mesh_gradient(size: int, c1, c2, seed: str):
            """Mesh gradient moderne : grands blobs colorés + Gaussian blur."""
            rng = _seeded_rng(seed)
            palette = _build_palette(c1, c2, rng)

            # Toile à basse résolution pour un flou massif et rapide
            low = max(80, size // 6)
            scale = size / low

            # Fond : couleur la plus foncée de la palette pour la profondeur
            darkest = min(palette, key=lambda c: sum(c))
            base_bg = _adjust(darkest, dl=-0.15, ds=-0.05)
            img = Image.new('RGB', (low, low), base_bg)
            draw = ImageDraw.Draw(img)

            # 5-6 blobs de couleurs avec positions/tailles randomisées
            n_blobs = rng.randint(5, 7)
            for i in range(n_blobs):
                color = palette[i % len(palette)]
                cx = rng.uniform(-0.1, 1.1) * low
                cy = rng.uniform(-0.1, 1.1) * low
                radius = rng.uniform(0.35, 0.7) * low
                draw.ellipse(
                    [cx - radius, cy - radius, cx + radius, cy + radius],
                    fill=color,
                )

            # Flou massif pour fusionner les blobs
            img = img.filter(ImageFilter.GaussianBlur(radius=low * 0.18))
            # Upscale en LANCZOS puis flou léger pour lisser
            img = img.resize((size, size), RESAMPLING_LANCZOS)
            img = img.filter(ImageFilter.GaussianBlur(radius=size * 0.02))
            return img

        def _add_film_grain(img: Image.Image, intensity: int = 10) -> Image.Image:
            """Grain de film monochrome multiplié, plus subtil que random sur RGB."""
            w, h = img.size
            noise = Image.effect_noise((w, h), intensity * 6)  # L mode
            noise = noise.filter(ImageFilter.GaussianBlur(0.5))
            # Centre le noise autour de 128 et applique comme overlay doux
            noise_rgb = Image.merge('RGB', (noise, noise, noise))
            # Mélange à faible opacité
            return Image.blend(img, ImageChops.multiply(img, noise_rgb), 0.12)

        def _add_vignette(img: Image.Image, strength: float = 0.45) -> Image.Image:
            """Vignette douce qui assombrit les coins."""
            w, h = img.size
            mask = Image.new('L', (w, h), 0)
            md = ImageDraw.Draw(mask)
            # Ellipse blanche → noir aux coins (radial)
            md.ellipse([-w*0.1, -h*0.1, w*1.1, h*1.1], fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(radius=w*0.25))
            dark = Image.new('RGB', (w, h), (0, 0, 0))
            base = Image.composite(img, dark, mask.point(lambda p: int(p * (1 - strength) + 255 * (1 - strength))))
            # subtle color tint toward the center using the first palette color if available
            try:
                tint = tuple(int(x * 0.06) for x in self.poster_style.get('default_colors', [(30,30,100)])[0])
                tint_layer = Image.new('RGB', (w, h), tint)
                base = Image.blend(base, tint_layer, 0.03)
            except Exception:
                pass
            return base

        def _rounded_rect_mask(size, radius):
            """Masque alpha rectangle arrondi."""
            w, h = size
            mask = Image.new('L', (w, h), 0)
            d = ImageDraw.Draw(mask)
            d.rounded_rectangle([0, 0, w-1, h-1], radius=radius, fill=255)
            return mask


        # Constantes par défaut (utilisées si pas de rotation par playlist)
        ROTATE_ENABLED = bool(self.poster_style.get("rotate"))
        SIZE_DEFAULT = int(self.poster_style.get("size", 600))

        # Récupérer toutes les playlists
        try:
            url = f"{PLEX_URL}/playlists?X-Plex-Token={PLEX_TOKEN}"
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req)
            root = ET.fromstring(resp.read())
        except Exception as e:
            self.logger.error(f"❌ Impossible de lister les playlists: {e}")
            return

        playlists = root.findall('.//Playlist')
        self.logger.info(f"🎨 Génération des posters pour {len(playlists)} playlists{' (rotation active)' if ROTATE_ENABLED else ''}...")

        ok = 0
        fail = 0
        for playlist in playlists:
            title = playlist.get('title', '')
            rk = playlist.get('ratingKey', '')
            if not rk:
                continue

            # Choix du style (rotation par playlist si activée)
            saved_style = self.poster_style
            if ROTATE_ENABLED:
                self.poster_style = self._pick_style_for_playlist(title)

            FONT_PATH = str(self.poster_style.get("font_path", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
            EMOJI_FONT_PATH = str(self.poster_style.get("emoji_font_path", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"))
            SIZE = int(self.poster_style.get("size", 1000))
            TITLE_COLOR = tuple(self.poster_style.get("title_color", (255, 255, 255, 255)))

            emoji, c1, c2 = self._find_poster_theme(title)

            # ── Fond : image custom OU mesh gradient moderne ──
            bg_path = self._find_background_image(title)
            img = None
            if bg_path:
                try:
                    bg = Image.open(bg_path).convert('RGB')
                    # crop centré carré
                    bw, bh = bg.size
                    s = min(bw, bh)
                    bg = bg.crop(((bw - s) // 2, (bh - s) // 2, (bw + s) // 2, (bh + s) // 2))
                    bg = bg.resize((SIZE, SIZE), RESAMPLING_LANCZOS)
                    # Flou léger + désaturation partielle pour laisser respirer le texte
                    bg = bg.filter(ImageFilter.GaussianBlur(radius=SIZE * 0.008))
                    img = bg
                    self.logger.debug(f"  🖼️ Fond: {bg_path.name}")
                except Exception:
                    img = None

            if img is None:
                img = _make_mesh_gradient(SIZE, c1, c2, seed=title)

            # ── Vignette douce pour focaliser le regard ──
            img = _add_vignette(img, strength=0.35)

            # ── Grain de film discret ──
            img = _add_film_grain(img, intensity=8)

            img = img.convert('RGBA')

            # ── Nettoyage du titre ──
            display_title = title
            if bool(self.poster_style.get("strip_auto_prefix", True)):
                display_title = display_title.replace(AUTO_PLAYLIST_PREFIX, '').strip()
            if bool(self.poster_style.get("strip_count_suffix", True)):
                display_title = re.sub(r'\s*\(\d+ titres?\)\s*$', '', display_title)
            # Nettoyage emojis dans le titre (pour la typo)
            clean_title = EMOJI_CHARS_RE.sub('', display_title).strip()
            if not clean_title:
                clean_title = display_title

            # ── Typographie : tailles adaptatives selon la longueur ──
            # Auto-fit : on essaie de rentrer en 1-3 lignes max
            def _wrap_lines(text, font, max_w, draw_ref):
                words = text.split()
                lines, cur = [], ""
                for w in words:
                    test = (cur + " " + w).strip()
                    bb = draw_ref.textbbox((0, 0), test, font=font)
                    if bb[2] - bb[0] > max_w and cur:
                        lines.append(cur)
                        cur = w
                    else:
                        cur = test
                if cur:
                    lines.append(cur)
                return lines

            draw_tmp = ImageDraw.Draw(img)
            max_text_w = int(SIZE * 0.82)
            # Démarre grand puis réduit jusqu'à tenir en max 3 lignes
            title_size = int(SIZE * 0.13)
            min_title_size = int(SIZE * 0.06)
            font_title = None
            lines = []
            while title_size >= min_title_size:
                try:
                    font_title = ImageFont.truetype(FONT_PATH, title_size)
                except Exception:
                    font_title = ImageFont.load_default()
                    break
                lines = _wrap_lines(clean_title, font_title, max_text_w, draw_tmp)
                if len(lines) <= 3:
                    # Vérifie aussi qu'aucune ligne ne déborde (mot très long)
                    overflow = any(
                        draw_tmp.textbbox((0, 0), ln, font=font_title)[2] > max_text_w
                        for ln in lines
                    )
                    if not overflow:
                        break
                title_size = int(title_size * 0.92)

            # Polices secondaires
            try:
                font_kicker = ImageFont.truetype(FONT_PATH, max(14, int(SIZE * 0.026)))
                font_meta = ImageFont.truetype(FONT_PATH, max(16, int(SIZE * 0.030)))
            except Exception:
                font_kicker = font_meta = ImageFont.load_default()

            # ── Layout centré + 1 élément graphique "surprise" piloté par seed ──
            padding = int(SIZE * 0.07)
            cx_img = SIZE // 2

            line_h = int(title_size * 1.05)
            total_title_h = line_h * len(lines)

            leaf_count = playlist.get('leafCount', '')
            meta_text = f"{leaf_count} titres" if leaf_count else ""
            kicker_text = self._poster_kicker_text(clean_title)

            # RNG dédié au layout (différent du seed du mesh pour varier)
            layout_rng = random.Random(hashlib.md5(("layout-" + title).encode("utf-8")).hexdigest())
            layout_variant = layout_rng.choice(["ghost_word", "big_circle", "halo_ring", "side_bar"])

            palette = _build_palette(c1, c2, layout_rng)
            accent = _adjust(_hex_to_rgb(c2), dl=+0.10, ds=+0.15)
            accent_light = _adjust(_hex_to_rgb(c1), dl=+0.25, ds=+0.05)

            # ── ÉLÉMENT GRAPHIQUE SURPRISE (sous le scrim/texte) ──
            decor = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
            ddraw = ImageDraw.Draw(decor)

            if layout_variant == "big_circle":
                # Énorme cercle qui déborde d'un coin
                r = int(SIZE * layout_rng.uniform(0.55, 0.75))
                corner = layout_rng.choice(["tl", "tr", "bl", "br"])
                cx, cy = {
                    "tl": (-int(r*0.3), -int(r*0.3)),
                    "tr": (SIZE + int(r*0.3), -int(r*0.3)),
                    "bl": (-int(r*0.3), SIZE + int(r*0.3)),
                    "br": (SIZE + int(r*0.3), SIZE + int(r*0.3)),
                }[corner]
                ddraw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(*accent_light, 55))
                # Contour fin
                ddraw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(*accent_light, 90), width=max(2, SIZE//300))
                decor = decor.filter(ImageFilter.GaussianBlur(radius=2))

            elif layout_variant == "halo_ring":
                # Anneaux concentriques fins centrés autour du titre
                cy = int(SIZE * 0.5)
                base_r = int(SIZE * 0.42)
                for i in range(4):
                    r = base_r + i * int(SIZE * 0.045)
                    ddraw.ellipse(
                        [cx_img-r, cy-r, cx_img+r, cy+r],
                        outline=(*accent_light, 80 - i*15),
                        width=max(1, SIZE//500),
                    )

            elif layout_variant == "side_bar":
                # Deux gros traits verticaux qui encadrent
                bar_w = int(SIZE * 0.012)
                bar_h = int(SIZE * 0.55)
                bar_y = (SIZE - bar_h) // 2
                inset = int(SIZE * 0.10)
                ddraw.rectangle([inset, bar_y, inset+bar_w, bar_y+bar_h], fill=(*accent, 180))
                ddraw.rectangle([SIZE-inset-bar_w, bar_y, SIZE-inset, bar_y+bar_h], fill=(*accent, 180))

            # ghost_word : géré plus bas (besoin du clean_title et de la fonte)

            img.alpha_composite(decor)

            # ── Mot fantôme géant en arrière-plan (pour variant ghost_word) ──
            if layout_variant == "ghost_word":
                try:
                    ghost_word = kicker_text.upper() if kicker_text else clean_title.split()[0].upper()
                    # Taille gigantesque (auto-fit à la largeur)
                    ghost_size = int(SIZE * 0.48)
                    while ghost_size > 60:
                        gf = ImageFont.truetype(FONT_PATH, ghost_size)
                        gw = ImageDraw.Draw(img).textbbox((0, 0), ghost_word, font=gf)
                        if gw[2] - gw[0] <= SIZE * 1.05:
                            break
                        ghost_size = int(ghost_size * 0.9)
                    ghost_layer = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
                    gd = ImageDraw.Draw(ghost_layer)
                    gw = gd.textbbox((0, 0), ghost_word, font=gf)
                    tw = gw[2] - gw[0]
                    th = gw[3] - gw[1]
                    gx = (SIZE - tw) // 2 - gw[0]
                    gy = (SIZE - th) // 2 - gw[1]
                    gd.text((gx, gy), ghost_word, font=gf, fill=(*accent_light, 60))
                    img.alpha_composite(ghost_layer)
                except Exception:
                    pass

            # ── Plaque sombre dégradée en bas (lisibilité du texte) ──
            scrim_h = int(SIZE * 0.50)
            scrim = Image.new('L', (SIZE, scrim_h), 0)
            sd = ImageDraw.Draw(scrim)
            for y in range(scrim_h):
                a = int(200 * (y / scrim_h) ** 1.8)
                sd.line([(0, y), (SIZE, y)], fill=a)
            scrim_rgba = Image.new('RGBA', (SIZE, scrim_h), (0, 0, 0, 0))
            scrim_rgba.putalpha(scrim)
            img.alpha_composite(scrim_rgba, (0, SIZE - scrim_h))

            draw = ImageDraw.Draw(img)

            # ── Bloc texte centré en bas ──
            block_h = total_title_h
            if meta_text:
                block_h += int(font_meta.size * 1.9)
            if kicker_text:
                block_h += int(font_kicker.size * 2.4)

            title_y = SIZE - padding - total_title_h
            if meta_text:
                title_y -= int(font_meta.size * 1.9)

            # ── Kicker centré : barre — TEXT — barre ──
            if kicker_text:
                kicker_y = title_y - int(font_kicker.size * 2.2)
                ktext = kicker_text.upper()
                kbb = draw.textbbox((0, 0), ktext, font=font_kicker)
                ktw = kbb[2] - kbb[0]
                bar_w = int(SIZE * 0.06)
                bar_h = max(2, int(SIZE * 0.004))
                gap = int(SIZE * 0.018)
                total_w = bar_w * 2 + gap * 2 + ktw
                kx_start = (SIZE - total_w) // 2
                bar_y = kicker_y + int(font_kicker.size * 0.55)
                # barre gauche
                draw.rectangle(
                    [kx_start, bar_y, kx_start + bar_w, bar_y + bar_h],
                    fill=(*accent, 230),
                )
                # texte
                draw.text(
                    (kx_start + bar_w + gap, kicker_y),
                    ktext,
                    font=font_kicker,
                    fill=(245, 245, 245, 235),
                )
                # barre droite
                draw.rectangle(
                    [kx_start + bar_w + gap * 2 + ktw, bar_y,
                     kx_start + bar_w + gap * 2 + ktw + bar_w, bar_y + bar_h],
                    fill=(*accent, 230),
                )

            # ── Titre centré (lignes empilées) ──
            y = title_y
            for ln in lines:
                lb = draw.textbbox((0, 0), ln, font=font_title)
                lw = lb[2] - lb[0]
                tx = (SIZE - lw) // 2 - lb[0]
                # Ombres multi-couches pour plus de profondeur
                try:
                    for off, blur, a in ((6, 10, 120), (3, 6, 90), (1, 2, 60)):
                        shadow_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
                        sd_draw = ImageDraw.Draw(shadow_layer)
                        sd_draw.text((tx + off, y + off), ln, font=font_title, fill=(0, 0, 0, a))
                        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=blur))
                        img.alpha_composite(shadow_layer)
                except Exception:
                    # Fallback single subtle shadow
                    shadow_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
                    sd_draw = ImageDraw.Draw(shadow_layer)
                    sd_draw.text((tx + 2, y + 4), ln, font=font_title, fill=(0, 0, 0, 170))
                    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=5))
                    img.alpha_composite(shadow_layer)

                # Draw stroked text if Pillow supports stroke params, otherwise emulate
                try:
                    draw.text((tx, y), ln, font=font_title, fill=TITLE_COLOR, stroke_width=max(1, int(self.poster_style.get('title_stroke_width', 2))), stroke_fill=self.poster_style.get('title_shadow_color', (0,0,0,180)))
                except TypeError:
                    # emulate stroke by drawing text multiple times offset by 1px
                    sw = max(1, int(self.poster_style.get('title_stroke_width', 2)))
                    for sx in range(-sw, sw+1):
                        for sy in range(-sw, sw+1):
                            if sx == 0 and sy == 0:
                                continue
                            draw.text((tx + sx, y + sy), ln, font=font_title, fill=self.poster_style.get('title_shadow_color', (0,0,0,180)))
                    draw.text((tx, y), ln, font=font_title, fill=TITLE_COLOR)
                y += line_h

            # ── Méta centrée : • {N} titres • ──
            if meta_text:
                meta_y = y + int(font_meta.size * 0.5)
                mtext = f"·  {meta_text}  ·"
                mbb = draw.textbbox((0, 0), mtext, font=font_meta)
                mtw = mbb[2] - mbb[0]
                mx = (SIZE - mtw) // 2 - mbb[0]
                draw.text((mx, meta_y), mtext, font=font_meta, fill=(225, 225, 225, 215))

            # ── Emoji badge en haut centré (rond plus clair derrière) ──
            if emoji and bool(self.poster_style.get("show_emoji_badge", True)):
                try:
                    # NotoColorEmoji est bitmap-only : size DOIT être 109 (ou multiple)
                    em_canvas = 220
                    try:
                        emoji_font = ImageFont.truetype(EMOJI_FONT_PATH, 109)
                    except OSError:
                        # Certaines installs acceptent d'autres tailles
                        emoji_font = ImageFont.truetype(EMOJI_FONT_PATH, 96)
                    em_img = Image.new('RGBA', (em_canvas, em_canvas), (0, 0, 0, 0))
                    em_draw = ImageDraw.Draw(em_img)
                    try:
                        em_draw.text((0, 0), emoji, font=emoji_font, embedded_color=True)
                    except TypeError:
                        em_draw.text((0, 0), emoji, font=emoji_font)
                    bbox = em_img.getbbox()
                    if bbox:
                        em_img = em_img.crop(bbox)
                        target = int(SIZE * 0.13)
                        ratio = target / max(em_img.size)
                        nw = max(1, int(em_img.size[0] * ratio))
                        nh = max(1, int(em_img.size[1] * ratio))
                        em_img = em_img.resize((nw, nh), Image.LANCZOS)
                        # Cercle subtil derrière
                        circle_d = int(max(nw, nh) * 1.55)
                        # Cercle subtil derrière
                        circle = Image.new('RGBA', (circle_d, circle_d), (0, 0, 0, 0))
                        ImageDraw.Draw(circle).ellipse(
                            [0, 0, circle_d-1, circle_d-1],
                            fill=(255, 255, 255, 25),
                        )
                        # Halo lumineux coloré (accent)
                        try:
                            glow = Image.new('RGBA', (circle_d*2, circle_d*2), (0, 0, 0, 0))
                            ImageDraw.Draw(glow).ellipse([0, 0, circle_d*2-1, circle_d*2-1], fill=(*accent_light, 40))
                            glow = glow.filter(ImageFilter.GaussianBlur(radius=12))
                            top_y = int(SIZE * 0.10) - circle_d//2
                            gx = (SIZE - glow.size[0]) // 2
                            img.alpha_composite(glow, (gx, top_y))
                        except Exception:
                            pass
                        # Floute légèrement le halo du cercle principal
                        circle = circle.filter(ImageFilter.GaussianBlur(radius=4))
                        # Centré en haut
                        top_y = int(SIZE * 0.10)
                        cx_pos = (SIZE - circle_d) // 2
                        img.alpha_composite(circle, (cx_pos, top_y))
                        img.alpha_composite(
                            em_img,
                            (cx_pos + (circle_d - nw) // 2, top_y + (circle_d - nh) // 2),
                        )
                except Exception as e:
                    self.logger.debug(f"emoji skip: {e}")

            img = img.convert('RGB')

            # Encoder en PNG en mémoire
            buf = io.BytesIO()
            img.save(buf, format='PNG', optimize=True)
            png_data = buf.getvalue()

            # Upload via API
            try:
                upload_url = f"{PLEX_URL}/library/metadata/{rk}/posters?X-Plex-Token={PLEX_TOKEN}"
                req = urllib.request.Request(upload_url, data=png_data, method='POST')
                req.add_header('Content-Type', 'image/png')
                urllib.request.urlopen(req)
                ok += 1
                self.logger.debug(f"  🖼️ {title}")
            except Exception as e:
                fail += 1
                self.logger.warning(f"  ⚠️ Poster échoué pour {title}: {e}")

            # Restaure le style global après rotation
            if ROTATE_ENABLED:
                self.poster_style = saved_style

        self.logger.info(f"🖼️ Posters: {ok} appliqués, {fail} échoués sur {len(playlists)}")

    def _poster_kicker_text(self, title: str) -> str:
        """Choisit un petit label en majuscules à afficher au-dessus du titre."""
        t = title.lower()
        rules = [
            (['80s', 'stars 80', 'années 80'], 'Throwback'),
            (['90s', '90', 'années 90'], '90s'),
            (['2000', '00s'], '2000s'),
            (['running', 'workout', 'cardio', 'sport'], 'Energy'),
            (['chill', 'relax', 'lofi', 'lo-fi'], 'Chill'),
            (['focus', 'study', 'concentration', 'work'], 'Focus'),
            (['party', 'fête', 'club', 'dance'], 'Party'),
            (['love', 'romantic', 'amour'], 'Mood'),
            (['sad', 'mélancolie', 'melancholy'], 'Mood'),
            (['top', 'best', 'meilleur'], 'Top picks'),
            (['discovery', 'discover', 'new'], 'New'),
            (['radio', 'fun radio', 'nrj'], 'Radio'),
            (['jazz', 'blues'], 'Smooth'),
            (['rock', 'metal'], 'Rock'),
            (['rap', 'hip-hop', 'hip hop'], 'Beats'),
            (['electro', 'electronic', 'synth', 'techno', 'house'], 'Electronic'),
        ]
        for keys, label in rules:
            if any(k in t for k in keys):
                return label
        return 'Playlist'

    def report_missing_year(
        self,
        tracks: Optional[List[Dict]] = None,
        output_file: Optional[str] = None,
    ) -> List[Dict]:
        """Génère un mini rapport des pistes sans tag année.

        Utile pour identifier les fichiers dont les métadonnées sont à corriger
        à la source (MusicBrainz Picard, beets, etc.).

        Args:
            tracks: Liste de pistes déjà chargée ; si None, charge depuis la DB.
            output_file: Chemin d'un fichier CSV de sortie (optionnel).

        Returns:
            Liste des pistes sans année.
        """
        import csv as _csv

        if tracks is None:
            tracks = self.get_track_data()

        missing = [t for t in tracks if not (t.get('year') or 0)]
        total = len(tracks)
        pct = round(100 * len(missing) / max(1, total), 1)

        sep = "=" * 60
        self.logger.info(f"\n📅 RAPPORT TAGS ANNÉE MANQUANTS")
        self.logger.info(sep)
        self.logger.info(f"  Total pistes   : {total}")
        self.logger.info(f"  Sans année     : {len(missing)} ({pct} %)")
        self.logger.info(f"  Avec année     : {total - len(missing)}")

        if not missing:
            self.logger.info("  ✅ Toutes les pistes ont un tag année !")
            return []

        # Regroupement par artiste
        by_artist: Dict[str, List[Dict]] = {}
        for t in missing:
            artist = (t.get('artist') or 'Unknown Artist').strip()
            by_artist.setdefault(artist, []).append(t)

        top_n = sorted(by_artist.items(), key=lambda x: -len(x[1]))[:20]
        self.logger.info(f"\n  Top artistes avec le plus de pistes sans année :")
        for artist, atracks in top_n:
            sample_albums = {(t.get('album') or '?') for t in atracks}
            albums_str = ', '.join(sorted(sample_albums)[:3])
            if len(sample_albums) > 3:
                albums_str += f' … (+{len(sample_albums) - 3})'
            self.logger.info(f"    {artist:40s} {len(atracks):4d} piste(s)  [{albums_str}]")

        # Regroupement par album (top 10)
        by_album: Dict[str, List[Dict]] = {}
        for t in missing:
            key = f"{(t.get('artist') or '?').strip()} — {(t.get('album') or '?').strip()}"
            by_album.setdefault(key, []).append(t)
        top_albums = sorted(by_album.items(), key=lambda x: -len(x[1]))[:10]
        self.logger.info(f"\n  Top albums/artistes à corriger :")
        for key, atracks in top_albums:
            self.logger.info(f"    {key[:70]:70s}  {len(atracks):3d} piste(s)")

        # Export CSV
        if output_file:
            out_path = Path(output_file)
            fieldnames = ['id', 'title', 'artist', 'album', 'genre', 'play_count', 'rating', 'file_path']
            try:
                with open(out_path, 'w', newline='', encoding='utf-8') as f:
                    writer = _csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                    writer.writeheader()
                    for t in missing:
                        writer.writerow({k: t.get(k, '') for k in fieldnames})
                self.logger.info(f"\n  📁 Export CSV : {out_path} ({len(missing)} lignes)")
            except Exception as e:
                self.logger.error(f"  ❌ Export CSV échoué : {e}")

        self.logger.info(sep)
        return missing

    def generate_all_playlists(self, save_to_plex: bool = True,
                               append_existing: bool = False,
                               custom_config: Optional[str] = None,
                               selected_names: Optional[List[str]] = None) -> Dict[str, int]:
        """Génère toutes les playlists automatiques"""
        self.logger.info("🎵 Génération des playlists automatiques PlexAmp")
        
        # Charger les données
        tracks = self.get_track_data()
        if not tracks:
            self.logger.error("❌ Aucune piste trouvée")
            return {}
        
        # Générer toutes les playlists
        all_playlists = self._build_all_playlists(tracks, custom_config=custom_config)

        # Fusion automatique des groupes de playlists très similaires (>60%)
        playlist_tracks = {name: set(t['id'] for t in entries) for name, entries in all_playlists.items()}
        names = list(playlist_tracks.keys())
        n = len(names)
        merged = set()
        fusion_groups = []
        for i in range(n):
            if names[i] in merged:
                continue
            base = names[i]
            base_ids = playlist_tracks[base]
            group = [base]
            for j in range(i+1, n):
                other = names[j]
                if other in merged:
                    continue
                other_ids = playlist_tracks[other]
                if not base_ids or not other_ids:
                    continue
                overlap = len(base_ids & other_ids) / min(len(base_ids), len(other_ids))
                if overlap > 0.6:
                    group.append(other)
                    merged.add(other)
            if len(group) > 1:
                fusion_groups.append(group)
                merged.update(group)
        # Fusionne les groupes
        for group in fusion_groups:
            tracks_union = set()
            for name in group:
                tracks_union.update(playlist_tracks[name])
            # Prend le nom du premier, ajoute "+fusion"
            main_name = group[0] + " [fusion]"
            # Récupère les objets piste
            id_to_track = {t['id']: t for t in tracks}
            merged_tracks = [id_to_track[tid] for tid in tracks_union if tid in id_to_track]
            all_playlists[main_name] = merged_tracks
            # Supprime les originaux
            for name in group:
                if name in all_playlists:
                    del all_playlists[name]
        # Mise à jour pour la suite
        playlist_tracks = {name: set(t['id'] for t in entries) for name, entries in all_playlists.items()}
        names = list(playlist_tracks.keys())

        if selected_names:
            def _norm_sel(n: str) -> str:
                s = self._strip_emojis(str(n).strip())
                return re.sub(r'\s*\(\d+\s+titres\)\s*$', '', s, flags=re.IGNORECASE).strip()
            selected_set = {_norm_sel(n) for n in selected_names if str(n).strip()}
            all_playlists = {
                name: entries
                for name, entries in all_playlists.items()
                if name in selected_set
            }
            self.logger.info(f"🎯 Filtre selection active: {len(all_playlists)} playlist(s) retenue(s)")

        # Nettoyer uniquement les playlists qui vont être régénérées (pas en mode ajout)
        if save_to_plex and not append_existing:
            self.cleanup_old_auto_playlists(new_playlist_names=list(all_playlists.keys()))
        self._last_tracks = tracks
        self._last_playlists = all_playlists

        # Statistiques et export
        created_count = 0
        results = {}

        # Sauvegarder dans Plex si demandé
        if save_to_plex:
            for playlist_name, playlist_tracks in all_playlists.items():
                if self.save_playlist_to_plex(playlist_name, playlist_tracks, append_existing=append_existing):
                    created_count += 1
                    results[playlist_name] = len(playlist_tracks)
        else:
            # Mode simulation
            for playlist_name, playlist_tracks in all_playlists.items():
                self.logger.info(f"📋 {playlist_name}: {len(playlist_tracks)} titres")
                results[playlist_name] = len(playlist_tracks)
        self.logger.info(f"\n🎉 {created_count if save_to_plex else len(all_playlists)} playlists {'créées' if save_to_plex else 'simulées'}")

        # Générer les posters pour toutes les playlists
        if save_to_plex and not getattr(self, '_no_posters', False):
            self.generate_playlist_posters()

        return results

# Chemin par défaut de l'export M3U
DEFAULT_M3U_EXPORT_DIR = os.getenv('PLAYLISTS_DIR', '/mnt/ssd/Musiques/Playlists')

def main():
    parser = argparse.ArgumentParser(description="Générateur de playlists automatiques PlexAmp")
    parser.add_argument("--report-overlap", nargs='?', const='-', metavar='CSV_FILE',
        help="Affiche un rapport des groupes de playlists avec >60% de morceaux en commun. Export CSV si chemin fourni.")
    default_plex_db = os.getenv(
        "PLEX_DB",
        "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
    )
    parser.add_argument("--plex-db", 
                       default=default_plex_db,
                       help="Chemin vers la base de données Plex")
    parser.add_argument("--dry-run", action="store_true",
                       help="Mode simulation (ne crée pas les playlists)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Mode verbeux")
    parser.add_argument("--export-dir",
                       default=DEFAULT_M3U_EXPORT_DIR,
                       help="Répertoire d'export des fichiers M3U")
    parser.add_argument("--no-export", action="store_true",
                       help="Ne pas exporter en M3U")
    parser.add_argument("--no-posters", action="store_true",
                       help="Ne pas générer les images de poster")
    parser.add_argument("--posters-only", action="store_true",
                       help="Regénérer uniquement les posters (sans toucher aux playlists)")
    parser.add_argument("--poster-style-config",
                       default="",
                       help="Chemin vers un JSON de style posters (équivaut à PLEX_POSTER_STYLE_CONFIG)")
    parser.add_argument("--usb", nargs='?', const='/media/paulceline/MUSIC',
                       help="Exporter Stars 80 et Top 50 vers clé USB (défaut: /media/paulceline/MUSIC)")
    parser.add_argument("--usb-playlists", nargs='+', default=['Stars 80', 'Top 50'],
                       help="Noms des playlists à exporter sur USB (défaut: Stars 80, Top 50)")
    parser.add_argument("--append-existing", action="store_true",
                       help="Ajoute les nouvelles pistes aux playlists existantes au lieu de les recréer")
    parser.add_argument("--custom-config",
                       default=os.getenv("PLEX_CUSTOM_PLAYLISTS_CONFIG", ""),
                       help="Chemin vers un fichier JSON de playlists personnalisées")
    parser.add_argument("--report-missing-year", nargs='?', const='-', metavar='CSV_FILE',
                       help="Affiche un rapport des pistes sans tag année. "
                            "Fournir un chemin pour exporter en CSV (ex. --report-missing-year rapport.csv). "
                            "Sortie console seule si omis.")
    parser.add_argument("--lastfm-user",
                       default=os.getenv("LASTFM_USER", ""),
                       help="Nom d'utilisateur Last.fm")
    parser.add_argument("--lastfm-api-key",
                       default=os.getenv("LASTFM_API_KEY", ""),
                       help="API key Last.fm")
    parser.add_argument("--lastfm-period",
                       default=os.getenv("LASTFM_PERIOD", "overall"),
                       choices=["overall", "7day", "1month", "3month", "6month", "12month"],
                       help="Période Last.fm pour user.getTopTracks")
    parser.add_argument("--lastfm-max-pages", type=int,
                       default=int(os.getenv("LASTFM_MAX_PAGES", "5") or 5),
                       help="Nombre de pages Last.fm à récupérer (1000 tracks/page)")
    parser.add_argument("--only-playlists", nargs='+', default=[],
                       help="Noms exacts des playlists a generer/synchroniser")
    parser.add_argument("--selected-names-file", default="",
                       help="Fichier JSON contenant {selected_names:[...]} pour limiter les playlists synchronisées")
    parser.add_argument("--randomize-poster-styles", action="store_true",
                       default=os.getenv("PLEX_RANDOMIZE_POSTER_STYLES", "").lower() in ("1", "true", "yes"),
                       help="Choisir aléatoirement le style de poster pour chaque playlist au lieu de mode déterministe")

    args = parser.parse_args()
    if args.report_overlap is not None:
        csv_out = None if args.report_overlap == '-' else args.report_overlap
        selected_names_file = args.selected_names_file if args.selected_names_file else None
        PlexAmpAutoPlaylist(args.plex_db, args.verbose).report_overlap(output_file=csv_out, selected_names_file=selected_names_file)
        return

    if args.poster_style_config:
        os.environ["PLEX_POSTER_STYLE_CONFIG"] = args.poster_style_config

    generator = PlexAmpAutoPlaylist(
        args.plex_db,
        verbose=args.verbose,
        lastfm_user=args.lastfm_user,
        lastfm_api_key=args.lastfm_api_key,
        lastfm_period=args.lastfm_period,
        lastfm_max_pages=args.lastfm_max_pages,
        randomize_poster_styles=args.randomize_poster_styles,
    )
    generator._no_posters = args.no_posters

    # Mode rapport tags année manquants (exclusif)
    if args.report_missing_year is not None:
        csv_out = None if args.report_missing_year == '-' else args.report_missing_year
        generator.report_missing_year(output_file=csv_out)
        return

    # Mode posters uniquement
    if args.posters_only:
        generator.generate_playlist_posters()
        return

    selected_names: List[str] = [str(x).strip() for x in (args.only_playlists or []) if str(x).strip()]
    selected_file_loaded = False
    selected_file = str(args.selected_names_file or "").strip()
    if selected_file:
        try:
            with open(selected_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            file_names = payload.get("selected_names") if isinstance(payload, dict) else None
            if isinstance(file_names, list):
                selected_file_loaded = True
                selected_names.extend(str(x).strip() for x in file_names if str(x).strip())
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"⚠️ Impossible de lire --selected-names-file: {exc}")

    if selected_names:
        selected_names = sorted(set(selected_names))

    if selected_file_loaded and not selected_names:
        print("ℹ️ Aucune playlist selectionnee dans le fichier: rien a synchroniser.")
        return

    # generate_all_playlists gère le dry-run et le nettoyage des anciennes
    results = generator.generate_all_playlists(
        save_to_plex=not args.dry_run,
        append_existing=args.append_existing,
        custom_config=args.custom_config or None,
        selected_names=selected_names or None,
    )

    # Export M3U (réutilise les données déjà en mémoire)
    if not args.dry_run and not args.no_export and results:
        all_playlists = generator._last_playlists or generator._build_all_playlists(
            generator._last_tracks or generator.get_track_data(),
            custom_config=args.custom_config or None,
        )
        generator.export_playlists_m3u(all_playlists, args.export_dir)

    # Export USB si demandé
    if args.usb:
        generator.export_playlists_to_usb(args.usb_playlists, args.usb)

    # Résumé
    print(f"\n📊 RÉSUMÉ: {len(results)} playlists")
    print("=" * 50)
    for playlist_name, count in results.items():
        print(f"  {playlist_name}: {count} titres")
    total = sum(results.values())
    print(f"\n  Total: {total} pistes (avec doublons inter-playlists)")

if __name__ == "__main__":
    main()