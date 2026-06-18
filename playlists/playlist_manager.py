#!/home/paulceline/bin/plex-ratings-sync/.venv/bin/python3
"""
Script pour exporter les playlists Spotify depuis le fichier de cache local
et les synchroniser avec Plex
"""

import json
import sqlite3
import logging
import time
import re
import os
from pathlib import Path
from typing import List, Dict, Optional

PLEX_DB_PATH = "/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
SPOTIFY_CACHE_PATH = Path.home() / ".cache" / "spotify" / "www-cache"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SpotifyPlaylistExtractor:
    def __init__(self):
        self.spotify_cache = SPOTIFY_CACHE_PATH
    
    def find_playlist_json_in_cache(self, playlist_id: str) -> Optional[Dict]:
        """Cherche la playlist dans le cache Spotify"""
        try:
            if not self.spotify_cache.exists():
                logger.warning(f"⚠️ Cache Spotify non trouvé: {self.spotify_cache}")
                return None
            
            # Chercher dans les fichiers du cache
            for file in self.spotify_cache.glob("**/*"):
                if file.is_file() and playlist_id in str(file):
                    try:
                        with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                            # Chercher du JSON
                            if content.startswith('{'):
                                return json.loads(content)
                    except:
                        pass
            
            return None
        except Exception as e:
            logger.error(f"❌ Erreur cache: {e}")
            return None
    
    def extract_tracks_from_api_response(self, data: Dict) -> List[Dict]:
        """Extrait les chansons depuis une réponse API Spotify"""
        tracks = []
        
        try:
            # Format API Spotify standard
            if 'tracks' in data:
                items = data['tracks']
                if isinstance(items, dict) and 'items' in items:
                    items = items['items']
                
                for item in items if isinstance(items, list) else []:
                    if isinstance(item, dict):
                        track = item.get('track', item)
                        if track:
                            title = track.get('name', '')
                            artists = track.get('artists', [])
                            artist_name = ''
                            
                            if isinstance(artists, list) and artists:
                                if isinstance(artists[0], dict):
                                    artist_name = artists[0].get('name', '')
                                else:
                                    artist_name = str(artists[0])
                            
                            if title:
                                tracks.append({
                                    'title': title,
                                    'artist': artist_name,
                                    'album': track.get('album', {}).get('name', '') if isinstance(track.get('album'), dict) else ''
                                })
        except Exception as e:
            logger.error(f"❌ Erreur extraction: {e}")
        
        return tracks

