#!/usr/bin/env python3
"""Scan audio files under a root, estimate BPM for decodable files,
and write results to CSV and failures to JSON.

Outputs:
  /tmp/bpm_results.csv  (columns: path,bpm)
  /tmp/bpm_failures.json (list of {path,reason})

Usage: run_bpm_index.py [--root DIR] [--max N]
"""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import csv
import time
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).parent))
import compute_bpm_sample
estimate_bpm = compute_bpm_sample.estimate_bpm


def iter_audio_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in ('.mp3', '.flac', '.m4a', '.ogg'):
            yield p


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='/mnt/MyBook/itunes')
    parser.add_argument('--max', type=int, default=0, help='Max files to process (0 = all)')
    args = parser.parse_args()

    root = Path(args.root)
    out_csv = Path('/tmp/bpm_results.csv')
    out_fail = Path('/tmp/bpm_failures.json')

    rows = []
    fails = []
    start = time.time()
    n = 0
    for p in iter_audio_files(root):
        if args.max and n >= args.max:
            break
        n += 1
        try:
            size = p.stat().st_size
        except Exception as e:
            fails.append({'path': str(p), 'reason': f'stat_error: {e}'})
            continue
        if size == 0:
            fails.append({'path': str(p), 'reason': 'zero_size'})
            continue
        bpm = None
        try:
            bpm = estimate_bpm(p)
        except Exception as e:
            fails.append({'path': str(p), 'reason': f'exception: {e}'})
            continue
        if bpm is None:
            fails.append({'path': str(p), 'reason': 'decode_failed'})
            continue
        rows.append({'path': str(p), 'bpm': round(float(bpm), 1)})
        if n % 50 == 0:
            print(f'Processed {n} files...')

    # write outputs
    with out_csv.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['path', 'bpm'])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    out_fail.write_text(json.dumps(fails, ensure_ascii=False, indent=2), encoding='utf-8')

    elapsed = time.time() - start
    print(f'Done. Processed {n} files in {elapsed:.1f}s — successes: {len(rows)}, failures: {len(fails)}')
    print(f'CSV: {out_csv}  FAILS: {out_fail}')


if __name__ == '__main__':
    raise SystemExit(main())
