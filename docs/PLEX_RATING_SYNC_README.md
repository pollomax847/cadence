# Synchronisation des Ratings Plex vers les Métadonnées Audio

Ce script permet de synchroniser les étoiles (ratings) que vous mettez dans Plex Media Server vers les métadonnées des fichiers audio eux-mêmes.

## 🎯 Problème résolu

Avant, quand vous mettiez des étoiles dans Plex, elles n'étaient visibles que dans Plex. Maintenant, les ratings sont écrits directement dans les métadonnées des fichiers MP3, FLAC, MP4/M4A, etc.

## 📋 Prérequis

- Python 3
- Module `mutagen` : `pip3 install mutagen`
- Accès à la base de données Plex

## 🚀 Utilisation

### Script principal : `plex_rating_sync_complete.py`

#### Voir les statistiques des ratings
```bash
python3 plex_rating_sync_complete.py --auto-find-db --stats
```

#### Simulation (recommandé d'abord)
```bash
python3 plex_rating_sync_complete.py --auto-find-db --dry-run
```

#### Synchronisation réelle
```bash
python3 plex_rating_sync_complete.py --auto-find-db
```

#### Exporter les ratings vers JSON
```bash
python3 plex_rating_sync_complete.py --auto-find-db --export-only ratings.json
```

### Script de démonstration : `demo_plex_rating_sync.sh`

Lance une démonstration complète avec vérifications et confirmation :

```bash
./demo_plex_rating_sync.sh
```

## 📊 Formats supportés

- **MP3** : Écrit dans les tags ID3 (POPM frame)
- **FLAC** : Écrit dans les tags Vorbis (RATING, FMPS_RATING)
- **MP4/M4A** : Écrit dans les tags iTunes (rating, rtng)

## ⭐ Conversion des ratings

Le script convertit automatiquement les ratings Plex vers l'échelle 1-5 étoiles :
- Plex stocke parfois sur 10 points → converti en 5 étoiles
- Plex stocke parfois déjà sur 5 points → gardé tel quel

## 🔧 Options avancées

- `--plex-db PATH` : Spécifier manuellement le chemin de la base Plex
- `--verbose` : Mode verbeux pour plus de détails
- `--export-only FILE` : Exporter sans synchroniser

## ⚠️ Sécurité

- **Toujours tester d'abord en mode `--dry-run`**
- Le script ne supprime aucun fichier
- Il ne fait que **ajouter** des métadonnées de rating
- Les fichiers originaux restent inchangés (sauf métadonnées)

## 📈 Résultats

Après synchronisation, vous verrez les étoiles dans :
- Explorateur de fichiers Windows (pour MP3)
- Lecteurs audio comme VLC, foobar2000, etc.
- Tags audio universels

## 🔍 Dépannage

Si le script ne trouve pas la base Plex automatiquement :
```bash
find /var -name "com.plexapp.plugins.library.db" 2>/dev/null
```

Puis utiliser :
```bash
python3 plex_rating_sync_complete.py --plex-db /chemin/vers/plex.db
```