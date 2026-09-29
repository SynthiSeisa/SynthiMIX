"""AutoMix und Radio-Modus."""
import asyncio
import json
import os
import random
import re
from difflib import SequenceMatcher
from pathlib import Path
from .core import _state
from . import core, download, keys, media, search, store

_automix_running = False

async def _do_automix(last_title: str):
    """Search YouTube for a similar song, download it, and add to queue."""
    global _automix_running
    if _automix_running:
        return  # Already downloading a suggestion, skip duplicate trigger
    _automix_running = True
    try:
        await _do_automix_inner(last_title)
    finally:
        _automix_running = False

# ── Similar-artist pool — grows as AutoMix discovers artists via YTM results ──
_similar_artist_pool: list[str] = []    # ordered by discovery, no duplicates
_similar_artist_set:  set[str]  = set() # lowercase for fast lookup

def _extract_ytm_artist(r: dict) -> str | None:
    """Extract a clean artist name from a yt-dlp YTM search result."""
    # YouTube Music Topic channels: "Artist Name - Topic"
    for field in ("uploader", "channel"):
        v = r.get(field) or ""
        if v.endswith(" - Topic"):
            return v[:-8].strip()
    # Explicit artist field (sometimes present) — bei mehreren nur der erste
    if r.get("artist"):
        return str(r["artist"]).split(",")[0].strip()
    # Parse "Artist - Title" from track title
    title = r.get("title", "")
    if " - " in title:
        return title.split(" - ", 1)[0].strip()
    return None

def _ingest_similar_artists(results: list[dict], seed_artist: str) -> None:
    """Add newly discovered artists (≠ seed) to the pool."""
    seed_lo = seed_artist.lower()
    for r in results:
        a = _extract_ytm_artist(r)
        if not a:
            continue
        a_lo = a.lower()
        if a_lo == seed_lo or a_lo in _similar_artist_set:
            continue
        # Ignore "various artists" / channel-like names
        if re.search(r'\b(various|playlist|compilation|topic)\b', a, re.I):
            continue
        _similar_artist_pool.append(a)
        _similar_artist_set.add(a_lo)
        if len(_similar_artist_pool) > 200:       # cap pool size
            removed = _similar_artist_pool.pop(0)
            _similar_artist_set.discard(removed.lower())

_NOISE_RE = re.compile(
    r'\b(official|music|video|audio|lyrics?|lyric|hd|hq|4k|'
    r'original|mix|remix|edit|version|remaster(?:ed)?|'
    r'feat|ft|prod|explicit|clean|radio|extended|instrumental)\b',
    re.IGNORECASE
)

def _norm_title(t: str) -> str:
    """Normalise a title for duplicate detection."""
    t = re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', '', t)   # strip (anything in brackets)
    t = re.sub(r'[|–—].*$', '', t)                  # cut off after | or em-dash
    t = _NOISE_RE.sub('', t)
    return ' '.join(re.sub(r'[^\w\s]', '', t.lower()).split())

def _titles_similar(a: str, b: str) -> bool:
    """True if two raw titles refer to the same song (fuzzy word overlap)."""
    wa = set(_norm_title(a).split())
    wb = set(_norm_title(b).split())
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / min(len(wa), len(wb))
    return overlap >= 0.75

