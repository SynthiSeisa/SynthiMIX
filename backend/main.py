"""
YT-Downloader backend — FastAPI + WebSocket
Replaces the PyQt5 main thread with a clean async server.
"""
import asyncio, json, os, sys, math, subprocess, re, base64, random, time
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
if _data_dir_arg:
    BASE_DIR = Path(_data_dir_arg)
    BASE_DIR.mkdir(parents=True, exist_ok=True)
else:
    BASE_DIR = Path(__file__).parent.parent

QUEUE_FILE    = BASE_DIR / "queue.json"
SETTINGS_FILE = BASE_DIR / "settings.json"
LIB_CACHE     = BASE_DIR / "library_cache.json"
PLAYLISTS_DIR = BASE_DIR / "playlists"
HISTORY_FILE  = BASE_DIR / "history.json"
PLAY_LOG_FILE = BASE_DIR / "play_log.json"
NOTES_FILE    = BASE_DIR / "notes.json"

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
    asyncio.create_task(_watcher_loop())
    asyncio.create_task(_auto_scan_loop())
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
    loop    = asyncio.get_event_loop()
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
    await loop.run_in_executor(None, _sync)
    try:
        await ws.send_text(json.dumps({"type": "tools_info", **info}))
    except Exception:
        pass

async def _update_ytdlp(ws: WebSocket):
    import urllib.request as _req
    loop = asyncio.get_running_loop()
    async def _send(text, pct):
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
    r'\b(live\s*(at|in|from|version|performance|session|recording|concert|show)?'
    r'|concert|tour\s*\d{4}?|unplugged|acoustic\s+live|live\s+acoustic'
    r'|at\s+the\s+\w+\s+(?:arena|stadium|festival|hall|theater|theatre))\b',
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
            batch = await _run_search_cmd([YTDLP, f"ytmsearch15:{sq}"] + base_args)
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
                batch = await _run_search_cmd([YTDLP, f"ytsearch15:{sq} song"] + base_args)
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
        }))

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
            asyncio.create_task(scan_folder(folder))

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
        url = msg.get("url", "").strip()
        fmt = msg.get("format", "mp3-best")
        if url:
            asyncio.create_task(run_download(url, fmt))

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
        for t in _state["queue"]:
            p = t.get("path")
            if p in seen_paths:
                continue
            norm = _norm_queue_title(t.get("title", ""))
            if norm and norm in seen_titles:
                continue
            seen_paths.add(p)
            if norm:
                seen_titles.add(norm)
            deduped.append(t)
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
            for i, t in zip(idxs, shuffled):
                q[i] = t
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
                        tracks.append({"path": str(entry), "name": entry.stem})
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
                        tree["files"].append({"path": str(entry), "name": entry.stem})
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

async def _auto_scan_loop():
    while True:
        interval = _state.get("auto_scan_interval_min", 0)
        if interval > 0:
            await asyncio.sleep(interval * 60)
            for folder in list(_state.get("watched_folders", [])):
                if os.path.isdir(folder):
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

