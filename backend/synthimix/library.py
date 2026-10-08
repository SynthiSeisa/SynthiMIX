"""Bibliothek: Ordner einlesen, beobachten, Papierkorb, Playlists, Warteschlangen-Duplikate."""
import asyncio
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from .core import _state
from . import core, media, store

async def _fileid_scan_task():
    """Einmalig nach dem Start: Datei-Kennung fuer Eintraege ohne — damit
    verknuepfte Dateien (Hardlinks) nicht als Duplikate erscheinen."""
    await asyncio.sleep(30)
    todo = [lt for lt in _state["library"] if lt.get("path") and not lt.get("fid") and not lt.get("missing")]
    if not todo:
        return
    loop = asyncio.get_running_loop()
    ids = await loop.run_in_executor(None, lambda: [media._file_id(lt["path"]) for lt in todo])
    n = 0
    for lt, fid in zip(todo, ids):
        if fid:
            lt["fid"] = fid
            n += 1
    if n:
        store.save_library()
        await core.push_library()
        print(f"[library] Datei-Kennung fuer {n} Titel ergaenzt", flush=True)

# ── playlist helpers ─────────────────────────────────────────────────────────
PLAYLISTS_SUBDIR = "playlists"


def sync_playlists_dir() -> int:
    """Eigene Playlists (.m3u) liegen im Download-Ordner unter "playlists" —
    bei der Musik, nicht im Datenordner des Programms. Was am bisherigen Ort
    liegt (Datenordner oder der vorige Download-Ordner), zieht mit um.
    Ist der Download-Ordner nicht erreichbar (Platte ab), bleibt es beim alten
    Ort; beim naechsten Mal wird nachgezogen. Liefert die Zahl der umgezogenen."""
    dl = Path(_state.get("download_dir") or (core.BASE_DIR / "Downloads"))
    new, old = dl / PLAYLISTS_SUBDIR, Path(core.PLAYLISTS_DIR)
    try:
        if old.resolve() == new.resolve():
            return 0
        new.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[playlists] {new}: {e}", flush=True)
        return 0
    moved = 0
    if old.exists():
        for f in sorted(old.glob("*.m3u")):
            target = new / f.name
            n = 2
            while target.exists():                   # gleicher Name schon da: nichts ueberschreiben
                target = new / f"{f.stem} ({n}).m3u"
                n += 1
            try:
                shutil.move(str(f), str(target))
                moved += 1
            except OSError as e:
                print(f"[playlists] {f.name}: {e}", flush=True)
    core.PLAYLISTS_DIR = new
    if moved:
        print(f"[playlists] {moved} Playlist(s) nach {new} umgezogen", flush=True)
    return moved


def _get_playlists() -> list[dict]:
    if not core.PLAYLISTS_DIR.exists():
        return []
    result = []
    for p in sorted(core.PLAYLISTS_DIR.glob("*.m3u")):
        try:
            count = sum(1 for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
                        if ln.strip() and not ln.startswith("#"))
        except Exception:
            count = 0
        result.append({"name": p.stem, "path": str(p), "track_count": count})
    return result

