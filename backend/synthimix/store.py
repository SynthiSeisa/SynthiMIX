"""Speichern und Laden: Warteschlange, Bibliothek, Einstellungen, Verlauf, Messwert-Speicher."""
import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from .core import _state
from . import core, keys, media

# ── persistence ──────────────────────────────────────────────────────────────
def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default

def _save_json(path: Path, data):
    """Erst in eine Zwischendatei, dann austauschen. Fehler werden
    protokolliert — frueher gingen sie in Hintergrund-Aufgaben still unter,
    und Messwerte kamen nie auf der Platte an."""
    tmp = path.with_suffix(".tmp")
    try:
        try:
            txt = json.dumps(data, ensure_ascii=False)
            tmp.write_text(txt, "utf-8")
        except UnicodeEncodeError:
            # z. B. kaputte Zeichen in Dateinamen: als \uXXXX schreiben
            tmp.write_text(json.dumps(data, ensure_ascii=True), "utf-8")
        tmp.replace(path)
    except Exception as e:
        print(f"[speichern] {path.name} FEHLER: {e}", flush=True)
        raise

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
    core.spawn(_do())

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
    core.spawn(_do())

def load_queue():
    raw = _load_json(core.QUEUE_FILE, {})
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
    _save_json(core.QUEUE_FILE, {
        "items":       slim,
        "current_idx": _state["current_idx"],
        "position_ms": _state["position_ms"],
    })

_MOJIBAKE_C1 = ('€‚ƒ„…†‡'
                'ˆ‰Š‹ŒŽ‘’“”'
                '•–—˜™š›œžŸ')
# Auch Emojis (4 Byte: "ðŸ‡¦" statt der Flagge)
_MOJIBAKE_RUN_RE = re.compile(f'(?:[ÃÂ][ -¿{_MOJIBAKE_C1}]|â€[{_MOJIBAKE_C1}]|[ðñòóô][ -¿{_MOJIBAKE_C1}]{{3}})+')

def _fix_mojibake(s: str) -> str:
    """Repair UTF-8 text that was previously misdecoded as cp1252 (e.g. ffprobe
    output read without an explicit encoding) — 'fÃ¼hl' → 'fühl'. Only the
    corrupted run is re-encoded, so legitimate Unicode elsewhere (e.g. a real
    curly apostrophe) in the same string is left untouched."""
    if not s or ('Ã' not in s and 'â€' not in s and 'ð' not in s):
        return s
    def _repl(m):
        chunk = m.group(0)
        try:
            return chunk.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return chunk
    return _MOJIBAKE_RUN_RE.sub(_repl, s)

# Alte ID3v1-Tags fassen nur 30 Zeichen; manche Programme haben daraus den
# ID3v2-Titel uebernommen ("Ivy Lab - Sunday Crunk (Mefjus"). Der Dateiname
# hat dann meist den vollen Titel.
_TRACKNO_RE = re.compile(r'^\s*\d{1,3}\s*[.\-_)]\s*')

def _complete_title(title: str, path: str) -> str:
    """Nach 30 Zeichen abgeschnittenen Titel aus dem Dateinamen ergaenzen."""
    if not title or len(title) != 30 or not path:
        return title
    stem = _TRACKNO_RE.sub('', Path(path).stem)
    i = stem.lower().find(title.lower())
    if i < 0:
        return title
    rest = stem[i + len(title):]
    # "-1-1-1" (Kopie) oder nur Ziffern/Leerzeichen: kein abgeschnittener Titel
    if not re.search(r'[^\W\d_]', rest):
        return title
    return stem[i:].strip()

