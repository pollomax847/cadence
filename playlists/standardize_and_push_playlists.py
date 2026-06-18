#!/usr/bin/env python3
"""Standardize playlist names in `playlists/manual_playlists.json` and push to Plex.

Behaviors:
 - Backup manual_playlists.json
 - Normalize names: strip "(NN titres)", remove [fusion], trim whitespace
 - Optionally add emoji prefixes based on keywords
 - Write updated file and call `apply_manual_playlists.py` to push changes when `PLEX_TOKEN` present
"""
from __future__ import annotations
import json
import re
import os
from pathlib import Path

EMOJI_MAP = [
    (r"run|running|workout", "🏃"),
    (r"chill|relax|lounge|ambient|balearic", "🧘"),
    (r"party|fête|fiesta|club", "🎉"),
    (r"jazz|blues", "🎷"),
    (r"rock|punk|garage", "🎸"),
    (r"rap|hip-?hop", "🎤"),
    (r"yoga|meditation|méditation", "🧘‍♀️"),
    (r"summer|été|sun|tropical|mojito", "☀️"),
]


def normalize_name(name: str) -> str:
    s = name
    # remove parenthetical counts like (123 titres)
    s = re.sub(r"\([^)]*titres[^)]*\)", "", s, flags=re.I)
    # remove [fusion] and other bracket tags
    s = re.sub(r"\[.*?\]", "", s)
    s = s.strip()
    # compact whitespace
    s = re.sub(r"\s{2,}", " ", s)
    # add emoji if matching
    lower = s.lower()
    for pat, emoji in EMOJI_MAP:
        if re.search(pat, lower):
            # avoid double emoji
            if not s.startswith(emoji):
                s = f"{emoji} {s}"
            break
    return s


def main() -> int:
    manual = Path(__file__).parent / "manual_playlists.json"
    if not manual.exists():
        print("manual_playlists.json not found")
        return 1
    bak = manual.with_name(manual.name + ".backup.auto_std")
    bak.write_text(manual.read_text(encoding='utf-8'), encoding='utf-8')
    data = json.loads(manual.read_text(encoding='utf-8'))
    updated = []
    for pl in data.get('playlists', []):
        name = pl.get('name','')
        new = normalize_name(name)
        pl['name'] = new
        updated.append(new)
    manual.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Updated {manual} — {len(updated)} playlists renamed; backup -> {bak}")

    # push via apply script if token available
    if os.environ.get('PLEX_TOKEN'):
        print('PLEX_TOKEN found — pushing to Plex')
        os.system('.venv/bin/python3 playlists/apply_manual_playlists.py')
    else:
        print('PLEX_TOKEN not set — skipping Plex push')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