async def _do_automix_inner(last_title: str):
    await core.broadcast({"type": "automix_status", "text": "⟳ Auto-Mix sucht…"})

    clean = re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', '', last_title).strip() or last_title

    # ── Already-played blacklist ──────────────────────────────────────────────
    existing_urls = {h.get("url", "") for h in _state.get("history", [])}
    played_norm: set[str] = set()
    queue_paths: set[str] = set()
    for t in _state.get("queue", []):
        n = _norm_title(t.get("title", ""))
        if n: played_norm.add(n)
        if t.get("path"): queue_paths.add(t["path"])
    for h in _state.get("play_log", [])[:100]:
        n = _norm_title(h.get("title", ""))
        if n: played_norm.add(n)

    # ── Artist pool: last 50 actually played tracks (not just downloaded) ─────
    # Kept un-deduplicated so random.choice() naturally weights toward artists
    # played more often recently, not just artists played at all.
    recent_artists: list[str] = []
    for h in _state.get("play_log", [])[:50]:
        ht = h.get("title", "")
        if h.get("artist"):
            a = str(h["artist"]).strip()
        elif " - " in ht:
            a = ht.split(" - ", 1)[0].strip()
        else:
            a = ""
        if a:
            recent_artists.append(a)
    # Also consider library tracks that are in queue for artist hints
    for t in _state.get("queue", []):
        if t.get("artist"):
            a = str(t["artist"]).strip()
            if a:
                recent_artists.append(a)

    chosen_artist = random.choice(recent_artists) if recent_artists else ""
    if not chosen_artist and ' - ' in clean:
        chosen_artist = clean.split(' - ', 1)[0].strip()

    # ── Try local library first ───────────────────────────────────────────────
    if chosen_artist:
        artist_lo = chosen_artist.lower()
        candidates = [
            lt for lt in _state.get("library", [])
            if lt.get("path") not in queue_paths
            and (lt.get("artist", "").lower() == artist_lo
                 or lt.get("title", "").lower().startswith(artist_lo + " - "))
            and _norm_title(lt.get("title", "")) not in played_norm
        ]
        if candidates:
            ci = _state.get("current_idx", -1)
            cur_path = _state["queue"][ci].get("path", "") if 0 <= ci < len(_state["queue"]) else ""
            pick = _pick_harmonic(candidates, cur_path)
            _state["queue"].append({
                "path":         pick["path"],
                "title":        pick["title"],
                "duration_sec": pick.get("duration_sec", 0),
                "lufs":         pick.get("lufs", -99.0),
                "bpm":          pick.get("bpm", 0),
                "bitrate_kbps": pick.get("bitrate_kbps", 0),
                "played":       False,
            })
            store.save_queue()
            await core.push_queue()
            await core.broadcast({"type": "automix_status",
                             "text": f"✓ {pick['title']}"})
            return

    # ── Fallback: YouTube search ──────────────────────────────────────────────
    has_split = ' - ' in clean
    artist_part = chosen_artist or (clean.split(' - ', 1)[0].strip() if has_split else clean)
    title_part  = clean.split(' - ', 1)[1].strip() if has_split else ''
    title_words = set(re.sub(r'[^\w\s]', '', title_part.lower()).split()) if title_part else set()

    search_queries: list[str] = []
    seen_sq: set[str] = set()

    def _add_sq(a: str) -> None:
        if a and a.lower() not in seen_sq:
            search_queries.append(a)
            seen_sq.add(a.lower())

    if artist_part:
        _add_sq(artist_part)
        if _similar_artist_pool:
            _add_sq(random.choice(_similar_artist_pool))
    else:
        _add_sq(clean + " music")

    try:
        base_args = ["--flat-playlist", "-j", "--no-playlist", "--quiet"]
        if core.FFMPEG_DIR:
            base_args += ["--ffmpeg-location", core.FFMPEG_DIR]

        # Search across all queries, collect unique results
        all_results: list[dict] = []
        seen_result_urls: set[str] = set()
        for sq in search_queries:
            batch = await search._ytm_songs(sq, 8, details=True)
            # Learn similar artists from whatever YTM returned
            _ingest_similar_artists(batch, sq)
            for r in batch:
                u = r.get("url", "")
                if u and u not in seen_result_urls:
                    seen_result_urls.add(u)
                    all_results.append(r)

        # Fallback to regular YouTube only if YouTube Music returned nothing at all.
        # Plain YouTube search isn't scoped to music, so bias the query toward songs
        # and mark these results as "unverified" — they get extra scrutiny below.
        fallback_urls: set[str] = set()
        if not all_results:
            for sq in search_queries:
                batch = await search._run_search_cmd(core._yt(f"ytsearch15:{sq} song", *base_args))
                for r in batch:
                    u = r.get("url", "")
                    if u and u not in seen_result_urls:
                        seen_result_urls.add(u)
                        fallback_urls.add(u)
                        all_results.append(r)

        all_results.sort(key=lambda r: r.get("_score", 0), reverse=True)

        found_url   = None
        found_title = ""
        for r in all_results:
            u = r.get("url", "")
            if not u or u in existing_urls:
                continue
            r_title = r.get("title", "")
            # Skip mixes / compilations / podcasts
            if search.MIX_RE.search(r_title):
                continue
            # Skip interviews, reactions, talk content etc. — common in the unscoped
            # plain-YouTube fallback, since the YTM song search never surfaces these
            if search.NON_MUSIC_RE.search(r_title):
                continue
            # Skip live recordings / concert performances
            if search.LIVE_RE.search(r_title):
                continue
            # Keine Musikvideos (Intro, Pausen) — auch die Song-Suche laesst vereinzelt eins durch
            if search._MV_TITLE_RE.search(r_title):
                continue
            # Skip playlists / full albums
            if download._is_playlist(u):
                continue
            dur = r.get("duration") or 0
            # Skip tracks over 8 min (likely a mix/medley) and very short clips (<40s)
            if dur > 480 or (dur and dur < 40):
                continue
            # Unverified (plain-YouTube fallback) results: require a Topic-channel or
            # audio/lyrics title match — otherwise too easy to grab non-music uploads
            if u in fallback_urls and r.get("_score", 0) < 40:
                continue
            # Skip if exact normalised title already played
            r_norm = _norm_title(r_title)
            if r_norm in played_norm:
                continue
            # Fuzzy check only against the trigger song (avoids O(n²) over full history)
            if title_part and _titles_similar(r_title, title_part):
                continue
            found_url   = u
            found_title = r_title
            break

        if not found_url:
            await core.broadcast({"type": "automix_status", "text": "⚠ Kein Song gefunden"})
            await asyncio.sleep(4)
            await core.broadcast({"type": "automix_status", "text": ""})
            return

        await core.broadcast({"type": "automix_status", "text": f"⬇ {found_title[:48]}…"})
        path = await download.run_download(found_url, "mp3-best")

        if path and os.path.exists(path):
            loop = asyncio.get_running_loop()
            probe = await loop.run_in_executor(None, media._probe_sync, path)
            track = {
                "path":         path,
                "title":        probe["title"] or Path(path).stem,
                "duration_sec": probe["duration_sec"],
                "lufs":         -99.0,
                "bpm":          probe["bpm"],
                "bitrate_kbps": probe["bitrate_kbps"],
                "played":       False,
            }
            _state["queue"].append(track)
            store.save_queue()
            await core.push_queue()
            # Auto-play if player was idle
            if not _state["playing"] or _state["current_idx"] < 0:
                idx = len(_state["queue"]) - 1
                _state["current_idx"] = idx
                _state["playing"]     = True
                _state["position_ms"] = 0
                track["played"]       = True
                track["play_count"]   = 1
                await core.push_player()
                await core.broadcast({"type": "now_playing", "track": dict(track)})
                asyncio.create_task(media._enrich_track(path))
            await core.broadcast({"type": "automix_status", "text": f"✓ {track['title'][:48]}"})
        else:
            await core.broadcast({"type": "automix_status", "text": "⚠ Download fehlgeschlagen"})

    except Exception as e:
        await core.broadcast({"type": "automix_status", "text": f"⚠ Fehler: {str(e)[:40]}"})

    await asyncio.sleep(5)
    await core.broadcast({"type": "automix_status", "text": ""})


