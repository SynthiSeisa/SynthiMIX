"""Fernbedienung und Musikwuensche (eigener Server auf Port 8080)."""
import asyncio
import base64
import json
import os
import re
import socket as _socket
import time
import uvicorn
from difflib import SequenceMatcher
from functools import lru_cache
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pathlib import Path
from typing import Any
from .core import _state
from . import core, download, keys, library, media, search, store

# Nachrichten der Fernbedienung laufen durch denselben Verteiler wie die
# der App; main.py traegt ihn beim Import ein.
handle_message = None

_remote_clients: set[WebSocket] = set()
_wish_clients: set[WebSocket] = set()      # offene Musikwunsch-Seiten
_remote_server: Any = None
# Fernbedienung und Musikwunsch laufen ueber denselben Server, lassen sich aber
# einzeln ein- und ausschalten. Der Server laeuft, solange einer an ist.
_remote_services: dict = {"remote": False, "wishes": False}

def _remote_status_msg(**extra) -> dict:
    ip = _get_local_ip()
    running = _remote_server is not None
    return {"type": "remote_status", "running": running, "services": dict(_remote_services),
            **({"ip": ip, "port": _remote_port, **_remote_urls(ip)} if running else {}), **extra}
_remote_port = 8080

def _get_local_ip() -> str:
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

# ── Geheimer Link fuer die Fernbedienung ─────────────────────────────────────
# Fernbedienung und Wunschseite teilen sich Port 8080. Ohne Schutz kam ein Gast,
# der im Wunsch-Link "/wunsch" wegloeschte, auf die Fernbedienung und konnte
# pausieren oder die Warteschlange leeren. Einen PIN wollte der Nutzer nicht —
# stattdessen steckt ein zufaelliger Schluessel in der Adresse (und im QR-Code).
def _remote_key() -> str:
    import secrets
    if not _state.get("remote_key"):
        _state["remote_key"] = secrets.token_urlsafe(9)
        store.save_settings()
    return _state["remote_key"]

def _remote_key_ok(k: str | None) -> bool:
    import secrets
    return bool(k) and secrets.compare_digest(str(k), _remote_key())

def _remote_urls(ip: str) -> dict:
    base = f"http://{ip}:{_remote_port}"
    return {"url": f"{base}/?k={_remote_key()}", "wish_url": f"{base}/wunsch"}

# Die Handy-Seiten liegen als eigene Dateien in synthimix/web/ (frueher als
# lange Zeichenketten hier im Code). Erzeugt aus Vorlagen mit eingesetzten
# Tabler-Symbolen; "__KEY__" wird beim Ausliefern ersetzt.
_WEB = Path(__file__).resolve().parent / "web"

def _web_page(name: str) -> str:
    return (_WEB / name).read_text(encoding="utf-8")

_REMOTE_HTML = _web_page("remote.html")

# ── Musikwuensche ────────────────────────────────────────────────────────────
# Gaeste wuenschen sich Titel ueber eine eigene Seite. Der Titel wird sofort
# heruntergeladen und analysiert, damit er beim Annehmen ohne Wartezeit
# spielbar ist. In die Warteschlange kommt er erst, wenn der DJ zustimmt.
_wish_counter = 0

def load_wishes():
    global _wish_counter
    try:
        if core.WISHES_FILE.exists():
            _state["wishes"] = json.loads(core.WISHES_FILE.read_text(encoding="utf-8"))
            _wish_counter = max((w.get("id", 0) for w in _state["wishes"]), default=0)
    except Exception as e:
        print(f"[wishes] konnte nicht geladen werden: {e}", flush=True)
        _state["wishes"] = []

