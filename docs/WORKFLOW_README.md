# 🎵 Workflow Plex Ratings - Configuration Personnalisée

## 🎯 Configuration de Paul

- **Bibliothèque** : `/mnt/mybook/itunes/Music`
- **Automatisation** : Fin de mois (cron mensuel)
- **Logique des étoiles** :
  - **1 ⭐** → Suppression définitive automatique
  - **2 ⭐** → Scan avec `songrec-rename` pour identification/correction

## 🚀 Installation rapide

### 1. Installation des outils
```bash
# Installation du workflow principal
./install_plex_ratings_sync.sh

# Installation de songrec-rename
./install_songrec_rename.sh
```

### 2. Configuration du cron mensuel
```bash
# Ouvrir l'éditeur cron
crontab -e

# Ajouter cette ligne (traitement fin de mois à 2h du matin) :
0 2 28-31 * * [ "$(date -d tomorrow +%d)" -eq 1 ] && /home/paulceline/bin/audio/plex_monthly_workflow.sh
```

## 📋 Workflow mensuel automatique

### Fin de mois (automatique)
Le script `plex_monthly_workflow.sh` s'exécute automatiquement et :

1. **📊 Analyse** les ratings dans Plex
2. **🗑️ Supprime** tous les fichiers avec 1 ⭐ (avec sauvegarde)
3. **📋 Prépare** une queue pour les fichiers avec 2 ⭐
4. **📈 Génère** un rapport mensuel détaillé
5. **🔍 Analyse** les doublons dans la bibliothèque
6. **🧹 Nettoie** les anciens logs/sauvegardes
7. **📧 Envoie** un rapport (optionnel)

### Tous les soirs (automatique)
Le script `plex_daily_ratings_sync.sh` s'exécute automatiquement et :

1. **🗑️ Supprime** automatiquement les fichiers avec 1 ⭐
2. **🎵 Synchronise** les ratings 3-5⭐ vers les métadonnées ID3 des fichiers
3. **📊 Génère** un rapport quotidien des opérations

### Début de mois (manuel)
Pour traiter les fichiers 2 ⭐ avec songrec-rename :

```bash
# Aller dans la queue générée
cd ~/songrec_queue/YYYYMMDD_HHMMSS/

# Lancer le traitement automatique
./process_2_stars.sh
```

## 🎵 Utilisation dans PlexAmp

### Évaluation des morceaux
- **🎧 Écoutez** vos morceaux dans PlexAmp
- **⭐ Évaluez** selon votre satisfaction :
  - **1 ⭐** : Morceau à supprimer définitivement
  - **2 ⭐** : Morceau mal identifié/tagué à corriger
  - **3-5 ⭐** : Morceaux à conserver

### Exemple d'usage
```
🎵 "Unknown Track.mp3" → 2 ⭐ (scan songrec)
🎵 "Bad Song.mp3" → 1 ⭐ (suppression)
🎵 "Great Song.mp3" → 5 ⭐ (conservation)
```

## 📁 Structure des répertoires

```
~/logs/plex_monthly/          # Logs mensuels
~/plex_backup/monthly_YYYYMM/ # Sauvegardes mensuelles
~/songrec_queue/              # Queues de traitement
    └── YYYYMMDD_HHMMSS/
        ├── process_2_stars.sh     # Script de traitement
        ├── files_to_scan.txt      # Liste des fichiers
        ├── files_details.json     # Détails complets
        └── songrec_processing.log # Log du traitement
```

## 🔧 Scripts disponibles

### Scripts principaux
- `plex_monthly_workflow.sh` - Workflow mensuel complet
- `plex_daily_ratings_sync.sh` - Synchronisation quotidienne (suppressions + ID3)
- `plex_ratings_sync.py` - Script de synchronisation de base
- `plex_ratings_helper.sh` - Assistant interactif
- `generate_monthly_report.py` - Génération de rapports mensuels détaillés
- `duplicate_detector.py` - Analyse et détection des doublons

### Scripts d'installation
- `install_plex_ratings_sync.sh` - Installation du workflow principal
- `install_songrec_rename.sh` - Installation de songrec-rename

### Configuration
- `crontab_monthly_workflow.conf` - Exemples de configuration cron
- `PLEX_RATINGS_README.md` - Documentation complète

## 🧪 Tests et validation

