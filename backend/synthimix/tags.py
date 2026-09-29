"""Titel aufraeumen, Genres, Fingerprint-Erkennung (AcoustID), Dienste pruefen."""
import asyncio
import base64
import json
import os
import re
import time
from difflib import SequenceMatcher
from fastapi import WebSocket
from pathlib import Path
from .core import _NO_WINDOW, _state
from . import core, search, store

# ── Titel aufraeumen ─────────────────────────────────────────────────────────
# Video-Zusaetze, Kanal-/Label-Angaben und Genre-Klammern raus, "Kuenstler -
# Titel" auf Titel- und Kuenstler-Tag verteilen. Remix, Edit, VIP, Bootleg,
# feat., Acapella und "Original Mix" bleiben — das sind echte Angaben.
# Geschrieben werden nur Titel- und Kuenstler-Tag, nie der Dateiname
# (rekordbox wuerde die Datei sonst nicht mehr finden).
_title_cancel = False
_TITLE_NOISE_WORDS = (r"official|offizielle[sr]?|oficial|video|videoclip|musikvideo|lyrics?|audio|visuali[sz]er|"
                      r"\bhd\b|\bhq\b|\b4k\b|free\s+(?:dl|download)|out\s+now|explicit|\bclean\b|premiere|exclusive|"
                      r"records|recordings|release|\bncs\b|monstercat|\bukf\b|liquicity|audius")
_TITLE_NOISE_BRACKET_RE = re.compile(r"\s*[\(\[\{【][^\)\]\}】]*(?:" + _TITLE_NOISE_WORDS + r")[^\)\]\}】]*[\)\]\}】]", re.IGNORECASE)
_TITLE_NOISE_BARE_RE = re.compile(
    r"\s*[-–—]?\s*\b(?:official\s+(?:hd\s+)?(?:music\s+)?video(?:\s+hd)?|official\s+audio|official\s+lyric\s+video|"
    r"lyric\s+video|offizielles\s+(?:musik)?video|free\s+(?:dl|download))\b", re.IGNORECASE)
_TITLE_KEEP_RE = re.compile(r"remix|mix\b|edit|vip|bootleg|flip|rework|feat|ft\.|acapella|a\s*capella|instrumental|extended|cover|version|live", re.IGNORECASE)
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]+")
# yt-dlp ersetzt in Dateinamen verbotene Zeichen durch aehnlich aussehende
_YTDLP_CHARS = str.maketrans({"⧸": "/", "⧹": "\\", "＂": '"', "：": ":", "？": "?", "＊": "*", "＜": "<", "＞": ">", "｜": "|"})
_TITLE_TRAIL_RE = re.compile(r"(?:\s+(?:audio|official|hd|hq|4k|lyrics?|video))+\s*$", re.IGNORECASE)


def _clean_title_local(title: str, artist: str) -> tuple[str, str]:
    """(Kuenstler, Titel) nach den lokalen Regeln."""
    t = (title or "").translate(_YTDLP_CHARS)
    artist = (artist or "").translate(_YTDLP_CHARS)
    t = t.split(" | ")[0]                                     # "Song | Kanal/Serie"
    if t.count("_") >= 2 and t.count(" ") < t.count("_"):
        t = t.replace("_", " ")
    t = _EMOJI_RE.sub("", t)
    t = re.sub(r"^\s*\[[^\]]*\]\s*", lambda m: "" if _genre_map(m.group(0)) or re.search(_TITLE_NOISE_WORDS, m.group(0), re.I) else m.group(0), t)
    t = _TITLE_NOISE_BRACKET_RE.sub("", t)
    # Klammern, die nur ein Genre nennen: "(Rock)", "(Drum & Bass)"
    def _genre_only(m):
        inner = m.group(1)
        if _TITLE_KEEP_RE.search(inner) or len(inner.split()) > 3:
            return m.group(0)
        return "" if _genre_map(inner) else m.group(0)
    t = re.sub(r"\s*[\(\[]([^\)\]]+)[\)\]]", _genre_only, t)
    t = _TITLE_NOISE_BARE_RE.sub("", t)
    t = _TITLE_TRAIL_RE.sub("", t)                             # "... AUDIO", "... HD"
    t = re.sub(r"\b[Ff][Tt]\.?\s", "feat. ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—|")
    new_artist = artist or ""
    m = re.match(r"^['‘’\"“”](.+?)['‘’\"“”]\s+by\s+(.+)$", t, re.IGNORECASE)   # "'Song' by Artist"
    if m:
        t, new_artist = m.group(1).strip(), m.group(2).strip()
    else:
        m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", t)
        if m:
            new_artist, t = m.group(1).strip(), m.group(2).strip()
    t = re.sub(r"^['‘’\"“”](.+)['‘’\"“”]$", r"\1", t.strip())        # Anfuehrungszeichen um den Titel
    t = re.sub(r"^['‘’\"“”]([^'‘’\"“”]+)['‘’\"“”](\s*\(.*)$", r"\1\2", t)
    return new_artist.strip(" -"), t.strip(" -")