def load_library():
    raw = _load_json(core.LIB_CACHE, None)
    if raw is None and core.LIB_CACHE.exists():
        print("[library] library_cache.json nicht lesbar — nehme die Sicherung", flush=True)
        raw = _load_json(core.LIB_CACHE.with_suffix(".bak"), [])
    if raw is None:
        raw = []
    # Old PyQt5 format: {"folder": "...", "tracks": [...]}
    if isinstance(raw, dict):
        raw = raw.get("tracks", [])
    # Normalise field names from old format
    normalised = []
    mojibake_fixed = False
    for t in raw:
        if core.is_temp_audio(str(t.get("path") or "")):
            continue                 # frueher mit eingelesene Zwischendatei ("….temp.mp3")
        orig_title = t.get("title", "")
        fixed_title = _complete_title(_fix_mojibake(orig_title), str(t.get("path") or ""))
        if fixed_title != orig_title:
            mojibake_fixed = True
        normalised.append({
            "path":         str(Path(t.get("path", ""))) if t.get("path") else "",
            "title":        fixed_title,
            "folder":       _fix_mojibake(t.get("folder", "")),
            "duration_sec": float(str(t.get("duration_sec") or t.get("duration") or 0).replace(",", ".")),
            "lufs":         float(str(t.get("lufs", -99)).replace(",", ".")),
            "bpm":          t.get("bpm", 0),
            # Herkunft der BPM ("tag" | "analyse") und Stand der Schaetzung
            **({"bpm_src": str(t["bpm_src"])} if t.get("bpm_src") else {}),
            **({"bpm_rev": int(t["bpm_rev"])} if t.get("bpm_rev") else {}),
            "bitrate_kbps": t.get("bitrate_kbps") or t.get("bitrate") or 0,
            "comment":      _fix_mojibake(str(t.get("comment", ""))),
            "album_artist": _fix_mojibake(str(t.get("album_artist", ""))),
            # Fehlten hier bis 1.4.2 — der Kuenstler ging dadurch bei jedem
            # Start verloren, Album und Genre kamen gar nicht erst an.
            "artist":       _fix_mojibake(str(t.get("artist", ""))),
            "album":        _fix_mojibake(str(t.get("album", ""))),
            "genre":        _fix_mojibake(str(t.get("genre", ""))),
            "key":          keys._parse_key(t.get("key")) or "",
            "key_src":      str(t.get("key_src", "")),
            "meta_rev":     int(t.get("meta_rev", 0)),
            "ext":          str(t.get("ext", Path(str(t.get("path",""))).suffix.lstrip('.').lower())),
            "mtime":        int(t.get("mtime", 0)),
            "play_count":   int(t.get("play_count", 0)),
            # Obere Grenzfrequenz (Qualitaetspruefung); fehlt = noch nicht gemessen
            **({"cutoff_khz": float(t["cutoff_khz"])} if t.get("cutoff_khz") is not None else {}),
            # Lautheit des Hauptteils (nur die lautere Haelfte), fuer die Angleichung
            **({"lufs_main": float(t["lufs_main"])} if t.get("lufs_main") is not None else {}),
            # Mixed In Key: Energie-Level 1-10 und Cue-Punkte (ms)
            **({"energy": int(t["energy"])} if t.get("energy") else {}),
            **({"mik_cues": [int(c) for c in t["mik_cues"]][:16]} if t.get("mik_cues") else {}),
            # Taktraster fuer Beat-Sync (genaues Tempo, erster Schlag, Verlaesslichkeit)
            **({"bpm_f": float(t["bpm_f"]), "beat_off": float(t.get("beat_off", 0)),
                "beat_conf": float(t.get("beat_conf", 0)),
                **({"grid_rev": int(t["grid_rev"])} if t.get("grid_rev") else {})} if t.get("bpm_f") else {}),
            # Takt und Phrasen (Uebergang auf Phrasenanfang, Bass-Tausch)
            **({"phrase_off": float(t["phrase_off"]), "bar_beats": int(t.get("bar_beats") or 4),
                "phrase_src": str(t.get("phrase_src") or "")} if t.get("bpm_f") and t.get("phrase_off") is not None else {}),
            # "Passt so" in der Qualitaetsansicht
            **({"quality_ok": True} if t.get("quality_ok") else {}),
            **({"fid": str(t["fid"])} if t.get("fid") else {}),
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
    _load_quality_cache()
    # Alte Messwerte in den getrennten Speicher uebernehmen, fehlende von dort holen
    for lt in deduped:
        if lt.get("cutoff_khz") is not None and _qkey(lt["path"], lt.get("duration_sec")) not in _quality_cache:
            _remember_cutoff(lt)
    wieder = _restore_cutoffs()
    gemessen = sum(1 for lt in deduped if lt.get("cutoff_khz") is not None)
    print(f"[library] {len(deduped)} Titel geladen - Qualitaet gemessen: {gemessen}"
          + (f" ({wieder} aus dem Messwert-Speicher)" if wieder else ""), flush=True)
    # Beim Start bekannte Problemdateien sofort ins Set laden → Race Condition vermeiden
    media._unanalyzable_paths.update(t["path"] for t in deduped if t.get("unanalyzable"))
    if len(deduped) != len(normalised) or mojibake_fixed:
        save_library()

def save_library():
    try:
        _save_json(core.LIB_CACHE, _state["library"])
    except Exception:
        return
    _save_quality_cache()
    # Einmal pro Sitzung eine Sicherung der zuletzt guten Datei
    global _lib_backup_done
    if not _lib_backup_done and _state["library"]:
        try:
            shutil.copyfile(core.LIB_CACHE, core.LIB_CACHE.with_suffix(".bak"))
            _lib_backup_done = True
        except Exception:
            pass

_lib_backup_done = False

# ── Messwerte der Qualitaetspruefung zusaetzlich getrennt merken ────────────
# Schluessel: Pfad (klein) + Laenge in Sekunden. Wird die Bibliothek einmal neu
# aufgebaut (neu eingelesen, Datei beschaedigt), muss nicht alles neu gemessen
# werden — nach einem Update war genau das passiert.
QUALITY_CACHE = core.BASE_DIR / "quality_cache.json"
_quality_cache: dict = {}
_quality_cache_dirty = False

def _qkey(path: str, dur) -> str:
    return f"{(path or '').lower()}|{int(round(float(dur or 0)))}"

def _load_quality_cache():
    global _quality_cache
    raw = _load_json(QUALITY_CACHE, {})
    _quality_cache = raw if isinstance(raw, dict) else {}

def _save_quality_cache():
    global _quality_cache_dirty
    if not _quality_cache_dirty:
        return
    try:
        _save_json(QUALITY_CACHE, _quality_cache)
        _quality_cache_dirty = False
    except Exception:
        pass

def _remember_cutoff(lt: dict):
    global _quality_cache_dirty
    if lt.get("path") and lt.get("cutoff_khz") is not None:
        _quality_cache[_qkey(lt["path"], lt.get("duration_sec"))] = lt["cutoff_khz"]
        _quality_cache_dirty = True

def _restore_cutoffs() -> int:
    """Fehlende Messwerte aus dem getrennten Speicher uebernehmen."""
    n = 0
    for lt in _state["library"]:
        if lt.get("cutoff_khz") is None and lt.get("path"):
            v = _quality_cache.get(_qkey(lt["path"], lt.get("duration_sec")))
            if v is not None:
                lt["cutoff_khz"] = float(v)
                n += 1
    return n

def load_settings():
    raw = _load_json(core.SETTINGS_FILE, {})
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
    try:
        _state["dl_parallel"]         = max(1, min(6, int(raw.get("dl_parallel", 3))))
    except (TypeError, ValueError):
        _state["dl_parallel"]         = 3
    _state["dl_filename_format"]      = str(raw.get("dl_filename_format", "title"))
    _state["download_dir"]            = str(raw.get("download_dir", str(core.BASE_DIR / "Downloads")))
    _state["auto_scan_interval_min"]  = int(raw.get("auto_scan_interval_min", 0))
    _state["favorites"]               = list(raw.get("favorites", []))
    _state["remote_autostart"]        = bool(raw.get("remote_autostart", False))
    _state["remote_services_saved"]   = dict(raw.get("remote_services_saved") or {"remote": True, "wishes": True})
    _state["followed"]                = [f for f in (raw.get("followed") or []) if isinstance(f, dict) and f.get("url")]
    _state["followed_channels"]       = [c for c in (raw.get("followed_channels") or []) if isinstance(c, dict) and c.get("url")]
    _state["remote_key"]              = str(raw.get("remote_key", ""))
    _state["ytdlp_autoupdate"]        = bool(raw.get("ytdlp_autoupdate", True))
    _state["ytdlp_last_check"]        = int(raw.get("ytdlp_last_check", 0))
    _state["ffmpeg_last_check"]       = int(raw.get("ffmpeg_last_check", 0))
    _state["tool_updates"]            = dict(raw.get("tool_updates", {}) or {})
    _state["service_ok"]              = dict(raw.get("service_ok", {}) or {})
    _state["normalize_volume"]        = bool(raw.get("normalize_volume", True))
    _state["target_lufs"]             = float(raw.get("target_lufs", -10.0))
    _state["spotify_client_id"]       = str(raw.get("spotify_client_id", ""))
    _state["spotify_client_secret"]   = str(raw.get("spotify_client_secret", ""))
    _state["lastfm_api_key"]          = str(raw.get("lastfm_api_key", ""))
    _state["acoustid_api_key"]        = str(raw.get("acoustid_api_key", ""))
    _state["radio_enabled"]           = bool(raw.get("radio_enabled", False))
    # Geloeschte/private YouTube-Titel (Video-IDs): werden nicht mehr versucht
    _state["gone_ids"]                = [str(v) for v in (raw.get("gone_ids") or [])][-5000:]
    # Wiederholen/Zufall wurden nie gespeichert — "Queue-Ende: Wiederholen"
    # in den Einstellungen war nach jedem Neustart wieder weg.
    _state["repeat"]                  = int(raw.get("repeat", 0)) % 3
    _state["shuffle"]                 = bool(raw.get("shuffle", False))

# Erfolgreiche Dienst-Tests merken — gebunden an den Key (nur ein Hash wird
# gespeichert): wird der Key geaendert, gilt der alte Test nicht mehr.
_SVC_KEYS = {"lastfm": "lastfm_api_key", "acoustid": "acoustid_api_key", "spotify": "spotify_client_id"}

def _svc_fp(name: str) -> str:
    import hashlib
    return hashlib.sha1((_state.get(_SVC_KEYS[name]) or "").strip().encode()).hexdigest()[:12]

def remember_service_tests(res: dict):
    ok = _state.setdefault("service_ok", {})
    for name in _SVC_KEYS:
        r = res.get(name)
        if not r:
            continue
        if r.get("ok"):
            ok[name] = {"fp": _svc_fp(name), "ts": int(time.time())}
        else:
            ok.pop(name, None)
    save_settings()

def known_service_tests() -> dict:
    from datetime import datetime
    out = {}
    for name, v in (_state.get("service_ok") or {}).items():
        if name in _SVC_KEYS and (_state.get(_SVC_KEYS[name]) or "").strip() and v.get("fp") == _svc_fp(name):
            when = datetime.fromtimestamp(v.get("ts", 0)).strftime("%d.%m.%Y")
            out[name] = {"ok": True, "text": f"Verbunden (zuletzt getestet am {when}).", "saved": True}
    return out

def save_settings():
    _save_json(core.SETTINGS_FILE, {
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
        "dl_parallel":             _state.get("dl_parallel", 3),
        "dl_filename_format":      _state.get("dl_filename_format", "title"),
        "download_dir":            _state.get("download_dir", str(core.BASE_DIR / "Downloads")),
        "auto_scan_interval_min":  _state.get("auto_scan_interval_min", 0),
        "favorites":               _state.get("favorites", []),
        "remote_autostart":        _state.get("remote_autostart", False),
        "remote_services_saved":   _state.get("remote_services_saved", {"remote": True, "wishes": True}),
        "followed":                _state.get("followed", []),
        "followed_channels":       _state.get("followed_channels", []),
        "remote_key":              _state.get("remote_key", ""),
        "ytdlp_autoupdate":        _state.get("ytdlp_autoupdate", True),
        "ytdlp_last_check":        _state.get("ytdlp_last_check", 0),
        "ffmpeg_last_check":       _state.get("ffmpeg_last_check", 0),
        "tool_updates":            _state.get("tool_updates", {}),
        "normalize_volume":        _state.get("normalize_volume", True),
        "target_lufs":             _state.get("target_lufs", -10.0),
        "spotify_client_id":       _state.get("spotify_client_id", ""),
        "spotify_client_secret":   _state.get("spotify_client_secret", ""),
        "lastfm_api_key":          _state.get("lastfm_api_key", ""),
        "acoustid_api_key":        _state.get("acoustid_api_key", ""),
        "service_ok":              _state.get("service_ok", {}),
        "radio_enabled":           _state.get("radio_enabled", False),
        "gone_ids":                _state.get("gone_ids", []),
        "repeat":                  _state.get("repeat", 0),
        "shuffle":                 _state.get("shuffle", False),
    })

def load_history():
    data = _load_json(core.HISTORY_FILE, [])
    data = data if isinstance(data, list) else []
    for h in data:
        if h.get("title"):
            h["title"] = _fix_mojibake(h["title"])
    _state["history"] = data

def save_history():
    _save_json(core.HISTORY_FILE, _state["history"][-500:])  # keep last 500

def _history_payload() -> dict:
    """Download-Verlauf fuer die Oberflaeche. Geloeschte Dateien bleiben drin,
    werden aber markiert, damit sie ausgegraut erscheinen."""
    items = []
    for h in _state["history"][:500]:
        p = h.get("path") or ""
        items.append({**h, "gone": bool(p) and not os.path.exists(p)})
    return {"type": "history", "items": items}

def _append_history(url: str, title: str, path: str, bitrate_kbps: int):
    _state["history"] = [h for h in _state["history"] if h.get("url") != url]
    from datetime import date
    _state["history"].insert(0, {
        "url": url, "title": title, "path": path,
        "date": date.today().isoformat(), "ts": int(time.time()),
        "bitrate_kbps": bitrate_kbps
    })
    save_history()

def load_play_log():
    data = _load_json(core.PLAY_LOG_FILE, [])
    _state["play_log"] = data if isinstance(data, list) else []

def save_play_log():
    _save_json(core.PLAY_LOG_FILE, _state["play_log"][:300])

def load_notes():
    data = _load_json(core.NOTES_FILE, {"text": ""})
    _state["notes"] = str(data.get("text", "")) if isinstance(data, dict) else ""

def save_notes():
    _save_json(core.NOTES_FILE, {"text": _state["notes"]})

def _record_play(path: str, title: str, artist: str = ""):
    """Track actual playback (most recent first) — used by Auto-Mix to follow listening taste."""
    _state["play_log"].insert(0, {
        "path": path, "title": title, "artist": artist,
        "played_at": int(time.time()),
    })
    _state["play_log"] = _state["play_log"][:300]
    save_play_log()
