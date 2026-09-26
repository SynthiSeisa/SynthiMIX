"""
YT-Downloader backend — FastAPI + WebSocket
Replaces the PyQt5 main thread with a clean async server.
"""
import asyncio, json, os, sys, math, subprocess, re, base64, random, time, shutil, zipfile, tempfile
import urllib.request as _urllib_req
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

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

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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
    BASE_DIR = Path(__file__).parent.parent

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
_exe_dir  = Path(sys.executable).parent if _frozen else Path(__file__).parent.parent
_meipass  = Path(sys._MEIPASS) if _frozen and hasattr(sys, '_MEIPASS') else None

# Kein Console-Fenster bei subprocess-Aufrufen auf Windows (wichtig im gepackten Modus)
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

def _find_tool(name: str, *extra_dirs: Path) -> str:
    candidates = [
        _exe_dir / name,
        *([_meipass / name] if _meipass else []),
        _exe_dir / "_internal" / name,   # PyInstaller 6.x onedir layout
        *[d / name for d in extra_dirs],
    ]
    return next((str(p) for p in candidates if p.exists()), name.replace(".exe", ""))

FFMPEG  = _find_tool("ffmpeg.exe",
    Path(__file__).parent.parent,
    Path(__file__).parent.parent / "bin",
    Path(os.environ.get("FFMPEG_PATH", "")))
FFPROBE = _find_tool("ffprobe.exe",
    Path(__file__).parent.parent,
    Path(__file__).parent.parent / "bin",
    Path(os.environ.get("FFMPEG_PATH", "")))

# ── app ──────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    load_queue()
    load_library()
    load_settings()
    load_history()
    load_play_log()
    load_notes()
    load_wishes()
    _resume_wishes()
    asyncio.create_task(_watcher_loop())
    asyncio.create_task(_auto_scan_loop())
    asyncio.create_task(_ytdlp_autoupdate_loop())
    asyncio.create_task(_refresh_tag_meta_task())
    asyncio.create_task(_quality_scan_loop())
    print(f"[backend] ready on ws://127.0.0.1:{_BACKEND_PORT}/ws", flush=True)
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

clients: set[WebSocket] = set()
_remote_clients: set[WebSocket] = set()
_remote_server: Any = None
_remote_port = 8080
_dl_procs: dict[int, Any] = {}  # session_id → asyncio.Process (for kill support)

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
    if _remote_clients:
        asyncio.create_task(_broadcast_remote_state())
    asyncio.create_task(_check_radio_queue())

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
    if _remote_clients:
        asyncio.create_task(_broadcast_remote_state())

_dl_last_push: float = 0.0

async def push_downloads(force: bool = False):
    global _dl_last_push
    now = time.monotonic()
    if force or (now - _dl_last_push) >= 0.2:
        _dl_last_push = now
        await broadcast({"type": "downloads", "items": _state["downloads"]})

async def push_library():
    await broadcast({"type": "library", "tracks": _state["library"]})

# ── persistence ──────────────────────────────────────────────────────────────
def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default

def _save_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    tmp.replace(path)

_save_scheduled: bool = False

def schedule_save():
    """Debounced save: writes library at most once per 3 s during bulk operations."""
    global _save_scheduled
    if _save_scheduled:
        return
    _save_scheduled = True
    async def _do():
        global _save_scheduled
        await asyncio.sleep(3)
        _save_scheduled = False
        save_library()
    asyncio.create_task(_do())

_notes_save_scheduled: bool = False

def schedule_notes_save():
    """Debounced save: writes notes at most once per second while typing."""
    global _notes_save_scheduled
    if _notes_save_scheduled:
        return
    _notes_save_scheduled = True
    async def _do():
        global _notes_save_scheduled
        await asyncio.sleep(1)
        _notes_save_scheduled = False
        save_notes()
    asyncio.create_task(_do())

def load_queue():
    raw = _load_json(QUEUE_FILE, {})
    if isinstance(raw, list):
        raw = {"items": raw, "current_idx": 0, "position_ms": 0}
    items = raw.get("items", [])
    valid = [it for it in items if it.get("path") and os.path.exists(it["path"])]
    for it in valid:
        if it.get("title"):
            it["title"] = _fix_mojibake(it["title"])
    _state["queue"]       = valid
    # Der gespeicherte Index bezieht sich auf die Liste VOR dem Aussortieren
    # fehlender Dateien — ueber den Titel selbst wiederfinden.
    saved_idx = int(raw.get("current_idx", -1))
    saved_item = items[saved_idx] if 0 <= saved_idx < len(items) else None
    vorhanden = {id(it) for it in valid}
    if saved_item is not None and id(saved_item) in vorhanden:
        _state["current_idx"] = next(i for i, it in enumerate(valid) if it is saved_item)
    elif saved_item is not None and valid:
        # Der laufende Titel fehlt selbst: auf den naechsten, der noch da ist
        davor = sum(1 for it in items[:saved_idx] if id(it) in vorhanden)
        _state["current_idx"] = min(davor, len(valid) - 1)
    else:
        _state["current_idx"] = -1 if not valid else 0
    _state["position_ms"] = int(raw.get("position_ms", 0))

def save_queue():
    # Strip 'art' (base64) before saving — can be MB per track
    slim = [{k: v for k, v in t.items() if k != "art"} for t in _state["queue"]]
    _save_json(QUEUE_FILE, {
        "items":       slim,
        "current_idx": _state["current_idx"],
        "position_ms": _state["position_ms"],
    })

_MOJIBAKE_C1 = ('€‚ƒ„…†‡'
                'ˆ‰Š‹ŒŽ‘’“”'
                '•–—˜™š›œžŸ')
_MOJIBAKE_RUN_RE = re.compile(f'(?:[ÃÂ][ -¿{_MOJIBAKE_C1}]|â€[{_MOJIBAKE_C1}])+')

def _fix_mojibake(s: str) -> str:
    """Repair UTF-8 text that was previously misdecoded as cp1252 (e.g. ffprobe
    output read without an explicit encoding) — 'fÃ¼hl' → 'fühl'. Only the
    corrupted run is re-encoded, so legitimate Unicode elsewhere (e.g. a real
    curly apostrophe) in the same string is left untouched."""
    if not s or ('Ã' not in s and 'â€' not in s):
        return s
    def _repl(m):
        chunk = m.group(0)
        try:
            return chunk.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return chunk
    return _MOJIBAKE_RUN_RE.sub(_repl, s)

def load_library():
    raw = _load_json(LIB_CACHE, [])
    # Old PyQt5 format: {"folder": "...", "tracks": [...]}
    if isinstance(raw, dict):
        raw = raw.get("tracks", [])
    # Normalise field names from old format
    normalised = []
    mojibake_fixed = False
    for t in raw:
        orig_title = t.get("title", "")
        fixed_title = _fix_mojibake(orig_title)
        if fixed_title != orig_title:
            mojibake_fixed = True
        normalised.append({
            "path":         str(Path(t.get("path", ""))) if t.get("path") else "",
            "title":        fixed_title,
            "folder":       _fix_mojibake(t.get("folder", "")),
            "duration_sec": float(str(t.get("duration_sec") or t.get("duration") or 0).replace(",", ".")),
            "lufs":         float(str(t.get("lufs", -99)).replace(",", ".")),
            "bpm":          t.get("bpm", 0),
            "bitrate_kbps": t.get("bitrate_kbps") or t.get("bitrate") or 0,
            "comment":      _fix_mojibake(str(t.get("comment", ""))),
            "album_artist": _fix_mojibake(str(t.get("album_artist", ""))),
            # Fehlten hier bis 1.4.2 — der Kuenstler ging dadurch bei jedem
            # Start verloren, Album und Genre kamen gar nicht erst an.
            "artist":       _fix_mojibake(str(t.get("artist", ""))),
            "album":        _fix_mojibake(str(t.get("album", ""))),
            "genre":        _fix_mojibake(str(t.get("genre", ""))),
            "key":          _parse_key(t.get("key")) or "",
            "key_src":      str(t.get("key_src", "")),
            "meta_rev":     int(t.get("meta_rev", 0)),
            "ext":          str(t.get("ext", Path(str(t.get("path",""))).suffix.lstrip('.').lower())),
            "mtime":        int(t.get("mtime", 0)),
            "play_count":   int(t.get("play_count", 0)),
            # Obere Grenzfrequenz (Qualitaetspruefung); fehlt = noch nicht gemessen
            **({"cutoff_khz": float(t["cutoff_khz"])} if t.get("cutoff_khz") is not None else {}),
            **({"unanalyzable": True} if t.get("unanalyzable") else {}),
            **({"missing": True} if t.get("missing") else {}),
        })
    # Pfad-Duplikate zusammenführen (alte Scan-Läufe konnten denselben Pfad mehrfach anlegen —
    # mit Duplikaten crasht das gekeyte {#each} im Frontend komplett)
    by_path: dict[str, dict] = {}
    for t in normalised:
        p = t["path"]
        if not p:
            continue
        existing = by_path.get(p)
        if existing is None:
            by_path[p] = t
            continue
        keep, other = existing, t
        if t.get("lufs", -99) > -90 and existing.get("lufs", -99) <= -90:
            keep, other = t, existing
        keep["play_count"] = max(keep.get("play_count", 0), other.get("play_count", 0))
        if keep.get("lufs", -99) <= -90 and other.get("unanalyzable"):
            keep["unanalyzable"] = True
        by_path[p] = keep
    deduped = list(by_path.values())
    if len(deduped) != len(normalised):
        print(f"[library] {len(normalised) - len(deduped)} doppelte Pfad-Einträge bereinigt", flush=True)
    if mojibake_fixed:
        print("[library] Umlaut-Kodierungsfehler in Titeln repariert", flush=True)

    _state["library"] = deduped
    # Beim Start bekannte Problemdateien sofort ins Set laden → Race Condition vermeiden
    _unanalyzable_paths.update(t["path"] for t in deduped if t.get("unanalyzable"))
    if len(deduped) != len(normalised) or mojibake_fixed:
        save_library()

def save_library():
    _save_json(LIB_CACHE, _state["library"])

def load_settings():
    raw = _load_json(SETTINGS_FILE, {})
    _state["volume"]                  = int(raw.get("volume", 80))
    _state["crossfade_s"]             = float(raw.get("crossfade_s", 8))
    _state["bpm_analysis"]    = bool(raw.get("bpm_analysis", True))
    _state["scan_recursive"]  = bool(raw.get("scan_recursive", True))
    _state["watched_folders"] = list(raw.get("watched_folders", []))
    _state["excluded_folders"] = list(raw.get("excluded_folders", []))
    _state["auto_mix"]                = bool(raw.get("auto_mix", True))
    _state["loudnorm_on_dl"]          = bool(raw.get("loudnorm_on_dl", False))
    _state["loudnorm_target"]         = float(raw.get("loudnorm_target", -10.0))
    _state["loudnorm_tp"]             = float(raw.get("loudnorm_tp", -1.5))
    _state["playlist_folder_enabled"] = bool(raw.get("playlist_folder_enabled", True))
    _state["dl_filename_format"]      = str(raw.get("dl_filename_format", "title"))
    _state["download_dir"]            = str(raw.get("download_dir", str(BASE_DIR / "Downloads")))
    _state["auto_scan_interval_min"]  = int(raw.get("auto_scan_interval_min", 0))
    _state["favorites"]               = list(raw.get("favorites", []))
    _state["remote_autostart"]        = bool(raw.get("remote_autostart", False))
    _state["remote_key"]              = str(raw.get("remote_key", ""))
    _state["ytdlp_autoupdate"]        = bool(raw.get("ytdlp_autoupdate", True))
    _state["ytdlp_last_check"]        = int(raw.get("ytdlp_last_check", 0))
    _state["tool_updates"]            = dict(raw.get("tool_updates", {}) or {})
    _state["normalize_volume"]        = bool(raw.get("normalize_volume", True))
    _state["target_lufs"]             = float(raw.get("target_lufs", -10.0))
    _state["spotify_client_id"]       = str(raw.get("spotify_client_id", ""))
    _state["spotify_client_secret"]   = str(raw.get("spotify_client_secret", ""))
    _state["lastfm_api_key"]          = str(raw.get("lastfm_api_key", ""))
    _state["acoustid_api_key"]        = str(raw.get("acoustid_api_key", ""))
    _state["radio_enabled"]           = bool(raw.get("radio_enabled", False))
    # Wiederholen/Zufall wurden nie gespeichert — "Queue-Ende: Wiederholen"
    # in den Einstellungen war nach jedem Neustart wieder weg.
    _state["repeat"]                  = int(raw.get("repeat", 0)) % 3
    _state["shuffle"]                 = bool(raw.get("shuffle", False))

def save_settings():
    _save_json(SETTINGS_FILE, {
        "volume":                   _state["volume"],
        "crossfade_s":              _state["crossfade_s"],
        "bpm_analysis":    _state.get("bpm_analysis", True),
        "scan_recursive":  _state.get("scan_recursive", True),
        "watched_folders": _state.get("watched_folders", []),
        "excluded_folders": _state.get("excluded_folders", []),
        "auto_mix":                _state.get("auto_mix", True),
        "loudnorm_on_dl":          _state.get("loudnorm_on_dl", False),
        "loudnorm_target":         _state.get("loudnorm_target", -10.0),
        "loudnorm_tp":             _state.get("loudnorm_tp", -1.5),
        "playlist_folder_enabled": _state.get("playlist_folder_enabled", True),
        "dl_filename_format":      _state.get("dl_filename_format", "title"),
        "download_dir":            _state.get("download_dir", str(BASE_DIR / "Downloads")),
        "auto_scan_interval_min":  _state.get("auto_scan_interval_min", 0),
        "favorites":               _state.get("favorites", []),
        "remote_autostart":        _state.get("remote_autostart", False),
        "remote_key":              _state.get("remote_key", ""),
        "ytdlp_autoupdate":        _state.get("ytdlp_autoupdate", True),
        "ytdlp_last_check":        _state.get("ytdlp_last_check", 0),
        "tool_updates":            _state.get("tool_updates", {}),
        "normalize_volume":        _state.get("normalize_volume", True),
        "target_lufs":             _state.get("target_lufs", -10.0),
        "spotify_client_id":       _state.get("spotify_client_id", ""),
        "spotify_client_secret":   _state.get("spotify_client_secret", ""),
        "lastfm_api_key":          _state.get("lastfm_api_key", ""),
        "acoustid_api_key":        _state.get("acoustid_api_key", ""),
        "radio_enabled":           _state.get("radio_enabled", False),
        "repeat":                  _state.get("repeat", 0),
        "shuffle":                 _state.get("shuffle", False),
    })

def load_history():
    data = _load_json(HISTORY_FILE, [])
    data = data if isinstance(data, list) else []
    for h in data:
        if h.get("title"):
            h["title"] = _fix_mojibake(h["title"])
    _state["history"] = data

def save_history():
    _save_json(HISTORY_FILE, _state["history"][-500:])  # keep last 500

def _append_history(url: str, title: str, path: str, bitrate_kbps: int):
    _state["history"] = [h for h in _state["history"] if h.get("url") != url]
    from datetime import date
    _state["history"].insert(0, {
        "url": url, "title": title, "path": path,
        "date": date.today().isoformat(), "bitrate_kbps": bitrate_kbps
    })
    save_history()

def load_play_log():
    data = _load_json(PLAY_LOG_FILE, [])
    _state["play_log"] = data if isinstance(data, list) else []

def save_play_log():
    _save_json(PLAY_LOG_FILE, _state["play_log"][:300])

def load_notes():
    data = _load_json(NOTES_FILE, {"text": ""})
    _state["notes"] = str(data.get("text", "")) if isinstance(data, dict) else ""

def save_notes():
    _save_json(NOTES_FILE, {"text": _state["notes"]})

def _record_play(path: str, title: str, artist: str = ""):
    """Track actual playback (most recent first) — used by Auto-Mix to follow listening taste."""
    _state["play_log"].insert(0, {
        "path": path, "title": title, "artist": artist,
        "played_at": int(time.time()),
    })
    _state["play_log"] = _state["play_log"][:300]
    save_play_log()