def _title_candidates(only: set | None) -> list[dict]:
    out = []
    for lt in _state["library"]:
        if lt.get("missing") or not lt.get("path"):
            continue
        if only is not None and lt["path"] not in only:
            continue
        if _GENRE_SKIP_RE.search(os.path.basename(os.path.dirname(lt["path"]))) or \
                re.search(r"_(bass|drums|vocals|other|instrumental)$", lt.get("title") or "", re.I):
            continue       # Stems
        out.append(lt)
    return out


def _lfm_track_info_sync(api_key: str, artist: str, title: str) -> tuple[str, str] | None:
    from urllib.parse import urlencode
    d = _http_json("https://ws.audioscrobbler.com/2.0/?" + urlencode(
        {"method": "track.getInfo", "artist": artist, "track": title, "api_key": api_key,
         "format": "json", "autocorrect": 1}))
    tr = d.get("track") or {}
    a = (tr.get("artist") or {}).get("name") if isinstance(tr.get("artist"), dict) else tr.get("artist")
    return (a or "", tr.get("name") or "") if tr.get("name") else None


def _net_accept(local: str, net: str) -> bool:
    """Netz-Schreibweise nur uebernehmen, wenn es derselbe Text ist (andere
    Gross-/Kleinschreibung, Satzzeichen, kleine Tippfehler)."""
    a, b = search._dupe_norm(local), search._dupe_norm(net)
    return bool(a and b) and (a == b or SequenceMatcher(None, a, b).ratio() >= 0.9)


async def _title_suggest(ws: WebSocket, online: bool = False, only: set | None = None):
    global _title_cancel
    _title_cancel = False

    async def send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    cands = _title_candidates(only)
    lfm_key = (_state.get("lastfm_api_key") or "").strip()
    cache = store._load_json(_genre_cache_file(), {})
    loop = asyncio.get_running_loop()
    items = []
    total = len(cands) if online and lfm_key else 0
    await send("title_progress", done=0, total=total)
    for n, lt in enumerate(cands, 1):
        if _title_cancel:
            break
        old_t, old_a = lt.get("title") or "", lt.get("artist") or ""
        new_a, new_t = _clean_title_local(old_t, old_a)
        source = "Regeln"
        if total and new_a and new_t:
            k = f"ti|{new_a.lower()}|{new_t.lower()}"
            if k not in cache:
                cache[k] = await loop.run_in_executor(None, _lfm_track_info_sync, lfm_key, new_a, new_t)
                await asyncio.sleep(0.2)
            got = cache[k]
            if got:
                na, nt = got
                changed = False
                if na and na != new_a and _net_accept(new_a, na):
                    new_a, changed = na, True
                if nt and nt != new_t and _net_accept(new_t, nt) and not (" - " in nt and "(" in new_t):
                    new_t, changed = nt, True
                if changed:
                    source = "Last.fm"
            if n % 20 == 0:
                store._save_json(_genre_cache_file(), cache)
                await send("title_progress", done=n, total=total)
        if new_t and (new_t != old_t or (new_a or "") != old_a):
            # Unsicher: der "Kuenstler" sieht nach Titel aus ("Titel - Kuenstler" vertauscht)
            sure = not re.search(r"[\(\[]|\bfeat\b|\bremix\b|\bedit\b|\bmix\b", new_a or "", re.I)
            items.append({"path": lt["path"], "old_title": old_t, "old_artist": old_a,
                          "title": new_t, "artist": new_a, "source": source, "sure": sure})
    if total:
        store._save_json(_genre_cache_file(), cache)
    await send("title_suggestions", items=items, total=len(cands), cancelled=_title_cancel,
               has_lastfm=bool(lfm_key))


def _write_title_sync(path: str, title: str, artist: str) -> bool:
    """Nur Titel- und Kuenstler-Tag aendern, alle anderen Tags bleiben."""
    import mutagen
    ext = Path(path).suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, TIT2, TPE1, ID3NoHeaderError
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.setall("TIT2", [TIT2(encoding=3, text=title)])
            tags.setall("TPE1", [TPE1(encoding=3, text=artist)]) if artist else None
            v = tags.version[1] if tags.version and tags.version[1] in (3, 4) else 3
            tags.save(path, v2_version=v)
            return True
        f = mutagen.File(path)
        if f is None:
            return False
        if f.tags is None:
            f.add_tags()
        if ext in (".wav", ".aif", ".aiff"):
            from mutagen.id3 import TIT2, TPE1
            f.tags.setall("TIT2", [TIT2(encoding=3, text=title)])
            if artist:
                f.tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
        elif ext in (".m4a", ".mp4", ".aac"):
            f.tags["\xa9nam"] = [title]
            if artist:
                f.tags["\xa9ART"] = [artist]
        else:
            f.tags["title"] = [title]
            if artist:
                f.tags["artist"] = [artist]
        f.save()
        return True
    except Exception as e:
        print(f"[title] {Path(path).name}: {e}", flush=True)
        return False


