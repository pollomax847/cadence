# Automatisation des Playlists PlexAmp

## 🎵 Qu'est-ce que c'est ?

Ce système génère automatiquement des playlists intelligentes pour PlexAmp basées sur :

- **Notes/Ratings** : Playlists par étoiles (5★, 4★, etc.)
- **Années** : Par décennie + musique récente
- **Genres** : Groupement automatique par style musical
- **Écoutes** : Top 100, favoris, à redécouvrir
- **Artistes** : "Best of" pour les artistes principaux
- **Humeur** : Mix concentration, mix énergique

## 📋 Types de playlists générées

### 🌟 Par Rating
- ⭐ 5 étoiles (X titres)
- ⭐ 4 étoiles (X titres)
- ⭐ 3 étoiles (X titres)

### 🕰️ Par Époque
- 🕰️ Années 1980s (X titres)
- 🕰️ Années 1990s (X titres)
- 🕰️ Années 2000s (X titres)
- 🆕 Récents (5 dernières années)

### 🎵 Par Genre
- 🎵 Rock (X titres)
- 🎵 Pop (X titres)
- 🎵 Jazz (X titres)
- (Etc. selon votre bibliothèque)

### 🔥 Intelligentes
- 🔥 Top 100 plus écoutés
- ❤️ Mes favoris (5★ + souvent écoutés)
- 🔍 À redécouvrir (peu écoutés mais bien notés)
- 🆕 Ajoutés récemment (30 derniers jours)

### 🧘 Par Humeur
- 🧘 Mix concentration (titres longs)
- ⚡ Mix énergique (titres courts et dynamiques)

### 🎤 Par Artiste
- 🎤 Best of [Artiste] (25 meilleurs titres)

## 🚀 Installation et Usage

### Installation
```bash
# Rendre les scripts exécutables
chmod +x generate_plexamp_playlists.sh
chmod +x auto_playlists_plexamp.py
```

### Utilisation en ligne de commande

```bash
# Aperçu des playlists (simulation)
./generate_plexamp_playlists.sh --preview

# Créer toutes les playlists
./generate_plexamp_playlists.sh --create

# Nettoyer les anciennes playlists automatiques
./generate_plexamp_playlists.sh --clean

# Tout nettoyer et recréer
./generate_plexamp_playlists.sh --refresh
```

### Utilisation interactive
```bash
# Menu interactif
./generate_plexamp_playlists.sh
```

## 🔧 Configuration

### Base de données Plex
Le script cherche automatiquement la base Plex ici :
```
/var/snap/plexmediaserver/common/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db
```

### Critères de génération

- **Minimum de titres par genre** : 15 titres
- **Minimum de titres par artiste** : 20 titres pour un "Best of"
- **Best of artiste** : 25 meilleurs titres
- **Mix humeur** : 50 titres maximum
- **Favoris** : Rating ≥ 4★ ET ≥ 3 écoutes
- **À redécouvrir** : ≤ 1 écoute ET rating ≥ 3★

## 🔄 Automatisation

La reference principale est maintenant `AUTO_README.md` (doc unique de l'automatisation reelle).

### Intégration dans le workflow mensuel

Ajoutez cette section à votre `plex_monthly_workflow.sh` :

```bash
# ÉTAPE X: GÉNÉRATION DES PLAYLISTS PLEXAMP
if [ "$ENABLE_AUTO_PLAYLISTS" = "true" ]; then
    log "${BLUE}🎵 Génération des playlists PlexAmp automatiques${NC}"
    
    if [ -f "$SCRIPT_DIR/generate_plexamp_playlists.sh" ]; then
        "$SCRIPT_DIR/generate_plexamp_playlists.sh" --refresh
    else
        log "${YELLOW}⚠️ Script playlists non trouvé${NC}"
    fi
fi
```

### Automatisation quotidienne (configuration actuelle)

Horaires aligns Docker/systemd:

```bash
# Regeneration quotidienne a 23h
0 23 * * * cd /home/paulceline/scripts && ./playlists/generate_plexamp_playlists.sh --refresh
```

### Lancement immediat (a la demande)

```bash
cd /home/paulceline/scripts
./playlists/generate_plexamp_playlists.sh --refresh
```

## 📊 Logs et Monitoring

Les logs sont sauvegardés dans :
```
~/logs/plexamp_playlists/auto_playlists.log
```

## 🎯 Avantages

1. **Automatique** : Plus besoin de créer manuellement les playlists
2. **Intelligent** : Basé sur vos habitudes d'écoute réelles
3. **Adaptatif** : Se met à jour avec votre bibliothèque
4. **Organisé** : Structure claire et logique
5. **Personnalisé** : Basé sur VOS ratings et écoutes

## 🔧 Personnalisation

### Modifier les critères

Éditez `auto_playlists_plexamp.py` pour :
- Changer le nombre minimum de titres par playlist
- Modifier les critères des playlists intelligentes
- Ajouter de nouveaux types de playlists
- Personnaliser les émojis et noms

### Exemple de personnalisation

```python
# Dans create_smart_playlists(), ajoutez :
# Playlist "Nostalgie" - musique d'il y a 10-20 ans
nostalgia_years = range(current_year - 20, current_year - 10)
nostalgia_tracks = [t for t in tracks if t['year'] in nostalgia_years and t['rating'] >= 6]
if nostalgia_tracks:
    smart_playlists[f"💭 Nostalgie ({len(nostalgia_tracks)} titres)"] = nostalgia_tracks
```

## 🚨 Important

- **Backup** : Plex sauvegarde automatiquement, mais faites un backup avant la première utilisation
- **Test** : Utilisez `--preview` pour voir ce qui sera créé
- **Nettoyage** : Les playlists automatiques sont identifiées par leurs émojis et sont remplacées à chaque exécution

## 💡 Conseils d'utilisation

1. **Ratings** : Notez vos musiques pour de meilleures playlists intelligentes
2. **Nettoyage** : Exécutez `--clean` si vous voulez supprimer toutes les playlists automatiques
3. **Timing** : Lancez après avoir ajouté de nouveaux albums pour les inclure
4. **PlexAmp** : Les playlists apparaîtront automatiquement dans PlexAmp après génération