# ── track enrichment ──────────────────────────────────────────────────────────
def _extract_art_sync(path: str) -> str | None:
    try:
        proc = subprocess.run(
            [FFMPEG, "-nostdin", "-i", path, "-an",
             "-vframes", "1", "-f", "image2", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=10, creationflags=_NO_WINDOW)
        if proc.returncode == 0 and len(proc.stdout) > 200:
            return "data:image/jpeg;base64," + base64.b64encode(proc.stdout).decode()
    except Exception:
        pass
    return None

def _compute_lufs_sync(path: str) -> float:
    """LUFS via ebur128, mit astats-Fallback für zu kurze/ungewöhnliche Dateien."""
    name = Path(path).name
    print(f"[lufs] starte Analyse: '{name}'", flush=True)
    try:
        r = subprocess.run(
            [FFMPEG, "-nostdin", "-i", path,
             "-filter:a", "ebur128=framelog=quiet", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, creationflags=_NO_WINDOW)
        m = re.search(r"I:\s+([-\d.]+)\s+LUFS", r.stderr)
        if m:
            val = round(float(m.group(1)), 1)
            print(f"[lufs] OK '{name}' → {val} LUFS", flush=True)
            return val

        # Fallback: astats für sehr kurze Dateien oder wenn ebur128 kein Ergebnis liefert
        # Schätze LUFS aus RMS-dB (astats mean_volume)
        r2 = subprocess.run(
            [FFMPEG, "-nostdin", "-i", path,
             "-filter:a", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
             "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=_NO_WINDOW)
        m2 = re.search(r"RMS_level=([+-]?\d+(?:\.\d+)?)", r2.stderr + r2.stdout)
        if m2:
            rms_db = float(m2.group(1))
            if math.isfinite(rms_db) and rms_db > -90:
                val = round(rms_db - 3.0, 1)
                print(f"[lufs] Fallback (astats) '{name}' → {val} LUFS", flush=True)
                return val

        name = Path(path).name
        last_lines = [l for l in r.stderr.splitlines() if l.strip()][-5:]
        stderr_text = "\n".join(last_lines)
        # "No such file" → temporärer Fehler (Laufwerk getrennt etc.) → -98.0
        # Dekodierungsfehler (moov, Invalid data) → permanent → -97.0
        if "No such file" in stderr_text or "no such file" in stderr_text.lower():
            print(f"[lufs] NICHT GEFUNDEN '{name}'", flush=True)
            return -98.0
        print(f"[lufs] FEHLER '{name}' (rc={r.returncode}):", flush=True)
        for ln in last_lines:
            print(f"  ffmpeg: {ln}", flush=True)
        return -97.0
    except Exception as e:
        print(f"[lufs] Exception bei '{Path(path).name}': {e}", flush=True)
        return -98.0

def _estimate_bpm_sync(path: str) -> int:
    """BPM via onset-energy autocorrelation (60-180 BPM range)."""
    try:
        sr, hop = 22050, 512
        proc = subprocess.Popen(
            [FFMPEG, "-nostdin", "-i", path,
             "-filter:a", "aformat=channel_layouts=mono",
             "-acodec", "pcm_s16le", "-f", "s16le", "-ar", str(sr),
             "-t", "60", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        raw = proc.stdout.read()
        proc.wait()
        if len(raw) < sr * 2:
            return 0
        import struct
        n_samp  = len(raw) // 2
        samples = struct.unpack(f"{n_samp}h", raw)
        env     = [math.sqrt(max(0, sum(s*s for s in samples[i:i+hop]) / hop))
                   for i in range(0, n_samp - hop, hop)]
        n       = len(env)
        fps     = sr / hop
        lo      = max(1, int(fps * 60 / 180))
        hi      = min(n // 2, int(fps * 60 / 60))
        best_lag, best_c = lo, -1.0
        for lag in range(lo, hi + 1):
            c = sum(env[i] * env[i + lag] for i in range(n - lag))
            if c > best_c:
                best_c = c; best_lag = lag
        bpm = fps * 60.0 / best_lag
        # Correct half-time detection (80 BPM that's actually 160)
        if bpm < 90:
            bpm2 = bpm * 2
            if bpm2 <= 180:
                lag2 = max(lo, int(fps * 60 / bpm2))
                c2   = sum(env[i] * env[i + lag2] for i in range(n - lag2))
                if c2 >= best_c * 0.75:
                    bpm = bpm2
        return int(round(bpm))
    except Exception:
        return 0

# ── Tonart ───────────────────────────────────────────────────────────────────
# Gespeichert wird immer in musikalischer Schreibweise mit ♯ ("F♯m", "C").
# Eingelesen wird alles, was in Tags vorkommt: rekordbox ("F♯m"), Mixed In Key
# (Camelot "4A"), Traktor (Open Key "1m"), Varianten mit #, b und "minor".
_KEY_NAMES = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B']
_KEY_FLATS = {'CB': 11, 'DB': 1, 'EB': 3, 'FB': 4, 'GB': 6, 'AB': 8, 'BB': 10}
# Camelot-Nummer -> Tonhoehenklasse (0 = C). A = Moll, B = Dur.
_CAMELOT_MINOR = {1: 8, 2: 3, 3: 10, 4: 5, 5: 0, 6: 7, 7: 2, 8: 9, 9: 4, 10: 11, 11: 6, 12: 1}
_CAMELOT_MAJOR = {1: 11, 2: 6, 3: 1, 4: 8, 5: 3, 6: 10, 7: 5, 8: 0, 9: 7, 10: 2, 11: 9, 12: 4}

def _parse_key(raw) -> str | None:
    """Beliebige Tonart-Angabe -> "F♯m" / "C", oder None wenn nicht lesbar."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    txt = str(raw).strip().replace('♯', '#').replace('♭', 'b')
    if not txt:
        return None
    m = re.fullmatch(r'0?(1[0-2]|[1-9])\s*([ABab])', txt)                  # Camelot
    if m:
        minor = m.group(2).upper() == 'A'
        pc = (_CAMELOT_MINOR if minor else _CAMELOT_MAJOR)[int(m.group(1))]
        return _KEY_NAMES[pc] + ('m' if minor else '')
    m = re.fullmatch(r'(1[0-2]|[1-9])\s*([dmDM])', txt)                     # Open Key
    if m:
        minor = m.group(2).lower() == 'm'
        camelot = (int(m.group(1)) + 6) % 12 + 1
        pc = (_CAMELOT_MINOR if minor else _CAMELOT_MAJOR)[camelot]
        return _KEY_NAMES[pc] + ('m' if minor else '')
    m = re.fullmatch(r'([A-Ga-g])\s*(#|b)?\s*(m|min|minor|moll|maj|major|dur)?', txt, re.I)
    if not m:
        return None
    note, acc = m.group(1).upper(), m.group(2) or ''
    if acc == 'b':
        pc = _KEY_FLATS.get(note + 'B')
        if pc is None:
            return None
    else:
        pc = _KEY_NAMES.index(note)
        if acc == '#':
            pc = (pc + 1) % 12
    suffix = (m.group(3) or '').lower()
    minor = suffix in ('m', 'min', 'minor', 'moll')
    return _KEY_NAMES[pc] + ('m' if minor else '')

def _key_to_camelot(key) -> tuple[int, str] | None:
    k = _parse_key(key)
    if not k:
        return None
    minor = k.endswith('m')
    pc = _KEY_NAMES.index(k[:-1] if minor else k)
    table = _CAMELOT_MINOR if minor else _CAMELOT_MAJOR
    num = next(n for n, v in table.items() if v == pc)
    return num, 'A' if minor else 'B'

def _key_compat(a, b) -> int:
    """3 = gleiche Tonart, 2 = passt (Parallel- oder Nachbartonart), 0 = passt nicht,
    -1 = mindestens eine Tonart unbekannt."""
    ca, cb = _key_to_camelot(a), _key_to_camelot(b)
    if not ca or not cb:
        return -1
    if ca == cb:
        return 3
    if ca[0] == cb[0]:
        return 2
    if ca[1] == cb[1] and (ca[0] - cb[0]) % 12 in (1, 11):
        return 2
    return 0

def _make_library_entry(path: str, probe: dict, **overrides) -> dict:
    """Bibliothekseintrag aus einem _probe_sync-Ergebnis.

    Frueher bauten vier Stellen ihre Eintraege selbst — Album und Genre wurden
    dabei ueberall vergessen. Neue Felder gehoeren ab jetzt nur noch hierher
    (und in die Normalisierung von load_library).
    """
    p = Path(path)
    key = _parse_key(probe.get("key")) or ""
    entry = {
        "path":         path,
        "title":        probe.get("title") or p.stem,
        "artist":       probe.get("artist", "") or "",
        "album_artist": probe.get("album_artist", "") or "",
        "album":        probe.get("album", "") or "",
        "genre":        probe.get("genre", "") or "",
        "key":          key,
        "key_src":      "tag" if key else "",
        "meta_rev":     _TAG_META_REV,
        "folder":       p.parent.name,
        "ext":          probe.get("ext") or p.suffix.lstrip('.').lower(),
        "duration_sec": probe.get("duration_sec", 0),
        "lufs":         -99.0,
        "bpm":          probe.get("bpm", 0),
        "bitrate_kbps": probe.get("bitrate_kbps", 0),
        "comment":      probe.get("comment", "") or "",
        "mtime":        int(os.path.getmtime(path)) if os.path.exists(path) else 0,
        "play_count":   0,
    }
    entry.update(overrides)
    return entry

def _probe_sync(path: str) -> dict:
    result = {"duration_sec": 0.0, "bitrate_kbps": 0, "bpm": 0,
              "title": Path(path).stem, "artist": "", "album_artist": "",
              "album": "", "genre": "", "key": "",
              "comment": "", "ext": Path(path).suffix.lstrip('.').lower()}
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=_NO_WINDOW)
        d = json.loads(r.stdout)
        fmt = d.get("format", {})
        tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
        result["duration_sec"] = round(float(fmt.get("duration") or 0), 2)
        result["bitrate_kbps"] = int(float(fmt.get("bit_rate") or 0)) // 1000
        result["bpm"]     = int(float(tags.get("bpm") or tags.get("tbpm") or 0))
        result["title"]   = tags.get("title") or Path(path).stem
        result["artist"]  = tags.get("artist") or tags.get("album_artist") or ""
        result["comment"]      = tags.get("comment") or tags.get("description") or ""
        result["album_artist"] = tags.get("album_artist") or tags.get("albumartist") or ""
        result["album"]        = tags.get("album") or ""
        result["genre"]        = tags.get("genre") or ""
        result["key"]          = _parse_key(tags.get("tkey") or tags.get("initialkey")
                                            or tags.get("key")) or ""
        if result["duration_sec"] > 0:
            return result
    except Exception:
        pass
    # Fallback: mutagen (pure-Python, works without ffprobe)
    try:
        import mutagen
        mf = mutagen.File(path)
        if mf and hasattr(mf, 'info'):
            result["duration_sec"]  = round(getattr(mf.info, 'length', 0), 2)
            result["bitrate_kbps"]  = getattr(mf.info, 'bitrate', 0) // 1000
            tags = mf.tags or {}
            def _t(*keys):
                for k in keys:
                    v = tags.get(k)
                    if v is None: continue
                    if hasattr(v, 'text'): return str(v.text[0]) if v.text else ''
                    return str(v[0]) if isinstance(v, list) else str(v)
                return ''
            result["title"]   = _t('TIT2','title','\xa9nam') or Path(path).stem
            result["artist"]  = _t('TPE1','artist','\xa9ART','album_artist')
            result["comment"] = _t('COMM::eng','COMM::deu','COMM','comment','\xa9cmt','description')
            # COMM frames need special handling
            if not result["comment"] and hasattr(tags, 'getall'):
                comms = tags.getall('COMM')
                if comms and hasattr(comms[0], 'text'):
                    result["comment"] = str(comms[0].text[0]) if comms[0].text else ''
            result["album_artist"] = _t('TPE2','album_artist','aART','\xa9aAR')
            result["album"]        = _t('TALB','album','\xa9alb')
            result["genre"]        = _t('TCON','genre','\xa9gen')
            result["key"]          = (_read_tags_sync(path) or {}).get("key", "")
            bpm_s = _t('TBPM','bpm')
            try: result["bpm"] = int(float(bpm_s)) if bpm_s else 0
            except ValueError: pass
    except Exception:
        pass
    return result

# Erhoehen, wenn Bibliothekseintraege neue Tag-Felder bekommen: der Bestand wird
# dann beim naechsten Start einmal nachgelesen (siehe _refresh_tag_meta_task).
_TAG_META_REV = 2      # 2: Tonart dazu

def _read_tags_sync(path: str) -> dict | None:
    """Nur Kuenstler, Album und Genre aus den Tags lesen.

    Bewusst mutagen statt ffprobe: auf der Musik-Festplatte braucht ffprobe
    rund 450 ms je Datei, mutagen unter 10 ms. Fuer 3500 Titel sind das knapp
    eine halbe Stunde gegen eine halbe Minute (gemessen 09/2026).
    """
    try:
        import mutagen
        mf = mutagen.File(path)
    except Exception:
        return None
    if mf is None:
        return None
    tags = mf.tags or {}
    def _t(*keys):
        for k in keys:
            try:
                v = tags.get(k)
            except Exception:
                continue
            if v is None:
                continue
            if hasattr(v, 'text'):
                return str(v.text[0]).strip() if v.text else ''
            if isinstance(v, list):
                v = v[0] if v else ''
            # MP4-Freiform-Felder (iTunes initialkey) kommen als Bytes
            if isinstance(v, (bytes, bytearray)):
                return v.decode('utf-8', errors='replace').strip()
            return str(v).strip()
        return ''
    album_artist = _t('TPE2', 'albumartist', 'album_artist', 'aART')
    return {
        "artist":       _t('TPE1', 'artist', '\xa9ART') or album_artist,
        "album_artist": album_artist,
        "album":        _t('TALB', 'album', '\xa9alb'),
        "genre":        _t('TCON', 'genre', '\xa9gen'),
        "key":          _parse_key(_t('TKEY', 'initialkey', '----:com.apple.iTunes:initialkey')) or "",
    }

async def _refresh_tag_meta_task():
    """Tag-Felder der Bibliothek mit den Dateien abgleichen.

    Zwei Faelle:
    - Eintraege aus einem aelteren Stand (meta_rev zu alt): Bis 1.4.2 wurden
      Album und Genre nie gespeichert und der Kuenstler beim Start verworfen.
      Hier werden nur leere Felder gefuellt, von Hand Gesetztes bleibt.
    - Dateien, die sich seit dem Einlesen geaendert haben (mtime neuer): etwa
      nach einem Durchlauf durch Mixed In Key. Dann gelten die Tags. Leere
      Tags ueberschreiben nichts.
    Eine Tonart aus dem Tag schlaegt immer eine eigene Schaetzung.
    """
    FIELDS = ("artist", "album_artist", "album", "genre", "key")
    snapshot = [(lt["path"], int(lt.get("mtime", 0) or 0), lt.get("meta_rev", 0),
                 {k: lt.get(k, "") for k in FIELDS}, lt.get("key_src", ""))
                for lt in _state["library"] if lt.get("path")]

    def _work():
        # Nur lesen und Ergebnisse sammeln. Angewendet wird im Event-Loop,
        # damit niemand die Bibliothek speichert, waehrend hier geschrieben wird.
        updates: dict[str, dict] = {}
        for path, mtime, rev, current, key_src in snapshot:
            try:
                file_mtime = int(os.path.getmtime(path))
            except OSError:
                continue    # Laufwerk gerade nicht da: beim naechsten Start nochmal
            changed_on_disk = mtime > 0 and file_mtime > mtime
            if rev >= _TAG_META_REV and not changed_on_disk:
                continue
            tags = _read_tags_sync(path) or {}
            upd: dict = {"meta_rev": _TAG_META_REV}
            for k in ("artist", "album_artist", "album", "genre"):
                v = tags.get(k)
                if v and (changed_on_disk or not current.get(k)) and v != current.get(k):
                    upd[k] = v
            tag_key = tags.get("key")
            if tag_key and (tag_key != current.get("key") or key_src != "tag"):
                upd["key"] = tag_key
                upd["key_src"] = "tag"
            if changed_on_disk:
                upd["mtime"] = file_mtime
            updates[path] = upd
        return updates

    loop = asyncio.get_running_loop()
    updates = await loop.run_in_executor(None, _work)
    if not updates:
        return

    ergaenzt = 0
    for lt in _state["library"]:
        upd = updates.get(lt.get("path"))
        if upd is None:
            continue
        if any(k not in ("meta_rev", "mtime") for k in upd):
            ergaenzt += 1
        lt.update(upd)

    save_library()
    await push_library()
    print(f"[library] Tags abgeglichen: {len(updates)} Dateien, {ergaenzt} geaendert", flush=True)
    if ergaenzt:
        await broadcast({"type": "scan_status", "text": f"Tags ergänzt: {ergaenzt} Titel"})
        await asyncio.sleep(4)
        await broadcast({"type": "scan_status", "text": ""})

# ── Tonart-Erkennung ─────────────────────────────────────────────────────────
# Chromagramm per FFT, verglichen mit den Tonart-Profilen nach Temperley
# (Kostka-Payne). Gemessen im September 2026 an 120 Titeln der Bibliothek, die
# eine Tonart von Mixed In Key oder rekordbox trugen:
#   alle Schaetzungen:        45 % exakt, 64 % harmonisch passend
#   nur ausreichend sichere:  62 % exakt, 73 % passend (etwa die Haelfte)
# Getestete Verfeinerungen (Log-/Wurzelkompression, Spektralspitzen, Trennung
# von Schlagzeug und Toenen) brachten nichts bzw. schadeten. Deshalb werden nur
# sichere Ergebnisse uebernommen und als "analyse" markiert; Mixed In Key ist
# klar genauer, eine Tonart aus dem Tag hat immer Vorrang.
_KEY_PROFILE_MAJOR = (0.748, 0.060, 0.488, 0.082, 0.670, 0.460, 0.096, 0.715, 0.104, 0.366, 0.057, 0.400)
_KEY_PROFILE_MINOR = (0.712, 0.084, 0.474, 0.618, 0.049, 0.460, 0.105, 0.747, 0.404, 0.067, 0.133, 0.330)
_KEY_MIN_MARGIN = 0.09   # Abstand zur zweitbesten Tonart; darunter lieber keine Angabe

_numpy_state: bool | None = None
def _numpy_ok() -> bool:
    global _numpy_state
    if _numpy_state is None:
        try:
            import numpy  # noqa: F401
            _numpy_state = True
        except Exception:
            _numpy_state = False
            print("[key] numpy nicht verfuegbar — Tonart nur aus Tags", flush=True)
    return _numpy_state

def _key_from_chroma(chroma) -> tuple[str, float]:
    """Bestpassende Tonart und ihr Abstand zur zweitbesten."""
    import numpy as np
    maj, mino = np.array(_KEY_PROFILE_MAJOR), np.array(_KEY_PROFILE_MINOR)
    scores = []
    for tonic in range(12):
        scores.append((float(np.corrcoef(chroma, np.roll(maj, tonic))[0, 1]), tonic, False))
        scores.append((float(np.corrcoef(chroma, np.roll(mino, tonic))[0, 1]), tonic, True))
    scores.sort(reverse=True)
    best, second = scores[0], scores[1]
    return _KEY_NAMES[best[1]] + ('m' if best[2] else ''), best[0] - second[0]

def _detect_key_sync(path: str) -> str | None:
    """Tonart schaetzen. "" = nicht sicher genug oder nicht dekodierbar,
    None = Erkennung gar nicht moeglich (numpy fehlt)."""
    if not _numpy_ok():
        return None
    import numpy as np
    sr, n_fft, hop = 11025, 8192, 4096
    x = np.zeros(0, dtype=np.float32)
    # Ab Sekunde 30 und bis zu zwei Minuten: Intros sind oft nur Schlagzeug.
    # Kurze Titel beginnen dafuer von vorn.
    for start in ("30", None):
        cmd = [FFMPEG, "-nostdin", "-v", "error"] + (["-ss", start] if start else []) + \
              ["-i", path, "-t", "120", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=_NO_WINDOW)
        except Exception:
            return ""
        x = np.frombuffer(r.stdout, dtype=np.float32)
        if len(x) > sr * 10:
            break
    if len(x) < n_fft * 4:
        return ""
    n_frames = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    mag = np.abs(np.fft.rfft(x[idx] * np.hanning(n_fft)[None, :], axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    sel = (freqs >= 65) & (freqs <= 2000)
    midi = 69 + 12 * np.log2(freqs[sel] / 440.0)
    pitch_class = np.round(midi).astype(int) % 12
    # Frequenzen nahe der Halbton-Mitte zaehlen mehr, gegen Uebersprechen
    weight = np.exp(-((midi - np.round(midi)) ** 2) / (2 * 0.15 ** 2))
    m = mag[:, sel] * weight[None, :]
    chroma = np.stack([m[:, pitch_class == k].sum(axis=1) for k in range(12)], axis=1)
    energy = chroma.sum(axis=1)
    if not np.any(energy > 0):
        return ""
    keep = energy > np.percentile(energy, 20)    # leise Stellen nicht mitzaehlen
    chroma = (chroma[keep] / (energy[keep, None] + 1e-9)).mean(axis=0)
    key, margin = _key_from_chroma(chroma)
    return key if margin >= _KEY_MIN_MARGIN else ""

_analyze_running = False
_analyze_cancel  = False
_unanalyzable_paths: set[str] = set()   # Pfade die dauerhaft nicht analysierbar sind

async def _analyze_library_meta_task(only_paths: list[str] | None = None):
    """Background: fill in missing LUFS and BPM for every library track.

    only_paths schraenkt auf bestimmte Titel ein (Kontextmenue "Analysieren"
    eines Ordners) — nacheinander statt alle auf einmal.
    """
    global _analyze_running, _analyze_cancel
    if _analyze_running:
        return
    _analyze_running = True
    _analyze_cancel  = False
    loop    = asyncio.get_running_loop()
    try:
        tracks  = list(_state["library"])
        if only_paths is not None:
            wanted = set(only_paths)
            tracks = [lt for lt in tracks if lt.get("path") in wanted]
        pending = [lt for lt in tracks if lt.get("path") and os.path.exists(lt["path"])
                   and not lt.get("unanalyzable")
                   and (lt.get("lufs", -99) <= -90 or not lt.get("bpm")
                        or (not lt.get("key") and lt.get("key_src") != "none" and _numpy_ok()))]
        total   = len(pending)
        done    = 0
        changed = False
        if total > 0:
            await broadcast({"type": "analyze_progress", "done": 0, "total": total})
        for lt in pending:
            if _analyze_cancel:
                break
            path = lt.get("path", "")
            need_lufs = lt.get("lufs", -99) <= -90
            need_bpm  = not lt.get("bpm")
            need_key  = not lt.get("key") and lt.get("key_src") != "none"
            try:
                if need_lufs:
                    lufs = await loop.run_in_executor(None, _compute_lufs_sync, path)
                    if lufs > -90:
                        lt["lufs"] = lufs
                        changed = True
                    elif lufs == -97.0:
                        # Echter Dekodierfehler → dauerhaft überspringen
                        lt["unanalyzable"] = True
                        _unanalyzable_paths.add(path)
                        schedule_save()
                        changed = True
                    # lufs == -98.0 → Datei nicht gefunden (temporär) → nichts setzen
                if need_bpm:
                    bpm = await loop.run_in_executor(None, _estimate_bpm_sync, path)
                    if bpm:
                        lt["bpm"] = bpm
                        changed = True
                if need_key:
                    est = await loop.run_in_executor(None, _detect_key_sync, path)
                    if est is not None:
                        lt["key"] = est
                        lt["key_src"] = "analyse" if est else "none"
                        changed = True
            except Exception as e:
                print(f"[analyze_meta] {Path(path).name}: {e}")
            done += 1
            await broadcast({"type": "analyze_progress", "done": done, "total": total})
            if changed:
                await broadcast({"type": "track_meta_update", "track": lt})
                changed = False
        save_library()
        await push_library()
        await broadcast({"type": "analyze_progress", "done": done, "total": total, "finished": True})
    finally:
        _analyze_running = False
        _analyze_cancel  = False

async def _update_track_meta(path: str, title: str, artist: str):
    """Rewrite ID3/metadata tags in-place using ffmpeg, then update library cache."""
    suffix = Path(path).suffix
    tmp = path + '.__tmp' + suffix
    try:
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, '-y', '-i', path,
            '-c', 'copy',
            '-metadata', f'title={title}',
            '-metadata', f'artist={artist}',
            tmp,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=_NO_WINDOW)
        await proc.wait()
        if proc.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, path)
    except Exception as e:
        print(f"[update_meta] {e}")
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except: pass
    # Update in-memory library regardless of file write success
    for lt in _state["library"]:
        if lt["path"] == path:
            lt["title"]  = title
            lt["artist"] = artist
            break
    save_library()
    await broadcast({"type": "library", "tracks": _state["library"]})

async def _auto_add_to_library(path: str):
    if any(lt.get("path") == path for lt in _state["library"]):
        return
    loop  = asyncio.get_running_loop()
    probe = await loop.run_in_executor(None, _probe_sync, path)
    _state["library"].append(_make_library_entry(path, probe))
    save_library()
    await push_library()

async def _enrich_track(path: str, force: bool = False):
    loop = asyncio.get_running_loop()
    # Pull cached values from queue entry (if present)
    entry = next((t for t in _state["queue"] if t.get("path") == path), None)
    lib_entry = next((t for t in _state["library"] if t.get("path") == path), None)
    if not entry:
        entry = lib_entry
    # Korrupte/unlesbare Dateien nicht erneut analysieren (auch nicht mit force)
    if path in _unanalyzable_paths:
        return
    cached_art      = (entry or {}).get("art")
    cached_lufs     = (entry or {}).get("lufs", -99.0)
    cached_bpm      = (entry or {}).get("bpm",  0)
    cached_duration = (entry or {}).get("duration_sec", 0)

    art      = cached_art  if (cached_art  and not force) else await loop.run_in_executor(None, _extract_art_sync,    path)
    lufs     = cached_lufs if (cached_lufs > -90 and not force) else await loop.run_in_executor(None, _compute_lufs_sync, path)
    bpm      = cached_bpm  if ((cached_bpm and not force) or not _state.get("bpm_analysis", True)) else await loop.run_in_executor(None, _estimate_bpm_sync, path)
    duration = cached_duration if (cached_duration > 0 and not force) else (await loop.run_in_executor(None, _probe_sync, path)).get("duration_sec", 0)

    # Update queue entry
    for t in _state["queue"]:
        if t.get("path") == path:
            if art:          t["art"]          = art
            if lufs > -90:   t["lufs"]         = lufs
            if bpm:          t["bpm"]           = bpm
            if duration > 0: t["duration_sec"] = duration

    # Update library entry + persist (oder neu anlegen wenn noch nicht vorhanden)
    lib_changed = False
    lib_entry = next((lt for lt in _state["library"] if lt.get("path") == path), None)
    if lib_entry is None and os.path.exists(path):
        # Track noch nicht in der Bibliothek → mit vollständigen Metadaten anlegen
        probe = await loop.run_in_executor(None, _probe_sync, path)
        lib_entry = _make_library_entry(
            path, probe,
            duration_sec=duration or probe.get("duration_sec", 0),
            lufs=lufs if lufs > -90 else -99.0,
            bpm=bpm or probe.get("bpm", 0))
        _state["library"].append(lib_entry)
        lib_changed = True
    elif lib_entry is not None:
        if lufs > -90 and lib_entry.get("lufs", -99) <= -90:
            lib_entry["lufs"] = lufs;     lib_changed = True
        elif lufs == -97.0:
            # Echter Dekodierfehler → dauerhaft markieren
            lib_entry["unanalyzable"] = True
            _unanalyzable_paths.add(path)
            lib_changed = True
        # lufs == -98.0 → Datei nicht gefunden (temporär) → nichts markieren
        if bpm  and not lib_entry.get("bpm"):                       lib_entry["bpm"]          = bpm;      lib_changed = True
        if duration > 0 and not lib_entry.get("duration_sec", 0):  lib_entry["duration_sec"] = duration; lib_changed = True

    # Tonart nur schaetzen, wenn weder ein Tag eine liefert noch eine
    # fruehere Erkennung schon ergebnislos war ("none").
    if lib_entry is not None and not lib_entry.get("key") and lib_entry.get("key_src") != "none":
        est = await loop.run_in_executor(None, _detect_key_sync, path)
        if est is not None:
            lib_entry["key"] = est
            lib_entry["key_src"] = "analyse" if est else "none"
            lib_changed = True
    if lib_changed:
        save_library()
        if lib_entry is not None:
            await broadcast({"type": "track_meta_update", "track": lib_entry})

    save_queue()
    await broadcast({"type": "track_enriched", "path": path, "art": art, "lufs": lufs, "bpm": bpm, "duration_sec": duration})

# ── Qualitaet: Bandbreite messen, bessere Version einsetzen ─────────────────
# Die Bitrate sagt wenig: YouTube-Konverter schreiben "320 kbps" auch aus einer
# 128er-Quelle. Verraten wird das von der oberen Grenzfrequenz — verlustbehaftete
# Encoder schneiden die Hoehen ab. Gemessen 09/2026 an der Bibliothek:
#   dvdvideosoft-Dateien ("320 kbps")   ~16 kHz
#   eigene Downloads (YouTube-Opus, V0) ~20 kHz
#   FLAC / echte WAV                    ~22 kHz
_CUTOFF_UPSCALED_KHZ = 17.0    # darunter klingt eine Datei wie <= 128 kbps
_QUALITY_MIN_SEC     = 60      # Samples, FX, Chops nicht pruefen
_QUALITY_PARALLEL    = 2
_QUALITY_RESCAN_SEC  = 600     # neue Titel werden spaeter nachgemessen


def _cutoff_khz_sync(path: str, duration: float = 0) -> float:
    """Obere Grenzfrequenz in kHz aus 10 s ab 40 % der Laenge. 0 = nicht messbar."""
    if not _numpy_ok():
        return 0.0
    import numpy as np
    sr, n = 44100, 8192
    start = max(0.0, (duration or 0) * 0.4)
    cmd = [FFMPEG, "-nostdin", "-v", "error", "-ss", f"{start:.1f}", "-t", "10", "-i", path,
           "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    except Exception:
        return 0.0
    x = np.frombuffer(r.stdout, dtype=np.float32)
    if x.size < n * 2:
        return 0.0
    win = np.hanning(n)
    acc = np.zeros(n // 2 + 1)
    cnt = 0
    for i in range(0, x.size - n, n // 2):
        seg = x[i:i + n]
        if np.max(np.abs(seg)) < 1e-3:       # Stille zaehlt nicht
            continue
        acc += np.abs(np.fft.rfft(seg * win)) ** 2
        cnt += 1
    if not cnt:
        return 0.0
    db = 10 * np.log10(acc / cnt + 1e-20)
    db = np.convolve(db, np.ones(19) / 19, mode="same")      # ~100 Hz glaetten
    freqs = np.fft.rfftfreq(n, 1 / sr)
    ref = float(np.median(db[(freqs > 2000) & (freqs < 8000)]))
    above = np.where((db > ref - 45) & (freqs > 5000) & (freqs < 21900))[0]
    return round(float(freqs[above[-1]]) / 1000, 1) if above.size else 5.0


def _quality_pending() -> list[dict]:
    return [lt for lt in _state["library"]
            if lt.get("path") and lt.get("cutoff_khz") is None and not lt.get("missing")
            and (lt.get("duration_sec") or 0) >= _QUALITY_MIN_SEC]


async def _quality_scan_once():
    pending = _quality_pending()
    if not pending:
        return
    total, done = len(pending), 0
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(_QUALITY_PARALLEL)
    await broadcast({"type": "quality_scan", "done": 0, "total": total})

    async def one(lt):
        nonlocal done
        async with sem:
            path = lt["path"]
            if not os.path.exists(path):
                return                        # Laufwerk fehlt: spaeter nochmal
            lt["cutoff_khz"] = await loop.run_in_executor(
                None, _cutoff_khz_sync, path, lt.get("duration_sec") or 0)
            done += 1
            if done % 25 == 0:
                schedule_save()
                await broadcast({"type": "quality_scan", "done": done, "total": total})
            if done % 500 == 0:
                await push_library()

    await asyncio.gather(*(one(lt) for lt in pending))
    save_library()
    await push_library()
    await broadcast({"type": "quality_scan", "done": done, "total": total, "finished": True})
    print(f"[quality] Bandbreite gemessen: {done} Titel", flush=True)


async def _quality_scan_loop():
    """Misst im Hintergrund alle Titel ohne Wert, danach alle 10 Minuten neue."""
    await asyncio.sleep(60)     # Start, Tag-Abgleich und Wiedergabe gehen vor
    while True:
        try:
            await _quality_scan_once()
        except Exception as e:
            print(f"[quality] {e}", flush=True)
        await asyncio.sleep(_QUALITY_RESCAN_SEC)


async def _quality_candidates(path: str, query: str, ws: WebSocket):
    """Kandidaten fuer "Bessere Version": Studio-Versionen zuerst, dann
    YouTube-Uploads ohne Musikvideos. Zweistufig wie die Suche."""
    lt = next((x for x in _state["library"] if x.get("path") == path), None)
    q = (query or "").strip() or _song_query((lt or {}).get("title") or Path(path).stem)

    async def send(results, final):
        try:
            await ws.send_text(json.dumps({"type": "quality_candidates", "path": path, "query": q,
                                           "results": _public(results), "final": final}))
        except Exception:
            pass

    songs, videos = await _songs_and_videos(q, 5, 8)
    await send(_merge_songs_first(songs, videos), not songs)
    if songs:
        await _ytm_fill_details(songs)
        songs = [r for r in songs if not _is_unwanted_result(r) and not LIVE_RE.search(r.get("title", ""))]
        await send(_merge_songs_first(songs, videos), True)


# Format der alten Datei → yt-dlp --audio-format (gleiche Endung, gleicher Name)
_REPLACE_FORMATS = {"mp3": "mp3", "m4a": "m4a", "flac": "flac", "wav": "wav",
                    "opus": "opus", "ogg": "vorbis", "aac": "aac"}
_replace_running: set[str] = set()


async def _download_replacement(url: str, ext: str, tmpdir: str) -> tuple[str | None, str]:
    """Neue Version ohne eigene Tags und Cover in tmpdir laden (die kommen von
    der alten Datei). Liefert (Pfad oder None, Fehlermeldung von yt-dlp)."""
    cmd = _yt("-x", "--audio-format", _REPLACE_FORMATS[ext], "--audio-quality", "0",
              "--no-playlist", "--newline", "--encoding", "utf-8",
              "-o", os.path.join(tmpdir, "neu.%(ext)s"))
    if FFMPEG_DIR:
        cmd += ["--ffmpeg-location", FFMPEG_DIR]
    cmd.append(url)
    err = ""
    try:
        pr = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            creationflags=_NO_WINDOW)
        _, raw = await asyncio.wait_for(pr.communicate(), timeout=900)
        lines = [l.strip() for l in raw.decode("utf-8", errors="replace").splitlines() if "ERROR" in l]
        err = lines[-1].replace("ERROR:", "").strip()[:160] if lines else ""
    except Exception as e:
        return None, str(e)
    want = ".ogg" if ext == "ogg" else "." + ext
    found = [f for f in os.listdir(tmpdir) if f.lower().endswith(want)]
    return (os.path.join(tmpdir, found[0]) if found else None), err


def _copy_tags_sync(src: str, dst: str) -> bool:
    """Alle Tags der alten Datei auf die neue: Titel, Kuenstler, Tonart und
    Kommentar (Mixed In Key), Cover, rekordbox-/Serato-Felder. Was yt-dlp oder
    ffmpeg selbst geschrieben haben, faellt weg."""
    import mutagen
    ext = Path(dst).suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError
            try:
                tags = ID3(src)
            except ID3NoHeaderError:
                tags = None
            try:
                ID3(dst).delete()
            except ID3NoHeaderError:
                pass
            if tags is not None:
                v = tags.version[1] if tags.version[1] in (3, 4) else 4
                tags.save(dst, v2_version=v)
            return True
        s_file, d_file = mutagen.File(src), mutagen.File(dst)
        if d_file is None:
            return False
        if d_file.tags is not None:
            d_file.delete()
            d_file = mutagen.File(dst)
        if s_file is not None and s_file.tags is not None:
            if d_file.tags is None:
                d_file.add_tags()
            if ext in (".wav", ".aif", ".aiff"):
                for frame in s_file.tags.values():
                    d_file.tags.add(frame)
            else:
                for k, val in s_file.tags.items():
                    d_file.tags[k] = val
            if hasattr(s_file, "pictures") and hasattr(d_file, "add_picture"):
                d_file.clear_pictures()
                for pic in s_file.pictures:
                    d_file.add_picture(pic)
            d_file.save()
        return True
    except Exception as e:
        print(f"[quality] Tags {Path(src).name}: {e}", flush=True)
        return False


async def _quality_replace(path: str, url: str, ws: WebSocket):
    """Neue Version unter exakt demselben Namen und Pfad einsetzen, damit
    rekordbox, Playlisten und Warteschlange den Titel weiter finden. Die alte
    Datei geht in den Papierkorb. Schlaegt ein Schritt fehl, bleibt alles wie es war."""
    async def status(state, text=""):
        try:
            await ws.send_text(json.dumps({"type": "quality_replace_status", "path": path,
                                           "state": state, "text": text}))
        except Exception:
            pass

    lt = next((x for x in _state["library"] if x.get("path") == path), None)
    if lt is None or not os.path.exists(path):
        await status("error", "Datei nicht gefunden.")
        return
    ci = _state.get("current_idx", -1)
    q = _state.get("queue", [])
    if 0 <= ci < len(q) and q[ci].get("path") == path:
        await status("error", "Der Titel ist gerade im Player geladen. Erst einen anderen Titel spielen.")
        return
    ext = Path(path).suffix.lower().lstrip(".")
    if ext not in _REPLACE_FORMATS:
        await status("error", f"Dateien vom Typ .{ext} kann SynthiMIX nicht ersetzen.")
        return
    if path in _replace_running:
        return
    _replace_running.add(path)
    tmpdir = tempfile.mkdtemp(prefix="synthimix-ersatz-")
    loop = asyncio.get_running_loop()
    try:
        await status("download", "Lade die neue Version…")
        new, err = await _download_replacement(url, ext, tmpdir)
        if not new and "403" in err:
            # YouTube weist nach vielen schnellen Abfragen gelegentlich ab — einmal nachfassen
            await asyncio.sleep(3)
            new, err = await _download_replacement(url, ext, tmpdir)
        if not new:
            await status("error", "Download fehlgeschlagen" + (f" ({err})" if err else "") + ". Nichts ersetzt.")
            return
        await status("tags", "Übernehme Tags und Cover…")
        if not await loop.run_in_executor(None, _copy_tags_sync, path, new):
            await status("error", "Tags ließen sich nicht übernehmen. Nichts ersetzt.")
            return
        # Ohne Audio-Endung: der Ordner-Waechter nimmt die Zwischendatei nicht auf
        staged = path + ".synthimix-neu"
        shutil.move(new, staged)
        if not await loop.run_in_executor(None, _move_to_trash, path):
            try: os.remove(staged)
            except OSError: pass
            await status("error", "Die alte Datei ließ sich nicht in den Papierkorb legen. Nichts ersetzt.")
            return
        try:
            os.replace(staged, path)
        except OSError as e:
            await status("error", f"Neue Datei liegt unter {staged}, die alte im Papierkorb ({e}).")
            return

        probe = await loop.run_in_executor(None, _probe_sync, path)
        dur = probe.get("duration_sec") or lt.get("duration_sec") or 0
        lt["duration_sec"] = dur
        lt["bitrate_kbps"] = probe.get("bitrate_kbps") or 0
        lt["lufs"]         = -99.0
        lt["mtime"]        = int(os.path.getmtime(path))
        lt["meta_rev"]     = _TAG_META_REV
        lt.pop("unanalyzable", None)
        _unanalyzable_paths.discard(path)
        lt["cutoff_khz"]   = await loop.run_in_executor(None, _cutoff_khz_sync, path, dur)
        for item in _state["queue"]:
            if item.get("path") == path:
                item["lufs"] = -99.0
                item["duration_sec"] = dur
                item["bitrate_kbps"] = lt["bitrate_kbps"]
        _wf_cache.pop(path, None)
        save_library()
        save_queue()
        await broadcast({"type": "track_meta_update", "track": lt})
        asyncio.create_task(_enrich_track(path))       # Lautheit neu messen
        await status("done", f"Ersetzt: {lt['bitrate_kbps']} kbps, Höhen bis {lt['cutoff_khz']} kHz.")
    finally:
        _replace_running.discard(path)
        shutil.rmtree(tmpdir, ignore_errors=True)


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
    return BASE_DIR / "genre_rules.json"


def _genre_rules_load() -> dict:
    return _load_json(_genre_rules_file(), {})


def _genre_rules_save(rules: dict):
    # "" = bewusst keine Regel (sonst gilt der Vorschlag aus dem Ordnernamen)
    clean = {str(k): v for k, v in rules.items() if v in GENRES or v == ""}
    _save_json(_genre_rules_file(), clean)


def _genre_cache_file() -> Path:
    return BASE_DIR / "genre_cache.json"


def _artist_title(lt: dict) -> tuple[str, str]:
    """Kuenstler und Titel fuer die Online-Abfrage. "Kuenstler - Titel" im
    Dateititel geht vor dem Kuenstler-Tag (bei YouTube-Downloads der Kanal)."""
    t = _DL_DUPE_NOISE_RE.sub(" ", lt.get("title") or Path(lt.get("path", "")).stem)
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

        cache = _load_json(_genre_cache_file(), {})
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
                    _save_json(_genre_cache_file(), cache)
                    await send("genre_progress", done=done, total=total)
        _save_json(_genre_cache_file(), cache)
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
    wird uebersprungen (die Datei ist im Player offen)."""
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
    for n, it in enumerate(items, 1):
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
        if n % 25 == 0:
            await send("genre_apply_progress", done=n, total=total)
    save_library()
    await push_library()
    await send("genre_applied", ok=ok, failed=failed[:20], failed_count=len(failed), skipped=skipped)


# ── waveform ─────────────────────────────────────────────────────────────────
_wf_cache: dict[str, list] = {}

async def compute_waveform(path: str, bars: int = 1000) -> list[float]:
    if path in _wf_cache:
        return _wf_cache[path]
    if not os.path.exists(path):
        return []
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _waveform_sync, path, bars)
    _wf_cache[path] = data
    return data

def _waveform_sync(path: str, bars: int) -> list[float]:
    try:
        proc = subprocess.Popen(
            [FFMPEG, "-nostdin", "-i", path,
             "-filter:a", "aformat=channel_layouts=mono",
             "-acodec", "pcm_s16le", "-f", "s16le", "-ar", "8000", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        raw = proc.stdout.read()
        proc.wait()
        if not raw or len(raw) < 4:
            return []
        import array as _arr
        samples = _arr.array("h", raw[:len(raw) & ~1])
        n = len(samples)
        if n < bars:
            return []
        chunk = n // bars
        result = []
        for i in range(bars):
            seg = samples[i * chunk:(i + 1) * chunk]
            rms = math.sqrt(sum(v * v for v in seg) / len(seg)) if seg else 0
            result.append(rms)
        mx = max(result) or 1
        return [v / mx for v in result]
    except Exception:
        return []

def _find_spotdl_cmd() -> list[str] | None:
    """Return spotdl command as a list, or None if not found."""
    if SPOTDL_LOCAL.exists():
        return [str(SPOTDL_LOCAL)]
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

def _is_spotify(url: str) -> bool:
    return "open.spotify.com" in url or "spotify.link" in url

async def _check_tools(ws: WebSocket):
    loop = asyncio.get_running_loop()
    info: dict = {}
    def _sync():
        try:
            r = subprocess.run([YTDLP, "--version"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=8, creationflags=_NO_WINDOW)
            info["ytdlp_version"] = r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            info["ytdlp_version"] = None
        try:
            r = subprocess.run([FFMPEG, "-version"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=8, creationflags=_NO_WINDOW)
            first = (r.stdout or "").splitlines()[0]
            m = re.search(r'version\s+(\S+)', first)
            info["ffmpeg_version"] = m.group(1) if m else first[:40]
        except Exception:
            info["ffmpeg_version"] = None
        try:
            spotdl = _find_spotdl_cmd()
            if spotdl:
                r = subprocess.run(spotdl + ["--version"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=8, creationflags=_NO_WINDOW)
                m = re.search(r'(\d+\.\d+[\.\d]*)', r.stdout + r.stderr)
                info["spotdl_version"] = m.group(1) if m else "installiert"
            else:
                info["spotdl_version"] = None
        except Exception:
            info["spotdl_version"] = None
        info["fpcalc_found"] = bool(_find_fpcalc())
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
        dest = Path(YTDLP).resolve()
        tmp  = str(dest) + ".tmp"
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
        await _send(f"✓ {tag} installiert", 100)
        tu = _state.setdefault("tool_updates", {})
        tu["ytdlp"] = {"latest": tag, "available": False}
        save_settings()
        await broadcast({"type": "tool_updates", "items": tu})
        if ws is not None:
            await _check_tools(ws)
        return True
    except Exception as e:
        await _send(f"❌ {e}", -1)
        return False

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
    await broadcast({"type": "automix_status", "text": "⟳ Auto-Mix sucht…"})

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
            save_queue()
            await push_queue()
            await broadcast({"type": "automix_status",
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
        if FFMPEG_DIR:
            base_args += ["--ffmpeg-location", FFMPEG_DIR]

        # Search across all queries, collect unique results
        all_results: list[dict] = []
        seen_result_urls: set[str] = set()
        for sq in search_queries:
            batch = await _ytm_songs(sq, 8, details=True)
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
                batch = await _run_search_cmd(_yt(f"ytsearch15:{sq} song", *base_args))
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
            if MIX_RE.search(r_title):
                continue
            # Skip interviews, reactions, talk content etc. — common in the unscoped
            # plain-YouTube fallback, since the YTM song search never surfaces these
            if NON_MUSIC_RE.search(r_title):
                continue
            # Skip live recordings / concert performances
            if LIVE_RE.search(r_title):
                continue
            # Keine Musikvideos (Intro, Pausen) — auch die Song-Suche laesst vereinzelt eins durch
            if _MV_TITLE_RE.search(r_title):
                continue
            # Skip playlists / full albums
            if _is_playlist(u):
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
            await broadcast({"type": "automix_status", "text": "⚠ Kein Song gefunden"})
            await asyncio.sleep(4)
            await broadcast({"type": "automix_status", "text": ""})
            return

        await broadcast({"type": "automix_status", "text": f"⬇ {found_title[:48]}…"})
        path = await run_download(found_url, "mp3-best")

        if path and os.path.exists(path):
            loop = asyncio.get_running_loop()
            probe = await loop.run_in_executor(None, _probe_sync, path)
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
            save_queue()
            await push_queue()
            # Auto-play if player was idle
            if not _state["playing"] or _state["current_idx"] < 0:
                idx = len(_state["queue"]) - 1
                _state["current_idx"] = idx
                _state["playing"]     = True
                _state["position_ms"] = 0
                track["played"]       = True
                track["play_count"]   = 1
                await push_player()
                await broadcast({"type": "now_playing", "track": dict(track)})
                asyncio.create_task(_enrich_track(path))
            await broadcast({"type": "automix_status", "text": f"✓ {track['title'][:48]}"})
        else:
            await broadcast({"type": "automix_status", "text": "⚠ Download fehlgeschlagen"})

    except Exception as e:
        await broadcast({"type": "automix_status", "text": f"⚠ Fehler: {str(e)[:40]}"})

    await asyncio.sleep(5)
    await broadcast({"type": "automix_status", "text": ""})


async def _normalize_files(paths: list, target_lufs: float, target_tp: float, ws: WebSocket):
    loop   = asyncio.get_running_loop()
    total  = len(paths)
    done   = 0
    errors = 0
    for path in paths:
        if not os.path.exists(path):
            errors += 1; done += 1; continue
        try:
            await ws.send_text(json.dumps({
                "type": "normalize_progress",
                "done": done, "total": total,
                "current": Path(path).name[:60]
            }))
            ok = await loop.run_in_executor(None, _normalize_one_sync,
                                            path, target_lufs, target_tp)
            if ok:
                # Update LUFS in library
                for lt in _state["library"]:
                    if lt.get("path") == path:
                        lt["lufs"] = target_lufs
                        break
            else:
                errors += 1
        except Exception:
            errors += 1
        done += 1

    save_library()
    await push_library()
    await ws.send_text(json.dumps({
        "type": "normalize_done",
        "normalized": done - errors,
        "errors": errors
    }))


def _audio_stream_info(path: str) -> tuple[int, int]:
    """Bitrate (kbps) und Abtastrate der ersten Audiospur, 0 wenn unbekannt."""
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_streams",
             "-show_format", "-select_streams", "a:0", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=_NO_WINDOW)
        d = json.loads(r.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        br = int(float(st.get("bit_rate") or d.get("format", {}).get("bit_rate") or 0)) // 1000
        return br, int(st.get("sample_rate") or 0)
    except Exception:
        return 0, 0

# Verlustbehaftete Formate muessen mit Encoder und Bitrate neu kodiert werden.
# Ohne Angabe nimmt ffmpeg seine Vorgabe (MP3: 128 kbps) — "Normalisieren"
# hat so aus 320-kbps-Dateien dauerhaft 128er gemacht.
_REENCODE = {".mp3": "libmp3lame", ".m4a": "aac", ".aac": "aac",
             ".ogg": "libvorbis", ".opus": "libopus", ".wma": "wmav2"}

def _normalize_one_sync(path: str, target_lufs: float, target_tp: float) -> bool:
    ext = Path(path).suffix.lower()
    tmp = path + ".norm_tmp" + ext
    try:
        # Pass 1 – measure
        p1 = subprocess.run(
            [FFMPEG, "-nostdin", "-i", path,
             "-af", f"loudnorm=I={target_lufs}:TP={target_tp}:LRA=11:print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, creationflags=_NO_WINDOW)
        meas = {}
        in_json = False; buf = ""
        for line in (p1.stderr or "").splitlines():
            if line.strip() == "{": in_json = True
            if in_json: buf += line + "\n"
            if in_json and line.strip() == "}": break
        if buf.strip():
            try: meas = json.loads(buf)
            except Exception: pass
        # Pass 2 – apply
        if meas:
            af = (f"loudnorm=I={target_lufs}:TP={target_tp}:LRA=11"
                  f":measured_I={meas.get('input_i', target_lufs)}"
                  f":measured_TP={meas.get('input_tp', target_tp)}"
                  f":measured_LRA={meas.get('input_lra', 11)}"
                  f":measured_thresh={meas.get('input_thresh', -30)}"
                  f":linear=true:print_format=none")
        else:
            af = f"loudnorm=I={target_lufs}:TP={target_tp}:LRA=11"
        br, sr = _audio_stream_info(path)
        codec = []
        if ext in _REENCODE:
            codec = ["-c:a", _REENCODE[ext], "-b:a", f"{max(br, 128) if br else 320}k"]
        # Cover und Tags unveraendert mitnehmen; loudnorm rechnet intern mit
        # 192 kHz, deshalb die urspruengliche Abtastrate wieder setzen.
        p2 = subprocess.run(
            [FFMPEG, "-nostdin", "-i", path, "-map", "0:a:0", "-map", "0:v?",
             "-af", af, *codec, "-c:v", "copy", "-ar", str(sr or 48000),
             "-map_metadata", "0", "-y", tmp],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, creationflags=_NO_WINDOW)
        if p2.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
            return True
        return False
    except Exception:
        return False
    finally:
        try:
            if os.path.exists(tmp): os.remove(tmp)
        except Exception: pass


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
        passend = [c for c in candidates if _key_compat(ref, c.get("key")) >= 2]
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
                passend = [t for t in treffer if ref and _key_compat(ref, t.get('key')) >= 2]
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
        save_queue()
        await push_queue()
        await broadcast({'type': 'radio_added',
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
                with zf.open(exe_name) as src, open(FPCALC_LOCAL, 'wb') as dst:
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

def _spotdl_version_sync() -> str:
    cmd = _find_spotdl_cmd()
    if not cmd:
        return ""
    try:
        r = subprocess.run(cmd + ["--version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20,
                           creationflags=_NO_WINDOW)
        m = re.search(r'(\d+\.\d+[\.\d]*)', r.stdout + r.stderr)
        return m.group(1) if m else ""
    except Exception:
        return ""

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
            tmp = SPOTDL_LOCAL.with_suffix(".part")
            _urllib_req.urlretrieve(url, tmp, _on_block)
            # Erst nach vollständigem Download an den finalen Platz — ein
            # abgebrochener Download soll nicht als "installiert" gelten.
            tmp.replace(SPOTDL_LOCAL)
        await loop.run_in_executor(None, _do_install)
    except Exception as e:
        SPOTDL_LOCAL.with_suffix(".part").unlink(missing_ok=True)
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
    save_settings()
    await broadcast({"type": "tool_updates", "items": tu})
    await _send("spotdl_install_done", version=version)

# ── AcoustID fingerprinting ───────────────────────────────────────────────────
async def _acoustid_identify(path: str) -> dict:
    import urllib.request as _req, urllib.parse as _parse
    api_key = _state.get('acoustid_api_key', '').strip()
    if not api_key:
        return {'error': 'Kein AcoustID API-Key konfiguriert', 'fix_tab': 'services'}

    fpcalc = _find_fpcalc()
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

    if not rec or not rec.get('title'):
        # Last-resort: MusicBrainz text search via existing library tags
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

    return {'title': title, 'artist': artist, 'album': album, 'score': round(score, 3), 'path': path}


# ── message handler ───────────────────────────────────────────────────────────
async def handle_message(ws: WebSocket, msg: dict):
    t = msg.get("type")

    if t == "get_state":
        await ws.send_text(json.dumps({"type": "queue", "items": _state["queue"],
                                       "current_idx": _state["current_idx"]}))
        await push_player()
        await ws.send_text(json.dumps({"type": "library", "tracks": _state["library"]}))
        await ws.send_text(json.dumps({"type": "downloads", "items": _state["downloads"]}))
        await ws.send_text(json.dumps({"type": "playlists", "items": _get_playlists()}))
        # Ohne das hier haette ein frisch gestarteter Client die Wuensche erst
        # gesehen, wenn sich der naechste geaendert hat.
        await ws.send_text(json.dumps({"type": "wishes", "items": _state.get("wishes", [])}))
        await ws.send_text(json.dumps({"type": "watched_folders", "items": _watched_folders_info()}))
        await ws.send_text(json.dumps({"type": "excluded_folders",
                                       "items": _state.get("excluded_folders", [])}))
        await ws.send_text(json.dumps({"type": "tool_updates",
                                       "items": _state.get("tool_updates", {})}))
        await ws.send_text(json.dumps({
            "type":             "settings",
            "volume":           _state["volume"],
            "crossfade_s":      _state["crossfade_s"],
            "scan_recursive":   _state.get("scan_recursive", True),
            "bpm_analysis":     _state.get("bpm_analysis", True),
            "auto_mix":                _state.get("auto_mix", True),
            "auto_remove_played":      _state.get("auto_remove_played", False),
            "loudnorm_on_dl":          _state.get("loudnorm_on_dl", False),
            "loudnorm_target":         _state.get("loudnorm_target", -14.0),
            "loudnorm_tp":             _state.get("loudnorm_tp", -1.5),
            "playlist_folder_enabled": _state.get("playlist_folder_enabled", True),
            "dl_filename_format":      _state.get("dl_filename_format", "title"),
            "download_dir":            _state.get("download_dir", str(BASE_DIR / "Downloads")),
            "auto_scan_interval_min":  _state.get("auto_scan_interval_min", 0),
            "favorites":               _state.get("favorites", []),
            "remote_autostart":        _state.get("remote_autostart", False),
            "ytdlp_autoupdate":        _state.get("ytdlp_autoupdate", True),
            "normalize_volume":        _state.get("normalize_volume", True),
            "target_lufs":             _state.get("target_lufs", -10.0),
            "spotify_client_id":       _state.get("spotify_client_id", ""),
            "spotify_client_secret":   _state.get("spotify_client_secret", ""),
            "lastfm_api_key":          _state.get("lastfm_api_key", ""),
            "acoustid_api_key":        _state.get("acoustid_api_key", ""),
            "radio_enabled":           _state.get("radio_enabled", False),
        }))
        if _state.get("remote_autostart") and _remote_server is None:
            asyncio.create_task(_start_remote_server(ws))

    elif t == "queue_add":
        track = {
            "path":         msg["path"],
            "title":        msg.get("title") or Path(msg["path"]).stem,
            "duration_sec": msg.get("duration_sec", 0),
            "lufs":         msg.get("lufs", -99.0),
            "bpm":          msg.get("bpm", 0),
            "bitrate_kbps": msg.get("bitrate_kbps", 0),
            "played":       False,
        }
        if not _queue_is_duplicate(track["path"], track["title"]):
            was_idle = _state["current_idx"] == -1
            _state["queue"].append(track)
            save_queue()
            if was_idle:
                _state["current_idx"] = len(_state["queue"]) - 1
                _state["playing"] = True
                await push_queue()
                await push_player()
                await broadcast({"type": "now_playing", "track": track})
                asyncio.create_task(_enrich_track(track["path"]))
            else:
                await push_queue()

    elif t == "queue_remove":
        idx = msg.get("index", -1)
        if 0 <= idx < len(_state["queue"]):
            _state["queue"].pop(idx)
            if _state["current_idx"] >= idx:
                _state["current_idx"] = max(-1, _state["current_idx"] - 1)
            save_queue()
            await push_queue()  # carries current_idx — no push_player needed

    elif t == "play_at":
        idx = msg.get("index", 0)
        if 0 <= idx < len(_state["queue"]):
            # Auto-remove: gespielte Tracks VOR dem neuen Index löschen
            if _state.get("auto_remove_played"):
                before = [i for i in range(idx) if _state["queue"][i].get("played")]
                for i in reversed(before):
                    _state["queue"].pop(i)
                idx -= len(before)  # Index anpassen

            _state["current_idx"] = idx
            _state["playing"]     = True
            _state["position_ms"] = 0
            track = _state["queue"][idx]
            track["played"]    = True
            track["played_at"] = int(time.time())
            track["play_count"] = track.get("play_count", 0) + 1
            # Also update library play_count for "Zuletzt gespielt"
            track_artist = ""
            for lt in _state["library"]:
                if lt.get("path") == track["path"]:
                    lt["play_count"] = lt.get("play_count", 0) + 1
                    track_artist = lt.get("artist", "")
                    break
            _record_play(track["path"], track.get("title", ""), track_artist)
            save_queue()
            save_library()
            await push_player()
            await push_queue()
            await push_library()
            now = {**track}
            await broadcast({"type": "now_playing", "track": now})
            asyncio.create_task(_enrich_track(track["path"]))
            # Pre-enrich next track so Deck 2 has real LUFS before crossfade
            nxt_idx = idx + 1
            if 0 <= nxt_idx < len(_state["queue"]):
                nxt_path = _state["queue"][nxt_idx].get("path", "")
                if nxt_path and nxt_path not in _unanalyzable_paths and _state["queue"][nxt_idx].get("lufs", -99) <= -90:
                    asyncio.create_task(_enrich_track(nxt_path))
            # Auto-Mix: start download early when this is the last track
            elif nxt_idx >= len(_state["queue"]) and _state.get("auto_mix", True):
                asyncio.create_task(_do_automix(track.get("title", "")))

    elif t == "play_now":
        track = {
            "path":         msg["path"],
            "title":        msg.get("title") or Path(msg["path"]).stem,
            "duration_sec": msg.get("duration_sec", 0),
            "lufs":         msg.get("lufs", -99.0),
            "bpm":          msg.get("bpm", 0),
            "bitrate_kbps": msg.get("bitrate_kbps", 0),
            "played": True, "play_count": 1,
        }
        _state["queue"].insert(_state["current_idx"] + 1, track)
        _state["current_idx"] += 1
        _state["playing"]     = True
        _state["position_ms"] = 0
        track_artist = ""
        for lt in _state["library"]:
            if lt.get("path") == track["path"]:
                lt["play_count"] = lt.get("play_count", 0) + 1
                track_artist = lt.get("artist", "")
                break
        _record_play(track["path"], track.get("title", ""), track_artist)
        save_queue()
        save_library()
        await push_queue()
        await push_player()
        await broadcast({"type": "now_playing", "track": track})
        asyncio.create_task(_enrich_track(track["path"]))
        # Pre-enrich next track so Deck 2 has real LUFS before crossfade
        nxt_idx = _state["current_idx"] + 1
        if 0 <= nxt_idx < len(_state["queue"]):
            nxt_path = _state["queue"][nxt_idx].get("path", "")
            if nxt_path and _state["queue"][nxt_idx].get("lufs", -99) <= -90:
                asyncio.create_task(_enrich_track(nxt_path))

    elif t == "get_wishes":
        await ws.send_text(json.dumps({"type": "wishes", "items": _state.get("wishes", [])}))

    elif t == "wish_accept":
        wid = msg.get("id")
        w = next((x for x in _state.get("wishes", []) if x.get("id") == wid), None)
        if w and w.get("path") and os.path.exists(w["path"]):
            td = next((lt for lt in _state["library"] if lt.get("path") == w["path"]), None)
            entry = {
                "path":         w["path"],
                "title":        (td or {}).get("title") or w.get("title", ""),
                "artist":       (td or {}).get("artist", ""),
                "duration_sec": (td or {}).get("duration_sec", 0),
                "lufs":         (td or {}).get("lufs", -99.0),
                "bpm":          (td or {}).get("bpm", 0),
                "bitrate_kbps": (td or {}).get("bitrate_kbps", 0),
                "played":       False,
            }
            if msg.get("as_next") and _state.get("current_idx", -1) >= 0:
                _state["queue"].insert(_state["current_idx"] + 1, entry)
            else:
                _state["queue"].append(entry)
            save_queue()
            await push_queue()
            _state["wishes"] = [x for x in _state["wishes"] if x.get("id") != wid]
            # Merken, damit der Gast sieht, wann sein Titel kommt
            _wish_outcomes[wid] = {"state": "angenommen", "title": w.get("title", ""),
                                   "path": w["path"]}
            save_wishes()
            await push_wishes()

    elif t == "wish_reject":
        wid = msg.get("id")
        w = next((x for x in _state.get("wishes", []) if x.get("id") == wid), None)
        if w:
            # Heruntergeladene Datei mitnehmen — abgelehnte Wuensche sollen
            # den Download-Ordner nicht vollmuellen.
            p = w.get("path", "")
            # Nur loeschen, was der Wunsch selbst heruntergeladen hat — nie einen
            # Titel aus der eigenen Sammlung oder einen, der schon vorher da war.
            eigen = not w.get("from_library") and not w.get("keep_file")
            if p and os.path.exists(p) and eigen and msg.get("delete_file", True):
                if await asyncio.get_running_loop().run_in_executor(None, _move_to_trash, p):
                    _state["library"] = [lt for lt in _state["library"] if lt.get("path") != p]
                    save_library()
                    await push_library()
                else:
                    print(f"[wishes] konnte {p} nicht in den Papierkorb verschieben", flush=True)
            _state["wishes"] = [x for x in _state["wishes"] if x.get("id") != wid]
            _wish_outcomes[wid] = {"state": "abgelehnt", "title": w.get("title", "")}
            save_wishes()
            await push_wishes()

    elif t == "enrich_track":
        path = msg.get("path", "")
        force = bool(msg.get("force", False))
        if path and os.path.exists(path):
            asyncio.create_task(_enrich_track(path, force=force))

    elif t == "play_next":
        nxt = _state["current_idx"] + 1
        if nxt < len(_state["queue"]):
            await handle_message(ws, {"type": "play_at", "index": nxt})

    elif t == "play_prev":
        prv = _state["current_idx"] - 1
        if prv >= 0:
            await handle_message(ws, {"type": "play_at", "index": prv})

    elif t == "pause":
        _state["playing"] = False
        await push_player()

    elif t == "resume":
        _state["playing"] = True
        ci = _state["current_idx"]
        if 0 <= ci < len(_state["queue"]):
            track = _state["queue"][ci]
            if not track.get("played"):
                track["played"]    = True
                track["played_at"] = int(time.time())
                track["play_count"] = track.get("play_count", 0) + 1
                save_queue()
                await push_queue()
        await push_player()

    elif t == "seek":
        _state["position_ms"] = msg.get("position_ms", 0)
        await push_player()

    elif t == "position_update":
        _state["position_ms"] = msg.get("position_ms", 0)
        _state["duration_ms"] = msg.get("duration_ms", _state["duration_ms"])
        if _remote_clients:
            asyncio.create_task(_broadcast_remote_pos())

    elif t == "seek_relative":
        delta  = int(msg.get("delta_ms", 0))
        new_pos = max(0, _state["position_ms"] + delta)
        if _state["duration_ms"] > 0:
            new_pos = min(new_pos, _state["duration_ms"] - 200)
        _state["position_ms"] = new_pos
        await push_player()

    elif t == "library_remove":
        path = msg.get("path", "")
        _state["library"] = [lt for lt in _state["library"] if lt.get("path") != path]
        save_library()
        await push_library()

    elif t == "library_remove_disk":
        path = msg.get("path", "")
        if path:
            # Erst in den Papierkorb, dann aus der Bibliothek. Frueher lief es
            # andersherum, und ein fehlgeschlagenes Loeschen fiel niemandem auf:
            # Eintrag weg, Datei noch da.
            ok = True
            if os.path.exists(path):
                ok = await asyncio.get_running_loop().run_in_executor(None, _move_to_trash, path)
            if ok:
                _state["library"] = [lt for lt in _state["library"] if lt.get("path") != path]
                save_library()
                await push_library()
            else:
                await broadcast({"type": "scan_status",
                                 "text": f"Konnte nicht in den Papierkorb: {Path(path).name} (in Benutzung?)"})

    elif t == "set_volume":
        _state["volume"] = min(100, max(0, int(float(msg.get("value", 80)))))
        save_settings()
        await broadcast({"type": "settings", "volume": _state["volume"], "crossfade_s": _state["crossfade_s"]})
        if _remote_clients:
            asyncio.create_task(_broadcast_remote_state())

    elif t == "set_normalize_volume":
        _state["normalize_volume"] = bool(msg.get("value", True))
        if "target_lufs" in msg:
            _state["target_lufs"] = float(msg["target_lufs"])
        save_settings()
        await broadcast({"type": "settings", "normalize_volume": _state["normalize_volume"], "target_lufs": _state["target_lufs"]})
        if _remote_clients:
            asyncio.create_task(_broadcast_remote_state())

    elif t == "set_crossfade":
        _state["crossfade_s"] = msg.get("seconds", 4)
        save_settings()
        await broadcast({"type": "settings", "volume": _state["volume"], "crossfade_s": _state["crossfade_s"]})

    elif t == "add_favorite":
        fav_path = msg.get("path", "")
        fav_name = msg.get("name", Path(fav_path).name if fav_path else "")
        if fav_path and os.path.isdir(fav_path):
            favs = _state.get("favorites", [])
            if not any(f["path"] == fav_path for f in favs):
                favs.append({"name": fav_name, "path": fav_path})
                _state["favorites"] = favs
                save_settings()
        await ws.send_text(json.dumps({"type": "favorites", "items": _state.get("favorites", [])}))

    elif t == "remove_favorite":
        fav_path = msg.get("path", "")
        _state["favorites"] = [f for f in _state.get("favorites", []) if f["path"] != fav_path]
        save_settings()
        await ws.send_text(json.dumps({"type": "favorites", "items": _state.get("favorites", [])}))

    elif t == "set_auto_scan_interval":
        _state["auto_scan_interval_min"] = max(0, int(msg.get("minutes", 0)))
        save_settings()
        await ws.send_text(json.dumps({"type": "settings",
                                       "auto_scan_interval_min": _state["auto_scan_interval_min"]}))

    elif t == "export_settings":
        payload = {
            "volume":                   _state["volume"],
            "crossfade_s":              _state["crossfade_s"],
            "bpm_analysis":             _state.get("bpm_analysis", True),
            "scan_recursive":           _state.get("scan_recursive", True),
            "watched_folders":          _state.get("watched_folders", []),
            "auto_mix":                 _state.get("auto_mix", True),
            "loudnorm_on_dl":           _state.get("loudnorm_on_dl", False),
            "loudnorm_target":          _state.get("loudnorm_target", -14.0),
            "loudnorm_tp":              _state.get("loudnorm_tp", -1.5),
            "playlist_folder_enabled":  _state.get("playlist_folder_enabled", True),
            "dl_filename_format":       _state.get("dl_filename_format", "title"),
            "download_dir":             _state.get("download_dir", str(BASE_DIR / "Downloads")),
            "auto_scan_interval_min":   _state.get("auto_scan_interval_min", 0),
        }
        await ws.send_text(json.dumps({"type": "settings_export", "data": payload}))

    elif t == "import_settings":
        data = msg.get("data", {})
        if isinstance(data, dict):
            for key, default in [
                ("volume", 80), ("crossfade_s", 8.0), ("bpm_analysis", True),
                ("scan_recursive", True), ("auto_mix", True), ("loudnorm_on_dl", False),
                ("loudnorm_target", -10.0), ("loudnorm_tp", -1.5),
                ("playlist_folder_enabled", True), ("dl_filename_format", "title"),
                ("download_dir", str(BASE_DIR / "Downloads")), ("auto_scan_interval_min", 0),
            ]:
                if key in data:
                    _state[key] = type(default)(data[key]) if not isinstance(default, bool) else bool(data[key])
            if "watched_folders" in data and isinstance(data["watched_folders"], list):
                _state["watched_folders"] = data["watched_folders"]
            save_settings()
            await ws.send_text(json.dumps({
                "type":                    "settings",
                "volume":                  _state["volume"],
                "crossfade_s":             _state["crossfade_s"],
                "scan_recursive":          _state.get("scan_recursive", True),
                "bpm_analysis":            _state.get("bpm_analysis", True),
                "auto_mix":                _state.get("auto_mix", True),
                "loudnorm_on_dl":          _state.get("loudnorm_on_dl", False),
                "loudnorm_target":         _state.get("loudnorm_target", -14.0),
                "loudnorm_tp":             _state.get("loudnorm_tp", -1.5),
                "playlist_folder_enabled": _state.get("playlist_folder_enabled", True),
                "dl_filename_format":      _state.get("dl_filename_format", "title"),
                "download_dir":            _state.get("download_dir", str(BASE_DIR / "Downloads")),
                "auto_scan_interval_min":  _state.get("auto_scan_interval_min", 0),
            }))

    elif t == "get_waveform":
        path = msg.get("path", "")
        data = await compute_waveform(path)
        await ws.send_text(json.dumps({"type": "waveform", "path": path, "data": data}))

    elif t == "get_waveform_next":
        path = msg.get("path", "")
        data = await compute_waveform(path)
        await ws.send_text(json.dumps({"type": "waveform_next", "path": path, "data": data}))

    elif t == "scan_library":
        folder = msg.get("folder", "")
        if folder and os.path.isdir(folder):
            # Liegt der Ordner schon in einem rekursiv beobachteten, wird er
            # ohnehin mit durchsucht — als eigener Eintrag waere er nur doppelt.
            schon_drin = folder in _state["watched_folders"] or (
                _state.get("scan_recursive", True)
                and any(_path_in_folder(folder, f, True) for f in _state["watched_folders"]))
            if not schon_drin:
                _state["watched_folders"].append(folder)
                save_settings()
                await broadcast({"type": "watched_folders", "items": _watched_folders_info()})

        async def _scan_all():
            # Ohne Ordnerauswahl alles neu einlesen — vorher passierte hier
            # schlicht nichts, etwa wenn der Knopf aus dem Remote kam.
            for f in _scan_folders():
                await scan_folder(f)
        asyncio.create_task(_scan_all())

    elif t == "get_watched_folders":
        await ws.send_text(json.dumps({"type": "watched_folders", "items": _watched_folders_info()}))

    elif t == "remove_watched_folder":
        folder = msg.get("folder", "")
        if folder in _state.get("watched_folders", []):
            lost = _library_paths_lost_without(folder)
            if msg.get("dry_run"):
                # Nur ausrechnen, was verschwinden wuerde — fuer die Rueckfrage
                await ws.send_text(json.dumps({"type": "watched_folder_impact",
                                               "folder": folder, "tracks": len(lost)}))
            else:
                # Dateien bleiben unangetastet; nur die Bibliothek vergisst sie
                _state["watched_folders"] = [f for f in _state["watched_folders"] if f != folder]
                save_settings()
                if lost:
                    weg = set(lost)
                    _state["library"] = [lt for lt in _state["library"] if lt.get("path") not in weg]
                    save_library()
                    await push_library()
                await broadcast({"type": "watched_folders", "items": _watched_folders_info()})

    elif t == "exclude_folder":
        # "Aus Bibliothek ausschliessen": Eintraege entfernen UND merken —
        # vorher holte der Ordner-Waechter die Titel nach 10 s zurueck.
        folder = msg.get("folder", "")
        if folder:
            if folder not in _state.setdefault("excluded_folders", []):
                _state["excluded_folders"].append(folder)
                save_settings()
            vorher = len(_state["library"])
            _state["library"] = [lt for lt in _state["library"] if not _is_excluded(lt.get("path", ""))]
            if len(_state["library"]) != vorher:
                save_library()
                await push_library()
            await broadcast({"type": "excluded_folders", "items": _state["excluded_folders"]})
            await broadcast({"type": "watched_folders", "items": _watched_folders_info()})

    elif t == "include_folder":
        # Wieder aufnehmen — der Waechter liest die Titel in den naechsten
        # Sekunden von selbst neu ein.
        folder = msg.get("folder", "")
        if folder in _state.get("excluded_folders", []):
            _state["excluded_folders"] = [f for f in _state["excluded_folders"] if f != folder]
            save_settings()
            await broadcast({"type": "excluded_folders", "items": _state["excluded_folders"]})

    elif t == "set_scan_recursive":
        _state["scan_recursive"] = bool(msg.get("enabled", True))
        save_settings()
        await ws.send_text(json.dumps({"type": "scan_recursive",
                                       "enabled": _state["scan_recursive"]}))

    elif t == "search":
        query = msg.get("query", "").strip()
        if query:
            asyncio.create_task(do_search(query, ws))

    elif t == "download_add":
        url    = msg.get("url", "").strip()
        fmt    = msg.get("format", "mp3-best")
        choice = msg.get("playlist_choice")   # None | "single" | "all"
        # "song"/"video": im Dialog entschieden, dann nicht noch einmal pruefen.
        # dupe_checked: "Trotzdem laden" trotz Treffer in der Bibliothek.
        video_choice = msg.get("video_choice")
        dupe_checked = bool(msg.get("dupe_checked"))
        if url:
            if _is_spotify(url):
                asyncio.create_task(run_spotify_download(url, fmt))
            elif choice is None and _is_mixed_playlist_url(url):
                asyncio.create_task(_ask_playlist_choice(url, fmt, ws))
            else:
                if choice == "single":
                    url = _strip_playlist_params(url)
                if video_choice is None and _is_single_link(url):
                    asyncio.create_task(_check_video_then_download(url, fmt, ws, check_dupes=not dupe_checked))
                else:
                    asyncio.create_task(run_download(url, fmt))

    elif t == "set_spotify_creds":
        _state["spotify_client_id"]     = str(msg.get("client_id", "")).strip()
        _state["spotify_client_secret"] = str(msg.get("client_secret", "")).strip()
        save_settings()

    elif t == "set_services":
        if "lastfm_api_key"  in msg: _state["lastfm_api_key"]  = str(msg["lastfm_api_key"]).strip()
        if "acoustid_api_key" in msg: _state["acoustid_api_key"] = str(msg["acoustid_api_key"]).strip()
        save_settings()

    elif t == "set_radio":
        _state["radio_enabled"] = bool(msg.get("enabled", False))
        save_settings()
        await broadcast({"type": "radio_status", "enabled": _state["radio_enabled"]})
        if _remote_clients:
            asyncio.create_task(_broadcast_remote_state())
        if _state["radio_enabled"]:
            asyncio.create_task(_check_radio_queue())

    elif t == "identify_track":
        path = msg.get("path", "")
        if path and os.path.exists(path):
            result = await _acoustid_identify(path)
            await ws.send_text(json.dumps({"type": "track_identified", **result}))

    elif t == "download_fpcalc":
        asyncio.create_task(_download_fpcalc(ws))

    elif t == "install_spotdl":
        asyncio.create_task(_install_spotdl(ws))

    elif t == "download_stop":
        # Kill entire session (all tracks) and terminate subprocess
        sid = msg.get("session_id")
        if sid is not None:
            proc = _dl_procs.get(sid)
            if proc:
                try: proc.kill()
                except Exception: pass
            _state["downloads"] = [d for d in _state["downloads"]
                                    if d.get("session", d.get("id")) != sid]
        await push_downloads()

    elif t == "download_cancel":
        # Remove a single finished/error item from the list (doesn't kill subprocess)
        dl_id = msg.get("id")
        _state["downloads"] = [d for d in _state["downloads"] if d.get("id") != dl_id]
        await push_downloads()

    elif t == "download_clear_done":
        _state["downloads"] = [d for d in _state["downloads"] if d.get("status") == "active"]
        await push_downloads()

    elif t == "queue_insert_at":
        pos = int(msg.get("index", len(_state["queue"])))
        pos = max(0, min(pos, len(_state["queue"])))
        track = {
            "path":         msg.get("path", ""),
            "title":        msg.get("title") or Path(msg.get("path","")).stem,
            "duration_sec": float(msg.get("duration_sec", 0) or 0),
            "lufs":         float(msg.get("lufs", -99.0) or -99.0),
            "bpm":          int(msg.get("bpm", 0) or 0),
            "bitrate_kbps": int(msg.get("bitrate_kbps", 0) or 0),
            "played": False,
        }
        if not _queue_is_duplicate(track["path"], track["title"]):
            _state["queue"].insert(pos, track)
            if _state["current_idx"] >= pos:
                _state["current_idx"] += 1
            save_queue()
            await push_queue()  # carries current_idx — no push_player needed

    elif t == "queue_insert_next":
        track = {
            "path":         msg.get("path", ""),
            "title":        msg.get("title") or Path(msg.get("path","")).stem,
            "duration_sec": float(msg.get("duration_sec", 0) or 0),
            "lufs":         float(msg.get("lufs", -99.0) or -99.0),
            "bpm":          int(msg.get("bpm", 0) or 0),
            "bitrate_kbps": int(msg.get("bitrate_kbps", 0) or 0),
            "played": False,
        }
        if not _queue_is_duplicate(track["path"], track["title"]):
            pos = max(0, _state["current_idx"] + 1)
            _state["queue"].insert(pos, track)
            save_queue()
            await push_queue()

    elif t == "queue_move":
        from_i = msg.get("from", -1)
        to_i   = msg.get("to", -1)
        q = _state["queue"]
        if 0 <= from_i < len(q) and 0 <= to_i <= len(q) and from_i != to_i:
            ci   = _state["current_idx"]
            item = q.pop(from_i)
            real_to = (to_i - 1) if to_i > from_i else to_i
            q.insert(real_to, item)
            if ci == from_i:
                _state["current_idx"] = real_to
            elif from_i < ci <= real_to:
                _state["current_idx"] = ci - 1
            elif real_to <= ci < from_i:
                _state["current_idx"] = ci + 1
            save_queue()
            await push_queue()  # carries current_idx — no push_player needed

    elif t == "queue_shuffle":
        q  = _state["queue"]
        ci = _state["current_idx"]
        if len(q) > 1:
            if 0 <= ci < len(q):
                cur = q.pop(ci)
                random.shuffle(q)
                q.insert(0, cur)
                _state["current_idx"] = 0
            else:
                random.shuffle(q)
            save_queue()
            await push_queue()
            await push_player()

    elif t == "queue_shuffle_selected":
        indices = msg.get("indices", [])
        q = _state["queue"]
        ci = _state["current_idx"]
        valid = sorted({i for i in indices if 0 <= i < len(q)})
        if len(valid) > 1:
            # Laufenden Titel VOR dem Mischen merken — vorher wurde erst danach
            # nachgesehen und dabei der Titel gefunden, der nun an seiner
            # Stelle steht; der Zeiger zeigte dann auf etwas anderes.
            cur_path = q[ci]["path"] if 0 <= ci < len(q) else None
            tracks = [q[i] for i in valid]
            random.shuffle(tracks)
            for i, idx in enumerate(valid):
                q[idx] = tracks[i]
            if cur_path:
                _state["current_idx"] = next((i for i, t in enumerate(q) if t.get("path") == cur_path), ci)
            save_queue()
            await push_queue()
            await push_player()

    elif t == "queue_mark_unplayed":
        for item in _state["queue"]:
            item["played"] = False
        save_queue()
        await push_queue()

    elif t == "queue_remove_played":
        ci  = _state["current_idx"]
        cur_path = _state["queue"][ci]["path"] if 0 <= ci < len(_state["queue"]) else None
        _state["queue"] = [t for t in _state["queue"] if not t.get("played") or t.get("path") == cur_path]
        # Recalculate current_idx
        if cur_path:
            _state["current_idx"] = next((i for i, t in enumerate(_state["queue"]) if t.get("path") == cur_path), -1)
        else:
            _state["current_idx"] = -1
        save_queue()
        await push_queue()
        await push_player()

    elif t == "queue_remove_duplicates":
        ci = _state["current_idx"]
        cur_path = _state["queue"][ci]["path"] if 0 <= ci < len(_state["queue"]) else None
        seen_paths: set[str] = set()
        seen_titles: set[str] = set()
        deduped = []
        for qt in _state["queue"]:
            p = qt.get("path")
            if p in seen_paths:
                continue
            norm = _norm_queue_title(qt.get("title", ""))
            if norm and norm in seen_titles:
                continue
            seen_paths.add(p)
            if norm:
                seen_titles.add(norm)
            deduped.append(qt)
        _state["queue"] = deduped
        _state["current_idx"] = next((i for i, t in enumerate(_state["queue"]) if t.get("path") == cur_path), -1) \
            if cur_path else -1
        save_queue()
        await push_queue()
        await push_player()

    elif t == "set_auto_remove_played":
        _state["auto_remove_played"] = bool(msg.get("enabled", False))
        save_settings()
        await broadcast({"type": "auto_remove_played", "enabled": _state["auto_remove_played"]})

    elif t == "queue_shuffle_unplayed":
        q   = _state["queue"]
        ci  = _state["current_idx"]
        # Nur ungespielte Tracks NACH dem aktuellen Index mischen
        future = [(i, q[i]) for i in range(ci + 1, len(q)) if not q[i].get("played")]
        if len(future) > 1:
            idxs, tracks = zip(*future)
            shuffled = list(tracks)
            random.shuffle(shuffled)
            for i, qt in zip(idxs, shuffled):
                q[i] = qt
        save_queue()
        await push_queue()

    elif t == "queue_clear":
        _state["queue"].clear()
        _state["current_idx"] = -1
        _state["playing"]     = False
        save_queue()
        await push_queue()
        await push_player()

    elif t == "set_shuffle":
        _state["shuffle"] = bool(msg.get("value", False))
        save_settings()
        await push_player()

    elif t == "set_repeat":
        _state["repeat"] = int(msg.get("value", 0)) % 3
        save_settings()
        await push_player()

    elif t == "save_playlist":
        name = (msg.get("name") or "").strip()
        if name:
            PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r'[<>:"/\\|?*]', '_', name)
            pl_path = PLAYLISTS_DIR / (safe + ".m3u")
            paths_filter = set(msg.get("paths") or [])
            tracks_to_save = [tr for tr in _state["queue"]
                              if not paths_filter or tr.get("path") in paths_filter]
            with open(pl_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for track in tracks_to_save:
                    dur   = int(track.get("duration_sec", -1))
                    title = track.get("title", "")
                    f.write(f"#EXTINF:{dur},{title}\n{track['path']}\n")
            await ws.send_text(json.dumps({"type": "playlists",
                                           "items": _get_playlists()}))
            if msg.get("clear_after"):
                _state["queue"] = []
                _state["current_idx"] = -1
                _state["playing"] = False
                save_queue()
                await push_queue()
                await push_player()

    elif t == "load_playlist":
        pl_path = msg.get("path", "")
        if os.path.exists(pl_path):
            tracks = _parse_m3u(pl_path)
            _state["queue"].extend(tracks)
            save_queue()
            await push_queue()

    elif t == "get_download_tree":
        dl_dir = Path(_state.get("download_dir", str(BASE_DIR / "Downloads")))
        AUDIO_EXT = {'.mp3', '.opus', '.m4a', '.flac', '.wav', '.ogg', '.aac', '.wma'}

        def _scan_dl_dir(path: Path, depth: int = 0) -> dict:
            tracks, subfolders = [], []
            try:
                for entry in sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
                    if entry.is_dir() and not entry.name.startswith('.'):
                        if depth < 4:
                            subfolders.append(_scan_dl_dir(entry, depth + 1))
                    elif entry.is_file() and entry.suffix.lower() in AUDIO_EXT:
                        tracks.append({"path": str(entry), "name": entry.stem, "mtime": entry.stat().st_mtime})
            except PermissionError:
                pass
            return {"name": path.name, "path": str(path),
                    "tracks": sorted(tracks, key=lambda x: x["name"].lower()),
                    "folders": subfolders}

        tree = {"folders": [], "files": []}
        if dl_dir.exists():
            try:
                for entry in sorted(dl_dir.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
                    if entry.is_dir() and not entry.name.startswith('.'):
                        tree["folders"].append(_scan_dl_dir(entry))
                    elif entry.is_file() and entry.suffix.lower() in AUDIO_EXT:
                        tree["files"].append({"path": str(entry), "name": entry.stem, "mtime": entry.stat().st_mtime})
            except PermissionError:
                pass
        await ws.send_text(json.dumps({"type": "download_tree", "tree": tree}))

    elif t == "get_playlists":
        await ws.send_text(json.dumps({"type": "playlists",
                                       "items": _get_playlists()}))

    elif t == "get_playlist_content":
        pl_path = msg.get("path", "")
        if os.path.exists(pl_path):
            pl_tracks = _parse_m3u(pl_path)
            lib_by_path = {lt["path"]: lt for lt in _state["library"]}
            result = []
            for pt in pl_tracks:
                lt = lib_by_path.get(pt["path"])
                result.append(lt if lt else pt)
            await ws.send_text(json.dumps({"type": "playlist_content",
                                           "path": pl_path, "tracks": result}))

    elif t == "playlist_add_track":
        pl_path   = msg.get("playlist", "")
        trk_path  = msg.get("path", "")
        trk_title = msg.get("title", "") or Path(trk_path).stem
        trk_dur   = int(msg.get("duration_sec", 0))
        if os.path.exists(pl_path) and trk_path:
            # Read existing paths to avoid dupes
            existing = set()
            try:
                with open(pl_path, "r", encoding="utf-8") as f:
                    for line in f:
                        l = line.strip()
                        if l and not l.startswith("#"):
                            existing.add(l)
            except Exception:
                pass
            if trk_path not in existing:
                with open(pl_path, "a", encoding="utf-8") as f:
                    f.write(f"#EXTINF:{trk_dur},{trk_title}\n{trk_path}\n")
                pl_tracks = _parse_m3u(pl_path)
                lib_by_path = {lt["path"]: lt for lt in _state["library"]}
                result = [lib_by_path.get(pt["path"], pt) for pt in pl_tracks]
                await ws.send_text(json.dumps({"type": "playlist_content",
                                               "path": pl_path, "tracks": result}))

    elif t == "delete_playlist":
        pl_path = msg.get("path", "")
        if pl_path and os.path.exists(pl_path):
            await asyncio.get_running_loop().run_in_executor(None, _move_to_trash, pl_path)
            await ws.send_text(json.dumps({"type": "playlists",
                                           "items": _get_playlists()}))

    elif t == "playlist_remove_track":
        pl_path  = msg.get("playlist", "")
        rm_path  = msg.get("path", "")
        if pl_path and rm_path and os.path.exists(pl_path):
            try:
                with open(pl_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                out = ["#EXTM3U\n"]
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    if line.startswith("#EXTINF:"):
                        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
                        if nxt != rm_path:
                            out.append(lines[i])
                            if i + 1 < len(lines):
                                out.append(lines[i + 1])
                        i += 2
                    elif line and not line.startswith("#"):
                        if line != rm_path:
                            out.append(lines[i])
                        i += 1
                    else:
                        i += 1
                with open(pl_path, "w", encoding="utf-8") as f:
                    f.writelines(out)
                # Push updated content
                pl_tracks = _parse_m3u(pl_path)
                lib_by_path = {lt["path"]: lt for lt in _state["library"]}
                result = [lib_by_path.get(pt["path"], pt) for pt in pl_tracks]
                await ws.send_text(json.dumps({"type": "playlist_content",
                                               "path": pl_path, "tracks": result}))
            except Exception as e:
                print(f"[playlist_remove_track] {e}")

    elif t == "find_duplicates":
        import unicodedata
        def norm_title(s):
            s = unicodedata.normalize('NFKC', (s or '').lower().strip())
            return re.sub(r'[^\w\s]', '', re.sub(r'\s+', ' ', s))
        seen = {}
        dupes = []
        for lt in _state["library"]:
            key = norm_title(lt.get("title", ""))
            if key in seen:
                if seen[key] not in dupes: dupes.append(seen[key])
                dupes.append(lt["path"])
            else:
                seen[key] = lt["path"]
        await ws.send_text(json.dumps({"type": "duplicates", "paths": dupes}))

    elif t == "genre_suggest":
        if "folder_rules" in msg:
            _genre_rules_save(msg.get("folder_rules") or {})
        asyncio.create_task(_genre_suggest(ws, online=bool(msg.get("online", True))))

    elif t == "genre_apply":
        asyncio.create_task(_genre_apply(msg.get("items") or [], ws))

    elif t == "genre_cancel":
        global _genre_cancel
        _genre_cancel = True

    elif t == "quality_candidates":
        path = msg.get("path", "")
        if path:
            asyncio.create_task(_quality_candidates(path, msg.get("query", ""), ws))

    elif t == "quality_replace":
        path, url = msg.get("path", ""), msg.get("url", "")
        if path and url:
            asyncio.create_task(_quality_replace(path, url, ws))

    elif t == "analyze_library_meta":
        paths = msg.get("paths")
        asyncio.create_task(_analyze_library_meta_task(
            [str(p) for p in paths] if isinstance(paths, list) else None))

    elif t == "cancel_analyze":
        global _analyze_cancel
        _analyze_cancel = True

    elif t == "library_update_meta":
        path   = msg.get("path", "")
        title  = (msg.get("title") or "").strip()
        artist = (msg.get("artist") or "").strip()
        if path and os.path.exists(path) and title:
            asyncio.create_task(_update_track_meta(path, title, artist))

    elif t == "set_bpm_analysis":
        _state["bpm_analysis"] = bool(msg.get("enabled", True))
        save_settings()

    elif t == "automix_trigger":
        title = (msg.get("title") or "").strip()
        if title and _state.get("auto_mix", True):
            asyncio.create_task(_do_automix(title))

    elif t == "set_auto_mix":
        _state["auto_mix"] = bool(msg.get("value", True))
        save_settings()
        # Kann jetzt auch vom Handy kommen — App und Fernbedienung nachziehen
        await broadcast({"type": "settings", "auto_mix": _state["auto_mix"]})
        if _remote_clients:
            asyncio.create_task(_broadcast_remote_state())

    elif t == "get_history":
        await ws.send_text(json.dumps({"type": "history", "items": _state["history"][:100]}))

    elif t == "clear_play_history":
        for lt in _state["library"]:
            lt["play_count"] = 0
        save_library()
        await push_library()

    elif t == "remote_start":
        await _start_remote_server(ws)

    elif t == "remote_stop":
        await _stop_remote_server()

    elif t == "remote_new_key":
        # Falls der Link in falsche Haende geraten ist: neuer Schluessel,
        # verbundene Fernbedienungen fliegen raus und muessen neu scannen.
        _state["remote_key"] = ""
        _remote_key()
        for rws in list(_remote_clients):
            try: await rws.close(code=1008)
            except Exception: pass
        _remote_clients.clear()
        if _remote_server is not None:
            ip = _get_local_ip()
            await broadcast({"type": "remote_status", "running": True,
                             "ip": ip, "port": _remote_port, **_remote_urls(ip)})

    elif t == "set_ytdlp_autoupdate":
        _state["ytdlp_autoupdate"] = bool(msg.get("value", True))
        save_settings()
        await ws.send_text(json.dumps({"type": "settings",
                                       "ytdlp_autoupdate": _state["ytdlp_autoupdate"]}))

    elif t == "set_remote_autostart":
        _state["remote_autostart"] = bool(msg.get("value", False))
        save_settings()
        # Frueher folgte hier ein pauschales remote_status running=False — die
        # Einstellungen zeigten dann "gestoppt" und keinen QR-Code mehr,
        # obwohl der Server weiterlief. Am laufenden Server aendert sich nichts.
        await ws.send_text(json.dumps({"type": "settings", "remote_autostart": _state["remote_autostart"]}))

    elif t == "get_notes":
        await ws.send_text(json.dumps({"type": "notes", "text": _state["notes"]}))

    elif t == "save_notes":
        _state["notes"] = str(msg.get("text", ""))
        schedule_notes_save()

    elif t == "normalize_files":
        paths = msg.get("paths", [])
        target_lufs = float(msg.get("target_lufs", -14.0))
        target_tp   = float(msg.get("target_tp", -1.5))
        if paths:
            asyncio.create_task(_normalize_files(paths, target_lufs, target_tp, ws))

    elif t == "get_logs":
        await ws.send_json({"type": "logs", "lines": list(_log_buffer)})

    elif t == "check_tools":
        asyncio.create_task(_check_tools(ws))

    elif t == "update_ytdlp":
        asyncio.create_task(_update_ytdlp(ws))

    elif t == "check_tool_updates":
        asyncio.create_task(_tools_check_once())

    elif t == "dismiss_ytdlp_updated":
        _state.setdefault("tool_updates", {}).pop("ytdlp_updated", None)
        save_settings()
        await broadcast({"type": "tool_updates", "items": _state["tool_updates"]})

    elif t == "set_loudnorm_dl":
        _state["loudnorm_on_dl"]  = bool(msg.get("enabled", True))
        _state["loudnorm_target"] = float(msg.get("target", -10.0))
        _state["loudnorm_tp"]     = float(msg.get("true_peak", _state.get("loudnorm_tp", -1.5)))
        save_settings()

    elif t == "set_download_folder":
        folder = msg.get("path", "")
        if folder and os.path.isdir(folder):
            _state["download_dir"] = folder
            save_settings()

    elif t == "set_playlist_folder":
        _state["playlist_folder_enabled"] = bool(msg.get("enabled", True))
        save_settings()

    elif t == "set_dl_filename_format":
        _state["dl_filename_format"] = str(msg.get("format", "title"))
        save_settings()

# ── playlist helpers ─────────────────────────────────────────────────────────
def _get_playlists() -> list[dict]:
    if not PLAYLISTS_DIR.exists():
        return []
    result = []
    for p in sorted(PLAYLISTS_DIR.glob("*.m3u")):
        try:
            count = sum(1 for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
                        if ln.strip() and not ln.startswith("#"))
        except Exception:
            count = 0
        result.append({"name": p.stem, "path": str(p), "track_count": count})
    return result

def _parse_m3u(path: str) -> list[dict]:
    tracks = []; title = ""; dur = 0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip().lstrip('﻿')
                if line.startswith("#EXTINF:"):
                    parts = line[8:].split(",", 1)
                    try: dur = int(parts[0])
                    except Exception: dur = 0
                    title = parts[1] if len(parts) > 1 else ""
                elif line and not line.startswith("#") and os.path.exists(line):
                    tracks.append({
                        "path": line,
                        "title": title or Path(line).stem,
                        "duration_sec": max(0, dur),
                        "lufs": -99.0, "bpm": 0, "bitrate_kbps": 0, "played": False,
                    })
                    title = ""; dur = 0
    except Exception:
        pass
    return tracks

# ── library scan ─────────────────────────────────────────────────────────────
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus", ".wma"}

async def scan_folder(folder: str):
    await broadcast({"type": "scan_status", "text": "Scanne…"})
    loop = asyncio.get_running_loop()
    tracks = await loop.run_in_executor(None, _scan_sync, folder)

    lib_by_path = {t["path"]: t for t in _state["library"]}
    scanned_paths = {t["path"] for t in tracks}
    added = updated = 0

    for t in tracks:
        existing = lib_by_path.get(t["path"])
        if existing is None:
            _state["library"].append(t)
            added += 1
        else:
            if t.get("mtime", 0) != existing.get("mtime", 0):
                for key in ("title", "artist", "album_artist", "folder", "ext",
                            "duration_sec", "bitrate_kbps", "comment", "mtime"):
                    if key in t:
                        existing[key] = t[key]
                updated += 1
            # Datei wieder da → missing-Flag entfernen
            existing.pop("missing", None)

    # Tracks aus diesem Ordner die nicht mehr auf der Platte liegen → als missing markieren
    folder_path = Path(folder)
    for lt in _state["library"]:
        lt_path = Path(lt.get("path", ""))
        try:
            lt_path.relative_to(folder_path)
        except ValueError:
            continue
        was_missing = lt.get("missing", False)
        now_missing = lt["path"] not in scanned_paths
        if now_missing and not was_missing:
            lt["missing"] = True
        elif not now_missing and was_missing:
            lt.pop("missing", None)

    save_library()
    await push_library()

    parts = [f"{len(_state['library'])} Tracks"]
    if added:    parts.append(f"+{added} neu")
    if updated:  parts.append(f"{updated} aktualisiert")
    await broadcast({"type": "scan_status", "text": "Fertig · " + " · ".join(parts)})
    await asyncio.sleep(4)
    await broadcast({"type": "scan_status", "text": ""})

def _move_to_trash(path: str) -> bool:
    """Datei in den Windows-Papierkorb verschieben statt endgueltig loeschen.

    Frueher stand hier os.remove — ein Fehlgriff beim Aufraeumen von
    Duplikaten war nicht mehr rueckgaengig zu machen, und die Bibliothek
    kennt einen Knopf, der alle ausgeblendeten Duplikate auf einmal loescht.
    SHFileOperationW aus der Shell, damit keine zusaetzliche Bibliothek noetig
    ist. Liefert True, wenn die Datei danach weg ist.
    """
    if not path or not os.path.exists(path):
        return False
    if sys.platform != "win32":
        os.remove(path)
        return True
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", wintypes.LPVOID), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x0004, 0x0010, 0x0040, 0x0400
    # pFrom muss doppelt nullterminiert sein; die zweite Null haengt ctypes an
    op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(path) + "\0", None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
                         False, None, None)
    try:
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception as e:
        print(f"[trash] {path}: {e}", flush=True)
        return False
    return rc == 0 and not op.fAnyOperationsAborted and not os.path.exists(path)

def _path_in_folder(path: str, folder: str, recursive: bool) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    f = os.path.normcase(os.path.abspath(folder))
    if recursive:
        return p.startswith(f + os.sep)
    return os.path.dirname(p) == f

def _is_excluded(path: str) -> bool:
    """Liegt der Pfad in einem Ordner, den der Nutzer aus der Bibliothek
    ausgeschlossen hat? Solche Titel holen Scan und Waechter nicht zurueck."""
    for f in _state.get("excluded_folders", []):
        if _path_in_folder(path, f, True) or            os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(f)):
            return True
    return False

def _watched_folders_info() -> list[dict]:
    """Beobachtete Ordner fuer die Einstellungen: Titelanzahl und ob der
    Ordner schon in einem anderen, rekursiv durchsuchten liegt (dann ist er
    ueberfluessig und wird nur doppelt gescannt)."""
    folders = list(_state.get("watched_folders", []))
    recursive = _state.get("scan_recursive", True)
    info = []
    for f in folders:
        inside = next((o for o in folders if o != f and recursive
                       and _path_in_folder(f, o, True)), None)
        info.append({
            "path": f,
            "exists": os.path.isdir(f),
            "tracks": sum(1 for lt in _state.get("library", [])
                          if _path_in_folder(lt.get("path", ""), f, recursive)),
            "inside": inside,
        })
    return info

def _library_paths_lost_without(folder: str) -> list[str]:
    """Welche Bibliothekseintraege kein beobachteter Ordner und auch nicht der
    Download-Ordner mehr abdeckt, wenn `folder` wegfaellt."""
    recursive = _state.get("scan_recursive", True)
    remaining = [f for f in _state.get("watched_folders", []) if f != folder]
    dl = _state.get("download_dir") or str(BASE_DIR / "Downloads")
    lost = []
    for lt in _state.get("library", []):
        path = lt.get("path", "")
        if not _path_in_folder(path, folder, recursive):
            continue
        if any(_path_in_folder(path, f, recursive) for f in remaining):
            continue
        if dl and _path_in_folder(path, dl, recursive):
            continue
        lost.append(path)
    return lost

def _scan_folders() -> list[str]:
    """Beobachtete Ordner samt Download-Ordner.

    Der Download-Ordner steht bewusst nicht in watched_folders — er soll
    mitziehen, wenn der Nutzer ihn umstellt. Frisch geladene Titel sollen
    trotzdem in der Bibliothek auftauchen, ohne ihn extra hinzuzufuegen.
    """
    folders = [f for f in _state.get("watched_folders", []) if os.path.isdir(f)]
    dl = _state.get("download_dir") or str(BASE_DIR / "Downloads")
    if not os.path.isdir(dl):
        return folders

    dl_n      = os.path.normcase(os.path.abspath(dl))
    recursive = _state.get("scan_recursive", True)
    for f in folders:
        f_n = os.path.normcase(os.path.abspath(f))
        # Schon abgedeckt: selber Ordner, oder Unterordner eines rekursiv
        # durchsuchten — sonst wuerde alles doppelt eingelesen.
        if dl_n == f_n or (recursive and dl_n.startswith(f_n + os.sep)):
            return folders
    return folders + [dl]

def _ytdlp_version_sync() -> str:
    try:
        r = subprocess.run([YTDLP, "--version"], capture_output=True, text=True,
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

async def _tools_check_once():
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
                await broadcast({"type": "tools_info", "ytdlp_version": neu})
        tu["ytdlp"] = {"latest": neueste, "available": veraltet}
    elif not lokal:
        tu.pop("ytdlp", None)       # nicht installiert: kein alter Hinweis stehen lassen
    # Ohne Netz (neueste leer) bleibt der letzte bekannte Stand

    if await loop.run_in_executor(None, _find_spotdl_cmd):
        s_lokal   = await loop.run_in_executor(None, _spotdl_version_sync)
        s_neueste = await loop.run_in_executor(None, _spotdl_latest_tag)
        if s_lokal and s_neueste:
            tu["spotdl"] = {"current": s_lokal, "latest": s_neueste.lstrip("v"),
                            "available": _is_newer(s_neueste, s_lokal)}
    else:
        tu.pop("spotdl", None)

    save_settings()
    await broadcast({"type": "tool_updates", "items": tu})

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

async def _auto_scan_loop():
    while True:
        interval = _state.get("auto_scan_interval_min", 0)
        if interval > 0:
            await asyncio.sleep(interval * 60)
            for folder in _scan_folders():
                await scan_folder(folder)
        else:
            await asyncio.sleep(60)

def _scan_sync(folder: str) -> list[dict]:
    result = []
    recursive = _state.get("scan_recursive", True)
    if recursive:
        iter_dirs = os.walk(folder)
    else:
        iter_dirs = [(folder, [], os.listdir(folder))]
    for root, _, files in iter_dirs:
        if _is_excluded(root):
            continue
        for f in files:
            if Path(f).suffix.lower() in AUDIO_EXTS:
                full = str(Path(os.path.join(root, f)))  # normalisiert Slashes auf Windows
                probe = _probe_sync(full)
                result.append(_make_library_entry(full, probe))
    return result

def _find_new_audio_paths(folder: str, existing: set[str], recursive: bool) -> list[str]:
    """Fast filesystem scan — returns only paths NOT already in the library (no ffprobe)."""
    new_paths = []
    if recursive:
        for root, _, files in os.walk(folder):
            if _is_excluded(root):
                continue
            for f in files:
                if Path(f).suffix.lower() in AUDIO_EXTS:
                    full = str(Path(os.path.join(root, f)))
                    if full not in existing:
                        new_paths.append(full)
    else:
        try:
            for f in os.listdir(folder):
                if Path(f).suffix.lower() in AUDIO_EXTS:
                    full = str(Path(os.path.join(folder, f)))
                    if full not in existing and not _is_excluded(full):
                        new_paths.append(full)
        except OSError:
            pass
    return new_paths

async def _watcher_loop():
    """Periodically check watched folders for new audio files."""
    await asyncio.sleep(15)   # initial delay — let app settle
    while True:
        try:
            folders   = _scan_folders()
            recursive = _state.get("scan_recursive", True)
            if folders:
                existing = {t["path"] for t in _state["library"]}
                new_paths: list[str] = []
                loop = asyncio.get_running_loop()
                for folder in folders:
                    if not os.path.isdir(folder):
                        continue
                    # Fast path: only list new files (no ffprobe for existing tracks)
                    found = await loop.run_in_executor(
                        None, _find_new_audio_paths, folder, existing, recursive)
                    for p in found:
                        if p not in existing:
                            new_paths.append(p)
                            existing.add(p)
                if new_paths:
                    new_tracks = []
                    for p in new_paths:
                        probe = await loop.run_in_executor(None, _probe_sync, p)
                        new_tracks.append(_make_library_entry(p, probe))
                    _state["library"].extend(new_tracks)
                    save_library()
                    await push_library()
                    await broadcast({"type": "scan_status",
                                     "text": f"+{len(new_tracks)} neue Tracks"})
                    await asyncio.sleep(4)
                    await broadcast({"type": "scan_status", "text": ""})
        except Exception as e:
            print(f"[watcher] error: {e}", flush=True)
        await asyncio.sleep(10)

# ── queue duplicate detection ────────────────────────────────────────────────
_QUEUE_NOISE_RE = re.compile(
    r'^\d+[\.\-\)\s]+|'                                          # leading track numbers: "01 - "
    r'\(\s*\d{4}\s*\)|\[\s*\d{4}\s*\]|'                        # years: (2024) [2024]
    r'\b(?:official|music|video|lyrics?|audio|hd|hq|4k|'
    r'remaster(?:ed)?|live|version|edit|mix|feat(?:uring)?|'
    r'ft|prod|explicit|clean|extended|radio|original|'
    r'album|single|cover|acoustic|instrumental)\b|'
    r'\(.*?\)|\[.*?\]',                                           # any parenthetical
    re.IGNORECASE
)

def _norm_queue_title(title: str) -> str:
    t = _QUEUE_NOISE_RE.sub(' ', title or '')
    t = re.sub(r'[^\w\s]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip().lower()

def _queue_is_duplicate(path: str, title: str, fuzzy: bool = False) -> bool:
    # Exact same file is always a duplicate. Fuzzy title matching is only used
    # for auto-added tracks (AutoMix) — explicit user adds should always work
    # unless it's literally the same file already queued.
    norm = _norm_queue_title(title)
    for q in _state["queue"]:
        if q.get("path") == path:           # exact same file
            return True
        if fuzzy and norm:
            qn = _norm_queue_title(q.get("title", ""))
            if qn and SequenceMatcher(None, norm, qn).ratio() >= 0.82:
                return True
    return False


# ── download ─────────────────────────────────────────────────────────────────
_dl_counter = 0

YTDLP = _find_tool("yt-dlp.exe", BASE_DIR, BASE_DIR / "bin")

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
    """yt-dlp-Kommando inklusive JS-Laufzeit."""
    return [YTDLP, *_JS_ARGS, *args]
FFMPEG_DIR = str(Path(FFMPEG).parent) if FFMPEG != "ffmpeg" else ""

# format-id → (audio_format, audio_quality)
_FMT_MAP = {
    "mp3-best": ("mp3",  "0"),
    "flac":     ("flac", "0"),
    "wav":      ("wav",  "0"),
    "m4a":      ("m4a",  "0"),
    "opus":     ("opus", "0"),
}

def _is_playlist(url: str) -> bool:
    return "list=" in url or "/playlist" in url

def _is_mixed_playlist_url(url: str) -> bool:
    """Link auf einen einzelnen Titel, der zugleich eine Playlist mitfuehrt.

    So sieht ein Link aus, den man aus einer laufenden Playlist kopiert:
    watch?v=ABC&list=PL…&index=7 — gemeint ist meist nur der eine Titel,
    frueher wurde stillschweigend die ganze Playlist geladen.
    """
    if not url.startswith("http") or "list=" not in url:
        return False
    # youtu.be/<id> ist die Kurzform aus dem Teilen-Menue und fuehrt weder
    # /watch noch v= mit — ohne den Fall liefe sie am Dialog vorbei.
    return "/watch" in url or "v=" in url or "youtu.be/" in url

def _strip_playlist_params(url: str) -> str:
    """list=/index=/start_radio entfernen — uebrig bleibt der reine Titel-Link."""
    import urllib.parse as _p
    try:
        parts = _p.urlsplit(url)
        keep = [(k, v) for k, v in _p.parse_qsl(parts.query, keep_blank_values=True)
                if k not in ("list", "index", "start_radio", "pp")]
        return _p.urlunsplit(parts._replace(query=_p.urlencode(keep)))
    except Exception:
        return url

async def _playlist_probe(url: str) -> dict:
    """Titel des Einzeltracks sowie Name und Laenge der Playlist holen."""
    async def _run(args: list[str]) -> list[str]:
        try:
            pr = await asyncio.create_subprocess_exec(
                *_yt(*args, "--no-warnings", "--quiet", "--encoding", "utf-8", url),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                creationflags=_NO_WINDOW)
            out, _ = await asyncio.wait_for(pr.communicate(), timeout=20)
            return out.decode(errors="replace").strip().splitlines()
        except Exception:
            return []

    pl_task = asyncio.create_task(_run(
        ["--flat-playlist", "--playlist-items", "1",
         "--print", "%(playlist_title)s", "--print", "%(playlist_count)s"]))
    tr_task = asyncio.create_task(_run(
        ["--no-playlist", "--skip-download", "--print", "%(title)s"]))
    pl_lines, tr_lines = await asyncio.gather(pl_task, tr_task)

    info: dict = {"track_title": "", "playlist_title": "", "count": 0}
    if tr_lines:
        info["track_title"] = tr_lines[0][:100]
    if len(pl_lines) >= 2:
        t = pl_lines[0].strip()
        if t and t not in ("NA", "N/A"):
            info["playlist_title"] = t[:80]
        try:
            info["count"] = int(pl_lines[1].strip())
        except Exception:
            pass
    return info

async def _ask_playlist_choice(url: str, fmt: str, ws: WebSocket):
    """Nachfragen, ob nur der Titel oder die ganze Playlist geladen wird."""
    async def _send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    await _send("playlist_choice_pending", url=url)
    info = await _playlist_probe(url)

    # Keine echte Playlist dahinter (oder Abfrage fehlgeschlagen) — dann nicht
    # mit einer sinnlosen Rueckfrage aufhalten, sondern einfach den Titel laden.
    if info["count"] <= 1:
        await _send("playlist_choice_cancel")
        asyncio.create_task(_check_video_then_download(_strip_playlist_params(url), fmt, ws))
        return

    await _send("playlist_choice", url=url, format=fmt, **info)

def _ytdlp_cmd(url: str, fmt_id: str, out_dir: str, playlist_folder: str | None = None,
               force_folder: bool = False) -> list[str]:
    audio_fmt, quality = _FMT_MAP.get(fmt_id, ("mp3", "0"))
    is_search = not url.startswith("http")
    is_playlist = not is_search and _is_playlist(url)

    fn_fmt = _state.get("dl_filename_format", "title")
    if fn_fmt == "uploader_title":
        name_tmpl = "%(uploader)s - %(title)s.%(ext)s"
    elif fn_fmt == "artist_title":
        name_tmpl = "%(artist)s - %(title)s.%(ext)s"
    else:
        name_tmpl = "%(title)s.%(ext)s"

    use_folder = (is_playlist or force_folder) and _state.get("playlist_folder_enabled", True) and playlist_folder
    if use_folder:
        # Use the pre-fetched playlist title as literal folder name (avoids "NA" from template)
        out_tmpl = os.path.join(out_dir, playlist_folder, name_tmpl)
    else:
        out_tmpl = os.path.join(out_dir, name_tmpl)

    cmd = [
        YTDLP,
        *_JS_ARGS,
        "-x",
        "--audio-format", audio_fmt,
        "--audio-quality", quality,
        "--yes-playlist" if is_playlist else "--no-playlist",
        "--no-overwrites",      # skip if file already exists
        "--ignore-errors",      # skip unavailable videos, don't abort playlist
        "-o", out_tmpl,
        "--newline",
        "--encoding", "utf-8",   # force UTF-8 console output (Windows defaults to the ANSI codepage, mangling umlauts)
    ]
    if FFMPEG_DIR:
        cmd += ["--ffmpeg-location", FFMPEG_DIR]

    cmd += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
    cmd += ["--embed-metadata",
            "--parse-metadata", "%(uploader)s:%(meta_comment)s"]

    if _state.get("loudnorm_on_dl", False):
        t  = float(_state.get("loudnorm_target", -10.0))
        tp = float(_state.get("loudnorm_tp", -1.5))
        # Nur beim Umwandeln in das Zielformat. "ffmpeg:" allein galt fuer alle
        # ffmpeg-Schritte — der Metadaten-Schritt kopiert die Spur (-c copy),
        # scheiterte am Filter, und Tags und Cover fehlten im fertigen Titel.
        cmd += ["--postprocessor-args",
                f"ExtractAudio+ffmpeg_o:-af loudnorm=I={t}:TP={tp}:LRA=11"]

    if is_search:
        # Erster Song-Treffer von YouTube Music ("ytmsearch1:" gibt es nicht)
        cmd += ["--playlist-items", "1", _ytm_search_url(url)]
    else:
        cmd.append(url)
    return cmd

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
    except Exception:
        pass
    return results

_VIDEO_TITLE_RE = re.compile(
    r'\b(official\s+(?:music\s+)?video|music\s+video|official\s+mv|'
    r'\bmv\b|live\s+(?:version|performance|session|at)|concert|tour)\b',
    re.IGNORECASE
)

async def _audit_playlist_for_videos(playlist_url: str) -> list[dict]:
    """
    Flat-list a playlist. For entries whose title contains video keywords,
    search YTM for an audio replacement. Returns list of {url, title, replaced}.
    """
    base_args = ["--flat-playlist", "-j", "--quiet", "--no-warnings"]
    if FFMPEG_DIR:
        base_args += ["--ffmpeg-location", FFMPEG_DIR]

    # Flat-list the whole playlist
    entries: list[dict] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            *_yt(*base_args, playlist_url),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=_NO_WINDOW)
        async for raw in proc.stdout:
            try:
                item = json.loads(raw.decode("utf-8", errors="replace"))
                raw_url = item.get("url") or item.get("webpage_url") or item.get("id", "")
                url = _normalise_yt_url(raw_url)
                if url:
                    entries.append({"url": url, "title": item.get("title") or "", "replaced": False,
                                    "uploader": item.get("uploader") or item.get("channel") or "",
                                    "duration": item.get("duration") or 0})
            except Exception:
                pass
        await proc.wait()
    except Exception:
        pass

    if not entries:
        return entries

    # Eintraege mit Video-Stichwort: Studio-Version von YouTube Music suchen.
    # Frueher per "ytmsearch", das es nicht gibt — ersetzt wurde nie etwas.
    sem = asyncio.Semaphore(3)

    async def replace(entry):
        title = entry["title"] or ""
        query = _song_query(title)
        if not query:
            return
        async with sem:
            song = _pick_song_version(entry, await _ytm_song_search(query))
        if song:
            entry["url"]      = song["url"]
            entry["title"]    = (f'{song["artist"]} - {song["title"]}' if song.get("artist") else song["title"]) or title
            entry["replaced"] = True

    await asyncio.gather(*(replace(e) for e in entries if _VIDEO_TITLE_RE.search(e["title"] or "")))
    return entries


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


async def _ytm_fill_details(songs: list[dict]) -> list[dict]:
    """Laenge, Kuenstler und Songtitel je Treffer nachladen (parallel)."""
    sem = asyncio.Semaphore(_YTM_DETAIL_PARALLEL)

    async def one(r):
        async with sem:
            try:
                pr = await asyncio.create_subprocess_exec(
                    *_yt("--skip-download", "-j", "--no-playlist", "--quiet", "--no-warnings", r["url"]),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=_NO_WINDOW)
                out, _ = await asyncio.wait_for(pr.communicate(), timeout=30)
                item = json.loads(out.decode("utf-8", errors="replace").strip().splitlines()[0])
            except Exception:
                return
            # YouTube Music fuehrt teils auch Komponisten als Kuenstler — hoechstens zwei zeigen
            names = item.get("artists") or [a.strip() for a in (item.get("artist") or "").split(",") if a.strip()]
            artist = ", ".join(names[:2]) or item.get("uploader") or ""
            r["title"]    = item.get("track") or item.get("title") or r["title"]
            r["artist"]   = artist
            r["uploader"] = artist
            r["duration"] = item.get("duration") or r.get("duration") or 0
            r["abr"]      = item.get("abr") or r.get("abr") or 0

    await asyncio.gather(*(one(r) for r in songs))
    return songs


async def _ytm_songs(query: str, n: int = 8, details: bool = True) -> list[dict]:
    """Studio-Versionen von YouTube Music. details=False ist schnell (~2 s),
    liefert aber nur Titel, Link und Vorschaubild."""
    songs = await _run_search_cmd(_yt(
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
    return url.lower().startswith("http") and not _is_spotify(url) and not _is_playlist(url)


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


def _library_matches(video: dict, limit: int = 5) -> list[dict]:
    """Bibliothekstitel, die derselbe Song sein duerften wie video
    ({title, artist, uploader, duration}). Beste zuerst."""
    a, song = _dupe_parts(video.get("title", ""), video.get("artist") or "")
    if len(song) < 2:
        return []
    words = set(song.split())
    version = words & _VERSION_WORDS
    vdur = video.get("duration") or 0
    found = []
    for lt in _state["library"]:
        if lt.get("missing") or not lt.get("path"):
            continue
        la, ls = _dupe_parts(lt.get("title", ""), lt.get("artist") or lt.get("album_artist") or "")
        if not ls or (set(ls.split()) & _VERSION_WORDS) != version:
            continue
        sm = SequenceMatcher(None, song, ls)
        if sm.real_quick_ratio() < _DL_DUPE_SONG_SIM or sm.ratio() < _DL_DUPE_SONG_SIM:
            continue
        ldur = lt.get("duration_sec") or 0
        if vdur and ldur and abs(vdur - ldur) > _DL_DUPE_MAX_DIFF:
            continue
        if a and la:
            if not (a in la or la in a or SequenceMatcher(None, a, la).ratio() >= _DL_DUPE_ARTIST_SIM):
                continue
        elif song != ls and not (vdur and ldur and abs(vdur - ldur) <= 3):
            continue        # ohne Kuenstler: gleicher Titel, sonst muss die Laenge passen
        found.append((sm.ratio(), -abs((vdur or 0) - ldur), lt))
    found.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [lt for _, _, lt in found[:limit]]


def _is_single_youtube_video(url: str) -> bool:
    u = url.lower()
    if not u.startswith("http") or _is_playlist(url):
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
            *_yt("--no-playlist", "--skip-download", "-j", "--quiet", "--no-warnings", url),
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


async def _ytm_song_search(query: str, n: int = 3) -> list[dict]:
    """Song-Suche von YouTube Music mit Laenge und Kuenstler (siehe _ytm_songs)."""
    return await _ytm_songs(query, n, details=True)


async def _check_video_then_download(url: str, fmt: str, ws: WebSocket, check_dupes: bool = True):
    """Vor dem Laden pruefen: Gibt es den Titel schon in der Bibliothek, wird
    gefragt (trotzdem laden oder zur Datei springen). Bei YouTube-Musikvideos
    wird die Studio-Version angeboten. Faellt die Pruefung aus (kein Netz,
    Zeitlimit), wird ohne Rueckfrage geladen wie bisher."""
    async def _send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    await _send("video_check_pending", url=url)
    matches: list[dict] = []
    try:
        video = await _probe_video(url)
        song = None
        if video and check_dupes:
            matches = _library_matches(video)
        if video and not matches and _is_single_youtube_video(url) \
                and "- topic" not in video["uploader"].lower() \
                and (not _AUDIO_TITLE_RE.search(video["title"]) or _MV_TITLE_RE.search(video["title"])):
            query = _song_query(video["title"])
            if query:
                song = _pick_song_version(video, await _ytm_song_search(query))
    finally:
        await _send("video_check_done", url=url)

    if matches:
        await _send("dupe_choice", url=url, format=fmt,
                    video={k: video.get(k, "") for k in ("title", "artist", "uploader", "duration")},
                    matches=[{k: m.get(k) for k in ("path", "title", "artist", "folder", "duration_sec",
                                                     "bitrate_kbps", "cutoff_khz")} for m in matches])
        return
    if video and _needs_video_choice(video, song):
        await _send("video_choice", url=url, format=fmt,
                    video={k: video[k] for k in ("title", "uploader", "duration")},
                    song={"url": song["url"], "title": song["title"],
                          "uploader": song.get("artist") or song["uploader"], "duration": song["duration"]})
        return
    asyncio.create_task(run_download(url, fmt))


async def _songs_and_videos(query: str, n_songs: int, n_videos: int):
    """Beide Suchen parallel: Studio-Versionen (ohne Details) und YouTube."""
    base_args = ["--flat-playlist", "-j", "--no-playlist", "--quiet", "--no-warnings"]
    songs, videos = await asyncio.gather(
        _ytm_songs(query, n_songs, details=False),
        _run_search_cmd(_yt(f"ytsearch{n_videos}:{query}", *base_args)))
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
    if not songs:
        await send(videos, True)
        return
    # Erst zeigen, dann Laenge und Kuenstler der Songs nachliefern
    await send(_merge_songs_first(songs, videos), False)
    await _ytm_fill_details(songs)
    songs = [r for r in songs if not _is_unwanted_result(r) and not LIVE_RE.search(r.get("title", ""))]
    await send(_merge_songs_first(songs, videos), True)

async def run_spotify_download(url: str, fmt_id: str = "mp3-best"):
    """Download a Spotify track/album/playlist via spotdl."""
    global _dl_counter
    _dl_counter += 1
    session_id = _dl_counter

    hdr: dict = {
        "id":            session_id,
        "session":       session_id,
        "session_label": "Spotify",
        "title":         "",
        "url":           url,
        "fmt":           fmt_id,
        "path":          None,
        "track_n":       0,
        "track_total":   0,
        "progress":      0,
        "status":        "active",
        "status_text":   "Verbinde mit Spotify…",
        "error_msg":     "",
    }
    _state["downloads"].insert(0, hdr)
    await push_downloads(force=True)

    loop = asyncio.get_running_loop()
    spotdl_cmd = await loop.run_in_executor(None, _find_spotdl_cmd)

    if not spotdl_cmd:
        hdr["status"]      = "error"
        hdr["status_text"] = "spotdl nicht gefunden"
        hdr["error_msg"]   = "spotdl ist nicht installiert"
        # Sagt dem Frontend, welcher Einstellungen-Tab das Problem loest
        hdr["fix_tab"]     = "download"
        await push_downloads(force=True)
        return

    out_dir = _state.get("download_dir", str(BASE_DIR / "Downloads"))
    os.makedirs(out_dir, exist_ok=True)

    fmt_map  = {"mp3-best": "mp3", "flac": "flac", "wav": "wav", "m4a": "m4a", "opus": "opus"}
    sdl_fmt  = fmt_map.get(fmt_id, "mp3")
    out_tmpl = str(Path(out_dir) / "{artist} - {title}.{output-ext}")

    cmd = spotdl_cmd + [
        "download", url,
        "--output",  out_tmpl,
        "--format",  sdl_fmt,
        # Der Ton kommt auch bei spotdl von YouTube Music (~130-160 kbps).
        # 320k CBR blaehte die Datei nur auf; V0 wie bei yt-dlp haelt den
        # Verlust beim Umwandeln genauso klein.
        "--bitrate", "0" if sdl_fmt == "mp3" else "auto",
        "--print-errors",
    ]
    # Die spotdl-Standalone-Exe hat kein eigenes ffmpeg — auf das gebundelte zeigen
    if FFMPEG and FFMPEG != "ffmpeg" and os.path.exists(FFMPEG):
        cmd += ["--ffmpeg", FFMPEG]
    cid  = _state.get("spotify_client_id",     "").strip()
    csec = _state.get("spotify_client_secret",  "").strip()
    if cid and csec:
        cmd += ["--client-id", cid, "--client-secret", csec]

    track_total = 0
    track_done  = 0
    error_lines: list[str] = []   # collect error/warning lines for display

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_NO_WINDOW)
        _dl_procs[session_id] = proc

        async def _read_stderr():
            async for raw in proc.stderr:
                ln = raw.decode('utf-8', errors='replace').strip()
                if ln:
                    error_lines.append(ln)
                    print('[spotdl err] ' + ln, flush=True)
        asyncio.create_task(_read_stderr())

        async for raw in proc.stdout:
            if hdr not in _state['downloads']:
                try: proc.kill()
                except Exception: pass
                break
            line = raw.decode('utf-8', errors='replace').strip()
            if not line:
                continue
            print('[spotdl] ' + line, flush=True)
            if re.search(r'error|failed|rate.?limit|unauthorized|invalid', line, re.IGNORECASE):
                error_lines.append(line)
            m = re.search(r'Found\s+(\d+)\s+songs?', line, re.IGNORECASE)
            if m:
                track_total = int(m.group(1))
                hdr['track_total'] = track_total
                hdr['status_text'] = str(track_total) + ' Titel gefunden…'
                await push_downloads()
                continue
            m = re.search(r'(?:Downloaded|Skipping)\s+[““”]?([^”“”\n]+?)[““”]?(?:\s+to\s+|$)', line, re.IGNORECASE)
            if m:
                track_done += 1
                title = m.group(1).strip()
                hdr['track_n']     = track_done
                hdr['title']       = title[:80]
                hdr['status_text'] = title[:60]
                hdr['progress']    = (track_done / track_total * 100) if track_total else 50
                await push_downloads()
                continue
            m = re.search(r'Downloading\s+(\d+)\s+songs?', line, re.IGNORECASE)
            if m and not track_total:
                track_total = int(m.group(1))
                hdr['track_total'] = track_total
                await push_downloads()

        await proc.wait()
        _dl_procs.pop(session_id, None)

        if hdr in _state['downloads']:
            if proc.returncode == 0 or track_done > 0:
                hdr['status']      = 'done'
                hdr['status_text'] = '✓ ' + str(track_done) + ' Titel'
                hdr['progress']    = 100
                asyncio.create_task(scan_folder(out_dir))
            else:
                err_msg = 'spotdl Fehler'
                for ln in reversed(error_lines):
                    clean = re.sub(r'\x1b\[[0-9;]*m', '', ln).strip()
                    if clean and len(clean) < 120:
                        err_msg = clean
                        break
                hdr['status']      = 'error'
                hdr['status_text'] = err_msg
            await push_downloads(force=True)
    except Exception as exc:
        _dl_procs.pop(session_id, None)
        if hdr in _state["downloads"]:
            hdr["status"]      = "error"
            hdr["status_text"] = f"Fehler: {exc}"
            await push_downloads(force=True)


async def run_download(url: str, fmt_id: str = "mp3-best") -> str | None:
    """Returns the final output path on success, None on failure."""
    global _dl_counter
    out_dir = _state.get("download_dir", str(BASE_DIR / "Downloads"))
    os.makedirs(out_dir, exist_ok=True)

    # ── session label + playlist folder name ─────────────────────────────────
    playlist_folder: str | None = None
    if url.startswith("http") and _is_playlist(url):
        try:
            pr = await asyncio.create_subprocess_exec(
                *_yt('--print', 'playlist_title', '--playlist-items', '1',
                     '--no-warnings', '--quiet', '--encoding', 'utf-8', url),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                creationflags=_NO_WINDOW)
            out, _ = await asyncio.wait_for(pr.communicate(), timeout=15)
            raw_title = out.decode(errors='replace').strip().splitlines()[0] if out else ''
            if raw_title and raw_title not in ('NA', 'N/A', ''):
                playlist_folder = re.sub(r'[<>:"/\\|?*]', '_', raw_title)[:80]
                slabel = raw_title[:60]
            else:
                m = re.search(r'list=([^&]+)', url)
                slabel = f"Playlist · {m.group(1)[:28]}" if m else url[:60]
        except Exception:
            m = re.search(r'list=([^&]+)', url)
            slabel = f"Playlist · {m.group(1)[:28]}" if m else url[:60]
    elif url.startswith("http"):
        slabel = url[:60]
    else:
        slabel = url[:60]

    # ── session header item  (id == session_id identifies it as header) ───────
    _dl_counter += 1
    session_id = _dl_counter

    # For single-track HTTP URLs, title starts empty and gets filled from filename
    hdr_title = slabel if not (url.startswith("http") and not _is_playlist(url)) else ""
    hdr: dict = {
        "id":            session_id,
        "session":       session_id,
        "session_label": slabel,
        "title":         hdr_title,
        "url":           url,
        "fmt":           fmt_id,
        "path":          None,
        "track_n":       0,
        "track_total":   0,
        "progress":      0,
        "status":        "active",
        "status_text":   "Starte…",
        "error_msg":     "",
    }
    _state["downloads"].insert(0, hdr)      # always at top of list
    await push_downloads(force=True)

    # ── per-track items for playlists ─────────────────────────────────────────
    def _new_track(title: str) -> dict:
        global _dl_counter
        # Guard: if session was stopped, hdr is no longer in _state["downloads"]
        if hdr not in _state["downloads"]:
            return hdr  # dummy — modifications won't affect visible state
        _dl_counter += 1
        t = {"id": _dl_counter, "session": session_id, "session_label": slabel,
             "title": title[:80], "path": None,
             "progress": 0, "status": "active", "status_text": "…"}
        _state["downloads"].append(t)
        return t

    def _done(item: dict, ok: bool = True, skipped: bool = False):
        item["progress"]    = 100
        item["status"]      = "done"
        item["status_text"] = "✓ Vorhanden" if skipped else "✓ Fertig" if ok else "✗ Fehler"
        # Auto-add to library when a new file is downloaded
        if ok and not skipped and item.get("path") and os.path.exists(item["path"]):
            asyncio.create_task(_auto_add_to_library(item["path"]))

    track_n     = 0
    track_total = 0
    last_pct    = -1.0
    cur         = hdr   # points to the current track item (or header for singles)
    err_lines: list[str] = []   # ERROR-Zeilen von yt-dlp, fuer die Fehlermeldung

    # Build URL→history lookup once for O(1) smart-skip (newest entry wins)
    _hist_by_url = {h["url"]: h for h in reversed(_state.get("history", []))}

    # Smart-skip: only for single tracks (not playlists — playlists may have new entries)
    if not (url.startswith("http") and _is_playlist(url)):
        h = _hist_by_url.get(url)
        if h and h.get("bitrate_kbps", 0) >= 192:
            p = h.get("path", "")
            if p and os.path.exists(p):
                hdr["status"]      = "done"
                hdr["status_text"] = f"⏭ Bereits vorhanden ({h['bitrate_kbps']} kbps)"
                hdr["progress"]    = 100
                hdr["path"]        = p
                hdr["title"]       = h.get("title") or Path(p).stem
                await push_downloads(force=True)
                await _auto_add_to_library(p)
                return p  # return path so callers (e.g. automix) can still queue it

    # ── Playlist: flat-list once, replace video entries, then download per-URL ─
    # Using per-URL mode always avoids a second internal flat-list by yt-dlp.
    _audited_entries: list[dict] | None = None
    if url.startswith("http") and _is_playlist(url):
        hdr["status_text"] = "Playlist analysieren…"
        await push_downloads(force=True)
        entries = await _audit_playlist_for_videos(url)
        if entries:
            replaced_count = sum(1 for e in entries if e["replaced"])
            _audited_entries = entries  # always use per-URL mode → one flat-list total
            hdr["status_text"] = f"Starte… ({replaced_count} Videos ersetzt)" if replaced_count else "Starte…"
        else:
            hdr["status_text"] = "Starte…"
        await push_downloads(force=True)

    # ── Per-URL mode (when videos were replaced in playlist) ─────────────────
    if _audited_entries is not None:
        track_total = len(_audited_entries)
        hdr["track_total"] = track_total
        final_path = None
        for i, entry in enumerate(_audited_entries):
            if hdr not in _state["downloads"]:
                break
            track_n = i + 1
            hdr["track_n"]     = track_n
            hdr["status_text"] = f"{track_n} / {track_total}"
            hdr["progress"]    = round((track_n - 1) / track_total * 100)
            cur = _new_track(entry["title"])
            if entry["replaced"]:
                cur["status_text"] = "Audio-Version"
            await push_downloads(force=True)

            # Smart-skip per-entry (O(1) via pre-built dict)
            entry_path = None
            h = _hist_by_url.get(entry["url"])
            if h and h.get("bitrate_kbps", 0) >= 192:
                p = h.get("path", "")
                if p and os.path.exists(p):
                    cur["path"]  = p
                    cur["title"] = h.get("title") or Path(p).stem
                    _done(cur, skipped=True)
                    entry_path = p

            if entry_path is None:
                _env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
                ep = await asyncio.create_subprocess_exec(
                    *_ytdlp_cmd(entry["url"], fmt_id, out_dir, playlist_folder, force_folder=True),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    env=_env, creationflags=_NO_WINDOW)
                _dl_procs[session_id] = ep
                last_pct = -1.0
                async for raw in ep.stdout:
                    try:
                        line = raw.decode('utf-8', errors='replace').strip()
                        if not line: continue
                        if "[download] Destination:" in line:
                            fname = line.split("Destination:", 1)[1].strip()
                            cur["title"] = Path(fname).stem[:80]; cur["path"] = fname
                        elif "[ExtractAudio] Destination:" in line:
                            fname = line.split("Destination:", 1)[1].strip()
                            cur["path"] = fname; cur["title"] = Path(fname).stem[:80]
                            entry_path = fname
                        elif "has already been downloaded" in line:
                            fname = line.split("] ", 1)[-1].split(" has")[0].strip()
                            cur["title"] = Path(fname).stem[:80]; cur["path"] = fname
                            entry_path = fname
                            _done(cur, skipped=True)
                        elif "[download]" in line and "%" in line:
                            pct = float(line.split("%")[0].split()[-1])
                            if abs(pct - last_pct) >= 2.0:
                                last_pct = pct; cur["progress"] = pct
                                sp = re.search(r'at\s+([\d.]+\s*\w+/s)', line)
                                et = re.search(r'ETA\s+(\d+:\d+)', line)
                                spd = sp.group(1).strip() if sp else ''
                                eta = et.group(1) if et else ''
                                if spd and eta:
                                    cur["status_text"] = f"{pct:.0f}% · {spd} · ETA {eta}"
                                elif eta:
                                    cur["status_text"] = f"{pct:.0f}% · ETA {eta}"
                                else:
                                    cur["status_text"] = f"{pct:.0f}%"
                                await push_downloads()
                    except Exception:
                        pass
                await ep.wait()
                if cur["status"] != "done":
                    ok = ep.returncode == 0 and bool(cur.get("path"))
                    _done(cur, ok=ok)

                # Record freshly-downloaded entries in history right away so they
                # show up immediately, not just at the very end of the batch
                if entry_path and os.path.exists(entry_path):
                    probe = await asyncio.get_running_loop().run_in_executor(None, _probe_sync, entry_path)
                    _append_history(entry["url"], cur.get("title", ""), entry_path,
                                     probe.get("bitrate_kbps", 0))
                    await broadcast({"type": "history", "items": _state["history"][:100]})

            if entry_path:
                final_path = entry_path
            hdr["progress"] = round(track_n / track_total * 100)
            await push_downloads(force=True)

        hdr["status"] = "done"; hdr["progress"] = 100
        hdr["status_text"] = f"✓ {track_total} Tracks"
        _dl_procs.pop(session_id, None)
        await push_downloads(force=True)
        return final_path

    try:
        _env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        proc = await asyncio.create_subprocess_exec(
            *_ytdlp_cmd(url, fmt_id, out_dir, playlist_folder),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_env, creationflags=_NO_WINDOW)
        _dl_procs[session_id] = proc

        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            try:
                line = raw.decode('utf-8', errors='replace').strip()
                if not line:
                    continue

                # ── [download] Downloading playlist: Name ────────────────────
                if line.startswith("[download] Downloading playlist:"):
                    pl_title = line.split("playlist:", 1)[1].strip()
                    if pl_title:
                        slabel = pl_title[:60]          # update closure so _new_track() picks it up
                        hdr["session_label"] = slabel
                        hdr["title"]         = slabel
                    await push_downloads(force=True)

                # ── [download] Downloading item 3 of 231 ─────────────────────
                elif line.startswith("[download] Downloading item"):
                    try:
                        p = line.split("item")[1].split("of")
                        track_n     = int(p[0].strip())
                        track_total = int(p[1].strip())
                    except Exception:
                        pass

                    if cur is not hdr:
                        _done(cur)   # finish previous track

                    hdr["track_n"]     = track_n
                    hdr["track_total"] = track_total
                    hdr["status_text"] = f"{track_n} / {track_total}"
                    hdr["progress"]    = round((track_n - 1) / track_total * 100) if track_total else 0

                    cur      = _new_track(f"Track {track_n}")
                    last_pct = -1.0
                    await push_downloads(force=True)

                # ── file already exists ───────────────────────────────────────
                elif "has already been downloaded" in line:
                    fname        = line.split("] ", 1)[-1].split(" has")[0].strip()
                    cur["title"] = Path(fname).stem[:80]
                    cur["path"]  = fname
                    _done(cur, skipped=True)
                    done_n = sum(1 for d in _state["downloads"]
                                 if d["session"] == session_id
                                 and d["id"] != session_id
                                 and d["status"] == "done")
                    if track_total:
                        hdr["progress"] = round(done_n / track_total * 100)
                    await push_downloads(force=True)

                # ── destination file ──────────────────────────────────────────
                elif "[download] Destination:" in line:
                    fname        = line.split("Destination:", 1)[1].strip()
                    cur["title"] = Path(fname).stem[:80]
                    cur["path"]  = fname
                    if cur is hdr:
                        hdr["title"] = cur["title"]
                    await push_downloads()

                # ── extracted audio path ──────────────────────────────────────
                elif "[ExtractAudio] Destination:" in line:
                    fname        = line.split("Destination:", 1)[1].strip()
                    cur["path"]  = fname
                    cur["title"] = Path(fname).stem[:80]
                    if cur is hdr:
                        hdr["path"]  = fname
                        hdr["title"] = cur["title"]
                    await push_downloads(force=True)

                # ── progress % ────────────────────────────────────────────────
                elif "[download]" in line and "%" in line:
                    pct = float(line.split("%")[0].split()[-1])
                    if abs(pct - last_pct) >= 1.0:
                        last_pct           = pct
                        cur["progress"]    = pct
                        sp = re.search(r'at\s+([\d.]+\s*\w+/s)', line)
                        et = re.search(r'ETA\s+(\d+:\d+)', line)
                        spd = sp.group(1).strip() if sp else ''
                        eta = et.group(1) if et else ''
                        if spd and eta:
                            cur["status_text"] = f"{pct:.0f}% · {spd} · ETA {eta}"
                        elif eta:
                            cur["status_text"] = f"{pct:.0f}% · ETA {eta}"
                        else:
                            cur["status_text"] = f"{pct:.0f}%"
                        done_n = sum(1 for d in _state["downloads"]
                                     if d["session"] == session_id
                                     and d["id"] != session_id
                                     and d["status"] == "done")
                        base  = done_n / track_total if track_total else 0
                        bonus = pct / 100 / track_total if track_total else pct / 100
                        hdr["progress"] = min(99, round((base + bonus) * 100))
                        await push_downloads()   # throttled to 200 ms

                # ── Fehlermeldung merken ──────────────────────────────────────
                elif line.startswith("ERROR:"):
                    err_lines.append(line[6:].strip()[:120])

            except Exception:
                pass

        await proc.wait()

        # Erfolg heisst: es ist wirklich eine Datei entstanden. yt-dlp beendet
        # sich wegen --ignore-errors auch dann mit Code 1, wenn gar nichts
        # geladen wurde — das galt frueher als Erfolg, wodurch fehlgeschlagene
        # Downloads als "Fertig" in der Liste standen.
        def _has_file(it: dict) -> bool:
            fp = it.get("path")
            return bool(fp) and os.path.exists(fp)

        ok = any(_has_file(d) for d in _state["downloads"]
                 if d.get("session") == session_id)

        # finish current track
        if cur is not hdr:
            _done(cur, ok=ok)

        # finish header
        hdr["progress"]    = 100 if ok else hdr["progress"]
        hdr["status"]      = "done" if ok else "error"
        partial = ok and proc.returncode != 0
        if ok and track_total:
            hdr["status_text"] = (f"✓ {track_total} Tracks · einige übersprungen"
                                  if partial else f"✓ {track_total} Tracks")
        elif ok:
            hdr["status_text"] = "✓ Fertig"
        else:
            hdr["status_text"] = "✗ Fehler"
        # Nur bei Playlists melden, dass etwas uebersprungen wurde. Bei einem
        # einzelnen Titel, der als Datei vorliegt, waere eine Fehlerzeile
        # daneben nur verwirrend — heruntergeladen ist heruntergeladen.
        if partial and err_lines and track_total:
            hdr["error_msg"] = err_lines[-1]
        if not ok:
            hdr["error_msg"] = err_lines[-1] if err_lines else "Download fehlgeschlagen"

    except Exception as e:
        hdr["status"]      = "error"
        hdr["status_text"] = str(e)[:60]
        hdr["error_msg"]   = str(e)[:120]

    finally:
        _dl_procs.pop(session_id, None)

    # Record in download history
    if hdr.get("path") and os.path.exists(hdr["path"]):
        loop = asyncio.get_running_loop()
        probe = await loop.run_in_executor(None, _probe_sync, hdr["path"])
        _append_history(url, hdr.get("title", ""), hdr["path"], probe.get("bitrate_kbps", 0))
        await broadcast({"type": "history", "items": _state["history"][:100]})
        # Einzelne Downloads durchlaufen kein _done() — ohne das hier fehlten
        # sie in der Bibliothek, bis irgendwann ein Scan lief.
        await _auto_add_to_library(hdr["path"])

    await push_downloads(force=True)
    return hdr.get("path") if hdr.get("path") and os.path.exists(hdr.get("path", "")) else None

# ── WebSocket endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            data = await ws.receive_text()
            msg  = json.loads(data)
            # Isolate per-message failures: a bug in one handler must not tear
            # down the whole connection (which would break all playback).
            try:
                await handle_message(ws, msg)
            except Exception:
                import traceback
                print(f"[handler error] type={msg.get('type')!r}", file=sys.stderr)
                traceback.print_exc()
    except WebSocketDisconnect:
        clients.discard(ws)
    except Exception:
        clients.discard(ws)

# ── Remote Control Server ──────────────────────────────────────────────────────
import socket as _socket
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse

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
        save_settings()
    return _state["remote_key"]

def _remote_key_ok(k: str | None) -> bool:
    import secrets
    return bool(k) and secrets.compare_digest(str(k), _remote_key())

def _remote_urls(ip: str) -> dict:
    base = f"http://{ip}:{_remote_port}"
    return {"url": f"{base}/?k={_remote_key()}", "wish_url": f"{base}/wunsch"}

_REMOTE_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>SynthiMIX Remote</title>
<link rel="manifest" href="/manifest.json?k=__KEY__">
<meta name="theme-color" content="#0d1625">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="SynthiMIX">
<style>
/* Grundsystem von SynthiMIX (App.svelte / lib/ui.css) als eigene Variablen —
   die Seite laeuft ohne die App. Werte hier mitziehen, wenn sich die App aendert.
   Handy: Schrift mindestens 13px, Klickziele mindestens 44px. */
:root{
  --bg:#0a0e18;--bg2:#080c16;--surf:#0e1624;--hover:#101828;--sel:#0e1c38;
  --br1:#121e30;--br2:#1e2e44;--br3:#2a3e5c;
  --tx1:#ecf2ff;--tx2:#d4e0f2;--tx3:#b0c6dc;--tx4:#9ab2cc;--tx5:#8ba6c4;
  --accent:#e07800;--accent2:#ff9020;--accent-tx:#ff9a33;--on-accent:#0a0e18;--act-bg:#1a1206;
  --green-tx:#6fcf7c;--green-bg:#0e1a10;--green-br:#2a6a30;
  --red-tx:#ff7b7b;--red-bg:#1a0808;--red-br:#8a3030;
  --blue-tx:#7fb0ec;--blue-bg:#0e1a2c;--blue-br:#2a5888;
  --r-s:4px;--r-m:8px;--r-l:12px;
  --fs-sm:13px;--fs-body:15px;--fs-lg:17px;--fs-h:20px;
  --tap:44px;
  color-scheme:dark;
}
/* Handy auf hell gestellt: helles Theme der App — draussen in der Sonne lesbarer */
@media (prefers-color-scheme: light){
  :root{
    --bg:#f4f0eb;--bg2:#ebe7e1;--surf:#ffffff;--hover:#e0dcd6;--sel:#ddeeff;
    --br1:#d6d0c8;--br2:#bdb6ad;--br3:#a39b91;
    --tx1:#0a0806;--tx2:#1e1a14;--tx3:#3a342a;--tx4:#4a443c;--tx5:#554e47;
    --accent:#b35400;--accent2:#c86000;--accent-tx:#9a4700;--on-accent:#ffffff;--act-bg:#fff1dc;
    --green-tx:#1a7030;--green-bg:#eaf6ec;--green-br:#8ac098;
    --red-tx:#b82020;--red-bg:#fff0f0;--red-br:#d89a9a;
    --blue-tx:#1a5cb0;--blue-bg:#e8f0fb;--blue-br:#93b6e0;
    color-scheme:light;
  }
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--tx2);font:var(--fs-body)/1.4 'Segoe UI',system-ui,-apple-system,sans-serif;
  padding-bottom:calc(172px + env(safe-area-inset-bottom,0px))}
button{font:inherit;color:inherit}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent-tx);outline-offset:2px}
.ico{width:22px;height:22px;flex-shrink:0}

/* Kopf mit Verbindungsstatus als Text */
.hdr{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:10px;
  padding:calc(10px + env(safe-area-inset-top,0px)) 16px 10px;background:var(--bg2);border-bottom:1px solid var(--br1)}
.logo{font-weight:700;font-size:var(--fs-lg);color:var(--accent-tx)}.logo span{color:var(--blue-tx)}
.sub{font-size:var(--fs-sm);color:var(--tx3)}
.conn{margin-left:auto;display:inline-flex;align-items:center;gap:6px;font-size:var(--fs-sm);font-weight:700;
  padding:4px 10px;border-radius:999px;color:var(--red-tx);background:var(--red-bg);border:1px solid var(--red-br)}
.conn::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.conn.on{color:var(--green-tx);background:var(--green-bg);border-color:var(--green-br)}

/* Laufender Titel */
.np{padding:16px 16px 12px;text-align:center;border-bottom:1px solid var(--br1)}
.cov{display:none;width:148px;height:148px;object-fit:cover;border-radius:var(--r-l);margin:0 auto 12px;box-shadow:0 8px 24px rgba(0,0,0,.35)}
.cov.on{display:block}
.np-t{font-size:var(--fs-h);font-weight:700;color:var(--tx1);line-height:1.25;word-break:break-word}
.np-a{font-size:var(--fs-body);color:var(--tx3);min-height:20px;margin-top:4px}
.pb-hit{padding:14px 0;margin:6px 0 -6px;cursor:pointer}
.pb-wrap{background:var(--br2);border-radius:3px;height:6px;overflow:hidden}
.pb-fill{background:var(--accent);height:100%;width:0%;transition:width .9s linear}
.pb-row{display:flex;justify-content:space-between;font-size:var(--fs-sm);color:var(--tx3);font-variant-numeric:tabular-nums}
.np-nx{font-size:var(--fs-sm);color:var(--tx3);margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* Feste Bedienleiste unten — im Daumenbereich */
.dock{position:fixed;left:0;right:0;bottom:0;z-index:60;background:var(--bg2);border-top:1px solid var(--br2);
  padding:10px 16px calc(12px + env(safe-area-inset-bottom,0px));box-shadow:0 -8px 24px rgba(0,0,0,.25)}
.vol-row{display:flex;align-items:center;gap:12px}
.vol-row label{font-size:var(--fs-sm);font-weight:700;color:var(--tx3);min-width:32px}
.vol-row input{flex:1}
.vol-val{font-size:var(--fs-body);font-weight:700;color:var(--tx1);min-width:44px;text-align:right;font-variant-numeric:tabular-nums}
.ctrls{display:flex;justify-content:center;align-items:center;gap:28px;margin-top:8px}
.tbtn{width:56px;height:56px;border-radius:50%;border:none;background:var(--surf);color:var(--tx1);
  display:flex;align-items:center;justify-content:center;cursor:pointer}
.tbtn .ico{width:26px;height:26px}
.tbtn.big{width:68px;height:68px;background:var(--accent);color:var(--on-accent)}
.tbtn.big .ico{width:32px;height:32px}
.tbtn:active{transform:scale(.95)}
input[type=range]{height:var(--tap);accent-color:var(--accent);width:100%}

/* Abschnitte */
.sec{padding:16px 16px 8px;border-top:1px solid var(--br1)}
.sec-h{display:flex;align-items:center;gap:8px;font-size:var(--fs-sm);font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--tx3);margin-bottom:10px}
.sec-h .cnt,.wc{font-size:var(--fs-sm);letter-spacing:0;font-weight:700;padding:1px 8px;border-radius:999px;
  background:var(--surf);border:1px solid var(--br2);color:var(--tx2);font-variant-numeric:tabular-nums}
.wc{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.wc:empty{display:none}

/* Schalter-Knoepfe (Normalisierung, Auto-Mix, Radio) */
.norm-wrap{display:grid;gap:10px}
.norm-head{display:flex;align-items:center;gap:10px}
.norm-btn{flex:1;min-height:var(--tap);border-radius:var(--r-m);border:1px solid var(--br3);background:var(--surf);
  color:var(--tx2);font-size:var(--fs-body);font-weight:600;cursor:pointer;padding:0 12px}
.norm-btn.on{background:var(--act-bg);border-color:var(--accent);color:var(--accent-tx)}
.norm-val{font-size:var(--fs-body);font-weight:700;color:var(--tx1);min-width:76px;text-align:right;font-variant-numeric:tabular-nums}
.tg-row{display:flex;gap:10px}

/* Warteschlange */
.q-wrap{max-height:52vh;overflow-y:auto;-webkit-overflow-scrolling:touch;border-radius:var(--r-m);border:1px solid var(--br1)}
.qi{display:flex;align-items:center;gap:6px;min-height:56px;padding:6px 8px 6px 2px;border-bottom:1px solid var(--br1);user-select:none;background:var(--bg)}
.qi.cur{background:var(--act-bg);box-shadow:inset 4px 0 0 var(--accent)}
.dh{color:var(--tx4);font-size:22px;width:40px;height:var(--tap);display:flex;align-items:center;justify-content:center;flex-shrink:0;touch-action:none;cursor:grab}
.qi-info{flex:1;min-width:0;cursor:pointer}
.qi-t{font-size:var(--fs-body);color:var(--tx1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.qi-t.a{color:var(--accent-tx);font-weight:700}
.qi-t.p{color:var(--tx4)}
.qi-d{font-size:var(--fs-sm);color:var(--tx3);margin-top:2px;display:flex;align-items:center;flex-wrap:wrap;gap:4px;font-variant-numeric:tabular-nums}
.qi-eta{color:var(--tx4)}
.qa{display:flex;gap:8px;padding:8px 8px 12px 42px;border-bottom:1px solid var(--br1);background:var(--surf)}
.qa button{flex:1;min-height:var(--tap);border-radius:var(--r-m);border:1px solid var(--br3);background:var(--bg);
  color:var(--tx1);font-size:var(--fs-sm);font-weight:600;cursor:pointer;padding:0 6px}
.qa button.rm{color:var(--red-tx);border-color:var(--red-br);background:var(--red-bg)}

/* Tonart-Chips wie in der App: Zeichen + Farbe */
.kc{display:inline-block;padding:1px 7px;border-radius:var(--r-s);background:var(--surf);border:1px solid var(--br2);
  color:var(--tx2);font-size:var(--fs-sm);font-weight:600}
.kc.ok{color:var(--green-tx);border-color:var(--green-br);background:var(--green-bg)}
.kc.no{color:var(--red-tx);border-color:var(--red-br);background:var(--red-bg)}
.kc.est{font-style:italic}

/* Wuensche */
.wi{display:flex;align-items:center;gap:8px;min-height:60px;padding:8px 0;border-bottom:1px solid var(--br1)}
.wi-info{flex:1;min-width:0}
.wi-t{font-size:var(--fs-body);font-weight:600;color:var(--tx1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wi-s{font-size:var(--fs-sm);color:var(--tx3);margin-top:2px}
.wi-s.err{color:var(--red-tx)}

/* Aktionsknoepfe in Listen */
.nxt-b,.add-b,.dl-b,.wno{width:var(--tap);height:var(--tap);flex-shrink:0;border-radius:var(--r-m);cursor:pointer;
  display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;padding:0;
  border:1px solid var(--br3);background:var(--surf);color:var(--tx1)}
.add-b,.dl-b{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.wno{color:var(--red-tx);border-color:var(--red-br);background:var(--red-bg)}
.nxt-b:disabled,.add-b:disabled,.dl-b:disabled{opacity:.5}
.nxt-b:active,.add-b:active,.dl-b:active,.wno:active{transform:scale(.95)}

/* Playlisten */
.pl-list{display:flex;flex-wrap:wrap;gap:8px}
.pl-b{min-height:var(--tap);border-radius:var(--r-m);border:1px solid var(--br3);background:var(--surf);
  color:var(--tx1);font-size:var(--fs-sm);font-weight:600;padding:0 14px;cursor:pointer}
.pl-b:active{border-color:var(--accent);background:var(--act-bg)}

/* Suche */
.s-tabs{display:flex;gap:8px;margin-bottom:10px}
.s-tab{flex:1;min-height:var(--tap);border-radius:var(--r-m);border:1px solid var(--br3);background:var(--surf);
  color:var(--tx2);font-size:var(--fs-body);font-weight:600;cursor:pointer}
.s-tab.active{border-color:var(--accent);background:var(--act-bg);color:var(--accent-tx)}
.s-row{display:flex;gap:8px;margin-bottom:8px}
.inp{flex:1;min-height:48px;border-radius:var(--r-m);border:1px solid var(--br3);background:var(--surf);
  color:var(--tx1);font-size:16px;padding:0 14px}
.inp::placeholder{color:var(--tx4)}
.inp:focus{border-color:var(--accent);outline:none}
.ri{display:flex;align-items:center;gap:8px;min-height:60px;padding:8px 0;border-bottom:1px solid var(--br1)}
.ri-info{flex:1;min-width:0}
.ri-t{font-size:var(--fs-body);color:var(--tx1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ri-a,.ri-sub{font-size:var(--fs-sm);color:var(--tx3);margin-top:2px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.empty{padding:18px 4px;color:var(--tx3);font-size:var(--fs-sm);text-align:center}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <symbol id="i-prev" viewBox="0 0 24 24"><path fill="currentColor" d="M6 5h2v14H6zM20 5.5v13a1 1 0 0 1-1.5.87L9 13.1v-2.2l9.5-6.27A1 1 0 0 1 20 5.5z"/></symbol>
  <symbol id="i-next" viewBox="0 0 24 24"><path fill="currentColor" d="M16 5h2v14h-2zM4 5.5v13a1 1 0 0 0 1.5.87L15 13.1v-2.2L5.5 4.63A1 1 0 0 0 4 5.5z"/></symbol>
  <symbol id="i-play" viewBox="0 0 24 24"><path fill="currentColor" d="M7 4.8v14.4a1 1 0 0 0 1.53.85l11.2-7.2a1 1 0 0 0 0-1.7L8.53 3.95A1 1 0 0 0 7 4.8z"/></symbol>
  <symbol id="i-pause" viewBox="0 0 24 24"><rect x="6" y="4" width="4.5" height="16" rx="1" fill="currentColor"/><rect x="13.5" y="4" width="4.5" height="16" rx="1" fill="currentColor"/></symbol>
</svg>
<header class="hdr">
  <span class="logo">Synthi<span>MIX</span></span>
  <span class="sub">Fernbedienung</span>
  <span id="dot" class="conn" role="status">getrennt</span>
</header>
<section class="np">
  <img id="cov" class="cov" alt="">
  <div id="npT" class="np-t">&#8211;</div>
  <div id="npA" class="np-a"></div>
  <div class="pb-hit" onclick="seekAt(event)" title="Zum Spulen antippen">
    <div class="pb-wrap"><div id="pbf" class="pb-fill"></div></div>
  </div>
  <div class="pb-row"><span id="pbt">0:00</span><span id="pbr">&#8211;</span></div>
  <div id="npNx" class="np-nx"></div>
</section>
<section class="sec" id="wsec" style="display:none">
  <div class="sec-h">W&#252;nsche <span id="wcn" class="wc"></span></div>
  <div id="wl"></div>
</section>
<section class="sec">
  <div class="sec-h">Warteschlange <span id="qc" class="cnt">0</span></div>
  <div id="qw" class="q-wrap"><div id="ql"></div></div>
</section>
<section class="sec">
  <div class="sec-h">Mix</div>
  <div class="norm-wrap">
    <div class="norm-head">
      <button id="normb" class="norm-btn on" onclick="toggleNorm()">Normalisierung an</button>
      <span id="normv" class="norm-val">-10 LUFS</span>
    </div>
    <input type="range" id="normr" min="-23" max="-8" step="1" value="-10" oninput="onNorm(this.value)" onchange="flushNorm()" aria-label="Ziel-Lautst&#228;rke">
    <div class="tg-row">
      <button id="amb" class="norm-btn" onclick="toggleAM()">Auto-Mix</button>
      <button id="rdb" class="norm-btn" onclick="toggleRadio()">Radio</button>
    </div>
  </div>
</section>
<section class="sec">
  <div class="sec-h">Playlisten</div>
  <div id="pll" class="pl-list"><div class="empty">&#8230;</div></div>
</section>
<section class="sec" style="padding-bottom:24px">
  <div class="sec-h">Suche</div>
  <div class="s-tabs">
    <button id="tb-lib" class="s-tab active" onclick="setMode('lib')">Bibliothek</button>
    <button id="tb-yt" class="s-tab" onclick="setMode('yt')">YouTube</button>
  </div>
  <div class="s-row"><input class="inp" id="si" placeholder="Titel oder K&#252;nstler&#8230;" type="search" oninput="onS(this.value)" aria-label="Suchen"></div>
  <div id="sr"></div>
</section>
<nav class="dock" aria-label="Wiedergabe">
  <div class="vol-row">
    <label for="vr">Vol</label>
    <input type="range" id="vr" min="0" max="100" value="80" oninput="onVol(this.value)" onchange="flushVol()">
    <span id="vv" class="vol-val">80%</span>
  </div>
  <div class="ctrls">
    <button class="tbtn" onclick="send({type:'play_prev'})" aria-label="Zur&#252;ck"><svg class="ico"><use href="#i-prev"/></svg></button>
    <button id="pb" class="tbtn big" onclick="toggle()" aria-label="Abspielen"><svg class="ico"><use href="#i-play"/></svg></button>
    <button class="tbtn" onclick="send({type:'play_next'})" aria-label="Weiter"><svg class="ico"><use href="#i-next"/></svg></button>
  </div>
</nav>
<script>
var KEY='__KEY__'
var _lastCi=null,_openPath=null
var st={playing:false,current_idx:-1,volume:80,normalize_volume:true,target_lufs:-10,queue:[]},ws,_vt,_res=[],_ytRes=[],_srMode='lib'
var _rt=null,_wl=null
function conn(){
  if(ws&&(ws.readyState===0||ws.readyState===1))return
  clearTimeout(_rt);_rt=null
  ws=new WebSocket('ws://'+location.host+'/ws?k='+encodeURIComponent(KEY))
  ws.onopen=function(){dot(true);send({type:'remote_playlists'})}
  ws.onclose=function(){dot(false);clearTimeout(_rt);_rt=setTimeout(conn,2000)}
  ws.onerror=function(){ws.close()}
  ws.onmessage=function(e){
    var m=JSON.parse(e.data)
    if(m.type==='state'){st=m;render();updPos(m)}
    else if(m.type==='pos'){updPos(m)}
    else if(m.type==='search_results'){showRes(m.results||[])}
    else if(m.type==='yt_results'){showYtRes(m.results||[])}
    else if(m.type==='playlists'){showPls(m.items||[])}
    else if(m.type==='yt_dl_status'){updDl(m)}
  }
}
function setMode(m){
  _srMode=m
  document.getElementById('tb-lib').className='s-tab'+(m==='lib'?' active':'')
  document.getElementById('tb-yt').className='s-tab'+(m==='yt'?' active':'')
  document.getElementById('si').placeholder=m==='lib'?'Titel oder Künstler…':'YouTube suchen…'
  document.getElementById('sr').innerHTML=''
  document.getElementById('si').value=''
}
function dot(on){var d=document.getElementById('dot');d.className='conn'+(on?' on':'');d.textContent=on?'verbunden':'getrennt'}
function send(o){if(ws&&ws.readyState===1)ws.send(JSON.stringify(o))}
function toggle(){send({type:st.playing?'pause':'resume'})}
var _pv=null
function onVol(v){document.getElementById('vv').textContent=v+'%';_pv=+v;clearTimeout(_vt);_vt=setTimeout(flushVol,120)}
function flushVol(){if(_pv!=null){send({type:'set_volume',value:_pv});_pv=null}}
function updateNormUI(){var nb=document.getElementById('normb');var nr=document.getElementById('normr');if(nb){nb.textContent=st.normalize_volume?'Normalisierung an':'Normalisierung aus';nb.className='norm-btn'+(st.normalize_volume?' on':'')};if(nr)nr.style.opacity=st.normalize_volume?'1':'0.4'}
function toggleNorm(){st.normalize_volume=!st.normalize_volume;send({type:'set_normalize_volume',value:st.normalize_volume});updateNormUI()}
var _nv=null,_nt
function onNorm(v){document.getElementById('normv').textContent=v+' LUFS';_nv=+v;clearTimeout(_nt);_nt=setTimeout(flushNorm,200)}
function flushNorm(){if(_nv!=null){st.target_lufs=_nv;send({type:'set_normalize_volume',value:st.normalize_volume,target_lufs:_nv});_nv=null}}
function fmt(s){if(!s)return'';var m=Math.floor(s/60);return m+':'+(Math.floor(s%60)+'').padStart(2,'0')}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function rm(i){var t=(st.queue||[])[i];if(t)send({type:'queue_remove',index:i,path:t.path});_openPath=null}
function addLib(i){if(_res[i]){send({type:'queue_append',path:_res[i].path});document.getElementById('si').value='';document.getElementById('sr').innerHTML='';_res=[]}}
var _st=null
function onS(q){
  clearTimeout(_st)
  if(!q.trim()){document.getElementById('sr').innerHTML='';return}
  var delay=_srMode==='yt'?700:300
  _st=setTimeout(function(){
    if(_srMode==='lib'){send({type:'search_library',query:q})}
    else{document.getElementById('sr').innerHTML='<div class=empty>Suche läuft…</div>';send({type:'yt_search_remote',query:q})}
  },delay)
}
function showRes(rs){
  _res=rs
  var el=document.getElementById('sr')
  if(!rs.length){el.innerHTML='<div class=empty>Keine Ergebnisse</div>';return}
  el.innerHTML=rs.map(function(r,i){
    return'<div class=ri><div class=ri-info><div class=ri-t>'+esc(r.title||'&#8211;')+'</div><div class=ri-a>'+esc(r.artist||'')+(r.duration_sec?' &middot; '+fmt(r.duration_sec):'')+keyChip(r.key,r.key_src,r.bpm,-1)+'</div></div><button class=nxt-b onclick="addNext('+i+')" title="Als n&#228;chstes einreihen">&#9197;</button><button class=add-b onclick="addLib('+i+')" title="Ans Ende">+</button></div>'
  }).join('')
}
function addNext(i){if(_res[i]){send({type:'queue_insert_next',path:_res[i].path});document.getElementById('si').value='';document.getElementById('sr').innerHTML='';_res=[]}}
function showYtRes(rs){
  _ytRes=rs
  var el=document.getElementById('sr')
  if(!rs.length){el.innerHTML='<div class=empty>Keine Ergebnisse</div>';return}
  el.innerHTML=rs.map(function(r,i){
    return'<div class=ri id="ytr'+i+'"><div class=ri-info><div class=ri-t>'+esc(r.title||'&#8211;')+'</div><div class=ri-sub>'+esc(r.uploader||'')+(r.duration?' &middot; '+fmt(r.duration):'')+'</div></div><button class=nxt-b id="nlb'+i+'" onclick="dlYtNext('+i+')" title="Herunterladen &amp; als n&#228;chstes einreihen">&#9197;</button><button class=dl-b id="dlb'+i+'" onclick="dlYt('+i+')" title="Herunterladen &amp; ans Ende">+</button></div>'
  }).join('')
}
function dlYt(i){
  if(!_ytRes[i])return
  var b=document.getElementById('dlb'+i),n=document.getElementById('nlb'+i)
  if(b){b.disabled=true;b.textContent='⏳'}
  if(n)n.disabled=true
  send({type:'yt_dl_queue',url:_ytRes[i].url,title:_ytRes[i].title})
}
function dlYtNext(i){
  if(!_ytRes[i])return
  var b=document.getElementById('dlb'+i),n=document.getElementById('nlb'+i)
  if(n){n.disabled=true;n.textContent='⏳'}
  if(b)b.disabled=true
  send({type:'yt_dl_queue',url:_ytRes[i].url,title:_ytRes[i].title,as_next:true})
}
function updDl(m){
  for(var i=0;i<_ytRes.length;i++){
    if(_ytRes[i].title===m.title){
      var b=document.getElementById('dlb'+i),n=document.getElementById('nlb'+i)
      if(!b)break
      if(m.status==='done'){b.textContent='✓';b.style.color='#75d595';if(n){n.textContent='✓';n.style.color='#75d595'}}
      else{b.textContent='✕';b.style.color='#d57575';b.disabled=false;if(n){n.textContent='⏭';n.style.color='';n.disabled=false}}
      break
    }
  }
}
/* ── Touch drag-to-reorder ─────────────────────────────────────────────── */
var dg={on:false,idx:-1,ghost:null,gy0:0,gy1:0,dropAt:-1,scInt:null,timer:null}
function dhStart(e,i){
  e.preventDefault()
  var t=e.touches[0]
  dg.idx=i;dg.gy0=t.clientY;dg.on=false
  dg.timer=setTimeout(function(){dhAct(i)},160)
}
function dhAct(i){
  dg.on=true
  var rows=document.querySelectorAll('.qi'),src=rows[i];if(!src)return
  var rect=src.getBoundingClientRect()
  var g=document.createElement('div')
  g.style.cssText='position:fixed;left:0;right:0;top:'+rect.top+'px;height:'+rect.height+'px;background:#1e3050;border:1px solid #3b82f6;border-radius:4px;z-index:999;pointer-events:none;display:flex;align-items:center;padding:0 50px 0 14px;opacity:.92;font-size:13px;color:#c8d8f0;overflow:hidden'
  var info=src.querySelector('.qi-info');if(info)g.innerHTML=info.outerHTML
  document.body.appendChild(g)
  dg.ghost=g;dg.gy1=rect.top;dg.dropAt=i
  src.style.opacity='.2';showLine(i)
}
function dhMove(e){
  var t=e.touches[0]
  if(!dg.on){if(Math.abs(t.clientY-dg.gy0)>10){clearTimeout(dg.timer);dg.timer=null}return}
  e.preventDefault()
  var dy=t.clientY-dg.gy0
  if(dg.ghost)dg.ghost.style.top=(dg.gy1+dy)+'px'
  var rows=document.querySelectorAll('.qi'),drop=0
  for(var i=0;i<rows.length;i++){var r=rows[i].getBoundingClientRect();if(t.clientY>r.top+r.height/2)drop=i+1}
  if(drop!==dg.dropAt){dg.dropAt=drop;showLine(drop)}
  clearInterval(dg.scInt);dg.scInt=null
  var qw=document.getElementById('qw'),qr=qw.getBoundingClientRect()
  if(t.clientY<qr.top+65)dg.scInt=setInterval(function(){qw.scrollTop-=8},20)
  else if(t.clientY>qr.bottom-65)dg.scInt=setInterval(function(){qw.scrollTop+=8},20)
}
function dhEnd(e){
  clearTimeout(dg.timer);clearInterval(dg.scInt);dg.timer=null;dg.scInt=null
  if(!dg.on){dg.on=false;return}
  dg.on=false
  if(dg.ghost){dg.ghost.remove();dg.ghost=null}
  hideLine()
  var rows=document.querySelectorAll('.qi')
  for(var i=0;i<rows.length;i++)rows[i].style.opacity=''
  var from=dg.idx,to=dg.dropAt>from?dg.dropAt-1:dg.dropAt
  if(to>=0&&from!==to)send({type:'queue_move',from:from,to:to})
}
function showLine(i){
  var dl=document.getElementById('dl')
  if(!dl){dl=document.createElement('div');dl.id='dl';dl.style.cssText='position:fixed;left:0;right:0;height:3px;background:#3b82f6;z-index:1000;pointer-events:none';document.body.appendChild(dl)}
  var rows=document.querySelectorAll('.qi'),ref=rows[Math.min(i,rows.length-1)]
  if(!ref){dl.style.display='none';return}
  var r=ref.getBoundingClientRect()
  dl.style.top=(i<rows.length?r.top:r.bottom)+'px';dl.style.display='block'
}
function hideLine(){var d=document.getElementById('dl');if(d)d.style.display='none'}
function updPos(m){
  var pos=m.pos!=null?m.pos:(st.position_ms||0)
  var dur=m.dur!=null?m.dur:(st.duration_ms||0)
  var pct=dur>0?Math.min(100,pos/dur*100):0
  document.getElementById('pbf').style.width=pct+'%'
  document.getElementById('pbt').textContent=fmt(pos/1000)
  document.getElementById('pbr').textContent=dur>0?('-'+fmt((dur-pos)/1000)):'–'
}
/* ── Render ─────────────────────────────────────────────────────────────── */
function render(){
  var q=st.queue||[],ci=st.current_idx,cur=q[ci]
  setCover(cur?ci:-1)
  document.getElementById('npT').textContent=cur&&cur.title?cur.title:'–'
  document.getElementById('npA').textContent=cur&&cur.artist?cur.artist:''
  var nx=st.next_title?'↓ '+st.next_title+(st.next_artist?' · '+st.next_artist:''):''
  document.getElementById('npNx').textContent=nx
  var pb=document.getElementById('pb');pb.innerHTML='<svg class="ico"><use href="#i-'+(st.playing?'pause':'play')+'"/></svg>';pb.setAttribute('aria-label',st.playing?'Pause':'Abspielen')
  document.getElementById('vr').value=st.volume
  document.getElementById('vv').textContent=st.volume+'%'
  var nr=document.getElementById('normr')
  if(nr&&_nv==null){nr.value=st.target_lufs;document.getElementById('normv').textContent=st.target_lufs+' LUFS'}
  updateNormUI()
  document.getElementById('qc').textContent=q.length
  // compute ETA for upcoming tracks
  var posMs=st.position_ms||0,durMs=st.duration_ms||0
  var rem=durMs>0?Math.max(0,(durMs-posMs)/1000):0
  var etas={}
  for(var j=ci+1;j<q.length;j++){etas[j]=rem;rem+=q[j].duration_sec||0}
  document.getElementById('ql').innerHTML=q.length?q.map(function(t,i){
    var a=i===ci,p=t.played&&!a
    var etaAbs=etas[i]!=null?absTime(etas[i]):'';
    var row='<div class="qi'+(a?' cur':'')+'" data-i="'+i+'"><div class=dh ontouchstart="dhStart(event,'+i+')" ontouchmove="dhMove(event)" ontouchend="dhEnd(event)">☰</div><div class=qi-info onclick="toggleRow('+i+')"><div class="qi-t'+(a?' a':p?' p':'')+'">'+esc(t.title||'–')+'</div><div class=qi-d>'+fmt(t.duration_sec)+keyChip(t.key,t.key_src,t.bpm,t.compat)+(etaAbs?'<span class=qi-eta> · '+etaAbs+'</span>':'')+'</div></div></div>'
    if(_openPath&&t.path===_openPath){
      row+='<div class=qa>'
      if(!a)row+='<button onclick="mixNow('+i+')">&#8646; Jetzt mischen</button>'
      if(!a&&i!==ci+1)row+='<button onclick="asNext('+i+')">&#9197; Als n&#228;chstes</button>'
      if(!a)row+='<button class=rm onclick="rm('+i+')">&#10005; Entfernen</button>'
      if(a)row+='<button onclick="toggleRow(-1)">L&#228;uft gerade &#8212; schlie&#223;en</button>'
      row+='</div>'
    }
    return row
  }).join(''):'<div class=empty>Warteschlange leer</div>'
  // Nur beim Titelwechsel zum laufenden Titel springen — vorher sprang die
  // Liste bei jeder Aenderung (Lautstaerke, Pause) zurueck, auch mitten beim Scrollen.
  if(ci>=0&&ci!==_lastCi){var cr=document.querySelector('#ql .qi[data-i="'+ci+'"]');if(cr)cr.scrollIntoView({behavior:'smooth',block:'nearest'})}
  _lastCi=ci
  setTog('amb',st.auto_mix,'Auto-Mix')
  setTog('rdb',st.radio_enabled,'Radio')
  renderWishes(st.wishes||[])
}
function setTog(id,on,lbl){var b=document.getElementById(id);if(b){b.className='norm-btn'+(on?' on':'');b.textContent=lbl+(on?' an':' aus')}}
function toggleAM(){send({type:'set_auto_mix',value:!st.auto_mix})}
function toggleRadio(){send({type:'set_radio',enabled:!st.radio_enabled})}
/* Tonart-Chip: gleiche Farben wie in der App (passt / passt nicht) */
function keyChip(k,src,bpm,compat){
  if(!k&&!bpm)return''
  var cls='kc'+(compat>=2?' ok':compat===0?' no':'')+(src==='analyse'?' est':'')
  var sym=compat>=2?'&#10003; ':compat===0?'&#9888; ':''
  return'<span class="'+cls+'">'+(k?sym+esc(k):'')+(k&&bpm?' &middot; ':'')+(bpm?bpm+' BPM':'')+'</span>'
}
function toggleRow(i){var t=(st.queue||[])[i];_openPath=(t&&_openPath!==t.path)?t.path:null;render()}
function mixNow(i){send({type:'play_at',index:i});_openPath=null}
function asNext(i){var ci=st.current_idx,to=i>ci?ci+1:ci;if(ci<0)to=0;send({type:'queue_move',from:i,to:to});_openPath=null}
/* ── Wuensche direkt am Handy ─────────────────────────────────────────── */
var WL={neu:'wartet',laedt:'l&#228;dt&#8230;',analysiert:'analysiert&#8230;',bereit:'bereit',fehler:'Fehler'}
function renderWishes(ws){
  var sec=document.getElementById('wsec')
  sec.style.display=ws.length?'':'none'
  document.getElementById('wcn').textContent=ws.length?ws.length:''
  var rang={bereit:0,analysiert:1,laedt:2,neu:3,fehler:4}
  ws=ws.slice().sort(function(a,b){return(rang[a.status]||9)-(rang[b.status]||9)||(b.count||1)-(a.count||1)})
  document.getElementById('wl').innerHTML=ws.map(function(w){
    var sub=(WL[w.status]||esc(w.status))+((w.count||1)>1?' &middot; '+w.count+'&#215; gew&#252;nscht':'')
    var h='<div class=wi><div class=wi-info><div class=wi-t>'+esc(w.title||'–')+'</div><div class="wi-s'+(w.status==='fehler'?' err':'')+'">'+sub+(w.error?' &middot; '+esc(w.error):'')+'</div></div>'
    if(w.status==='bereit'){
      h+='<button class=nxt-b onclick="wAcc('+w.id+',true)" title="Als n&#228;chstes">&#9197;</button>'
      h+='<button class=add-b onclick="wAcc('+w.id+',false)" title="Ans Ende">+</button>'
    }
    h+='<button class=wno onclick="wRej('+w.id+')" title="Ablehnen">&#10005;</button></div>'
    return h
  }).join('')
}
function wAcc(id,next){send({type:'wish_accept',id:id,as_next:next})}
function wRej(id){if(confirm('Wunsch ablehnen?'))send({type:'wish_reject',id:id})}
function seekAt(e){
  var box=e.currentTarget.getBoundingClientRect()
  var frac=Math.min(1,Math.max(0,(e.clientX-box.left)/box.width))
  var dur=st.duration_ms||0
  if(dur>0){send({type:'seek',position_ms:Math.round(frac*dur)});updPos({pos:frac*dur,dur:dur})}
}
function setCover(ci){
  var el=document.getElementById('cov')
  if(ci==null||ci<0){el.className='cov';el.removeAttribute('src');return}
  var want='/cover?i='+ci+'&k='+encodeURIComponent(KEY)
  if(el.getAttribute('src')===want)return
  el.onload=function(){el.className='cov on'}
  el.onerror=function(){el.className='cov';el.removeAttribute('src')}
  el.setAttribute('src',want)
}
function showPls(items){
  document.getElementById('pll').innerHTML=(items&&items.length)
    ?items.map(function(pl){
        return'<button class=pl-b onclick="loadPl(this)" data-p="'+esc(pl.path)+'">'+esc(pl.name||'?')+'</button>'
      }).join('')
    :'<div class=empty>Keine Playlisten</div>'
}
function loadPl(b){send({type:'remote_load_playlist',path:b.getAttribute('data-p')})}
function absTime(secs){var d=new Date(Date.now()+secs*1000);return'~'+('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)}

/* Sperrt das Handy den Bildschirm, stirbt die Verbindung. Ohne das hier
   merkt die Seite das erst beim naechsten Sendeversuch und haengt beim
   Zurueckkommen ein paar Sekunden auf "getrennt". */
document.addEventListener('visibilitychange',function(){
  if(document.visibilityState==='visible'){conn();lock()}
  else releaseLock()
})
window.addEventListener('online',conn)
window.addEventListener('pageshow',conn)

/* Beim Auflegen soll der Bildschirm nicht dauernd zugehen. */
function lock(){
  if(!navigator.wakeLock||_wl)return
  navigator.wakeLock.request('screen').then(function(w){
    _wl=w
    w.addEventListener('release',function(){_wl=null})
  }).catch(function(){})
}
function releaseLock(){if(_wl){try{_wl.release()}catch(e){}_wl=null}}

conn()
lock()
</script>
</body>
</html>"""

# ── Musikwuensche ────────────────────────────────────────────────────────────
# Gaeste wuenschen sich Titel ueber eine eigene Seite. Der Titel wird sofort
# heruntergeladen und analysiert, damit er beim Annehmen ohne Wartezeit
# spielbar ist. In die Warteschlange kommt er erst, wenn der DJ zustimmt.
_wish_counter = 0

def load_wishes():
    global _wish_counter
    try:
        if WISHES_FILE.exists():
            _state["wishes"] = json.loads(WISHES_FILE.read_text(encoding="utf-8"))
            _wish_counter = max((w.get("id", 0) for w in _state["wishes"]), default=0)
    except Exception as e:
        print(f"[wishes] konnte nicht geladen werden: {e}", flush=True)
        _state["wishes"] = []

def save_wishes():
    try:
        WISHES_FILE.write_text(json.dumps(_state.get("wishes", []),
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
    await broadcast({"type": "wishes", "items": _state.get("wishes", [])})
    if _remote_clients:
        asyncio.create_task(_broadcast_remote_state())

def _title_matches(a: str, b: str) -> bool:
    na, nb = _norm_queue_title(a), _norm_queue_title(b)
    if not na or not nb:
        return False
    return SequenceMatcher(None, na, nb).ratio() >= 0.82

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
        path = await run_download(wish["url"], _state.get("wish_format", "mp3-best"))
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
        await _enrich_track(path, force=True)
    except Exception:
        pass          # ohne BPM/LUFS ist er trotzdem spielbar
    _set("bereit", path=path)
    await push_wishes()

remote_app = FastAPI()
remote_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@remote_app.get("/")
async def remote_index(k: str = ""):
    # Ohne gueltigen Schluessel: freundlich auf die Wunschseite
    if not _remote_key_ok(k):
        return RedirectResponse("/wunsch")
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
        data = await loop.run_in_executor(None, _extract_art_sync, path)
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

_WISH_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Musikwunsch</title>
<meta name="theme-color" content="#0d1625">
<style>
/* Grundsystem von SynthiMIX (App.svelte / lib/ui.css) als eigene Variablen —
   die Seite laeuft ohne die App. Werte hier mitziehen, wenn sich die App aendert.
   Handy: Schrift mindestens 13px, Klickziele mindestens 44px. */
:root{
  --bg:#0a0e18;--bg2:#080c16;--surf:#0e1624;--hover:#101828;--sel:#0e1c38;
  --br1:#121e30;--br2:#1e2e44;--br3:#2a3e5c;
  --tx1:#ecf2ff;--tx2:#d4e0f2;--tx3:#b0c6dc;--tx4:#9ab2cc;--tx5:#8ba6c4;
  --accent:#e07800;--accent2:#ff9020;--accent-tx:#ff9a33;--on-accent:#0a0e18;--act-bg:#1a1206;
  --green-tx:#6fcf7c;--green-bg:#0e1a10;--green-br:#2a6a30;
  --red-tx:#ff7b7b;--red-bg:#1a0808;--red-br:#8a3030;
  --blue-tx:#7fb0ec;--blue-bg:#0e1a2c;--blue-br:#2a5888;
  --r-s:4px;--r-m:8px;--r-l:12px;
  --fs-sm:13px;--fs-body:15px;--fs-lg:17px;--fs-h:20px;
  --tap:44px;
  color-scheme:dark;
}
/* Handy auf hell gestellt: helles Theme der App — draussen in der Sonne lesbarer */
@media (prefers-color-scheme: light){
  :root{
    --bg:#f4f0eb;--bg2:#ebe7e1;--surf:#ffffff;--hover:#e0dcd6;--sel:#ddeeff;
    --br1:#d6d0c8;--br2:#bdb6ad;--br3:#a39b91;
    --tx1:#0a0806;--tx2:#1e1a14;--tx3:#3a342a;--tx4:#4a443c;--tx5:#554e47;
    --accent:#b35400;--accent2:#c86000;--accent-tx:#9a4700;--on-accent:#ffffff;--act-bg:#fff1dc;
    --green-tx:#1a7030;--green-bg:#eaf6ec;--green-br:#8ac098;
    --red-tx:#b82020;--red-bg:#fff0f0;--red-br:#d89a9a;
    --blue-tx:#1a5cb0;--blue-bg:#e8f0fb;--blue-br:#93b6e0;
    color-scheme:light;
  }
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--tx2);font:var(--fs-body)/1.45 'Segoe UI',system-ui,-apple-system,sans-serif;padding-bottom:32px}
button{font:inherit}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent-tx);outline-offset:2px}

/* Einladender Kopf */
.hdr{padding:calc(22px + env(safe-area-inset-top,0px)) 20px 18px;text-align:center;background:var(--bg2);border-bottom:1px solid var(--br1)}
.logo{font-weight:700;font-size:var(--fs-lg);color:var(--accent-tx)}.logo span{color:var(--blue-tx)}
.sub{font-size:24px;font-weight:700;color:var(--tx1);margin-top:6px;line-height:1.2}
.sub-2{font-size:var(--fs-body);color:var(--tx3);margin-top:6px}

/* Laeuft gerade */
.np{display:none;margin:16px 16px 0;padding:14px 16px;border-radius:var(--r-l);background:var(--surf);border:1px solid var(--br2)}
.np-l{display:flex;align-items:center;gap:8px;font-size:var(--fs-sm);font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--green-tx)}
.np-l::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.np-t{font-size:var(--fs-lg);font-weight:700;color:var(--tx1);margin-top:4px;word-break:break-word}
.np-n{font-size:var(--fs-sm);color:var(--tx3);margin-top:6px;line-height:1.5}

.wrap{padding:16px}
.inp{width:100%;min-height:52px;border-radius:var(--r-l);border:2px solid var(--br3);background:var(--surf);
  color:var(--tx1);font-size:17px;padding:0 16px}
.inp::placeholder{color:var(--tx4)}
.inp:focus{border-color:var(--accent);outline:none}
.note{font-size:var(--fs-sm);color:var(--tx3);margin:10px 2px 16px;line-height:1.5}

/* Bestaetigung nach dem Wuenschen: gross und deutlich */
.msg{display:flex;align-items:flex-start;gap:10px;padding:14px 16px;border-radius:var(--r-l);
  font-size:var(--fs-body);font-weight:600;line-height:1.4;color:var(--green-tx);background:var(--green-bg);border:1px solid var(--green-br)}
.msg::before{content:"\\2713";font-size:20px;line-height:1;font-weight:700}
.msg.no{color:var(--red-tx);background:var(--red-bg);border-color:var(--red-br)}
.msg.no::before{content:"!"}

/* Eigene Wuensche */
.mine{display:none;margin:0 0 18px;padding:12px 14px;border-radius:var(--r-l);background:var(--surf);border:1px solid var(--br2)}
.mine-h{font-size:var(--fs-sm);font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--tx3);margin-bottom:4px}
.mi{display:flex;justify-content:space-between;align-items:center;gap:10px;min-height:44px;border-bottom:1px solid var(--br1)}
.mi:last-child{border-bottom:none}
.mi-t{flex:1;min-width:0;font-size:var(--fs-body);color:var(--tx1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mi-s{flex-shrink:0;font-size:var(--fs-sm);font-weight:600;color:var(--tx3)}
.mi-s.ok{color:var(--green-tx)}.mi-s.no{color:var(--red-tx)}

/* Suchergebnisse mit Status-Etiketten */
.r{display:flex;align-items:center;gap:12px;min-height:68px;padding:10px 0;border-bottom:1px solid var(--br1)}
.r-i{flex:1;min-width:0}
.r-t{font-size:var(--fs-body);font-weight:600;color:var(--tx1);line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.r-s{font-size:var(--fs-sm);margin-top:4px;color:var(--tx3)}
.s-free{color:var(--tx3)}
.s-playing,.s-queued,.s-played,.s-wished{display:inline-block;font-weight:700;padding:2px 8px;border-radius:999px}
.s-playing{color:var(--on-accent);background:var(--green-tx)}
.s-queued{color:var(--accent-tx);border:1px solid var(--accent)}
.s-played{color:var(--tx2);background:var(--hover)}
.s-wished{color:var(--blue-tx);border:1px solid var(--blue-br);background:var(--blue-bg)}
.lib{color:var(--green-tx);font-weight:700}
.b{flex-shrink:0;min-height:48px;min-width:108px;padding:0 16px;border-radius:var(--r-m);cursor:pointer;
  font-size:var(--fs-body);font-weight:700;border:none;background:var(--accent);color:var(--on-accent)}
.b:active{transform:scale(.97)}
.b:disabled{background:var(--hover);color:var(--tx4);cursor:default}
.empty{text-align:center;padding:24px 10px;font-size:var(--fs-sm);color:var(--tx3)}
</style>
</head>
<body>
<header class="hdr">
  <div class="logo">Synthi<span>MIX</span></div>
  <h1 class="sub">Was m&#246;chtest du h&#246;ren?</h1>
  <div class="sub-2">Titel suchen, auf W&#252;nschen tippen &#8212; der DJ sieht deinen Wunsch sofort.</div>
</header>
<div class="np" id="np">
  <div class="np-l">L&#228;uft gerade</div>
  <div class="np-t" id="npT"></div>
  <div class="np-n" id="npN"></div>
</div>
<div class="wrap">
  <input class="inp" id="q" type="search" placeholder="Titel oder K&#252;nstler suchen&#8230;" autocomplete="off" aria-label="Titel oder K&#252;nstler suchen">
  <div class="note" id="note" role="status">Mindestens zwei Buchstaben eingeben. Titel mit &#8222;&#10003; sofort da&#8220; kann der DJ gleich spielen.</div>
  <div class="mine" id="mine"><div class="mine-h">Deine W&#252;nsche</div><div id="mineL"></div></div>
  <div id="res"></div>
</div>
<script>
var ws,_t,_busy={}
/* Eigene Wuensche merkt sich das Handy (12 Stunden), damit der Gast sieht,
   ob sein Titel angenommen wurde und wann er ungefaehr laeuft. */
function mineLoad(){try{var a=JSON.parse(localStorage.getItem('synthimix-wuensche')||'[]');var g=Date.now()-12*3600e3;return a.filter(function(x){return x.at>g})}catch(e){return[]}}
function mineAdd(id){if(!id)return;var a=mineLoad().filter(function(x){return x.id!==id});a.push({id:id,at:Date.now()});try{localStorage.setItem('synthimix-wuensche',JSON.stringify(a.slice(-20)))}catch(e){}}
function info(){if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'wish_info',ids:mineLoad().map(function(x){return x.id})}))}
setInterval(info,15000)
document.addEventListener('visibilitychange',function(){if(document.visibilityState==='visible'){conn();info()}})
function showInfo(m){
  var np=document.getElementById('np')
  if(m.now){np.style.display='block';document.getElementById('npT').textContent=m.now.title+(m.now.artist&&m.now.title.indexOf(m.now.artist)<0?' · '+m.now.artist:'')}
  else np.style.display='none'
  document.getElementById('npN').innerHTML=(m.next||[]).length?'Danach: '+m.next.map(function(t){return esc(t.title)}).join(' &middot; '):''
  var mine=m.mine||[],box=document.getElementById('mine')
  box.style.display=mine.length?'block':'none'
  document.getElementById('mineL').innerHTML=mine.map(function(w){
    var t='wartet auf den DJ',c=''
    if(w.state==='queued'){t='l&#228;uft etwa um '+inClock(w.in_sec||0)+' Uhr';c='ok'}
    else if(w.state==='playing'){t='l&#228;uft gerade!';c='ok'}
    else if(w.state==='played'){t='lief um '+clock(w.at||0)+' Uhr';c='ok'}
    else if(w.state==='accepted'){t='angenommen';c='ok'}
    else if(w.state==='rejected'){t='leider nicht dabei';c='no'}
    else if(w.state==='failed'){t='konnte nicht geladen werden';c='no'}
    return'<div class=mi><span class=mi-t>'+esc(w.title)+'</span><span class="mi-s '+c+'">'+t+'</span></div>'
  }).join('')
}
function conn(){
  if(ws&&(ws.readyState===0||ws.readyState===1))return
  ws=new WebSocket('ws://'+location.host+'/wunsch/ws')
  ws.onopen=info
  ws.onclose=function(){setTimeout(conn,2000)}
  ws.onerror=function(){ws.close()}
  ws.onmessage=function(e){
    var m=JSON.parse(e.data)
    if(m.type==='wish_results')show(m.results||[],m.final!==false)
    else if(m.type==='wish_info')showInfo(m)
    else if(m.type==='wish_ack'){
      mineAdd(m.id);info()
      document.getElementById('note').innerHTML='<div class=msg><span>'+esc(m.title||'Dein Wunsch')+' ist beim DJ angekommen. Unten unter &#8222;Deine W&#252;nsche&#8220; siehst du, wann er l&#228;uft.</span></div>'
      document.getElementById('res').innerHTML=''
      document.getElementById('q').value=''
    }
    else if(m.type==='wish_deny'){
      document.getElementById('note').innerHTML='<div class="msg no">'+esc(m.text||'Das ging nicht.')+'</div>'
    }
  }
}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function clock(ts){var d=new Date(ts*1000);return('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)}
function inClock(sec){return clock(Date.now()/1000+sec)}
document.getElementById('q').oninput=function(){
  var v=this.value.trim()
  clearTimeout(_t)
  if(v.length<2){document.getElementById('res').innerHTML='';return}
  _t=setTimeout(function(){
    document.getElementById('res').innerHTML='<div class=empty>Suche&#8230;</div>'
    if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'wish_search',query:v}))
  },400)
}
function show(rs,final){
  window._rs=rs
  if(!rs.length){document.getElementById('res').innerHTML=final?'<div class=empty>Nichts gefunden</div>':'<div class=empty>Suche&#8230;</div>';return}
  document.getElementById('res').innerHTML=rs.map(function(r,i){
    var st=r.status||{},txt='',cls='s-free',dis=''
    if(st.state==='playing'){txt='l&#228;uft gerade';cls='s-playing';dis=' disabled'}
    else if(st.state==='queued'){txt='l&#228;uft etwa um '+inClock(st.in_sec||0)+' Uhr';cls='s-queued';dis=' disabled'}
    else if(st.state==='played'){txt='lief um '+clock(st.at||0)+' Uhr';cls='s-played'}
    else if(st.state==='wished'){txt='schon gew&#252;nscht'+(st.count>1?' ('+st.count+'x)':'');cls='s-wished';dis=' disabled'}
    else txt=(r.lib?'<span class=lib>&#10003; sofort da</span>'+(r.uploader?' &middot; ':''):'')+esc(r.uploader||'')
    return'<div class=r><div class=r-i><div class=r-t>'+esc(r.title)+'</div><div class="r-s '+cls+'">'+txt+'</div></div>'+
      '<button class=b'+dis+' onclick="wish('+i+',this)">W&#252;nschen</button></div>'
  }).join('')+(final?'':'<div class=empty>Suche weiter auf YouTube&#8230;</div>')
}
function wish(i,btn){
  var r=(window._rs||[])[i]
  var k=r&&(r.url||r.lib)
  if(!r||_busy[k])return
  _busy[k]=1;btn.disabled=true;btn.textContent='…'
  ws.send(JSON.stringify(r.lib?{type:'wish_add',lib:r.lib,title:r.title}:{type:'wish_add',url:r.url,title:r.title}))
}
conn()
</script>
</body>
</html>"""

@remote_app.get("/wunsch")
async def wish_page():
    return HTMLResponse(_WISH_HTML)

# Wie viele offene Wuensche ein Geraet gleichzeitig haben darf. Ohne Grenze
# kippt ein einzelner Spassvogel die Liste voll.
_WISH_LIMIT_PER_CLIENT = 3

@remote_app.websocket("/wunsch/ws")
async def wish_ws(websocket: WebSocket):
    """Bewusst getrennt vom Remote: hier gibt es keine Wiedergabesteuerung."""
    global _wish_counter
    await websocket.accept()
    who = websocket.client.host if websocket.client else "?"
    try:
        while True:
            msg = await websocket.receive_json()
            t = msg.get("type", "")

            if t == "wish_search":
                q = (msg.get("query") or "").strip()
                if q:
                    asyncio.create_task(_do_wish_search(q, websocket))

            elif t == "wish_info":
                ids = [int(i) for i in (msg.get("ids") or [])[:20] if str(i).isdigit()]
                mine = [st for st in (_guest_wish_status(i) for i in ids) if st]
                await websocket.send_text(json.dumps({"type": "wish_info", "mine": mine,
                                                      **_guest_now_next()}))

            elif t == "wish_add":
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
                    asyncio.create_task(_enrich_track(lib_entry["path"]))
    except Exception:
        pass

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

    songs, videos = await _songs_and_videos(query, 4, 8)
    if songs:
        await _send(lib_hits + online(_merge_songs_first(songs, videos)), False)
        await _ytm_fill_details(songs)
        songs = [r for r in songs if not _is_unwanted_result(r) and not LIVE_RE.search(r.get("title", ""))]
    await _send(lib_hits + online(_merge_songs_first(songs, videos)), True)

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
        "background_color": "#0a0f1a",
        "theme_color": "#0d1625",
        "icons": [],
    })

@remote_app.websocket("/ws")
async def remote_ws_endpoint(websocket: WebSocket):
    if not _remote_key_ok(websocket.query_params.get("k")):
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
                    save_queue()
                    await push_queue()
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
                    save_queue()
                    await push_queue()
            elif t == "queue_append":
                path = msg.get("path", "")
                lib = _state.get("library", [])
                td = next((x for x in lib if x.get("path") == path), None)
                if td and not _queue_is_duplicate(path, td.get("title", "")):
                    _state["queue"].append({
                        "path": path,
                        "title": td.get("title") or Path(path).stem,
                        "duration_sec": td.get("duration_sec", 0),
                        "lufs": td.get("lufs", -99.0),
                        "bpm": td.get("bpm", 0),
                        "bitrate_kbps": td.get("bitrate_kbps", 0),
                        "played": False,
                    })
                    save_queue()
                    await push_queue()
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
                    save_queue()
                    await push_queue()
            elif t == "search_library":
                query = (msg.get("query") or "").lower()
                if query and query != "__clear__":
                    lib = _state.get("library", [])
                    results = [
                        {"title": x.get("title",""), "artist": x.get("artist",""),
                         "path": x.get("path",""), "duration_sec": x.get("duration_sec",0),
                         "key": x.get("key", ""), "key_src": x.get("key_src", ""),
                         "bpm": x.get("bpm", 0)}
                        for x in lib
                        if query in (x.get("title","") or "").lower()
                        or query in (x.get("artist","") or "").lower()
                    ][:30]
                    await websocket.send_text(json.dumps({"type": "search_results", "results": results}))
            elif t == "yt_search_remote":
                query = msg.get("query", "").strip()
                if query:
                    asyncio.create_task(_do_yt_search_remote(query, websocket))
            elif t == "yt_dl_queue":
                url = msg.get("url", "")
                title = msg.get("title", "")
                as_next = bool(msg.get("as_next", False))
                if url:
                    asyncio.create_task(_remote_dl_and_queue(url, title, websocket, as_next))
            elif t in ("set_normalize_volume", "wish_accept", "wish_reject",
                       "set_auto_mix", "set_radio"):
                await handle_message(websocket, msg)
            elif t == "remote_playlists":
                await websocket.send_text(json.dumps(
                    {"type": "playlists", "items": _get_playlists()}))
            elif t == "remote_load_playlist":
                # Pfad gegen die bekannten Playlisten pruefen — load_playlist
                # wuerde sonst jede beliebige Datei einlesen.
                want  = msg.get("path", "")
                known = {pl.get("path") for pl in _get_playlists()}
                if want in known:
                    await handle_message(websocket, {"type": "load_playlist", "path": want})
            elif t in ("pause", "resume", "play_at", "play_next", "play_prev",
                       "set_volume", "seek"):
                await handle_message(websocket, msg)
    except Exception:
        pass
    finally:
        _remote_clients.discard(websocket)

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

    songs, videos = await _songs_and_videos(query, 4, 8)
    await send(_merge_songs_first(songs, videos))
    if songs:
        await _ytm_fill_details(songs)
        songs = [r for r in songs if not _is_unwanted_result(r) and not LIVE_RE.search(r.get("title", ""))]
        await send(_merge_songs_first(songs, videos))

async def _remote_dl_and_queue(url: str, title: str, ws: WebSocket, as_next: bool = False):
    try:
        path = await run_download(url)
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
            if not _queue_is_duplicate(path, entry["title"]):
                if as_next:
                    ci = _state.get("current_idx", -1)
                    _state["queue"].insert(ci + 1, entry)
                else:
                    _state["queue"].append(entry)
                save_queue()
                await push_queue()
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
                "compat": _key_compat(prev_key, key) if key and prev_key else -1}
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
                    "status": w.get("status", ""), "error": w.get("error", "")}
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
        ip = _get_local_ip()
        await requester.send_text(json.dumps({
            "type": "remote_status", "running": True,
            "ip": ip, "port": _remote_port, **_remote_urls(ip)
        }))
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
                        await broadcast({"type": "remote_status", "running": False})
                    except Exception:
                        pass

        asyncio.create_task(_serve_task())
        # Kurz warten bis uvicorn den Port gebunden hat
        await asyncio.sleep(0.3)
        if _remote_server is None:
            # Startup fehlgeschlagen (z.B. Port belegt)
            try:
                await requester.send_text(json.dumps({"type": "remote_status", "running": False, "error": f"Port {_remote_port} nicht verfügbar"}))
            except Exception:
                pass
            return
        print(f"[remote] gestartet auf http://{ip}:{_remote_port}", flush=True)
        await broadcast({"type": "remote_status", "running": True,
                         "ip": ip, "port": _remote_port, **_remote_urls(ip)})
    except Exception as e:
        print(f"[remote] Fehler beim Starten: {e}", flush=True)
        _remote_server = None
        try:
            await requester.send_text(json.dumps({"type": "remote_status", "running": False, "error": str(e)}))
        except Exception:
            pass

async def _stop_remote_server():
    global _remote_server
    if _remote_server:
        _remote_server.should_exit = True
        _remote_server = None
        print("[remote] gestoppt", flush=True)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=_BACKEND_PORT, log_level="warning")