async def _title_apply(items: list, ws: WebSocket):
    global _title_cancel
    _title_cancel = False

    async def send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    by_path = {lt.get("path"): lt for lt in _state["library"]}
    ci = _state.get("current_idx", -1)
    q = _state.get("queue", [])
    loaded = q[ci].get("path") if 0 <= ci < len(q) else None
    loop = asyncio.get_running_loop()
    ok, failed, skipped, cancelled = 0, 0, 0, False
    for n, it in enumerate(items, 1):
        if _title_cancel:
            cancelled = True
            break
        path, title, artist = it.get("path"), (it.get("title") or "").strip(), (it.get("artist") or "").strip()
        lt = by_path.get(path)
        if not lt or not title:
            continue
        if path == loaded:
            skipped += 1
            continue
        if os.path.exists(path) and await loop.run_in_executor(None, _write_title_sync, path, title, artist):
            lt["title"] = title
            if artist:
                lt["artist"] = artist
            try:
                lt["mtime"] = int(os.path.getmtime(path))
            except OSError:
                pass
            for qi in _state["queue"]:
                if qi.get("path") == path:
                    qi["title"] = title
            ok += 1
        else:
            failed += 1
        if n % 10 == 0:
            await send("title_apply_progress", done=n, total=len(items))
            await asyncio.sleep(0)
    store.save_library()
    store.save_queue()
    await core.push_library()
    await core.push_queue()
    await send("title_applied", ok=ok, failed_count=failed, skipped=skipped, cancelled=cancelled)


# ── Genres: vereinheitlichen und fehlende ergaenzen ─────────────────────────
# Grobe Hauptgenres, damit die Navigation uebersichtlich bleibt. Quellen fuer
# fehlende Genres, in dieser Reihenfolge: Ordner-Regeln (die eigene Sortierung),
# Last.fm-Tags des Titels, Spotify-Genres des Kuenstlers, Last.fm-Tags des
# Kuenstlers. Nichts wird ohne Bestaetigung geschrieben (Vorschlagsliste).
GENRES = ["Drum & Bass", "Dubstep", "House", "Techno", "Trance", "Hardstyle",
          "EDM / Dance", "Pop", "Hip-Hop / Rap", "R&B / Soul", "Rock / Metal",
          "Latin", "Schlager / Party", "Chill"]

# Stichwort -> Hauptgenre. Reihenfolge zaehlt: Spezielles vor Allgemeinem
# ("bass house" ist House, nicht EDM; "post-hardcore" ist Rock, nicht Hardstyle).
_GENRE_RULES = [(g, re.compile(rx, re.IGNORECASE)) for g, rx in [
    ("Drum & Bass",      r"drum\s*(?:and|n|&|'n'|’n’)?\s*bass|\bdnb\b|\bd\s*&\s*b\b|jungle|neuro\s*funk|\bneuro\b|jump\s*up|\bliquid\b|halftime|drumstep"),
    ("Dubstep",          r"dubstep|riddim|brostep|tear\s*out"),
    ("Rock / Metal",     r"post[\s-]*hardcore|metalcore|pop[\s-]*punk|melodic\s+hardcore"),
    ("Hardstyle",        r"hardstyle|rawstyle|hard\s*dance|gabber|frenchcore|\bhardcore\b|hard\s*techno"),
    ("Techno",           r"techno|\bminimal\b"),
    ("Trance",           r"trance|\bgoa\b"),
    ("House",            r"house|bassline|\bgarage\b|\bukg\b|nu[\s-]*disco|\bdisco\b"),
    ("Latin",            r"reggaeton|\blatin|salsa|bachata|cumbia|dembow"),
    ("Hip-Hop / Rap",    r"hip[\s-]*hop|\brap\b|deutschrap|german\s+rap|\btrap\b|grime|\bdrill\b"),
    ("R&B / Soul",       r"\br\s*&\s*b\b|\brnb\b|\bsoul\b"),
    ("Schlager / Party", r"schlager|ballermann|mallorca|apres[\s-]*ski|après[\s-]*ski|volksmusik|stimmungs"),
    ("Chill",            r"chill|lo[\s-]*fi|ambient|downtempo"),
    ("Pop",              r"dance[\s-]*pop|electro[\s-]*pop|synth[\s-]*pop|\bk[\s-]*pop|\bpop\b|deutschpop|singer[\s-]*songwriter"),
    ("EDM / Dance",      r"\bedm\b|electro|big\s*room|future\s*bass|\bdance\b|electronic|eurodance|moombahton|bass\s*music"),
    ("Rock / Metal",     r"\brock\b|metal|punk|grunge|alternative|\bindie\b"),
]]
# Keine Genres: yt-dlp schreibt die YouTube-Kategorie ins Genre-Feld
_YT_CATEGORIES = {"music", "other", "entertainment", "people & blogs", "gaming", "film & animation",
                  "comedy", "howto & style", "education", "news & politics", "science & technology",
                  "autos & vehicles", "pets & animals", "sports", "travel & events", "nonprofits & activism"}