# ── Radio mode ────────────────────────────────────────────────────────────────
_radio_fill_running = False

async def _lastfm_similar(artist: str, title: str, api_key: str, limit: int = 30) -> list[dict]:
    import urllib.request as _req, urllib.parse as _parse
    params = _parse.urlencode({
        'method': 'track.getSimilar', 'artist': artist, 'track': title,
        'api_key': api_key, 'format': 'json', 'limit': limit, 'autocorrect': 1,
    })
    url = f"https://ws.audioscrobbler.com/2.0/?{params}"
    loop = asyncio.get_running_loop()
    def _fetch():
        try:
            req = _req.Request(url, headers={'User-Agent': 'SynthiMIX/1.4'})
            with _req.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode())
        except Exception:
            return {}
    data = await loop.run_in_executor(None, _fetch)
    tracks = data.get('similartracks', {}).get('track', [])
    return [{'artist': t.get('artist', {}).get('name', '') if isinstance(t.get('artist'), dict) else str(t.get('artist', '')),
             'title': t.get('name', '')} for t in tracks]

def _library_key(path: str) -> str:
    lt = next((x for x in _state.get("library", []) if x.get("path") == path), None)
    return (lt or {}).get("key", "") or ""

def _pick_harmonic(candidates: list[dict], ref_path: str) -> dict:
    """Zufaellig, aber bevorzugt unter den Titeln, deren Tonart zum Referenztitel
    passt. Ohne bekannte Tonart oder ohne passenden Kandidaten wie bisher
    zufaellig aus allen — bevorzugt, nicht ausgeschlossen."""
    ref = _library_key(ref_path)
    if ref:
        passend = [c for c in candidates if keys._key_compat(ref, c.get("key")) >= 2]
        if passend:
            return random.choice(passend)
    return random.choice(candidates)

