"""DJ-Set zwischen rekordbox und SynthiMIX: Playlist-Datei (.m3u8) einlesen,
Lautheit je Titel in Set-Reihenfolge, Playlist fuer rekordbox ausgeben.

rekordbox: Rechtsklick auf eine Playlist → "Playlist exportieren" schreibt
eine .m3u8 mit den Dateipfaden; Datei → Importieren → Playlist liest eine.
"""
import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from .core import _state
from . import core, library, media

EXPORT_SUBDIR = "rekordbox"
_MEASURE_PARALLEL = 4
# Lautheit von Titeln, die nicht in der Bibliothek sind: (Pfad, Groesse, mtime) → LUFS
_measured: dict[tuple, float] = {}
_cancel: set[str] = set()


def _read_text(path: str) -> str:
    """.m3u8 ist UTF-8; eine alte .m3u kann in der Windows-Codepage stehen."""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _entry_path(line: str, base: Path) -> str:
    """Zeile einer Playlist-Datei → Dateipfad (file://-Adresse, relativ zur Playlist)."""
    if re.match(r"^file:", line, re.I):
        u = urlparse(line)
        p = unquote(u.path)
        if u.netloc and u.netloc.lower() != "localhost":
            p = f"//{u.netloc}{p}"                      # Netzlaufwerk
        elif re.match(r"^/[A-Za-z]:", p):
            p = p[1:]
        line = p
    p = Path(line)
    if not p.is_absolute():
        p = base / p
    return os.path.normpath(str(p))


def parse_playlist_file(path: str) -> tuple[list[dict], list[str]]:
    """([{path, title, duration_sec}] in der Reihenfolge der Datei, [nicht gefundene Pfade])."""
    base = Path(path).parent
    found, missing, title, dur = [], [], "", 0
    for line in _read_text(path).splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            parts = line[8:].split(",", 1)
            try: dur = int(float(parts[0]))
            except ValueError: dur = 0
            title = parts[1].strip() if len(parts) > 1 else ""
        elif line and not line.startswith("#"):
            p = _entry_path(line, base)
            if os.path.isfile(p):
                found.append({"path": p, "title": title or Path(p).stem, "duration_sec": max(0, dur)})
            else:
                missing.append(p)
            title, dur = "", 0
    return found, missing


def _lib_by_path() -> dict:
    return {os.path.normcase(lt.get("path") or ""): lt for lt in _state["library"]}