_GENRE_MIN_SEC = 60            # Samples und FX bekommen kein Genre
_GENRE_SKIP_RE = re.compile(r"\b(stems?|vocals?|instrumental|karaoke|a[\s-]?cap+ella|sample[\s-]*pack|drums|fx)\b", re.IGNORECASE)
_LFM_SURE_SHARE = 0.6          # Anteil des staerksten Genres an allen passenden Last.fm-Tags
_genre_cancel = False
_genre_running = False


def _genre_map(text: str) -> str | None:
    """Freies Genre/Tag/Ordnername -> Hauptgenre oder None."""
    t = (text or "").strip()
    if not t:
        return None
    if t in GENRES:
        return t
    for genre, rx in _GENRE_RULES:
        if rx.search(t):
            return genre
    return None


def _genre_from_tags(tags: list) -> tuple[str | None, float]:
    """Last.fm-/Spotify-Tags [(name, gewicht), ...] -> (Genre, Anteil)."""
    score: dict[str, float] = {}
    for name, weight in tags:
        g = _genre_map(name)
        if g:
            score[g] = score.get(g, 0) + max(float(weight or 0), 1.0)
    if not score:
        return None, 0.0
    best = max(score, key=score.get)
    return best, score[best] / sum(score.values())


def _genre_rules_file() -> Path:
    return core.BASE_DIR / "genre_rules.json"


def _genre_rules_load() -> dict:
    return store._load_json(_genre_rules_file(), {})


def _genre_rules_save(rules: dict):
    # "" = bewusst keine Regel (sonst gilt der Vorschlag aus dem Ordnernamen)
    clean = {str(k): v for k, v in rules.items() if v in GENRES or v == ""}
    store._save_json(_genre_rules_file(), clean)


def _genre_cache_file() -> Path:
    return core.BASE_DIR / "genre_cache.json"


def _artist_title(lt: dict) -> tuple[str, str]:
    """Kuenstler und Titel fuer die Online-Abfrage. "Kuenstler - Titel" im
    Dateititel geht vor dem Kuenstler-Tag (bei YouTube-Downloads der Kanal)."""
    t = search._DL_DUPE_NOISE_RE.sub(" ", lt.get("title") or Path(lt.get("path", "")).stem)
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -")
    m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", t)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return (lt.get("artist") or lt.get("album_artist") or "").strip(), t


def _http_json(url: str, headers: dict | None = None, data: bytes | None = None) -> dict:
    import urllib.request as _req
    try:
        req = _req.Request(url, data=data, headers={"User-Agent": "SynthiMIX/1.5", **(headers or {})})
        with _req.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _lfm_tags_sync(method: str, api_key: str, artist: str, title: str = "") -> list:
    from urllib.parse import urlencode
    params = {"method": method, "artist": artist, "api_key": api_key, "format": "json", "autocorrect": 1}
    if title:
        params["track"] = title
    data = _http_json("https://ws.audioscrobbler.com/2.0/?" + urlencode(params))
    tags = (data.get("toptags") or {}).get("tag") or []
    if isinstance(tags, dict):
        tags = [tags]
    return [(t.get("name", ""), t.get("count", 0)) for t in tags[:15]]


class _Spotify:
    """Minimaler Zugriff auf die Spotify-Web-API (Client-Credentials)."""
    def __init__(self, cid: str, secret: str):
        self.cid, self.secret, self.token, self.until = cid, secret, "", 0.0

    def _auth(self) -> bool:
        if self.token and time.time() < self.until - 60:
            return True
        raw = base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode()
        d = _http_json("https://accounts.spotify.com/api/token",
                       headers={"Authorization": f"Basic {raw}",
                                "Content-Type": "application/x-www-form-urlencoded"},
                       data=b"grant_type=client_credentials")
        self.token = d.get("access_token", "")
        self.until = time.time() + float(d.get("expires_in", 3600))
        return bool(self.token)

    def artist_genres(self, artist: str, title: str) -> list:
        from urllib.parse import urlencode
        if not self._auth():
            return []
        h = {"Authorization": f"Bearer {self.token}"}
        q = f"track:{title} artist:{artist}" if artist else title
        d = _http_json("https://api.spotify.com/v1/search?" + urlencode({"q": q, "type": "track", "limit": 1}), h)
        items = ((d.get("tracks") or {}).get("items")) or []
        if not items or not items[0].get("artists"):
            return []
        aid = items[0]["artists"][0].get("id")
        a = _http_json(f"https://api.spotify.com/v1/artists/{aid}", h) if aid else {}
        return [(g, 50) for g in (a.get("genres") or [])]


def _genre_candidates() -> list[dict]:
    """Titel, die einen Vorschlag brauchen: Genre leer, YouTube-Kategorie oder
    anders geschrieben als das Hauptgenre."""
    out = []
    for lt in _state["library"]:
        if lt.get("missing") or not lt.get("path") or (lt.get("duration_sec") or 0) < _GENRE_MIN_SEC:
            continue
        if _GENRE_SKIP_RE.search(lt.get("title") or "") or _GENRE_SKIP_RE.search(os.path.basename(os.path.dirname(lt["path"]))):
            continue    # Stems, Acapellas, Sample-Packs
        cur = (lt.get("genre") or "").strip()
        if cur in GENRES:
            continue
        out.append(lt)
    return out


