#!/usr/bin/env python3
"""Scaffold for Spotify sync using spotipy.

This file provides helper functions to fetch Spotify playlist tracks and audio features.
It does NOT run without valid Spotify credentials. Use as starting point to implement sync.
"""
from __future__ import annotations
import os
from typing import List

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except Exception:
    spotipy = None


def get_spotify_client():
    if spotipy is None:
        raise RuntimeError('spotipy not installed (pip install spotipy)')
    client = spotipy.Spotify(auth_manager=SpotifyOAuth(scope='playlist-read-private'))
    return client


def fetch_playlist_features(sp_client, playlist_id: str):
    """Return list of (track_name, artists, tempo, energy) tuples for playlist tracks."""
    features = []
    results = sp_client.playlist_items(playlist_id)
    track_ids = [item['track']['id'] for item in results['items'] if item.get('track')]
    if not track_ids:
        return features
    for i in range(0, len(track_ids), 50):
        batch = track_ids[i:i+50]
        af = sp_client.audio_features(batch)
        for j, tid in enumerate(batch):
            track = results['items'][i+j]['track']
            feat = af[j] if af and j < len(af) else None
            tempo = feat['tempo'] if feat else None
            energy = feat['energy'] if feat else None
            features.append((track['name'], [a['name'] for a in track['artists']], tempo, energy))
    return features


if __name__ == '__main__':
    print('This is a scaffold. Provide Spotify credentials and call functions from a script.')
