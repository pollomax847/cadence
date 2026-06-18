# 🎨 Génération Améliorée des Posters de Playlists

## 🎯 Améliorations Apportées

### 1. **Dégradés Sophistiqués**
- **Dégradé Radial** : Crée un effet de profondeur naturel du centre vers les bords
- **Dégradé Diagonal Multi-Couleurs** : Transitions fluides sur plusieurs couleurs
- **Dégradé Ondulatoire** : Effet d'onde sinusoïdale pour une esthétique plus organique

### 2. **Effets Visuels**
- **Texture de Bruit** : Ajoute une subtilité qui empêche l'effet "trop lisse"
- **Motifs Géométriques** : Dots, lignes, grilles pour plus de profondeur
- **Overlay Radial** : L'assombrissement s'intensifie vers les bords pour plus de profondeur
- **Glow Emoji** : Halos subtils autour des emojis pour les faire ressortir

### 3. **Typographie Améliorée**
- **Ombres Multi-Couches** : Plus d'une couche d'ombre pour un rendu professionnel
- **Ligne Décorative Gradient** : La séparation s'estompe progressivement
- **Meilleure Gestion du Contraste** : Texte plus lisible sur tous les gradients

### 4. **Styles Thématiques**
Plusieurs profils de styles prédéfinis pour différentes ambiances :

## 📋 Styles Disponibles

### `poster_style.elegant.json` ✨
- **Esthétique** : Moderne et élégant
- **Couleurs** : Bleus et teintes cool par défaut
- **Utilisation** : Playlists générales, meilleures polyvalence
- **Pattern** : Dots légers

### `poster_style.modern.json` 💫
- **Esthétique** : Contemporain avec effet futuriste
- **Couleurs** : Noirs, bleus profonds, couleurs électriques
- **Utilisation** : Musique électronique, synthwave, futuriste
- **Pattern** : Grille pour effet technologique

### `poster_style.minimal.json` 🎼
- **Esthétique** : Épuré et classique
- **Couleurs** : Teintes douces et neutres
- **Utilisation** : Jazz, classique, musique acoustique
- **Pattern** : Aucun (épure totale)

### `poster_style.vibrant.json` 🔥
- **Esthétique** : Coloré et énergique
- **Couleurs** : Arc-en-ciel, couleurs chaudes et froides
- **Utilisation** : Pop, musique énergique, party
- **Pattern** : Dots pour ajouter du rythme

### `poster_style.synthwave.json` 🌆
- **Esthétique** : Rétro/Vintage 80s-90s
- **Couleurs** : Magentas, purples, teintes électriques
- **Utilisation** : 80s, synthpop, vaporwave
- **Pattern** : Lignes pour effet rétro

### `poster_style.luxe.json` 👑
- **Esthétique** : Premium et sophistiqué
- **Couleurs** : Teintes dorées et marron
- **Utilisation** : Jazz, soul, musique sophistiquée
- **Pattern** : Dots subtils

### `poster_style.default.json` 🎵
- **Esthétique** : Équilibre entre tous les styles
- **Couleurs** : Bleus et turquoise
- **Utilisation** : Fallback/Défaut
- **Pattern** : Dots légers

---

## 🚀 Utilisation

### Configuration pour utiliser un style

#### Option 1 : Variable d'environnement
```bash
export PLEX_POSTER_STYLE_CONFIG=/home/paulceline/scripts/playlists/poster_style.elegant.json
python3 auto_playlists_plexamp.py --generate-posters
```

#### Option 2 : Docker Compose
```yaml
environment:
  - PLEX_POSTER_STYLE_CONFIG=/app/playlists/poster_style.elegant.json
```

#### Option 3 : Fichier .env
```
PLEX_POSTER_STYLE_CONFIG=/home/paulceline/scripts/playlists/poster_style.elegant.json
```

---

## 🎨 Configuration Personnalisée

### Créer votre propre style

Copiez un style existant et modifiez les paramètres :

```json
{
  "size": 600,
  "overlay_alpha": 120,
  "title_size": 48,
  "subtitle_size": 26,
  "emoji_size": 100,
  
  "title_color": [255, 255, 255, 255],
  "title_shadow_color": [0, 0, 0, 200],
  "title_stroke_width": 1,
  "subtitle_color": [220, 220, 220, 240],
  "line_color": [255, 255, 255, 100],
  
  "add_noise": true,
  "add_pattern": true,
  "pattern_type": "dots",
  
  "default_colors": [[45, 85, 150], [100, 180, 240]],
  
  "themes": [
    {
      "keywords": ["mon-genre"],
      "match": "any",
      "emoji": "🎶",
      "colors": [[R, G, B], [R, G, B]]
    }
  ]
}
```

### Paramètres Configurables

| Paramètre | Type | Description |
|-----------|------|-------------|
| `size` | int | Taille du poster (256-2000px) |
| `overlay_alpha` | int | Assombrissement (0-255) |
| `add_noise` | bool | Ajouter texture bruit |
| `add_pattern` | bool | Ajouter motifs géométriques |
| `pattern_type` | str | Type de pattern : `dots`, `lines`, `grid` |
| `title_size` | int | Taille titre (14-200px) |
| `emoji_size` | int | Taille emoji (16-300px) |

---

## 📊 Résultats

### Avant / Après
- ✅ Dégradés plus fluides et naturels
- ✅ Texture subtile ajoutant de la profondeur
- ✅ Emojis avec halos pour plus d'impact
- ✅ Texte plus élégant avec ombres multi-couches
- ✅ Séparation décorée avec effect gradient
- ✅ Design cohérent et professionnel

---

## 🔧 Dépannage

### Les posters ne changent pas
Vérifiez que:
1. `PLEX_POSTER_STYLE_CONFIG` pointe vers un fichier valide
2. Le fichier JSON est syntaxiquement correct
3. Plex a bien appliqué les nouveaux posters (refresh)

### Les textures ne s'affichent pas
Vérifiez:
1. `add_noise` et `add_pattern` sont à `true` dans le config
2. `pattern_type` est valide (`dots`, `lines`, `grid`)

### Les emojis ne s'affichent pas
Installez la police Noto Color Emoji:
```bash
apt-get install fonts-noto-color-emoji
```

---

## 📝 Générateur de Posters

Pour générer/mettre à jour les posters :

```bash
# Avec style spécifique
PLEX_POSTER_STYLE_CONFIG=poster_style.elegant.json python3 auto_playlists_plexamp.py --generate-posters

# Avec style par défaut
python3 auto_playlists_plexamp.py --generate-posters
```

---

**Amusez-vous à créer vos propres styles! 🎨✨**