def _nearest_rule(path: str, rules: dict) -> str | None:
    """Regel des naechsten Ordners nach oben. Ohne gespeicherte Regel zaehlt der
    Vorschlag aus dem Ordnernamen ("Liquid" -> Drum & Bass); "" heisst bewusst keine."""
    d = os.path.dirname(path)
    while d and d != os.path.dirname(d):
        if d in rules:
            return rules[d] or None
        auto = _genre_map(os.path.basename(d))
        if auto:
            return auto
        d = os.path.dirname(d)
    return None


async def _genre_suggest(ws: WebSocket, online: bool = True):
    """Vorschlaege berechnen und an den Dialog schicken. Online-Antworten
    werden zwischengespeichert, ein zweiter Durchlauf ist schnell."""
    global _genre_cancel, _genre_running
    if _genre_running:
        return
    _genre_running, _genre_cancel = True, False

    async def send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    try:
        rules = _genre_rules_load()
        cands = _genre_candidates()
        # Ordner der betroffenen Titel mit Vorschlag aus dem Ordnernamen
        folders: dict[str, dict] = {}
        for lt in cands:
            d = os.path.dirname(lt["path"])
            f = folders.setdefault(d, {"folder": d, "name": os.path.basename(d), "count": 0,
                                       "auto": _genre_map(os.path.basename(d)), "rule": rules.get(d)})
            f["count"] += 1

        cache = store._load_json(_genre_cache_file(), {})
        lfm_key = (_state.get("lastfm_api_key") or "").strip()
        cid, csec = (_state.get("spotify_client_id") or "").strip(), (_state.get("spotify_client_secret") or "").strip()
        spotify = _Spotify(cid, csec) if cid and csec else None
        loop = asyncio.get_running_loop()
        items, need_online = [], []

        for lt in cands:
            cur = (lt.get("genre") or "").strip()
            base = {"path": lt["path"], "title": lt.get("title", ""), "current": cur}
            mapped = None if cur.lower() in _YT_CATEGORIES else _genre_map(cur)
            if mapped:
                items.append({**base, "genre": mapped, "source": "Vereinheitlicht", "sure": True})
                continue
            rule = _nearest_rule(lt["path"], rules)
            if rule:
                items.append({**base, "genre": rule, "source": "Ordner", "sure": True})
                continue
            need_online.append((lt, base))

        total = len(need_online) if online and (lfm_key or spotify) else 0
        await send("genre_progress", done=0, total=total)
        done = 0
        for lt, base in need_online:
            if _genre_cancel:
                break
            artist, title = _artist_title(lt)
            genre, source, sure = None, "", False
            if total and artist and title:
                if lfm_key:
                    k = f"lt|{artist.lower()}|{title.lower()}"
                    if k not in cache:
                        cache[k] = await loop.run_in_executor(None, _lfm_tags_sync, "track.gettoptags", lfm_key, artist, title)
                        await asyncio.sleep(0.2)          # Last.fm: hoechstens ~5 Anfragen/s
                    g, share = _genre_from_tags(cache[k])
                    if g:
                        genre, source, sure = g, "Last.fm", share >= _LFM_SURE_SHARE
                if not sure and spotify:
                    k = f"sp|{artist.lower()}|{title.lower()}"
                    if k not in cache:
                        cache[k] = await loop.run_in_executor(None, spotify.artist_genres, artist, title)
                    g, share = _genre_from_tags(cache[k])
                    if g and (not genre or share >= _LFM_SURE_SHARE):
                        genre, source, sure = g, "Spotify (Künstler)", False
                if not genre and lfm_key:
                    k = f"la|{artist.lower()}"
                    if k not in cache:
                        cache[k] = await loop.run_in_executor(None, _lfm_tags_sync, "artist.gettoptags", lfm_key, artist)
                        await asyncio.sleep(0.2)
                    g, _ = _genre_from_tags(cache[k])
                    if g:
                        genre, source, sure = g, "Last.fm (Künstler)", False
            if genre:
                items.append({**base, "genre": genre, "source": source, "sure": sure})
            if total:
                done += 1
                if done % 20 == 0:
                    store._save_json(_genre_cache_file(), cache)
                    await send("genre_progress", done=done, total=total)
        store._save_json(_genre_cache_file(), cache)
        await send("genre_suggestions", items=items, genres=GENRES,
                   folders=sorted(folders.values(), key=lambda f: -f["count"]),
                   missing=len(cands) - len(items), cancelled=_genre_cancel,
                   has_lastfm=bool(lfm_key), has_spotify=bool(spotify))
    finally:
        _genre_running = False


