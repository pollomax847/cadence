# 🎵 RÉSUMÉ : GESTION DES RATINGS D'ALBUMS PLEX

## ✅ Système Créé

Vous avez maintenant un système complet pour gérer les ratings au niveau des **albums entiers** en plus des pistes individuelles.

### 🔧 Nouveaux Fichiers Créés

1. **`album_ratings_manager.py`** - Moteur d'analyse des albums et pistes
2. **`manage_album_ratings.sh`** - Interface interactive pour la gestion
3. **`ALBUM_RATINGS_README.md`** - Documentation complète
4. **`plex_monthly_workflow.sh`** - Modifié pour intégrer les albums

### 🎯 Fonctionnement

#### Quand vous mettez des étoiles sur un ALBUM dans Plex :
- **1⭐ sur album** → Tous les fichiers de l'album seront supprimés
- **2⭐ sur album** → Tous les fichiers de l'album passeront par songrec-rename  
- **3-5⭐ sur album** → Synchronisation des ratings vers métadonnées

#### Compatibilité totale avec l'existant :
- Continue de fonctionner avec les ratings de pistes individuelles
- Fallback automatique vers l'ancien système si problème
- Même interface, mêmes commandes

## 🚀 Comment Utiliser

### 1. Interface Interactive
```bash
./manage_album_ratings.sh
```
Menu interactif pour :
- Voir albums 1⭐ (suppression)
- Voir albums 2⭐ (songrec)  
- Résumé complet
- Test et workflow complet

### 2. Analyse Rapide
```bash
# Voir tous les ratings
./manage_album_ratings.sh --direct all

# Voir albums 2 étoiles
./manage_album_ratings.sh --direct 2
```

### 3. Workflow Mensuel (Automatique)
```bash
./plex_monthly_workflow.sh
```
Maintenant analyse automatiquement albums + pistes

## 📊 Exemple de Votre Bibliothèque Actuelle

D'après l'analyse de test :
- **📀 2 albums** avec 2⭐ (3 fichiers) → songrec-rename
- **📀 10 albums** avec 3-5⭐ (pour sync métadonnées)  
- **🎵 26 pistes individuelles** avec 2⭐ → songrec-rename
- **🎵 194 pistes** avec 3-5⭐ → sync métadonnées

**Total fichiers 2⭐ : 29 fichiers** (albums + pistes)

## 🎮 Workflow Recommandé

### Pour supprimer un album complet :
1. Dans Plex/PlexAmp : Mettre **1⭐** sur l'album
2. Lancer `./manage_album_ratings.sh` 
3. Voir "1) Albums avec 1 étoile" pour prévisualiser
4. Lancer le workflow mensuel ou confirmation

### Pour scanner un album avec songrec :
1. Mettre **2⭐** sur l'album dans Plex
2. Le workflow mensuel s'occupe automatiquement du scan
3. Ou prévisualiser avec l'interface

### Avantages vs avant :
- **Avant** : Noter 12 pistes individuellement 
- **Maintenant** : Noter 1 album = 12 pistes traitées automatiquement

## 🛡️ Sécurité

- ✅ Mode test disponible (dry-run)
- ✅ Sauvegarde automatique avant suppression  
- ✅ Fallback vers l'ancien système si erreur
- ✅ Compatible à 100% avec scripts existants
- ✅ Prévisualisation avant action

## 📈 Statistiques Détaillées

Le nouveau système vous donne une visibilité complète :
```
📀 Albums 1⭐: 0 (0 fichiers)
📀 Albums 2⭐: 2 (3 fichiers)  
🎵 Pistes seules 1⭐: 0
🎵 Pistes seules 2⭐: 26
📁 Total fichiers 1⭐: 0
📁 Total fichiers 2⭐: 29
```

## 🎯 Prochaines Étapes

1. **Tester** avec `./manage_album_ratings.sh` 
2. **Prévisualiser** les albums qui seront traités
3. **Configurer** les notifications avec `./plex_notifications.sh configure`
4. **Utiliser** le workflow mensuel normalement
5. **Profiter** de la gestion d'albums entiers !

Le système est **prêt à l'emploi** et totalement compatible avec votre workflow existant. 🎉

---

# 🔔 SYSTÈME DE NOTIFICATIONS AJOUTÉ

## ✨ Nouvelles Fonctionnalités de Notification

### 📱 Notifications Desktop
- Notifications en temps réel pendant les traitements
- Icônes spécifiques pour chaque type d'action
- Urgence adaptée (normal/critique)

### 📧 Notifications Email (Optionnel)
- Résumés détaillés après chaque workflow
- Statistiques complètes avec durée et nombres de fichiers
- Alertes d'erreur critiques

### 🎯 Types de Notifications
- **🚀 Démarrage** : Workflow commence avec X fichiers à traiter
- **🗑️ Suppression** : X fichiers 1⭐ supprimés 
- **🔍 Songrec** : X fichiers traités, Y erreurs
- **🎵 Sync Ratings** : X ratings synchronisés
- **✅ Résumé Final** : Statistiques complètes
- **❌ Erreurs** : Problèmes critiques nécessitant attention

