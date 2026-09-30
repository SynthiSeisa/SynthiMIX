"""Radio: haelt die Warteschlange mit passenden Titeln gefuellt — per Knopf
dauerhaft oder erst am Ende der Warteschlange (Queue-Ende: Radio, frueher
Auto-Mix).

Die Richtung kommt aus den letzten Titeln der Warteschlange, nicht nur aus
dem laufenden: der zuletzt eingereihte zaehlt am meisten, ein einzelner
Ausreisser kippt die Stimmung nicht. Gewaehlt wird wie beim harmonischen
Sortieren (Tonart, Tempo, Energie, alle 4-6 Titel ein Energie-Schub), dazu
Aehnlichkeit von Last.fm (Titel und Kuenstler), Genre und Tempo der Richtung.
Meist aus der eigenen Bibliothek; jeder fuenfte Titel ist neu und wird im
Hintergrund von YouTube Music geladen.
"""
import asyncio
import json
import math
import os
import random
import re
import time
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from .core import _state
from . import core, download, keys, media, search, store, tags

NEW_EVERY = 5            # jeder 5. Radio-Titel ist neu (aus dem Netz)
_AHEAD = 3               # so viele ungespielte Titel haelt das Radio vorraetig
_SEEDS = 5               # Richtung aus so vielen Titeln
_BLOCK_PLAYS = 150       # die zuletzt gespielten Titel kommen nicht wieder ...
_BLOCK_SEC = 8 * 3600    # ... und nichts aus den letzten 8 Stunden
_TOP = 12                # gewuerfelt wird unter den besten
_LFM_TTL = 6 * 3600

# ── Kuenstler-Pool: waechst mit jeder YouTube-Music-Suche (Radio ohne Last.fm) ─
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


# ── Titel vergleichbar machen ────────────────────────────────────────────────
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\+|/|\bx\b|\band\b|\bvs\.?(?=\s)|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b)\s*", re.I)
_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
# Einzelspuren, Samples, Gesangsaufnahmen: keine Lieder fuers Radio
_NOT_A_SONG = re.compile(r"\b(stems?|instrumental|karaoke|a[\s-]?cap+ella|sample[\s-]*pack|drums|drm|perc|fx|"
                         r"unprocessed|take\s*\d+|(?:full|lead|wet|dry)\s+vocals?|kick\s*drum|no\s+kick|looperman)\b|_instrum|[\s-](?:wet|dry)\s*$|"
                         # Stem-Trennung: "Titel_bass", "Titel (vocals)"
                         r"(?:_|[(\[])(?:bass|drums|other|vocals|piano|guitar|no[\s_]vocals)[)\]]?\s*$",
                         re.IGNORECASE)

def _split(lt: dict) -> tuple[str, str]:
    """(Kuenstler, Titel) — aus den Tags oder aus "Kuenstler - Titel"."""
    title = (lt.get("title") or lt.get("name") or "").strip()
    artist = (lt.get("artist") or "").strip()
    if " - " in title:
        a, t = (x.strip() for x in title.split(" - ", 1))
        if not artist:
            artist, title = a, t
        elif _artists(a) & _artists(artist):
            title = t
    return artist, title

def _artists(artist: str) -> frozenset:
    return frozenset(n for n in (search._dupe_norm(x) for x in _ARTIST_SPLIT.split(artist or "")) if n)

def _tnorm(title: str) -> str:
    """Titel ohne Klammern (Remix, Radio Edit …): dasselbe Lied in anderer Fassung zaehlt gleich."""
    return search._dupe_norm(_BRACKETS.sub("", title or "")) or search._dupe_norm(title)

def _feat(lt: dict) -> dict:
    a, t = _split(lt)
    return {"lt": lt, "a": a, "t": t, "artists": _artists(a), "title": _tnorm(t),
            "folder": os.path.dirname(lt.get("path") or "").lower(),
            "genre": tags._genre_map(lt.get("genre") or ""), "pre": keys._pre(lt)}

