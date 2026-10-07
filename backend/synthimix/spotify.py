"""Spotify-Links: Titelliste von Spotify lesen, den Ton ueber die eigene
Song-Suche (YouTube Music) und den eigenen Download holen.

Frueher lief das komplett ueber spotdl. Dessen Suche und Download brachen
10/2026 bei jedem Titel ab ("YouTube Music returned no usable results",
"YT-DLP download error") — in der App stand dann minutenlang "0 / 88".
Spotify selbst liefert nur die Liste (Titel, Kuenstler, Laenge); die steht
ohne Zugangsdaten in der Einbett-Seite (open.spotify.com/embed/…).
"""
import asyncio
import json
import re
import time
import urllib.request as _req

from . import search

_LINK_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?(?:embed/)?(track|album|playlist)/([A-Za-z0-9]{10,})")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
# Die Einbett-Seite zeigt hoechstens so viele Titel einer Playlist
EMBED_MAX = 100
_PARALLEL = 6


def parse(url: str) -> tuple[str, str] | None:
    """("track" | "album" | "playlist", ID) oder None."""
    m = _LINK_RE.search(url or "")
    return (m.group(1), m.group(2)) if m else None


def is_list(url: str) -> bool:
    p = parse(url)
    return bool(p) and p[0] in ("album", "playlist")


def _expand_sync(url: str) -> str:
    """Kurzlink (spotify.link/…) aufloesen."""
    if parse(url) or "spotify.link" not in url:
        return url
    try:
        with _req.urlopen(_req.Request(url, headers={"User-Agent": _UA}), timeout=15) as r:
            final = r.geturl()
            if parse(final):
                return final
            m = _LINK_RE.search(r.read(400_000).decode("utf-8", errors="replace"))
            return "https://" + m.group(0) if m else url
    except Exception:
        return url


def _parse_embed(html: str) -> dict | None:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        ent = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError, ValueError):
        return None

    def names(s) -> str:
        return ", ".join(p.strip() for p in (s or "").replace("\xa0", " ").split(",") if p.strip())

    name = (ent.get("name") or ent.get("title") or "").strip()
    rows = ent.get("trackList")
    if rows is None:                                    # einzelner Titel
        artist = ", ".join(a.get("name", "") for a in ent.get("artists") or [] if a.get("name"))
        rows = [{"title": name, "subtitle": artist, "duration": ent.get("duration")}]
    tracks = []
    for t in rows:
        title = (t.get("title") or "").strip()
        if title and (t.get("entityType") or "track") == "track":
            tracks.append({"title": title, "artist": names(t.get("subtitle")),
                           "duration": round((t.get("duration") or 0) / 1000)})
    return {"title": name, "tracks": tracks}


def _fetch_sync(url: str) -> dict | None:
    """{title, tracks: [{title, artist, duration}], kind} oder None (nicht lesbar)."""
    url = _expand_sync(url)
    p = parse(url)
    if not p:
        return None
    data = None
    for attempt in (1, 2):                              # ein zweiter Versuch: der Abruf fiel vereinzelt aus
        try:
            req = _req.Request(f"https://open.spotify.com/embed/{p[0]}/{p[1]}", headers={"User-Agent": _UA})
            with _req.urlopen(req, timeout=20) as r:
                data = _parse_embed(r.read().decode("utf-8", errors="replace"))
        except Exception as e:
            print(f"[spotify] Liste nicht lesbar (Versuch {attempt}): {e}", flush=True)
        if data and data["tracks"]:
            break
        if attempt == 1:
            time.sleep(1.5)
    if data:
        data["kind"] = p[0]
    return data


def _first_artist(artist: str) -> str:
    return (artist or "").split(",")[0].strip()


_EXACT_SEC = 7      # so nah an Spotifys Laenge gilt ein Treffer als dieselbe Fassung


def _words(t: str) -> set[str]:
    # "Lieb's" (YouTube Music) und "Liebs" (Spotify) sind dasselbe Wort
    return search._norm_words(re.sub(r"['’`´]", "", t or ""))