async def _radio_fill():
    global _radio_fill_running
    if _radio_fill_running:
        return
    _radio_fill_running = True
    try:
        ci = _state['current_idx']
        q  = _state['queue']
        if ci < 0 or ci >= len(q):
            return

        np = q[ci]
        raw_title = np.get('title', '') or ''
        if ' - ' in raw_title:
            parts  = raw_title.split(' - ', 1)
            artist = parts[0].strip()
            title  = parts[1].strip()
        else:
            artist = np.get('artist', '') or ''
            title  = raw_title
        if not title:
            return

        in_queue = {t.get('path', '') for t in q}
        lib = _state.get('library', [])

        # Bug fix: all tracks already in queue → nothing to add, stop trying
        candidates_total = [lt for lt in lib if lt.get('path', '') not in in_queue
                            and float(lt.get('duration_sec', 0) or 0) > 60]
        if not candidates_total:
            return

        best_match: dict | None = None
        api_key = _state.get('lastfm_api_key', '').strip()
        if api_key:
            similar = await _lastfm_similar(artist, title, api_key)
            if similar:
                treffer: list[dict] = []    # in Last.fm-Reihenfolge
                for s in similar:
                    s_artist = s['artist'].lower()
                    s_title  = s['title'].lower()
                    best: dict | None = None
                    best_score = 0.0
                    for lt in candidates_total:
                        lt_title  = (lt.get('title')  or lt.get('name', '') or '').lower()
                        lt_artist = (lt.get('artist') or '').lower()
                        t_score = SequenceMatcher(None, s_title, lt_title).ratio()
                        a_score = SequenceMatcher(None, s_artist, lt_artist).ratio() if lt_artist else 0.5
                        score   = t_score * 0.7 + a_score * 0.3
                        if score > best_score and t_score > 0.7:
                            best_score = score
                            best = lt
                    if best and best not in treffer:
                        treffer.append(best)
                        if len(treffer) >= 8:
                            break
                # Der aehnlichste Titel, dessen Tonart passt — sonst der aehnlichste
                ref = _library_key(np.get('path', ''))
                passend = [t for t in treffer if ref and keys._key_compat(ref, t.get('key')) >= 2]
                if passend or treffer:
                    best_match = (passend or treffer)[0]

        # Fallback: Titel aus der Bibliothek, bevorzugt harmonisch passend
        if not best_match:
            best_match = _pick_harmonic(candidates_total, np.get('path', ''))

        track = {
            'path':         best_match.get('path', ''),
            'title':        best_match.get('title') or best_match.get('name', ''),
            'duration_sec': float(best_match.get('duration_sec', 0) or 0),
            'lufs':         float(best_match.get('lufs', -99.0) or -99.0),
            'bpm':          int(best_match.get('bpm', 0) or 0),
            'bitrate_kbps': int(best_match.get('bitrate_kbps', 0) or 0),
            'played':       False,
        }
        _state['queue'].append(track)
        store.save_queue()
        await core.push_queue()
        await core.broadcast({'type': 'radio_added',
                         'title': track['title'],
                         'similar_to': f"{artist} – {title}"})
    finally:
        _radio_fill_running = False

async def _check_radio_queue():
    if not _state.get('radio_enabled'):
        return
    ci = _state['current_idx']
    q  = _state['queue']
    remaining = sum(1 for t in q[max(0, ci+1):] if not t.get('played', False))
    if remaining < 3:
        asyncio.create_task(_radio_fill())
