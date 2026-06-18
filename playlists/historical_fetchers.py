from __future__ import annotations

import html
import json
import urllib.parse
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import List, Dict, Optional

CACHE_DIR = Path(__file__).parent / 'cache'
CACHE_DIR.mkdir(exist_ok=True)

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; scripts/generate_top_france)'}

LASTFM_API_KEY: str = os.environ.get('LASTFM_API_KEY', '')
DISCOGS_USER_TOKEN: str = os.environ.get('DISCOGS_USER_TOKEN', '')

_DECADE_TAGS: Dict[int, List[str]] = {
    1960: ['60s', '1960s'],
    1970: ['70s', '1970s'],
    1980: ['80s', '1980s', 'french 80s'],
    1990: ['90s', '1990s', 'french 90s'],
    2000: ['2000s', '00s', 'noughties'],
    2010: ['2010s', '10s'],
    2020: ['2020s', '20s'],
}


def _fetch_url(url: str, cache_key: str | None = None, timeout: int = 30) -> str:
    if cache_key:
        cache_path = CACHE_DIR / f"{cache_key}.html"
        if cache_path.exists():
            return cache_path.read_text(encoding='utf-8', errors='replace')
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read().decode('utf-8', errors='replace')
    if cache_key:
        try:
            (CACHE_DIR / f"{cache_key}.html").write_text(data, encoding='utf-8')
        except Exception:
            pass
    # politeness
    time.sleep(1.0)
    return data


def _fetch_json(
    url: str,
    extra_headers: Optional[Dict[str, str]] = None,
    cache_key: str | None = None,
    timeout: int = 30,
    sleep: float = 0.5,
) -> dict:
    if cache_key:
        cache_path = CACHE_DIR / f"{cache_key}.json"
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding='utf-8'))
            except Exception:
                pass
    hdrs = dict(HEADERS)
    if extra_headers:
        hdrs.update(extra_headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode('utf-8', errors='replace'))
    if cache_key:
        try:
            (CACHE_DIR / f"{cache_key}.json").write_text(json.dumps(data), encoding='utf-8')
        except Exception:
            pass
    time.sleep(sleep)
    return data


# ─────────────────────────────── Last.fm ────────────────────────────────────

def fetch_lastfm_geo_toptracks(
    country: str = 'France',
    limit: int = 500,
    api_key: str | None = None,
) -> List[Dict[str, str]]:
    """Top tracks les plus scrobblées dans un pays via Last.fm geo.getTopTracks.

    Retourne [{'title', 'artist', 'source': 'lastfm_geo', 'listeners', 'playcount'}, ...]
    """
    key = api_key or LASTFM_API_KEY
    if not key:
        return []

    results: List[Dict[str, str]] = []
    page = 1

    while len(results) < limit:
        ckey = f'lastfm_geo_{re.sub(r"[^a-z0-9]", "_", country.lower())}_{page}'
        url = (
            'http://ws.audioscrobbler.com/2.0/'
            f'?method=geo.gettoptracks'
            f'&country={urllib.parse.quote(country)}'
            f'&api_key={key}'
            f'&format=json&limit=200&page={page}'
        )
        try:
            data = _fetch_json(url, cache_key=ckey, sleep=0.3)
        except Exception:
            break

        tracks_data = data.get('tracks', {}).get('track', [])
        if not tracks_data:
            break

        for t in tracks_data:
            name = (t.get('name') or '').strip()
            artist = t.get('artist', {})
            artist = artist.get('name', '').strip() if isinstance(artist, dict) else str(artist).strip()
            if name:
                results.append({
                    'title': name,
                    'artist': artist,
                    'source': 'lastfm_geo',
                    'listeners': str(t.get('listeners', '')),
                    'playcount': str(t.get('playcount', '')),
                })

        attr = data.get('tracks', {}).get('@attr', {})
        total_pages = int(attr.get('totalPages', 1) or 1)
        if page >= total_pages:
            break
        page += 1

    return results[:limit]


