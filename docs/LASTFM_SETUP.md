# 🎵 Configuration Last.fm pour PlexAmp Auto Playlists

## Pourquoi Last.fm ?

Last.fm est un service qui **enregistre automatiquement chaque chanson** que vous écoutez (scrobbles). Cet intégration permet de créer des playlists basées sur **vos vraies écoutes**, pas juste les ratings Plex.

**Exemple** : 
- Vous avez écouté "Bohemian Rhapsody" 150 fois sur Last.fm
- La playlist **Last.fm Top écoutes** vous le propose en priorité
- Vous pouvez même découvrir des patterns d'écoute cachés !

---

## Étape 1️⃣ : Créer un compte Last.fm (si nécessaire)

1. Allez sur **https://www.last.fm/join**
2. Créez un compte avec un pseudo (ex: `pol2_1`, `alice_music`, etc.)
3. Confirmez votre email

> **Note** : Vous devez d'abord avoir écouté des chansons sur Last.fm pour que les playlists aient des données !

---

## Étape 2️⃣ : Obtenir votre API Key Last.fm

### A. Allez sur la page API Last.fm
➜ **https://www.last.fm/api/account/create**

### B. Remplissez le formulaire (5 champs simples)

| Champ | Valeur | Exemple |
|-------|--------|---------|
| **Application name** | Nom de votre app | `PlexAmp Auto Playlists` |
| **Application type** | Type | Sélectionnez `Desktop` |
| **Commercial?** | Non-commercial? | Cochez `Non-commercial` |
| **Description** | Courte description | `Auto playlist generation from scrobbles` |
| **Personal website** | (Optionnel) | `https://plex.example.com` |

### C. Acceptez les conditions

- Lisez et acceptez **Last.fm API Terms**
- Cliquez **Create Application**

### D. Récupérez votre API Key

Vous verrez une page avec :
- ✅ **API Key** (longue chaîne hexadécimale) → **C'EST CELUI-CI !**
- Shared Secret (vous ne le besoin pas)
- ...autres infos

**Exemple d'API Key** :
```
a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4
```

> ⚠️ **IMPORTANT** : Gardez votre API Key **secrète** ! Ne la partagez pas publiquement.

---

## Étape 3️⃣ : Configurer dans le Docker

### Option A : Configuration via l'interface webui (📱 Facile)

1. Ouvrez **http://localhost:8900/playlists**
2. Allez à l'onglet **Auto ✨**
3. Remplissez :
   - **Pseudo Last.fm** : `pol2_1` (votre pseudo)
   - **API Key Last.fm** : Collez votre clé
   - **Période Last.fm** : Choisissez (overall/7day/1month/etc.)

4. Cliquez **Générer** → Les playlists Last.fm seront créées ! 🎉

### Option B : Configuration via fichier `.env` (🔧 Avancé)

1. Ouvrez `.env` dans le répertoire `/scripts`
2. Remplacez :

```bash
LASTFM_USER=pol2_1
LASTFM_API_KEY=votre_clé_ici
LASTFM_PERIOD=overall
LASTFM_MAX_PAGES=5
```

3. Redémarrez le container :

```bash
docker compose up -d webui
```

---

## Étape 4️⃣ : Générer les playlists

### Via l'interface webui :

1. Allez à **http://localhost:8900/playlists**
2. Onglet **Auto ✨**
3. Cliquez **Générer toutes les playlists** ou **Prévisualiser d'abord**

### Via la CLI (avancé) :

```bash
docker exec plex-scripts-webui python3 playlists/auto_playlists_plexamp.py \
  --plex-db /plex/Plug-in\ Support/Databases/com.plexapp.plugins.library.db \
  --lastfm-user pol2_1 \
  --lastfm-period overall \
  --lastfm-max-pages 5 \
  --verbose
```

---

## Quoi de neuf ?

Une fois configuré, vous aurez **3 nouvelles playlists** :

| Playlist | Contient | Tri |
|----------|----------|-----|
| 📈 **Last.fm Top écoutes** | Top 10% des titres les plus écoutés | Par play_count ↓ |
| 🔥 **Last.fm Rotation forte** | Titres écoutés 75-90 percentile | Par play_count ↓ |
| 🌊 **Last.fm Rotation moyenne** | Titres écoutés 50-75 percentile | Par play_count ↓ |

**Exemple** : Si vous avez écouté 5000 titres sur Last.fm :
- 📈 Top écoutes = top 500 (300 titres max disponibles localement)
- 🔥 Rotation forte = 500-1250 titres
- 🌊 Rotation moyenne = 1250-2500 titres

> **💡 Astuce** : Ces playlists sont mises à jour chaque fois que vous relancez la génération !

---

## 🐛 Dépannage

### ❌ "Match Plex: 0/5000" (aucun titre trouvé)

**Problèmes possibles** :
1. Votre pseudo Last.fm est incorrect → Vérifiez l'orthographe
2. L'API Key est invalide → Récréez-la sur https://www.last.fm/api/account/create
3. Vous n'avez pas d'écoutes sur Last.fm → Écoutez d'abord quelques chansons !
4. Les noms d'artistes/titres ne matchent pas → Plex a des noms différents

**Solution** : Vérifiez dans les logs (onglet **Auto ✨** → Afficher les logs)

### ❌ "Cannot connect to Last.fm API"

**Cause** : Connexion réseau bloquée ou API Last.fm down

**Solution** :
- Vérifiez votre connexion internet
- Testez : `curl -s "https://ws.audioscrobbler.com/2.0/?method=user.getInfo&user=pol2_1&api_key=YOUR_KEY&format=json"`
- Vérifiez le statut https://www.last.fm/

### ❌ "API Key missing" dans docker-compose

**Cause** : Vous avez laissé `LASTFM_API_KEY=` vide

**Solution** : Remplissez `.env` avec votre vraie clé

---

## 📚 Ressources

- **Last.fm API Docs** : https://www.last.fm/api
- **Create API Key** : https://www.last.fm/api/account/create
- **My Account** : https://www.last.fm/user/{votre_pseudo}

---

## ❓ Questions fréquentes

**Q: Puis-je utiliser plusieurs comptes Last.fm?**  
R: Non, un seul par Docker container. Lancez un nouveau container avec un autre compte Last.fm si besoin.

**Q: Mes écoutes vont-elles s'envoyer à Last.fm?**  
R: Non, on **lit** juste vos écoutes existantes. Plex n'envoie rien à Last.fm automatiquement.

**Q: Dois-je payer pour Last.fm?**  
R: Non, le service est gratuit. L'API aussi (il y a des limites, mais 5 pages/5000 titres c'est OK).

**Q: À quelle fréquence sont les playlists mises à jour?**  
R: Quand vous cliquez "Générer". Pas de mise à jour auto pour l'instant.

---

## ✅ Vous êtes prêt !

Vous avez tout ce qu'il faut. Allez créer vos playlists Last.fm ! 🚀

**Besoin d'aide?** Consultez les logs dans l'interface ou relisez cette guide.
