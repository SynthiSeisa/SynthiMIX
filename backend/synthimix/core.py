"""Grundlagen: Protokollpuffer, Pfade, Werkzeuge (ffmpeg, yt-dlp), gemeinsamer Zustand, Senden an die Oberflaeche."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from fastapi import WebSocket
from pathlib import Path
from typing import Any

"""
YT-Downloader backend — FastAPI + WebSocket
Replaces the PyQt5 main thread with a clean async server.
"""

# ── In-Memory Log Buffer ──────────────────────────────────────────────────────
_log_buffer: deque = deque(maxlen=600)

class _LogTee:
    """Schreibt gleichzeitig auf den Original-Stream und in den Log-Buffer."""
    def __init__(self, original, prefix=""):
        self._orig   = original
        self._prefix = prefix
        self._buf    = ""
    def write(self, s):
        # Original-Stream: CP1252 auf Windows kann kein Unicode → replace unbekannte Zeichen
        try:
            self._orig.write(s)
        except (UnicodeEncodeError, UnicodeDecodeError):
            safe = s.encode(self._orig.encoding or 'utf-8', errors='replace').decode(self._orig.encoding or 'utf-8', errors='replace')
            try:
                self._orig.write(safe)
            except Exception:
                pass
        # Buffer speichert immer volles Unicode (kein Encoding-Problem im RAM)
        self._buf += s
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            line = line.rstrip()
            if line:
                _log_buffer.append(self._prefix + line)
    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass
    def fileno(self):
        return self._orig.fileno()
    def isatty(self):
        return False

# UTF-8 für stdout/stderr erzwingen (Windows verwendet sonst CP1252)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.stdout = _LogTee(sys.stdout)
sys.stderr = _LogTee(sys.stderr, prefix="")

# ── paths ────────────────────────────────────────────────────────────────────
# In packaged mode Electron passes --data-dir so we write to %APPDATA%\SynthiMIX
# SYNTHIMIX_PORT nur fuer Tests neben einem laufenden SynthiMIX, das 8765 belegt.
# Electron setzt die Variable nicht; das Frontend verbindet sich fest auf 8765.
_BACKEND_PORT = int(os.environ.get("SYNTHIMIX_PORT", "8765"))

_data_dir_arg = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == '--data-dir' and i+1 < len(sys.argv)), None)
# Electron reicht seinen eigenen Pfad durch — er dient yt-dlp als JS-Laufzeit
_electron_exe = next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == '--electron-exe' and i+1 < len(sys.argv)), None)
if _data_dir_arg:
    BASE_DIR = Path(_data_dir_arg)
    BASE_DIR.mkdir(parents=True, exist_ok=True)
else:
    BASE_DIR = Path(__file__).parent.parent.parent

FPCALC_LOCAL  = BASE_DIR / "fpcalc.exe"   # downloaded via Settings
SPOTDL_LOCAL  = BASE_DIR / "spotdl.exe"   # downloaded via Settings

def _find_fpcalc() -> str | None:
    """Return path to fpcalc executable or None."""
    if FPCALC_LOCAL.exists():
        return str(FPCALC_LOCAL)
    found = shutil.which('fpcalc')
    if found:
        return found
    for p in [
        r'C:\Program Files\Chromaprint\fpcalc.exe',
        r'C:\Program Files (x86)\Chromaprint\fpcalc.exe',
    ]:
        if os.path.exists(p):
            return p
    return None

QUEUE_FILE    = BASE_DIR / "queue.json"
SETTINGS_FILE = BASE_DIR / "settings.json"
LIB_CACHE     = BASE_DIR / "library_cache.json"
PLAYLISTS_DIR = BASE_DIR / "playlists"
HISTORY_FILE  = BASE_DIR / "history.json"
PLAY_LOG_FILE = BASE_DIR / "play_log.json"
NOTES_FILE    = BASE_DIR / "notes.json"
WISHES_FILE   = BASE_DIR / "wishes.json"

# Exe dir (where ffmpeg/yt-dlp are bundled in packaged mode)
_frozen   = getattr(sys, 'frozen', False)
_exe_dir  = Path(sys.executable).parent if _frozen else Path(__file__).parent.parent.parent
_meipass  = Path(sys._MEIPASS) if _frozen and hasattr(sys, '_MEIPASS') else None

# Kein Console-Fenster bei subprocess-Aufrufen auf Windows (wichtig im gepackten Modus)
# Alle Hilfsprogramme (yt-dlp, ffmpeg, fpcalc) ohne Fenster und mit niedrigerer
# Prioritaet: Suchen, Analysen und Downloads duerfen der laufenden Musik keine
# Rechenzeit wegnehmen. Vorher stockte die Wiedergabe, wenn Gaeste Wuensche
# suchten (bis zu sechs yt-dlp-Prozesse je Suche, mehrere Suchen gleichzeitig).
_NO_WINDOW = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if sys.platform == "win32" else 0

# Hintergrund-Aufgaben: asyncio haelt Tasks nur schwach — ohne eigene
# Referenz kann eine noch laufende Aufgabe vom Garbage Collector eingesammelt
# werden. Ausserdem landen Fehler darin im Log statt still zu verschwinden.
_tasks: set = set()

def _task_done(t: asyncio.Task):
    _tasks.discard(t)
    if not t.cancelled() and t.exception() is not None:
        name = getattr(t.get_coro(), "__qualname__", "?")
        print(f"[aufgabe] {name}: {t.exception()!r}", flush=True)

def sender(ws):
    """send(typ, **felder) an genau diese Verbindung. Ist sie inzwischen zu
    (Fenster geschlossen, Handy gesperrt), wird die Nachricht verworfen."""
    async def send(t, **kw):
        try:
            await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception:
            pass
    return send

def spawn(coro) -> asyncio.Task:
    """asyncio.create_task mit gehaltener Referenz und Fehler-Log."""
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_task_done)
    return t

# Nur die jeweils letzte Suche einer Verbindung laeuft weiter; eine neue
# Eingabe bricht die vorige ab (samt ihren yt-dlp-Prozessen).
_latest_search: dict = {}


def _start_search(ws, kind: str, coro):
    key = (id(ws), kind)
    old = _latest_search.get(key)
    if old is not None and not old.done():
        old.cancel()
    task = spawn(coro)
    _latest_search[key] = task

    def _done(t, key=key):
        if _latest_search.get(key) is t:
            _latest_search.pop(key, None)
    task.add_done_callback(_done)
    return task

def _find_tool(name: str, *extra_dirs: Path) -> str:
    candidates = [
        _exe_dir / name,
        *([_meipass / name] if _meipass else []),
        _exe_dir / "_internal" / name,   # PyInstaller 6.x onedir layout
        *[d / name for d in extra_dirs],
    ]
    return next((str(p) for p in candidates if p.exists()), name.replace(".exe", ""))

FFMPEG  = _find_tool("ffmpeg.exe",
    Path(__file__).parent.parent.parent,
    Path(__file__).parent.parent.parent / "bin",
    Path(os.environ.get("FFMPEG_PATH", "")))
FFPROBE = _find_tool("ffprobe.exe",
    Path(__file__).parent.parent.parent,
    Path(__file__).parent.parent.parent / "bin",
    Path(os.environ.get("FFMPEG_PATH", "")))

# Aktualisierte ffmpeg-Versionen liegen je in einem eigenen Ordner
# tools/ffmpeg-<Builddatum>/ im Datenordner. So muss nie eine Exe ersetzt
# werden, die gerade laeuft (Analysen nutzen ffmpeg staendig).
FFMPEG_TOOLS_DIR = BASE_DIR / "tools"

def _ffmpeg_build_date(ver: str) -> str:
    """Builddatum aus der Versionszeile ("N-125258-g…-20260624" → "20260624")."""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})\b", ver or "")
    return m.group(0) if m else ""

def _ffmpeg_local_builds() -> list[tuple[str, Path]]:
    out = []
    try:
        for d in FFMPEG_TOOLS_DIR.glob("ffmpeg-*"):
            if (d / "ffmpeg.exe").exists() and (d / "ffprobe.exe").exists():
                out.append((d.name.split("-", 1)[1], d))
    except Exception:
        pass
    return sorted(out)

def _use_newest_ffmpeg():
    """Beim Start: die neueste eigene Kopie nehmen und aeltere wegraeumen."""
    global FFMPEG, FFPROBE
    builds = _ffmpeg_local_builds()
    if not builds:
        return
    date, d = builds[-1]
    # Bringt ein App-Update selbst einen neueren Build mit, gilt der
    try:
        r = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10, creationflags=_NO_WINDOW)
        mitgeliefert = _ffmpeg_build_date(r.stdout)
    except Exception:
        mitgeliefert = ""
    if mitgeliefert and mitgeliefert >= date:
        return
    FFMPEG, FFPROBE = str(d / "ffmpeg.exe"), str(d / "ffprobe.exe")
    for _, old in builds[:-1]:
        shutil.rmtree(old, ignore_errors=True)

_use_newest_ffmpeg()

clients: set[WebSocket] = set()

_state: dict[str, Any] = {
    "queue":        [],   # list of track dicts
    "current_idx":  -1,
    "playing":      False,
    "position_ms":  0,
    "duration_ms":  0,
    "downloads":    [],
    "library":      [],
    "volume":       80,
    "crossfade_s":  8,
    "shuffle":      False,
    "repeat":       0,     # 0=off 1=track 2=all
    "auto_mix":        True,
    "history":            [],    # list of {url, title, path, date, bitrate_kbps}
    "play_log":           [],    # list of {path, title, artist, played_at} — actual playback, most recent first
    "notes":              "",    # free-text scratchpad (bug notes etc.)
    "wishes":             [],    # Musikwuensche der Gaeste, siehe _process_wish
    "loudnorm_on_dl":     False,
    "loudnorm_target":    -10.0,
    "scan_recursive":     True,
    "watched_folders":    [],
    "auto_remove_played": False,
}

# ── broadcast ────────────────────────────────────────────────────────────────
async def broadcast(msg: dict):
    dead = set()
    for ws in list(clients):   # Kopie: waehrend await kann sich die Menge aendern
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)

async def push_queue():
    await broadcast({"type": "queue", "items": _state["queue"],
                     "current_idx": _state["current_idx"]})
    if remote._remote_clients:
        spawn(remote._broadcast_remote_state())
    spawn(automix._check_radio_queue())

async def push_player():
    await broadcast({
        "type":  "player_state",
        "state": {
            "playing":     _state["playing"],
            "position_ms": _state["position_ms"],
            "duration_ms": _state["duration_ms"],
            "current_idx": _state["current_idx"],
            "shuffle":     _state.get("shuffle", False),
            "repeat":      _state.get("repeat", 0),
        }
    })
    if remote._remote_clients:
        spawn(remote._broadcast_remote_state())

_dl_last_push: float = 0.0

async def push_downloads(force: bool = False):
    global _dl_last_push
    now = time.monotonic()
    if force or (now - _dl_last_push) >= 0.2:
        _dl_last_push = now
        await broadcast({"type": "downloads", "items": _state["downloads"]})

# Bibliothek an die Oberflaeche: nur geaenderte Titel. Frueher ging bei jeder
# Kleinigkeit (ein Tag, ein Messwert) die ganze Liste (~2.700 Titel, ~1 MB)
# raus und die Oberflaeche baute alles neu auf. Je Verbindung wird gemerkt,
# welchen Stand sie hat (Pfad -> Pruefsumme des Eintrags).
_lib_sent: dict = {}              # WebSocket -> {pfad: signatur}
_LIB_DELTA_MAX = 0.4              # mehr als 40 % geaendert: ganze Liste

def _lib_sigs() -> dict:
    return {lt.get("path"): hash(json.dumps(lt, sort_keys=True, ensure_ascii=False))
            for lt in _state["library"] if lt.get("path")}

async def send_library_full(ws):
    _lib_sent[ws] = _lib_sigs()
    await ws.send_text(json.dumps({"type": "library", "tracks": _state["library"]}))

async def push_library():
    sigs = _lib_sigs()
    by_path = None
    for ws in list(clients):
        old = _lib_sent.get(ws)
        try:
            if old is None:
                _lib_sent[ws] = sigs
                await ws.send_text(json.dumps({"type": "library", "tracks": _state["library"]}))
                continue
            changed = [p for p, h in sigs.items() if old.get(p) != h]
            removed = [p for p in old if p not in sigs]
            _lib_sent[ws] = sigs
            if not changed and not removed:
                continue
            if len(changed) + len(removed) > _LIB_DELTA_MAX * max(1, len(sigs)):
                await ws.send_text(json.dumps({"type": "library", "tracks": _state["library"]}))
                continue
            if by_path is None:
                by_path = {lt.get("path"): lt for lt in _state["library"]}
            await ws.send_text(json.dumps({"type": "library_delta",
                                           "upsert": [by_path[p] for p in changed], "remove": removed}))
        except Exception:
            clients.discard(ws)
            _lib_sent.pop(ws, None)

YTDLP = _find_tool("yt-dlp.exe", BASE_DIR, BASE_DIR / "bin")

# Aktualisiertes yt-dlp liegt als tools/yt-dlp-<Version>.exe im Datenordner.
# Frueher wurde die Exe im Installationsordner ersetzt — das scheiterte mit
# "[WinError 5] Zugriff verweigert" (laufender Prozess, Schreibschutz), und
# yt-dlp blieb stehen, bis YouTube die alte Version aussperrte.
_YTDLP_TOOLS = BASE_DIR / "tools"

def _ytdlp_tool_builds() -> list[tuple[tuple, Path]]:
    out = []
    try:
        for f in _YTDLP_TOOLS.glob("yt-dlp-*.exe"):
            ver = f.stem.split("-", 2)[-1]
            nums = tuple(int(n) for n in re.findall(r"\d+", ver))
            if nums:
                out.append((nums, f))
    except Exception:
        pass
    return sorted(out)

def _use_newest_ytdlp():
    """Beim Start die neueste eigene Kopie nehmen (wenn neuer als die
    mitgelieferte) und aeltere wegraeumen."""
    global YTDLP
    builds = _ytdlp_tool_builds()
    if not builds:
        return
    nums, f = builds[-1]
    try:
        r = subprocess.run([YTDLP, "--version"], capture_output=True, text=True, timeout=15,
                           creationflags=_NO_WINDOW)
        mit = tuple(int(n) for n in re.findall(r"\d+", r.stdout or ""))
    except Exception:
        mit = ()
    if not mit or nums > mit:
        YTDLP = str(f)
    for _, old in builds[:-1]:
        try: old.unlink()
        except Exception: pass

_use_newest_ytdlp()

# ── JavaScript-Laufzeit fuer yt-dlp ──────────────────────────────────────────
# Ohne sie warnt yt-dlp, dass die YouTube-Extraktion ohne JS-Runtime veraltet
# ist und Formate fehlen koennen — irgendwann werden daraus echte Fehlschlaege.
# Electron bringt Node mit: mit ELECTRON_RUN_AS_NODE=1 verhaelt sich die
# SynthiMIX.exe wie ein node-Binary, das yt-dlp direkt benutzen kann. Damit
# braucht es weder ein System-Node noch einen zusaetzlichen Download.
def _find_js_runtime() -> list[str]:
    if _electron_exe and os.path.exists(_electron_exe):
        return ["--js-runtimes", f"node:{_electron_exe}"]
    for name in ("deno", "node"):          # Fallback fuer den Dev-Betrieb
        found = shutil.which(name)
        if found:
            return ["--js-runtimes", f"{name}:{found}"]
    return []

_JS_ARGS = _find_js_runtime()
# Gilt fuer alle Kindprozesse dieses Backends. Betrifft nur die Faelle, in denen
# tatsaechlich die Electron-Exe als Laufzeit gestartet wird; ffmpeg, yt-dlp und
# spotdl ignorieren die Variable.
os.environ["ELECTRON_RUN_AS_NODE"] = "1"
print(f"[backend] JS-Laufzeit: {_JS_ARGS[1] if _JS_ARGS else 'keine gefunden'}", flush=True)

def _yt(*args: str) -> list[str]:
    """yt-dlp-Kommando inklusive JS-Laufzeit, Ausgabe immer UTF-8.

    Ohne --encoding schreibt yt-dlp unter Windows in der ANSI-Codepage — die
    Titel kamen dann verstuemmelt an ("Arc\ufffdngel" statt "Arcángel"), in
    der Playlist-Pruefung wie in den Suchergebnissen."""
    return [YTDLP, *_JS_ARGS, "--encoding", "utf-8", *args]
FFMPEG_DIR = str(Path(FFMPEG).parent) if FFMPEG != "ffmpeg" else ""

# Zwischendateien mit Audio-Endung: yt-dlp ("Titel.temp.mp3" beim Umwandeln)
# und die eigenen beim Tag-Schreiben/Normalisieren. Bricht etwas ab, bleiben sie
# liegen — sie sind keine Titel und gehoeren nicht in die Bibliothek.
_TEMP_AUDIO_RE = re.compile(r'\.(temp|__tmp|norm_tmp)\.[a-z0-9]{2,5}$', re.I)

def is_temp_audio(name: str) -> bool:
    return bool(_TEMP_AUDIO_RE.search(name or ""))

def _kill_quietly(proc):
    """Prozess einer abgebrochenen Suche beenden (sonst liefe yt-dlp weiter)."""
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


# Andere Module erst am Ende: sie greifen beim Import auf core zu
from . import automix, remote  # noqa: E402
