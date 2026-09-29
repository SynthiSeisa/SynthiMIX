"""Suche: YouTube Music, Song-Versionen, Abgleich mit der Sammlung."""
import asyncio
import json
import re
import time
from difflib import SequenceMatcher
from fastapi import WebSocket
from .core import _NO_WINDOW, _state
from . import core, download, store

LIVE_RE = re.compile(
    r'[\(\[]\s*live\s*[\)\]]'                                          # (live) oder [live]
    r'|\blive\s+(?:at|in|from|version|performance|session|recording|concert|show)\b'
    r'|\bconcert\b|\btour\s*\d{4}\b|\bunplugged\b'
    r'|\blive\s+acoustic\b|\bacoustic\s+live\b'
    r'|\bat\s+the\s+\w+\s+(?:arena|stadium|festival|hall|theater|theatre)\b',
    re.IGNORECASE
)

MIX_RE = re.compile(
    r'\b(dj\s+mix|mixed\s+by|continuous\s+mix|megamix|mashup|mixtape|'
    r'hour\s+mix|\d+\s*h(our|r)?\s*mix|full\s+mix|best\s+of\s+mix|'
    r'mix\s*\d{4}|podcast|radio\s+show|episode\s+\d)\b',
    re.IGNORECASE
)

# Spoken-word / non-music content — mostly shows up via the plain-YouTube fallback search,
# which (unlike the YouTube Music song search) isn't scoped to the music catalog at all
NON_MUSIC_RE = re.compile(
    r'\b(interview|talks?\s+about|talking\s+about|reacts?\s+to|reaction|'
    r'documentary|behind\s+the\s+scenes|q\s*&?\s*a|q\s+and\s+a|discusses?|discussion|'
    r'explains?|explained|vlog|live\s*stream|asmr|tutorial|how\s+to|'
    r'review|unboxing|trailer|teaser|breaking\s+news|news\s+update|announcement|'
    r'press\s+conference|speech|lecture|tedx?|sermon|audiobook|story\s*time|'
    r'gameplay|walkthrough|in\s+conversation|sits?\s+down\s+with)\b',
    re.IGNORECASE
)

_VIDEO_KEYWORDS = {"official video", "music video", "official mv", "mv)", "(mv)", "live", "concert", "tour"}

def _score_result(item: dict) -> int:
    """Higher = prefer. Topic channels and lyric/audio versions rank first."""
    score = 0
    uploader = (item.get("uploader") or item.get("channel") or "").lower()
    title    = (item.get("title") or "").lower()
    if "- topic" in uploader:
        score += 100          # YouTube Music auto-generated channel
    if any(k in title for k in ("audio", "lyrics", "lyric", "official audio")):
        score += 40
    if any(k in title for k in _VIDEO_KEYWORDS):
        score -= 60           # penalise video versions
    return score

def _normalise_yt_url(raw: str) -> str:
    """yt-dlp --flat-playlist sometimes returns just a video ID, not a full URL."""
    if raw.startswith("http"):
        return raw
    if raw:
        return f"https://www.youtube.com/watch?v={raw}"
    return raw


async def _run_search_cmd(cmd: list) -> list[dict]:
    results = []
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=_NO_WINDOW)
        async for raw in proc.stdout:
            try:
                item = json.loads(raw.decode("utf-8", errors="replace"))
                url = _normalise_yt_url(
                    item.get("url") or item.get("webpage_url") or item.get("id", ""))
                if not url:
                    continue
                vid_id = item.get("id") or ""
                thumb = (item.get("thumbnail") or
                         (item.get("thumbnails") or [{}])[-1].get("url") or
                         (f"https://i.ytimg.com/vi/{vid_id}/mqdefault.jpg" if vid_id else ""))
                results.append({
                    "url":       url,
                    "title":     item.get("title", ""),
                    "uploader":  item.get("uploader") or item.get("channel") or "",
                    "duration":  item.get("duration") or 0,
                    "abr":       item.get("abr") or item.get("audio_bitrate") or 0,
                    "thumbnail": thumb,
                    "_score":    _score_result(item),
                })
            except Exception:
                pass
        await proc.wait()
    except asyncio.CancelledError:
        core._kill_quietly(proc)
        raise
    except Exception:
        core._kill_quietly(proc)
    return results

