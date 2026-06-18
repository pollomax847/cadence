# 📀 GESTION DES RATINGS D'ALBUMS PLEX

## 🎯 Objectif

Étendre le système de gestion des ratings pour traiter des **albums entiers** et pas seulement des pistes individuelles. Maintenant, quand vous mettez 1 ou 2 étoiles sur un album dans Plex, tous les fichiers de l'album seront traités en conséquence.

## 🌟 Nouvelles Fonctionnalités

### 📀 Ratings d'Albums
- **1 étoile sur un album** → Tous les fichiers de l'album seront supprimés
- **2 étoiles sur un album** → Tous les fichiers de l'album passeront par songrec-rename
- **3-5 étoiles sur un album** → Synchronisation des ratings vers les métadonnées

### 🎵 Compatibilité avec les Pistes
- Le système continue de fonctionner avec les ratings de pistes individuelles
- Pistes avec ratings individuels + Albums avec ratings = traitement combiné
- Priorité aux ratings spécifiques des pistes si différents de l'album

## 🔧 Nouveaux Outils

### 1. `album_ratings_manager.py`
Script Python pour analyser les ratings d'albums et pistes :
```bash
python3 album_ratings_manager.py /path/to/plex.db /output/dir
```

**Sorties générées :**
- `albums_1_star.json` - Albums avec 1 étoile
- `albums_2_star.json` - Albums avec 2 étoiles  
- `files_1_star.json` - Tous fichiers 1 étoile (albums + pistes)
- `files_2_star.json` - Tous fichiers 2 étoiles (albums + pistes)
- `ratings_stats.json` - Statistiques détaillées

### 2. `manage_album_ratings.sh`
Interface interactive pour gérer les albums :
```bash
./manage_album_ratings.sh
```

**Fonctionnalités :**
- Visualiser albums par rating
- Prévisualiser les fichiers qui seront traités
- Lancer le workflow complet
- Mode test (dry-run)

## 📊 Exemples d'Usage

### Analyser vos albums notés
```bash
# Interface interactive
./manage_album_ratings.sh

# Voir directement les albums 1 étoile
./manage_album_ratings.sh --direct 1

# Résumé complet
./manage_album_ratings.sh --direct all
```

### Workflow mensuel amélioré
Le workflow mensuel (`plex_monthly_workflow.sh`) utilise maintenant automatiquement la gestion d'albums :

```bash
./plex_monthly_workflow.sh
```

**Nouveau processus :**
1. 📊 Analyse albums + pistes avec ratings
2. 🗑️ Suppression : Albums 1⭐ + Pistes 1⭐
3. 🔍 Songrec : Albums 2⭐ + Pistes 2⭐  
4. 🎵 Sync : Albums 3-5⭐ + Pistes 3-5⭐

## 🏗️ Architecture Technique

### Base de Données Plex
```sql
-- Albums (metadata_type = 9)
SELECT album.title, album_settings.rating 
FROM metadata_items album
JOIN metadata_item_settings album_settings ON album.guid = album_settings.guid
WHERE album.metadata_type = 9

-- Pistes d'un album (metadata_type = 10)
SELECT track.title, track.parent_id
FROM metadata_items track  
WHERE track.metadata_type = 10 AND track.parent_id = ?
```

### Logique de Priorité
1. **Rating album existe** → Appliquer à toutes les pistes
2. **Rating piste individuel différent** → Priorité au rating piste
3. **Pas de rating album** → Utiliser uniquement ratings pistes

### Compatibilité
- ✅ Fonctionne avec les scripts existants
- ✅ Fallback automatique vers l'ancienne méthode si erreur
- ✅ Génère les mêmes fichiers JSON pour compatibilité

## 📈 Avantages

### 🎯 Efficacité
- **Traitement d'albums complets** en une seule action
- Plus besoin de noter chaque piste individuellement
- Gestion cohérente d'albums entiers

### 🔍 Visibilité
- Statistiques détaillées : albums vs pistes
- Prévisualisation avant action
- Séparation claire des sources (album/piste)

### 🛡️ Sécurité
- Mode dry-run pour tester
- Fallback automatique
- Compatibilité totale avec l'existant

## 🎮 Guide d'Utilisation

### Scénario 1: Supprimer un album complet
1. Dans Plex/PlexAmp, mettre **1 étoile** sur l'album
2. Lancer `./manage_album_ratings.sh`
3. Choisir "1) Voir albums avec 1 étoile"
4. Confirmer puis lancer le workflow

### Scénario 2: Scanner un album avec songrec
1. Mettre **2 étoiles** sur l'album dans Plex
2. Le workflow mensuel s'occupera automatiquement du scan
3. Ou utiliser l'interface pour prévisualiser

### Scénario 3: Mélange albums/pistes
- Album à 3⭐ mais une piste problématique à 1⭐
- → La piste sera supprimée, le reste de l'album synchronisé
- Priorité aux ratings individuels des pistes

## 🚀 Migration

### Depuis l'ancien système
- ✅ **Aucune migration nécessaire**
- ✅ Les scripts existants continuent de fonctionner  
- ✅ Amélioration transparente

### Recommandations
1. Tester avec `./manage_album_ratings.sh` d'abord
2. Utiliser le mode "Test" pour comprendre l'impact
3. Puis passer au workflow automatique mensuel

## 📝 Logs et Débogage

### Nouveaux logs dans le workflow
```
📀 Albums 1⭐: 2 (45 fichiers)
📀 Albums 2⭐: 1 (12 fichiers)  
🎵 Pistes seules 1⭐: 3
🎵 Pistes seules 2⭐: 1
```

### Fichiers de debug
- `/tmp/plex_ratings_*/ratings_stats.json` - Statistiques complètes
- `/tmp/plex_ratings_*/albums_*.json` - Détails par rating
- Logs workflow dans `~/logs/plex_monthly/`

## ⚠️ Notes Importantes

### Plex Database
- Utilise la même base que l'ancien système
- Pas de modification de la structure Plex
- Lecture seule de la base de données

### Performance  
- Analyse plus complète = légèrement plus lent
- Fallback automatique si problème
- Cache des résultats dans /tmp

### Sauvegarde
- Système de backup inchangé
- Sauvegarde avant suppression toujours active
- Logs détaillés de toutes les opérations