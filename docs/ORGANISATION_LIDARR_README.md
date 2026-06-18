# 🎵 Gestionnaire Audio Rapide - Organisation Lidarr + Recherche En Ligne

## Description
Scripts Python ultra-rapides pour nettoyer et organiser votre bibliothèque musicale selon la structure Lidarr, avec **recherche en ligne optionnelle** pour enrichir les tags manquants.

**MODES DISPONIBLES :**
- ⚡ **RAPIDE** - Tags existants uniquement (hors ligne)
- 🌐 **ENRICHI** - Tags existants + recherche en ligne si nécessaire
- 🔧 **HYBRIDE** - Combinaison intelligente pour le meilleur des deux mondes

## Fonctionnalités de recherche en ligne

### 🎵 **AcoustID** (Reconnaissance audio)
- Reconnaissance par empreinte audio comme SongRec
- Base de données MusicBrainz
- Très précis même avec fichiers mal taguées

### 📡 **Last.fm** (Métadonnées)
- API Last.fm pour récupérer métadonnées
- Bon pour albums/artistes populaires
- Rapide et fiable

### 🤖 **Mode intelligent**
- Essaie **tags existants** d'abord (rapide)
- Si manquant → **AcoustID** (reconnaissance)
- Si échec → **Last.fm** (recherche par nom)
- Garde toujours les bons tags existants

## Installation

### Étape 1 : Dépendances de base (obligatoire)
```bash
pip3 install mutagen
```

### Étape 2 : Dépendances recherche en ligne (optionnel)
```bash
# Installation automatique
./install_online_dependencies.sh

# OU installation manuelle
pip3 install pyacoustid pylast requests
```

## Scripts disponibles

### 1. **Script principal complet**
```bash
python3 nettoyer_musique_simple.py
```
**Nouveaux modes avec recherche en ligne :**
- 1. 🧹 Nettoyage
- 2. 📁 Organisation Lidarr (hors ligne) 
- 3. 🌐 **Organisation Lidarr + Recherche en ligne**
- 4. 🔧 Les deux (hors ligne)
- 5. 🔧 **Les deux + Recherche en ligne**

### 2. **Scripts rapides (non interactifs)**
```bash
# Organisation hors ligne
python3 organiser_rapide_lidarr.py

# Nettoyage
python3 nettoyer_rapide.py
```

## Exemples d'utilisation

### Mode rapide (tags existants)
```bash
python3 nettoyer_musique_simple.py
# Choisir : 2 (Organisation hors ligne)
# ⚡ Ultra rapide, utilise vos tags actuels
```

### Mode enrichi (recherche en ligne)
```bash
python3 nettoyer_musique_simple.py  
# Choisir : 3 (Organisation + recherche en ligne)
# 🌐 Plus lent, enrichit tags manquants
# 🎯 Parfait pour fichiers sans métadonnées
```

### Mode hybride intelligent
```bash
python3 nettoyer_musique_simple.py
# Choisir : 5 (Les deux + recherche en ligne)
# 🧹 Nettoie d'abord les fichiers suspects
# 📁 Organise avec enrichissement en ligne
```

## Comparaison des modes

| Mode | Vitesse | Qualité tags | Internet requis | Idéal pour |
|------|---------|--------------|----------------|------------|
| **Hors ligne** | ⚡⚡⚡ | Existants | ❌ | Fichiers déjà taguées |
| **En ligne** | ⚡⚡ | Enrichis | ✅ | Fichiers mal/non taguées |
| **Hybride** | ⚡⚡ | Optimale | ✅ | Collections mixtes |

## Structure de sortie Lidarr (identique)

```
📁 Destination/
├── 📁 Artist Name/
│   ├── 📁 Album Name (2023)/
│   │   ├── 01 - Song Title.mp3
│   │   ├── 02 - Another Song.flac
│   │   └── 03 - Final Track.m4a
│   └── 📁 Another Album (2024)/
│       └── 01 - New Song.mp3
└── 📁 Another Artist/
    └── 📁 Single Album/
        └── Track Name.mp3
```

## Fonctionnalités avancées

### 🌐 **Recherche en ligne intelligente**
- **Rate limiting** automatique pour éviter les blocages API
- **Fallback** : AcoustID → Last.fm → Tags existants
- **Seuil de confiance** configurable (défaut: 80%)
- **Préservation** des bons tags existants

### � **Rapports détaillés**
- Statistiques d'enrichissement
- Sources de données utilisées
- Fichiers avec tags améliorés
- Temps de traitement par source

### ⚡ **Optimisations**
- Cache local des résultats (évite re-recherche)
- Traitement par lots intelligent
- Pause automatique entre requêtes API

## Configuration APIs (optionnel)

Les clés API sont incluses (publiques), mais vous pouvez utiliser les vôtres :

```python
# Dans nettoyer_musique_simple.py
self.acoustid_api_key = "VOTRE_CLE"
self.lastfm_api_key = "VOTRE_CLE"
```

### Obtenir vos clés :
- **AcoustID** : https://acoustid.org/new-application
- **Last.fm** : https://www.last.fm/api/account/create

## Cas d'usage recommandés

### 📁 **Collection déjà bien taguée**
```bash
# Mode rapide suffit
python3 organiser_rapide_lidarr.py
```

### 🎵 **Downloads YouTube/Torrent mal taguées**
```bash
# Mode en ligne obligatoire
python3 nettoyer_musique_simple.py
# → Option 3 ou 5
```

### 🔄 **Collection mixte (taguée + non taguée)**
```bash
# Mode hybride optimal
python3 nettoyer_musique_simple.py  
# → Option 5 (Les deux + en ligne)
```

### 🧹 **Gros nettoyage + organisation**
```bash
# Workflow complet
python3 nettoyer_musique_simple.py
# 1. Nettoie les fichiers corrompus
# 2. Enrichit tags manquants en ligne
# 3. Organise structure Lidarr
```

## Avantages vs alternatives

| Outil | Vitesse | Hors ligne | Tags enrichis | Structure Lidarr |
|-------|---------|------------|---------------|------------------|
| **Ce script (hors ligne)** | ⚡⚡⚡ | ✅ | ❌ | ✅ |
| **Ce script (en ligne)** | ⚡⚡ | ❌ | ✅ | ✅ |
| Beets | 🐌 | ❌ | ✅ | ✅ |
| SongRec | 🐌🐌 | ❌ | ✅ | ❌ |
| MusicBrainz Picard | 🐌 | ❌ | ✅ | ❌ |

## Logs et rapports

En mode en ligne, génération automatique de :
- `music_cleaner_YYYYMMDD_HHMMSS.log`
- Rapports d'enrichissement avec sources
- Statistiques de performance par API

## Installation troubleshooting

### Erreur pyacoustid
```bash
# Ubuntu/Debian
sudo apt-get install python3-dev libchromaprint-dev

# OU utiliser le mode Last.fm seul
pip3 install pylast
```

### Test des dépendances
```bash
./install_online_dependencies.sh
# → Affiche le statut de chaque dépendance
```

**🎯 Le meilleur des deux mondes : vitesse ET qualité !** 🎵