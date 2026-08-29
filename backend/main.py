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
    asyncio.create_task(_watcher_loop())
    asyncio.create_task(_auto_scan_loop())
    asyncio.create_task(_ytdlp_autoupdate_loop())
    print("[backend] ready on ws://127.0.0.1:8765/ws", flush=True)
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
    "loudnorm_on_dl":     True,
    "loudnorm_target":    -10.0,
    "scan_recursive":     True,
    "watched_folders":    [],
    "auto_remove_played": False,
}

# ── broadcast ────────────────────────────────────────────────────────────────
async def broadcast(msg: dict):
    dead = set()
    for ws in clients:
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
    saved_idx = int(raw.get("current_idx", -1))
    _state["current_idx"] = saved_idx if 0 <= saved_idx < len(valid) else (-1 if not valid else 0)
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
            "ext":          str(t.get("ext", Path(str(t.get("path",""))).suffix.lstrip('.').lower())),
            "mtime":        int(t.get("mtime", 0)),
            "play_count":   int(t.get("play_count", 0)),
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
    _state["auto_mix"]                = bool(raw.get("auto_mix", True))
    _state["loudnorm_on_dl"]          = bool(raw.get("loudnorm_on_dl", True))
    _state["loudnorm_target"]         = float(raw.get("loudnorm_target", -10.0))
    _state["loudnorm_tp"]             = float(raw.get("loudnorm_tp", -1.5))
    _state["playlist_folder_enabled"] = bool(raw.get("playlist_folder_enabled", True))
    _state["dl_filename_format"]      = str(raw.get("dl_filename_format", "title"))
    _state["download_dir"]            = str(raw.get("download_dir", str(BASE_DIR / "Downloads")))
    _state["auto_scan_interval_min"]  = int(raw.get("auto_scan_interval_min", 0))
    _state["favorites"]               = list(raw.get("favorites", []))
    _state["remote_autostart"]        = bool(raw.get("remote_autostart", False))
    _state["ytdlp_autoupdate"]        = bool(raw.get("ytdlp_autoupdate", True))
    _state["ytdlp_last_check"]        = int(raw.get("ytdlp_last_check", 0))
    _state["normalize_volume"]        = bool(raw.get("normalize_volume", True))
    _state["target_lufs"]             = float(raw.get("target_lufs", -10.0))
    _state["spotify_client_id"]       = str(raw.get("spotify_client_id", ""))
    _state["spotify_client_secret"]   = str(raw.get("spotify_client_secret", ""))
    _state["lastfm_api_key"]          = str(raw.get("lastfm_api_key", ""))
    _state["acoustid_api_key"]        = str(raw.get("acoustid_api_key", ""))
    _state["radio_enabled"]           = bool(raw.get("radio_enabled", False))

