#!/usr/bin/env python3
"""
Script complet pour synchroniser les ratings Plex vers les métadonnées des fichiers audio
Lit les ratings depuis Plex et les écrit directement dans les tags ID3/FLAC/MP4
"""

import json
import sys
import argparse
import sqlite3
import subprocess
import os
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import logging

try:
    from mutagen.id3 import ID3
    from mutagen.id3._frames import POPM
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.flac import FLAC
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
    from mutagen._file import File
except ImportError:
    print("❌ Erreur: Module 'mutagen' requis. Installez avec: pip3 install mutagen")
    sys.exit(1)

class PlexRatingSync:
    def __init__(self, plex_db_path: str, verbose: bool = False):
        self.plex_db_path = Path(plex_db_path)
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

    def get_plex_ratings(self) -> List[Dict]:
        """Récupère tous les ratings depuis la base Plex"""
        if not self.plex_db_path.exists():
            raise FileNotFoundError(f"Base Plex introuvable: {self.plex_db_path}")

        ratings = []
        try:
            conn = sqlite3.connect(str(self.plex_db_path))
            cursor = conn.cursor()

            # Requête pour récupérer les ratings des pistes audio
            query = """
            SELECT
                mi.title as track_title,
                mis.rating as user_rating,
                mis.view_count as play_count,
                mp.file as file_path,
                mi.duration,
                mi.year,
                parent_mi.title as album_title,
                grandparent_mi.title as artist_name
            FROM metadata_items mi
            LEFT JOIN media_items media ON mi.id = media.metadata_item_id
            LEFT JOIN media_parts mp ON media.id = mp.media_item_id
            LEFT JOIN metadata_items parent_mi ON mi.parent_id = parent_mi.id
            LEFT JOIN metadata_items grandparent_mi ON parent_mi.parent_id = grandparent_mi.id
            LEFT JOIN metadata_item_settings mis ON mi.guid = mis.guid
            WHERE mi.metadata_type = 10  -- Type 10 = Track/Audio
            AND mp.file IS NOT NULL
            AND mis.rating IS NOT NULL
            ORDER BY mis.rating DESC, grandparent_mi.title, parent_mi.title, mi.title
            """

            cursor.execute(query)
            rows = cursor.fetchall()

            for row in rows:
                track_title, user_rating, play_count, file_path, duration, year, album_title, artist_name = row

                # Convertir le rating Plex (0-10 ou 0-5) vers étoiles (1-5)
                stars_rating = user_rating
                if stars_rating:
                    # Normaliser sur une échelle de 1-5 étoiles
                    if stars_rating > 5:
                        stars_rating = stars_rating / 2.0  # Conversion 10 -> 5

                    if 1 <= stars_rating <= 5:
                        ratings.append({
                            'file_path': file_path,
                            'rating': stars_rating,
                            'play_count': play_count or 0,
                            'title': track_title or 'Unknown',
                            'album': album_title or 'Unknown Album',
                            'artist': artist_name or 'Unknown Artist',
                            'duration': duration,
                            'year': year,
                            'plex_rating': user_rating  # Garder l'original pour référence
                        })

            conn.close()

        except sqlite3.Error as e:
            self.logger.error(f"❌ Erreur base de données Plex: {e}")
            raise

        self.logger.info(f"📊 {len(ratings)} fichiers avec ratings trouvés dans Plex")
        return ratings

    def rating_to_stars_255(self, rating: float) -> int:
        """Convertit rating 1-5 étoiles vers valeur 0-255 pour POPM"""
        mapping = {
            1.0: 1,
            2.0: 64,
            3.0: 128,
            4.0: 196,
            5.0: 255
        }
        return mapping.get(rating, 128)

    def set_mp3_rating(self, file_path: Path, rating: float, play_count: Optional[int] = None) -> bool:
        """Définit le rating et play count pour fichier MP3"""
        try:
            audio = MP3(file_path, ID3=ID3)

            if audio.tags is None:
                audio.add_tags()

            if audio.tags is not None:
                rating_255 = self.rating_to_stars_255(rating)
                if play_count is not None:
                    audio.tags.add(POPM(email="no@email", rating=rating_255, count=play_count))
                else:
                    audio.tags.add(POPM(email="no@email", rating=rating_255, count=1))

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

            rating_100 = int(rating * 20)  # 1⭐=20, 5⭐=100
            # Utiliser le bon format pour mutagen
            audio["rtng"] = [rating_100]  # Rating iTunes (0-100)

            if play_count is not None:
                audio["plct"] = [str(play_count)]  # Play count iTunes

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

            rating_100 = str(int(rating * 20))
            audio["RATING"] = rating_100
            rating_decimal = str(rating / 5.0)
            audio["FMPS_RATING"] = rating_decimal

            if play_count is not None:
                audio["PLAYCOUNT"] = str(play_count)

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

    def set_ogg_rating(self, file_path: Path, rating: float, play_count: Optional[int] = None) -> bool:
        """Définit le rating et play count pour fichier OGG"""
        try:
            from mutagen.oggvorbis import OggVorbis
            audio = OggVorbis(file_path)

            rating_100 = str(int(rating * 20))
            audio["RATING"] = rating_100
            rating_decimal = str(rating / 5.0)
            audio["FMPS_RATING"] = rating_decimal

            if play_count is not None:
                audio["PLAYCOUNT"] = str(play_count)

            audio.save()

            log_msg = f"✅ OGG rating {rating}⭐"
            if play_count is not None:
                log_msg += f" + {play_count} lectures"
            log_msg += f" écrit: {file_path.name}"
            self.logger.info(log_msg)
            return True

        except Exception as e:
            self.logger.error(f"❌ Erreur OGG {file_path.name}: {e}")
            return False

    def sync_file_rating(self, file_info: Dict) -> bool:
        """Synchronise le rating d'un fichier vers ses métadonnées"""
        file_path = Path(file_info['file_path'])
        rating = float(file_info['rating'])
        play_count = file_info.get('play_count')

        if not file_path.exists():
            self.logger.warning(f"❌ Fichier introuvable: {file_path}")
            self.skipped_files.append(file_info)
            return False

        suffix = file_path.suffix.lower()

        success = False
        if suffix in ['.mp3']:
            success = self.set_mp3_rating(file_path, rating, play_count)
        elif suffix in ['.mp4', '.m4a', '.aac']:
            success = self.set_mp4_rating(file_path, rating, play_count)
        elif suffix in ['.flac']:
            success = self.set_flac_rating(file_path, rating, play_count)
        elif suffix in ['.opus']:
            success = self.set_opus_rating(file_path, rating, play_count)
        elif suffix in ['.ogg']:
            success = self.set_ogg_rating(file_path, rating, play_count)
        else:
            self.logger.warning(f"⚠️ Format non supporté: {suffix} - {file_path.name}")
            self.skipped_files.append(file_info)
            return False

        if success:
            self.processed_files.append(file_info)
        else:
            self.failed_files.append(file_info)

        return success

    def save_ratings_json(self, ratings: List[Dict], output_file: Path):
        """Sauvegarde les ratings dans un fichier JSON"""
        data = {
            'export_date': datetime.now().isoformat(),
            'total_ratings': len(ratings),
            'ratings': ratings
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.logger.info(f"💾 Ratings sauvegardés: {output_file}")

    def show_statistics(self, ratings: List[Dict]):
        """Affiche les statistiques des ratings"""
        if not ratings:
            self.logger.warning("Aucun rating trouvé")
            return

        rating_counts = {}
        for item in ratings:
            rating = item['rating']
            rating_counts[rating] = rating_counts.get(rating, 0) + 1

        print("\n📊 STATISTIQUES DES RATINGS PLEX:")
        print("=" * 50)
        for rating in sorted(rating_counts.keys()):
            stars = "⭐" * int(rating)
            print(f"{stars} ({rating}) : {rating_counts[rating]} fichiers")

        print(f"\nTotal: {len(ratings)} fichiers avec ratings")

    def sync_all_ratings(self, dry_run: bool = False) -> Dict:
        """Synchronise tous les ratings de Plex vers les fichiers"""
        try:
            # Récupérer les ratings depuis Plex
            ratings = self.get_plex_ratings()

            if not ratings:
                return {'success': False, 'error': 'Aucun rating trouvé dans Plex'}

            # Statistiques
            self.show_statistics(ratings)

            if dry_run:
                self.logger.info("🔍 Mode simulation - aucun fichier ne sera modifié")
                return {
                    'success': True,
                    'dry_run': True,
                    'total_ratings': len(ratings)
                }

            # Synchroniser chaque fichier
            self.logger.info(f"🎵 Synchronisation ratings pour {len(ratings)} fichiers...")

            for file_info in ratings:
                self.sync_file_rating(file_info)

            # Résultats
            stats = {
                'success': True,
                'total_ratings': len(ratings),
                'processed': len(self.processed_files),
                'failed': len(self.failed_files),
                'skipped': len(self.skipped_files)
            }

            self.logger.info("✅ Synchronisation terminée:")
            self.logger.info(f"   📊 Total ratings: {stats['total_ratings']}")
            self.logger.info(f"   ✅ Traités: {stats['processed']}")
            self.logger.info(f"   ❌ Échecs: {stats['failed']}")
            self.logger.info(f"   ⚠️ Ignorés: {stats['skipped']}")

            return stats

        except Exception as e:
            self.logger.error(f"❌ Erreur synchronisation: {e}")
            return {'success': False, 'error': str(e)}

def find_plex_database():
    """Trouve automatiquement la base de données Plex"""
    env_db = os.environ.get("PLEX_DB", "").strip()
    possible_paths = [
        Path(env_db).expanduser() if env_db else None,
        Path("/plex/Plug-in Support/Databases/com.plexapp.plugins.library.db"),
        Path("/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"),
        Path.home() / ".config/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db",
        Path("/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"),
        Path.home() / "Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db",
        Path.home() / "AppData/Local/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
    ]

    for path in possible_paths:
        if path and path.exists():
            return str(path)

    return None


def should_manage_plex_service() -> bool:
    """Le start/stop Plex via snap n'est valable que sur l'hôte avec sudo disponible."""
    return not Path("/.dockerenv").exists() and shutil.which("sudo") is not None and shutil.which("snap") is not None


def set_plex_service_running(should_start: bool) -> None:
    if not should_manage_plex_service():
        return

    action = "start" if should_start else "stop"
    subprocess.run(["sudo", "snap", action, "plexmediaserver"], check=True, capture_output=True)

def main():
    parser = argparse.ArgumentParser(
        description='Synchronise les ratings Plex vers les métadonnées des fichiers audio',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
    # Simulation (recommandé d'abord)
    python3 plex_rating_sync_complete.py --plex-db /path/to/plex.db --dry-run

    # Synchronisation réelle
    python3 plex_rating_sync_complete.py --plex-db /path/to/plex.db

    # Avec recherche automatique de la base Plex
    python3 plex_rating_sync_complete.py --auto-find-db

    # Sauvegarder les ratings dans un JSON sans synchroniser
    python3 plex_rating_sync_complete.py --plex-db /path/to/plex.db --export-only ratings.json

    # Statistiques seulement
    python3 plex_rating_sync_complete.py --plex-db /path/to/plex.db --stats
        """
    )

    parser.add_argument(
        '--plex-db', '--plex-database',
        type=str,
        help='Chemin vers la base de données Plex'
    )

    parser.add_argument(
        '--auto-find-db',
        action='store_true',
        help='Recherche automatiquement la base Plex'
    )

    parser.add_argument(
        '--dry-run', '--simulate',
        action='store_true',
        help='Mode simulation (aucun fichier modifié)'
    )

    parser.add_argument(
        '--stats', '--statistics',
        action='store_true',
        help='Affiche les statistiques et quitte'
    )

    parser.add_argument(
        '--export-only',
        type=str,
        help='Exporte les ratings vers JSON sans synchroniser'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Mode verbeux'
    )

    args = parser.parse_args()

    # Déterminer le chemin de la base Plex
    plex_db_path = args.plex_db

    if args.auto_find_db or not plex_db_path:
        auto_path = find_plex_database()
        if auto_path:
            plex_db_path = auto_path
            print(f"🔍 Base Plex trouvée automatiquement: {plex_db_path}")
        elif not plex_db_path:
            print("❌ Base de données Plex introuvable.")
            print("Utilisez --plex-db pour spécifier le chemin manuellement.")
            sys.exit(1)

    # Arrêter Plex pour éviter les conflits de DB
    set_plex_service_running(False)

    # Initialiser le synchroniseur
    try:
        syncer = PlexRatingSync(plex_db_path, verbose=args.verbose)

        # Mode export seulement
        if args.export_only:
            ratings = syncer.get_plex_ratings()
            syncer.save_ratings_json(ratings, Path(args.export_only))
            return

        # Mode statistiques
        if args.stats:
            ratings = syncer.get_plex_ratings()
            syncer.show_statistics(ratings)
            return

        # Synchronisation
        result = syncer.sync_all_ratings(dry_run=args.dry_run)

        if not result['success']:
            print(f"❌ Erreur: {result.get('error', 'Erreur inconnue')}")
            sys.exit(1)

        # Message de succès si mode réel
        if not args.dry_run:
            print("\n✅ Synchronisation terminée avec succès!")
            print("Les ratings Plex ont été écrits dans les métadonnées des fichiers audio.")
            print("Vous pouvez maintenant voir les étoiles dans vos lecteurs audio!")

    except KeyboardInterrupt:
        print("\n⏹️ Opération interrompue par l'utilisateur")
        set_plex_service_running(True)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Erreur inattendue: {e}")
        set_plex_service_running(True)
        sys.exit(1)

    set_plex_service_running(True)

if __name__ == "__main__":
    main()