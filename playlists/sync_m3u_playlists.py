#!/home/paulceline/bin/plex-ratings-sync/.venv/bin/python
"""
Script de synchronisation bidirectionnelle des playlists M3U
- Plex → M3U : Export des playlists Plex vers fichiers M3U
- M3U → Plex : Import des fichiers M3U vers Plex (via API)
"""

import sqlite3
import json
import sys
import argparse
import re
from pathlib import Path
from typing import List, Dict, Optional
import logging
from urllib.parse import quote, unquote

class M3UPlaylistSync:
    def __init__(self, plex_db_path: str, m3u_dir: str, music_library_path: str, verbose: bool = False):
        self.plex_db_path = Path(plex_db_path)
        self.m3u_dir = Path(m3u_dir)
        self.music_library_path = Path(music_library_path)
        self.verbose = verbose
        self.setup_logging()
        
        # Créer le répertoire M3U si nécessaire
        self.m3u_dir.mkdir(parents=True, exist_ok=True)
        
    def setup_logging(self):
        log_format = '%(asctime)s - %(levelname)s - %(message)s'
        level = logging.DEBUG if self.verbose else logging.INFO
        logging.basicConfig(
            level=level,
            format=log_format,
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        self.logger = logging.getLogger(__name__)

    def _connect_db(self):
        """Crée une connexion DB avec collation icu_root et text_factory."""
        conn = sqlite3.connect(str(self.plex_db_path))
        conn.create_collation('icu_root', lambda a, b: (a > b) - (a < b))
        conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
        return conn

    def get_smart_playlists_from_ratings(self) -> List[Dict]:
        """Crée des playlists intelligentes basées sur les ratings"""
        try:
            with self._connect_db() as conn:
                cursor = conn.cursor()
                
                # Requête pour obtenir les fichiers par rating
                query = """
                SELECT 
                    mi.title as track_title,
                    mis.rating as user_rating,
                    mis.view_count as play_count,
                    mp.file as file_path,
                    parent_mi.title as album_title,
                    grandparent_mi.title as artist_name
                FROM metadata_items mi
                LEFT JOIN media_items media ON mi.id = media.metadata_item_id
                LEFT JOIN media_parts mp ON media.id = mp.media_item_id
                LEFT JOIN metadata_items parent_mi ON mi.parent_id = parent_mi.id
                LEFT JOIN metadata_items grandparent_mi ON parent_mi.parent_id = grandparent_mi.id
                LEFT JOIN metadata_item_settings mis ON mi.guid = mis.guid
                WHERE mi.metadata_type = 10
                AND mp.file IS NOT NULL
                AND mis.rating IS NOT NULL
                ORDER BY mis.rating DESC, mis.view_count DESC, grandparent_mi.title, mi.title
                """
                
                cursor.execute(query)
                rows = cursor.fetchall()
                
                # Organiser par rating
                playlists = {}
                for row in rows:
                    track_title, rating, play_count, file_path, album_title, artist_name = row
                    
                    # Normaliser le rating (Plex peut utiliser 1-10)
                    if rating > 5:
                        stars = int(rating / 2)  # 10 → 5, 8 → 4, etc.
                    else:
                        stars = int(rating)
                    
                    playlist_name = f"{stars}_étoiles"
                    if playlist_name not in playlists:
                        playlists[playlist_name] = {
                            'name': f"★{stars} Étoiles",
                            'description': f"Titres avec {stars} étoiles dans PlexAmp",
                            'tracks': []
                        }
                    
                    playlists[playlist_name]['tracks'].append({
                        'file_path': file_path,
                        'title': track_title or 'Unknown',
                        'artist': artist_name or 'Unknown Artist',
                        'album': album_title or 'Unknown Album',
                        'rating': rating,
                        'play_count': play_count or 0
                    })
                
                # Créer aussi des playlists par popularité
                most_played = [row for row in rows if row[2] and row[2] > 2]  # Plus de 2 écoutes
                if most_played:
                    playlists['most_played'] = {
                        'name': "🎧 Plus Écoutés",
                        'description': "Titres les plus écoutés (>2 fois)",
                        'tracks': []
                    }
                    
                    for row in most_played[:100]:  # Top 100
                        track_title, rating, play_count, file_path, album_title, artist_name = row
                        playlists['most_played']['tracks'].append({
                            'file_path': file_path,
                            'title': track_title or 'Unknown',
                            'artist': artist_name or 'Unknown Artist',
                            'album': album_title or 'Unknown Album',
                            'rating': rating,
                            'play_count': play_count or 0
                        })
                
                self.logger.info(f"📊 Créé {len(playlists)} playlists intelligentes depuis Plex")
                return list(playlists.values())
                
        except Exception as e:
            self.logger.error(f"❌ Erreur lecture playlists Plex: {e}")
            return []

    def export_playlist_to_m3u(self, playlist: Dict, extended: bool = True) -> bool:
        """Exporte une playlist vers un fichier M3U"""
        try:
            # Nom de fichier sûr
            safe_name = re.sub(r'[^\w\s-]', '', playlist['name']).strip()
            safe_name = re.sub(r'[-\s]+', '_', safe_name)
            
            m3u_file = self.m3u_dir / f"{safe_name}.m3u8"
            
            with open(m3u_file, 'w', encoding='utf-8') as f:
                if extended:
                    f.write("#EXTM3U\n")
                    f.write(f"#PLAYLIST:{playlist['name']}\n")
                    if playlist.get('description'):
                        f.write(f"#DESCRIPTION:{playlist['description']}\n")
                    f.write("\n")
                
                for track in playlist['tracks']:
                    file_path = Path(track['file_path'])
                    
                    # Convertir le chemin Plex vers chemin réel si nécessaire
                    if not file_path.exists():
                        # Essayer de mapper le chemin
                        relative_path = self.map_plex_path_to_real(track['file_path'])
                        if relative_path and relative_path.exists():
                            file_path = relative_path
                        else:
                            self.logger.warning(f"⚠️ Fichier introuvable: {track['file_path']}")
                            continue
                    
                    if extended:
                        # Durée en secondes (estimation ou -1)
                        duration = -1
                        
                        # Ligne EXTINF avec métadonnées
                        artist = track.get('artist', 'Unknown Artist')
                        title = track.get('title', 'Unknown Title')
                        f.write(f"#EXTINF:{duration},{artist} - {title}\n")
                        
                        # Informations étendues
                        if track.get('album'):
                            f.write(f"#EXTALB:{track['album']}\n")
                        if track.get('rating'):
                            f.write(f"#EXTGENRE:Rating-{track['rating']}\n")
                        if track.get('play_count'):
                            f.write(f"#EXTGENRE:PlayCount-{track['play_count']}\n")
                    
                    # Chemin du fichier (relatif ou absolu)
                    f.write(f"{file_path}\n")
                    if extended:
                        f.write("\n")
            
            self.logger.info(f"✅ Playlist exportée: {m3u_file.name} ({len(playlist['tracks'])} titres)")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Erreur export playlist {playlist['name']}: {e}")
            return False

    def map_plex_path_to_real(self, plex_path: str) -> Optional[Path]:
        """Mappe un chemin Plex vers le chemin réel du système"""
        # Plex peut avoir des chemins comme /data/music/... qu'il faut mapper
        # vers votre vraie bibliothèque /mnt/MyBook/itunes/Music/...
        
        # Extraire juste le nom du fichier et chercher dans la bibliothèque
        file_name = Path(plex_path).name
        
        # Recherche récursive du fichier
        for music_file in self.music_library_path.rglob(file_name):
            if music_file.is_file():
                return music_file
        
        return None

    def find_file_in_plex_db(self, track_title: str, artist_name: str = None, album_title: str = None) -> Optional[str]:
        """Recherche un fichier dans la base de données Plex basé sur le titre et les métadonnées"""
        try:
            with self._connect_db() as conn:
                cursor = conn.cursor()
                
                # Construire la requête de recherche
                query = """
                SELECT mp.file as file_path
                FROM metadata_items mi
                LEFT JOIN media_items media ON mi.id = media.metadata_item_id
                LEFT JOIN media_parts mp ON media.id = mp.media_item_id
                LEFT JOIN metadata_items parent_mi ON mi.parent_id = parent_mi.id
                LEFT JOIN metadata_items grandparent_mi ON parent_mi.parent_id = grandparent_mi.id
                WHERE mi.metadata_type = 10
                AND mp.file IS NOT NULL
                AND LOWER(mi.title) LIKE LOWER(?)
                """
                
                params = [f'%{track_title}%']
                
                # Ajouter des critères supplémentaires si disponibles
                if artist_name:
                    query += " AND LOWER(grandparent_mi.title) LIKE LOWER(?)"
                    params.append(f'%{artist_name}%')
                
                if album_title:
                    query += " AND LOWER(parent_mi.title) LIKE LOWER(?)"
                    params.append(f'%{album_title}%')
                
                query += " LIMIT 1"
                
                cursor.execute(query, params)
                result = cursor.fetchone()
                
                if result:
                    file_path = result[0]
                    if Path(file_path).exists():
                        return file_path
                
        except Exception as e:
            self.logger.debug(f"Erreur recherche Plex pour '{track_title}': {e}")
        
        return None

    def import_m3u_to_plex_json(self, m3u_file: Path) -> Dict:
        try:
            with open(m3u_file, 'r', encoding='utf-8') as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
            
            playlist = {
                'name': m3u_file.stem.replace('_', ' '),
                'description': f"Importée depuis {m3u_file.name}",
                'tracks': []
            }
            
            current_track = {}
            
            for line in lines:
                if line.startswith('#EXTM3U'):
                    continue
                elif line.startswith('#PLAYLIST:'):
                    playlist['name'] = line[10:].strip()
                elif line.startswith('#DESCRIPTION:'):
                    playlist['description'] = line[13:].strip()
                elif line.startswith('#EXTINF:'):
                    # Parser #EXTINF:durée,artiste - titre
                    parts = line[8:].split(',', 1)
                    if len(parts) > 1:
                        artist_title = parts[1].strip()
                        if ' - ' in artist_title:
                            artist, title = artist_title.split(' - ', 1)
                            current_track = {'artist': artist.strip(), 'title': title.strip()}
                elif line.startswith('#EXT'):
                    # Autres tags étendus
                    continue
                elif not line.startswith('#'):
                    # C'est un chemin de fichier - utiliser la recherche intelligente dans Plex
                    file_path_str = line.strip()
                    
                    # Ignorer les URLs HTTP/HTTPS (playlists IPTV)
                    if file_path_str.startswith(('http://', 'https://')):
                        self.logger.debug(f"⏭️ URL ignorée: {file_path_str}")
                        current_track = {}
                        continue
                    
                    # Essayer d'abord les chemins directs
                    file_path = None
                    
                    # Essayer comme chemin absolu
                    if Path(file_path_str).exists():
                        file_path = Path(file_path_str)
                    # Essayer comme chemin relatif par rapport au dossier M3U
                    elif (m3u_file.parent / file_path_str).exists():
                        file_path = (m3u_file.parent / file_path_str).resolve()
                    # Essayer comme chemin relatif par rapport à la bibliothèque musicale
                    elif (self.music_library_path / file_path_str).exists():
                        file_path = (self.music_library_path / file_path_str).resolve()
                    
                    if file_path and file_path.exists():
                        track_info = current_track.copy() if current_track else {}
                        track_info['file_path'] = str(file_path.absolute())
                        playlist['tracks'].append(track_info)
                        self.logger.debug(f"✅ Fichier trouvé directement: {file_path}")
                    else:
                        # Recherche intelligente dans la base de données Plex
                        track_title = current_track.get('title', '')
                        artist_name = current_track.get('artist', '')
                        
                        # Extraire le titre du nom de fichier si pas d'EXTINF
                        if not track_title:
                            # Enlever l'extension et le numéro de piste
                            track_title = Path(file_path_str).stem
                            track_title = re.sub(r'^\d+\s*-\s*', '', track_title)  # Enlever "01 - "
                            track_title = re.sub(r'^\d+\s+', '', track_title)  # Enlever "01 "
                        
                        plex_file = self.find_file_in_plex_db(track_title, artist_name)
                        
                        if plex_file:
                            track_info = current_track.copy() if current_track else {}
                            track_info['file_path'] = plex_file
                            playlist['tracks'].append(track_info)
                            self.logger.debug(f"✅ Fichier trouvé via Plex DB: {track_title} -> {Path(plex_file).name}")
                        else:
                            self.logger.warning(f"⚠️ Fichier introuvable: '{file_path_str}' (titre: '{track_title}', artiste: '{artist_name}')")
                    
                    current_track = {}
            
            self.logger.info(f"📥 M3U importé: {playlist['name']} ({len(playlist['tracks'])} titres)")
            return playlist
            
        except Exception as e:
            self.logger.error(f"❌ Erreur import M3U {m3u_file}: {e}")
            return {}

    def export_all_playlists(self) -> int:
        """Exporte toutes les playlists intelligentes vers M3U"""
        playlists = self.get_smart_playlists_from_ratings()
        exported = 0
        
        for playlist in playlists:
            if self.export_playlist_to_m3u(playlist):
                exported += 1
        
        self.logger.info(f"🎵 Export terminé: {exported} playlists M3U créées")
        return exported

    def import_all_m3u(self) -> int:
        """Importe tous les fichiers M3U du répertoire"""
        imported = 0
        json_dir = self.m3u_dir / 'plex_import'
        json_dir.mkdir(exist_ok=True)
        
        for m3u_file in self.m3u_dir.glob('*.m3u*'):
            if m3u_file.is_file():
                playlist = self.import_m3u_to_plex_json(m3u_file)
                if playlist and playlist['tracks']:
                    # Sauvegarder en JSON pour import manuel dans Plex
                    json_file = json_dir / f"{m3u_file.stem}.json"
                    with open(json_file, 'w', encoding='utf-8') as f:
                        json.dump(playlist, f, ensure_ascii=False, indent=2)
                    imported += 1
        
        self.logger.info(f"📥 Import terminé: {imported} playlists préparées pour Plex")
        self.logger.info(f"📁 Fichiers JSON: {json_dir}")
        return imported

    def sync_bidirectional(self) -> Dict:
        """Synchronisation bidirectionnelle complète"""
        self.logger.info("🔄 Synchronisation bidirectionnelle des playlists M3U")
        
        results = {
            'exported': self.export_all_playlists(),
            'imported': self.import_all_m3u(),
            'export_dir': str(self.m3u_dir),
            'import_dir': str(self.m3u_dir / 'plex_import')
        }
        
        self.logger.info(f"✅ Synchronisation terminée: {results['exported']} exportées, {results['imported']} importées")
        return results

def main():
    parser = argparse.ArgumentParser(
        description='Synchronisation bidirectionnelle des playlists M3U avec Plex'
    )
    parser.add_argument('--plex-db', 
                        default='/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db',
                        help='Chemin vers la base de données Plex')
    parser.add_argument('--m3u-dir', 
                        default='/mnt/ssd/Musiques/Playlists',
                        help='Répertoire des fichiers M3U')
    parser.add_argument('--music-library', 
                        default='/mnt/ssd/Musiques',
                        help='Chemin vers la bibliothèque musicale')
    parser.add_argument('--export', 
                        action='store_true',
                        help='Exporter Plex → M3U seulement')
    parser.add_argument('--import', 
                        action='store_true', 
                        dest='import_mode',
                        help='Importer M3U → Plex seulement')
    parser.add_argument('--verbose', '-v', 
                        action='store_true',
                        help='Mode verbeux')
    
    args = parser.parse_args()
    
    # Expansion du chemin home
    m3u_dir = Path(args.m3u_dir).expanduser()
    
    # Initialiser le syncer
    syncer = M3UPlaylistSync(
        plex_db_path=args.plex_db,
        m3u_dir=str(m3u_dir),
        music_library_path=args.music_library,
        verbose=args.verbose
    )
    
    # Synchronisation selon les options
    if args.export:
        syncer.export_all_playlists()
    elif args.import_mode:
        syncer.import_all_m3u()
    else:
        # Synchronisation bidirectionnelle par défaut
        syncer.sync_bidirectional()

if __name__ == "__main__":
    main()