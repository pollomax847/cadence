# Backgrounds des posters de playlists

Pose ici tes images de fond (.jpg, .jpeg, .png, .webp).

## Règle de nommage

Le nom du fichier (sans extension) est comparé au titre de la playlist.
Le système choisit **l'image dont les mots correspondent le mieux** au titre.

### Exemples

| Fichier                | Playlist matchée                          |
|------------------------|-------------------------------------------|
| `rap.jpg`              | "[Auto] Rap FR (120 titres)"              |
| `90s_rap.jpg`          | "[Auto] 90's Rap (45 titres)"             |
| `jazz.png`             | "[Auto] Jazz & Blues (32 titres)"         |
| `chill.webp`           | "[Auto] Chill / Concentration (80 titres)"|
| `rock.jpg`             | "[Auto] Pop/Rock (200 titres)"            |
| `electronic.jpg`       | "[Auto] Electronic / Club (60 titres)"    |
| `default.jpg`          | (fallback si aucun autre ne correspond)   |

## Taille recommandée

600×600 px minimum (carré). Le système redimensionne automatiquement à la taille
définie dans le style JSON (par défaut 600×600).

## Dossier personnalisé

Tu peux aussi définir un dossier différent dans le fichier style JSON :
```json
{
  "backgrounds_dir": "/chemin/vers/ton/dossier"
}
```
