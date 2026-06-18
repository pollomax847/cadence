#!/usr/bin/env python3
"""
Script pour synchroniser les ratings Plex vers les métadonnées ID3 des fichiers audio
Écrit les évaluations directement dans les tags pour affichage universel
"""

import json
import sys
import argparse
from pathlib import Path
from typing import List, Dict, Optional
import logging

try:
    from mutagen.id3 import ID3
    from mutagen.id3._frames import POPM
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.flac import FLAC
    from mutagen._file import File
except ImportError:
    print("❌ Erreur: Module 'mutagen' requis. Installez avec: pip3 install mutagen")
    sys.exit(1)

class RatingSync:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.setup_logging()
        self.processed_files = []
        self.failed_files = []
        self.skipped_files = []
        
    def setup_logging(self):
        log_format = '%(asctime)s - %(levelname)s - %(message)s'
        level = logging.DEBUG if self.verbose else logging.INFO
        logging.basicConfig(
            level=level,
            format=log_format,
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        self.logger = logging.getLogger(__name__)

    def rating_to_stars_255(self, rating: float) -> int:
        """Convertit rating 1-5 étoiles vers valeur 0-255 pour POPM"""
        # Mapping standard: 1⭐=1, 2⭐=64, 3⭐=128, 4⭐=196, 5⭐=255
        mapping = {
            1.0: 1,
            2.0: 64, 
            3.0: 128,
            4.0: 196,
            5.0: 255
        }
        return mapping.get(rating, 128)  # Default 3 étoiles si inconnu

    def set_mp3_rating(self, file_path: Path, rating: float, play_count: Optional[int] = None) -> bool:
        """Définit le rating et play count pour fichier MP3"""
        try:
            audio = MP3(file_path, ID3=ID3)
            
            # Ajouter tags ID3 si absent
            if audio.tags is None:
                audio.add_tags()
            
            if audio.tags is not None:
                # POPM frame pour rating (Windows Media Player, etc.)
                rating_255 = self.rating_to_stars_255(rating)
                if play_count is not None:
                    audio.tags.add(POPM(email="no@email", rating=rating_255, count=play_count))
                else:
                    audio.tags.add(POPM(email="no@email", rating=rating_255, count=1))
            
                # Sauvegarder
                audio.save()
            
                log_msg = f"✅ MP3 rating {rating}⭐"
                if play_count is not None:
                    log_msg += f" + {play_count} lectures"
                log_msg += f" écrit: {file_path.name}"
                self.logger.info(log_msg)
                return True
            else:
                self.logger.error(f"❌ Impossible de créer tags ID3: {file_path.name}")
                return False
            
        except Exception as e:
            self.logger.error(f"❌ Erreur MP3 {file_path.name}: {e}")
            return False

    def set_mp4_rating(self, file_path: Path, rating: float, play_count: Optional[int] = None) -> bool:
        """Définit le rating et play count pour fichier MP4/M4A"""
        try:
            audio = MP4(file_path)
            
            # Tag ---- pour rating iTunes (0-100)
            rating_100 = int(rating * 20)  # 1⭐=20, 5⭐=100
            audio["----:com.apple.iTunes:rating"] = str(rating_100).encode('utf-8')
            
            # Tag rtng alternatif
            audio["rtng"] = [rating_100]
            
            # Play count si fourni
            if play_count is not None:
                audio["plct"] = [play_count]  # Play count iTunes
            
            audio.save()
            
            log_msg = f"✅ MP4 rating {rating}⭐"
            if play_count is not None:
                log_msg += f" + {play_count} lectures"
            log_msg += f" écrit: {file_path.name}"
            self.logger.info(log_msg)
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Erreur MP4 {file_path.name}: {e}")
            return False

    def set_flac_rating(self, file_path: Path, rating: float, play_count: Optional[int] = None) -> bool:
        """Définit le rating et play count pour fichier FLAC"""
        try:
            audio = FLAC(file_path)
            
            # Tag RATING standard (0-100)
            rating_100 = str(int(rating * 20))
            audio["RATING"] = rating_100
            
            # Tag FMPS_RATING alternatif (0.0-1.0)
            rating_decimal = str(rating / 5.0)
            audio["FMPS_RATING"] = rating_decimal
            
            # Play count si fourni
            if play_count is not None:
                audio["PLAYCOUNT"] = str(play_count)  # Tag standard FLAC
            
            audio.save()
            
            log_msg = f"✅ FLAC rating {rating}⭐"
            if play_count is not None:
                log_msg += f" + {play_count} lectures"
            log_msg += f" écrit: {file_path.name}"
            self.logger.info(log_msg)
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Erreur FLAC {file_path.name}: {e}")
            return False

    def sync_file_rating(self, file_info: Dict) -> bool:
        """Synchronise le rating et play count d'un fichier vers ses métadonnées"""
        file_path = Path(file_info['file_path'])
        rating = float(file_info['rating'])
        play_count = file_info.get('play_count')  # Optionnel
        
        if not file_path.exists():
            self.logger.warning(f"❌ Fichier introuvable: {file_path}")
            self.skipped_files.append(file_info)
            return False
        
        # Déterminer le type de fichier
        suffix = file_path.suffix.lower()
        
        success = False
        if suffix in ['.mp3']:
            success = self.set_mp3_rating(file_path, rating, play_count)
        elif suffix in ['.mp4', '.m4a', '.aac']:
            success = self.set_mp4_rating(file_path, rating, play_count)
        elif suffix in ['.flac']:
            success = self.set_flac_rating(file_path, rating, play_count)
        else:
            self.logger.warning(f"⚠️ Format non supporté: {suffix} - {file_path.name}")
            self.skipped_files.append(file_info)
            return False
        
        if success:
            self.processed_files.append(file_info)
        else:
            self.failed_files.append(file_info)
            
        return success

    def sync_ratings_from_json(self, json_file: Path) -> Dict:
        """Synchronise tous les ratings depuis un fichier JSON"""
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                files_data = json.load(f)
                
            self.logger.info(f"🎵 Synchronisation ratings pour {len(files_data)} fichiers...")
            
            for file_info in files_data:
                self.sync_file_rating(file_info)
            
            # Statistiques
            stats = {
                'total_files': len(files_data),
                'processed': len(self.processed_files),
                'failed': len(self.failed_files),
                'skipped': len(self.skipped_files)
            }
            
            self.logger.info(f"✅ Synchronisation terminée:")
            self.logger.info(f"   📊 Total: {stats['total_files']}")
            self.logger.info(f"   ✅ Traités: {stats['processed']}")
            self.logger.info(f"   ❌ Échecs: {stats['failed']}")
            self.logger.info(f"   ⚠️ Ignorés: {stats['skipped']}")
            
            return stats
            
        except Exception as e:
            self.logger.error(f"❌ Erreur lecture JSON {json_file}: {e}")
            return {'total_files': 0, 'processed': 0, 'failed': 0, 'skipped': 0}

def main():
    parser = argparse.ArgumentParser(
        description='Synchronise les ratings Plex vers les métadonnées ID3 des fichiers audio'
    )
    parser.add_argument('json_file', 
                        help='Fichier JSON contenant les informations de rating')
    parser.add_argument('--verbose', '-v', 
                        action='store_true',
                        help='Mode verbeux')
    
    args = parser.parse_args()
    
    json_file = Path(args.json_file)
    if not json_file.exists():
        print(f"❌ Fichier JSON introuvable: {json_file}")
        sys.exit(1)
    
    # Synchronisation
    sync = RatingSync(verbose=args.verbose)
    stats = sync.sync_ratings_from_json(json_file)
    
    # Code de sortie selon résultats
    if stats['failed'] > 0:
        sys.exit(2)  # Échecs partiels
    elif stats['processed'] == 0:
        sys.exit(1)  # Aucun traitement
    else:
        sys.exit(0)  # Succès

if __name__ == "__main__":
    main()