from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import abort, jsonify, render_template, request


def register_playlists_routes(app, ctx: dict[str, Any]) -> None:
    runner = ctx["runner"]
    project_root = ctx["project_root"]
    poster_style_glob = ctx["poster_style_glob"]
    default_plex_url = ctx["default_plex_url"]
    tool_snapshot = ctx["tool_snapshot"]
    summarize_run = ctx["summarize_run"]
    pld = ctx["pld"]
    pld_err = ctx["pld_err"]

    def _require_pld() -> None:
        if pld is None:
            abort(503, description=f"playlist_detector indisponible: {pld_err}")

    @app.route("/playlists")
    def playlists_page():
        def _has_playlists(p: str) -> bool:
            if not p:
                return False
            d = Path(p)
            if not d.is_dir():
                return False
            return any(d.rglob("*.m3u")) or any(d.rglob("*.m3u8")) or any(d.rglob("*.pls"))

        candidates = [
            os.environ.get("PLAYLISTS_DIR", ""),
            "/music/Playlists",
            "/music/playlists",
            "/playlists",
        ]
        playlists_default = next((p for p in candidates if _has_playlists(p)), "/playlists")

        # Styles affichés dans le dropdown — sélection curatée (pas les 37)
        CURATED_STYLES = [
            "random", "default", "spotify", "deezer", "elegant", "minimal",
            "vinyl_warm", "midnight_blue", "neon_city", "vaporwave", "synthwave",
            "warm_retro", "noir_cinema",
        ]
        all_style_files = {
            p.name.replace("poster_style.", "").replace(".json", ""): p
            for p in sorted((project_root / "playlists").glob(poster_style_glob))
        }
        poster_style_files = [
            all_style_files[name] for name in CURATED_STYLES if name in all_style_files
        ]
        # Par défaut : random
        current_poster_style = os.environ.get(
            "PLEX_POSTER_STYLE_CONFIG",
            str(all_style_files.get("random", ""))
        ).strip()
        defaults = {
            "music": os.environ.get("AUDIO_LIBRARY", "/music"),
            "playlists": playlists_default,
            "plex_url": os.environ.get("PLEX_URL", default_plex_url()),
            "plex_token": "",
            "lastfm_user": os.environ.get("LASTFM_USER", ""),
            "lastfm_period": os.environ.get("LASTFM_PERIOD", "overall"),
            "lastfm_max_pages": int(os.environ.get("LASTFM_MAX_PAGES", "5") or 5),
            "lastfm_api_key_set": bool(os.environ.get("LASTFM_API_KEY", "").strip()),
            "poster_style": current_poster_style,
            "poster_styles": [
                {"name": p.name.replace("poster_style.", "").replace(".json", ""), "path": str(p)}
                for p in poster_style_files
            ],
        }
        status = {
            "deleted": str(request.args.get("deleted") or "").strip(),
            "error": str(request.args.get("delete_error") or "").strip(),
        }
        pipeline_tools = [
            tool_snapshot("beet"),
            tool_snapshot("songrec"),
            tool_snapshot("songrec-rename"),
        ]
        pipeline_job_keys = {
            "ratings_2star_pipeline",
            "ratings_2star_pipeline_sim",
            "ratings_2star_songrec_only",
            "ratings_clear_2stars",
        }
        pipeline_runs = [r for r in runner.list_runs() if r.job_key in pipeline_job_keys]
        active_pipeline_run = next((r for r in pipeline_runs if r.status == "running"), None)
        latest_pipeline_run = active_pipeline_run or (pipeline_runs[0] if pipeline_runs else None)
        pipeline_status = summarize_run(latest_pipeline_run) if latest_pipeline_run else None
        return render_template(
            "playlists.html",
            defaults=defaults,
            delete_status=status,
            pipeline_tools=pipeline_tools,
            pipeline_status=pipeline_status,
        )

    @app.post("/api/playlists/scan")
    def api_pl_scan():
        _require_pld()
        data = request.get_json(force=True)
        root = Path(data.get("path", "/music")).expanduser()
        if not root.exists():
            return jsonify(error=f"introuvable: {root}"), 400
        files = pld.discover_playlists(
            root,
            recursive=data.get("recursive", True),
            max_depth=int(data.get("max_depth", 0)),
            follow_symlinks=bool(data.get("follow_symlinks", False)),
        )
        from collections import Counter

        by_ext = Counter(f.suffix.lower() for f in files)
        return jsonify(
            {
                "count": len(files),
                "by_ext": by_ext.most_common(),
                "files": [str(f) for f in files[:500]],
                "truncated": len(files) > 500,
            }
        )

    @app.post("/api/playlists/analyze")
    def api_pl_analyze():
        _require_pld()
        data = request.get_json(force=True)
        raw = (data.get("path") or "").strip()
        if not raw:
            return jsonify(error="Chemin vide. Saisissez le chemin complet vers un fichier .m3u/.pls."), 400
        path = Path(raw).expanduser()
        if not path.is_file():
            return jsonify(error=f"Fichier introuvable : {path}"), 400
        pl = pld.parse_playlist(path)
        entries = []
        for e in pl.entries:
            p = pld.resolve_entry_path(e.path_or_uri, pl.path)
            entries.append(
                {
                    "path": e.path_or_uri,
                    "resolved": str(p) if p else None,
                    "exists": bool(p and p.exists()),
                    "artist": e.artist,
                    "title": e.title,
                    "duration": e.duration,
                }
            )
        return jsonify(
            {
                "name": pl.name,
                "format": pl.format,
                "encoding": pl.encoding,
                "track_count": pl.track_count,
                "entries": entries,
            }
        )

    @app.post("/api/playlists/match")
    def api_pl_match():
        _require_pld()
        data = request.get_json(force=True)
        raw_path = (data.get("path") or "").strip()
        raw_lib = (data.get("library") or "").strip()
        if not raw_path:
            return jsonify(error="Chemin de playlist vide. Saisissez le chemin complet vers un fichier .m3u/.pls."), 400
        if not raw_lib:
            return jsonify(error="Chemin de bibliothèque vide. Saisissez le dossier racine de votre musique."), 400
        path = Path(raw_path).expanduser()
        library = Path(raw_lib).expanduser()
        if not path.is_file():
            return jsonify(error=f"Fichier playlist introuvable : {path}"), 400
        if not library.is_dir():
            return jsonify(error=f"Dossier bibliothèque introuvable : {library}"), 400

        index = pld.MusicIndex.build(library, read_tags=bool(data.get("tags", False)))
        pl = pld.parse_playlist(path)
        mappings = [tuple(m.split("=", 1)) for m in data.get("map", []) if "=" in m]

        from collections import Counter

        by_strategy: Counter = Counter()
        matched, unmatched = [], []
        for e in pl.entries:
            r = index.match(e, playlist_path=pl.path, path_mappings=mappings)
            if r:
                by_strategy[r.strategy] += 1
                matched.append(
                    {
                        "entry": e.path_or_uri,
                        "title": e.title,
                        "artist": e.artist,
                        "matched": str(r.track.path),
                        "strategy": r.strategy,
                        "confidence": r.confidence,
                    }
                )
            else:
                unmatched.append({"entry": e.path_or_uri, "title": e.title, "artist": e.artist})

        return jsonify(
            {
                "total": pl.track_count,
                "matched": len(matched),
                "unmatched": len(unmatched),
                "rate": (100 * len(matched) / pl.track_count) if pl.track_count else 0,
                "by_strategy": by_strategy.most_common(),
                "matched_list": matched[:200],
                "unmatched_list": unmatched[:200],
                "library_size": len(index),
            }
        )

    @app.post("/api/playlists/plex")
    def api_pl_plex():
        _require_pld()
        data = request.get_json(force=True)
        url = data.get("url") or os.environ.get("PLEX_URL", default_plex_url())
        token = data.get("token") or os.environ.get("PLEX_TOKEN", "")
        if not token:
            return jsonify(error="PLEX_TOKEN manquant"), 400
        client = pld.PlexPlaylistClient(url, token)
        try:
            playlists = client.list_all(
                types=tuple(data.get("types", ("audio", "video", "photo"))),
                include_smart=bool(data.get("include_smart", True)),
            )
        except Exception as exc:
            return jsonify(error=str(exc)), 500
        playlists.sort(key=lambda p: (p.type, p.title.casefold()))
        return jsonify([asdict(p) for p in playlists])

    @app.post("/api/playlists/plex/tracks")
    def api_pl_plex_tracks():
        _require_pld()
        data = request.get_json(force=True)
        rating_key = str(data.get("rating_key") or "").strip()
        if not rating_key:
            return jsonify(error="rating_key manquant"), 400

        url = data.get("url") or os.environ.get("PLEX_URL", default_plex_url())
        token = data.get("token") or os.environ.get("PLEX_TOKEN", "")
        if not token:
            return jsonify(error="PLEX_TOKEN manquant"), 400

        client = pld.PlexPlaylistClient(url, token)
        try:
            tracks = client.get_tracks(rating_key)
        except Exception as exc:
            return jsonify(error=str(exc)), 500

        total_duration = sum(int(t.get("duration") or 0) for t in tracks)
        return jsonify(
            {
                "rating_key": rating_key,
                "count": len(tracks),
                "duration_min": round(total_duration / 60, 1),
                "tracks": tracks,
            }
        )
