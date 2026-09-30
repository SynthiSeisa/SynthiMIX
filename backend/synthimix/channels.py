"""Kanaele verfolgen: alle (oder ausgewaehlte) Playlists eines YouTube-Kanals.

Ein verfolgter Kanal ist eine Liste von Playlists: beim Pruefen wird die
Playlist-Uebersicht des Kanals gelesen, neue Playlists kommen dazu (oder als
Vorschlag), danach laufen die einzelnen Playlists durch das normale Verfolgen
(download._follow_check). Abgelegt wird unter Downloads/<Kanal>/<Playlist>.
"""
import asyncio
import json
import re
import time
from fastapi import WebSocket
from .core import _NO_WINDOW, _state
from . import core, download, store

# youtube.com/@name, /channel/UC…, /c/name, /user/name — auch music.youtube.com,
# auch mit angehaengtem Reiter (/videos, /playlists …)
_CHANNEL_RE = re.compile(
    r'^https?://(?:www\.|m\.|music\.)?youtube\.com/(@[^/?#]+|channel/[\w-]+|c/[^/?#]+|user/[^/?#]+)', re.I)
_channel_running: set[str] = set()
_channel_plans: dict = {}
_channel_plan_counter = 0


def _channel_base(url: str) -> str | None:
    """Einheitliche Kanal-Adresse oder None (auch fuer Playlist-/Video-Links)."""
    url = (url or "").strip()
    if not url or "list=" in url or "watch?" in url:
        return None
    m = _CHANNEL_RE.match(url)
    return f"https://www.youtube.com/{m.group(1)}" if m else None


def _channels() -> list[dict]:
    return _state.setdefault("followed_channels", [])


def _channel_of(url: str) -> dict | None:
    return next((c for c in _channels() if c["url"] == url), None)


class ChannelReadError(RuntimeError):
    """yt-dlp konnte die Playlist-Uebersicht nicht lesen (Text = Grund)."""


async def _read_tab(cmd: list[str]) -> tuple[dict | None, str]:
    """yt-dlp ausfuehren: (JSON oder None, letzte ERROR-Zeile)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=_NO_WINDOW)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        core._kill_quietly(proc)
        raise
    try:
        data = json.loads(out.decode("utf-8", errors="replace") or "null")
    except ValueError:
        data = None
    errs = [ln.strip() for ln in err.decode("utf-8", errors="replace").splitlines()
            if ln.strip().startswith("ERROR")]
    return (data if isinstance(data, dict) else None), (errs[-1] if errs else "")


def _channel_error_text(msg: str) -> str:
    """yt-dlp-Fehler → kurzer Grund fuer die Anzeige."""
    low = (msg or "").lower()
    if "winerror 2" in low or "no such file" in low:
        return "yt-dlp wurde nicht gefunden (evtl. vom Virenscanner entfernt) — unter Einstellungen → Dienste → yt-dlp neu laden"
    if "http error 404" in low or "does not exist" in low:
        return "Kanal nicht gefunden — Link prüfen"
    kind = download._dl_error_reason(msg)
    if kind != "other":
        return download._DL_REASON_TEXT[kind]
    t = re.sub(r"^ERROR:\s*", "", (msg or "").strip())
    t = re.sub(r"^\[[^\]]+\]\s*[^:]{0,60}:\s*", "", t)      # "[youtube:tab] @name: "
    return (t[:160] + "…") if len(t) > 160 else t


async def _channel_playlists(base: str) -> tuple[str, list[dict]]:
    """Name des Kanals und seine oeffentlichen Playlists [{url, title, thumb}].
    Kann yt-dlp die Uebersicht nicht lesen: ChannelReadError mit dem Grund —
    frueher wurde die Fehlermeldung verworfen, und es hiess nur "keine
    Playlists gefunden"."""
    tab = base + "/playlists"
    data, err = await _read_tab(core._yt("--flat-playlist", "-J", "--no-warnings", tab))
    if data is None and core._JS_ARGS:
        # Zweiter Versuch ohne JS-Laufzeit (Electron als Node): fuer die
        # Uebersicht braucht es sie nicht, und so faellt ein Problem dort nicht ins Gewicht
        print(f"[kanal] {base}: {err or 'keine Antwort'} — zweiter Versuch ohne JS-Laufzeit", flush=True)
        data, err2 = await _read_tab([core.YTDLP, "--encoding", "utf-8", "--flat-playlist", "-J", "--no-warnings", tab])
        err = err2 or err
    if data is None:
        raise ChannelReadError(err or "yt-dlp lieferte keine Antwort")
    title = (data.get("channel") or data.get("uploader")
             or re.sub(r"\s*-\s*Playlists$", "", data.get("title") or "")).strip()
    out_pl, seen = [], set()
    for e in data.get("entries") or []:
        u = e.get("url") or ""
        if "list=" not in u or u in seen:
            continue
        seen.add(u)
        thumbs = e.get("thumbnails") or []
        out_pl.append({"url": u, "title": (e.get("title") or "Playlist")[:80],
                       "thumb": (thumbs[0].get("url") or "") if thumbs else ""})
    return title[:80], out_pl


def _channels_public() -> list[dict]:
    out = []
    for c in _channels():
        mine = [f for f in _state.get("followed", []) if f.get("channel") == c["url"]]
        out.append({"url": c["url"], "title": c.get("title") or c["url"], "auto_new": c.get("auto_new", True),
                    "last_check": c.get("last_check", 0), "last_new": c.get("last_new", 0),
                    "playlists": len(mine), "pending": c.get("pending", []),
                    "checking": c["url"] in _channel_running or any(f["url"] in download._follow_running for f in mine)})
    return out


