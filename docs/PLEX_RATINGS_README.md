# Configuration et utilisation du synchronisateur Plex Ratings

## 📌 IMPORTANT - Changement de fréquence de synchronisation

**À partir de maintenant, la synchronisation des étoiles se fait QUOTIDIENNEMENT (tous les soirs à 22h00)**

| Avant | Après |
|-------|-------|
| 1 fois par **mois** | **Tous les soirs** à 22h00 |
| `crontab_monthly_workflow.conf` | `crontab_daily_ratings_sync.conf` |
| Script: `plex_monthly_workflow.sh` | Script: `plex_daily_ratings_sync.sh` |

**Avantages de la synchronisation quotidienne:**
- ✅ Vos ratings sont toujours à jour
- ✅ Plus rapide (pas de traitement lourd comme SongRec)
- ✅ Meilleure traçabilité des changements
- ✅ Logs plus détaillés

**Pour installer:** Voir la section [Installation rapide quotidienne](#installation-rapide-quotidienne)

---

## 🎯 Objectif
Ce système permet de synchroniser automatiquement les évaluations (étoiles) de PlexAmp avec votre bibliothèque audio physique. Les fichiers avec 1 étoile (ou un autre rating de votre choix) sont automatiquement supprimés de votre système.

## 📋 Fonctionnalités

### ⭐ Système d'évaluations supporté
- **1 étoile** : Suppression automatique (comportement par défaut)
- **2-5 étoiles** : Conservation (ou suppression configurable)
- Support des ratings utilisateur Plex et ratings automatiques
- Normalisation automatique des échelles de notation (5 ou 10 points)

### 🛡️ Sécurité
- **Mode simulation par défaut** : Aucune suppression sans confirmation explicite
- **Sauvegarde automatique** optionnelle avant suppression
- **Logs détaillés** de toutes les opérations
- **Vérification d'existence** des fichiers avant traitement
- **Rapports JSON** des suppressions effectuées

### 🎵 Types de fichiers supportés
- MP3, FLAC, M4A, OGG, WMA, AAC, WAV
- Extraction des métadonnées depuis la base Plex
- Correspondance avec les chemins système

## 🚀 Installation et utilisation

### 1. Installation rapide
```bash
# Le script est déjà dans votre répertoire audio
cd /home/paulceline/bin/audio

# Rendre exécutable (déjà fait)
chmod +x plex_ratings_helper.sh

# Tester la détection de Plex
./plex_ratings_helper.sh find
```

### 2. Utilisation simple (recommandée)
```bash
# Assistant interactif
./plex_ratings_helper.sh

# Voir les statistiques des ratings
./plex_ratings_helper.sh stats

# Simulation de suppression (1 étoile)
./plex_ratings_helper.sh simulate

# Suppression réelle avec sauvegarde
./plex_ratings_helper.sh delete
```

### 3. Utilisation avancée
```bash
# Simulation pour 2 étoiles
python3 plex_ratings_sync.py --plex-db /path/to/plex.db --rating 2

# Suppression réelle avec sauvegarde
python3 plex_ratings_sync.py --plex-db /path/to/plex.db --delete --backup ~/backup

# Mode verbeux
python3 plex_ratings_sync.py --plex-db /path/to/plex.db --delete --verbose

# Recherche automatique de la base
python3 plex_ratings_sync.py --auto-find-db --stats
```

## 🔧 Configuration

### Localisation de la base Plex
Le script recherche automatiquement dans :
- Linux : `~/.config/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db`
- Linux (service) : `/var/lib/plexmediaserver/Library/Application Support/...`
- macOS : `~/Library/Application Support/Plex Media Server/...`
- Windows : `~/AppData/Local/Plex Media Server/...`

### Personnalisation
```python
# Exemple de configuration personnalisée
config = {
    'target_rating': 2,  # Supprimer les 2 étoiles au lieu de 1
    'backup_dir': '/backup/plex',  # Répertoire de sauvegarde fixe
    'verify_file_exists': True,  # Vérifier l'existence avant suppression
    'log_level': 'DEBUG'  # Logs détaillés
}
```

## 📊 Workflow recommandé

### 1. Première utilisation
```bash
# 1. Voir les statistiques actuelles
./plex_ratings_helper.sh stats

# 2. Faire une simulation
./plex_ratings_helper.sh simulate

# 3. Si satisfait, faire la suppression avec sauvegarde
./plex_ratings_helper.sh delete
```

### 2. Utilisation régulière
```bash
# Automatiser avec un cron job (exemple : chaque dimanche à 3h)
# 0 3 * * 0 /home/paulceline/bin/audio/plex_ratings_sync.py --auto-find-db --delete --backup /backup/plex
```

## 🔍 Recherche et requêtes Plex

### Tables utilisées
- `metadata_items` : Métadonnées des pistes (titre, rating, etc.)
- `media_items` : Éléments média
- `media_parts` : Chemins des fichiers physiques

### Requête SQL utilisée
```sql
SELECT 
    mi.title as track_title,
    mi.rating, mi.user_rating,
    mp.file as file_path,
    parent_mi.title as album_title,
    grandparent_mi.title as artist_name
FROM metadata_items mi
LEFT JOIN media_items media ON mi.id = media.metadata_item_id
LEFT JOIN media_parts mp ON media.id = mp.media_item_id
WHERE mi.metadata_type = 10  -- Audio tracks
AND mp.file IS NOT NULL
AND (mi.rating IS NOT NULL OR mi.user_rating IS NOT NULL)
```

## ⚠️ Précautions importantes

### Sauvegarde obligatoire
```bash
# TOUJOURS faire une sauvegarde avant la première utilisation
rsync -av /mnt/MyBook/Musiques/ /backup/musiques_$(date +%Y%m%d)/
```

### Test sur un sous-ensemble
```bash
# Tester d'abord sur un petit répertoire
cp -r "/mnt/MyBook/Musiques/Artist Test" /tmp/test_music/
# Configurer Plex pour scanner ce répertoire test
# Lancer le script sur ce test
```

### Vérifications post-suppression
```bash
# Vérifier que Plex met à jour sa bibliothèque
# Scanner la bibliothèque dans Plex après les suppressions
# Vérifier les logs du script
tail -f plex_ratings_sync_*.log
```

## 🐛 Dépannage

### Erreurs courantes
1. **Base Plex introuvable**
   ```bash
   # Chercher manuellement
   find /home -name "com.plexapp.plugins.library.db" 2>/dev/null
   ```

2. **Permissions insuffisantes**
   ```bash
   # Vérifier les permissions
   ls -la "$plex_db_path"
   # Utiliser sudo si nécessaire (service Plex)
   ```

3. **Fichiers déjà supprimés**
   - Normal si la bibliothèque Plex n'est pas à jour
   - Faire un scan de bibliothèque dans Plex
   - Utiliser `--verify-file-exists` pour ignorer les fichiers manquants

### Logs et monitoring
```bash
# Logs en temps réel
tail -f plex_ratings_sync_*.log

# Rapport de suppression
cat plex_deletions_*.json | jq '.deleted_files[] | {artist, title, rating}'
```

## 🔄 Intégration avec vos scripts existants

### Compatibilité
Le script s'intègre parfaitement avec vos outils existants :
- `nettoyer_musique_*.py` : Pour nettoyer après suppressions
- `organize-*.sh` : Pour réorganiser les fichiers restants
- `beets_monthly_maintenance.sh` : Pour maintenir la bibliothèque

### Workflow complet
```bash
#!/bin/bash
# Script de maintenance complète

# 1. Synchroniser les ratings Plex (supprimer 1 étoile)
./plex_ratings_sync.py --auto-find-db --delete --backup /backup/plex

# 2. Nettoyer les fichiers corrompus
python3 nettoyer_musique_simple.py

# 3. Réorganiser pour Lidarr
./organize-for-lidarr.sh

# 4. Maintenance Beets
./beets_monthly_maintenance.sh
```

## 📈 Métriques et rapports

### Statistiques générées
- Nombre total de fichiers avec ratings
- Répartition par nombre d'étoiles
- Fichiers traités vs supprimés vs ignorés
- Erreurs rencontrées

### Rapports JSON
```json
{
  "deletion_date": "2025-11-11T15:30:00",
  "total_deleted": 15,
  "deleted_files": [
    {
      "file_path": "/mnt/MyBook/Musiques/Artist/Album/Track.mp3",
      "artist": "Artist Name",
      "title": "Track Name",
      "album": "Album Name",
      "rating": 1.0,
      "deleted_at": "2025-11-11T15:30:15"
    }
  ]
}
```

Ce système vous permet de maintenir automatiquement une bibliothèque audio de haute qualité en supprimant les pistes que vous avez évaluées négativement dans PlexAmp ! 🎵✨