def _write_genre_sync(path: str, genre: str) -> bool:
    """Nur das Genre-Feld der Datei aendern, alle anderen Tags bleiben."""
    import mutagen
    ext = Path(path).suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, TCON, ID3NoHeaderError
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.setall("TCON", [TCON(encoding=3, text=genre)])
            v = tags.version[1] if tags.version and tags.version[1] in (3, 4) else 3
            tags.save(path, v2_version=v)
            return True
        f = mutagen.File(path)
        if f is None:
            return False
        if f.tags is None:
            f.add_tags()
        if ext in (".wav", ".aif", ".aiff"):
            from mutagen.id3 import TCON
            f.tags.setall("TCON", [TCON(encoding=3, text=genre)])
        elif ext in (".m4a", ".mp4", ".aac"):
            f.tags["\xa9gen"] = [genre]
        else:
            f.tags["genre"] = [genre]
        f.save()
        return True
    except Exception as e:
        print(f"[genre] {Path(path).name}: {e}", flush=True)
        return False


async def _genre_apply(items: list, ws: WebSocket):
    """Genres in Dateien und Bibliothek schreiben. Der gerade geladene Titel
    wird uebersprungen (die Datei ist im Player offen). Abbrechbar: was schon
    geschrieben ist, bleibt."""
    global _genre_cancel
    _genre_cancel = False

    async def send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    by_path = {lt.get("path"): lt for lt in _state["library"]}
    ci = _state.get("current_idx", -1)
    q = _state.get("queue", [])
    loaded = q[ci].get("path") if 0 <= ci < len(q) else None
    loop = asyncio.get_running_loop()
    ok, failed, skipped = 0, [], 0
    total = len(items)
    cancelled = False
    for n, it in enumerate(items, 1):
        if _genre_cancel:
            cancelled = True
            break
        path, genre = it.get("path"), it.get("genre")
        lt = by_path.get(path)
        if not lt or genre not in GENRES:
            continue
        if path == loaded:
            skipped += 1
            continue
        if os.path.exists(path) and await loop.run_in_executor(None, _write_genre_sync, path, genre):
            lt["genre"] = genre
            try:
                lt["mtime"] = int(os.path.getmtime(path))
            except OSError:
                pass
            ok += 1
        else:
            failed.append(lt.get("title") or path)
        if n % 10 == 0:
            await send("genre_apply_progress", done=n, total=total)
            await asyncio.sleep(0)       # Abbrechen-Nachricht durchlassen
    store.save_library()
    await core.push_library()
    await send("genre_applied", ok=ok, failed=failed[:20], failed_count=len(failed), skipped=skipped,
               cancelled=cancelled)

# ── AcoustID fingerprinting ───────────────────────────────────────────────────
async def _test_services() -> dict:
    """Jeden Dienst mit einer kleinen Abfrage pruefen und in Worten melden, was
    los ist. Vorher fielen falsche oder nicht gespeicherte Keys erst auf, wenn
    Radio oder Trackerkennung still nichts taten."""
    loop = asyncio.get_running_loop()
    out: dict = {}

    def _get(url, headers=None):
        import urllib.request as _req, urllib.error as _err
        try:
            with _req.urlopen(_req.Request(url, headers={"User-Agent": "SynthiMIX/1.5", **(headers or {})}), timeout=10) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except _err.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8", errors="replace") or "{}") or {"_http": e.code}
            except Exception:
                return {"_http": e.code}
        except Exception as e:
            return {"_net": str(e)}

    from urllib.parse import urlencode
    key = (_state.get("lastfm_api_key") or "").strip()
    if not key:
        out["lastfm"] = {"ok": False, "text": "Kein Key eingetragen."}
    else:
        d = await loop.run_in_executor(None, _get, "https://ws.audioscrobbler.com/2.0/?" + urlencode(
            {"method": "artist.getTopTags", "artist": "Hybrid Minds", "api_key": key, "format": "json"}))
        if d.get("_net"):
            out["lastfm"] = {"ok": False, "text": "Keine Verbindung zu Last.fm."}
        elif d.get("error"):
            out["lastfm"] = {"ok": False, "text": f"Last.fm lehnt den Key ab: {d.get('message', d.get('error'))}"}
        else:
            out["lastfm"] = {"ok": True, "text": "Verbunden."}

    key = (_state.get("acoustid_api_key") or "").strip()
    if not key:
        out["acoustid"] = {"ok": False, "text": "Kein Key eingetragen."}
    else:
        d = await loop.run_in_executor(None, _get, "https://api.acoustid.org/v2/lookup?" + urlencode(
            {"client": key, "trackid": "9ff43b6a-4f16-427c-93c2-92307ca505e0"}))
        if d.get("_net"):
            out["acoustid"] = {"ok": False, "text": "Keine Verbindung zu AcoustID."}
        elif d.get("status") == "ok":
            out["acoustid"] = {"ok": True, "text": "Verbunden."}
        else:
            msg = (d.get("error") or {}).get("message", "") if isinstance(d.get("error"), dict) else str(d.get("error", ""))
            out["acoustid"] = {"ok": False, "text": (
                "Key ungültig. AcoustID braucht einen Application-Key (acoustid.org → „Register your "
                "application“), nicht den Key von deiner Benutzerseite.") if "invalid" in msg.lower()
                else f"AcoustID meldet: {msg or 'Fehler'}"}

    out["fpcalc"] = {"ok": bool(core._find_fpcalc()),
                     "text": "Gefunden." if core._find_fpcalc() else "Nicht installiert — „Installieren“ klicken."}

    cid, sec = (_state.get("spotify_client_id") or "").strip(), (_state.get("spotify_client_secret") or "").strip()
    if not (cid and sec):
        out["spotify"] = {"ok": False, "text": "Client-ID oder Secret fehlt (Tab Download)."}
    else:
        ok = await loop.run_in_executor(None, _Spotify(cid, sec)._auth)
        out["spotify"] = {"ok": bool(ok), "text": "Verbunden." if ok else "Spotify lehnt Client-ID/Secret ab."}
    return out


