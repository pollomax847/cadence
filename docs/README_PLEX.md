# Plex Ratings Sync

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

Script Python pour synchroniser les évaluations Plex avec le système de fichiers audio.

## 🚀 Installation Rapide

### Option 1: Installation automatique (recommandée)

```bash
# Téléchargez le repo
git clone https://github.com/pollomax847/plex-ratings-sync.git
cd plex-ratings-sync

# Lancez l'installation automatique
./install.sh
```

### Option 2: Installation manuelle

```bash
# 1. Prérequis système
sudo apt update && sudo apt install python3 python3-pip  # Ubuntu/Debian
# ou
brew install python3  # macOS

# 2. Installation des dépendances
pip3 install songrec
pip3 install -r requirements.txt

# 3. Rendre les scripts exécutables
chmod +x plex_notifications.sh
```

## ✅ Vérification de l'installation

```bash
# Test rapide
python3 plex_ratings_sync.py --help

# Vérification des dépendances
songrec --version
python3 -c "import sqlite3, pathlib, subprocess; print('✅ Toutes les dépendances OK')"
```

## ⚙️ Configuration

### Base de données Plex

Le script trouve automatiquement la base Plex, mais vous pouvez la spécifier manuellement :

```bash
# Recherche automatique (recommandé)
python3 plex_ratings_sync.py --auto-find-db

# Chemin manuel
python3 plex_ratings_sync.py --plex-db /chemin/vers/com.plexapp.plugins.library.db
```

### Chemins Plex courants

- **Linux (Snap)** : `/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db`
- **Linux (Apt)** : `/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db`
- **macOS** : `~/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db`
- **Windows** : `%LOCALAPPDATA%\Plex Media Server\Plug-in Support\Databases\com.plexapp.plugins.library.db`

## Fonctionnalités

- **Suppression automatique** : Supprime les fichiers notés 1⭐ dans Plex
- **Identification audio** : Utilise songrec pour identifier les fichiers 2⭐
- **Support albums/artistes** : Peut supprimer des albums ou artistes entiers selon leur rating
- **Sauvegarde** : Option de sauvegarde avant suppression
- **Nettoyage automatique** : Supprime les anciens logs
- **Notifications** : Envoie des notifications desktop/email
- **Mode simulation** : Teste sans supprimer réellement

## 🎯 Premiers Pas

### 1. Test de fonctionnement

```bash
# Mode simulation (recommandé pour commencer)
python3 plex_ratings_sync.py --auto-find-db
```

### 2. Voir les statistiques

```bash
# Afficher la répartition des ratings dans Plex
python3 plex_ratings_sync.py --auto-find-db --stats
```

### 3. Suppression réelle (avec précaution)

```bash
# Supprimer les fichiers 1⭐ avec sauvegarde
python3 plex_ratings_sync.py --auto-find-db --delete --backup ./sauvegarde_securisee
```

## Utilisation Avancée

### Mode simulation (recommandé d'abord)

```bash
python3 plex_ratings_sync.py --auto-find-db
```

### Suppression réelle

```bash
# Supprimer les fichiers 1⭐
python3 plex_ratings_sync.py --auto-find-db --delete

# Avec sauvegarde
python3 plex_ratings_sync.py --auto-find-db --delete --backup ./backup

# Supprimer aussi les albums 1⭐
python3 plex_ratings_sync.py --auto-find-db --delete --delete-albums

# Supprimer aussi les artistes 1⭐
python3 plex_ratings_sync.py --auto-find-db --delete --delete-artists
```

### Autres options

```bash
# Voir les statistiques des ratings
python3 plex_ratings_sync.py --auto-find-db --stats

# Nettoyer les logs de plus de 30 jours
python3 plex_ratings_sync.py --auto-find-db --cleanup-logs 30

# Mode verbeux
python3 plex_ratings_sync.py --auto-find-db --verbose
```

## Logique de traitement

- **1⭐** : Suppression du fichier
- **2⭐** : Identification avec songrec (conservation)
- **3-5⭐** : Conservation

## Sécurité

- **Toujours tester en simulation d'abord** (`--delete` pour la suppression réelle)
- **Utilisez `--backup`** pour sauvegarder avant suppression
- **Vérifiez les logs** après chaque exécution

