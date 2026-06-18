#!/usr/bin/env python3
"""Génère des posters en mode démo en utilisant la rotation de styles (random).

Sauvegarde les PNG dans `playlists/poster_samples_random/`.
"""
from pathlib import Path
import sys
import json
import logging
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import auto_playlists_plexamp as app  # noqa: E402


SAMPLES = [
    (f"Demo Playlist {i} - spotify charts", 30 + i) for i in range(1, 13)
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
    out = HERE / "poster_samples_random"
    out.mkdir(exist_ok=True)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    gen = app.PlexAmpAutoPlaylist.__new__(app.PlexAmpAutoPlaylist)
    gen.logger = logging.getLogger("posters")
    # Load random style config if present
    cfg = HERE / "poster_style.random.json"
    if cfg.exists():
        try:
            raw = json.loads(cfg.read_text(encoding="utf-8"))
            gen.poster_style = gen._normalize_style(raw)
        except Exception:
            gen.poster_style = gen._normalize_style({})
    else:
        gen.poster_style = gen._normalize_style({})

    gen.randomize_poster_styles = True

    fake_xml = build_fake_xml(SAMPLES)

    def fake_urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/playlists?" in url and "metadata" not in url:
            return DummyResp(fake_xml)
        # Simulate poster upload endpoint
        import re
        m = re.search(r"/metadata/(\d+)/posters", url)
        if m:
            rk = int(m.group(1))
            # store to file path
            data = req.data
            out_path = out / f"{rk:02d}_demo.png"
            out_path.write_bytes(data)
        return DummyResp(b"")

    from unittest.mock import patch
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        gen.generate_playlist_posters()

    print(f"Generated posters in {out}")


if __name__ == '__main__':
    main()
