#!/usr/bin/env python3
"""Sample script to compute tempo (BPM) for a few local audio files using librosa.

This script is advisory: install dependencies with:
  pip install librosa soundfile numpy

It does not automatically fetch files from Plex; point it at local audio files.
"""
from __future__ import annotations
import sys
from pathlib import Path
import subprocess
import tempfile
import os

try:
    import librosa
except Exception as e:
    print('librosa not installed or failed to import:', e)
    raise


def _ffmpeg_to_wav(src: str, dest_wav: str, duration: float = 30.0) -> bool:
    """Use ffmpeg to transcode `src` to `dest_wav` (mono, 22050 Hz) for `duration` seconds."""
    cmd = [
        'ffmpeg', '-v', 'error', '-y', '-i', src,
        '-t', str(duration), '-ar', '22050', '-ac', '1', dest_wav,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except Exception:
        return False


def estimate_bpm(path: Path) -> float | None:
    """Estimate BPM for `path`. Tries librosa directly, falls back to ffmpeg->wav then librosa."""
    try:
        y, sr = librosa.load(str(path), duration=30.0)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        return float(tempo)
    except Exception:
        # Fallback: transcode to temporary WAV via ffmpeg and try again
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
            tmp_path = tf.name
        try:
            ok = _ffmpeg_to_wav(str(path), tmp_path, duration=30.0)
            if not ok:
                return None
            y, sr = librosa.load(tmp_path, sr=None)
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            return float(tempo)
        except Exception:
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def main():
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print('Usage: compute_bpm_sample.py file1.mp3 file2.flac ...')
        return 1
    for p in paths:
        try:
            bpm = estimate_bpm(p)
            if bpm is None:
                print(f'{p.name}: error (could not decode / estimate BPM)')
            else:
                print(f'{p.name}: {bpm:.1f} BPM')
        except Exception as e:
            print(f'{p.name}: error {e}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