_VIDEO_TITLE_RE = re.compile(
    r'\b(official\s+(?:music\s+)?video|music\s+video|official\s+mv|'
    r'\bmv\b|live\s+(?:version|performance|session|at)|concert|tour)\b',
    re.IGNORECASE
)


_MV_TITLE_RE = re.compile(
    r'\b(official\s+(?:music\s+)?video|music\s+video|official\s+mv|\bmv\b)\b',
    re.IGNORECASE
)

def _is_unwanted_result(item: dict) -> bool:
    """True for full albums (>10 min) and music videos (not from Topic channels)."""
    duration = item.get("duration") or 0
    if duration > 600:          # longer than 10 min → likely full album / DJ mix
        return True
    title    = item.get("title", "")
    uploader = (item.get("uploader") or "").lower()
    is_topic = "- topic" in uploader
    if _MV_TITLE_RE.search(title) and not is_topic:
        return True             # official music video (Topic channels only release audio)
    return False

# ── Einzelner Videolink: Musikvideo oder Song? ──────────────────────────────
# Musikvideos haben oft Intro, Pausen oder Geraeusche. Gibt es auf YouTube Music
# die Studio-Version, wird gefragt, welche geladen werden soll. Playlists
# ersetzt _audit_playlist_for_videos schon ohne Rueckfrage.

_AUDIO_TITLE_RE = re.compile(r'\b(official\s+audio|audio|lyrics?|lyric\s+video|visuali[sz]er)\b', re.IGNORECASE)
_TITLE_NOISE_RE = re.compile(
    r'[\(\[][^\)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|mv|clip)\b[^\)\]]*[\)\]]',
    re.IGNORECASE)
_MV_EXTRA_SEC = 8   # so viel laenger als der Song gilt ein Video als "mit Intro/Pausen"


_VERSION_WORDS = {"remix", "edit", "vip", "bootleg", "mix", "flip", "rework", "extended", "acoustic", "cover", "live", "remaster", "instrumental"}


# ── YouTube Music: Song-Suche ────────────────────────────────────────────────
# Das Praefix "ytmsearch" gibt es in yt-dlp nicht ("Unsupported url scheme") —
# die App hat es seit v1.3.3 benutzt, jede Suche fiel still auf die normale
# YouTube-Suche zurueck. Die Such-Adresse mit #songs liefert nur Studio-
# Versionen, im schnellen flachen Modus aber nur Titel und ID. Laenge und
# Kuenstler kommen je Treffer per Einzelabfrage (_ytm_fill_details).
_YTM_DETAIL_PARALLEL = 4


def _ytm_search_url(query: str) -> str:
    from urllib.parse import quote_plus
    return f"https://music.youtube.com/search?q={quote_plus(query)}#songs"


def _video_id(url: str) -> str:
    m = re.search(r'(?:v=|youtu\.be/|shorts/)([\w-]{6,})', url or "")
    return m.group(1) if m else ""


_YTM_API = "https://music.youtube.com/youtubei/v1/search?prettyPrint=false"
_YTM_SONGS_PARAM = "EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D"      # Filter "Songs"