async def _acoustid_identify(path: str) -> dict:
    import urllib.request as _req, urllib.parse as _parse
    api_key = _state.get('acoustid_api_key', '').strip()
    if not api_key:
        return {'error': 'Kein AcoustID API-Key konfiguriert', 'fix_tab': 'services'}

    fpcalc = core._find_fpcalc()
    if not fpcalc:
        return {'error': 'fpcalc ist nicht installiert', 'fix_tab': 'services'}

    try:
        proc = await asyncio.create_subprocess_exec(
            fpcalc, '-json', path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=_NO_WINDOW)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        fp_data = json.loads(out.decode(errors='replace'))
    except asyncio.TimeoutError:
        return {'error': 'fpcalc Timeout — Datei zu groß oder kaputt'}
    except Exception as e:
        return {'error': f'fpcalc Fehler: {e}'}

    fingerprint = fp_data.get('fingerprint', '')
    duration    = int(fp_data.get('duration', 0))
    if not fingerprint:
        return {'error': 'Kein Fingerprint generiert'}

    loop = asyncio.get_running_loop()
    _UA = 'SynthiMIX/1.4 (synthiseisa@gmail.com)'

    params = _parse.urlencode({
        'client': api_key, 'fingerprint': fingerprint, 'duration': duration,
        'meta': 'recordings+releasegroups+tracks',
    })
    url = f"https://api.acoustid.org/v2/lookup?{params}"

    def _fetch(u, extra_headers=None):
        try:
            headers = {'User-Agent': _UA}
            if extra_headers:
                headers.update(extra_headers)
            req = _req.Request(u, headers=headers)
            with _req.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            return {'error': str(e)}

    data = await loop.run_in_executor(None, _fetch, url)
    if 'error' in data:
        return {'error': f'AcoustID API: {data["error"]}'}

    results = data.get('results', [])
    if not results:
        return {'error': 'Kein Match bei AcoustID gefunden'}

    best  = max(results, key=lambda r: r.get('score', 0))
    score = best.get('score', 0)
    recs  = best.get('recordings', [])

    # Pick a recording that has at least a title
    rec = next((r for r in recs if r.get('title')), recs[0] if recs else None)

    # Fallback: recording ID present but no title → fetch directly from MusicBrainz
    if rec and not rec.get('title') and rec.get('id'):
        mb_id  = rec['id']
        mb_url = f"https://musicbrainz.org/ws/2/recording/{mb_id}?inc=artists+releases+release-groups&fmt=json"
        mb = await loop.run_in_executor(None, _fetch, mb_url)
        if mb and 'id' in mb:
            ac = mb.get('artist-credit', [])
            rels = mb.get('releases', [])
            rec = {
                'title':   mb.get('title', ''),
                'artists': [{'name': a['artist']['name']} for a in ac if 'artist' in a],
                'releasegroups': [{'title': rels[0]['title']}] if rels else [],
            }

    via = 'AcoustID'
    if not rec or not rec.get('title'):
        # Last-resort: MusicBrainz text search via existing library tags
        via = 'MusicBrainz'

        lib_track = next((t for t in _state.get('library', []) if t.get('path') == path), None)
        q_title  = lib_track.get('title', '')  if lib_track else ''
        q_artist = lib_track.get('artist', '') if lib_track else ''
        if q_title or q_artist:
            parts = []
            if q_title:  parts.append(f'recording:"{q_title}"')
            if q_artist: parts.append(f'artist:"{q_artist}"')
            mb_q   = _parse.urlencode({'query': ' AND '.join(parts), 'limit': '1', 'fmt': 'json'})
            mb_url = f"https://musicbrainz.org/ws/2/recording/?{mb_q}"
            mb = await loop.run_in_executor(None, _fetch, mb_url)
            mb_recs = (mb or {}).get('recordings', [])
            if mb_recs:
                r0 = mb_recs[0]
                ac   = r0.get('artist-credit', [])
                rels = r0.get('releases', [])
                rec  = {
                    'title':        r0.get('title', ''),
                    'artists':      [{'name': a['artist']['name']} for a in ac if 'artist' in a],
                    'releasegroups': [{'title': rels[0]['title']}] if rels else [],
                }

    if not rec or not rec.get('title'):
        # Last.fm fallback: search by filename
        lfm_key = _state.get('lastfm_api_key', '').strip()
        if lfm_key:
            fname = os.path.splitext(os.path.basename(path))[0]
            # Clean up filename: remove common noise, replace separators
            fname = re.sub(r'[\[\(].*?[\]\)]', '', fname)          # strip [brackets] (notes)
            fname = re.sub(r'[-_]+', ' ', fname).strip()
            lfm_params = _parse.urlencode({
                'method': 'track.search', 'track': fname,
                'api_key': lfm_key, 'format': 'json', 'limit': '1',
            })
            lfm_url = f"http://ws.audioscrobbler.com/2.0/?{lfm_params}"
            lfm = await loop.run_in_executor(None, _fetch, lfm_url)
            matches = ((lfm or {}).get('results', {})
                       .get('trackmatches', {})
                       .get('track', []))
            if isinstance(matches, dict):
                matches = [matches]
            if matches:
                m = matches[0]
                via = 'Last.fm'
                rec = {
                    'title':        m.get('name', ''),
                    'artists':      [{'name': m.get('artist', '')}],
                    'releasegroups': [],
                    '_source': 'Last.fm',
                }

    if not rec or not rec.get('title'):
        return {'error': f'Match gefunden (score={score:.2f}), aber keine Metadaten in AcoustID, MusicBrainz oder Last.fm verfügbar'}

    title   = rec.get('title', '')
    artists = rec.get('artists', [])
    artist  = artists[0].get('name', '') if artists else ''
    rgs     = rec.get('releasegroups', [])
    album   = rgs[0].get('title', '') if rgs else ''

    return {'title': title, 'artist': artist, 'album': album, 'score': round(score, 3), 'path': path,
            'via': via}


