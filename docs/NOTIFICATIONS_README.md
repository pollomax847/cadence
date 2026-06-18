# 🔔 SYSTÈME DE NOTIFICATIONS PLEX

## 📋 Vue d'ensemble

Le système de notifications vous informe automatiquement de toutes les actions effectuées par les workflows Plex via des notifications desktop et/ou email.

## ✨ Types de Notifications

### 🚀 Notifications de Workflow
- **Démarrage** - Quand un workflow commence avec le nombre de fichiers à traiter
- **Progression** - Étapes importantes (suppression, songrec, synchronisation)
- **Résumé final** - Statistiques complètes à la fin du traitement

### 🎯 Notifications par Action
- **🗑️ Suppression** - Fichiers 1⭐ supprimés (nombre + détails)
- **🔍 Songrec** - Résultats du scan songrec-rename (traités/erreurs)
- **🎵 Sync Ratings** - Synchronisation des métadonnées (réussies/échecs)
- **❌ Erreurs** - Problèmes critiques nécessitant attention

## 🔧 Configuration

### Configuration Interactive
```bash
./plex_notifications.sh configure
```

### Configuration Manuelle
Fichier: `~/.config/plex_notifications.conf`
```bash
ENABLE_DESKTOP_NOTIFICATIONS=true
ENABLE_EMAIL_NOTIFICATIONS=true
EMAIL_RECIPIENT="votre@email.com"
SMTP_SERVER=""
NOTIFICATION_LEVEL="info"
LOG_NOTIFICATIONS=true
```

## 📱 Notifications Desktop

### Prérequis
- `notify-send` installé (généralement inclus dans les environnements desktop Linux)
- Session graphique active

### Types d'icônes
- 🎵 **Workflow** : `multimedia-audio-player`
- 🗑️ **Suppression** : `user-trash-full`
- 🔍 **Songrec** : `audio-card`
- 🎵 **Ratings** : `audio-volume-high`
- ❌ **Erreurs** : `dialog-error`

### Niveaux d'urgence
- **Normal** : Opérations standard
- **Critical** : Erreurs ou actions importantes

## 📧 Notifications Email

### Configuration Email Simple
```bash
# Installation du client mail (Ubuntu/Debian)
sudo apt install mailutils

# Configuration basique
sudo dpkg-reconfigure postfix
```

### Exemple de notification email
```
Sujet: [hostname] Plex Audio: Workflow mensuel terminé

Workflow mensuel Plex terminé en 00:03:42

📊 RÉSUMÉ DES ACTIONS:
═══════════════════════════

🗑️  SUPPRESSION (1⭐):
   📀 Albums: 0
   📁 Fichiers supprimés: 0

🔍 SONGREC-RENAME (2⭐):
   📀 Albums: 2  
   ✅ Fichiers traités: 26
   ❌ Erreurs: 3

🎵 SYNC RATINGS (3-5⭐):
   ✅ Fichiers synchronisés: 194
   ❌ Erreurs: 0

📈 TOTAL:
   📁 Fichiers traités: 220
   ⏱️  Durée: 00:03:42

✅ Workflow terminé sans erreur.

Logs disponibles dans: ~/logs/plex_monthly/
```

## 🎮 Utilisation

### Notifications Automatiques
Les notifications sont **automatiquement envoyées** lors de :
- Exécution du workflow mensuel (`plex_monthly_workflow.sh`)
- Utilisation de l'interface albums (`manage_album_ratings.sh`)
- Erreurs critiques dans les scripts

### Test des Notifications
```bash
# Test complet
./plex_notifications.sh test

# Test d'une notification spécifique
./plex_notifications.sh workflow_started 5 12 89 2 1
```

### Notifications Manuelles
```bash
# Notification de suppression
./plex_notifications.sh files_deleted 5 "Albums: 2"

# Notification songrec
./plex_notifications.sh songrec_completed 25 2 2 8

# Notification d'erreur
./plex_notifications.sh critical_error "Encodage" "Caractères spéciaux détectés"
```

## 📊 Exemples de Notifications

### Notification de Démarrage
```
🚀 Workflow Plex démarré
29 fichiers à traiter
```

### Notification Songrec
```
🔍 Songrec terminé
Traités: 26 | Erreurs: 3
```

### Notification de Résumé Final
```
🎵 Workflow Plex terminé
220 fichiers traités en 00:03:42
```

### Notification d'Erreur Critique
```
❌ Erreur Plex
Encodage: Caractères spéciaux dans /path/to/file
```

## ⚙️ Personnalisation

### Désactiver Notifications Desktop
```bash
echo "ENABLE_DESKTOP_NOTIFICATIONS=false" >> ~/.config/plex_notifications.conf
```

### Modifier le Niveau de Notifications
```bash
# Dans ~/.config/plex_notifications.conf
NOTIFICATION_LEVEL="warning"  # Seulement erreurs et avertissements
NOTIFICATION_LEVEL="error"    # Seulement erreurs critiques
NOTIFICATION_LEVEL="info"     # Toutes les notifications (défaut)
```

### Configuration SMTP Avancée
```bash
# Dans ~/.config/plex_notifications.conf
SMTP_SERVER="smtp.gmail.com:587"
SMTP_USER="votre@gmail.com"
SMTP_PASSWORD="votre-app-password"
```

## 🔍 Débogage

### Vérifier la Configuration
```bash
cat ~/.config/plex_notifications.conf
```

### Tester notify-send
```bash
notify-send "Test" "Notification de test"
```

### Logs des Notifications
Les notifications sont loggées dans les fichiers de log principaux du workflow.

### Problèmes Courants

#### Notifications Desktop ne s'affichent pas
- Vérifier que `notify-send` est installé : `which notify-send`
- Vérifier que DISPLAY est défini : `echo $DISPLAY`
- Tester manuellement : `notify-send "Test" "Message"`

#### Emails non reçus
- Vérifier la configuration postfix : `sudo postfix status`
- Tester l'envoi : `echo "Test" | mail -s "Test" votre@email.com`
- Vérifier les logs mail : `tail -f /var/log/mail.log`

## 🏆 Avantages

### 💡 Visibilité
- Suivi en temps réel des opérations
- Notifications même si vous n'êtes pas devant l'écran
- Historique complet par email

### 🛡️ Sécurité
- Alertes immédiates en cas d'erreur
- Confirmation des suppressions importantes
- Traçabilité de toutes les actions

### 📈 Statistiques
- Résumés détaillés après chaque traitement
- Métriques de performance (durée, fichiers traités)
- Comparaison entre sessions

Le système de notifications transforme votre workflow Plex en un système **totalement transparent** et **proactif** ! 🎉