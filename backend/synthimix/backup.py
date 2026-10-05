"""Sicherung und Wiederherstellung aller Daten in einer Datei.

Der alte Export in den Einstellungen nahm nur ein paar Schalter mit. Eine
Sicherung enthaelt alles, was sich nicht neu einlesen laesst: Bibliothek samt
Analysen, Warteschlange, Verlauf, Wiedergaben, Notizen, Wuensche, verfolgte
Playlists, die Schluessel der Dienste, die eigenen Playlists und die
Einstellungen der Oberflaeche (liefert das Fenster mit).

Wiederherstellen laeuft in zwei Schritten: stage() legt die Datei im
Datenordner ab, das Programm startet neu, und restore_pending() spielt sie vor
dem Laden ein — so ueberschreibt nichts Laufendes die frischen Dateien.
"""
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from .core import _state
from . import core, store

FORMAT = 1
MARK = "wiederherstellen.zip"                    # im Datenordner: beim naechsten Start einspielen
UI_PENDING = "oberflaeche_wiederherstellen.json"  # Einstellungen der Oberflaeche, holt sich das Fenster
PL_PENDING = "playlists_wiederherstellen"        # Playlists, bis ihr Ordner feststeht
# Laesst sich jederzeit neu holen — gehoert nicht in die Sicherung
_SKIP = {"changelog_cache.json", "ytm_search_cache.json", UI_PENDING}


def _data_files() -> list[Path]:
    return sorted(p for p in core.BASE_DIR.glob("*.json") if p.name not in _SKIP)


def create(dest_dir: str, ui: dict | None = None) -> dict:
    """Sicherung in dest_dir schreiben. Liefert {ok, path, tracks, playlists, size} oder {ok: False, error}."""
    dest = Path(dest_dir)
    if not dest.is_dir():
        return {"ok": False, "error": "Ordner nicht gefunden"}
    # alles Gemessene erst auf die Platte
    for save in (store.save_library, store.save_queue, store.save_settings, store.save_history, store.save_play_log):
        try:
            save()
        except Exception as e:
            print(f"[sicherung] {save.__name__}: {e}", flush=True)
    target = dest / f"SynthiMIX-Sicherung-{time.strftime('%Y-%m-%d_%H%M')}.zip"
    pls = sorted(Path(core.PLAYLISTS_DIR).glob("*.m3u")) if Path(core.PLAYLISTS_DIR).exists() else []
    meta = {"app": "SynthiMIX", "format": FORMAT, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tracks": len(_state.get("library", [])), "playlists": len(pls),
            "data_dir": str(core.BASE_DIR), "download_dir": _state.get("download_dir", ""),
            "portable_drive": _state.get("portable_drive", "")}
    tmp = target.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("sicherung.json", json.dumps(meta, ensure_ascii=False))
            z.writestr("oberflaeche.json", json.dumps(ui if isinstance(ui, dict) else {}, ensure_ascii=False))
            for p in _data_files():
                z.write(p, f"daten/{p.name}")
            for p in pls:
                z.write(p, f"playlists/{p.name}")
        tmp.replace(target)
    except Exception as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": str(target), "tracks": meta["tracks"], "playlists": len(pls), "size": target.stat().st_size}


def inspect(zip_path: str) -> dict:
    """Was steckt in der Datei? {ok, created, tracks, playlists, …} oder {ok: False, error}."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            meta = json.loads(z.read("sicherung.json"))
            names = z.namelist()
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return {"ok": False, "error": "Das ist keine SynthiMIX-Sicherung"}
    if meta.get("app") != "SynthiMIX" or int(meta.get("format") or 0) > FORMAT:
        return {"ok": False, "error": "Diese Sicherung stammt aus einer neueren Version — bitte SynthiMIX aktualisieren"}
    return {"ok": True, "path": zip_path, "created": meta.get("created", ""), "tracks": meta.get("tracks", 0),
            "playlists": sum(1 for n in names if n.startswith("playlists/") and n.endswith(".m3u")),
            "download_dir": meta.get("download_dir", "")}


def stage(zip_path: str) -> dict:
    """Sicherung fuer den naechsten Start bereitlegen."""
    info = inspect(zip_path)
    if not info["ok"]:
        return info
    try:
        shutil.copyfile(zip_path, core.BASE_DIR / MARK)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def restore_pending() -> bool:
    """Beim Start, vor dem Laden: liegt eine Sicherung bereit, die jetzigen
    Daten in "Sicherung <Datum>" beiseitelegen und die Sicherung einspielen."""
    mark = core.BASE_DIR / MARK
    if not mark.exists():
        return False
    try:
        if not inspect(str(mark))["ok"]:
            raise ValueError("keine gueltige Sicherung")
        keep = core.BASE_DIR / f"Sicherung {time.strftime('%Y-%m-%d %H-%M-%S')}"
        keep.mkdir(parents=True, exist_ok=True)
        for p in core.BASE_DIR.glob("*.json"):
            shutil.move(str(p), str(keep / p.name))
        pl_tmp = core.BASE_DIR / PL_PENDING
        shutil.rmtree(pl_tmp, ignore_errors=True)
        with zipfile.ZipFile(mark) as z:
            for n in z.namelist():
                base = os.path.basename(n)                      # nie aus dem Datenordner hinaus schreiben
                if n.startswith("daten/") and base.endswith(".json") and base not in _SKIP:
                    (core.BASE_DIR / base).write_bytes(z.read(n))
                elif n.startswith("playlists/") and base.endswith(".m3u"):
                    pl_tmp.mkdir(parents=True, exist_ok=True)
                    (pl_tmp / base).write_bytes(z.read(n))
                elif n == "oberflaeche.json":
                    (core.BASE_DIR / UI_PENDING).write_bytes(z.read(n))
        print(f"[sicherung] eingespielt; bisherige Daten liegen in {keep}", flush=True)
        return True
    except Exception as e:
        print(f"[sicherung] Wiederherstellen fehlgeschlagen: {e}", flush=True)
        return False
    finally:
        try:
            mark.unlink()
        except OSError:
            pass


def finish_playlists() -> int:
    """Nach dem Laden (der Playlist-Ordner steht fest): die Playlists aus der
    Sicherung einsetzen — gleichnamige ersetzt die Sicherung, andere bleiben."""
    pl_tmp = core.BASE_DIR / PL_PENDING
    if not pl_tmp.exists():
        return 0
    n = 0
    try:
        Path(core.PLAYLISTS_DIR).mkdir(parents=True, exist_ok=True)
        for p in pl_tmp.glob("*.m3u"):
            shutil.copyfile(p, Path(core.PLAYLISTS_DIR) / p.name)
            n += 1
        shutil.rmtree(pl_tmp, ignore_errors=True)
    except OSError as e:
        print(f"[sicherung] Playlists: {e}", flush=True)
    return n


def ui_pending() -> dict | None:
    p = core.BASE_DIR / UI_PENDING
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def ui_done():
    try:
        (core.BASE_DIR / UI_PENDING).unlink()
    except OSError:
        pass