def _safe_pl_name(name: str) -> str:
    """Playlist-Name als Dateiname (wie download._save_as_playlist)."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', (name or "").strip()).strip(' .')[:80]

def _rename_playlist(path: str, name: str) -> dict:
    """SynthiMIX-Playlist umbenennen. Gehoert sie zu einer verfolgten Playlist,
    merkt sich diese den Namen (sonst legte die naechste Pruefung die Playlist
    wieder unter dem YouTube-Namen an); die .m3u8 im Download-Ordner zieht mit.
    Der Download-Ordner selbst bleibt (keine Musikdateien verschieben)."""
    safe = _safe_pl_name(name)
    src = Path(path or "")
    if not safe:
        return {"ok": False, "error": "Bitte einen Namen eingeben."}
    try:
        inside = src.parent.resolve() == Path(core.PLAYLISTS_DIR).resolve()
    except OSError:
        inside = False
    if not inside or not src.is_file() or src.suffix.lower() != ".m3u":
        return {"ok": False, "error": "Playlist nicht gefunden."}
    dst = src.with_name(safe + ".m3u")
    if dst.exists() and os.path.normcase(str(dst.resolve())) != os.path.normcase(str(src.resolve())):
        return {"ok": False, "error": f"„{safe}“ gibt es schon."}
    old = src.stem
    os.rename(src, dst)
    out_dir = Path(_state.get("download_dir") or (core.BASE_DIR / "Downloads"))
    for f in _state.get("followed", []):
        if _safe_pl_name(f.get("name") or f.get("title") or "") != old:
            continue
        f["name"] = name.strip()
        m3u8 = (out_dir / f["folder"] if f.get("folder") else out_dir) / (old + ".m3u8")
        try:
            if m3u8.is_file():
                os.rename(m3u8, m3u8.with_name(safe + ".m3u8"))
        except OSError as e:
            print(f"[playlist] .m3u8 nicht umbenannt: {e}", flush=True)
    return {"ok": True, "path": str(dst), "old": str(src), "name": dst.stem}

def _rename_followed(url: str, name: str) -> dict:
    """Eigener Name fuer eine verfolgte Playlist (leer = wieder der YouTube-Name).
    Die zugehoerige SynthiMIX-Playlist wird mit umbenannt."""
    f = next((x for x in _state.get("followed", []) if x.get("url") == url), None)
    if f is None:
        return {"ok": False, "error": "Nicht gefunden."}
    new = (name or "").strip() or f.get("title") or ""
    old_file = Path(core.PLAYLISTS_DIR) / (_safe_pl_name(f.get("name") or f.get("title") or "") + ".m3u")
    if old_file.is_file():
        res = _rename_playlist(str(old_file), new)
        if not res.get("ok"):
            return res
    if (name or "").strip() and name.strip() != f.get("title"):
        f["name"] = name.strip()
    else:
        f.pop("name", None)
    return {"ok": True}

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

def _is_audio_file(name: str) -> bool:
    """Audio-Datei, aber keine liegengebliebene Zwischendatei (core.is_temp_audio)."""
    return Path(name).suffix.lower() in AUDIO_EXTS and not core.is_temp_audio(name)

# ── Einlesen ─────────────────────────────────────────────────────────────────
# Frueher: jede Datei nacheinander pruefen (ffprobe), alles erst am Schluss in
# die Bibliothek — bei 3000 Titeln auf einer externen Platte viele Minuten ohne
# Fortschritt, und wer das Programm in der Zeit schloss, verlor alles (gemeldet
# 10/2026: "nur 120 von 3000 eingelesen"). Jetzt: Dateien erst nur auflisten,
# unveraenderte ueberspringen, den Rest parallel pruefen und in Portionen
# eintragen, mit Fortschritt und Zwischenspeichern.
_SCAN_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="scan")
_SCAN_CHUNK = 48
_scan_busy = 0                 # laufende Einlese-Vorgaenge (der Waechter wartet solange)
scan_unreadable: list[str] = []   # was beim letzten Einlesen nicht lesbar war (Diagnose)
_UPDATE_KEYS = ("title", "artist", "album_artist", "folder", "ext",
                "duration_sec", "bitrate_kbps", "comment", "mtime")


def _list_audio_sync(folder: str, recursive: bool) -> list[tuple[str, int]]:
    """Audiodateien eines Ordners mit Aenderungszeit — ohne sie zu oeffnen."""
    out: list[tuple[str, int]] = []

    def unreadable(e):
        print(f"[scan] nicht lesbar: {e}", flush=True)
        if len(scan_unreadable) < 200:
            scan_unreadable.append(str(getattr(e, "filename", None) or e))
    if recursive:
        dirs = os.walk(folder, onerror=unreadable)
    else:
        try:
            dirs = [(folder, [], os.listdir(folder))]
        except OSError as e:
            unreadable(e)
            dirs = []
    for root, _, files in dirs:
        if _is_excluded(root):
            continue
        for f in files:
            if _is_audio_file(f):
                full = str(Path(os.path.join(root, f)))  # normalisiert Slashes auf Windows
                try:
                    mt = int(os.path.getmtime(full))
                except OSError:
                    mt = 0
                out.append((full, mt))
    return out


def _entry_sync(path: str) -> dict:
    return media._make_library_entry(path, media._probe_sync(path))


async def _ingest(paths: list[str], label: str = "Scanne") -> tuple[int, int]:
    """Dateien pruefen und eintragen: parallel, in Portionen, mit Fortschritt.
    Schon vorhandene Eintraege werden aufgefrischt. Liefert (neu, aktualisiert)."""
    loop = asyncio.get_running_loop()
    added = updated = done = 0
    last_save = time.monotonic()
    for i in range(0, len(paths), _SCAN_CHUNK):
        chunk = paths[i:i + _SCAN_CHUNK]
        entries = await asyncio.gather(*(loop.run_in_executor(_SCAN_POOL, _entry_sync, p) for p in chunk),
                                       return_exceptions=True)
        by_path = {t["path"]: t for t in _state["library"]}      # frisch: inzwischen kann sich etwas getan haben
        for p, t in zip(chunk, entries):
            if isinstance(t, BaseException):
                print(f"[scan] {p}: {t}", flush=True)
                if len(scan_unreadable) < 200:
                    scan_unreadable.append(p)
                continue
            ex = by_path.get(p)
            if ex is None:
                _state["library"].append(t)
                by_path[p] = t
                added += 1
            else:
                for key in _UPDATE_KEYS:
                    if key in t:
                        ex[key] = t[key]
                ex.pop("missing", None)
                updated += 1
        done += len(chunk)
        if len(paths) > _SCAN_CHUNK:
            await core.broadcast({"type": "scan_status", "text": f"{label}… {done} / {len(paths)}"})
        # Zwischenstand sichern und zeigen — ein Abbruch kostet hoechstens eine halbe Minute
        if done < len(paths) and time.monotonic() - last_save > 20:
            store.save_library()
            await core.push_library()
            last_save = time.monotonic()
    return added, updated


def in_library_folders(folder: str) -> bool:
    """Gehoert der Ordner zur Bibliothek (ein eingelesener Ordner oder darin)?"""
    f = os.path.normcase(os.path.abspath(folder))
    for s in _scan_folders():
        b = os.path.normcase(os.path.abspath(s))
        if f == b or f.startswith(b + os.sep):
            return True
    return False


async def scan_folder(folder: str, recursive: bool | None = None):
    """recursive: None = wie in den Einstellungen. "Aktualisieren" an einem
    Ordner nimmt die Unterordner immer mit."""
    global _scan_busy
    if recursive is None:
        recursive = _state.get("scan_recursive", True)
    _scan_busy += 1
    try:
        await core.broadcast({"type": "scan_status", "text": "Scanne…"})
        loop = asyncio.get_running_loop()
        scan_unreadable.clear()
        found = await loop.run_in_executor(None, _list_audio_sync, folder, recursive)
        lib_by_path = {t["path"]: t for t in _state["library"]}
        scanned_paths = {p for p, _ in found}
        todo = []
        for p, mt in found:
            existing = lib_by_path.get(p)
            if existing is None or existing.get("mtime", 0) != mt:
                todo.append(p)                       # neu oder geaendert: pruefen
            else:
                existing.pop("missing", None)        # unveraendert und wieder da
        added, updated = await _ingest(todo)

        # Tracks aus diesem Ordner die nicht mehr auf der Platte liegen → als missing markieren
        folder_path = Path(folder)
        for lt in _state["library"]:
            lt_path = Path(lt.get("path", ""))
            try:
                lt_path.relative_to(folder_path)
            except ValueError:
                continue
            if not recursive and lt_path.parent != folder_path:
                continue                             # Unterordner wurden nicht angesehen
            was_missing = lt.get("missing", False)
            now_missing = lt["path"] not in scanned_paths
            if now_missing and not was_missing:
                lt["missing"] = True
            elif not now_missing and was_missing:
                lt.pop("missing", None)

        store.save_library()
        await core.push_library()
    finally:
        _scan_busy -= 1

    parts = [f"{len(_state['library'])} Tracks"]
    if added:    parts.append(f"+{added} neu")
    if updated:  parts.append(f"{updated} aktualisiert")
    if scan_unreadable: parts.append(f"{len(scan_unreadable)} nicht lesbar (Einstellungen → System → Diagnose)")
    await core.broadcast({"type": "scan_status", "text": "Fertig · " + " · ".join(parts)})
    await asyncio.sleep(4)
    await core.broadcast({"type": "scan_status", "text": ""})

def _move_many_to_trash(paths: list[str]) -> list[str]:
    """Mehrere Dateien in einem Rutsch in den Papierkorb. Liefert die Pfade,
    die danach noch da sind (in Benutzung, keine Rechte).

    Ein SHFileOperation-Aufruf fuer viele Dateien ist um ein Vielfaches
    schneller als einer je Datei (der Papierkorb wird nur einmal angefasst).
    """
    paths = [p for p in dict.fromkeys(paths) if p and os.path.exists(p)]
    if not paths:
        return []
    if sys.platform != "win32":
        for p in paths:
            try: os.remove(p)
            except OSError: pass
        return [p for p in paths if os.path.exists(p)]
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", wintypes.LPVOID), ("lpszProgressTitle", wintypes.LPCWSTR)]

    flags = 0x0040 | 0x0010 | 0x0004 | 0x0400   # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    for i in range(0, len(paths), 200):
        chunk = paths[i:i + 200]
        # Pfade durch \0 getrennt, doppelt nullterminiert (die letzte Null haengt ctypes an)
        buf = "\0".join(os.path.abspath(p) for p in chunk) + "\0"
        op = SHFILEOPSTRUCTW(None, 3, buf, None, flags, False, None, None)
        try:
            ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        except Exception as e:
            print(f"[trash] {e}", flush=True)
    return [p for p in paths if os.path.exists(p)]

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
    dl = _state.get("download_dir") or str(core.BASE_DIR / "Downloads")
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
    dl = _state.get("download_dir") or str(core.BASE_DIR / "Downloads")
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
            if _is_audio_file(f):
                full = str(Path(os.path.join(root, f)))  # normalisiert Slashes auf Windows
                probe = media._probe_sync(full)
                result.append(media._make_library_entry(full, probe))
    return result

def _find_new_audio_paths(folder: str, existing: set[str], recursive: bool) -> list[str]:
    """Fast filesystem scan — returns only paths NOT already in the library (no ffprobe)."""
    new_paths = []
    if recursive:
        for root, _, files in os.walk(folder):
            if _is_excluded(root):
                continue
            for f in files:
                if _is_audio_file(f):
                    full = str(Path(os.path.join(root, f)))
                    if full not in existing:
                        new_paths.append(full)
    else:
        try:
            for f in os.listdir(folder):
                if _is_audio_file(f):
                    full = str(Path(os.path.join(folder, f)))
                    if full not in existing and not _is_excluded(full):
                        new_paths.append(full)
        except OSError:
            pass
    return new_paths

async def _watcher_loop():
    """Periodically check watched folders for new audio files."""
    global _scan_busy
    await asyncio.sleep(15)   # initial delay — let app settle
    while True:
        try:
            folders   = [] if _scan_busy else _scan_folders()
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
                # Laeuft gerade ein Einlesen, traegt das die Titel ein — sonst doppelt
                if new_paths and not _scan_busy:
                    _scan_busy += 1
                    try:
                        added, _ = await _ingest(new_paths, "Lese neue Titel")
                        store.save_library()
                        await core.push_library()
                    finally:
                        _scan_busy -= 1
                    await core.broadcast({"type": "scan_status",
                                     "text": f"+{added} neue Tracks"})
                    await asyncio.sleep(4)
                    await core.broadcast({"type": "scan_status", "text": ""})
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
