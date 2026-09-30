"""Externe Werkzeuge: yt-dlp, ffmpeg, spotdl, fpcalc aktuell halten; Changelog."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request as _urllib_req
import zipfile
from fastapi import WebSocket
from pathlib import Path
from .core import _NO_WINDOW, _state
from . import core, store

_CHANGELOG_API  = "https://api.github.com/repos/SynthiSeisa/SynthiMIX/releases?per_page=40"
_CHANGELOG_FILE = core.BASE_DIR / "changelog_cache.json"

def _changelog_sync() -> list[dict]:
    """Release-Notes aller Versionen von GitHub; offline die zuletzt geladenen."""
    try:
        req = _urllib_req.Request(_CHANGELOG_API, headers={"User-Agent": "SynthiMIX",
                                                            "Accept": "application/vnd.github+json"})
        with _urllib_req.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        items = [{"tag": x.get("tag_name", ""), "name": x.get("name") or x.get("tag_name", ""),
                  "date": (x.get("published_at") or "")[:10], "body": x.get("body") or ""}
                 for x in data if not x.get("draft") and not x.get("prerelease")]
        if items:
            store._save_json(_CHANGELOG_FILE, items)
            return items
    except Exception as e:
        print(f"[changelog] offline oder Fehler: {e}", flush=True)
    cached = store._load_json(_CHANGELOG_FILE, [])
    return cached if isinstance(cached, list) else []

def _find_spotdl_cmd() -> list[str] | None:
    """Return spotdl command as a list, or None if not found."""
    if core.SPOTDL_LOCAL.exists():
        return [str(core.SPOTDL_LOCAL)]
    exe = shutil.which("spotdl")
    if exe:
        return [exe]
    for py in ("py", "python", "python3"):
        p = shutil.which(py)
        if not p:
            continue
        try:
            r = subprocess.run([p, "-m", "spotdl", "--version"],
                               capture_output=True, timeout=5, creationflags=_NO_WINDOW)
            if r.returncode == 0:
                return [p, "-m", "spotdl"]
        except Exception:
            pass
    return None

# spotdl.exe entpackt sich bei jedem Start erst (~46 MB). Nach einem Neustart
# oder Update, wenn der Virenscanner sie neu prueft, dauerte "--version" laenger
# als die 8 s von frueher — dann stand "Nicht installiert" da, obwohl die Datei
# im Datenordner lag. Jetzt: laenger warten, Ergebnis je Datei merken, und eine
# vorhandene Datei gilt immer als installiert.
_spotdl_ver_cache: dict = {}

def _spotdl_version_sync() -> str | None:
    spotdl = _find_spotdl_cmd()
    if not spotdl:
        return None
    try:
        st = os.stat(spotdl[0]) if len(spotdl) == 1 else None
        key = (spotdl[0], st.st_size, int(st.st_mtime)) if st else tuple(spotdl)
    except OSError:
        key = tuple(spotdl)
    if key in _spotdl_ver_cache:
        return _spotdl_ver_cache[key]
    try:
        r = subprocess.run(spotdl + ["--version"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=45, creationflags=_NO_WINDOW)
        m = re.search(r'(\d+\.\d+[\.\d]*)', r.stdout + r.stderr)
        ver = m.group(1) if m else "installiert"
        _spotdl_ver_cache[key] = ver
        return ver
    except Exception as e:
        print(f"[spotdl] Version nicht lesbar ({e.__class__.__name__}) — Datei ist aber da", flush=True)
        return "installiert"

async def _check_tools(ws: WebSocket):
    loop = asyncio.get_running_loop()
    info: dict = {}
    def _sync():
        try:
            r = subprocess.run([core.YTDLP, "--version"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=8, creationflags=_NO_WINDOW)
            info["ytdlp_version"] = r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            info["ytdlp_version"] = None
        try:
            r = subprocess.run([core.FFMPEG, "-version"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=8, creationflags=_NO_WINDOW)
            first = (r.stdout or "").splitlines()[0]
            m = re.search(r'version\s+(\S+)', first)
            info["ffmpeg_version"] = m.group(1) if m else first[:40]
        except Exception:
            info["ffmpeg_version"] = None
        info["spotdl_version"] = _spotdl_version_sync()
        info["fpcalc_found"] = bool(core._find_fpcalc())
    await loop.run_in_executor(None, _sync)
    try:
        await ws.send_text(json.dumps({"type": "tools_info", **info}))
    except Exception:
        pass

async def _update_ytdlp(ws: WebSocket | None = None):
    import urllib.request as _req
    loop = asyncio.get_running_loop()
    async def _send(text, pct):
        # ws ist None, wenn die taegliche Pruefung das Update anstoesst
        if ws is None:
            print(f"[yt-dlp] {text}", flush=True); return
        try: await ws.send_text(json.dumps({"type": "update_progress", "text": text, "pct": pct}))
        except Exception: pass
    await _send("Suche neueste Version…", 0)
    try:
        api = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
        hdrs = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github+json"}
        def _meta():
            req = _req.Request(api, headers=hdrs)
            with _req.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        data     = await loop.run_in_executor(None, _meta)
        tag      = data.get("tag_name", "")
        exe_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
        dl_url   = next((a["browser_download_url"] for a in data.get("assets", [])
                         if a["name"] == exe_name), None)
        if not dl_url:
            await _send("❌ Release nicht gefunden", -1); return False
        await _send(f"Lade {tag}…", 10)
        core._YTDLP_TOOLS.mkdir(parents=True, exist_ok=True)
        ver  = re.sub(r"[^\w.]", "", tag.lstrip("v")) or str(int(time.time()))
        dest = core._YTDLP_TOOLS / f"yt-dlp-{ver}.exe"
        tmp  = str(dest) + ".part"
        def _dl():
            req = _req.Request(dl_url, headers={"User-Agent": "Mozilla/5.0"})
            with _req.urlopen(req, timeout=180) as r:
                with open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(65536)
                        if not chunk: break
                        f.write(chunk)
        await loop.run_in_executor(None, _dl)
        os.replace(tmp, str(dest))
        alt = core.YTDLP
        core.YTDLP = str(dest)
        # Aeltere eigene Kopien weg; eine gerade laufende bleibt bis zum naechsten Start
        for _, old in core._ytdlp_tool_builds():
            if old != dest:
                try: old.unlink()
                except Exception: pass
        print(f"[yt-dlp] jetzt {core.YTDLP} (vorher {alt})", flush=True)
        await _send(f"✓ {tag} installiert", 100)
        tu = _state.setdefault("tool_updates", {})
        tu["ytdlp"] = {"latest": tag, "available": False}
        store.save_settings()
        await core.broadcast({"type": "tool_updates", "items": tu})
        if ws is not None:
            await _check_tools(ws)
        return True
    except Exception as e:
        await _send(f"❌ {e}", -1)
        return False

# ── fpcalc auto-download ──────────────────────────────────────────────────────
_FPCALC_URL = "https://github.com/acoustid/chromaprint/releases/download/v1.5.1/chromaprint-fpcalc-1.5.1-windows-x86_64.zip"

async def _download_fpcalc(ws):
    async def _send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    await _send("fpcalc_install_progress", text="Wird heruntergeladen…")
    loop = asyncio.get_running_loop()
    try:
        def _do_download():
            import tempfile as _tf
            with _tf.NamedTemporaryFile(suffix='.zip', delete=False) as _f:
                tmp = Path(_f.name)
            _urllib_req.urlretrieve(_FPCALC_URL, tmp)
            with zipfile.ZipFile(tmp) as zf:
                names = zf.namelist()
                exe_name = next((n for n in names if n.lower().endswith('fpcalc.exe')), None)
                if not exe_name:
                    raise FileNotFoundError("fpcalc.exe not in zip")
                with zf.open(exe_name) as src, open(core.FPCALC_LOCAL, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
            tmp.unlink(missing_ok=True)
        await loop.run_in_executor(None, _do_download)
    except Exception as e:
        await _send("fpcalc_install_error", text=str(e))
        return

    await _send("fpcalc_install_done")
    # Re-broadcast tools_info so UI updates fpcalc_found flag
    await _send("tools_info", fpcalc_found=True)

# ── spotdl auto-install ───────────────────────────────────────────────────────
# Standalone-Exe statt "pip install": im gepackten Betrieb ist sys.executable
# backend.exe (kein pip), und ein System-Python ist auf fremden PCs nicht
# vorausgesetzt. Der Download läuft damit in Dev und Release identisch.
_SPOTDL_API      = "https://api.github.com/repos/spotDL/spotify-downloader/releases/latest"
_SPOTDL_FALLBACK = "https://github.com/spotDL/spotify-downloader/releases/download/v4.5.2/spotdl-4.5.2-win32.exe"

def _spotdl_asset_url() -> str:
    """Neueste Windows-Exe aus der GitHub-Release-API, sonst gepinnte Version."""
    try:
        req = _urllib_req.Request(_SPOTDL_API, headers={"User-Agent": "SynthiMIX"})
        with _urllib_req.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        for a in data.get("assets", []):
            if a.get("name", "").lower().endswith("-win32.exe"):
                return a["browser_download_url"]
    except Exception:
        pass
    return _SPOTDL_FALLBACK

def _spotdl_latest_tag() -> str:
    try:
        req = _urllib_req.Request(_SPOTDL_API, headers={"User-Agent": "SynthiMIX"})
        with _urllib_req.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", errors="replace")).get("tag_name", "")
    except Exception:
        return ""

def _spotify_download_active() -> bool:
    return any(d.get("status") == "active" and d.get("session_label") == "Spotify"
               for d in _state.get("downloads", []))

async def _install_spotdl(ws):
    async def _send(t, **kw):
        if ws is None:                       # automatische Aktualisierung
            if kw.get("text"):
                print(f"[spotdl] {kw['text']}", flush=True)
            return
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    # Unter Windows laesst sich die Exe nicht ersetzen, solange sie laeuft
    if _spotify_download_active():
        await _send("spotdl_install_error",
                    text="Erst nach dem laufenden Spotify-Download aktualisieren")
        return
    await _send("spotdl_install_progress", text="Suche Download…")
    loop = asyncio.get_running_loop()
    last_pct = -1

    def _on_block(done_blocks, block_size, total):
        nonlocal last_pct
        if total <= 0:
            return
        pct = min(100, int(done_blocks * block_size * 100 / total))
        if pct != last_pct and pct % 5 == 0:
            last_pct = pct
            mb = total / 1024 / 1024
            asyncio.run_coroutine_threadsafe(
                _send("spotdl_install_progress", text=f"Lädt… {pct}% von {mb:.0f} MB"), loop)

    try:
        url = await loop.run_in_executor(None, _spotdl_asset_url)

        def _do_install():
            tmp = core.SPOTDL_LOCAL.with_suffix(".part")
            _urllib_req.urlretrieve(url, tmp, _on_block)
            # Erst nach vollständigem Download an den finalen Platz — ein
            # abgebrochener Download soll nicht als "installiert" gelten.
            tmp.replace(core.SPOTDL_LOCAL)
        await loop.run_in_executor(None, _do_install)
    except Exception as e:
        core.SPOTDL_LOCAL.with_suffix(".part").unlink(missing_ok=True)
        await _send("spotdl_install_error", text=str(e))
        return

    # Detect version after install
    cmd = _find_spotdl_cmd()
    version = None
    if cmd:
        try:
            r = subprocess.run(cmd + ["--version"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=8, creationflags=_NO_WINDOW)
            m = re.search(r'(\d+\.\d+[\.\d]*)', r.stdout + r.stderr)
            version = m.group(1) if m else "installiert"
        except Exception:
            version = "installiert"
    tu = _state.setdefault("tool_updates", {})
    alt = tu.get("spotdl") or {}
    tu["spotdl"] = {"current": version or "", "latest": alt.get("latest", ""),
                    "available": _is_newer(alt.get("latest", ""), version or "")}
    store.save_settings()
    await core.broadcast({"type": "tool_updates", "items": tu})
    await _send("spotdl_install_done", version=version)

_FFMPEG_API   = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
_FFMPEG_ASSET = "ffmpeg-master-latest-win64-gpl.zip"
_FFMPEG_MAX_AGE_DAYS = 60      # erst ab diesem Rueckstand neu laden (~200 MB)

def _ffmpeg_version_sync(exe: str | None = None) -> str:
    try:
        r = subprocess.run([exe or core.FFMPEG, "-version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10, creationflags=_NO_WINDOW)
        first = (r.stdout or "").splitlines()[0] if r.stdout else ""
        m = re.search(r"version\s+(\S+)", first)
        return m.group(1) if m else ""
    except Exception:
        return ""

def _ffmpeg_latest_sync() -> dict:
    """Neuester Build bei BtbN: {date, url, size} oder {}."""
    try:
        req = _urllib_req.Request(_FFMPEG_API, headers={"User-Agent": "SynthiMIX",
                                                         "Accept": "application/vnd.github+json"})
        with _urllib_req.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        for a in data.get("assets", []):
            if a.get("name") == _FFMPEG_ASSET:
                date = (a.get("updated_at") or data.get("published_at") or "")[:10].replace("-", "")
                return {"date": date, "url": a["browser_download_url"], "size": a.get("size", 0)}
    except Exception:
        pass
    return {}

def _days_between(a: str, b: str) -> int:
    from datetime import datetime
    try:
        return (datetime.strptime(b, "%Y%m%d") - datetime.strptime(a, "%Y%m%d")).days
    except Exception:
        return 0

def _install_ffmpeg_sync(info: dict) -> str:
    """Zip laden, ffmpeg.exe und ffprobe.exe in tools/ffmpeg-<datum>/ legen,
    pruefen, dass sie laufen. Liefert den neuen Ordner oder ''."""
    core.FFMPEG_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    target = core.FFMPEG_TOOLS_DIR / f"ffmpeg-{info['date']}"
    tmp_zip = core.FFMPEG_TOOLS_DIR / f"ffmpeg-{info['date']}.zip.part"
    tmp_dir = core.FFMPEG_TOOLS_DIR / f"ffmpeg-{info['date']}.part"
    try:
        _urllib_req.urlretrieve(info["url"], tmp_zip)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True)
        with zipfile.ZipFile(tmp_zip) as z:
            for name in z.namelist():
                base = name.rsplit("/", 1)[-1].lower()
                if base in ("ffmpeg.exe", "ffprobe.exe"):
                    with z.open(name) as src, open(tmp_dir / base, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        if not _ffmpeg_version_sync(str(tmp_dir / "ffmpeg.exe")):
            raise RuntimeError("neue ffmpeg.exe startet nicht")
        if not (tmp_dir / "ffprobe.exe").exists():
            raise RuntimeError("ffprobe.exe fehlt im Paket")
        shutil.rmtree(target, ignore_errors=True)
        tmp_dir.rename(target)
        return str(target)
    except Exception as e:
        print(f"[ffmpeg] Update fehlgeschlagen: {e}", flush=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return ""
    finally:
        try: tmp_zip.unlink()
        except Exception: pass

async def _ffmpeg_check(tu: dict, now: int, force: bool = False):
    """Hoechstens einmal die Woche bei BtbN nachsehen; ist die eigene Version
    mehr als zwei Monate aelter, mit Automatik neu laden, sonst Hinweis."""
    if sys.platform != "win32":
        return
    if not force and now - _state.get("ffmpeg_last_check", 0) < 7 * 86400:
        return
    loop = asyncio.get_running_loop()
    lokal = await loop.run_in_executor(None, _ffmpeg_version_sync, None)
    latest = await loop.run_in_executor(None, _ffmpeg_latest_sync)
    _state["ffmpeg_last_check"] = now
    lokal_date = core._ffmpeg_build_date(lokal)
    if not latest or not lokal_date:
        return
    alt = _days_between(lokal_date, latest["date"]) > _FFMPEG_MAX_AGE_DAYS
    tu["ffmpeg"] = {"current": lokal_date, "latest": latest["date"], "available": alt}
    if alt and _state.get("ytdlp_autoupdate", True):
        print(f"[ffmpeg] Build {lokal_date} ist veraltet, lade {latest['date']}", flush=True)
        folder = await loop.run_in_executor(None, _install_ffmpeg_sync, latest)
        if folder:
            core.FFMPEG  = str(Path(folder) / "ffmpeg.exe")
            core.FFPROBE = str(Path(folder) / "ffprobe.exe")
            tu["ffmpeg"] = {"current": latest["date"], "latest": latest["date"], "available": False}
            tu["ffmpeg_updated"] = {"from": lokal_date, "to": latest["date"], "at": now}
            await core.broadcast({"type": "tools_info", "ffmpeg_version": await loop.run_in_executor(None, _ffmpeg_version_sync, None)})

async def _update_ffmpeg(ws: WebSocket | None = None):
    """ffmpeg von Hand auf den neuesten Build bringen (Dienste → ffmpeg)."""
    loop = asyncio.get_running_loop()
    async def _send(text, pct):
        if ws is None:
            print(f"[ffmpeg] {text}", flush=True); return
        try: await ws.send_text(json.dumps({"type": "update_progress", "text": text, "pct": pct, "tool": "ffmpeg"}))
        except Exception: pass
    await _send("Suche neuesten Build…", 0)
    latest = await loop.run_in_executor(None, _ffmpeg_latest_sync)
    if not latest:
        await _send("❌ Keine Verbindung zu GitHub", -1); return False
    lokal = core._ffmpeg_build_date(await loop.run_in_executor(None, _ffmpeg_version_sync, None))
    if lokal and lokal >= latest["date"]:
        await _send("✓ Schon der neueste Build", 100); return True
    size = f" (~{round(latest.get('size', 0) / 1e6)} MB)" if latest.get("size") else ""
    await _send(f"Lade Build {latest['date']}{size}…", 10)
    folder = await loop.run_in_executor(None, _install_ffmpeg_sync, latest)
    if not folder:
        await _send("❌ Laden oder Entpacken fehlgeschlagen", -1); return False
    core.FFMPEG  = str(Path(folder) / "ffmpeg.exe")
    core.FFPROBE = str(Path(folder) / "ffprobe.exe")
    tu = _state.setdefault("tool_updates", {})
    tu["ffmpeg"] = {"current": latest["date"], "latest": latest["date"], "available": False}
    store.save_settings()
    await core.broadcast({"type": "tool_updates", "items": tu})
    await core.broadcast({"type": "tools_info", "ffmpeg_version": await loop.run_in_executor(None, _ffmpeg_version_sync, None)})
    await _send(f"✓ ffmpeg-Build {latest['date']} installiert", 100)
    return True

def _ytdlp_version_sync() -> str:
    try:
        r = subprocess.run([core.YTDLP, "--version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=10, creationflags=_NO_WINDOW)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""

def _ytdlp_latest_tag() -> str:
    try:
        req = _urllib_req.Request(
            "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest",
            headers={"User-Agent": "SynthiMIX", "Accept": "application/vnd.github+json"})
        with _urllib_req.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("tag_name", "")
    except Exception:
        return ""

def _ver_tuple(v: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", v or ""))

def _is_newer(latest: str, current: str) -> bool:
    """Ist latest eine neuere Version als current? "v4.4.3" > "4.4.2",
    "2026.09.12" > "2026.07.04". Unbekanntes zaehlt nie als neuer."""
    a, b = _ver_tuple(latest), _ver_tuple(current)
    return bool(a) and bool(b) and a > b

async def _tools_check_once(force: bool = False):
    """yt-dlp und spotdl mit dem neuesten Release auf GitHub vergleichen.

    Bisher lief die Pruefung nur fuer yt-dlp und nur mit eingeschalteter
    Automatik — und sagte nie etwas. spotdl wurde gar nicht geprueft, obwohl
    die Exe ihr eigenes yt-dlp mitbringt, das genauso altert. Jetzt landet das
    Ergebnis in tool_updates; die App zeigt daraus einen Hinweis.
    """
    loop = asyncio.get_running_loop()
    tu   = _state.setdefault("tool_updates", {})
    now  = int(time.time())

    lokal   = await loop.run_in_executor(None, _ytdlp_version_sync)
    neueste = await loop.run_in_executor(None, _ytdlp_latest_tag)
    _state["ytdlp_last_check"] = now
    if lokal and neueste:
        veraltet = _is_newer(neueste, lokal)
        # Nicht mitten in einen laufenden Download platzen: unter Windows
        # laesst sich die Exe nicht ersetzen, solange sie benutzt wird.
        busy = any(d.get("status") == "active" for d in _state.get("downloads", []))
        if veraltet and _state.get("ytdlp_autoupdate", True) and not busy:
            print(f"[yt-dlp] {lokal} ist veraltet, neueste ist {neueste}", flush=True)
            if await _update_ytdlp():
                neu = await loop.run_in_executor(None, _ytdlp_version_sync)
                tu["ytdlp_updated"] = {"from": lokal, "to": neu or neueste, "at": now}
                veraltet = _is_newer(neueste, neu)
                await core.broadcast({"type": "tools_info", "ytdlp_version": neu})
        tu["ytdlp"] = {"latest": neueste, "available": veraltet}
    elif not lokal:
        tu.pop("ytdlp", None)       # nicht installiert: kein alter Hinweis stehen lassen
    # Ohne Netz (neueste leer) bleibt der letzte bekannte Stand

    if await loop.run_in_executor(None, _find_spotdl_cmd):
        s_lokal   = await loop.run_in_executor(None, _spotdl_version_sync)
        s_neueste = await loop.run_in_executor(None, _spotdl_latest_tag)
        # "installiert" = Version nicht lesbar (Start zu langsam): nicht vergleichen
        if s_lokal and s_lokal != "installiert" and s_neueste:
            tu["spotdl"] = {"current": s_lokal, "latest": s_neueste.lstrip("v"),
                            "available": _is_newer(s_neueste, s_lokal)}
            # Nur die von SynthiMIX geladene Exe selbst ersetzen — ein per pip
            # oder von Hand installiertes spotdl gehoert dem Nutzer.
            if (tu["spotdl"]["available"] and _state.get("ytdlp_autoupdate", True)
                    and core.SPOTDL_LOCAL.exists() and not _spotify_download_active()):
                await _install_spotdl(None)
                s_neu = await loop.run_in_executor(None, _spotdl_version_sync)
                if s_neu and s_neu != s_lokal:
                    tu["spotdl_updated"] = {"from": s_lokal, "to": s_neu, "at": now}
    else:
        tu.pop("spotdl", None)

    try:
        await _ffmpeg_check(tu, now, force=force)
    except Exception as e:
        print(f"[ffmpeg] Pruefung fehlgeschlagen: {e}", flush=True)

    store.save_settings()
    await core.broadcast({"type": "tool_updates", "items": tu})

async def _ytdlp_autoupdate_loop():
    """Taeglich pruefen, ob yt-dlp veraltet ist.

    YouTube dreht regelmaessig an der Auslieferung, wodurch aeltere Versionen
    mit HTTP 403 abbrechen. Im August 2026 hat eine knapp drei Monate alte
    Version einen Grossteil der Downloads gekostet, ohne dass man der App
    angesehen haette woran es liegt — genau das soll hier nicht wieder passieren.
    """
    await asyncio.sleep(90)          # die App erst hochkommen lassen
    while True:
        try:
            # Auch ohne Automatik pruefen — dann gibt es einen Hinweis statt
            # eines stillen Updates.
            if time.time() - _state.get("ytdlp_last_check", 0) > 86400:
                await _tools_check_once()
        except Exception as e:
            print(f"[yt-dlp] Pruefung fehlgeschlagen: {e}", flush=True)
        await asyncio.sleep(6 * 3600)
