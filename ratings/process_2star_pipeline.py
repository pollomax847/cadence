#!/usr/bin/env python3
"""
Pipeline complet pour les tracks 2★ dans Plex :
  1. Récupère les fichiers notés 2★ via l'API Plex (avec chemins réels)
  2. Lance `songrec-rename -i <fichier>` pour identifier et taguer en ID3
  3. Lance `beet import <fichier>` pour classer dans la bibliothèque Music
  4. Efface le rating 2★ via l'API Plex (la playlist [Auto] 2★ disparaît)

Usage (sur l'hôte) :
    python3 ratings/process_2star_pipeline.py              # mode réel
    python3 ratings/process_2star_pipeline.py --dry-run    # simulation (affiche les fichiers)
    python3 ratings/process_2star_pipeline.py --no-beet    # songrec-rename seulement
    python3 ratings/process_2star_pipeline.py --no-clear   # ne pas effacer le rating après

IMPORTANT : songrec-rename et beet doivent être installés sur la machine qui exécute ce script.
"""

import os
import sys
import argparse
import subprocess
import shutil
import json
import re
import difflib
from pathlib import Path
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

from mutagen import File as MutagenFile

PLEX_URL   = os.environ.get("PLEX_URL",   "http://localhost:32400").rstrip("/")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")

# Correspondance étoiles → valeur interne Plex (0-10 : 2★ = 4)
STARS_TO_PLEX = {1: 2, 2: 4, 3: 6, 4: 8, 5: 10}

# Chemin de remontée vers les fichiers réels (si exécuté depuis Docker, adapter via env)
# Sur l'hôte le chemin Plex retourne les chemins réels → aucune remontée nécessaire.
PLEX_PATH_PREFIX = os.environ.get("PLEX_PATH_PREFIX", "")   # ex. /home/paulceline/Musiques
DOCKER_PATH_PREFIX = os.environ.get("DOCKER_PATH_PREFIX", "")  # ex. /music


def _candidate_mappings() -> list[tuple[str, str]]:
    """Mappings connus entre chemins hôte Plex et chemins montés dans le conteneur."""
    pairs: list[tuple[str, str]] = []

    # Mapping explicite prioritaire (rétrocompatibilité)
    if PLEX_PATH_PREFIX and DOCKER_PATH_PREFIX:
        pairs.append((PLEX_PATH_PREFIX, DOCKER_PATH_PREFIX))

    # Mappings les plus fréquents sur cette stack Docker
    pairs.extend([
        ("/mnt/MyBook/Musiques/iTunes", "/itunes"),
        ("/mnt/MyBook/itunes",          "/itunes"),
        ("/mnt/MyBook/Music",           "/music"),
        ("/mnt/MyBook/playlists",       "/playlists"),
        ("/mnt/Music",                  "/music"),
        ("/home/paulceline/Musiques",   "/music"),
        ("/mnt/ssd/Musiques",           "/music"),
        ("/media/paulceline/Music/music", "/music"),
    ])
    return pairs