_FP_PARALLEL = 3
_VERSION_RE = re.compile(r"\b(remix|edit|vip|bootleg|mix|rework|flip|dub|acapella|instrumental|live)\b", re.I)

async def _fingerprint_suggest(ws: WebSocket, paths: list[str]):
    """Mehrere Titel per AcoustID erkennen. Ergebnis wie beim Titel-Aufraeumen
    (Vorschlagsliste, uebernommen wird per title_apply) — so wird nichts
    ungesehen ueberschrieben."""
    global _title_cancel
    _title_cancel = False

    async def send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    if not (_state.get("acoustid_api_key") or "").strip():
        await send("title_suggestions", items=[], total=0, fp=True, fix_tab="services",
                   error="Für die Fingerprint-Erkennung fehlt der AcoustID-Key (Einstellungen → Dienste).")
        return
    if not core._find_fpcalc():
        await send("title_suggestions", items=[], total=0, fp=True, fix_tab="services",
                   error="fpcalc ist nicht installiert (Einstellungen → Dienste → Installieren).")
        return

    by_path = {lt.get("path"): lt for lt in _state["library"]}
    todo = [by_path[p] for p in dict.fromkeys(paths) if p in by_path and os.path.exists(p)]
    total = len(todo)
    items: list[dict] = []
    stats = {"done": 0, "same": 0, "nomatch": 0}
    sem = asyncio.Semaphore(_FP_PARALLEL)
    await send("title_progress", done=0, total=total)

    async def one(lt):
        if _title_cancel:
            return
        async with sem:
            if _title_cancel:
                return
            try:
                r = await _acoustid_identify(lt["path"])
            except Exception as e:
                r = {"error": str(e)}
            await asyncio.sleep(0.35)          # AcoustID: hoechstens 3 Anfragen je Sekunde
        stats["done"] += 1
        old_t, old_a = lt.get("title") or "", lt.get("artist") or ""
        new_t, new_a = (r.get("title") or "").strip(), (r.get("artist") or "").strip()
        if r.get("error") or not new_t:
            stats["nomatch"] += 1
        elif search._dupe_norm(new_t) == search._dupe_norm(old_t) and search._dupe_norm(new_a) == search._dupe_norm(old_a):
            stats["same"] += 1
        else:
            score = float(r.get("score") or 0)
            fp = r.get("via") == "AcoustID"
            # Remix & Co.: AcoustID ordnet gern dem Original zu — nicht vorauswaehlen
            lost = {w.lower() for w in _VERSION_RE.findall(old_t)} - {w.lower() for w in _VERSION_RE.findall(new_t)}
            items.append({"path": lt["path"], "old_title": old_t, "old_artist": old_a,
                          "title": new_t, "artist": new_a,
                          "source": f"{round(score * 100)} %" if fp else r.get("via") or "Suche",
                          "score": score, "sure": fp and score >= 0.85 and not lost,
                          "why": "" if fp and not lost else ("Version (Remix/Edit) fehlt im Treffer" if lost
                                                             else "nur Text-Suche, kein Fingerprint-Treffer")})
        if stats["done"] % 3 == 0 or stats["done"] == total:
            await send("title_progress", done=stats["done"], total=total)

    await asyncio.gather(*(one(lt) for lt in todo))
    items.sort(key=lambda it: (not it["sure"], -it["score"]))
    await send("title_suggestions", items=items, total=total, cancelled=_title_cancel, fp=True,
               same=stats["same"], nomatch=stats["nomatch"], checked=stats["done"])
