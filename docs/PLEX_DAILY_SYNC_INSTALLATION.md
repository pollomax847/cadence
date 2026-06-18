# 📅 Installation de la synchronisation QUOTIDIENNE des ratings Plex

## Résumé du changement

Vous aviez configuré une synchronisation **mensuelle** des étoiles Plex avec votre bibliothèque audio.  
Nous avons changé cela en synchronisation **QUOTIDIENNE** (tous les soirs à 22h00).

| Aspect | Avant | Après |
|--------|-------|-------|
| **Fréquence** | 1 fois/mois | Chaque soir (22h00) |
| **Configuration** | `crontab_monthly_workflow.conf` | `crontab_daily_ratings_sync.conf` |
| **Script** | `plex_monthly_workflow.sh` | `plex_daily_ratings_sync.sh` |
| **Durée** | 5-10 minutes | 1-2 minutes |
| **Impact système** | Lourd (SongRec, etc) | Léger |

---

## ✅ Installation rapide (5 minutes)

### Étape 1 : Installer la cron quotidienne

```bash
crontab -e
```

Puis **ajouter cette ligne** (ou remplacer l'ancienne ligne mensuelle) :

```bash
0 22 * * * /home/paulceline/bin/audio/plex_daily_ratings_sync.sh >> /home/paulceline/logs/plex_ratings/daily.log 2>&1
```

**Explications:**
- `0 22 * * *` = tous les jours à 22h00 (10 PM)
- `/home/paulceline/bin/audio/plex_daily_ratings_sync.sh` = le script
- `>> /home/paulceline/logs/plex_ratings/daily.log 2>&1` = sauvegarde les logs

### Étape 2 : Sauvegarder et vérifier

Sauvegarder avec `Ctrl+X`, puis `Y`

Vérifier que c'est installé :
```bash
crontab -l | grep plex_daily
```

Vous devriez voir :
```
0 22 * * * /home/paulceline/bin/audio/plex_daily_ratings_sync.sh >> /home/paulceline/logs/plex_ratings/daily.log 2>&1
```

### Étape 3 : Créer le répertoire des logs (s'il n'existe pas)

```bash
mkdir -p /home/paulceline/logs/plex_ratings
```

---

## 🧪 Tester le script avant de l'automatiser

### Test 1 : Exécution manuelle simple

```bash
/home/paulceline/bin/audio/plex_daily_ratings_sync.sh
```

Vous devriez voir un rapport détaillé avec :
- ✅ Vérifications effectuées
- 📊 Statistiques
- ✅ Résumé final

### Test 2 : Voir le résultat dans les logs

```bash
tail -20 /home/paulceline/logs/plex_ratings/daily_sync_*.log
```

### Test 3 : Vérifier que cron l'exécutera bien

```bash
crontab -l
```

Devrait afficher la ligne avec `plex_daily_ratings_sync.sh`

---

## 📋 Différences entre ancien et nouveau

### ❌ Ancien workflow mensuel (déprécié)

```bash
# Fichier : crontab_monthly_workflow.conf
0 2 28-31 * * [ "$(date -d tomorrow +%d)" -eq 1 ] && /home/paulceline/bin/audio/plex_monthly_workflow.sh
```

**Inconvénients:**
- Seulement 1 fois par mois
- Exécution lourde : nettoyage + SongRec + maintenance
- Ratings non synchronisés pendant tout le mois

### ✅ Nouveau workflow quotidien (recommandé)

```bash
# Fichier : crontab_daily_ratings_sync.conf
0 22 * * * /home/paulceline/bin/audio/plex_daily_ratings_sync.sh >> /home/paulceline/logs/plex_ratings/daily.log 2>&1
```

**Avantages:**
- ✅ Tous les soirs à 22h00
- ✅ Synchronisation rapide et légère
- ✅ Vos ratings toujours à jour
- ✅ Logs détaillés chaque jour

---

## 🎯 Que fait le nouveau script

Chaque soir à 22h00, le script:

1. **Vérifie** que tout est accessible
   - ✓ Bibliothèque audio
   - ✓ Base de données Plex
   - ✓ Python et dépendances

2. **Synchronise** les ratings
   - Récupère les étoiles depuis Plex
   - Supprime les fichiers marqués 1 étoile
   - Met à jour les métadonnées ID3

3. **Génère un rapport**
   - 📊 Nombre de fichiers supprimés
   - ⚠️ Nombre d'erreurs
   - 📁 Chemin du log

4. **Nettoie les anciens logs**
   - Garde seulement les 30 derniers
   - Libère de l'espace disque

**Durée totale:** 1-2 minutes

---

## 📊 Monitorer la synchronisation

### Voir les logs en temps réel

```bash
# Voir le dernier log
tail -f /home/paulceline/logs/plex_ratings/daily_sync_*.log | tail -f

# Ou plus simplement
tail -f /home/paulceline/logs/plex_ratings/daily.log
```

### Voir les statistiques des 7 derniers jours

```bash
ls -lh /home/paulceline/logs/plex_ratings/daily_sync_*.log | tail -7
```

### Vérifier que la cron est bien active

```bash
# Voir la dernière exécution prévue
crontab -l

# Voir l'historique des exécutions cron
grep CRON /var/log/syslog | tail -20
```

---

## 🆘 Dépannage

### La cron ne s'exécute pas

**Vérifier que cron est actif:**
```bash
systemctl status cron
```

**Doit afficher:** `active (running)`

### Le script ne trouve pas Plex

```bash
# Localiser la base Plex manuellement
find ~/.config/Plex\ Media\ Server -name "*.db" 2>/dev/null
```

Le script devrait le trouver automatiquement avec `--auto-find-db`

### Pas de logs générés

```bash
# Vérifier les permissions
chmod 755 /home/paulceline/bin/audio/plex_daily_ratings_sync.sh
chmod 755 /home/paulceline/logs/plex_ratings
```

### Résoudre les problèmes de permissions

```bash
# Exécution manuelle pour voir les erreurs
bash -x /home/paulceline/bin/audio/plex_daily_ratings_sync.sh
```

---

## 🔄 Désactiver l'ancienne synchronisation mensuelle

Si vous aviez une entrée cron mensuelle, **commentez-la ou supprimez-la** :

```bash
crontab -e
```

Cherchez et supprimez/commentez cette ligne :
```bash
# 0 2 28-31 * * [ "$(date -d tomorrow +%d)" -eq 1 ] && /home/paulceline/bin/audio/plex_monthly_workflow.sh
```

---

## 📞 Support et questions

Pour plus d'informations:
- Configuration: voir `crontab_daily_ratings_sync.conf`
- Logs: `/home/paulceline/logs/plex_ratings/`
- Documentation: `PLEX_RATINGS_README.md`

---

## ✨ Prochaines étapes

1. ✅ **Installer la cron quotidienne** (voir étape 1)
2. ✅ **Tester le script** (voir section test)
3. ✅ **Vérifier les logs** après la première exécution
4. ✅ **Supprimer l'ancienne cron mensuelle** (optionnel)
5. ✅ **Profiter de ratings toujours synchronisés!** 🎉

---

**Date de cette migration:** 13 novembre 2025