def _ytm_api_search_sync(query: str, n: int) -> list[dict] | None:
    """Song-Suche direkt ueber die Web-Schnittstelle von YouTube Music: eine
    Anfrage (~0,7 s) liefert Titel, Kuenstler, Album und Laenge. yt-dlp braucht
    dafuer eine Abfrage je Treffer (~5 s). None = Schnittstelle nicht nutzbar,
    dann uebernimmt der yt-dlp-Weg."""
    import urllib.request as _req
    body = {"context": {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.20250101.01.00",
                                   "hl": "en", "gl": "DE"}},
            "query": query, "params": _YTM_SONGS_PARAM}
    try:
        req = _req.Request(_YTM_API, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "User-Agent": "Mozilla/5.0",
            "Origin": "https://music.youtube.com"})
        with _req.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8", errors="replace"))
        tabs = d["contents"]["tabbedSearchResultsRenderer"]["tabs"]
        sections = tabs[0]["tabRenderer"]["content"]["sectionListRenderer"]["contents"]
    except Exception:
        return None

    def runs(col):
        return (((col or {}).get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text") or {}).get("runs") or []

    out = []
    for sec in sections:
        for it in (sec.get("musicShelfRenderer") or {}).get("contents", []):
            r = it.get("musicResponsiveListItemRenderer") or {}
            vid = (r.get("playlistItemData") or {}).get("videoId")
            cols = r.get("flexColumns") or []
            if not vid or len(cols) < 2:
                continue
            title = "".join(x.get("text", "") for x in runs(cols[0])).strip()
            parts = [p.strip() for p in "".join(x.get("text", "") for x in runs(cols[1])).split("•")]
            dur = 0
            if parts and re.fullmatch(r"\d+:\d{2}(?::\d{2})?", parts[-1]):
                for x in parts[-1].split(":"):
                    dur = dur * 60 + int(x)
            artist = parts[0] if parts else ""
            out.append({"url": f"https://music.youtube.com/watch?v={vid}", "title": title,
                        "artist": artist, "uploader": artist, "duration": dur,
                        "album": parts[1] if len(parts) > 2 else "",
                        "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                        "kind": "song", "_score": 100 + _score_result({"title": title, "uploader": artist})})
            if len(out) >= n:
                return out
    return out


async def _ytm_fill_details(songs: list[dict]) -> list[dict]:
    """Laenge, Kuenstler und Songtitel je Treffer nachladen (parallel).
    Treffer aus der schnellen Suche haben beides schon."""
    songs_todo = [r for r in songs if not (r.get("duration") and r.get("artist"))]
    if not songs_todo:
        return songs
    sem = asyncio.Semaphore(_YTM_DETAIL_PARALLEL)

    async def one(r):
        async with sem:
            pr = None
            try:
                pr = await asyncio.create_subprocess_exec(
                    *core._yt("--skip-download", "-j", "--no-playlist", "--quiet", "--no-warnings", r["url"]),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=_NO_WINDOW)
                out, _ = await asyncio.wait_for(pr.communicate(), timeout=30)
                item = json.loads(out.decode("utf-8", errors="replace").strip().splitlines()[0])
            except asyncio.CancelledError:
                core._kill_quietly(pr)
                raise
            except Exception:
                core._kill_quietly(pr)
                return
            # YouTube Music fuehrt teils auch Komponisten als Kuenstler — hoechstens zwei zeigen
            names = item.get("artists") or [a.strip() for a in (item.get("artist") or "").split(",") if a.strip()]
            artist = ", ".join(names[:2]) or item.get("uploader") or ""
            r["title"]    = item.get("track") or item.get("title") or r["title"]
            r["artist"]   = artist
            r["uploader"] = artist
            r["duration"] = item.get("duration") or r.get("duration") or 0
            r["abr"]      = item.get("abr") or r.get("abr") or 0

    await asyncio.gather(*(one(r) for r in songs_todo))
    return songs


async def _ytm_songs(query: str, n: int = 8, details: bool = True) -> list[dict]:
    """Studio-Versionen von YouTube Music. Zuerst ueber die schnelle
    Schnittstelle (alles in einer Anfrage); faellt die aus, per yt-dlp —
    details=False ist dort schnell (~2 s), liefert aber nur Titel und Link."""
    fast = await asyncio.get_running_loop().run_in_executor(None, _ytm_api_search_sync, query, n)
    if fast:
        return fast
    songs = await _run_search_cmd(core._yt(
        "--flat-playlist", "-j", "--quiet", "--no-warnings",
        "--playlist-items", f"1-{n}", _ytm_search_url(query)))
    for r in songs:
        r["kind"]   = "song"
        r["artist"] = r.get("artist") or ""
        r["_score"] = r.get("_score", 0) + 100   # Studio-Version vor jedem Video-Upload
    if details and songs:
        await _ytm_fill_details(songs)
    return songs


def _merge_songs_first(songs: list[dict], videos: list[dict]) -> list[dict]:
    """Songs zuerst, dann YouTube-Uploads ohne die schon gelisteten Titel.
    YouTube bleibt dabei, weil es viele Remixe und Bootlegs nur dort gibt."""
    seen = {_video_id(r["url"]) for r in songs}
    rest = [r for r in videos if _video_id(r["url"]) not in seen]
    for r in rest:
        r.setdefault("kind", "video")
    return songs + rest


def _is_single_link(url: str) -> bool:
    """Ein einzelner Titel-Link (YouTube, YouTube Music, SoundCloud …), keine Playlist."""
    return url.lower().startswith("http") and not download._is_spotify(url) and not download._is_playlist(url)


# Gleicher Song trotz kleiner Abweichungen: Video-Zusaetze, "ft."/"feat.", Umlaute,
# Satzzeichen, Tippfehler. Remix/Edit/VIP/Extended bleiben verschiedene Versionen.
_DL_DUPE_NOISE_RE = re.compile(
    r'[\(\[][^\)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|mv|clip|videoclip|free\s+download|out\s+now)\b[^\)\]]*[\)\]]'
    r'|\b(official\s+(?:music\s+)?video|official\s+audio|lyric\s+video|music\s+video|lyrics)\b',
    re.IGNORECASE)
_DL_DUPE_SONG_SIM   = 0.88   # Titel-Aehnlichkeit
_DL_DUPE_ARTIST_SIM = 0.7
_DL_DUPE_MAX_DIFF   = 30     # s — deutlich laenger/kuerzer ist eine andere Fassung


def _dupe_norm(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("&", " and ")
    t = re.sub(r"\b(ft|feat|featuring)\b\.?", "feat", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _dupe_parts(title: str, artist: str = "") -> tuple[str, str]:
    """(Kuenstler, Songtitel) normalisiert. "Kuenstler - Titel" im Titel geht vor
    dem Kuenstler-Feld, das bei YouTube-Downloads meist der Kanal ist."""
    t = _DL_DUPE_NOISE_RE.sub(" ", title or "")
    t = re.sub(r"[\(\[]\s*original\s+mix\s*[\)\]]", " ", t, flags=re.IGNORECASE)   # "Original Mix" = der Song selbst
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", t).strip()
    m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", t)
    a, song = (m.group(1), m.group(2)) if m else (artist or "", t)
    return _dupe_norm(a), _dupe_norm(song)


def _song_key(title: str, artist: str, dur) -> tuple:
    """Vergleichsschluessel eines Songs: (Kuenstler, Titel, Versionswoerter, Laenge)."""
    a, song = _dupe_parts(title or "", artist or "")
    return (a, song, frozenset(set(song.split()) & _VERSION_WORDS), float(dur or 0))

def _same_song(k1: tuple, k2: tuple) -> float:
    """Wie sicher derselbe Song (0 = nein, sonst Titel-Aehnlichkeit)."""
    a, song, version, vdur = k1
    la, ls, lversion, ldur = k2
    if len(song) < 2 or not ls or version != lversion:
        return 0.0
    sm = SequenceMatcher(None, song, ls)
    if sm.real_quick_ratio() < _DL_DUPE_SONG_SIM or sm.ratio() < _DL_DUPE_SONG_SIM:
        return 0.0
    if vdur and ldur and abs(vdur - ldur) > _DL_DUPE_MAX_DIFF:
        return 0.0
    if a and la:
        if not (a in la or la in a or SequenceMatcher(None, a, la).ratio() >= _DL_DUPE_ARTIST_SIM):
            return 0.0
    elif song != ls and not (vdur and ldur and abs(vdur - ldur) <= 3):
        return 0.0          # ohne Kuenstler: gleicher Titel, sonst muss die Laenge passen
    return sm.ratio()

def _library_index() -> list[tuple]:
    """Vergleichsschluessel der ganzen Bibliothek, einmal berechnet — fuer
    viele Abfragen hintereinander (Playlist mit 140 Titeln)."""
    return [(_song_key(lt.get("title", ""), lt.get("artist") or lt.get("album_artist") or "",
                       lt.get("duration_sec")), lt)
            for lt in _state["library"] if lt.get("path") and not lt.get("missing")]

def _library_matches(video: dict, limit: int = 5, index: list | None = None) -> list[dict]:
    """Bibliothekstitel, die derselbe Song sein duerften wie video
    ({title, artist, uploader, duration}). Beste zuerst."""
    k = _song_key(video.get("title", ""), video.get("artist") or "", video.get("duration"))
    if len(k[1]) < 2:
        return []
    found = []
    for lk, lt in (index if index is not None else _library_index()):
        r = _same_song(k, lk)
        if r:
            found.append((r, -abs((k[3] or 0) - (lk[3] or 0)), lt))
    found.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [lt for _, _, lt in found[:limit]]


def _is_single_youtube_video(url: str) -> bool:
    u = url.lower()
    if not u.startswith("http") or download._is_playlist(url):
        return False
    return ("youtube.com/watch" in u or "youtu.be/" in u or "youtube.com/shorts/" in u) \
        and "music.youtube.com" not in u


def _song_query(title: str) -> str:
    """Suchbegriff fuer die Song-Version: Video-Zusaetze raus, Remix & Co. bleiben."""
    q = _TITLE_NOISE_RE.sub(" ", title or "")
    q = _VIDEO_TITLE_RE.sub(" ", q)
    return re.sub(r"\s+", " ", q).strip(" -|")


def _norm_words(t: str) -> set[str]:
    t = _TITLE_NOISE_RE.sub(" ", (t or "").lower())
    return {w for w in re.findall(r"\w+", t) if len(w) > 1 and w not in ("feat", "ft", "the", "and")}


def _pick_song_version(video: dict, results: list[dict]) -> dict | None:
    """Beste Song-Version zu einem Video oder None. results kommen aus der
    Song-Suche von YouTube Music (nur Studio-Versionen). Keine Live-Aufnahmen,
    Titel und Kuenstler muessen passen, der Song darf nicht laenger als das
    Video sein."""
    vdur = video.get("duration") or 0
    want = _norm_words(_song_query(video.get("title", "")))
    best = None
    for r in results:
        dur = r.get("duration") or 0
        if not dur:
            continue
        artist = _norm_words(r.get("artist") or "")
        if artist and not (artist & (want | _norm_words(video.get("uploader") or ""))):
            continue
        if LIVE_RE.search(r.get("title", "")):
            continue
        if vdur and (dur > vdur + 3 or dur < vdur * 0.5):
            continue
        # Der Songtitel muss im Videotitel stecken (Kuenstler steht beim Topic-Kanal)
        got = _norm_words(r.get("title", ""))
        if not got or not got <= want:
            continue
        # Ein Remix-Video bekommt nicht das Original und umgekehrt
        if (want & _VERSION_WORDS) - got:
            continue
        if best is None or abs(dur - vdur) < abs((best.get("duration") or 0) - vdur):
            best = r
    return best


def _needs_video_choice(video: dict, song: dict | None) -> bool:
    """Nachfragen, wenn es eine Song-Version gibt und das Video erkennbar eins ist:
    "Official Video" im Titel oder deutlich laenger als der Song."""
    if not song:
        return False
    if "- topic" in (video.get("uploader") or "").lower():
        return False
    title = video.get("title", "")
    if _MV_TITLE_RE.search(title):
        return True
    if _AUDIO_TITLE_RE.search(title):
        return False
    return (video.get("duration") or 0) - (song.get("duration") or 0) >= _MV_EXTRA_SEC


async def _probe_video(url: str) -> dict | None:
    try:
        pr = await asyncio.create_subprocess_exec(
            *core._yt("--no-playlist", "--skip-download", "-j", "--quiet", "--no-warnings", url),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=_NO_WINDOW)
        out, _ = await asyncio.wait_for(pr.communicate(), timeout=25)
        item = json.loads(out.decode("utf-8", errors="replace").strip().splitlines()[0])
        artists = item.get("artists") or []
        return {"url": url, "title": item.get("track") or item.get("title") or "",
                "artist": item.get("artist") or ", ".join(artists[:2]) or "",
                "uploader": item.get("uploader") or item.get("channel") or "",
                "duration": item.get("duration") or 0}
    except Exception:
        return None


# Ergebnisse der Song-Suche merken: dieselbe Playlist nochmal einfuegen oder
# eine verfolgte Playlist pruefen sucht dann nicht alles neu.
YTM_CACHE_FILE = core.BASE_DIR / "ytm_search_cache.json"
_YTM_CACHE_TTL = 14 * 86400
_YTM_CACHE_MAX = 6000
_ytm_cache: dict | None = None
_ytm_cache_dirty = False

def _ytm_cache_dict() -> dict:
    global _ytm_cache
    if _ytm_cache is None:
        raw = store._load_json(YTM_CACHE_FILE, {})
        now = time.time()
        _ytm_cache = {k: v for k, v in (raw.items() if isinstance(raw, dict) else [])
                      if isinstance(v, dict) and now - v.get("ts", 0) < _YTM_CACHE_TTL}
    return _ytm_cache

def _save_ytm_cache():
    global _ytm_cache_dirty
    if not _ytm_cache_dirty or _ytm_cache is None:
        return
    if len(_ytm_cache) > _YTM_CACHE_MAX:
        for k, _ in sorted(_ytm_cache.items(), key=lambda kv: kv[1].get("ts", 0))[:len(_ytm_cache) - _YTM_CACHE_MAX]:
            _ytm_cache.pop(k, None)
    try:
        store._save_json(YTM_CACHE_FILE, _ytm_cache)
        _ytm_cache_dirty = False
    except Exception:
        pass

async def _ytm_song_search(query: str, n: int = 3) -> list[dict]:
    """Song-Suche von YouTube Music mit Laenge und Kuenstler (siehe _ytm_songs)."""
    global _ytm_cache_dirty
    key = f"{n}|{query.strip().lower()}"
    cache = _ytm_cache_dict()
    hit = cache.get(key)
    if hit and time.time() - hit.get("ts", 0) < _YTM_CACHE_TTL:
        return [dict(r) for r in hit.get("r") or []]
    res = await _ytm_songs(query, n, details=True)
    if res:          # leer kann auch "kein Netz" heissen — nicht merken
        keep = ("url", "title", "artist", "uploader", "duration", "kind", "_score")
        cache[key] = {"ts": int(time.time()), "r": [{k: r.get(k) for k in keep if k in r} for r in res]}
        _ytm_cache_dirty = True
    return res


async def _songs_and_videos(query: str, n_songs: int, n_videos: int):
    """Beide Suchen parallel: Studio-Versionen (ohne Details) und YouTube."""
    base_args = ["--flat-playlist", "-j", "--no-playlist", "--quiet", "--no-warnings"]
    songs, videos = await asyncio.gather(
        _ytm_songs(query, n_songs, details=False),
        _run_search_cmd(core._yt(f"ytsearch{n_videos}:{query}", *base_args)))
    videos = [r for r in videos if not _is_unwanted_result(r)]
    videos.sort(key=lambda r: r["_score"], reverse=True)
    return songs, videos


def _public(results: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]


async def do_search(query: str, ws: WebSocket):
    async def send(results, final):
        try:
            await ws.send_text(json.dumps({"type": "search_results", "query": query,
                                           "results": _public(results), "final": final}))
        except Exception:
            pass

    songs, videos = await _songs_and_videos(query, 6, 10)
    complete = all(r.get("duration") and r.get("artist") for r in songs)
    if not songs or complete:          # schnelle Suche: alles schon da
        songs = [r for r in songs if not _is_unwanted_result(r) and not LIVE_RE.search(r.get("title", ""))]
        await send(_merge_songs_first(songs, videos), True)
        return
    # Erst zeigen, dann Laenge und Kuenstler der Songs nachliefern
    await send(_merge_songs_first(songs, videos), False)
    await _ytm_fill_details(songs)
    songs = [r for r in songs if not _is_unwanted_result(r) and not LIVE_RE.search(r.get("title", ""))]
    await send(_merge_songs_first(songs, videos), True)