def save_settings():
    _save_json(SETTINGS_FILE, {
        "volume":                   _state["volume"],
        "crossfade_s":              _state["crossfade_s"],
        "bpm_analysis":    _state.get("bpm_analysis", True),
        "scan_recursive":  _state.get("scan_recursive", True),
        "watched_folders": _state.get("watched_folders", []),
        "auto_mix":                _state.get("auto_mix", True),
        "loudnorm_on_dl":          _state.get("loudnorm_on_dl", True),
        "loudnorm_target":         _state.get("loudnorm_target", -10.0),
        "loudnorm_tp":             _state.get("loudnorm_tp", -1.5),
        "playlist_folder_enabled": _state.get("playlist_folder_enabled", True),
        "dl_filename_format":      _state.get("dl_filename_format", "title"),
        "download_dir":            _state.get("download_dir", str(BASE_DIR / "Downloads")),
        "auto_scan_interval_min":  _state.get("auto_scan_interval_min", 0),
        "favorites":               _state.get("favorites", []),
        "remote_autostart":        _state.get("remote_autostart", False),
        "ytdlp_autoupdate":        _state.get("ytdlp_autoupdate", True),
        "ytdlp_last_check":        _state.get("ytdlp_last_check", 0),
        "normalize_volume":        _state.get("normalize_volume", True),
        "target_lufs":             _state.get("target_lufs", -10.0),
        "spotify_client_id":       _state.get("spotify_client_id", ""),
        "spotify_client_secret":   _state.get("spotify_client_secret", ""),
        "lastfm_api_key":          _state.get("lastfm_api_key", ""),
        "acoustid_api_key":        _state.get("acoustid_api_key", ""),
        "radio_enabled":           _state.get("radio_enabled", False),
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

def _probe_sync(path: str) -> dict:
    result = {"duration_sec": 0.0, "bitrate_kbps": 0, "bpm": 0,
              "title": Path(path).stem, "artist": "", "album_artist": "",
              "album": "", "genre": "",
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
            bpm_s = _t('TBPM','bpm')
            try: result["bpm"] = int(float(bpm_s)) if bpm_s else 0
            except ValueError: pass
    except Exception:
        pass
    return result

_analyze_running = False
_analyze_cancel  = False
_unanalyzable_paths: set[str] = set()   # Pfade die dauerhaft nicht analysierbar sind

async def _analyze_library_meta_task():
    """Background: fill in missing LUFS and BPM for every library track."""
    global _analyze_running, _analyze_cancel
    if _analyze_running:
        return
    _analyze_running = True
    _analyze_cancel  = False
    loop    = asyncio.get_running_loop()
    try:
        tracks  = list(_state["library"])
        pending = [lt for lt in tracks if lt.get("path") and os.path.exists(lt["path"])
                   and not lt.get("unanalyzable")
                   and (lt.get("lufs", -99) <= -90 or not lt.get("bpm"))]
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
    _state["library"].append({
        "path":         path,
        "title":        probe["title"] or Path(path).stem,
        "artist":       probe.get("artist", ""),
        "album_artist": probe.get("album_artist", ""),
        "folder":       Path(path).parent.name,
        "ext":          probe.get("ext", Path(path).suffix.lstrip('.').lower()),
        "duration_sec": probe["duration_sec"],
        "lufs":         -99.0,
        "bpm":          probe["bpm"],
        "bitrate_kbps": probe["bitrate_kbps"],
        "comment":      probe.get("comment", ""),
        "mtime":        int(os.path.getmtime(path)) if os.path.exists(path) else 0,
        "play_count":   0,
    })
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
        p = Path(path)
        mtime = int(os.path.getmtime(path))
        lib_entry = {
            "path": path,
            "title": probe.get("title") or p.stem,
            "artist": probe.get("artist") or "",
            "album_artist": probe.get("album_artist") or "",
            "comment": probe.get("comment") or "",
            "ext": probe.get("ext") or p.suffix.lstrip('.').lower(),
            "folder": p.parent.name,
            "duration_sec": duration or probe.get("duration_sec", 0),
            "lufs": lufs if lufs > -90 else -99.0,
            "bpm": bpm or probe.get("bpm", 0),
            "bitrate_kbps": probe.get("bitrate_kbps", 0),
            "mtime": mtime,
            "play_count": 0,
        }
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
    if lib_changed:
        save_library()

    save_queue()
    await broadcast({"type": "track_enriched", "path": path, "art": art, "lufs": lufs, "bpm": bpm, "duration_sec": duration})

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
            await _send("❌ Release nicht gefunden", -1); return
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
        if ws is not None:
            await _check_tools(ws)
    except Exception as e:
        await _send(f"❌ {e}", -1)

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
# which (unlike ytmsearch) isn't scoped to the music catalog at all
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
    # Explicit artist field (sometimes present)
    if r.get("artist"):
        return str(r["artist"]).strip()
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
            pick = random.choice(candidates)
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
            batch = await _run_search_cmd(_yt(f"ytmsearch15:{sq}", *base_args))
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
            # plain-YouTube fallback, since ytmsearch alone rarely surfaces these
            if NON_MUSIC_RE.search(r_title):
                continue
            # Skip live recordings / concert performances
            if LIVE_RE.search(r_title):
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
        p2 = subprocess.run(
            [FFMPEG, "-nostdin", "-i", path, "-af", af, "-ar", "48000", "-y", tmp],
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
                    if best:
                        best_match = best
                        break

        # Fallback: random track from library not in queue
        if not best_match:
            best_match = random.choice(candidates_total)

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

async def _install_spotdl(ws):
    async def _send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

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
            save_wishes()
            await push_wishes()

    elif t == "wish_reject":
        wid = msg.get("id")
        w = next((x for x in _state.get("wishes", []) if x.get("id") == wid), None)
        if w:
            # Heruntergeladene Datei mitnehmen — abgelehnte Wuensche sollen
            # den Download-Ordner nicht vollmuellen.
            p = w.get("path", "")
            if p and os.path.exists(p) and msg.get("delete_file", True):
                try:
                    os.remove(p)
                    _state["library"] = [lt for lt in _state["library"] if lt.get("path") != p]
                    save_library()
                    await push_library()
                except Exception as e:
                    print(f"[wishes] konnte {p} nicht loeschen: {e}", flush=True)
            _state["wishes"] = [x for x in _state["wishes"] if x.get("id") != wid]
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
        _state["library"] = [lt for lt in _state["library"] if lt.get("path") != path]
        save_library()
        await push_library()
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

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
                ("scan_recursive", True), ("auto_mix", True), ("loudnorm_on_dl", True),
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
            if folder not in _state["watched_folders"]:
                _state["watched_folders"].append(folder)
                save_settings()

        async def _scan_all():
            # Ohne Ordnerauswahl alles neu einlesen — vorher passierte hier
            # schlicht nichts, etwa wenn der Knopf aus dem Remote kam.
            for f in _scan_folders():
                await scan_folder(f)
        asyncio.create_task(_scan_all())

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
        if url:
            if _is_spotify(url):
                asyncio.create_task(run_spotify_download(url, fmt))
            elif choice is None and _is_mixed_playlist_url(url):
                asyncio.create_task(_ask_playlist_choice(url, fmt, ws))
            else:
                if choice == "single":
                    url = _strip_playlist_params(url)
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
        valid = [i for i in indices if 0 <= i < len(q)]
        if len(valid) > 1:
            tracks = [q[i] for i in valid]
            random.shuffle(tracks)
            for i, idx in enumerate(valid):
                q[idx] = tracks[i]
            # Recalculate current_idx if the current track moved
            cur_path = q[ci]["path"] if 0 <= ci < len(q) else None
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
        await push_player()

    elif t == "set_repeat":
        _state["repeat"] = int(msg.get("value", 0)) % 3
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
            try:
                os.remove(pl_path)
            except Exception:
                pass
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

    elif t == "analyze_library_meta":
        asyncio.create_task(_analyze_library_meta_task())

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

    elif t == "set_ytdlp_autoupdate":
        _state["ytdlp_autoupdate"] = bool(msg.get("value", True))
        save_settings()
        await ws.send_text(json.dumps({"type": "settings",
                                       "ytdlp_autoupdate": _state["ytdlp_autoupdate"]}))

    elif t == "set_remote_autostart":
        _state["remote_autostart"] = bool(msg.get("value", False))
        save_settings()
        await ws.send_text(json.dumps({"type": "settings", "remote_autostart": _state["remote_autostart"]}))
        await broadcast({"type": "remote_status", "running": False})

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

async def _ytdlp_check_once():
    # Nicht mitten in einen laufenden Download platzen: unter Windows laesst
    # sich die Exe nicht ersetzen, solange sie benutzt wird.
    if any(d.get("status") == "active" for d in _state.get("downloads", [])):
        return
    loop     = asyncio.get_running_loop()
    lokal    = await loop.run_in_executor(None, _ytdlp_version_sync)
    neueste  = await loop.run_in_executor(None, _ytdlp_latest_tag)
    _state["ytdlp_last_check"] = int(time.time())
    save_settings()
    if not lokal or not neueste or lokal == neueste:
        return
    print(f"[yt-dlp] {lokal} ist veraltet, neueste ist {neueste}", flush=True)
    await _update_ytdlp()
    await broadcast({"type": "tools_info", "ytdlp_version": _ytdlp_version_sync()})

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
            if _state.get("ytdlp_autoupdate", True) and                time.time() - _state.get("ytdlp_last_check", 0) > 86400:
                await _ytdlp_check_once()
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
        for f in files:
            if Path(f).suffix.lower() in AUDIO_EXTS:
                full = str(Path(os.path.join(root, f)))  # normalisiert Slashes auf Windows
                probe = _probe_sync(full)
                result.append({
                    "path":         full,
                    "title":        probe["title"] or Path(f).stem,
                    "artist":       probe.get("artist", ""),
                    "album_artist": probe.get("album_artist", ""),
                    "folder":       Path(root).name,
                    "ext":          probe.get("ext", Path(f).suffix.lstrip('.').lower()),
                    "duration_sec": probe["duration_sec"],
                    "lufs":         -99.0,
                    "bpm":          probe["bpm"],
                    "bitrate_kbps": probe["bitrate_kbps"],
                    "comment":      probe.get("comment", ""),
                    "mtime":        int(os.path.getmtime(full)),
                    "play_count":   0,
                })
    return result

def _find_new_audio_paths(folder: str, existing: set[str], recursive: bool) -> list[str]:
    """Fast filesystem scan — returns only paths NOT already in the library (no ffprobe)."""
    new_paths = []
    if recursive:
        for root, _, files in os.walk(folder):
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
                    if full not in existing:
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
                        new_tracks.append({
                            "path":         p,
                            "title":        probe["title"] or Path(p).stem,
                            "artist":       probe.get("artist", ""),
                            "album_artist": probe.get("album_artist", ""),
                            "folder":       Path(p).parent.name,
                            "ext":          probe.get("ext", Path(p).suffix.lstrip('.').lower()),
                            "duration_sec": probe["duration_sec"],
                            "lufs":         -99.0,
                            "bpm":          probe["bpm"],
                            "bitrate_kbps": probe["bitrate_kbps"],
                            "comment":      probe.get("comment", ""),
                            "mtime":        int(os.path.getmtime(p)),
                            "play_count":   0,
                        })
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
        asyncio.create_task(run_download(_strip_playlist_params(url), fmt))
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

    if _state.get("loudnorm_on_dl", True):
        t  = float(_state.get("loudnorm_target", -10.0))
        tp = float(_state.get("loudnorm_tp", -1.5))
        cmd += ["--postprocessor-args",
                f"ffmpeg:-af loudnorm=I={t}:TP={tp}:LRA=11"]

    if is_search:
        cmd.append(f"ytmsearch1:{url}")  # YouTube Music search → prefers audio over video
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
                    entries.append({"url": url, "title": item.get("title") or "", "replaced": False})
            except Exception:
                pass
        await proc.wait()
    except Exception:
        pass

    if not entries:
        return entries

    # For video-keyword entries, search YTM for audio version
    search_base = ["--flat-playlist", "-j", "--no-playlist", "--quiet", "--no-warnings"]
    if FFMPEG_DIR:
        search_base += ["--ffmpeg-location", FFMPEG_DIR]

    for entry in entries:
        title = entry["title"] or ""
        if not _VIDEO_TITLE_RE.search(title):
            continue
        # Extract "Artist - Song" portion (strip video keywords)
        query = re.sub(_VIDEO_TITLE_RE, '', title).strip(' -|')
        if not query:
            continue
        results = await _run_search_cmd(_yt(f"ytmsearch5:{query}", *search_base))
        if not results:
            continue
        # Filter live recordings and overlong tracks before scoring
        results = [r for r in results
                   if not LIVE_RE.search(r.get("title", ""))
                   and 0 < (r.get("duration") or 0) <= 480]
        if not results:
            continue
        # Prefer Topic channel or audio/lyric in title, penalise video
        results.sort(key=lambda r: r["_score"], reverse=True)
        best = results[0]
        if best["_score"] >= 0:  # only replace if result is neutral or better
            entry["url"]      = best["url"]
            entry["title"]    = best["title"] or title
            entry["replaced"] = True

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

async def do_search(query: str, ws: WebSocket):
    base_args = ["--flat-playlist", "-j", "--no-playlist", "--quiet"]
    if FFMPEG_DIR:
        base_args += ["--ffmpeg-location", FFMPEG_DIR]

    results = await _run_search_cmd(_yt(f"ytmsearch10:{query}", *base_args))
    if not results:
        results = await _run_search_cmd(_yt(f"ytsearch10:{query}", *base_args))

    results = [r for r in results if not _is_unwanted_result(r)]
    results.sort(key=lambda r: r["_score"], reverse=True)
    for r in results:
        del r["_score"]

    await ws.send_text(json.dumps({"type": "search_results", "query": query, "results": results}))

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
        "--bitrate", "320k" if sdl_fmt == "mp3" else "auto",
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
from fastapi.responses import HTMLResponse, JSONResponse, Response

def _get_local_ip() -> str:
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

_REMOTE_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>SynthiMIX Remote</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0d1625">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="SynthiMIX">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0f1a;color:#c8d8f0;font-family:system-ui,sans-serif}
.hdr{background:#0d1625;padding:10px 14px;border-bottom:1px solid #1a2838;display:flex;align-items:center;gap:8px;position:sticky;top:0;z-index:50}
.logo{color:#e07800;font-weight:700;font-size:14px}.logo span{color:#3b82f6}
.sub{font-size:10px;color:#4a6080}
.dot{width:8px;height:8px;border-radius:50%;background:#d57575;margin-left:auto;transition:background .3s}
.dot.on{background:#75d595}
.np{padding:12px 14px 8px;background:#080c14;text-align:center;border-bottom:1px solid #0e1a28}
.np-t{font-size:16px;font-weight:600;color:#f0c070;min-height:22px;word-break:break-word}
.np-a{font-size:12px;color:#7090b0;min-height:16px;margin-top:3px}
.ctrls{display:flex;justify-content:center;gap:20px;padding:14px}
.btn{background:#1a2838;border:none;border-radius:50%;color:#c8d8f0;font-size:18px;width:46px;height:46px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.big{background:#e07800;color:#fff;font-size:22px;width:54px;height:54px}
.btn:active{opacity:.6}
.vol{padding:4px 16px 12px}
.vol-h{font-size:10px;color:#7090b0;margin-bottom:4px;display:flex;justify-content:space-between}
input[type=range]{width:100%;accent-color:#e07800;height:24px}
.sec{border-top:1px solid #1a2838;padding:10px 14px 4px}
.sec-h{font-size:10px;font-weight:700;letter-spacing:.1em;color:#4a6080;margin-bottom:8px}
.q-wrap{max-height:45vh;overflow-y:auto;-webkit-overflow-scrolling:touch}
.qi{display:flex;align-items:center;gap:6px;padding:8px 2px;border-bottom:1px solid #0e1a28;min-height:48px;user-select:none}
.qi.cur{background:#12200a}
.dh{color:#2a3a54;font-size:20px;padding:4px 8px;flex-shrink:0;touch-action:none;line-height:1;cursor:grab}
.qi-info{flex:1;min-width:0}
.qi-t{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.qi-t.a{color:#f0a040;font-weight:600}
.qi-t.p{color:#3a5070}
.qi-d{font-size:10px;color:#3a5070;margin-top:2px}
.rmb{background:none;border:none;color:#5a2a2a;font-size:18px;width:36px;height:36px;cursor:pointer;flex-shrink:0;padding:0}
.rmb:active{color:#c05050}
.s-row{display:flex;gap:6px;margin-bottom:8px}
.inp{flex:1;background:#1a2838;border:1px solid #2a3848;border-radius:6px;color:#c8d8f0;font-size:14px;padding:8px 10px;outline:none}
.inp::placeholder{color:#4a6080}
.ri{display:flex;align-items:center;gap:8px;padding:8px 2px;border-bottom:1px solid #0e1a28;min-height:48px}
.ri-info{flex:1;min-width:0}
.ri-t{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ri-a{font-size:10px;color:#4a6080;margin-top:2px}
.add-b{background:#0d2010;border:1px solid #1a4020;border-radius:6px;color:#75d595;font-size:18px;width:36px;height:36px;cursor:pointer;flex-shrink:0;padding:0}
.add-b:active{background:#1a3820}
.empty{padding:14px 2px;color:#4a6080;font-size:12px;text-align:center}
.np-nx{font-size:11px;color:#4a6080;min-height:14px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cov{display:none;width:132px;height:132px;object-fit:cover;border-radius:8px;margin:0 auto 10px;box-shadow:0 6px 18px rgba(0,0,0,.5)}
.cov.on{display:block}
/* Der Balken selbst ist 4px hoch — zu wenig zum Treffen. Die Huelle gibt ihm
   eine Trefferflaeche von 22px, ohne das Aussehen zu veraendern. */
.pb-hit{padding:9px 0;margin:-1px 0 -7px;cursor:pointer}
.pb-wrap{background:#1a2838;border-radius:3px;height:4px;overflow:hidden}
.pl-list{display:flex;flex-wrap:wrap;gap:6px}
.pl-b{background:#1a2838;border:1px solid #2a3848;border-radius:6px;color:#8aaac8;font-size:12px;padding:7px 12px;cursor:pointer}
.pl-b:active{background:#12243a;border-color:#e07800}
.pb-fill{background:#e07800;height:100%;width:0%;transition:width .9s linear}
.pb-row{display:flex;justify-content:space-between;font-size:10px;color:#4a6080;margin-bottom:6px}
.qi-eta{font-size:10px;color:#2a4060;margin-top:1px}
.s-tabs{display:flex;gap:6px;margin-bottom:8px}
.s-tab{flex:1;background:#1a2838;border:1px solid #2a3848;border-radius:6px;color:#5a7898;font-size:12px;padding:6px 4px;cursor:pointer}
.s-tab.active{background:#12243a;border-color:#3b82f6;color:#c8d8f0}
.dl-b{background:#0d1a30;border:1px solid #1a3050;border-radius:6px;color:#4a9eff;font-size:18px;width:36px;height:36px;cursor:pointer;flex-shrink:0;padding:0;transition:color .15s}
.dl-b:disabled{color:#2a3848;cursor:default}
.ri-sub{font-size:10px;color:#4a6080;margin-top:2px}
.norm-wrap{margin-top:8px}
.norm-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.norm-btn{flex:1;background:#1a2838;border:1px solid #2a3848;border-radius:6px;color:#5a7898;font-size:12px;padding:6px 10px;cursor:pointer;text-align:center}
.norm-btn.on{background:#0d2010;border-color:#1a4020;color:#75d595}
.norm-val{font-size:12px;color:#4a9eff;min-width:52px;text-align:right;white-space:nowrap}
#normr{width:100%;margin-top:6px;accent-color:#3b82f6}
.nxt-b{background:#0d1a30;border:1px solid #1a3050;border-radius:6px;color:#4a9eff;font-size:16px;width:36px;height:36px;cursor:pointer;flex-shrink:0;padding:0;margin-right:4px}
.nxt-b:active{opacity:.6}
</style>
</head>
<body>
<div class="hdr">
  <span class="logo">Synthi<span>MIX</span></span>
  <span class="sub">Remote</span>
  <div id="dot" class="dot"></div>
</div>
<div class="np">
  <img id="cov" class="cov" alt="">
  <div id="npT" class="np-t">&#8211;</div>
  <div id="npA" class="np-a">&#8211;</div>
  <div class="pb-hit" onclick="seekAt(event)" title="Zum Spulen antippen">
    <div class="pb-wrap"><div id="pbf" class="pb-fill"></div></div>
  </div>
  <div class="pb-row"><span id="pbt">0:00</span><span id="pbr">&#8211;</span></div>
  <div id="npNx" class="np-nx"></div>
</div>
<div class="ctrls">
  <button class="btn" onclick="send({type:'play_prev'})">&#9198;</button>
  <button id="pb" class="btn big" onclick="toggle()">&#9654;</button>
  <button class="btn" onclick="send({type:'play_next'})">&#9197;</button>
</div>
<div class="vol">
  <div class="vol-h"><span>Lautst&#228;rke</span><span id="vv">80%</span></div>
  <input type="range" id="vr" min="0" max="100" value="80" oninput="onVol(this.value)" onchange="flushVol()">
  <div class="norm-wrap">
    <div class="norm-head">
      <button id="normb" class="norm-btn on" onclick="toggleNorm()">&#128266; Normalisierung</button>
      <span id="normv" class="norm-val">-10 LUFS</span>
    </div>
    <input type="range" id="normr" min="-24" max="-6" step="1" value="-10" oninput="onNorm(this.value)" onchange="flushNorm()">
  </div>
</div>
<div class="sec">
  <div class="sec-h">WARTESCHLANGE &nbsp;<span id="qc" style="font-weight:400;color:#3a5070">0</span></div>
  <div id="qw" class="q-wrap"><div id="ql"></div></div>
</div>
<div class="sec">
  <div class="sec-h">PLAYLISTEN</div>
  <div id="pll" class="pl-list"><div class="empty">&#8230;</div></div>
</div>
<div class="sec" style="padding-bottom:20px">
  <div class="sec-h">SUCHE</div>
  <div class="s-tabs">
    <button id="tb-lib" class="s-tab active" onclick="setMode('lib')">Bibliothek</button>
    <button id="tb-yt" class="s-tab" onclick="setMode('yt')">YouTube</button>
  </div>
  <div class="s-row"><input class="inp" id="si" placeholder="Titel oder K&#252;nstler&#8230;" type="search" oninput="onS(this.value)"></div>
  <div id="sr"></div>
</div>
<script>
var st={playing:false,current_idx:-1,volume:80,normalize_volume:true,target_lufs:-10,queue:[]},ws,_vt,_res=[],_ytRes=[],_srMode='lib'
var _rt=null,_wl=null
function conn(){
  if(ws&&(ws.readyState===0||ws.readyState===1))return
  clearTimeout(_rt);_rt=null
  ws=new WebSocket('ws://'+location.hostname+':8080/ws')
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
function dot(on){document.getElementById('dot').className='dot'+(on?' on':'')}
function send(o){if(ws&&ws.readyState===1)ws.send(JSON.stringify(o))}
function toggle(){send({type:st.playing?'pause':'resume'})}
var _pv=null
function onVol(v){document.getElementById('vv').textContent=v+'%';_pv=+v;clearTimeout(_vt);_vt=setTimeout(flushVol,120)}
function flushVol(){if(_pv!=null){send({type:'set_volume',value:_pv});_pv=null}}
function updateNormUI(){var nb=document.getElementById('normb');var nr=document.getElementById('normr');if(nb){nb.textContent=st.normalize_volume?'🔊 Normalisierung':'🔇 Normalisierung';nb.className='norm-btn'+(st.normalize_volume?' on':'')};if(nr)nr.style.opacity=st.normalize_volume?'1':'0.4'}
function toggleNorm(){st.normalize_volume=!st.normalize_volume;send({type:'set_normalize_volume',value:st.normalize_volume});updateNormUI()}
var _nv=null,_nt
function onNorm(v){document.getElementById('normv').textContent=v+' LUFS';_nv=+v;clearTimeout(_nt);_nt=setTimeout(flushNorm,200)}
function flushNorm(){if(_nv!=null){st.target_lufs=_nv;send({type:'set_normalize_volume',value:st.normalize_volume,target_lufs:_nv});_nv=null}}
function fmt(s){if(!s)return'';var m=Math.floor(s/60);return m+':'+(Math.floor(s%60)+'').padStart(2,'0')}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function rm(i){if(confirm('Entfernen?'))send({type:'queue_remove',index:i})}
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
    return'<div class=ri><div class=ri-info><div class=ri-t>'+esc(r.title||'&#8211;')+'</div><div class=ri-a>'+esc(r.artist||'')+(r.duration_sec?' &middot; '+fmt(r.duration_sec):'')+'</div></div><button class=nxt-b onclick="addNext('+i+')" title="Als n&#228;chstes einreihen">&#9197;</button><button class=add-b onclick="addLib('+i+')" title="Ans Ende">+</button></div>'
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
  document.getElementById('pb').innerHTML=st.playing?'⏸':'▶'
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
    return'<div class="qi'+(a?' cur':'')+'"><div class=dh ontouchstart="dhStart(event,'+i+')" ontouchmove="dhMove(event)" ontouchend="dhEnd(event)">☰</div><div class=qi-info><div class="qi-t'+(a?' a':p?' p':'')+'">'+esc(t.title||'–')+'</div><div class=qi-d>'+fmt(t.duration_sec)+(etaAbs?'<span class=qi-eta> · '+etaAbs+'</span>':'')+'</div></div><button class=rmb onclick="rm('+i+')">✕</button></div>'
  }).join(''):'<div class=empty>Warteschlange leer</div>'
  // Scroll current track into view
  if(ci>=0){var rows=document.getElementById('ql').children;if(rows[ci])rows[ci].scrollIntoView({behavior:'smooth',block:'nearest'})}
}
function seekAt(e){
  var box=e.currentTarget.getBoundingClientRect()
  var frac=Math.min(1,Math.max(0,(e.clientX-box.left)/box.width))
  var dur=st.duration_ms||0
  if(dur>0){send({type:'seek',position_ms:Math.round(frac*dur)});updPos({pos:frac*dur,dur:dur})}
}
function setCover(ci){
  var el=document.getElementById('cov')
  if(ci==null||ci<0){el.className='cov';el.removeAttribute('src');return}
  var want='/cover?i='+ci
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

async def push_wishes():
    await broadcast({"type": "wishes", "items": _state.get("wishes", [])})

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
    # 2. Lief er heute schon?
    for entry in _state.get("play_log", []):
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

    _set("analysiert", path=path)
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
async def remote_index():
    return HTMLResponse(_REMOTE_HTML)

# Cover werden per ffmpeg aus der Datei geholt — das dauert, also einmal je
# Pfad merken. None heisst "hat keins", damit nicht jedes Mal neu gesucht wird.
_remote_art: dict[str, bytes | None] = {}

@remote_app.get("/cover")
async def remote_cover(i: int = -1):
    """Cover des Titels an Warteschlangenposition i.

    Bewusst ueber den Index und nicht ueber einen Pfad: ein Pfad aus der
    Anfrage waere ein Weg, beliebige Dateien vom Rechner zu lesen.
    """
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
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0f1a;color:#c8d8f0;font-family:system-ui,sans-serif;padding-bottom:30px}
.hdr{background:#0d1625;padding:14px;border-bottom:1px solid #1a2838;text-align:center}
.logo{color:#e07800;font-weight:700;font-size:18px}.logo span{color:#3b82f6}
.sub{font-size:11px;color:#4a6080;margin-top:3px}
.wrap{padding:16px 14px}
.inp{width:100%;background:#0d1625;border:1px solid #2a3848;border-radius:8px;color:#c8d8f0;font-size:16px;padding:12px 14px;outline:none}
.inp:focus{border-color:#e07800}
.note{font-size:11px;color:#4a6080;margin:10px 2px 14px;line-height:1.5}
.r{display:flex;align-items:center;gap:10px;padding:11px 0;border-bottom:1px solid #131e2e}
.r-i{flex:1;min-width:0}
.r-t{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.r-s{font-size:11px;margin-top:3px}
.s-free{color:#4a6080}.s-queued{color:#e0a040}.s-played{color:#5a7090}
.s-wished{color:#3b82f6}.s-playing{color:#75d595}
.b{background:#0d2010;border:1px solid #1a4020;border-radius:8px;color:#75d595;font-size:13px;padding:10px 14px;cursor:pointer;flex-shrink:0}
.b:disabled{background:#141a24;border-color:#232f3f;color:#3a5068;cursor:default}
.msg{text-align:center;padding:18px 10px;font-size:13px;color:#75d595}
.empty{text-align:center;padding:24px 10px;font-size:12px;color:#4a6080}
</style>
</head>
<body>
<div class="hdr">
  <div class="logo">Synthi<span>MIX</span></div>
  <div class="sub">Was m&#246;chtest du h&#246;ren?</div>
</div>
<div class="wrap">
  <input class="inp" id="q" type="search" placeholder="Titel oder K&#252;nstler&#8230;" autocomplete="off">
  <div class="note" id="note">Such deinen Titel und tipp auf W&#252;nschen. Der DJ bekommt ihn angezeigt.</div>
  <div id="res"></div>
</div>
<script>
var ws,_t,_busy={}
function conn(){
  if(ws&&(ws.readyState===0||ws.readyState===1))return
  ws=new WebSocket('ws://'+location.host+'/wunsch/ws')
  ws.onclose=function(){setTimeout(conn,2000)}
  ws.onerror=function(){ws.close()}
  ws.onmessage=function(e){
    var m=JSON.parse(e.data)
    if(m.type==='wish_results')show(m.results||[])
    else if(m.type==='wish_ack'){
      document.getElementById('note').innerHTML='<div class=msg>&#10003; '+esc(m.title||'Dein Wunsch')+' ist beim DJ angekommen.</div>'
      document.getElementById('res').innerHTML=''
      document.getElementById('q').value=''
    }
    else if(m.type==='wish_deny'){
      document.getElementById('note').innerHTML='<div class=msg style="color:#e0a040">'+esc(m.text||'Das ging nicht.')+'</div>'
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
function show(rs){
  window._rs=rs
  if(!rs.length){document.getElementById('res').innerHTML='<div class=empty>Nichts gefunden</div>';return}
  document.getElementById('res').innerHTML=rs.map(function(r,i){
    var st=r.status||{},txt='',cls='s-free',dis=''
    if(st.state==='playing'){txt='l&#228;uft gerade';cls='s-playing';dis=' disabled'}
    else if(st.state==='queued'){txt='l&#228;uft etwa um '+inClock(st.in_sec||0)+' Uhr';cls='s-queued';dis=' disabled'}
    else if(st.state==='played'){txt='lief um '+clock(st.at||0)+' Uhr';cls='s-played'}
    else if(st.state==='wished'){txt='schon gew&#252;nscht'+(st.count>1?' ('+st.count+'x)':'');cls='s-wished';dis=' disabled'}
    else txt=r.uploader||''
    return'<div class=r><div class=r-i><div class=r-t>'+esc(r.title)+'</div><div class="r-s '+cls+'">'+txt+'</div></div>'+
      '<button class=b'+dis+' onclick="wish('+i+',this)">W&#252;nschen</button></div>'
  }).join('')
}
function wish(i,btn){
  var r=(window._rs||[])[i]
  if(!r||_busy[r.url])return
  _busy[r.url]=1;btn.disabled=true;btn.textContent='&#8230;'
  ws.send(JSON.stringify({type:'wish_add',url:r.url,title:r.title}))
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

            elif t == "wish_add":
                url   = (msg.get("url") or "").strip()
                title = (msg.get("title") or "").strip()
                if not url.startswith("http"):
                    continue

                offen = [w for w in _state.get("wishes", []) if w.get("from") == who]
                if len(offen) >= _WISH_LIMIT_PER_CLIENT:
                    await websocket.send_text(json.dumps({"type": "wish_deny",
                        "text": f"Du hast schon {len(offen)} Wuensche offen. Warte, bis der DJ sie bearbeitet hat."}))
                    continue

                # Schon gewuenscht? Dann nur hochzaehlen — das zeigt dem DJ,
                # was die Leute wirklich hoeren wollen.
                vorhanden = next((w for w in _state.get("wishes", [])
                                  if w.get("url") == url or _title_matches(title, w.get("title", ""))), None)
                if vorhanden:
                    vorhanden["count"] = vorhanden.get("count", 1) + 1
                    save_wishes()
                    await push_wishes()
                    await websocket.send_text(json.dumps({"type": "wish_ack", "title": title}))
                    continue

                _wish_counter += 1
                wish = {"id": _wish_counter, "url": url, "title": title,
                        "from": who, "count": 1, "status": "neu",
                        "path": None, "error": "", "created_at": int(time.time())}
                _state.setdefault("wishes", []).append(wish)
                save_wishes()
                await push_wishes()
                await websocket.send_text(json.dumps({"type": "wish_ack", "title": title}))
                asyncio.create_task(_process_wish(wish))
    except Exception:
        pass

async def _do_wish_search(query: str, ws: WebSocket):
    base_args = ["--flat-playlist", "-j", "--no-playlist", "--quiet"]
    results = await _run_search_cmd(_yt(f"ytmsearch8:{query}", *base_args))
    if not results:
        results = await _run_search_cmd(_yt(f"ytsearch8:{query}", *base_args))
    results = [r for r in results if not _is_unwanted_result(r)]
    results.sort(key=lambda r: r.get("_score", 0), reverse=True)
    try:
        await ws.send_text(json.dumps({"type": "wish_results", "results": [
            {"url": r["url"], "title": r["title"], "uploader": r.get("uploader", ""),
             "status": _wish_title_status(r["title"])}
            for r in results[:8]
        ]}))
    except Exception:
        pass

@remote_app.get("/manifest.json")
async def remote_manifest():
    """Macht die Seite ueber "Zum Startbildschirm" zur eigenstaendigen App."""
    return JSONResponse({
        "name": "SynthiMIX Remote",
        "short_name": "SynthiMIX",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0a0f1a",
        "theme_color": "#0d1625",
        "icons": [],
    })

@remote_app.websocket("/ws")
async def remote_ws_endpoint(websocket: WebSocket):
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
                         "path": x.get("path",""), "duration_sec": x.get("duration_sec",0)}
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
            elif t == "set_normalize_volume":
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
    base_args = ["--flat-playlist", "-j", "--no-playlist", "--quiet"]
    if FFMPEG_DIR:
        base_args += ["--ffmpeg-location", FFMPEG_DIR]
    results = await _run_search_cmd(_yt(f"ytmsearch8:{query}", *base_args))
    if not results:
        results = await _run_search_cmd(_yt(f"ytsearch8:{query}", *base_args))
    results = [r for r in results if not _is_unwanted_result(r)]
    results.sort(key=lambda r: r.get("_score", 0), reverse=True)
    try:
        await ws.send_text(json.dumps({"type": "yt_results", "results": [
            {"url": r["url"], "title": r["title"], "uploader": r.get("uploader", ""), "duration": r.get("duration", 0)}
            for r in results[:8]
        ]}))
    except Exception:
        pass

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
        "queue": [{"title": t.get("title",""), "artist": t.get("artist",""),
                   "duration_sec": t.get("duration_sec", 0),
                   "played": t.get("played", False),
                   "path": t.get("path","")} for t in q],
    })

async def _send_remote_state(ws: WebSocket):
    await ws.send_text(_remote_state_payload())

async def _broadcast_remote_state():
    if not _remote_clients:
        return
    payload = _remote_state_payload()
    dead = set()
    for ws in _remote_clients:
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
    for ws in _remote_clients:
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
            "ip": ip, "port": _remote_port,
            "url": f"http://{ip}:{_remote_port}"
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
                         "ip": ip, "port": _remote_port,
                         "url": f"http://{ip}:{_remote_port}"})
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
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