def _normalized_name(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _find_nearby_renamed_file(path: Path) -> str | None:
    parent = path.parent
    if not parent.is_dir():
        return None

    expected = _normalized_name(path.stem)
    if not expected:
        return None

    candidates = [child for child in parent.iterdir() if child.is_file() and child.suffix == path.suffix]
    if not candidates:
        return None

    scored: list[tuple[float, Path]] = []
    for child in candidates:
        score = difflib.SequenceMatcher(None, expected, _normalized_name(child.stem)).ratio()
        scored.append((score, child))

    best_score, best_path = max(scored, key=lambda item: item[0])
    if best_score >= 0.45:
        return str(best_path)
    return None


def _resolve_file_path(original: str) -> tuple[str, str]:
    """Résout un chemin de track Plex vers un fichier réellement accessible.

    Retourne (path, source) où source décrit la stratégie utilisée.
    """
    if not original:
        return original, "empty"

    p = Path(original)
    if p.exists():
        return original, "as-is"
    nearby = _find_nearby_renamed_file(p)
    if nearby:
        return nearby, "same-dir-similar"

    # 1) mappings explicites / connus
    for src, dst in _candidate_mappings():
        if original.startswith(src.rstrip("/") + "/") or original == src:
            cand = original.replace(src, dst, 1)
            if Path(cand).exists():
                return cand, f"mapped:{src}->{dst}"
            nearby = _find_nearby_renamed_file(Path(cand))
            if nearby:
                return nearby, f"mapped-similar:{src}->{dst}"

    # 2) heuristiques Plex/iTunes usuelles
    heuristics: list[str] = []
    if "/iTunes/" in original:
        heuristics.append("/itunes/" + original.split("/iTunes/", 1)[1].lstrip("/"))
    if "/itunes/" in original:
        heuristics.append("/itunes/" + original.split("/itunes/", 1)[1].lstrip("/"))
    if "/Music/" in original:
        heuristics.append("/itunes/Music/" + original.split("/Music/", 1)[1].lstrip("/"))

    for cand in heuristics:
        if Path(cand).exists():
            return cand, "heuristic"
        nearby = _find_nearby_renamed_file(Path(cand))
        if nearby:
            return nearby, "heuristic-similar"

    # 3) fallback par nom de fichier dans les volumes montés (plus coûteux)
    name = Path(original).name
    if name:
        for root in ("/itunes", "/music"):
            if not Path(root).exists():
                continue
            try:
                found = subprocess.run(
                    ["find", root, "-type", "f", "-name", name, "-print", "-quit"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                match = found.stdout.strip().splitlines()
                if match:
                    return match[0], f"basename:{root}"
            except Exception:
                pass

    return original, "unresolved"

# ─── helpers Plex API ────────────────────────────────────────────────────────

def _plex_req(method: str, path: str, extra: dict | None = None) -> ET.Element | int:
    params: dict[str, str] = {"X-Plex-Token": PLEX_TOKEN}
    if extra:
        params.update(extra)
    # Construire l'URL sans double-encoding des params déjà dans path
    if "?" in path:
        url = f"{PLEX_URL}{path}&" + urllib.parse.urlencode(params)
    else:
        url = f"{PLEX_URL}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            if method == "GET" and data:
                return ET.fromstring(data)
            return r.status
    except urllib.error.HTTPError as e:
        print(f"  ⚠️  HTTP {e.code} sur {path}", file=sys.stderr)
        return e.code


def plex_get(path: str) -> ET.Element:
    result = _plex_req("GET", path)
    assert isinstance(result, ET.Element), f"Réponse inattendue pour GET {path}"
    return result


def plex_put(path: str, params: dict | None = None) -> int:
    result = _plex_req("PUT", path, params)
    return result if isinstance(result, int) else 200


def get_audio_sections() -> list[dict]:
    root = plex_get("/library/sections")
    return [
        {"key": s.get("key"), "title": s.get("title", "?")}
        for s in root.findall(".//Directory")
        if s.get("type") == "artist"
    ]


def get_2star_tracks(section_key: str) -> list[dict]:
    """Retourne les tracks 2★ avec leur chemin de fichier."""
    plex_rating = STARS_TO_PLEX[2]
    root = plex_get(f"/library/sections/{section_key}/all?type=10&userRating={plex_rating}")
    tracks = []
    for t in root.findall(".//Track"):
        rk = t.get("ratingKey")
        if not rk:
            continue
        # Récupérer le chemin du fichier dans Media/Part
        file_path = None
        for part in t.findall(".//Part"):
            fp = part.get("file")
            if fp:
                file_path = fp
                break
        if not file_path:
            # Fallback : appel individuel sur la piste
            try:
                detail = plex_get(f"/library/metadata/{rk}")
                for part in detail.findall(".//Part"):
                    fp = part.get("file")
                    if fp:
                        file_path = fp
                        break
            except Exception:
                pass

        tracks.append({
            "ratingKey": rk,
            "title":     t.get("title", "?"),
            "artist":    t.get("grandparentTitle", "?"),
            "album":     t.get("parentTitle", "?"),
            "file":      file_path,
        })
    return tracks


def get_2star_albums(section_key: str) -> list[dict]:
    """Albums notés 2★ dans la section."""
    root = plex_get(f"/library/sections/{section_key}/all?type=9&userRating={STARS_TO_PLEX[2]}")
    return [
        {"ratingKey": d.get("ratingKey"), "title": d.get("title", "?"), "artist": d.get("parentTitle", "?")}
        for d in root.findall(".//Directory")
        if d.get("ratingKey")
    ]


def get_2star_artists(section_key: str) -> list[dict]:
    """Artistes notés 2★ dans la section."""
    root = plex_get(f"/library/sections/{section_key}/all?type=8&userRating={STARS_TO_PLEX[2]}")
    return [
        {"ratingKey": d.get("ratingKey"), "title": d.get("title", "?")}
        for d in root.findall(".//Directory")
        if d.get("ratingKey")
    ]


def get_children_tracks(parent_rk: str, all_leaves: bool = False) -> list[dict]:
    """Pistes enfants d'un album (/children) ou d'un artiste (/allLeaves)."""
    ep = f"/library/metadata/{parent_rk}/{'allLeaves' if all_leaves else 'children'}"
    try:
        root = plex_get(ep)
    except Exception:
        return []
    result = []
    for t in root.findall(".//Track"):
        rk = t.get("ratingKey")
        if not rk:
            continue
        file_path = next(
            (p.get("file") for p in t.findall(".//Part") if p.get("file")),
            None,
        )
        result.append({
            "ratingKey": rk,
            "title":     t.get("title", "?"),
            "artist":    t.get("grandparentTitle", "?"),
            "album":     t.get("parentTitle", "?"),
            "file":      file_path,
        })
    return result


def clear_rating(rating_key: str) -> bool:
    status = plex_put(
        "/:/rate",
        {"key": rating_key, "identifier": "com.plexapp.plugins.library", "rating": "0"},
    )
    return status in (200, 201, 204)


# ─── étapes du pipeline ──────────────────────────────────────────────────────

def _snapshot_sibling_names(file_path: str) -> set[str]:
    path = Path(file_path)
    parent = path.parent
    if not parent.is_dir():
        return set()
    return {child.name for child in parent.iterdir() if child.is_file() and child.suffix == path.suffix}


def _resolve_post_songrec_path(file_path: str, before_names: set[str]) -> str:
    path = Path(file_path)
    if path.exists():
        return str(path)

    parent = path.parent
    if not parent.is_dir():
        return file_path

    current = [child for child in parent.iterdir() if child.is_file() and child.suffix == path.suffix]
    new_files = [child for child in current if child.name not in before_names]
    if len(new_files) == 1:
        return str(new_files[0])
    if current:
        newest = max(current, key=lambda child: child.stat().st_mtime)
        return str(newest)
    return file_path

def run_songrec(file_path: str, dry_run: bool, timeout_s: int) -> tuple[bool, str]:
    """Lance songrec-rename si disponible, sinon fallback songrec + tags/rename simple."""
    songrec_rename_bin = shutil.which("songrec-rename")
    songrec_bin = shutil.which("songrec")
    before_names = _snapshot_sibling_names(file_path)

    if dry_run:
        if songrec_rename_bin:
            print(f"  [sim] songrec-rename -i '{file_path}'")
        elif songrec_bin:
            print(f"  [sim] songrec audio-file-to-recognized-song '{file_path}'")
            print("  [sim] fallback: mise à jour tags (artist/title) + renommage simple")
        else:
            print("  [sim] ❌ ni songrec-rename ni songrec n'est disponible")
        return True, file_path

    if songrec_rename_bin:
        cmd = [songrec_rename_bin, "-i", file_path]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
            if result.stdout:
                for line in result.stdout.strip().splitlines():
                    print(f"    {line}")
            if result.stderr:
                for line in result.stderr.strip().splitlines():
                    print(f"    ⚠️  {line}")
            if result.returncode == 0:
                return True, _resolve_post_songrec_path(file_path, before_names)
            if songrec_bin:
                print("  ⚠️  songrec-rename a échoué, tentative de fallback songrec…")
            else:
                return False, file_path
        except subprocess.TimeoutExpired:
            print(f"  ❌ Timeout songrec-rename (>{timeout_s}s)", file=sys.stderr)
            if songrec_bin:
                print("  ⚠️  tentative de fallback songrec après timeout…")
            else:
                return False, file_path

    if not songrec_bin:
        print(
            "  ❌ ni songrec-rename ni songrec n'est disponible dans le conteneur "
            "(vérifiez les montages SONGREC_* dans docker-compose/.env)",
            file=sys.stderr,
        )
        return False, file_path

    # Fallback: reconnaissance + tags/rename basique sans dépendre de songrec-rename.
    cmd = [songrec_bin, "audio-file-to-recognized-song", file_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        if result.returncode != 0:
            if result.stderr:
                for line in result.stderr.strip().splitlines():
                    print(f"    ⚠️  {line}")
            return False, file_path

        payload = (result.stdout or "").strip()
        if not payload:
            print("  ⚠️  songrec n'a renvoyé aucune sortie exploitable")
            return False, file_path

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            print("  ⚠️  sortie songrec non JSON, impossible de mettre à jour tags")
            return False, file_path

        track = data.get("track") or {}
        title = (track.get("title") or "").strip()
        artist = (track.get("subtitle") or "").strip()
        if not title and not artist:
            print("  ⚠️  songrec n'a pas identifié de métadonnées utiles")
            return False, file_path

        audio = MutagenFile(file_path, easy=True)
        if audio is None:
            print("  ⚠️  fichier reconnu mais format de tags non supporté par mutagen")
            return False, file_path

        if title:
            audio["title"] = [title]
        if artist:
            audio["artist"] = [artist]
        try:
            audio.save()
        except Exception as exc:
            print(f"  ⚠️  échec écriture tags mutagen: {exc}")
            return False, file_path
        print("    ✅ Tags mis à jour (fallback songrec)")

        # Renommage simple: <Artist> - <Title>.<ext>
        if title and artist:
            parent = Path(file_path).parent
            ext = Path(file_path).suffix
            safe = f"{artist} - {title}"
            safe = re.sub(r"[\\/:*?\"<>|]", "_", safe).strip()
            safe = re.sub(r"\s+", " ", safe)
            new_path = parent / f"{safe}{ext}"
            src_path = Path(file_path)
            if new_path != src_path and not new_path.exists():
                src_path.rename(new_path)
                print(f"    ✅ Renommé: {new_path.name}")
                file_path = str(new_path)

        return True, _resolve_post_songrec_path(file_path, before_names)
    except subprocess.TimeoutExpired:
        print(f"  ❌ Timeout songrec (>{timeout_s}s)", file=sys.stderr)
        return False, file_path
    except Exception as exc:
        print(f"  ⚠️  erreur songrec fallback inattendue: {exc}", file=sys.stderr)
        return False, file_path


def _resolve_beet_cmd() -> list[str] | None:
    beet_bin = shutil.which("beet")
    if beet_bin:
        return [beet_bin]

    python_bin = shutil.which("python3") or sys.executable
    if python_bin:
        probe = subprocess.run(
            [python_bin, "-m", "beets", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if probe.returncode == 0:
            return [python_bin, "-m", "beets"]
    return None


def run_beet(file_path: str, dry_run: bool, beet_cmd: list[str] | None = None, timeout_s: int = 300) -> bool:
    """Lance `beet import -q <fichier>`. Retourne True si succès."""
    if dry_run:
        print(f"  [sim] beet import -q '{file_path}'")
        return True
    resolved_beet = beet_cmd or _resolve_beet_cmd()
    if not resolved_beet:
        print("  ❌ beet introuvable — installez-le avec : pip install beets",
              file=sys.stderr)
        return False
    # config.yaml (chargé par défaut via BEETSDIR) pointe son log vers ~/beets/import.log,
    # un chemin inexistant/non-persistant dans le conteneur. config-docker.yaml surcharge
    # directory/library/log avec des chemins valides sous /beets-config — à charger en plus.
    docker_config = os.environ.get(
        'BEETS_DOCKER_CONFIG',
        '/beets-config/config-docker.yaml' if os.path.isdir('/beets-config') else ''
    )
    extra_args = ['--config', docker_config] if docker_config and os.path.isfile(docker_config) else []
    # -q = quiet (non-interactif), utilise les réglages du config.yaml
    cmd = [*resolved_beet, *extra_args, "import", "-q", file_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                print(f"    {line}")
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                print(f"    ⚠️  {line}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  ❌ Timeout beet import (>{timeout_s}s)", file=sys.stderr)
        return False


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline 2★ : songrec-rename -i → beet import → effacement rating Plex"
    )
    parser.add_argument("--dry-run",  action="store_true",
                        help="Simulation : affiche les actions sans les exécuter")
    parser.add_argument("--no-beet",  action="store_true",
                        help="Sauter l'étape beet import")
    parser.add_argument("--no-clear", action="store_true",
                        help="Ne pas effacer le rating 2★ après traitement")
    parser.add_argument("--songrec-timeout", type=int, default=int(os.environ.get("SONGREC_TIMEOUT", "120")),
                        help="Timeout en secondes pour songrec-rename/songrec")
    parser.add_argument("--beet-timeout", type=int, default=int(os.environ.get("BEET_TIMEOUT", "300")),
                        help="Timeout en secondes pour beet import")
    parser.add_argument("--summary-json", default=os.environ.get("PIPELINE_2STAR_SUMMARY_JSON", ""),
                        help="Chemin optionnel pour écrire un résumé JSON des échecs et résultats")
    args = parser.parse_args()

    args.songrec_timeout = max(1, int(args.songrec_timeout))
    args.beet_timeout = max(1, int(args.beet_timeout))

    if not PLEX_TOKEN:
        print("❌ PLEX_TOKEN non défini (variable d'environnement manquante)", file=sys.stderr)
        sys.exit(1)

    beet_cmd = None if args.no_beet else _resolve_beet_cmd()
    if not args.no_beet and not args.dry_run and not beet_cmd:
        print("⚠️  beet introuvable au démarrage — étape beet désactivée pour tout ce run", file=sys.stderr)
        print("   Installez-le avec : pip install beets", file=sys.stderr)
        args.no_beet = True

    mode = "SIMULATION" if args.dry_run else "RÉEL"
    print(f"🎵 Pipeline 2★ Plex [{mode}]")
    print(f"   URL Plex : {PLEX_URL}")
    steps = ["1. songrec-rename -i", "2. beet import"]
    if not args.no_beet:
        pass
    else:
        steps = ["1. songrec-rename -i"]
    if not args.no_clear:
        steps.append("→ effacement rating 2★")
    print(f"   Étapes   : {' → '.join(steps)}")
    print("=" * 60)

    sections = get_audio_sections()
    if not sections:
        print("❌ Aucune section audio trouvée dans Plex.")
        sys.exit(1)

    all_tracks: list[dict] = []
    for section in sections:
        tracks = get_2star_tracks(section["key"])
        if tracks:
            print(f"\n📚 Section «{section['title']}» : {len(tracks)} track(s) 2★")
            all_tracks.extend(tracks)

    # ── albums 2★ → expansion en pistes ──────────────────────────────────────
    parent_items: list[dict] = []   # [{ratingKey, label, track_rks}] pour nettoyage final
    seen_rks = {t["ratingKey"] for t in all_tracks}

    for section in sections:
        albums = get_2star_albums(section["key"])
        if albums:
            print(f"\n📀 Section «{section['title']}» : {len(albums)} album(s) 2★")
            for album in albums:
                label = f"{album['artist']} — {album['title']}"
                print(f"   💿 {label}")
                child_tracks = get_children_tracks(album["ratingKey"])
                album_rks = {t["ratingKey"] for t in child_tracks}
                new = [t for t in child_tracks if t["ratingKey"] not in seen_rks]
                for t in new:
                    all_tracks.append(t)
                    seen_rks.add(t["ratingKey"])
                if new:
                    print(f"     → {len(new)} piste(s) ajoutée(s) au traitement")
                parent_items.append({"ratingKey": album["ratingKey"], "label": label, "track_rks": album_rks})

    # ── artistes 2★ → expansion en pistes ────────────────────────────────────
    for section in sections:
        artists = get_2star_artists(section["key"])
        if artists:
            print(f"\n👤 Section «{section['title']}» : {len(artists)} artiste(s) 2★")
            for artist in artists:
                label = artist["title"]
                print(f"   🎤 {label}")
                child_tracks = get_children_tracks(artist["ratingKey"], all_leaves=True)
                artist_rks = {t["ratingKey"] for t in child_tracks}
                new = [t for t in child_tracks if t["ratingKey"] not in seen_rks]
                for t in new:
                    all_tracks.append(t)
                    seen_rks.add(t["ratingKey"])
                if new:
                    print(f"     → {len(new)} piste(s) ajoutée(s) au traitement")
                parent_items.append({"ratingKey": artist["ratingKey"], "label": label, "track_rks": artist_rks})

    if not all_tracks:
        print("\n✅ Aucun track/album/artiste 2★ trouvé — rien à faire.")
        return

    print(f"\n🔍 Total : {len(all_tracks)} track(s) 2★ à traiter\n")

    ok = 0
    errors = 0
    skipped = 0
    failures: list[dict[str, Any]] = []
    ok_rks: set[str] = set()
    failed_rks: set[str] = set()

    def add_failure(track: dict[str, Any], stage: str, reason: str, file_path: str | None) -> None:
        failures.append({
            "rating_key": track.get("ratingKey", ""),
            "artist": track.get("artist", ""),
            "title": track.get("title", ""),
            "file": file_path or "",
            "stage": stage,
            "reason": reason,
        })

    for i, track in enumerate(all_tracks, 1):
        title  = track["title"]
        artist = track["artist"]
        fpath  = track["file"]
        rk     = track["ratingKey"]

        print(f"[{i}/{len(all_tracks)}] {artist} — {title}")
        if fpath:
            print(f"  📄 {fpath}")
        else:
            print("  ⚠️  Chemin de fichier introuvable, passage en skip")
            skipped += 1
            add_failure(track, "resolve", "missing-file-path", fpath)
            failed_rks.add(rk)
            continue

        resolved, source = _resolve_file_path(fpath)
        if resolved != fpath:
            print(f"  🔁 Chemin résolu ({source})")
            print(f"     → {resolved}")
        fpath = resolved

        if not args.dry_run and not os.path.exists(fpath):
            print(f"  ⚠️  Fichier absent sur disque : {fpath}")
            skipped += 1
            add_failure(track, "resolve", "file-not-found", fpath)
            failed_rks.add(rk)
            continue

        # Étape 1 : songrec-rename -i
        print("  🎵 songrec-rename -i …")
        songrec_ok, processed_path = run_songrec(fpath, args.dry_run, args.songrec_timeout)
        if not songrec_ok:
            print("  ❌ songrec-rename a échoué — track ignoré pour beet/clear")
            errors += 1
            add_failure(track, "songrec", "songrec-failed", fpath)
            failed_rks.add(rk)
            continue
        if processed_path != fpath:
            print(f"  📦 Fichier traité: {processed_path}")
            fpath = processed_path

        # Étape 2 : beet import
        if not args.no_beet:
            print("  📚 beet import -q …")
            beet_ok = run_beet(fpath, args.dry_run, beet_cmd=beet_cmd, timeout_s=args.beet_timeout)
            if not beet_ok:
                print("  ⚠️  beet import a échoué — rating NON effacé (traitement manuel requis)")
                errors += 1
                add_failure(track, "beet", "beet-import-failed", fpath)
                failed_rks.add(rk)
                continue

        # Étape 3 : effacement du rating 2★
        if not args.no_clear:
            if args.dry_run:
                print(f"  [sim] API Plex PUT /:/rate?key={rk}&rating=0")
            else:
                cleared = clear_rating(rk)
                if cleared:
                    print("  🧹 Rating 2★ effacé dans Plex")
                else:
                    print("  ⚠️  Impossible d'effacer le rating via API")
                    errors += 1
                    add_failure(track, "clear", "clear-rating-failed", fpath)
                    failed_rks.add(rk)
                    continue

        print("  ✅ OK")
        ok += 1
        ok_rks.add(rk)

    # ── nettoyage ratings albums/artistes ────────────────────────────────────
    if parent_items and not args.no_clear:
        print("\n🧹 Ratings albums/artistes 2★")
        for parent in parent_items:
            n_failed = len(parent["track_rks"] & failed_rks)
            if n_failed == 0:
                if args.dry_run:
                    print(f"  [sim] effacement rating 2★ : {parent['label']}")
                else:
                    cleared = clear_rating(parent["ratingKey"])
                    icon = "✅" if cleared else "⚠️ "
                    print(f"  {icon} {parent['label']}")
            else:
                print(f"  ⏭️  {parent['label']} — {n_failed} piste(s) en échec, rating conservé")

    print()
    print("=" * 60)
    print(f"📊 Résultats du pipeline 2★ [{mode}]")
    print(f"   ✅ Traités avec succès : {ok}")
    print(f"   ❌ Erreurs             : {errors}")
    print(f"   ⏭️  Ignorés (pas de fichier) : {skipped}")
    print(f"   ⏱️  Timeout songrec    : {args.songrec_timeout}s")
    print(f"   ⏱️  Timeout beet       : {args.beet_timeout}s")

    if failures:
        print("\n🧾 Résumé des échecs")
        for failure in failures:
            print(
                "   - {artist} — {title} [{stage}] {reason}".format(
                    artist=failure["artist"] or "?",
                    title=failure["title"] or "?",
                    stage=failure["stage"],
                    reason=failure["reason"],
                )
            )

    if args.summary_json:
        summary_path = Path(args.summary_json)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_payload = {
            "mode": mode,
            "plex_url": PLEX_URL,
            "songrec_timeout": args.songrec_timeout,
            "beet_timeout": args.beet_timeout,
            "ok": ok,
            "errors": errors,
            "skipped": skipped,
            "failures": failures,
        }
        summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n💾 Résumé JSON écrit : {summary_path}")


if __name__ == "__main__":
    main()