class PlexPlaylistManager:
    def __init__(self, plex_db_path: str):
        self.plex_db_path = Path(plex_db_path)
    
    def _connect_db(self):
        conn = sqlite3.connect(str(self.plex_db_path))
        conn.create_collation('icu_root', lambda a, b: (a > b) - (a < b))
        conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
        return conn
    
    def _clean_string(self, s: str) -> str:
        """Nettoie un string pour le matching"""
        if not s:
            return ""
        s = s.lower().strip()
        s = re.sub(r'\(.*?\)', '', s)
        s = re.sub(r'\[.*?\]', '', s)
        s = re.sub(r'feat\..*', '', s, flags=re.IGNORECASE)
        s = re.sub(r'[^a-z0-9\s]', '', s)
        s = ' '.join(s.split())
        return s
    
    def find_track_in_plex(self, title: str, artist: str = "") -> Optional[int]:
        """Cherche une chanson dans Plex"""
        try:
            with self._connect_db() as conn:
                cursor = conn.cursor()
                
                clean_title = self._clean_string(title)
                if not clean_title:
                    return None
                
                query = """
                SELECT mi.id FROM metadata_items mi
                WHERE mi.metadata_type = 10
                AND LOWER(mi.title) LIKE ?
                LIMIT 1
                """
                
                cursor.execute(query, (f"%{clean_title}%",))
                result = cursor.fetchone()
                
                return result[0] if result else None
        except Exception as e:
            logger.error(f"❌ Erreur recherche: {e}")
            return None
    
    def create_plex_playlist(self, playlist_name: str, tracks: List[Dict]) -> int:
        """Crée une playlist Plex"""
        try:
            with self._connect_db() as conn:
                cursor = conn.cursor()
                
                now = int(time.time())
                
                # Supprimer la playlist si elle existe déjà
                cursor.execute("""
                    SELECT id FROM metadata_items
                    WHERE title = ? AND metadata_type = 15
                """, (playlist_name,))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("DELETE FROM play_queue_generators WHERE playlist_id = ?", (existing[0],))
                    cursor.execute("DELETE FROM metadata_items WHERE id = ?", (existing[0],))
                
                cursor.execute("""
                    INSERT INTO metadata_items (
                        library_section_id, metadata_type, guid, title, 
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    1, 15,
                    f"com.plexapp.agents.localmedia://playlist/{now}",
                    playlist_name,
                    now,
                    now
                ))
                
                playlist_id = cursor.lastrowid
                matched = 0
                seen_track_ids: set = set()
                
                logger.info(f"\n🔍 Searching {len(tracks)} tracks in Plex library...")
                for idx, track in enumerate(tracks):
                    track_id = self.find_track_in_plex(
                        track.get('title', ''),
                        track.get('artist', '')
                    )
                    
                    if track_id and track_id not in seen_track_ids:
                        seen_track_ids.add(track_id)
                        cursor.execute("""
                            INSERT INTO play_queue_generators (
                                playlist_id, metadata_item_id, "order",
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?)
                        """, (playlist_id, track_id, float(matched), now, now))
                        matched += 1
                        if (idx + 1) % 10 == 0:
                            logger.info(f"  {matched}/{idx+1} chansons matchées...")
                
                conn.commit()
                logger.info(f"\n✅ Playlist '{playlist_name}': {matched}/{len(tracks)} chansons ajoutées")
                return matched
        except Exception as e:
            logger.error(f"❌ Erreur création: {e}")
            return 0

def manual_playlist_creation():
    """Interface manuelle pour créer une playlist"""
    print("\n" + "="*60)
    print("🎵 Créateur de Playlist Plex")
    print("="*60)
    
    name = input("\nNom de la playlist: ").strip()
    if not name:
        print("❌ Nom invalide")
        return
    
    print("\nEntre les chansons (Titre | Artiste)")
    print("(Laisse vide pour finir)")
    print("-" * 60)
    
    tracks = []
    idx = 1
    while True:
        try:
            line = input(f"{idx}. ").strip()
            if not line:
                break
            
            parts = [p.strip() for p in line.split('|')]
            if parts[0]:
                tracks.append({
                    'title': parts[0],
                    'artist': parts[1] if len(parts) > 1 else '',
                    'album': parts[2] if len(parts) > 2 else ''
                })
                idx += 1
        except KeyboardInterrupt:
            print("\n❌ Annulé")
            return
    
    if tracks:
        manager = PlexPlaylistManager(PLEX_DB_PATH)
        manager.create_plex_playlist(name, tracks)

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # Mode: sync depuis fichier JSON
        json_file = sys.argv[1]
        
        try:
            with open(json_file, 'r') as f:
                playlists_data = json.load(f)
            
            if not isinstance(playlists_data, list):
                playlists_data = [playlists_data]
            
            manager = PlexPlaylistManager(PLEX_DB_PATH)
            
            for playlist in playlists_data:
                name = playlist.get('name', 'Unknown')
                tracks = playlist.get('tracks', [])
                
                logger.info(f"\n📋 Playlist: {name} ({len(tracks)} chansons)")
                manager.create_plex_playlist(name, tracks)
        except Exception as e:
            logger.error(f"❌ Erreur: {e}")
    else:
        # Mode interactif
        manual_playlist_creation()