def fetch_lastfm_tag_toptracks(
    tag: str,
    limit: int = 200,
    api_key: str | None = None,
) -> List[Dict[str, str]]:
    """Top tracks pour un tag Last.fm (ex: '2000s', 'chanson française').

    Retourne [{'title', 'artist', 'source': 'lastfm_tag', 'tag'}, ...]
    """
    key = api_key or LASTFM_API_KEY
    if not key:
        return []

    results: List[Dict[str, str]] = []
    page = 1

    while len(results) < limit:
        ckey = f'lastfm_tag_{re.sub(r"[^a-z0-9]", "_", tag.lower())}_{page}'
        url = (
            'http://ws.audioscrobbler.com/2.0/'
            f'?method=tag.gettoptracks'
            f'&tag={urllib.parse.quote(tag)}'
            f'&api_key={key}'
            f'&format=json&limit=200&page={page}'
        )
        try:
            data = _fetch_json(url, cache_key=ckey, sleep=0.3)
        except Exception:
            break

        tracks_data = data.get('tracks', {}).get('track', [])
        if not tracks_data:
            break

        for t in tracks_data:
            name = (t.get('name') or '').strip()
            artist = t.get('artist', {})
            artist = artist.get('name', '').strip() if isinstance(artist, dict) else str(artist).strip()
            if name:
                results.append({
                    'title': name,
                    'artist': artist,
                    'source': 'lastfm_tag',
                    'tag': tag,
                })

        attr = data.get('tracks', {}).get('@attr', {})
        total_pages = int(attr.get('totalPages', 1) or 1)
        if page >= total_pages:
            break
        page += 1

    return results[:limit]


def fetch_lastfm_france_decade(
    decade: int,
    limit: int = 500,
    api_key: str | None = None,
) -> List[Dict[str, str]]:
    """Top tracks France pour une décennie via Last.fm.

    Combine geo.getTopTracks(France) + tag.getTopTracks(décennie + tags français).
    Retourne [{'title', 'artist', 'source', 'decade'}, ...]
    """
    key = api_key or LASTFM_API_KEY
    if not key:
        return []

    seen: set = set()
    results: List[Dict[str, str]] = []

    def _add(tracks: List[Dict]) -> None:
        for t in tracks:
            k = (t['title'].lower(), t['artist'].lower())
            if k not in seen:
                seen.add(k)
                results.append({**t, 'decade': decade})

    _add(fetch_lastfm_geo_toptracks(country='France', limit=1000, api_key=key))

    decade_tags = _DECADE_TAGS.get(decade, [f'{decade}s'])
    french_tags = ['chanson française', 'variété française', 'french pop']
    for tag in decade_tags + french_tags:
        _add(fetch_lastfm_tag_toptracks(tag=tag, limit=200, api_key=key))

    return results[:limit]


def fetch_lastfm_global_decade(
    decade: int,
    limit: int = 500,
    api_key: str | None = None,
) -> List[Dict[str, str]]:
    """Top tracks mondiaux pour une décennie via Last.fm tag.getTopTracks.

    Retourne [{'title', 'artist', 'source': 'lastfm_tag', 'decade'}, ...]
    """
    key = api_key or LASTFM_API_KEY
    if not key:
        return []

    seen: set = set()
    results: List[Dict[str, str]] = []

    decade_tags = _DECADE_TAGS.get(decade, [f'{decade}s'])
    for tag in decade_tags:
        for t in fetch_lastfm_tag_toptracks(tag=tag, limit=limit, api_key=key):
            k = (t['title'].lower(), t['artist'].lower())
            if k not in seen:
                seen.add(k)
                results.append({**t, 'decade': decade})

    return results[:limit]


def fetch_lastfm_chart_toptracks(
    limit: int = 200,
    api_key: str | None = None,
) -> List[Dict[str, str]]:
    """Top tracks mondiaux en temps réel via Last.fm chart.getTopTracks.

    Retourne [{'title', 'artist', 'source': 'lastfm_chart', 'playcount', 'listeners'}, ...]
    """
    key = api_key or LASTFM_API_KEY
    if not key:
        return []

    results: List[Dict[str, str]] = []
    page = 1
    per_page = min(limit, 200)

    while len(results) < limit:
        ckey = f'lastfm_chart_toptracks_{page}'
        url = (
            'http://ws.audioscrobbler.com/2.0/'
            f'?method=chart.gettoptracks'
            f'&api_key={key}'
            f'&format=json&limit={per_page}&page={page}'
        )
        try:
            data = _fetch_json(url, cache_key=ckey, sleep=0.3)
        except Exception:
            break

        tracks_data = data.get('tracks', {}).get('track', [])
        if not tracks_data:
            break

        for t in tracks_data:
            name = (t.get('name') or '').strip()
            artist = t.get('artist', {})
            artist = artist.get('name', '').strip() if isinstance(artist, dict) else str(artist).strip()
            if name:
                results.append({
                    'title': name,
                    'artist': artist,
                    'source': 'lastfm_chart',
                    'playcount': str(t.get('playcount', '')),
                    'listeners': str(t.get('listeners', '')),
                })

        attr = data.get('tracks', {}).get('@attr', {})
        total_pages = int(attr.get('totalPages', 1) or 1)
        if page >= total_pages:
            break
        page += 1

    return results[:limit]


