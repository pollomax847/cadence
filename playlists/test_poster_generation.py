#!/usr/bin/env python3
"""Génère des posters d'aperçu en utilisant la vraie fonction du script principal.

Crée un objet PlexAmpAutoPlaylist factice et appelle le rendu sur une liste
de titres représentatifs. Sauvegarde les PNG dans ./poster_samples/.
"""

import sys
import logging
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import auto_playlists_plexamp as app  # noqa: E402


SAMPLES = [
    ("Stars 80 (148 titres)", 320),
    ("Chill & Relax (54 titres)", 54),
    ("Running & Workout (87 titres)", 87),
    ("Top 50 — Été 2026 (50 titres)", 50),
    ("Mélancolie (32 titres)", 32),
    ("Soirée & Fête (110 titres)", 110),
    ("Jazz & Blues du Dimanche (76 titres)", 76),
    ("Rap FR 90s (64 titres)", 64),
    ("Synthwave Nights (41 titres)", 41),
    ("Focus & Concentration (95 titres)", 95),
    ("Discovery — Nouveaux artistes", 28),
    ("Épique & Cinématique", 22),
]


def build_fake_xml(samples):
    from xml.sax.saxutils import escape
    items = "".join(
        f'<Playlist ratingKey="{i}" title="{escape(t, {chr(34): "&quot;"})}" leafCount="{c}"/>'
        for i, (t, c) in enumerate(samples, start=1)
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><MediaContainer>{items}</MediaContainer>'.encode("utf-8")


class DummyResp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def main():
    out_dir = HERE / "poster_samples"
    out_dir.mkdir(exist_ok=True)
    for f in out_dir.glob("*.png"):
        f.unlink()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    inst = app.PlexAmpAutoPlaylist.__new__(app.PlexAmpAutoPlaylist)
    inst.logger = logging.getLogger("posters")
    inst.poster_style = inst._normalize_style({
        "size": 1000,
        "font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "emoji_font_path": "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
        "strip_auto_prefix": True,
        "strip_count_suffix": True,
        "show_emoji_badge": True,
        "default_colors": [[28, 32, 64], [120, 80, 200]],
        "themes": [
            {"keywords": ["stars 80", "80s"], "match": "any", "emoji": "⭐",
             "colors": [[210, 60, 180], [70, 80, 220]]},
            {"keywords": ["running", "workout", "sport"], "match": "any", "emoji": "🏃",
             "colors": [[255, 90, 40], [255, 200, 70]]},
            {"keywords": ["chill", "relax", "lofi"], "match": "any", "emoji": "🧘",
             "colors": [[40, 130, 170], [120, 200, 220]]},
            {"keywords": ["fête", "party", "club", "soirée"], "match": "any", "emoji": "🎉",
             "colors": [[230, 50, 120], [90, 60, 200]]},
            {"keywords": ["mélancolie", "sad"], "match": "any", "emoji": "🌧️",
             "colors": [[50, 70, 110], [120, 130, 170]]},
            {"keywords": ["jazz", "blues"], "match": "any", "emoji": "🎷",
             "colors": [[60, 40, 90], [200, 150, 70]]},
            {"keywords": ["rap", "hip-hop"], "match": "any", "emoji": "🎤",
             "colors": [[35, 35, 50], [200, 60, 70]]},
            {"keywords": ["synthwave", "neon", "synth"], "match": "any", "emoji": "🌆",
             "colors": [[255, 60, 170], [60, 80, 220]]},
            {"keywords": ["focus", "concentration", "study"], "match": "any", "emoji": "🎯",
             "colors": [[20, 60, 90], [60, 160, 180]]},
            {"keywords": ["discovery", "discover", "new"], "match": "any", "emoji": "✨",
             "colors": [[40, 80, 60], [180, 220, 120]]},
            {"keywords": ["épique", "cinematic", "cinématique"], "match": "any", "emoji": "🎬",
             "colors": [[40, 25, 25], [200, 130, 60]]},
            {"keywords": ["top", "best"], "match": "any", "emoji": "🏆",
             "colors": [[80, 30, 30], [240, 180, 60]]},
        ],
    })
    inst.POSTER_THEMES = []

    fake_xml = build_fake_xml(SAMPLES)
    uploaded = {}

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/playlists?" in url and "metadata" not in url:
            return DummyResp(fake_xml)
        import re
        m = re.search(r"/metadata/(\d+)/posters", url)
        if m:
            rk = int(m.group(1))
            uploaded[rk] = req.data
        return DummyResp(b"")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        inst.generate_playlist_posters()

    for i, (title, _) in enumerate(SAMPLES, start=1):
        data = uploaded.get(i)
        if not data:
            print(f"  ⚠️ Pas de poster pour: {title}")
            continue
        safe = "".join(c if c.isalnum() else "_" for c in title)[:60]
        out_path = out_dir / f"{i:02d}_{safe}.png"
        out_path.write_bytes(data)
        print(f"  ✅ {out_path.name}  ({len(data)//1024} KB)")

    print(f"\n📁 Dossier: {out_dir}")


if __name__ == "__main__":
    main()
