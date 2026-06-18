#!/usr/bin/env python3
"""Diagnose audio files for decoding issues (zero-size, ffprobe failures).

Usage: diagnose_audio_decode.py [root_dir] [--ext ext1,ext2]
Writes JSON report to /tmp/audio_decode_report.json and prints a short summary.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import subprocess


def check_file(p: Path) -> dict:
    rec = {"path": str(p), "size": p.stat().st_size}
    if rec["size"] == 0:
        rec["status"] = "zero_size"
        return rec
    # run ffprobe to probe format
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-print_format",
        "json",
        str(p),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0 or not proc.stdout.strip():
            rec["status"] = "ffprobe_failed"
            rec["ffprobe_err"] = (proc.stderr or proc.stdout).strip()
        else:
            rec["status"] = "ok"
    except Exception as e:
        rec["status"] = "ffprobe_exception"
        rec["error"] = str(e)
    return rec


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/mnt/MyBook/itunes")
    exts = None
    if len(sys.argv) > 2 and sys.argv[2].startswith("--ext"):
        exts = [e.strip().lower() for e in sys.argv[2].split("=")[-1].split(",")]

    files = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if exts:
            if p.suffix.lower().lstrip('.') not in exts:
                continue
        elif p.suffix.lower() not in ('.mp3', '.flac', '.m4a', '.ogg'):
            continue
        files.append(p)

    report = []
    for i, p in enumerate(files, 1):
        rec = check_file(p)
        report.append(rec)
    out = Path('/tmp/audio_decode_report.json')
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    # summary
    from collections import Counter
    cnt = Counter(r['status'] for r in report)
    print(f"Scanned {len(report)} files — summary:")
    for k, v in cnt.items():
        print(f"  {k}: {v}")
    print(f"Report written to: {out}")


if __name__ == '__main__':
    raise SystemExit(main())
