# 🎵 Gestionnaire de Bibliothèque iTunes pour Linux

## 📋 Description

Suite d'outils Python pour gérer et modifier les bibliothèques iTunes XML sur Linux Mint. Ces scripts permettent d'analyser, corriger et mettre à jour votre bibliothèque iTunes sans avoir besoin d'iTunes installé.

## 📊 Votre Bibliothèque

- **92,065 pistes** dans votre collection
- **60 playlists** organisées
- **24,777 artistes** uniques
- **24,299 albums** différents
- **794.57 GB** de musique
- **17,629 heures** d'écoute

## 🛠️ Outils Disponibles

### 1. `itunes_complete_manager.py` - **Interface principale**
```bash
python3 itunes_complete_manager.py
```
**Interface complète avec menu interactif** pour toutes les opérations.

### 2. `itunes_analyzer.py` - **Analyseur de bibliothèque**
```bash
python3 itunes_analyzer.py --stats          # Statistiques de base
python3 itunes_analyzer.py --all            # Analyse complète
python3 itunes_analyzer.py --paths          # Analyse des chemins
python3 itunes_analyzer.py --formats        # Formats de fichiers
python3 itunes_analyzer.py --genres         # Genres musicaux
```

### 3. `itunes_path_updater.py` - **Correcteur de chemins**
```bash
python3 itunes_path_updater.py --analyze    # Analyser les chemins
python3 itunes_path_updater.py --interactive # Mode interactif
python3 itunes_path_updater.py --auto-fix   # Correction automatique
```

### 4. `itunes_library_manager.py` - **Gestionnaire original**
Interface menu pour les opérations de base.

## 🚀 Workflows Recommandés

### Workflow 1 : Première utilisation
```bash
# 1. Copier votre bibliothèque
cp "/mnt/MyBook/Musiques/iTunes/iTunes Music Library.xml" .

# 2. Analyser la bibliothèque
python3 itunes_analyzer.py --stats

# 3. Corriger les chemins
python3 itunes_path_updater.py --auto-fix

# 4. Vérifier les résultats
python3 itunes_analyzer.py --stats
```

### Workflow 2 : Mise à jour régulière
```bash
# Interface complète avec workflow automatisé
python3 itunes_complete_manager.py
# Choisir option 11 : "Mise à jour rapide depuis la source"
```

### Workflow 3 : Analyse détaillée
```bash
python3 itunes_analyzer.py --all
```

## 📁 Structure des Fichiers

```
/home/paulceline/Documents/Projets/Modif xml itunes/
├── iTunes Music Library.xml                # Votre bibliothèque iTunes
├── itunes_complete_manager.py             # Interface principale
├── itunes_analyzer.py                     # Analyseur robuste
├── itunes_path_updater.py                 # Correcteur de chemins
├── itunes_library_manager.py              # Gestionnaire original
├── itunes_alternatives_installer.py       # Installateur d'alternatives
├── itunes_match_explorer.py               # Explorateur iTunes Match
├── itunes_match_report.md                 # Rapport iTunes Match
├── *.backup_*                             # Sauvegardes automatiques
└── README.md                              # Cette documentation
```

## 🔧 Fonctionnalités Principales

### ✅ Analyse de Bibliothèque
- Statistiques complètes (pistes, artistes, albums, genres)
- Analyse des formats de fichiers
- Distribution par années
- Détection des fichiers manquants
- Analyse des emplacements de fichiers

### ✅ Correction de Chemins
- Remplacement automatique `E:/ → /mnt/MyBook/`
- Correction interactive personnalisée
- Mode simulation (dry-run)
- Sauvegardes automatiques avant modification

### ✅ Gestion des Fichiers
- Copie depuis/vers `/mnt/MyBook/Musiques/`
- Sauvegarde automatique avant modifications
- Vérification de l'intégrité des fichiers

### ✅ Workflows Automatisés
- Analyse → Correction → Sauvegarde en un clic
- Mise à jour rapide depuis la source
- Mode interactif pour les cas complexes

## 🔍 Exemples de Corrections Automatiques

Le système détecte et corrige automatiquement :

```
file://E:/Musiques/          → file:///mnt/MyBook/Musiques/
file://localhost/E:/         → file:///mnt/MyBook/
E:/Musiques/                 → /mnt/MyBook/Musiques/
C:/Musiques/                 → /mnt/MyBook/Musiques/
```

## 💾 Sauvegardes Automatiques

Tous les outils créent automatiquement des sauvegardes :
```
iTunes Music Library.xml.backup_20251107_154122
iTunes Music Library.xml.backup_20251107_153749
```

## 🎯 Cas d'Usage Typiques

### 1. **Migration Windows → Linux**
```bash
# Votre bibliothèque a des chemins Windows (E:/, C:/)
python3 itunes_path_updater.py --auto-fix
```

### 2. **Changement d'emplacement des fichiers**
```bash
# Vos fichiers ont déménagé
python3 itunes_path_updater.py --update "ancien/chemin" "nouveau/chemin"
```

### 3. **Nettoyage de la bibliothèque**
```bash
# Analyser et nettoyer
python3 itunes_analyzer.py --all
python3 itunes_complete_manager.py  # Option 10
```

### 4. **Synchronisation régulière**
```bash
# Mettre à jour depuis la source
python3 itunes_complete_manager.py  # Option 11
```

## ⚡ Utilisation Rapide

### Pour commencer immédiatement :
```bash
python3 itunes_complete_manager.py
```
Puis choisir l'option **10** (Workflow complet) ou **11** (Mise à jour rapide).

### Pour une analyse rapide :
```bash
python3 itunes_analyzer.py --stats
```

### Pour corriger les chemins :
```bash
python3 itunes_path_updater.py --interactive
```

## 📈 Statistiques de Votre Bibliothèque

Après analyse, voici ce que nous avons trouvé :
- 🎵 **92,065 pistes** au total
- 🎤 **24,777 artistes** différents  
- 💿 **24,299 albums** uniques
- 📁 **60 playlists** organisées
- 💾 **794.57 GB** de musique
- ⏰ **17,629 heures** d'écoute (soit plus de 2 ans !)

## 🔄 Alternatives iTunes Match

Pour l'accès à votre musique sur Linux, consultez :
- `itunes_match_report.md` - Rapport complet des options
- `itunes_alternatives_installer.py` - Installation de clients alternatifs

**Solutions recommandées :**
1. **Cider** - Client Apple Music natif pour Linux
2. **Apple Music Web** - Interface web officielle
3. **VM Windows + iTunes** - Pour un accès complet

## 🆘 Dépannage

### Problème : "Fichier non trouvé"
```bash
# Copier depuis la source
cp "/mnt/MyBook/Musiques/iTunes/iTunes Music Library.xml" .
```

### Problème : "Erreur de parsing XML"
```bash
# Utiliser l'analyseur robuste
python3 itunes_analyzer.py --stats
```

### Problème : "Pas de modifications détectées"
```bash
# Analyser les chemins d'abord
python3 itunes_path_updater.py --analyze
```

## 📞 Support

Les outils incluent :
- Messages d'erreur détaillés
- Mode `--help` pour chaque script
- Sauvegardes automatiques
- Mode simulation (`--dry-run`) pour tester

## 🎉 Félicitations !

Vous disposez maintenant d'une suite complète d'outils pour gérer votre énorme bibliothèque iTunes (92K+ pistes !) sur Linux Mint, sans avoir besoin d'iTunes ou de Windows !

---

*Créé pour la gestion de bibliothèques iTunes sur Linux Mint - Novembre 2025*