## Notifications

Le script envoie automatiquement des notifications pour :

- Fichiers identifiés avec songrec
- Fichiers supprimés

### Types de notifications

- **Console colorée** : Messages colorés dans le terminal (✅ recommandé)
- **Desktop** : Popups graphiques (✅ activé - nécessite environnement graphique)
- **Email** : Notifications par email (optionnel)
- **Sonore** : Sons de notification (fallback disponible)

### Configuration simple

```bash
# Configuration rapide (console + email)
./plex_notifications.sh configure

# Test des notifications
./plex_notifications.sh test

# Diagnostic complet
./plex_notifications.sh diagnose
```

### Exemple de notifications console

```bash
🔔 🗑️ Fichiers supprimés: 5 fichier(s) avec 1 étoile supprimé(s)
🔔 🔍 Songrec terminé: Traités: 12 | Erreurs: 0
🚨 ❗ Erreur critique: Problème de base de données
```

**Par défaut** : Console activée, Desktop activé, Email activé si configuré.

## Structure des fichiers

```text
plex_ratings_sync.py          # Script principal
plex_notifications.sh         # Script de notifications
requirements.txt              # Dépendances Python
README_PLEX.md                # Cette documentation
```

## Dépannage

### Base Plex introuvable

```bash
# Chercher manuellement
find / -name "com.plexapp.plugins.library.db" 2>/dev/null

# Puis spécifier le chemin
python3 plex_ratings_sync.py --plex-db /chemin/trouvé/com.plexapp.plugins.library.db
```

### Erreur de permissions

```bash
# Donner les droits sur la base Plex
sudo chmod 644 /chemin/vers/com.plexapp.plugins.library.db
```

### Songrec ne fonctionne pas

```bash
# Vérifier l'installation
songrec --version

# Tester manuellement
songrec audio-file-to-recognized-song "fichier_audio.mp3"
```

## ❓ FAQ

### Le script ne trouve pas ma base Plex ?

```bash
# Chercher manuellement
find / -name "com.plexapp.plugins.library.db" 2>/dev/null

# Puis spécifier le chemin
python3 plex_ratings_sync.py --plex-db /chemin/trouvé/com.plexapp.plugins.library.db
```

### Comment annuler une suppression ?

- Les fichiers supprimés ne peuvent pas être récupérés automatiquement
- Utilisez toujours `--backup` pour créer une sauvegarde
- Vérifiez les logs pour voir ce qui a été supprimé

### Songrec ne reconnaît pas mes fichiers ?

- Vérifiez que les fichiers audio sont lisibles
- Testez manuellement : `songrec audio-file-to-recognized-song "fichier.mp3"`
- Certains fichiers très courts ou bruités peuvent ne pas être reconnus

### Puis-je utiliser le script sur Windows ?

- Oui, mais vous devrez adapter les chemins dans le script
- La base Plex se trouve généralement dans `%LOCALAPPDATA%\Plex Media Server\`

### Le script est-il sûr ?

- **Mode simulation** : Testez toujours d'abord sans `--delete`
- **Sauvegarde** : Utilisez `--backup` pour conserver une copie
- **Logs** : Vérifiez toujours les logs après exécution

## 🤝 Contribuer

Les contributions sont les bienvenues ! Voici comment participer :

### Signaler un bug

1. Vérifiez que le bug n'a pas déjà été signalé
2. Utilisez le template de bug avec :
   - Version de Python
   - Système d'exploitation
   - Commande utilisée
   - Logs d'erreur complets

### Proposer une fonctionnalité

1. Décrivez clairement le besoin
2. Expliquez pourquoi cela serait utile
3. Si possible, proposez une implémentation

### Améliorer le code

1. Fork le projet
2. Créez une branche pour votre fonctionnalité
3. Testez vos changements
4. Soumettez une pull request

### Code de conduite

- Respectez les autres utilisateurs
- Testez vos changements avant de les proposer
- Documentez vos modifications

## 📄 Licence

Ce projet est distribué sous licence MIT. Voir le fichier `LICENSE` pour plus de détails.

---

**⭐ Si ce script vous est utile, n'hésitez pas à mettre une étoile sur GitHub !**