### Test manuel du workflow
```bash
# Test complet (simulation)
./plex_monthly_workflow.sh

# Test seulement l'analyse
python3 plex_ratings_sync.py --auto-find-db --stats

# Test songrec-rename
~/songrec_queue/test_songrec.sh
```

### Test avec des fichiers factices
```bash
# Créer une base Plex de démonstration
python3 create_demo_plex_db.py

# Tester avec la base de démo
python3 plex_ratings_sync.py --plex-db /tmp/demo.db --stats
```

## 📊 Exemple de rapport mensuel

```
🎵 TRAITEMENT MENSUEL - Novembre 2025
=====================================

📊 STATISTIQUES:
   ⭐ (1.0): 15 fichiers → 🗑️ SUPPRIMÉS
   ⭐⭐ (2.0): 8 fichiers → 🔍 QUEUE SONGREC
   ⭐⭐⭐ (3.0): 125 fichiers
   ⭐⭐⭐⭐ (4.0): 89 fichiers  
   ⭐⭐⭐⭐⭐ (5.0): 203 fichiers

💾 SAUVEGARDES:
   ~/plex_backup/monthly_202511/

🔍 QUEUE SONGREC:
   ~/songrec_queue/20251130_020015/
   8 fichiers prêts pour traitement
```

## 🛡️ Sécurité et sauvegardes

### Sauvegardes automatiques
- Tous les fichiers supprimés sont sauvegardés
- Conservation des 3 derniers mois
- Logs détaillés de toutes les opérations

### Restauration
```bash
# Restaurer un fichier supprimé
cp ~/plex_backup/monthly_YYYYMM/deleted_1_star/chemin/fichier.mp3 \
   /mnt/mybook/itunes/Music/chemin/

# Restaurer tout un album
cp -r ~/plex_backup/monthly_YYYYMM/deleted_1_star/Artist/Album/ \
      /mnt/mybook/itunes/Music/Artist/
```

### Sauvegarde complète recommandée
```bash
# Avant première utilisation (recommandé)
rsync -av /mnt/mybook/itunes/Music/ \
          /backup/itunes_music_$(date +%Y%m%d)/
```

## 🔄 Intégration avec vos outils existants

### Après le workflow mensuel
```bash
# Nettoyage complémentaire
python3 nettoyer_musique_simple.py --delete --dir /mnt/mybook/itunes/Music

# Organisation Lidarr
./lidarr-organize.sh --source "/mnt/mybook/itunes/Music"

# Maintenance Beets
./beets_monthly_maintenance.sh
```

### Surveillance
```bash
# Surveiller les logs en temps réel
tail -f ~/logs/plex_monthly/monthly_sync_*.log

# Vérifier l'espace disque
df -h /mnt/mybook
```

## 📧 Notifications (optionnel)

### Configuration email
```bash
# Ajouter au début de votre crontab :
NOTIFICATION_EMAIL=votre@email.com

# Installer mailutils si nécessaire :
sudo apt install mailutils
```

### Exemples de notifications
- Rapport mensuel automatique
- Alertes d'espace disque faible
- État des services Plex

## ❓ FAQ et dépannage

### "Base Plex introuvable"
```bash
# Rechercher manuellement
sudo find / -name "com.plexapp.plugins.library.db" 2>/dev/null

# Vérifier le service Plex
systemctl status plexmediaserver
```

### "songrec-rename command not found"
```bash
# Vérifier l'installation
which songrec-rename
echo $PATH

# Réinstaller si nécessaire
./install_songrec_rename.sh
```

### Workflow ne se déclenche pas
```bash
# Vérifier le cron
crontab -l

# Tester manuellement
/home/paulceline/bin/audio/plex_monthly_workflow.sh

# Vérifier les logs système
journalctl -u cron
```

## 📅 Planning recommandé

### Mensuel (automatique)
- **Fin de mois** : Workflow principal (suppression + queue)
- **1er du mois** : Nettoyage complémentaire
- **2ème jour** : Organisation/maintenance

### Manuel selon besoin
- **Traitement queue songrec** : Dès que possible après génération
- **Évaluation dans PlexAmp** : Au fil de vos écoutes
- **Vérifications** : Quelques fois par mois

---

✨ **Avec ce workflow, votre bibliothèque iTunes sera automatiquement nettoyée et optimisée chaque mois selon vos évaluations PlexAmp !** 🎵