def fetch_lastfm_user_toptracks(
    username: str,
    period: str = 'overall',
    limit: int = 200,
    api_key: str | None = None,
) -> List[Dict[str, str]]:
    """Top tracks d'un utilisateur Last.fm via user.getTopTracks.

    period: overall | 7day | 1month | 3month | 6month | 12month
    Retourne [{'title', 'artist', 'source': 'lastfm_user', 'playcount', 'rank'}, ...]
    """
    key = api_key or LASTFM_API_KEY
    if not key or not username:
        return []

    results: List[Dict[str, str]] = []
    page = 1
    per_page = min(limit, 200)

    while len(results) < limit:
        ckey = f'lastfm_user_{re.sub(r"[^a-z0-9]", "_", username.lower())}_{period}_{page}'
        url = (
            'http://ws.audioscrobbler.com/2.0/'
            f'?method=user.gettoptracks'
            f'&user={urllib.parse.quote(username)}'
            f'&period={period}'
            f'&api_key={key}'
            f'&format=json&limit={per_page}&page={page}'
        )
        try:
            data = _fetch_json(url, cache_key=ckey, sleep=0.3)
        except Exception:
            break

        tracks_data = data.get('toptracks', {}).get('track', [])
        if not tracks_data:
            break

        for t in tracks_data:
            name = (t.get('name') or '').strip()
            artist = t.get('artist', {})
            artist = artist.get('name', '').strip() if isinstance(artist, dict) else str(artist).strip()
            if name:
                results.append({
                    'title': name,
                    'artist': artist,
                    'source': 'lastfm_user',
                    'playcount': str(t.get('playcount', '')),
                    'rank': str(t.get('@attr', {}).get('rank', '')),
                })

        attr = data.get('toptracks', {}).get('@attr', {})
        total_pages = int(attr.get('totalPages', 1) or 1)
        if page >= total_pages:
            break
        page += 1

    return results[:limit]


def fetch_lastfm_user_lovedtracks(
    username: str,
    limit: int = 500,
    api_key: str | None = None,
) -> List[Dict[str, str]]:
    """Pistes aimées (loved) d'un utilisateur Last.fm via user.getLovedTracks.

    Retourne [{'title', 'artist', 'source': 'lastfm_loved', 'date'}, ...]
    """
    key = api_key or LASTFM_API_KEY
    if not key or not username:
        return []

    results: List[Dict[str, str]] = []
    page = 1
    per_page = min(limit, 200)

    while len(results) < limit:
        ckey = f'lastfm_loved_{re.sub(r"[^a-z0-9]", "_", username.lower())}_{page}'
        url = (
            'http://ws.audioscrobbler.com/2.0/'
            f'?method=user.getlovedtracks'
            f'&user={urllib.parse.quote(username)}'
            f'&api_key={key}'
            f'&format=json&limit={per_page}&page={page}'
        )
        try:
            data = _fetch_json(url, cache_key=ckey, sleep=0.3)
        except Exception:
            break

        tracks_data = data.get('lovedtracks', {}).get('track', [])
        if not tracks_data:
            break

        for t in tracks_data:
            name = (t.get('name') or '').strip()
            artist = t.get('artist', {})
            artist = artist.get('name', '').strip() if isinstance(artist, dict) else str(artist).strip()
            date_str = t.get('date', {}).get('#text', '') if isinstance(t.get('date'), dict) else ''
            if name:
                results.append({
                    'title': name,
                    'artist': artist,
                    'source': 'lastfm_loved',
                    'date': date_str,
                })

        attr = data.get('lovedtracks', {}).get('@attr', {})
        total_pages = int(attr.get('totalPages', 1) or 1)
        if page >= total_pages:
            break
        page += 1

    return results[:limit]


# ─────────────────────────────── Discogs ────────────────────────────────────

