#!/usr/bin/env python3
"""
Force un scan + vidage corbeille sur toutes les bibliothèques audio Plex.
Lit PLEX_URL et PLEX_TOKEN depuis l'environnement ou un .env parent.
"""
import os
import sys
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen
from pathlib import Path
import subprocess


def get_plex_token_and_url():
    # Utilise detect_plex_token.py si dispo
    script = Path(__file__).parent / "detect_plex_token.py"
    if script.exists():
        try:
            out = subprocess.check_output([sys.executable, str(script), "--json"])
            data = json.loads(out)
            return data.get("token"), data.get("url", "http://localhost:32400")
        except Exception as e:
            print(f"[PLEX] Erreur detect_plex_token.py: {e}")
    # Fallback env/.env
    def load_env_var(name: str) -> str | None:
        val = os.environ.get(name)
        if val:
            return val
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"')
        return None
    url = load_env_var("PLEX_URL") or "http://localhost:32400"
    token = load_env_var("PLEX_TOKEN")
    return token, url

PLEX_TOKEN, PLEX_URL = get_plex_token_and_url()
if not PLEX_TOKEN:
    print("[PLEX] PLEX_TOKEN introuvable (detect_plex_token.py/env/.env)")
    sys.exit(1)
base = (PLEX_URL or "http://localhost:32400").rstrip("/")

def request(method: str, path: str):
    req = Request(f"{base}{path}", method=method)
    req.add_header("X-Plex-Token", PLEX_TOKEN)
    return urlopen(req, timeout=30)

try:
    with request("GET", "/library/sections") as resp:
        root = ET.fromstring(resp.read())
except Exception as e:
    print(f"[PLEX] Erreur accès API: {e}")
    sys.exit(2)

sections = []
for node in root.findall("Directory"):
    key = node.attrib.get("key")
    title = node.attrib.get("title", "Unknown")
    section_type = node.attrib.get("type", "")
    if key:
        sections.append((key, title, section_type))

audio_sections = [(k, t) for (k, t, st) in sections if st == "artist"]
if not audio_sections:
    print("[PLEX] Aucune bibliothèque audio (type=artist) trouvée")
    sys.exit(0)

for key, title in audio_sections:
    print(f"[PLEX] Scan: {title} (section {key}) ...", end=" ")
    try:
        request("GET", f"/library/sections/{key}/refresh").read()
        print("OK")
    except Exception as e:
        print(f"ERREUR: {e}")

for key, title in audio_sections:
    print(f"[PLEX] Vidage corbeille: {title} (section {key}) ...", end=" ")
    done = False
    for method in ("PUT", "POST", "GET"):
        try:
            request(method, f"/library/sections/{key}/emptyTrash").read()
            done = True
            break
        except Exception:
            continue
    print("OK" if done else "ECHEC")

print("[PLEX] Scan + vidage corbeille terminé.")