def _pick(track: dict, results: list[dict], exact: bool) -> dict | None:
    """Der Treffer, der zu Titel, Kuenstler und Laenge von Spotify passt.
    exact: Laenge fast gleich; sonst darf sie abweichen (andere Pressung)."""
    dur = track["duration"] or 0
    artists = _words(track["artist"])
    want = _words(track["title"]) | artists
    live = bool(search.LIVE_RE.search(track["title"]) or re.search(r"\blive\b", track["title"], re.I))
    best = None
    for r in results:
        rd = r.get("duration") or 0
        if not rd:
            continue
        if dur and (abs(rd - dur) > _EXACT_SEC if exact else (rd > dur * 1.6 + 5 or rd < dur * 0.5)):
            continue
        ra = _words(r.get("artist") or "")
        if ra and artists and not (ra & artists):
            continue
        if not live and search.LIVE_RE.search(r.get("title", "")):
            continue
        got = _words(r.get("title", ""))
        # Der Songtitel muss passen; "Instrumental", "Remix" nur, wenn Spotify es nennt
        if not got or not got <= want or (want & search._VERSION_WORDS) - got:
            continue
        if best is None or abs(rd - dur) < abs((best.get("duration") or 0) - dur):
            best = r
    return best


async def _find(track: dict) -> dict | None:
    """Den Titel bei YouTube Music suchen: erst mit allen Kuenstlern, dann nur
    mit dem ersten, zuletzt ohne Klammerzusatz ("(Original)", "- Remastered").
    Zuerst zaehlt nur ein Treffer mit Spotifys Laenge, dann der naechstbeste."""
    title, artist = track["title"], track["artist"]
    plain = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]|\s+-\s+.*$", "", title).strip()
    queries = []
    for q in (f"{artist} {title}", f"{_first_artist(artist)} {title}", f"{_first_artist(artist)} {plain}"):
        q = re.sub(r"\s+", " ", q.replace(",", " ")).strip()
        if q and q.lower() not in (x.lower() for x in queries):
            queries.append(q)
    results = []
    for q in queries:
        res = await search._ytm_song_search(q)
        results.append(res)
        song = _pick(track, res, True)
        if song:
            return song
    for res in results:
        song = _pick(track, res, False)
        if song:
            return song
    return None


# Ergebnis der letzten Abfrage je Link: was Spotify nannte, sich aber nicht fand
not_found: dict[str, list[str]] = {}
truncated: dict[str, bool] = {}


async def resolve(url: str, progress=None) -> tuple[list[dict], str]:
    """Titelliste eines Spotify-Links als ladbare Eintraege (wie eine
    YouTube-Playlist): ([{url, title, uploader, duration, replaced}], Name)."""
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _fetch_sync, url)
    if not data or not data["tracks"]:
        return [], (data or {}).get("title", "")
    tracks = data["tracks"]
    total, done = len(tracks), 0
    if progress:
        await progress("search", 0, total)
    sem = asyncio.Semaphore(_PARALLEL)
    found: list = [None] * total

    async def one(i, t):
        nonlocal done
        async with sem:
            try:
                found[i] = await _find(t)
            except asyncio.CancelledError:
                raise
            except Exception:
                found[i] = None
        done += 1
        if progress:
            await progress("search", done, total)

    try:
        await asyncio.gather(*(one(i, t) for i, t in enumerate(tracks)))
    finally:
        search._save_ytm_cache()
    entries, missing = [], []
    for t, s in zip(tracks, found):
        name = f'{t["artist"]} - {t["title"]}' if t["artist"] else t["title"]
        if not s:
            missing.append(name)
            continue
        entries.append({"url": s["url"], "title": name, "replaced": False,
                        "uploader": t["artist"], "duration": s.get("duration") or t["duration"]})
    not_found[url] = missing
    truncated[url] = data["kind"] == "playlist" and total >= EMBED_MAX
    if missing:
        print(f"[spotify] {len(missing)} von {total} nicht gefunden: " + "; ".join(missing[:20]), flush=True)
    return entries, data["title"]