def _same_song(index: dict, artists: frozenset, title: str) -> bool:
    """Liegt dieses Lied (Titel + ein gemeinsamer Kuenstler) im Index {titel: [kuenstler]}?"""
    return any(not arts or not artists or arts & artists for arts in index.get(title, ()))


# ── Last.fm ──────────────────────────────────────────────────────────────────
_lfm_cache: dict[str, tuple[float, list]] = {}

def _lfm_sync(params: dict, api_key: str) -> dict:
    q = urllib.parse.urlencode({**params, "api_key": api_key, "format": "json", "autocorrect": 1})
    try:
        req = urllib.request.Request("https://ws.audioscrobbler.com/2.0/?" + q,
                                     headers={"User-Agent": "SynthiMIX"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}

def _lfm_name(a) -> str:
    return str(a.get("name", "") if isinstance(a, dict) else a or "")

async def _lfm(kind: str, artist: str, title: str = "") -> list[tuple[str, str, float]]:
    """Aehnliche Titel (kind="track") oder Kuenstler ("artist") als
    [(kuenstler, titel, match 0..1)], je 6 Stunden gemerkt."""
    api_key = (_state.get("lastfm_api_key") or "").strip()
    if not api_key or not artist:
        return []
    ck = f"{kind}|{artist.lower()}|{title.lower()}"
    hit = _lfm_cache.get(ck)
    if hit and time.time() - hit[0] < _LFM_TTL:
        return hit[1]
    loop = asyncio.get_running_loop()
    if kind == "track":
        d = await loop.run_in_executor(None, _lfm_sync, {"method": "track.getSimilar", "artist": artist,
                                                         "track": title, "limit": 60}, api_key)
        items = (d.get("similartracks") or {}).get("track", []) if isinstance(d, dict) else []
        items = [items] if isinstance(items, dict) else items
        out = [(_lfm_name(x.get("artist")), str(x.get("name", "")), float(x.get("match") or 0)) for x in items]
    else:
        d = await loop.run_in_executor(None, _lfm_sync, {"method": "artist.getSimilar", "artist": artist,
                                                         "limit": 60}, api_key)
        items = (d.get("similarartists") or {}).get("artist", []) if isinstance(d, dict) else []
        items = [items] if isinstance(items, dict) else items
        out = [(str(x.get("name", "")), "", float(x.get("match") or 0)) for x in items]
    if d:                                  # nur Antworten merken, keine Netzfehler
        _lfm_cache[ck] = (time.time(), out)
        if len(_lfm_cache) > 400:
            _lfm_cache.pop(next(iter(_lfm_cache)))
    return out


# ── Richtung ─────────────────────────────────────────────────────────────────
def _seed_tracks() -> list[dict]:
    """Die letzten Titel, auf die der naechste folgt (aeltester zuerst), mit den
    Angaben aus der Bibliothek. Reicht die Warteschlange nicht, zaehlt der Verlauf."""
    lib = {lt.get("path"): lt for lt in _state.get("library", [])}
    seeds = [{**t, **lib.get(t.get("path"), {})} for t in _state.get("queue", [])[-_SEEDS:] if t.get("path")]
    if len(seeds) < _SEEDS:
        have = {s.get("path") for s in seeds}
        extra = []
        for h in _state.get("play_log", [])[:30]:
            p = h.get("path")
            if p in lib and p not in have:
                have.add(p)
                extra.append(lib[p])
            if len(extra) + len(seeds) >= _SEEDS:
                break
        seeds = list(reversed(extra)) + seeds
    return seeds

def _tempo_of(feats: list[dict]) -> float:
    """Tempo der Richtung: Median der bekannten (halbes/doppeltes Tempo gefaltet)."""
    ref = next((f["pre"][1] for f in reversed(feats) if f["pre"][1]), 0.0)
    if not ref:
        return 0.0
    vals = sorted(min((b * m for m in (0.5, 1.0, 2.0)), key=lambda v: abs(v - ref))
                  for b in (f["pre"][1] for f in feats) if b)
    return vals[len(vals) // 2]

async def _profile(seeds: list[dict]) -> dict:
    n = len(seeds)
    w = [0.5 ** (n - 1 - i) for i in range(n)]        # der neueste zaehlt am meisten
    feats = [_feat(s) for s in seeds]
    genres: dict[str, float] = {}
    for f, wi in zip(feats, w):
        if f["genre"]:
            genres[f["genre"]] = genres.get(f["genre"], 0.0) + wi
    tot = sum(genres.values())
    prof = {
        "prev":           feats[-1]["pre"],
        "tempo":          _tempo_of(feats),
        "genres":         {g: v / tot for g, v in genres.items()} if tot else {},
        "seed_artists":   frozenset().union(*(f["artists"] for f in feats)),
        "recent_artists": frozenset().union(*(f["artists"] for f in feats[-2:])),
        "seed_titles":    {},
        "sim_t":          {},    # titel -> [(kuenstler, staerke, "Kuenstler – Titel")]
        "sim_a":          {},    # kuenstler -> (staerke, Seed-Kuenstler)
        "folders":        {},    # Ordner -> Anteil (die eigene Sortierung, oft nach Genre)
    }
    for f, wi in zip(feats, w):
        if f["folder"]:
            prof["folders"][f["folder"]] = prof["folders"].get(f["folder"], 0.0) + wi / sum(w)
    for f in feats:
        prof["seed_titles"].setdefault(f["title"], []).append(f["artists"])
    jobs, labels = [], []
    for f, wi in list(zip(feats, w))[-2:]:
        if f["a"] and f["t"]:
            jobs.append(_lfm("track", _ARTIST_SPLIT.split(f["a"])[0], _BRACKETS.sub("", f["t"]) or f["t"]))
            labels.append(("track", wi, f"{f['a']} – {f['t']}"))
    seen: set[str] = set()
    for f, wi in reversed(list(zip(feats, w))[-3:]):
        a0 = _ARTIST_SPLIT.split(f["a"])[0].strip() if f["a"] else ""
        if a0 and a0.lower() not in seen:
            seen.add(a0.lower())
            jobs.append(_lfm("artist", a0))
            labels.append(("artist", wi, a0))
    for (kind, wi, label), out in zip(labels, await asyncio.gather(*jobs)):
        for a, t, m in out:
            strength = wi * (0.4 + 0.6 * max(0.0, min(1.0, m)))
            if kind == "track":
                prof["sim_t"].setdefault(_tnorm(t), []).append((_artists(a), strength, label))
            else:
                for an in _artists(a):
                    if strength > prof["sim_a"].get(an, (0.0, ""))[0]:
                        prof["sim_a"][an] = (strength, label)
    return prof

def _blocked() -> tuple[set, dict]:
    """Zuletzt gespielt: Pfade und {titel: [kuenstler]} (andere Fassungen desselben Lieds)."""
    now = time.time()
    paths: set[str] = set()
    titles: dict[str, list] = {}
    for i, h in enumerate(_state.get("play_log", [])):
        if i >= _BLOCK_PLAYS and now - float(h.get("played_at") or 0) > _BLOCK_SEC:
            break
        if h.get("path"):
            paths.add(h["path"])
        a, t = _split({"title": h.get("title", ""), "artist": h.get("artist", "")})
        if t:
            titles.setdefault(_tnorm(t), []).append(_artists(a))
    for q in _state.get("queue", []):
        a, t = _split(q)
        if t:
            titles.setdefault(_tnorm(t), []).append(_artists(a))
    return paths, titles

def _camelot_txt(pre: tuple) -> str:
    c = pre[0]
    return f"{c[0]}{c[1]}" if c else ""

def _pick_sync(lib: list[dict], prof: dict, block_paths: set, block_titles: dict,
               queue_paths: set, boost_due: bool, rng: random.Random):
    """Bester Titel aus der Bibliothek (gewuerfelt unter den besten) oder None.
    Liefert (Titel, Grund, Tonart-Schritt, Energie-Unterschied)."""
    cands = []
    for lt in lib:
        p = lt.get("path")
        if not p or p in queue_paths or p in block_paths or lt.get("missing"):
            continue
        d = float(lt.get("duration_sec") or 0)
        if d < 60 or d > 900:                  # Samples, FX, DJ-Sets
            continue
        f = _feat(lt)
        if not f["title"] or _NOT_A_SONG.search(lt.get("title") or "") \
                or _same_song(block_titles, f["artists"], f["title"]):
            continue
        cands.append(f)
    if not cands:
        return None
    # Nicht zweimal hintereinander derselbe Kuenstler (wenn es anders geht)
    pool = [f for f in cands if not (f["artists"] & prof["recent_artists"])] or cands
    scored = []
    for f in pool:
        s, step, de = keys._trans_score(prof["prev"], f["pre"], boost_due)
        why = ""
        st, st_lbl = 0.0, ""
        for arts, strength, label in prof["sim_t"].get(f["title"], ()):
            if (not arts or arts & f["artists"]) and strength > st:
                st, st_lbl = strength, label
        if st:
            s += 3.0 * st
            why = f"ähnlich zu {st_lbl}"
        sa, sa_lbl = max((prof["sim_a"].get(a, (0.0, "")) for a in f["artists"]), default=(0.0, ""))
        if sa:
            s += 2.0 * sa
            why = why or f"ähnlich wie {sa_lbl}"
        if f["artists"] & prof["seed_artists"]:
            s += 0.6
        s += 1.0 * prof["folders"].get(f["folder"], 0.0)
        if not f["a"] and not f["lt"].get("key") and not f["genre"]:
            s -= 1.0                           # ohne jede Angabe: oft Schnipsel aus Projekten
        if prof["genres"]:
            share = prof["genres"].get(f["genre"], 0.0) if f["genre"] else None
            if share is None:
                s -= 0.3
            elif share:
                s += 1.5 * share
                why = why or f["genre"]
            else:
                s -= 1.5                       # anderes Genre als die Richtung
        # Tempo gegen die Richtung, nicht nur gegen den letzten Titel
        gap = keys._tempo_gap(prof["tempo"], f["pre"][1])
        if gap > 0.08:
            s -= min(2.0, (gap - 0.08) * 20)
        s += rng.random() * 0.3                # Gleichstand: nicht immer dieselben
        scored.append((s, f, step, de, why))
    scored.sort(key=lambda z: -z[0])
    top = scored[:_TOP]
    s0 = top[0][0]
    s, f, step, de, why = rng.choices(top, weights=[math.exp((z[0] - s0) / 0.35) for z in top])[0]
    extra = [x for x in (_camelot_txt(f["pre"]), f"{f['pre'][1]:.0f} BPM" if f["pre"][1] else "") if x]
    if boost_due and (step in keys._BOOST_KEY_SCORE or (f["lt"].get("energy") and de >= 0.6)):
        extra.append("Energie-Schub")
    return f["lt"], " · ".join([why] + extra if why else extra), step, de


# ── Anhaengen ────────────────────────────────────────────────────────────────
_fill_running = False
_discover_running = False
_since_new = 0
_since_boost = 0
_boost_at = random.randint(4, 6)
_rng = random.Random()

def _remaining() -> int:
    ci, q = _state.get("current_idx", -1), _state.get("queue", [])
    return sum(1 for t in q[max(0, ci + 1):] if not t.get("played", False))

def _queue_entry(lt: dict) -> dict:
    return {
        "path":         lt.get("path", ""),
        "title":        lt.get("title") or lt.get("name", ""),
        "duration_sec": float(lt.get("duration_sec", 0) or 0),
        "lufs":         float(lt.get("lufs", -99.0) or -99.0),
        "bpm":          int(lt.get("bpm", 0) or 0),
        "bitrate_kbps": int(lt.get("bitrate_kbps", 0) or 0),
        "played":       False,
    }

async def _fill(count: int):
    """Haengt bis zu count passende Titel an; jeder NEW_EVERY-te ist neu."""
    global _fill_running, _since_new, _since_boost, _boost_at
    if _fill_running:
        return
    _fill_running = True
    added: list[tuple[str, str]] = []
    try:
        loop = asyncio.get_running_loop()
        for _ in range(count):
            if _since_new >= NEW_EVERY - 1 and not _discover_running:
                _since_new = 0
                core.spawn(_discover())
            seeds = _seed_tracks()
            if not seeds:
                break
            prof = await _profile(seeds)
            block_paths, block_titles = _blocked()
            queue_paths = {t.get("path") for t in _state.get("queue", [])}
            boost_due = _since_boost >= _boost_at
            pick = await loop.run_in_executor(None, _pick_sync, list(_state.get("library", [])), prof,
                                              block_paths, block_titles, queue_paths, boost_due, _rng)
            if not pick:
                core.spawn(_discover())          # Bibliothek ausgeschoepft: dann eben Neues
                break
            lt, why, step, de = pick
            _state["queue"].append(_queue_entry(lt))
            added.append((lt.get("title") or "", why))
            _since_new += 1
            _since_boost += 1
            if boost_due and (step in keys._BOOST_KEY_SCORE or de >= 0.6):
                _since_boost, _boost_at = 0, _rng.randint(4, 6)
        if added:
            store.save_queue()
            await core.push_queue()
            title, why = added[-1]
            await core.broadcast({"type": "radio_added", "title": title, "similar_to": why})
    finally:
        _fill_running = False

async def _check_radio_queue():
    """Radio an: immer _AHEAD Titel vorraetig halten."""
    if not _state.get("radio_enabled"):
        return
    need = _AHEAD - _remaining()
    if need > 0:
        core.spawn(_fill(need))

async def _at_queue_end():
    """Letzter Titel laeuft (Queue-Ende: Radio): einen passenden anhaengen."""
    if _state.get("radio_enabled"):
        await _check_radio_queue()
    elif _remaining() == 0:
        await _fill(1)


# ── Neues aus dem Netz ───────────────────────────────────────────────────────
def _acceptable(r: dict, existing_urls: set) -> bool:
    """Ein einzelnes Lied in Studiofassung: keine Mixe, Live-Aufnahmen, Musikvideos, Playlists."""
    u, title = r.get("url", ""), r.get("title", "")
    if not u or u in existing_urls or download._is_playlist(u):
        return False
    if (search.MIX_RE.search(title) or search.NON_MUSIC_RE.search(title)
            or search.LIVE_RE.search(title) or search._MV_TITLE_RE.search(title)):
        return False
    dur = r.get("duration") or 0
    return not (dur > 480 or (dur and dur < 40))

def _known_index() -> dict:
    """{titel: [kuenstler]} fuer alles, was schon da ist oder gerade lief."""
    idx: dict[str, list] = {}
    for lt in _state.get("library", []):
        a, t = _split(lt)
        if t:
            idx.setdefault(_tnorm(t), []).append(_artists(a))
    for k, v in _blocked()[1].items():
        idx.setdefault(k, []).extend(v)
    return idx

def _matches(r: dict, artist: str, title: str) -> bool:
    """Ist das Suchergebnis wirklich das gesuchte Lied?"""
    ra = _extract_ytm_artist(r) or ""
    a, t = _split({"title": r.get("title", ""), "artist": ra})
    want, got = _tnorm(title), _tnorm(t)
    if not want or not got:
        return False
    same_title = want in got or got in want or SequenceMatcher(None, want, got).ratio() >= 0.75
    have = search._dupe_norm(f"{a} {r.get('uploader') or ''} {r.get('title') or ''}")
    return same_title and any(x in have for x in _artists(artist))

async def _discover():
    """Einen neuen, passenden Titel suchen, laden und anhaengen. Mit Last.fm:
    ein aehnlicher Titel, der noch nicht in der Bibliothek ist. Ohne (oder
    wenn nichts passt): Songs der Kuenstler aus der Richtung und aus dem
    Kuenstler-Pool von YouTube Music."""
    global _discover_running
    if _discover_running:
        return
    _discover_running = True
    try:
        seeds = _seed_tracks()
        if not seeds:
            return
        feats = [_feat(s) for s in seeds]
        known = _known_index()
        existing_urls = {h.get("url", "") for h in _state.get("history", [])}
        found: tuple[dict, str] | None = None

        opts: list[tuple[str, str, float, str]] = []
        for f in feats[-2:]:
            if f["a"] and f["t"]:
                for a, t, m in await _lfm("track", _ARTIST_SPLIT.split(f["a"])[0], _BRACKETS.sub("", f["t"]) or f["t"]):
                    if a and t and not _same_song(known, _artists(a), _tnorm(t)):
                        opts.append((a, t, m, f"{f['a']} – {f['t']}"))
        opts.sort(key=lambda o: -o[2])
        tries = opts[:15]
        _rng.shuffle(tries)
        for a, t, _m, label in tries[:3]:
            for r in await search._ytm_songs(f"{a} {t}", 5, details=True):
                if _acceptable(r, existing_urls) and _matches(r, a, t):
                    found = (r, f"neu · ähnlich zu {label}")
                    break
            if found:
                break

        if not found:
            artists = [x for x in (_ARTIST_SPLIT.split(f["a"])[0].strip() for f in feats[-3:] if f["a"]) if x]
            queries = ([_rng.choice(artists)] if artists else []) + \
                      ([_rng.choice(_similar_artist_pool)] if _similar_artist_pool else [])
            for q in queries:
                batch = await search._ytm_songs(q, 8, details=True)
                _ingest_similar_artists(batch, q)
                for r in sorted(batch, key=lambda r: -r.get("_score", 0)):
                    if not _acceptable(r, existing_urls):
                        continue
                    a, t = _split({"title": r.get("title", ""), "artist": _extract_ytm_artist(r) or ""})
                    if t and not _same_song(known, _artists(a), _tnorm(t)):
                        found = (r, f"neu · von {q}" if q in artists else f"neu · wie {q}")
                        break
                if found:
                    break

        if not found:
            await core.broadcast({"type": "automix_status", "text": "Radio: nichts Neues gefunden"})
            await asyncio.sleep(4)
            await core.broadcast({"type": "automix_status", "text": ""})
            return
        r, why = found
        await core.broadcast({"type": "automix_status", "text": f"⬇ Radio lädt: {r.get('title', '')[:40]}…"})
        path = await download.run_download(r["url"], "mp3-best")
        if not path or not os.path.exists(path):
            await core.broadcast({"type": "automix_status", "text": "⚠ Radio: Download fehlgeschlagen"})
            await asyncio.sleep(4)
            return
        probe = await asyncio.get_running_loop().run_in_executor(None, media._probe_sync, path)
        track = {
            "path":         path,
            "title":        probe["title"] or Path(path).stem,
            "duration_sec": probe["duration_sec"],
            "lufs":         -99.0,
            "bpm":          probe["bpm"],
            "bitrate_kbps": probe["bitrate_kbps"],
            "played":       False,
            "radio_new":    True,
        }
        _state["queue"].append(track)
        store.save_queue()
        await core.push_queue()
        await core.broadcast({"type": "radio_added", "title": track["title"], "similar_to": why})
    finally:
        _discover_running = False
        await core.broadcast({"type": "automix_status", "text": ""})
