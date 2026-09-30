"""Laufwerk umgezogen: Pfade in allen Daten umstellen.

Aendert sich der Laufwerksbuchstabe der Musik-Platte (M: -> X:), fehlen alle
Titel — die Eintraege bleiben aber mit BPM, Tonart, Wiedergaben usw. stehen
(nur als "fehlt" markiert). detect() findet, wohin sie gewandert sind,
apply() stellt Bibliothek, Warteschlange, Verlauf, Ordner und Zwischenspeicher
in einem Rutsch um. Die Musikdateien selbst werden nicht angefasst.
"""
import os
import string
from .core import _state
from . import beatgrid, core, media, store

# _state-Felder mit Pfaden (Titel, Ordner, Downloads, Wuensche …)
_KEYS = ("library", "queue", "play_log", "history", "favorites", "watched_folders",
         "excluded_folders", "download_dir", "followed", "followed_channels", "downloads", "wishes")


def _has_prefix(s, old: str) -> bool:
    if not isinstance(s, str) or len(s) < len(old) or s[:len(old)].lower() != old.lower():
        return False
    return len(s) == len(old) or old.endswith(("\\", "/")) or s[len(old)] in "\\/"


def _swap(s: str, old: str, new: str) -> str:
    return new + s[len(old):] if _has_prefix(s, old) else s


def _rewrite(obj, old: str, new: str) -> tuple:
    """Alle Strings (auch Schluessel), die mit old beginnen -> new. Listen und
    dicts werden an Ort und Stelle geaendert (laufende Downloads, Queue usw.
    halten Verweise darauf). Liefert (Objekt, Anzahl)."""
    if isinstance(obj, str):
        r = _swap(obj, old, new)
        return r, int(r != obj)
    n = 0
    if isinstance(obj, list):
        for i, x in enumerate(obj):
            y, c = _rewrite(x, old, new)
            if c and y is not x:
                obj[i] = y
            n += c
    elif isinstance(obj, dict):
        for k in list(obj.keys()):
            v2, c = _rewrite(obj[k], old, new)
            if c and v2 is not obj[k]:
                obj[k] = v2
            n += c
            if isinstance(k, str) and _has_prefix(k, old):
                obj[_swap(k, old, new)] = obj.pop(k)
                n += 1
    return obj, n


def _drives() -> list[str]:
    return [f"{c}:" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]


def detect() -> list[dict]:
    """Laufwerke, deren Titel fehlen, aber unter einem anderen Buchstaben da sind:
    [{from: "M:\\", to: "X:\\", count, found}]."""
    by_drive: dict[str, list[str]] = {}
    for lt in _state.get("library", []):
        p = lt.get("path") or ""
        d = os.path.splitdrive(p)[0]
        if len(d) == 2 and d[1] == ":":
            by_drive.setdefault(d.upper(), []).append(p)
    out, avail = [], None
    for d, paths in by_drive.items():
        if len(paths) < 5:
            continue
        step = max(1, len(paths) // 40)
        sample = paths[::step][:40]
        if sum(os.path.exists(p) for p in sample) > 0.2 * len(sample):
            continue                                  # Titel sind (grossteils) noch da
        if avail is None:
            avail = _drives()
        best = None
        for nd in avail:
            if nd == d:
                continue
            hits = sum(os.path.exists(nd + p[2:]) for p in sample)
            if hits and (best is None or hits > best[1]):
                best = (nd, hits)
        if best and best[1] >= max(3, 0.6 * len(sample)):
            out.append({"from": d + "\\", "to": best[0] + "\\", "count": len(paths),
                        "found": round(best[1] / len(sample), 2)})
    return out


def apply(old: str, new: str) -> dict:
    """Pfade old… -> new… umstellen. Liefert {paths, tracks, merged, playlists}."""
    n = 0
    for key in _KEYS:
        if key in _state:
            _state[key], c = _rewrite(_state[key], old, new)
            n += c
    # War der neue Ort schon eingelesen, gibt es Titel doppelt: zusammenfuehren,
    # der alte Eintrag behaelt Analyse und Wiedergaben, fehlende Felder kommen dazu
    lib, seen, merged = [], {}, 0
    for lt in _state.get("library", []):
        k = os.path.normcase(lt.get("path") or "")
        if k and k in seen:
            first = seen[k]
            for f, v in lt.items():
                if first.get(f) in (None, "", 0, -99, -99.0) and v not in (None, ""):
                    first[f] = v
            merged += 1
            continue
        seen[k] = lt
        lib.append(lt)
    _state["library"][:] = lib
    tracks = 0
    for lt in lib:
        if _has_prefix(lt.get("path"), new):
            tracks += 1
            if os.path.exists(lt["path"]):
                lt.pop("missing", None)
    # Zwischenspeicher (Taktraster, Waveforms, Qualitaet) — sonst wird neu gerechnet
    beatgrid._beatgrid_cache, _ = _rewrite(beatgrid._beatgrid_cache, old, new)
    media._wf_cache, _ = _rewrite(media._wf_cache, old, new)
    if media._wf_disk is not None:
        media._wf_disk, _ = _rewrite(media._wf_disk, old, new)
    store._quality_cache, _ = _rewrite(store._quality_cache, old.lower(), new.lower())
    store._quality_cache_dirty = True
    # Eigene Playlists (Datenordner)
    pls = 0
    if core.PLAYLISTS_DIR.exists():
        for f in core.PLAYLISTS_DIR.glob("*.m3u"):
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
                lines = [_swap(ln, old, new) for ln in txt.splitlines()]
                new_txt = "\n".join(lines) + ("\n" if txt.endswith("\n") else "")
                if new_txt != txt:
                    f.write_text(new_txt, encoding="utf-8")
                    pls += 1
            except OSError as e:
                print(f"[umzug] Playlist {f.name}: {e}", flush=True)
    store.save_library(); store.save_queue(); store.save_settings()
    store.save_history(); store.save_play_log(); store._save_quality_cache()
    try:
        media._save_wf_cache()
    except Exception:
        pass
    print(f"[umzug] {old} -> {new}: {tracks} Titel, {n} Pfade, {merged} zusammengefuehrt, {pls} Playlist(s)", flush=True)
    return {"paths": n, "tracks": tracks, "merged": merged, "playlists": pls}