### 🔧 Configuration Rapide
```bash
# Configuration interactive
./plex_notifications.sh configure

# Test
./plex_notifications.sh test
```

### 📊 Exemple de Notification
```
🎵 Workflow Plex terminé
220 fichiers traités en 00:03:42

📊 RÉSUMÉ:
🗑️ Supprimés: 0 fichiers
🔍 Songrec: 26 traités, 3 erreurs  
🎵 Ratings: 194 synchronisés
```

Le système vous informe maintenant **automatiquement** de tout ce qui se passe ! 🔔

---

# Guide de résolution des problèmes d'encodage UTF-8

## Problème rencontré

Erreur avec songrec : `ERROR: Directory /mnt/mybook/itunes/Music/Black Atlass(ë¸"ëž™ ì•„í‹€ë`

Le répertoire contient des caractères coréens mal encodés qui causent des problèmes avec songrec-rename.

## Solution rapide

### 1. Analyser les problèmes

```bash
# Scanner tous les problèmes d'encodage
./find_encoding_problems.sh scan /mnt/mybook/itunes/Music

# Tester la compatibilité songrec sur un répertoire spécifique
./find_encoding_problems.sh test "/mnt/mybook/itunes/Music/Black Atlass(ë¸"ëž™ ì•„í‹€ë"
```

### 2. Corriger automatiquement

```bash
# Mode simulation (voir ce qui sera fait)
./fix_encoding_issues.sh /mnt/mybook/itunes/Music dry-run

# Correction réelle
./fix_encoding_issues.sh /mnt/mybook/itunes/Music fix
```

### 3. Correction manuelle du répertoire Black Atlass

```bash
# Renommer le répertoire problématique
cd /mnt/mybook/itunes/Music
mv "Black Atlass(ë¸\"ëž™ ì•„í‹€ë" "Black Atlass"
```

## Prévention

### Intégration dans le workflow mensuel

Le script `plex_monthly_workflow.sh` doit vérifier l'encodage avant d'utiliser songrec :

```bash
# Avant l'étape songrec, ajouter:
if ! ./find_encoding_problems.sh test "$MUSIC_ROOT"; then
    log "🔧 Correction automatique des problèmes d'encodage..."
    ./fix_encoding_issues.sh "$MUSIC_ROOT" fix
fi
```

### Configuration des outils

1. **Plex** : S'assurer que l'importation utilise UTF-8
2. **Beets** : Configuration pour l'encodage des noms de fichiers
3. **Freyr** : Options de normalisation des noms

## Scripts disponibles

| Script | Description | Usage |
|--------|-------------|--------|
| `find_encoding_problems.sh` | Détecte les problèmes | `./find_encoding_problems.sh scan` |
| `fix_encoding_issues.sh` | Corrige automatiquement | `./fix_encoding_issues.sh /path fix` |
| `fix_audio_metadata.py` | Corrige les métadonnées | `python3 fix_audio_metadata.py` |

## Caractères problématiques courants

- **Coréens** : `ë¸"`, `ëž™`, `ì•„`, `í‹€`, `ë`
- **Accents** : `éèêëàáâäôöùúûü`
- **Caractères de contrôle** : invisibles, retours de ligne
- **Encodage mixte** : UTF-8 mal interprété

## Commandes de diagnostic

```bash
# Voir l'encodage actuel du terminal
locale

# Lister les fichiers avec caractères spéciaux
find /mnt/mybook/itunes/Music -name "*[^[:print:]]*" | head -10

# Vérifier un nom de fichier spécifique
file -bi "nom_du_fichier"
```

## Test après correction

```bash
# Tester songrec sur le répertoire corrigé
songrec-rename "/mnt/mybook/itunes/Music/Black Atlass/fichier.mp3"

# Vérifier le workflow complet
./plex_monthly_workflow.sh --dry-run
```

## En cas d'échec

1. **Sauvegarde** : Toujours sauvegarder avant modification massive
2. **Logs** : Vérifier `~/encoding_problems_report.txt`
3. **Manuel** : Renommer manuellement les répertoires problématiques
4. **Support** : Vérifier les issues de songrec-rename sur GitHub

## Intégration dans le workflow automatisé

Ajouter dans `plex_monthly_workflow.sh` avant l'étape songrec :

```bash
# ÉTAPE 2.5: VÉRIFICATION ET CORRECTION ENCODAGE
log "${YELLOW}🔍 Vérification des problèmes d'encodage${NC}"
if ! $SCRIPT_DIR/find_encoding_problems.sh test "$MUSIC_ROOT" >/dev/null 2>&1; then
    log "${YELLOW}🔧 Correction automatique des problèmes d'encodage...${NC}"
    $SCRIPT_DIR/fix_encoding_issues.sh "$MUSIC_ROOT" fix
fi
```