def import_playlist(src: str) -> dict:
    """Playlist-Datei (rekordbox, Virtual DJ, …) als SynthiMIX-Playlist anlegen.
    Titel, die in der Bibliothek stehen, bekommen deren Schreibweise des Pfads —
    sonst faende die Playlist sie spaeter nicht (M:\\musik gegen M:\\Musik)."""
    p = Path(src or "")
    if not p.is_file() or p.suffix.lower() not in (".m3u8", ".m3u"):
        return {"ok": False, "error": "Bitte eine Playlist-Datei (.m3u8 oder .m3u) wählen."}
    try:
        tracks, missing = parse_playlist_file(str(p))
    except OSError as e:
        return {"ok": False, "error": f"Datei nicht lesbar: {e}"}
    if not tracks:
        return {"ok": False, "missing": missing[:200],
                "error": ("Keiner der Titel wurde gefunden — liegt die Musik auf einer Platte, die gerade nicht "
                          "angeschlossen ist?" if missing else "In der Datei stehen keine Titel.")}
    lib = _lib_by_path()
    name = library._safe_pl_name(p.stem) or "Playlist"
    core.PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
    dest, n = core.PLAYLISTS_DIR / (name + ".m3u"), 2
    while dest.exists():                                 # nichts ueberschreiben
        dest = core.PLAYLISTS_DIR / f"{name} ({n}).m3u"
        n += 1
    with open(dest, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for t in tracks:
            lt = lib.get(os.path.normcase(t["path"]))
            path = lt["path"] if lt else t["path"]
            dur = int((lt or {}).get("duration_sec") or t["duration_sec"] or -1)
            f.write(f"#EXTINF:{dur},{(lt or {}).get('title') or t['title']}\n{path}\n")
    return {"ok": True, "path": str(dest), "name": dest.stem, "total": len(tracks),
            "missing": missing[:200], "missing_n": len(missing),
            "unknown": sum(1 for t in tracks if os.path.normcase(t["path"]) not in lib)}


def export_playlist(pl_path: str) -> dict:
    """SynthiMIX-Playlist als .m3u8 (UTF-8) fuer rekordbox, in der Reihenfolge
    der Playlist. Liegt unter <Playlisten-Ordner>\\rekordbox."""
    src = Path(pl_path or "")
    if not src.is_file():
        return {"ok": False, "error": "Playlist nicht gefunden."}
    tracks = library._parse_m3u(str(src))
    if not tracks:
        return {"ok": False, "error": "Die Playlist ist leer (oder ihre Titel sind gerade nicht erreichbar)."}
    lib = _lib_by_path()
    out = src.parent / EXPORT_SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    dest = out / (src.stem + ".m3u8")
    with open(dest, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("#EXTM3U\n")
        for t in tracks:
            lt = lib.get(os.path.normcase(t["path"])) or {}
            artist, title = (lt.get("artist") or "").strip(), lt.get("title") or t["title"]
            name = f"{artist} - {title}" if artist and not title.lower().startswith(artist.lower()) else title
            f.write(f"#EXTINF:{int(lt.get('duration_sec') or t['duration_sec'] or -1)},{name}\n{t['path']}\n")
    return {"ok": True, "path": str(dest), "total": len(tracks)}


def _file_key(path: str) -> tuple | None:
    try:
        st = os.stat(path)
        return (os.path.normcase(path), st.st_size, int(st.st_mtime))
    except OSError:
        return None


def _rows(pl_path: str) -> list[dict]:
    lib = _lib_by_path()
    rows = []
    for t in library._parse_m3u(pl_path):
        lt = lib.get(os.path.normcase(t["path"]))
        lufs = (lt or {}).get("lufs")
        if lufs is None or lufs <= -90:
            lufs = _measured.get(_file_key(t["path"]))
        rows.append({"path": t["path"], "title": (lt or {}).get("title") or t["title"],
                     "artist": (lt or {}).get("artist") or "", "duration_sec": (lt or {}).get("duration_sec") or t["duration_sec"],
                     "lufs": lufs if lufs is not None and lufs > -90 else None, "in_library": lt is not None})
    return rows


async def loudness(pl_path: str, ws):
    """Lautheit aller Titel einer Playlist in Set-Reihenfolge. Was noch nie
    gemessen wurde, wird jetzt gemessen (parallel, abbrechbar)."""
    send = core.sender(ws)
    loop = asyncio.get_running_loop()
    _cancel.discard(pl_path)
    if not os.path.isfile(pl_path):
        await send("playlist_loudness", path=pl_path, tracks=[], measuring=0, error="Playlist nicht gefunden.")
        return
    rows = await loop.run_in_executor(None, _rows, pl_path)
    todo = [r for r in rows if r["lufs"] is None]
    await send("playlist_loudness", path=pl_path, tracks=rows, measuring=len(todo))
    if not todo:
        return
    lib = _lib_by_path()
    sem = asyncio.Semaphore(_MEASURE_PARALLEL)
    changed = False

    async def one(r):
        nonlocal changed
        async with sem:
            if pl_path in _cancel:
                return
            try:
                val, main_l = await loop.run_in_executor(None, media._measure_loudness_sync, r["path"])
            except Exception:
                val, main_l = None, None
            if val is not None and val > -90:
                lt = lib.get(os.path.normcase(r["path"]))
                if lt is not None:
                    lt["lufs"] = val
                    if main_l is not None:
                        lt["lufs_main"] = main_l
                    changed = True
                else:
                    k = _file_key(r["path"])
                    if k:
                        _measured[k] = val
            await send("playlist_loudness_one", path=pl_path, track=r["path"],
                       lufs=val if val is not None and val > -90 else None)

    await asyncio.gather(*(one(r) for r in todo))
    if changed:
        from . import store
        store.save_library()
        await core.push_library()
    await send("playlist_loudness_done", path=pl_path, cancelled=pl_path in _cancel)
    _cancel.discard(pl_path)