def save_wishes():
    try:
        core.WISHES_FILE.write_text(json.dumps(_state.get("wishes", []),
                                          ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[wishes] konnte nicht gespeichert werden: {e}", flush=True)

def _resume_wishes():
    """Wuensche, die beim Beenden noch in Arbeit waren, weiterfuehren.

    Vorher blieben sie fuer immer auf "laedt…" stehen: gespeichert wird der
    Status, die Arbeit selbst lief nur im alten Prozess.
    """
    for w in _state.get("wishes", []):
        if w.get("status") not in ("neu", "laedt", "analysiert"):
            continue
        if w.get("path") and os.path.exists(w["path"]):
            w["status"] = "bereit"
        else:
            asyncio.create_task(_process_wish(w))
    save_wishes()

async def push_wishes():
    await core.broadcast({"type": "wishes", "items": _state.get("wishes", [])})
    if _remote_clients:
        asyncio.create_task(_broadcast_remote_state())

@lru_cache(maxsize=8192)
def _norm_title(t: str) -> str:
    return library._norm_queue_title(t)

def _title_matches(a: str, b: str) -> bool:
    # Jede Wunsch-Suche prueft jeden Treffer gegen Warteschlange, Abend-Log
    # und Wuensche (einige hundert Vergleiche): Normalform gemerkt, und die
    # billigen Obergrenzen zuerst — ratio() nur, wenn es knapp werden kann
    na, nb = _norm_title(a or ""), _norm_title(b or "")
    if not na or not nb:
        return False
    sm = SequenceMatcher(None, na, nb)
    return sm.real_quick_ratio() >= 0.82 and sm.quick_ratio() >= 0.82 and sm.ratio() >= 0.82

def _queue_eta_sec(idx: int) -> float:
    """Sekunden, bis der Titel an Position idx an der Reihe ist."""
    q  = _state.get("queue", [])
    ci = _state.get("current_idx", -1)
    if idx <= ci:
        return 0.0
    dur = _state.get("duration_ms", 0) / 1000
    pos = _state.get("position_ms", 0) / 1000
    rem = max(0.0, dur - pos) if dur > 0 else 0.0
    for j in range(ci + 1, idx):
        if 0 <= j < len(q):
            rem += q[j].get("duration_sec", 0) or 0
    return rem

_WISH_PLAYED_WINDOW = 12 * 3600   # "lief schon" gilt fuer die letzten 12 Stunden

# Was aus angenommenen und abgelehnten Wuenschen wurde — die Wuensche selbst
# verschwinden dabei aus der Liste, der Gast will aber wissen, wann sein Titel
# kommt. Nur im Speicher: nach einem Neustart zaehlt der neue Abend.
_wish_outcomes: dict[int, dict] = {}

def _guest_wish_status(wid: int) -> dict | None:
    """Stand eines Wunsches aus Sicht des Gastes. Keine Pfade, keine IPs."""
    q, ci = _state.get("queue", []), _state.get("current_idx", -1)
    out = _wish_outcomes.get(wid)
    if out:
        base = {"id": wid, "title": out.get("title", "")}
        if out["state"] == "abgelehnt":
            return {**base, "state": "rejected"}
        i = next((j for j, t in enumerate(q) if t.get("path") == out.get("path")), -1)
        if i == ci and i >= 0:
            return {**base, "state": "playing"}
        if i >= 0 and q[i].get("played"):
            return {**base, "state": "played", "at": q[i].get("played_at", 0)}
        if i > ci:
            return {**base, "state": "queued", "in_sec": round(_queue_eta_sec(i))}
        if i < 0:
            # Nicht mehr in der Warteschlange: lief er (und wurde danach automatisch
            # entfernt) oder hat ihn der DJ wieder herausgenommen?
            since = out.get("at", 0)
            hit = next((h for h in _state.get("play_log", [])
                        if h.get("path") == out.get("path") and (h.get("played_at") or 0) >= since - 1), None)
            if hit:
                return {**base, "state": "played", "at": hit.get("played_at", 0)}
            return {**base, "state": "rejected"}
        return {**base, "state": "accepted"}
    w = next((x for x in _state.get("wishes", []) if x.get("id") == wid), None)
    if w:
        return {"id": wid, "title": w.get("title", ""),
                "state": "failed" if w.get("status") == "fehler" else "pending"}
    return None

def _guest_now_next() -> dict:
    """Laufender Titel und die naechsten drei — fuer die Wunschseite."""
    q, ci = _state.get("queue", []), _state.get("current_idx", -1)
    lib = {lt.get("path"): lt for lt in _state.get("library", [])}
    def _t(t):
        return {"title": t.get("title", ""),
                "artist": t.get("artist", "") or (lib.get(t.get("path")) or {}).get("artist", "")}
    now = _t(q[ci]) if 0 <= ci < len(q) and _state.get("playing") else None
    nxt = [_t(t) for t in q[ci + 1:] if not t.get("played")][:3]
    return {"now": now, "next": nxt}

def _wish_title_status(title: str) -> dict:
    """Laeuft der Titel schon, lief er bereits, oder wuenscht ihn schon wer?"""
    # 1. Steht er in der Warteschlange? Dann die Uhrzeit dazu.
    for i, q in enumerate(_state.get("queue", [])):
        if _title_matches(title, q.get("title", "")):
            if i == _state.get("current_idx", -1):
                return {"state": "playing"}
            if q.get("played"):
                continue
            return {"state": "queued", "in_sec": round(_queue_eta_sec(i))}
    # 2. Lief er an diesem Abend schon? Das Play-Log reicht ueber Wochen
    # zurueck — ohne Grenze stand bei den Gaesten "lief um 21:14 Uhr" fuer
    # einen Titel von letzter Woche, und Wuenschen ging trotzdem.
    seit = time.time() - _WISH_PLAYED_WINDOW
    for entry in _state.get("play_log", []):
        if entry.get("played_at", 0) < seit:
            continue
        if _title_matches(title, entry.get("title", "")):
            return {"state": "played", "at": entry.get("played_at", 0)}
    # 3. Hat ihn schon jemand gewuenscht?
    for w in _state.get("wishes", []):
        if w.get("status") != "abgelehnt" and _title_matches(title, w.get("title", "")):
            return {"state": "wished", "count": w.get("count", 1)}
    return {"state": "free"}

async def _process_wish(wish: dict):
    """Titel herunterladen und analysieren, damit er sofort spielbar ist."""
    def _set(status: str, **kw):
        wish["status"] = status
        wish.update(kw)
        save_wishes()

    _set("laedt")
    await push_wishes()
    # Was schon vor dem Wunsch da war (Bibliothek, fruehere Downloads), darf
    # beim Ablehnen nicht im Papierkorb landen — run_download liefert fuer
    # bereits geladene Titel einfach die vorhandene Datei zurueck.
    vorher = {lt.get("path") for lt in _state.get("library", [])} | \
             {h.get("path") for h in _state.get("history", [])}
    try:
        path = await download.run_download(wish["url"], _state.get("wish_format", "mp3-best"))
    except Exception as e:
        _set("fehler", error=str(e)[:120]); await push_wishes(); return

    if not path or not os.path.exists(path):
        # Den echten Grund aus dem Download-Eintrag holen — "fehlgeschlagen"
        # allein hilft beim Auflegen niemandem weiter.
        eintrag = next((d for d in _state.get("downloads", [])
                        if d.get("url") == wish["url"] and d.get("error_msg")), None)
        grund = (eintrag or {}).get("error_msg") or "Download fehlgeschlagen"
        _set("fehler", error=grund[:120]); await push_wishes(); return

    _set("analysiert", path=path, keep_file=path in vorher)
    await push_wishes()
    try:
        await media._enrich_track(path, force=True)
    except Exception:
        pass          # ohne BPM/LUFS ist er trotzdem spielbar
    _set("bereit", path=path)
    await push_wishes()

remote_app = FastAPI()
remote_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def _off_page(was: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>SynthiMIX</title><body style=\"font-family:system-ui,sans-serif;background:#0b1220;color:#e6edf6;"
        "display:flex;align-items:center;justify-content:center;min-height:90vh;text-align:center;padding:24px\">"
        f"<div><h2 style=\"margin:0 0 8px\">{was} ist gerade ausgeschaltet</h2>"
        "<p style=\"color:#93a4bb\">Der DJ kann sie in SynthiMIX wieder einschalten.</p></div>", status_code=503)

@remote_app.get("/")
async def remote_index(k: str = ""):
    # Ohne gueltigen Schluessel: freundlich auf die Wunschseite
    if not _remote_key_ok(k):
        return RedirectResponse("/wunsch")
    if not _remote_services["remote"]:
        return RedirectResponse("/wunsch") if _remote_services["wishes"] else _off_page("Die Fernbedienung")
    return HTMLResponse(_REMOTE_HTML.replace("__KEY__", _remote_key()))

# Cover werden per ffmpeg aus der Datei geholt — das dauert, also einmal je
# Pfad merken. None heisst "hat keins", damit nicht jedes Mal neu gesucht wird.
_remote_art: dict[str, bytes | None] = {}

@remote_app.get("/cover")
async def remote_cover(i: int = -1, k: str = ""):
    """Cover des Titels an Warteschlangenposition i.

    Bewusst ueber den Index und nicht ueber einen Pfad: ein Pfad aus der
    Anfrage waere ein Weg, beliebige Dateien vom Rechner zu lesen.
    """
    if not _remote_key_ok(k):
        return Response(status_code=403)
    q = _state.get("queue", [])
    if not (0 <= i < len(q)):
        return Response(status_code=404)
    path = q[i].get("path", "")
    if not path or not os.path.exists(path):
        return Response(status_code=404)

    if path not in _remote_art:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, media._extract_art_sync, path)
        if data and data.startswith("data:image/jpeg;base64,"):
            _remote_art[path] = base64.b64decode(data.split(",", 1)[1])
        else:
            _remote_art[path] = None
        if len(_remote_art) > 200:
            _remote_art.clear()

    art = _remote_art[path]
    if not art:
        return Response(status_code=404)
    return Response(content=art, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=3600"})

_WISH_HTML = _web_page("wunsch.html")

@remote_app.get("/wunsch")
async def wish_page():
    if not _remote_services["wishes"]:
        return _off_page("Die Musikwunsch-Seite")
    return HTMLResponse(_WISH_HTML)

# Wie viele offene Wuensche ein Geraet gleichzeitig haben darf. Ohne Grenze
# kippt ein einzelner Spassvogel die Liste voll.
_WISH_LIMIT_PER_CLIENT = 3

@remote_app.websocket("/wunsch/ws")
async def wish_ws(websocket: WebSocket):
    """Bewusst getrennt vom Remote: hier gibt es keine Wiedergabesteuerung."""
    global _wish_counter
    if not _remote_services["wishes"]:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _wish_clients.add(websocket)
    who = websocket.client.host if websocket.client else "?"
    try:
        while True:
            msg = await websocket.receive_json()
            t = msg.get("type", "")

            if t == "wish_search":
                q = (msg.get("query") or "").strip()
                if q:
                    core._start_search(websocket, "wish", _do_wish_search(q, websocket))

            elif t == "wish_info":
                ids = [int(i) for i in (msg.get("ids") or [])[:20] if str(i).isdigit()]
                mine = [st for st in (_guest_wish_status(i) for i in ids) if st]
                await websocket.send_text(json.dumps({"type": "wish_info", "mine": mine,
                                                      **_guest_now_next()}))

            elif t == "wish_add":
                name   = _wish_text(msg.get("name"), 30)
                note   = _wish_text(msg.get("note"), 80)
                url    = (msg.get("url") or "").strip()
                title  = (msg.get("title") or "").strip()
                lib_id = (msg.get("lib") or "").strip()
                lib_entry = None
                if lib_id:
                    # Nie einen Pfad vom Gast uebernehmen — nur ueber die Kennung
                    lib_entry = next((lt for lt in _state.get("library", [])
                                      if lt.get("path") and _lib_wish_id(lt["path"]) == lib_id), None)
                    if lib_entry is None:
                        continue
                    title = lib_entry.get("title", "") or title
                elif not url.startswith("http"):
                    continue

                offen = [w for w in _state.get("wishes", []) if w.get("from") == who]
                if len(offen) >= _WISH_LIMIT_PER_CLIENT:
                    await websocket.send_text(json.dumps({"type": "wish_deny",
                        "text": f"Du hast schon {len(offen)} Wuensche offen. Warte, bis der DJ sie bearbeitet hat."}))
                    continue

                # Schon gewuenscht? Dann nur hochzaehlen — das zeigt dem DJ,
                # was die Leute wirklich hoeren wollen.
                vorhanden = next((w for w in _state.get("wishes", [])
                                  if (url and w.get("url") == url)
                                  or (lib_entry and w.get("path") == lib_entry["path"])
                                  or _title_matches(title, w.get("title", ""))), None)
                if vorhanden:
                    vorhanden["count"] = vorhanden.get("count", 1) + 1
                    _wish_sender(vorhanden, name, note)
                    save_wishes()
                    await push_wishes()
                    # Mit Kennung — so verfolgt auch der zweite Gast den Wunsch
                    await websocket.send_text(json.dumps({"type": "wish_ack", "title": title,
                                                          "id": vorhanden.get("id")}))
                    continue

                _wish_counter += 1
                wish = {"id": _wish_counter, "url": url, "title": title,
                        "from": who, "count": 1, "status": "neu",
                        "path": None, "error": "", "created_at": int(time.time())}
                _wish_sender(wish, name, note)
                if lib_entry is not None:
                    # Liegt schon auf der Platte: sofort bereit, nichts zu laden
                    wish.update(status="bereit", path=lib_entry["path"], from_library=True)
                _state.setdefault("wishes", []).append(wish)
                save_wishes()
                await push_wishes()
                await websocket.send_text(json.dumps({"type": "wish_ack", "title": title,
                                                      "id": wish["id"]}))
                if lib_entry is None:
                    asyncio.create_task(_process_wish(wish))
                elif lib_entry.get("lufs", -99) <= -90:
                    asyncio.create_task(media._enrich_track(lib_entry["path"]))
    except Exception:
        pass
    finally:
        _wish_clients.discard(websocket)

def _wish_text(v, limit: int) -> str:
    """Freitext vom Gast: nur druckbare Zeichen, Leerraum zusammengefasst, gekuerzt."""
    t = re.sub(r"[\x00-\x1f\x7f]", " ", str(v or ""))
    return re.sub(r"\s+", " ", t).strip()[:limit]

def _wish_sender(wish: dict, name: str, note: str):
    """Name(n) und Nachricht am Wunsch merken — nur fuer den DJ sichtbar."""
    if name and name not in wish.setdefault("names", []) and len(wish["names"]) < 5:
        wish["names"].append(name)
    if note:
        wish["note"] = f"{name}: {note}" if name and wish.get("note") else note

def _lib_wish_id(path: str) -> str:
    """Kennung eines Bibliothekstitels fuer die Wunschseite — der Pfad selbst
    bleibt auf dem Rechner, Gaeste sollen die Ordnerstruktur nicht sehen."""
    import hashlib
    return hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:12]

def _wish_library_hits(query: str, limit: int = 5) -> list[dict]:
    words = [w for w in query.lower().split() if w]
    if not words:
        return []
    hits = []
    for lt in _state.get("library", []):
        if lt.get("missing") or not lt.get("path"):
            continue
        text = f"{lt.get('title', '')} {lt.get('artist', '')}".lower()
        if all(w in text for w in words):
            hits.append(lt)
            if len(hits) >= limit:
                break
    return hits

_WISH_SKIP_RE = re.compile(r"\b(instrumental|karaoke|acapella|a\s*cappella|sped\s*up|slowed|nightcore|8d\s*audio|backing\s*track)\b", re.IGNORECASE)


async def _do_wish_search(query: str, ws: WebSocket):
    # Erst die eigene Sammlung: sofort spielbar, kein Download, keine
    # YouTube-Kopie in schlechter Qualitaet. Die Treffer gehen gleich raus,
    # YouTube folgt ein paar Sekunden spaeter.
    lib_hits = [{"lib": _lib_wish_id(lt["path"]), "title": lt.get("title", ""),
                 "uploader": lt.get("artist", ""),
                 "status": _wish_title_status(lt.get("title", ""))}
                for lt in _wish_library_hits(query)]
    async def _send(results, final):
        try:
            await ws.send_text(json.dumps({"type": "wish_results", "results": results,
                                           "final": final}))
        except Exception:
            pass
    if lib_hits:
        await _send(lib_hits, False)

    def online(results):
        # Songs tragen erst nach _ytm_fill_details den Kuenstler — fuer Gaeste
        # und den Abgleich mit der Sammlung als "Kuenstler - Titel" zeigen
        out = []
        for r in results:
            title = f'{r["artist"]} - {r["title"]}' if r.get("kind") == "song" and r.get("artist") else r["title"]
            if any(_title_matches(title, h["title"]) for h in lib_hits):
                continue   # gibt es schon in der Sammlung
            out.append({"url": r["url"], "title": title, "uploader": r.get("uploader", ""),
                        "status": _wish_title_status(title)})
        return out[:max(3, 8 - len(lib_hits))]

    # Nur Song-Versionen von YouTube Music — keine beliebigen YouTube-Videos,
    # keine Instrumentals, Karaoke- oder Live-Fassungen
    songs = await search._ytm_songs(query, 10, details=False)
    if songs and not all(r.get("duration") for r in songs):
        await _send(lib_hits + online(songs), False)
        await search._ytm_fill_details(songs)
    songs = [r for r in songs if not search._is_unwanted_result(r) and not search.LIVE_RE.search(r.get("title", ""))
             and not _WISH_SKIP_RE.search(r.get("title", ""))]
    await _send(lib_hits + online(songs), True)

_icon_png: dict[int, bytes] = {}

def _draw_icon(size: int) -> bytes:
    """SynthiMIX-Logo (Balken + Pfeil) als PNG — fuer "Zum Startbildschirm"."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    k = 4                                   # groesser zeichnen, dann glatt verkleinern
    S = size * k
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * .22), fill=(13, 26, 46, 255))
    u = S / 40.0
    for x, y, h, col in ((5, 16, 9, (224, 120, 0)), (11, 10, 15, (224, 120, 0)), (17, 13, 12, (245, 147, 50)),
                         (23, 7, 18, (224, 120, 0)), (29, 11, 14, (245, 147, 50))):
        d.rounded_rectangle((x * u + u, y * u + u, (x + 4) * u + u, (y + h) * u + u), radius=int(1.5 * u), fill=col + (255,))
    w = max(1, int(2 * u))
    d.line((21 * u, 29 * u, 21 * u, 35 * u), fill=(59, 130, 246, 255), width=w)
    d.line((17 * u, 32 * u, 21 * u, 36 * u, 25 * u, 32 * u), fill=(59, 130, 246, 255), width=w, joint="curve")
    out = BytesIO()
    img.resize((size, size), Image.LANCZOS).save(out, "PNG")
    return out.getvalue()

@remote_app.get("/icon-{size}.png")
async def remote_icon(size: int):
    if size not in (192, 512):
        return Response(status_code=404)
    if size not in _icon_png:
        try:
            _icon_png[size] = _draw_icon(size)
        except Exception:
            return Response(status_code=404)
    return Response(content=_icon_png[size], media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})

@remote_app.get("/manifest.json")
async def remote_manifest(k: str = ""):
    """Macht die Seite ueber "Zum Startbildschirm" zur eigenstaendigen App."""
    if not _remote_key_ok(k):
        return Response(status_code=403)
    return JSONResponse({
        "name": "SynthiMIX Remote",
        "short_name": "SynthiMIX",
        "start_url": f"/?k={_remote_key()}",
        "display": "standalone",
        "background_color": "#0a0e18",
        "theme_color": "#080c16",
        "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    })

@remote_app.websocket("/ws")
async def remote_ws_endpoint(websocket: WebSocket):
    if not _remote_key_ok(websocket.query_params.get("k")) or not _remote_services["remote"]:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    _remote_clients.add(websocket)
    try:
        await _send_remote_state(websocket)
        while True:
            msg = await websocket.receive_json()
            t = msg.get("type", "")
            if t == "queue_move":
                q = _state["queue"]
                fi, ti = int(msg.get("from", 0)), int(msg.get("to", 0))
                if 0 <= fi < len(q) and 0 <= ti < len(q) and fi != ti:
                    item = q.pop(fi)
                    q.insert(ti, item)
                    ci = _state.get("current_idx", -1)
                    if ci == fi:            _state["current_idx"] = ti
                    elif fi < ci <= ti:     _state["current_idx"] = ci - 1
                    elif ti <= ci < fi:     _state["current_idx"] = ci + 1
                    store.save_queue()
                    await core.push_queue()
            elif t == "queue_remove":
                idx = int(msg.get("index", -1))
                q = _state["queue"]
                # Ueber den Pfad: hat sich die Warteschlange seit dem Anzeigen
                # geaendert, traf der Index sonst einen anderen Titel.
                path = msg.get("path")
                if path:
                    if not (0 <= idx < len(q) and q[idx].get("path") == path):
                        idx = next((i for i, t in enumerate(q) if t.get("path") == path), -1)
                if 0 <= idx < len(q):
                    q.pop(idx)
                    ci = _state.get("current_idx", -1)
                    if idx < ci:   _state["current_idx"] = ci - 1
                    elif idx == ci: _state["current_idx"] = min(ci, len(q) - 1)
                    store.save_queue()
                    await core.push_queue()
            elif t == "queue_append":
                path = msg.get("path", "")
                lib = _state.get("library", [])
                td = next((x for x in lib if x.get("path") == path), None)
                if td and not library._queue_is_duplicate(path, td.get("title", "")):
                    _state["queue"].append({
                        "path": path,
                        "title": td.get("title") or Path(path).stem,
                        "duration_sec": td.get("duration_sec", 0),
                        "lufs": td.get("lufs", -99.0),
                        "bpm": td.get("bpm", 0),
                        "bitrate_kbps": td.get("bitrate_kbps", 0),
                        "played": False,
                    })
                    store.save_queue()
                    await core.push_queue()
            elif t == "queue_insert_next":
                path = msg.get("path", "")
                lib = _state.get("library", [])
                td = next((x for x in lib if x.get("path") == path), None)
                if td:
                    ci = _state.get("current_idx", -1)
                    insert_at = ci + 1 if ci >= 0 else 0
                    _state["queue"].insert(insert_at, {
                        "path": path,
                        "title": td.get("title") or Path(path).stem,
                        "duration_sec": td.get("duration_sec", 0),
                        "lufs": td.get("lufs", -99.0),
                        "bpm": td.get("bpm", 0),
                        "bitrate_kbps": td.get("bitrate_kbps", 0),
                        "played": False,
                    })
                    store.save_queue()
                    await core.push_queue()
            elif t == "search_library":
                query = (msg.get("query") or "").strip().lower()
                if query == "__clear__":
                    query = ""
                if query or msg.get("match"):
                    results = _remote_lib_search(query, bool(msg.get("match")))
                    await websocket.send_text(json.dumps({"type": "search_results", "results": results}))
            elif t == "yt_search_remote":
                query = msg.get("query", "").strip()
                if query:
                    core._start_search(websocket, "remote", _do_yt_search_remote(query, websocket))
            elif t == "yt_dl_queue":
                url = msg.get("url", "")
                title = msg.get("title", "")
                as_next = bool(msg.get("as_next", False))
                if url:
                    asyncio.create_task(_remote_dl_and_queue(url, title, websocket, as_next))
            elif t in ("set_normalize_volume", "wish_accept", "wish_reject",
                       "set_auto_mix", "set_radio", "queue_harmonic", "mix_now"):
                await handle_message(websocket, msg)
            elif t == "remote_playlists":
                await websocket.send_text(json.dumps(
                    {"type": "playlists", "items": library._get_playlists()}))
            elif t == "remote_load_playlist":
                # Pfad gegen die bekannten Playlisten pruefen — load_playlist
                # wuerde sonst jede beliebige Datei einlesen.
                want  = msg.get("path", "")
                known = {pl.get("path") for pl in library._get_playlists()}
                if want in known:
                    await handle_message(websocket, {"type": "load_playlist", "path": want})
            elif t in ("pause", "resume", "play_at", "play_next", "play_prev",
                       "set_volume", "seek"):
                await handle_message(websocket, msg)
    except Exception:
        pass
    finally:
        _remote_clients.discard(websocket)

def _bpm_near(a: float, b: float, tol: float = 0.08) -> float | None:
    """Relativer Tempo-Abstand (auch halbes/doppeltes Tempo) oder None."""
    if not (a and b):
        return None
    best = min(abs(a / (b * m) - 1) for m in (0.5, 1, 2))
    return best if best <= tol else None

def _remote_lib_search(query: str, match: bool) -> list[dict]:
    """Bibliothek fuer die Fernbedienung durchsuchen. match: nur Titel, die zum
    laufenden passen (Tonart gleich/benachbart, Tempo hoechstens 8 % weg);
    ohne Suchbegriff sind das Vorschlaege fuer den naechsten Titel."""
    q, ci = _state.get("queue", []), _state.get("current_idx", -1)
    lib = _state.get("library", [])
    by_path = {lt.get("path"): lt for lt in lib}
    cur = by_path.get(q[ci].get("path")) if 0 <= ci < len(q) else None
    ck = (cur or {}).get("key") or ""
    cb = float((cur or {}).get("bpm") or 0)
    kommt = {t.get("path") for t in q[max(ci, 0):]}
    words = [w for w in query.split() if w]
    out = []
    for x in lib:
        if not x.get("path") or x.get("missing"):
            continue
        if words:
            text = f"{x.get('title', '')} {x.get('artist', '')}".lower()
            if not all(w in text for w in words):
                continue
        comp = keys._key_compat(ck, x.get("key")) if ck and x.get("key") else -1
        near = _bpm_near(cb, float(x.get("bpm") or 0))
        if match:
            if x.get("path") in kommt or comp < 2 or near is None:
                continue
        out.append((x, comp, near))
        if not match and len(out) >= 30:
            break
    if match:
        out.sort(key=lambda r: (-r[1], r[2] if r[2] is not None else 1))
    return [{"title": x.get("title", ""), "artist": x.get("artist", ""), "path": x.get("path", ""),
             "duration_sec": x.get("duration_sec", 0), "key": x.get("key", ""), "key_src": x.get("key_src", ""),
             "bpm": x.get("bpm", 0), "compat": comp}
            for x, comp, _ in out[:30]]

async def _do_yt_search_remote(query: str, ws: WebSocket):
    async def send(results):
        try:
            await ws.send_text(json.dumps({"type": "yt_results", "results": [
                {"url": r["url"], "title": r["title"], "uploader": r.get("uploader", ""),
                 "duration": r.get("duration", 0)}
                for r in results[:8]
            ]}))
        except Exception:
            pass

    songs, videos = await search._songs_and_videos(query, 4, 8)
    if songs and all(r.get("duration") and r.get("artist") for r in songs):
        songs = [r for r in songs if not search._is_unwanted_result(r) and not search.LIVE_RE.search(r.get("title", ""))]
        await send(search._merge_songs_first(songs, videos))
        return
    await send(search._merge_songs_first(songs, videos))
    if songs:
        await search._ytm_fill_details(songs)
        songs = [r for r in songs if not search._is_unwanted_result(r) and not search.LIVE_RE.search(r.get("title", ""))]
        await send(search._merge_songs_first(songs, videos))

async def _remote_dl_and_queue(url: str, title: str, ws: WebSocket, as_next: bool = False):
    try:
        path = await download.run_download(url)
        if path and os.path.exists(path):
            lib = _state.get("library", [])
            td = next((x for x in lib if x.get("path") == path), None)
            entry = {
                "path": path,
                "title": (td.get("title") if td else None) or title or Path(path).stem,
                "duration_sec": (td.get("duration_sec") if td else 0) or 0,
                "lufs": (td.get("lufs") if td else -99.0) or -99.0,
                "bpm": (td.get("bpm") if td else 0) or 0,
                "bitrate_kbps": (td.get("bitrate_kbps") if td else 0) or 0,
                "played": False,
            }
            if not library._queue_is_duplicate(path, entry["title"]):
                if as_next:
                    ci = _state.get("current_idx", -1)
                    _state["queue"].insert(ci + 1, entry)
                else:
                    _state["queue"].append(entry)
                store.save_queue()
                await core.push_queue()
            status = "done"
        else:
            status = "error"
    except Exception:
        status = "error"
    try:
        await ws.send_text(json.dumps({"type": "yt_dl_status", "title": title, "status": status}))
    except Exception:
        pass

def _remote_state_payload() -> str:
    q = _state.get("queue", [])
    ci = _state.get("current_idx", -1)
    nxt = q[ci + 1] if 0 <= ci + 1 < len(q) else None
    # Tonart und BPM kommen aus der Bibliothek — Queue-Eintraege tragen sie nicht
    lib = {lt.get("path"): lt for lt in _state.get("library", [])}
    def _q_item(i, t):
        lt = lib.get(t.get("path")) or {}
        key = lt.get("key", "") or ""
        prev_key = (lib.get(q[i - 1].get("path")) or {}).get("key", "") if i > 0 else ""
        return {"title": t.get("title", ""), "artist": t.get("artist", "") or lt.get("artist", ""),
                "duration_sec": t.get("duration_sec", 0),
                "played": t.get("played", False),
                "path": t.get("path", ""),
                "key": key, "key_src": lt.get("key_src", ""),
                "bpm": lt.get("bpm", 0) or t.get("bpm", 0),
                # 3 gleich, 2 passt, 0 passt nicht, -1 unbekannt (wie in der App)
                "compat": keys._key_compat(prev_key, key) if key and prev_key else -1,
                "energy_boost": bool(t.get("energy_boost"))}
    return json.dumps({
        "type":        "state",
        "playing":     _state.get("playing", False),
        "current_idx": ci,
        "volume":      _state.get("volume", 80),
        "position_ms": _state.get("position_ms", 0),
        "duration_ms": _state.get("duration_ms", 0),
        "next_title":  nxt.get("title", "") if nxt else "",
        "next_artist": nxt.get("artist", "") if nxt else "",
        "normalize_volume": _state.get("normalize_volume", True),
        "target_lufs":      _state.get("target_lufs", -10.0),
        "queue": [_q_item(i, t) for i, t in enumerate(q)],
        "auto_mix":      _state.get("auto_mix", True),
        "radio_enabled": _state.get("radio_enabled", False),
        "radio_ok":      bool(_state.get("lastfm_api_key", "").strip()),
        # Ohne Pfade — die gehen nur die App etwas an
        "wishes": [{"id": w.get("id"), "title": w.get("title", ""), "count": w.get("count", 1),
                    "status": w.get("status", ""), "error": w.get("error", ""),
                    "names": w.get("names", []), "note": w.get("note", "")}
                   for w in _state.get("wishes", [])],
    })

async def _send_remote_state(ws: WebSocket):
    await ws.send_text(_remote_state_payload())

async def _broadcast_remote_state():
    if not _remote_clients:
        return
    payload = _remote_state_payload()
    dead = set()
    for ws in list(_remote_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _remote_clients.difference_update(dead)

async def _broadcast_remote_pos():
    if not _remote_clients:
        return
    payload = json.dumps({
        "type": "pos",
        "pos": _state.get("position_ms", 0),
        "dur": _state.get("duration_ms", 0),
    })
    dead = set()
    for ws in list(_remote_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _remote_clients.difference_update(dead)

async def _start_remote_server(requester: WebSocket):
    global _remote_server
    if _remote_server is not None:
        await core.broadcast(_remote_status_msg())
        return
    try:
        ip = _get_local_ip()
        config = uvicorn.Config(remote_app, host="0.0.0.0", port=_remote_port, log_level="warning")
        server = uvicorn.Server(config)
        _remote_server = server

        async def _serve_task():
            global _remote_server
            try:
                await server.serve()
            except OSError as e:
                print(f"[remote] Port {_remote_port} nicht verfügbar: {e}", flush=True)
            except Exception as e:
                print(f"[remote] serve Fehler: {e}", flush=True)
            finally:
                if _remote_server is server:
                    _remote_server = None
                    try:
                        await core.broadcast(_remote_status_msg())
                    except Exception:
                        pass

        asyncio.create_task(_serve_task())
        # Kurz warten bis uvicorn den Port gebunden hat
        await asyncio.sleep(0.3)
        if _remote_server is None:
            # Startup fehlgeschlagen (z.B. Port belegt)
            try:
                await requester.send_text(json.dumps(_remote_status_msg(error=f"Port {_remote_port} nicht verfügbar")))
            except Exception:
                pass
            return
        print(f"[remote] gestartet auf http://{ip}:{_remote_port}", flush=True)
        await core.broadcast(_remote_status_msg())
    except Exception as e:
        print(f"[remote] Fehler beim Starten: {e}", flush=True)
        _remote_server = None
        try:
            await requester.send_text(json.dumps(_remote_status_msg(error=str(e))))
        except Exception:
            pass

async def _stop_remote_server():
    global _remote_server
    if _remote_server:
        _remote_server.should_exit = True
        _remote_server = None
        print("[remote] gestoppt", flush=True)