def fetch_discogs_releases(
    year: Optional[int] = None,
    country: str = 'France',
    genre: Optional[str] = None,
    limit: int = 100,
    user_token: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Releases Discogs par pays/année triées par popularité ('have' count).

    Note: Discogs n'a pas de données de charts. Le compteur 'have' sert de proxy de popularité.
    Retourne [{'title', 'artist', 'year', 'source': 'discogs', 'genre'}, ...]
    """
    token = user_token or DISCOGS_USER_TOKEN
    if not token:
        return []

    results: List[Dict[str, str]] = []
    seen: set = set()
    page = 1
    per_page = min(limit, 100)

    while len(results) < limit:
        params: Dict[str, str] = {
            'country': country,
            'type': 'release',
            'per_page': str(per_page),
            'page': str(page),
            'sort': 'have',
            'sort_order': 'desc',
        }
        if year:
            params['year'] = str(year)
        if genre:
            params['genre'] = genre

        ckey = f'discogs_{re.sub(r"[^a-z0-9]", "_", country.lower())}_{year or "all"}_{genre or "all"}_{page}'
        url = f'https://api.discogs.com/database/search?{urllib.parse.urlencode(params)}'
        hdrs = {
            'Authorization': f'Discogs token={token}',
            'User-Agent': 'CadenceApp/1.0',
        }

        try:
            data = _fetch_json(url, extra_headers=hdrs, cache_key=ckey, sleep=1.0)
        except Exception:
            break

        items = data.get('results', [])
        if not items:
            break

        for item in items:
            raw = item.get('title', '')
            if ' - ' in raw:
                artist_part, title_part = raw.split(' - ', 1)
            else:
                artist_part, title_part = '', raw

            item_year = str(item.get('year', year or ''))
            item_genre = ', '.join((item.get('genre') or []) + (item.get('style') or []))

            k = (title_part.lower(), artist_part.lower())
            if k not in seen and title_part.strip():
                seen.add(k)
                results.append({
                    'title': _clean_text(title_part),
                    'artist': _clean_text(artist_part),
                    'year': item_year,
                    'source': 'discogs',
                    'genre': item_genre,
                })

        total_pages = data.get('pagination', {}).get('pages', 1)
        if page >= total_pages:
            break
        page += 1

    return results[:limit]


def fetch_wikipedia_year(year: int) -> List[Dict[str, str]]:
    """Try to fetch list of number-one singles in France for a year from Wikipedia.

    Returns list of {'position': int (optional), 'title': str, 'artist': str, 'source': 'wikipedia', 'year': int}
    """
    results: List[Dict[str, str]] = []
    # Try to find the best matching wiki page via search (en/fr), then fall back to common slugs
    urls: List[str] = []

    def _search_wikipedia_titles(year: int, lang: str = 'en') -> List[str]:
        cache_path = CACHE_DIR / f'wikipedia_search_{year}_{lang}.json'
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding='utf-8'))
            except Exception:
                pass
        queries = [
            f'"number-one singles" {year} France',
            f'List of number-one singles of {year} France',
            f'number one singles France {year}',
        ]
        titles: List[str] = []
        for q in queries:
            api = f'https://{lang}.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote_plus(q)}&format=json&utf8=1'
            try:
                txt = _fetch_url(api, cache_key=f'wikipedia_api_{year}_{lang}_{urllib.parse.quote_plus(q)}')
                data = json.loads(txt)
                for item in data.get('query', {}).get('search', []):
                    t = item.get('title')
                    if t and t not in titles:
                        titles.append(t)
                if titles:
                    break
            except Exception:
                continue
        try:
            cache_path.write_text(json.dumps(titles), encoding='utf-8')
        except Exception:
            pass
        return titles

    # prefer english search results, then french
    for lang in ('en', 'fr'):
        for title in _search_wikipedia_titles(year, lang=lang):
            urls.append(f'https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(" ", "_"))}')

    # fallback candidates (existing heuristics)
    urls.extend([
        f'https://en.wikipedia.org/wiki/List_of_number-one_singles_of_{year}_(France)',
        f'https://en.wikipedia.org/wiki/List_of_number-one_singles_of_{year}_in_France',
        f'https://fr.wikipedia.org/wiki/Liste_des_singles_num%C3%A9ro_un_en_France_en_{year}',
    ])
    for idx, url in enumerate(urls):
        try:
            html_text = _fetch_url(url, cache_key=f'wikipedia_{year}_{idx}')
        except Exception:
            continue
        # find first wikitable
        m = re.search(r'<table[^>]*class="[^"]*wikitable[^"]*"[^>]*>(.*?)</table>', html_text, flags=re.S | re.I)
        if not m:
            # fallback: search any table
            m = re.search(r'<table[^>]*>(.*?)</table>', html_text, flags=re.S | re.I)
            if not m:
                continue
        table = m.group(1)
        # extract rows
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, flags=re.S | re.I)
        for r in rows[1:]:
            # find anchor texts in the row
            anchors = re.findall(r'<a[^>]*>([^<]+)</a>', r, flags=re.S | re.I)
            # filter out date-like anchors (months, numbers)
            def is_date_like(s: str) -> bool:
                s2 = s.strip()
                if re.match(r'^\d{1,2}$', s2):
                    return True
                if re.match(r'^[A-Za-z]+\s+\d{1,2}$', s2):
                    return True
                if s2.lower() in ('january','february','march','april','may','june','july','august','september','october','november','december'):
                    return True
                return False

            filtered = [a.strip() for a in anchors if not is_date_like(a) and len(a.strip()) > 2]
            title = ''
            artist = ''
            if len(filtered) >= 2:
                title = _clean_text(filtered[0])
                artist = _clean_text(filtered[1])
            else:
                # fallback: look for italic title
                m = re.search(r'<i[^>]*>([^<]+)</i>', r)
                if m:
                    title = _clean_text(m.group(1))
                    # try to find following artist anchor
                    m2 = re.search(r'</i>.*?<a[^>]*>([^<]+)</a>', r, flags=re.S | re.I)
                    if m2:
                        artist = _clean_text(m2.group(1))
            if title:
                results.append({'title': title, 'artist': artist, 'source': 'wikipedia', 'year': str(year)})
        if results:
            return results
    return results


def fetch_lescharts_year(year: int) -> List[Dict[str, str]]:
    """Attempt to fetch top singles for a year from lescharts.com.

    lescharts structure is inconsistent; we try a common pattern and fall back to empty list.
    """
    results: List[Dict[str, str]] = []
    # Potential URL patterns — may not exist
    candidates = [
        f'https://lescharts.com/year.asp?year={year}',
        f'https://lescharts.com/year/{year}',
    ]

    def _is_nav_href(href: str) -> bool:
        href = href.lower()
        # ignore navigation, pagination, external links
        return any(x in href for x in ('/year', 'year.asp', 'chart', 'javascript:', 'mailto:', 'http')) and not any(x in href for x in ('showitem.asp', 'interpret', 'title', 'song', '/song/', '/track/'))

    for idx, url in enumerate(candidates):
        try:
            page = _fetch_url(url, cache_key=f'lescharts_{year}_{idx}')
        except Exception:
            continue

        # Strategy A: scan table rows / <tr> and capture anchors with hrefs
        matches = re.findall(r'<tr[^>]*>(.*?)</tr>', page, flags=re.S | re.I)
        for tr in matches:
            # capture (href, text) pairs
            pairs = re.findall(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>', tr, flags=re.S | re.I)
            # filter out nav links and short texts
            filtered = [(h, t.strip()) for (h, t) in pairs if t.strip() and not _is_nav_href(h)]
            if len(filtered) >= 2:
                # heuristics: href containing 'interpret' or 'artist' => artist; 'title'/'song'/'showitem' => title
                title = None
                artist = None
                # try to assign by href hints
                for h, t in filtered:
                    lo = h.lower()
                    if any(k in lo for k in ('title', 'song', 'showitem', '/song', '/track')) and not title:
                        title = t
                    elif any(k in lo for k in ('interpret', 'artist')) and not artist:
                        artist = t
                # fallback: first=title, second=artist
                if not title:
                    title = filtered[0][1]
                if not artist and len(filtered) >= 2:
                    artist = filtered[1][1]
                if title:
                    results.append({'title': _clean_text(title), 'artist': _clean_text(artist or ''), 'source': 'lescharts', 'year': str(year)})

        # Strategy B: look for showitem pairs anywhere in the page
        if not results:
            pairs = re.findall(r'<a[^>]*href=["\']([^"\']*showitem\.asp[^"\']*)["\'][^>]*>([^<]+)</a>', page, flags=re.S | re.I)
            # group nearby occurrences into title/artist pairs by proximity in the HTML
            texts = [t.strip() for (_, t) in pairs if t.strip()]
            # assume alternating title/artist or artist/title depending on common patterns
            for i in range(0, len(texts) - 1, 2):
                a = texts[i]
                b = texts[i + 1]
                # heuristics: if a contains comma or ' - ' treat as title
                results.append({'title': _clean_text(a), 'artist': _clean_text(b), 'source': 'lescharts', 'year': str(year)})

        # Strategy C: textual patterns like "Title — Artist" or "Artist — Title"
        if not results:
            for m in re.findall(r'>([^<>\n]{2,100}?)\s+[—-]\s+([^<>\n]{2,100}?)<', page, flags=re.I):
                left, right = m
                # decide which is title vs artist by simple heuristics: if right contains common artist tokens (feat, ft) treat right as artist
                results.append({'title': _clean_text(left), 'artist': _clean_text(right), 'source': 'lescharts', 'year': str(year)})

        if results:
            # deduplicate while preserving order
            seen = set()
            uniq: List[Dict[str, str]] = []
            for r in results:
                key = (r['title'].lower(), (r.get('artist') or '').lower())
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(r)
            return uniq

    if results:
        return results

    # Fallback: try acharts.co (alternative aggregator)
    try:
        return fetch_acharts_year(year)
    except Exception:
        return results


def fetch_acharts_year(year: int) -> List[Dict[str, str]]:
    """Fetch year listings from acharts.co as a fallback.

    Returns list of {'title','artist','source':'acharts','year':str(year)}
    """
    results: List[Dict[str, str]] = []
    candidates = [
        f'https://acharts.co/year/{year}',
        f'https://acharts.co/fr/year/{year}',
        f'https://acharts.co/year/{year}/charts',
    ]
    for idx, url in enumerate(candidates):
        try:
            page = _fetch_url(url, cache_key=f'acharts_{year}_{idx}')
        except Exception:
            continue

        # Strategy 1: table rows
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', page, flags=re.S | re.I)
        for r in rows:
            atexts = re.findall(r'<a[^>]*>([^<]+)</a>', r)
            if len(atexts) >= 2:
                title = _clean_text(atexts[0])
                artist = _clean_text(atexts[1])
                results.append({'title': title, 'artist': artist, 'source': 'acharts', 'year': str(year)})

        # Strategy 2: list items with anchors
        if not results:
            items = re.findall(r'<li[^>]*>(.*?)</li>', page, flags=re.S | re.I)
            for it in items:
                atexts = re.findall(r'<a[^>]*>([^<]+)</a>', it)
                if len(atexts) >= 2:
                    title = _clean_text(atexts[0])
                    artist = _clean_text(atexts[1])
                    results.append({'title': title, 'artist': artist, 'source': 'acharts', 'year': str(year)})

        # Strategy 3: textual "Title — Artist"
        if not results:
            for left, right in re.findall(r'>([^<>\n]{2,100}?)\s+[—-]\s+([^<>\n]{2,100}?)<', page, flags=re.I):
                results.append({'title': _clean_text(left), 'artist': _clean_text(right), 'source': 'acharts', 'year': str(year)})

        if results:
            # dedupe
            seen = set()
            uniq: List[Dict[str, str]] = []
            for r in results:
                key = (r['title'].lower(), (r.get('artist') or '').lower())
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(r)
            return uniq

    return results


def fetch_infodisc_year(year: int) -> List[Dict[str, str]]:
    """Fetch year listings from infodisc.fr as a fallback."""
    results: List[Dict[str, str]] = []
    candidates = [
        f'https://www.infodisc.fr/Annee_{year}.php',
        f'https://www.infodisc.fr/annees_{year}.php',
        f'https://www.infodisc.fr/{year}.php',
    ]
    for idx, url in enumerate(candidates):
        try:
            page = _fetch_url(url, cache_key=f'infodisc_{year}_{idx}')
        except Exception:
            continue

        # common patterns: tables, list items, or "Titre - Interprète"
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', page, flags=re.S | re.I)
        for r in rows:
            atexts = re.findall(r'<a[^>]*>([^<]+)</a>', r)
            if len(atexts) >= 2:
                title = _clean_text(atexts[0])
                artist = _clean_text(atexts[1])
                results.append({'title': title, 'artist': artist, 'source': 'infodisc', 'year': str(year)})

        if not results:
            for left, right in re.findall(r'>([^<>\n]{2,100}?)\s+[—-]\s+([^<>\n]{2,100}?)<', page, flags=re.I):
                results.append({'title': _clean_text(left), 'artist': _clean_text(right), 'source': 'infodisc', 'year': str(year)})

        if results:
            # dedupe
            seen = set()
            uniq: List[Dict[str, str]] = []
            for r in results:
                key = (r['title'].lower(), (r.get('artist') or '').lower())
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(r)
            return uniq
    return results


def fetch_hitparade_year(year: int) -> List[Dict[str, str]]:
    """Fetch year listings from hitparade.ch as a fallback."""
    results: List[Dict[str, str]] = []
    candidates = [
        f'https://hitparade.ch/year.asp?year={year}',
        f'https://hitparade.ch/year/{year}',
    ]
    for idx, url in enumerate(candidates):
        try:
            page = _fetch_url(url, cache_key=f'hitparade_{year}_{idx}')
        except Exception:
            continue

        # look for table rows or list items
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', page, flags=re.S | re.I)
        for r in rows:
            atexts = re.findall(r'<a[^>]*>([^<]+)</a>', r)
            if len(atexts) >= 2:
                title = _clean_text(atexts[0])
                artist = _clean_text(atexts[1])
                results.append({'title': title, 'artist': artist, 'source': 'hitparade', 'year': str(year)})

        if not results:
            items = re.findall(r'<li[^>]*>(.*?)</li>', page, flags=re.S | re.I)
            for it in items:
                atexts = re.findall(r'<a[^>]*>([^<]+)</a>', it)
                if len(atexts) >= 2:
                    title = _clean_text(atexts[0])
                    artist = _clean_text(atexts[1])
                    results.append({'title': title, 'artist': artist, 'source': 'hitparade', 'year': str(year)})

        if not results:
            for left, right in re.findall(r'>([^<>\n]{2,100}?)\s+[—-]\s+([^<>\n]{2,100}?)<', page, flags=re.I):
                results.append({'title': _clean_text(left), 'artist': _clean_text(right), 'source': 'hitparade', 'year': str(year)})

        if results:
            seen = set()
            uniq: List[Dict[str, str]] = []
            for r in results:
                key = (r['title'].lower(), (r.get('artist') or '').lower())
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(r)
            return uniq

    return results


def fetch_ultratop_year(year: int) -> List[Dict[str, str]]:
    """Fetch year listings from ultratop.be as a fallback."""
    results: List[Dict[str, str]] = []
    candidates = [
        f'https://www.ultratop.be/fr/annual.asp?year={year}',
        f'https://www.ultratop.be/fr/annual/{year}',
        f'https://www.ultratop.be/fr/annual.asp?year={year}&cat=s',
    ]
    for idx, url in enumerate(candidates):
        try:
            page = _fetch_url(url, cache_key=f'ultratop_{year}_{idx}')
        except Exception:
            continue

        # try table rows
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', page, flags=re.S | re.I)
        for r in rows:
            atexts = re.findall(r'<a[^>]*>([^<]+)</a>', r)
            if len(atexts) >= 2:
                title = _clean_text(atexts[0])
                artist = _clean_text(atexts[1])
                results.append({'title': title, 'artist': artist, 'source': 'ultratop', 'year': str(year)})

        # fallback: look for textual patterns
        if not results:
            for left, right in re.findall(r'>([^<>\n]{2,120}?)\s+[—-]\s+([^<>\n]{2,120}?)<', page, flags=re.I):
                results.append({'title': _clean_text(left), 'artist': _clean_text(right), 'source': 'ultratop', 'year': str(year)})

        if results:
            seen = set()
            uniq: List[Dict[str, str]] = []
            for r in results:
                key = (r['title'].lower(), (r.get('artist') or '').lower())
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(r)
            return uniq

    return results


def _clean_html(value: str) -> str:
    v = re.sub(r'<script.*?>.*?</script>', '', value, flags=re.S | re.I)
    v = re.sub(r'<style.*?>.*?</style>', '', v, flags=re.S | re.I)
    v = re.sub(r'<[^>]+>', '', v)
    v = html.unescape(v)
    return _clean_text(v)


def _clean_text(s: str) -> str:
    s = s.strip()
    # remove extra whitespace and footnote markers [1]
    s = re.sub(r'\[.*?\]', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s