def _follow_entry(pl: dict, ch: dict) -> dict:
    return {"url": pl["url"], "title": pl["title"], "fmt": ch.get("fmt") or "mp3-best", "mode": "playlist",
            "channel": ch["url"], "parent": ch.get("title") or None, "added": int(time.time()),
            "last_check": 0, "last_new": 0, "seen": []}


async def _plan_channel(url: str, fmt: str, ws: WebSocket):
    """Playlists des Kanals lesen und zur Auswahl schicken."""
    global _channel_plan_counter

    _send = core.sender(ws)

    base = _channel_base(url)
    await _send("playlist_plan_pending", url=url, kind="channel")
    await _send("playlist_plan_progress", url=url, phase="channel", done=0, total=0)
    download._plan_tasks[url] = asyncio.current_task()
    reason = ""
    try:
        title, pls = await _channel_playlists(base)
    except asyncio.CancelledError:
        await _send("playlist_plan_cancel", url=url)
        return
    except Exception as e:
        print(f"[kanal] {base}: {e}", flush=True)
        title, pls = "", []
        reason = _channel_error_text(str(e))
    finally:
        download._plan_tasks.pop(url, None)
    if not pls:
        await _send("playlist_plan_cancel", url=url)
        await _send("channel_plan", src=url, url=base, error=(
            f"Der Kanal ließ sich nicht lesen: {reason}" if reason
            else "Dieser Kanal hat keine öffentlichen Playlists."))
        return
    followed = {f["url"] for f in _state.get("followed", [])}
    ch = _channel_of(base)
    for p in pls:
        p["followed"] = p["url"] in followed
    _channel_plan_counter += 1
    pid = _channel_plan_counter
    _channel_plans[pid] = {"url": base, "title": title, "playlists": pls, "fmt": fmt, "ts": time.time()}
    await _send("channel_plan", src=url, plan_id=pid, url=base, title=title or base, playlists=pls,
                existing=ch is not None, auto_new=(ch or {}).get("auto_new", True),
                excluded=(ch or {}).get("excluded", []))


async def _channel_follow(msg: dict):
    """Auswahl uebernehmen: Kanal anlegen/aendern, gewaehlte Playlists verfolgen und laden."""
    plan = _channel_plans.pop(int(msg.get("plan_id") or 0), None)
    if not plan:
        return
    chosen = set(msg.get("selected") or [])
    all_urls = [p["url"] for p in plan["playlists"]]
    ch = _channel_of(plan["url"])
    if ch is None:
        ch = {"url": plan["url"], "added": int(time.time())}
        _channels().append(ch)
    ch.update({"title": plan["title"] or ch.get("title") or plan["url"], "fmt": plan["fmt"],
               "auto_new": bool(msg.get("auto_new", True)), "last_check": int(time.time()), "pending": []})
    ch["known"] = list(dict.fromkeys((ch.get("known") or []) + all_urls))
    ch["excluded"] = [u for u in all_urls if u not in chosen]
    followed = _state.setdefault("followed", [])
    # Abgewaehlt: nicht mehr verfolgen (geladene Titel bleiben)
    _state["followed"] = followed = [f for f in followed
                                     if not (f.get("channel") == ch["url"] and f["url"] not in chosen)]
    have = {f["url"] for f in followed}
    new = [_follow_entry(p, ch) for p in plan["playlists"] if p["url"] in chosen and p["url"] not in have]
    followed.extend(new)
    store.save_settings()
    await download._push_followed()
    if new:
        # Alles laden: jede neue Playlist einmal komplett (was schon da ist, wird verknuepft/uebersprungen)
        core.spawn(download._follow_check_all([f["url"] for f in new]))


async def _channel_check(ch: dict) -> list[str]:
    """Playlist-Uebersicht des Kanals lesen; neue Playlists verfolgen oder
    vorschlagen. Liefert die Adressen der Playlists, die geprueft werden sollen."""
    url = ch["url"]
    if url in _channel_running:
        return []
    _channel_running.add(url)
    await download._push_followed()
    try:
        title, pls = await _channel_playlists(url)
        known = set(ch.get("known") or []) | set(ch.get("excluded") or [])
        neu = [p for p in pls if p["url"] not in known]
        ch["known"] = list(dict.fromkeys((ch.get("known") or []) + [p["url"] for p in neu]))
        if title:
            ch["title"] = title
        ch["last_check"] = int(time.time())
        ch["last_new"] = len(neu)
        if neu:
            if ch.get("auto_new", True):
                have = {f["url"] for f in _state.setdefault("followed", [])}
                _state["followed"].extend(_follow_entry(p, ch) for p in neu if p["url"] not in have)
            else:
                pend = {p["url"]: p for p in ch.get("pending") or []}
                for p in neu:
                    pend.setdefault(p["url"], {"url": p["url"], "title": p["title"]})
                ch["pending"] = list(pend.values())
            print(f"[kanal] {ch.get('title')}: {len(neu)} neue Playlist(s)", flush=True)
    except Exception as e:
        print(f"[kanal] {url}: {e}", flush=True)
    finally:
        _channel_running.discard(url)
        store.save_settings()
        await download._push_followed()
    return [f["url"] for f in _state.get("followed", []) if f.get("channel") == url]


async def _channel_remove(url: str):
    """Kanal und seine Playlists nicht mehr verfolgen (Dateien bleiben)."""
    _state["followed_channels"] = [c for c in _channels() if c["url"] != url]
    _state["followed"] = [f for f in _state.get("followed", []) if f.get("channel") != url]
    store.save_settings()
    await download._push_followed()