async def _watcher_loop():
    """Periodically check watched folders for new audio files."""
    await asyncio.sleep(15)   # initial delay — let app settle
    while True:
        try:
            folders = list(_state.get("watched_folders", []))
            if folders:
                existing = {t["path"] for t in _state["library"]}
                new_tracks: list[dict] = []
                loop = asyncio.get_running_loop()
                for folder in folders:
                    if not os.path.isdir(folder):
                        continue
                    tracks = await loop.run_in_executor(None, _scan_sync, folder)
                    for t in tracks:
                        if t["path"] not in existing:
                            new_tracks.append(t)
                            existing.add(t["path"])
                if new_tracks:
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
        cmd.append(f"ytsearch1:{url}")   # exactly 1 search result
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
                results.append({
                    "url":      url,
                    "title":    item.get("title", ""),
                    "uploader": item.get("uploader") or item.get("channel") or "",
                    "duration": item.get("duration") or 0,
                    "abr":      item.get("abr") or item.get("audio_bitrate") or 0,
                    "_score":   _score_result(item),
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
            YTDLP, *base_args, playlist_url,
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
        results = await _run_search_cmd([YTDLP, f"ytmsearch5:{query}"] + search_base)
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


async def do_search(query: str, ws: WebSocket):
    base_args = ["--flat-playlist", "-j", "--no-playlist", "--quiet"]
    if FFMPEG_DIR:
        base_args += ["--ffmpeg-location", FFMPEG_DIR]

    results = await _run_search_cmd([YTDLP, f"ytmsearch10:{query}"] + base_args)
    if not results:
        results = await _run_search_cmd([YTDLP, f"ytsearch10:{query}"] + base_args)

    results.sort(key=lambda r: r["_score"], reverse=True)
    for r in results:
        del r["_score"]

    await ws.send_text(json.dumps({"type": "search_results", "query": query, "results": results}))

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
                YTDLP, '--print', 'playlist_title', '--playlist-items', '1',
                '--no-warnings', '--quiet', '--encoding', 'utf-8', url,
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
                        line = raw.decode("utf-8", errors="replace").strip()
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
                line = raw.decode("utf-8", errors="replace").strip()
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

            except Exception:
                pass

        await proc.wait()
        ok = proc.returncode in (0, 1)

        # finish current track
        if cur is not hdr:
            _done(cur, ok=ok)

        # finish header
        hdr["progress"]    = 100 if ok else hdr["progress"]
        hdr["status"]      = "done" if ok else "error"
        hdr["status_text"] = (f"✓ {track_total} Tracks" if track_total and ok
                              else "✓ Fertig" if ok else "✗ Fehler")
        if not ok:
            hdr["error_msg"] = "Download fehlgeschlagen"

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
from fastapi.responses import HTMLResponse

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
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>SynthiMIX Remote</title>
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
</style>
</head>
<body>
<div class="hdr">
  <span class="logo">Synthi<span>MIX</span></span>
  <span class="sub">Remote</span>
  <div id="dot" class="dot"></div>
</div>
<div class="np">
  <div id="npT" class="np-t">&#8211;</div>
  <div id="npA" class="np-a">&#8211;</div>
</div>
<div class="ctrls">
  <button class="btn" onclick="send({type:'play_prev'})">&#9198;</button>
  <button id="pb" class="btn big" onclick="toggle()">&#9654;</button>
  <button class="btn" onclick="send({type:'play_next'})">&#9197;</button>
</div>
<div class="vol">
  <div class="vol-h"><span>Lautst&#228;rke</span><span id="vv">80%</span></div>
  <input type="range" id="vr" min="0" max="100" value="80" oninput="onVol(this.value)" onchange="flushVol()">
</div>
<div class="sec">
  <div class="sec-h">WARTESCHLANGE &nbsp;<span id="qc" style="font-weight:400;color:#3a5070">0</span></div>
  <div id="qw" class="q-wrap"><div id="ql"></div></div>
</div>
<div class="sec" style="padding-bottom:20px">
  <div class="sec-h">BIBLIOTHEK</div>
  <div class="s-row"><input class="inp" id="si" placeholder="Titel oder K&#252;nstler&#8230;" type="search" oninput="onS(this.value)"></div>
  <div id="sr"></div>
</div>
<script>
var st={playing:false,current_idx:-1,volume:80,queue:[]},ws,_vt,_res=[]
function conn(){
  ws=new WebSocket('ws://'+location.hostname+':8080/ws')
  ws.onopen=function(){dot(true)}
  ws.onclose=function(){dot(false);setTimeout(conn,2000)}
  ws.onerror=function(){ws.close()}
  ws.onmessage=function(e){
    var m=JSON.parse(e.data)
    if(m.type==='state'){st=m;render()}
    else if(m.type==='search_results'){showRes(m.results||[])}
  }
}
function dot(on){document.getElementById('dot').className='dot'+(on?' on':'')}
function send(o){if(ws&&ws.readyState===1)ws.send(JSON.stringify(o))}
function toggle(){send({type:st.playing?'pause':'resume'})}
var _pv=null
function onVol(v){document.getElementById('vv').textContent=v+'%';_pv=+v;clearTimeout(_vt);_vt=setTimeout(flushVol,120)}
function flushVol(){if(_pv!=null){send({type:'set_volume',value:_pv});_pv=null}}
function fmt(s){if(!s)return'';var m=Math.floor(s/60);return m+':'+(Math.floor(s%60)+'').padStart(2,'0')}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function rm(i){if(confirm('Entfernen?'))send({type:'queue_remove',index:i})}
function addLib(i){if(_res[i]){send({type:'queue_append',path:_res[i].path});document.getElementById('si').value='';document.getElementById('sr').innerHTML='';_res=[]}}
var _st=null
function onS(q){clearTimeout(_st);if(!q.trim()){document.getElementById('sr').innerHTML='';return}_st=setTimeout(function(){send({type:'search_library',query:q})},300)}
function showRes(rs){
  _res=rs
  var el=document.getElementById('sr')
  if(!rs.length){el.innerHTML='<div class=empty>Keine Ergebnisse</div>';return}
  el.innerHTML=rs.map(function(r,i){
    return'<div class=ri><div class=ri-info><div class=ri-t>'+esc(r.title||'&#8211;')+'</div><div class=ri-a>'+esc(r.artist||'')+(r.duration_sec?' &middot; '+fmt(r.duration_sec):'')+'</div></div><button class=add-b onclick="addLib('+i+')">+</button></div>'
  }).join('')
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
/* ── Render ─────────────────────────────────────────────────────────────── */
function render(){
  var q=st.queue||[],ci=st.current_idx,cur=q[ci]
  document.getElementById('npT').textContent=cur&&cur.title?cur.title:'–'
  document.getElementById('npA').textContent=cur&&cur.artist?cur.artist:''
  document.getElementById('pb').innerHTML=st.playing?'⏸':'▶'
  document.getElementById('vr').value=st.volume
  document.getElementById('vv').textContent=st.volume+'%'
  document.getElementById('qc').textContent=q.length
  document.getElementById('ql').innerHTML=q.length?q.map(function(t,i){
    var a=i===ci,p=t.played&&!a
    return'<div class="qi'+(a?' cur':'')+'"><div class=dh ontouchstart="dhStart(event,'+i+')" ontouchmove="dhMove(event)" ontouchend="dhEnd(event)">☰</div><div class=qi-info><div class="qi-t'+(a?' a':p?' p':'')+'">'+esc(t.title||'–')+'</div><div class=qi-d>'+fmt(t.duration_sec)+'</div></div><button class=rmb onclick="rm('+i+')">✕</button></div>'
  }).join(''):'<div class=empty>Warteschlange leer</div>'
}
conn()
</script>
</body>
</html>"""

remote_app = FastAPI()
remote_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@remote_app.get("/")
async def remote_index():
    return HTMLResponse(_REMOTE_HTML)

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
            elif t in ("pause", "resume", "play_at", "play_next", "play_prev", "set_volume"):
                await handle_message(websocket, msg)
    except Exception:
        pass
    finally:
        _remote_clients.discard(websocket)

async def _send_remote_state(ws: WebSocket):
    q = _state.get("queue", [])
    await ws.send_text(json.dumps({
        "type": "state",
        "playing":     _state.get("playing", False),
        "current_idx": _state.get("current_idx", -1),
        "volume":      _state.get("volume", 80),
        "queue": [{"title": t.get("title",""), "artist": t.get("artist",""),
                   "duration_sec": t.get("duration_sec", 0),
                   "played": t.get("played", False),
                   "path": t.get("path","")} for t in q],
    }))

async def _broadcast_remote_state():
    if not _remote_clients:
        return
    q = _state.get("queue", [])
    payload = json.dumps({
        "type": "state",
        "playing":     _state.get("playing", False),
        "current_idx": _state.get("current_idx", -1),
        "volume":      _state.get("volume", 80),
        "queue": [{"title": t.get("title",""), "artist": t.get("artist",""),
                   "duration_sec": t.get("duration_sec", 0),
                   "played": t.get("played", False),
                   "path": t.get("path","")} for t in q],
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
        _remote_server = uvicorn.Server(config)
        asyncio.create_task(_remote_server.serve())
        print(f"[remote] gestartet auf http://{ip}:{_remote_port}", flush=True)
        await broadcast({"type": "remote_status", "running": True,
                         "ip": ip, "port": _remote_port,
                         "url": f"http://{ip}:{_remote_port}"})
    except Exception as e:
        print(f"[remote] Fehler beim Starten: {e}", flush=True)
        await requester.send_text(json.dumps({"type": "remote_status", "running": False, "error": str(e)}))

async def _stop_remote_server():
    global _remote_server
    if _remote_server:
        _remote_server.should_exit = True
        _remote_server = None
        print("[remote] gestoppt", flush=True)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
