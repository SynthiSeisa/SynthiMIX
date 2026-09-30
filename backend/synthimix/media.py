"""Audiodateien: Tags lesen, LUFS/BPM/Tonart messen, Waveform, Dateien normalisieren."""
import asyncio
import base64
import json
import math
import os
import re
import subprocess
from fastapi import WebSocket
from pathlib import Path
from .core import _NO_WINDOW, _state
from . import beatgrid, core, keys, store

# ── track enrichment ──────────────────────────────────────────────────────────
def _extract_art_sync(path: str) -> str | None:
    try:
        proc = subprocess.run(
            [core.FFMPEG, "-nostdin", "-i", path, "-an",
             "-vframes", "1", "-f", "image2", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=10, creationflags=_NO_WINDOW)
        if proc.returncode == 0 and len(proc.stdout) > 200:
            return "data:image/jpeg;base64," + base64.b64encode(proc.stdout).decode()
    except Exception:
        pass
    return None

_S_RE = re.compile(r"S:\s*(-?[\d.]+)")

def _main_loudness(stderr: str) -> float | None:
    """Lautheit des Hauptteils: Energie-Mittel der lauteren Haelfte der
    Kurzzeit-Lautheit (3-s-Fenster, alle 0,1 s). Ruhige Intros und Breakdowns
    zaehlen so nicht mit — ueber den ganzen Song gemessen wurden Titel mit
    langen ruhigen Teilen zu stark angehoben, der Drop knallte dann."""
    vals = [float(x) for x in _S_RE.findall(stderr)]
    vals = sorted(v for v in vals[30:] if v > -70)      # erste 3 s: Fenster fuellt sich
    if len(vals) < 20:
        return None
    top = vals[len(vals) // 2:]
    return round(10 * math.log10(sum(10 ** (v / 10) for v in top) / len(top)), 1)

def _measure_loudness_sync(path: str) -> tuple[float, float | None]:
    """(LUFS ueber den ganzen Titel, Lautheit des Hauptteils oder None)."""
    name = Path(path).name
    print(f"[lufs] starte Analyse: '{name}'", flush=True)
    try:
        r = subprocess.run(
            [core.FFMPEG, "-nostdin", "-i", path,
             "-filter:a", "ebur128=framelog=info", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, creationflags=_NO_WINDOW)
        m = re.findall(r"I:\s+([-\d.]+)\s+LUFS", r.stderr)
        if m:
            val = round(float(m[-1]), 1)
            main = _main_loudness(r.stderr)
            print(f"[lufs] OK '{name}' → {val} LUFS (Hauptteil {main})", flush=True)
            return val, main

        # Fallback: astats für sehr kurze Dateien oder wenn ebur128 kein Ergebnis liefert
        # Schätze LUFS aus RMS-dB (astats mean_volume)
        r2 = subprocess.run(
            [core.FFMPEG, "-nostdin", "-i", path,
             "-filter:a", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
             "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=_NO_WINDOW)
        m2 = re.search(r"RMS_level=([+-]?\d+(?:\.\d+)?)", r2.stderr + r2.stdout)
        if m2:
            rms_db = float(m2.group(1))
            if math.isfinite(rms_db) and rms_db > -90:
                val = round(rms_db - 3.0, 1)
                print(f"[lufs] Fallback (astats) '{name}' → {val} LUFS", flush=True)
                return val, None

        name = Path(path).name
        last_lines = [l for l in r.stderr.splitlines() if l.strip()][-5:]
        stderr_text = "\n".join(last_lines)
        # "No such file" → temporärer Fehler (Laufwerk getrennt etc.) → -98.0
        # Dekodierungsfehler (moov, Invalid data) → permanent → -97.0
        if "No such file" in stderr_text or "no such file" in stderr_text.lower():
            print(f"[lufs] NICHT GEFUNDEN '{name}'", flush=True)
            return -98.0, None
        print(f"[lufs] FEHLER '{name}' (rc={r.returncode}):", flush=True)
        for ln in last_lines:
            print(f"  ffmpeg: {ln}", flush=True)
        return -97.0, None
    except Exception as e:
        print(f"[lufs] Exception bei '{Path(path).name}': {e}", flush=True)
        return -98.0, None

def _compute_lufs_sync(path: str) -> float:
    return _measure_loudness_sync(path)[0]


_LOUD_PARALLEL = 1          # laeuft auch waehrend des Auflegens: sanft

async def _loud_main_once():
    pending = [lt for lt in _state["library"]
               if lt.get("path") and lt.get("lufs_main") is None and not lt.get("missing")
               and not lt.get("unanalyzable") and (lt.get("duration_sec") or 0) >= 30]
    if not pending:
        return
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(_LOUD_PARALLEL)
    done = 0
    print(f"[lufs] Hauptteil messen: {len(pending)} Titel", flush=True)

    async def one(lt):
        nonlocal done
        async with sem:
            path = lt["path"]
            if not os.path.exists(path):
                return                           # Laufwerk fehlt: spaeter nochmal
            lufs, main = await loop.run_in_executor(None, _measure_loudness_sync, path)
            if main is None:
                if lufs == -97.0:
                    lt["unanalyzable"] = True
                return
            lt["lufs_main"] = main
            if lt.get("lufs", -99) <= -90 and lufs > -90:
                lt["lufs"] = lufs
            done += 1
            if done % 25 == 0:
                store.schedule_save()
            if done % 100 == 0:
                await core.push_library()

    await asyncio.gather(*(one(lt) for lt in pending))
    store.save_library()
    await core.push_library()
    print(f"[lufs] Hauptteil gemessen: {done} Titel", flush=True)

async def _loud_main_loop():
    """Misst im Hintergrund die Lautheit des Hauptteils (einmal je Titel),
    danach alle 10 Minuten fuer neue Titel."""
    await asyncio.sleep(90)       # Start, Tag-Abgleich und Qualitaet gehen vor
    while True:
        try:
            await _loud_main_once()
        except Exception as e:
            print(f"[lufs] Hauptteil: {e}", flush=True)
        await asyncio.sleep(600)

def _estimate_bpm_sync(path: str) -> int:
    """BPM via onset-energy autocorrelation (60-180 BPM range)."""
    try:
        sr, hop = 22050, 512
        proc = subprocess.Popen(
            [core.FFMPEG, "-nostdin", "-i", path,
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

def _file_id(path: str) -> str:
    """Kennung der Datei auf dem Laufwerk (Volume + Dateinummer). Zwei Pfade mit
    derselben Kennung sind dieselbe Datei (Hardlink) — kein Duplikat."""
    try:
        st = os.stat(path)
        return f"{st.st_dev}:{st.st_ino}" if st.st_ino else ""
    except OSError:
        return ""

def _make_library_entry(path: str, probe: dict, **overrides) -> dict:
    """Bibliothekseintrag aus einem _probe_sync-Ergebnis.

    Frueher bauten vier Stellen ihre Eintraege selbst — Album und Genre wurden
    dabei ueberall vergessen. Neue Felder gehoeren ab jetzt nur noch hierher
    (und in die Normalisierung von load_library).
    """
    p = Path(path)
    key = keys._parse_key(probe.get("key")) or ""
    entry = {
        "path":         path,
        "title":        store._complete_title(probe.get("title") or p.stem, path),
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
        **({"energy": probe["energy"]} if probe.get("energy") else {}),
        **({"mik_cues": probe["mik_cues"]} if probe.get("mik_cues") else {}),
        "fid":          _file_id(path),
    }
    entry.update(overrides)
    v = store._quality_cache.get(store._qkey(path, entry.get("duration_sec")))
    if v is not None and "cutoff_khz" not in entry:
        entry["cutoff_khz"] = float(v)
    return entry

def _mik_json(raw) -> dict:
    """GEOB-Inhalt von Mixed In Key: JSON, manchmal base64-verpackt."""
    if isinstance(raw, (bytes, bytearray)):
        for cand in (bytes(raw),):
            try:
                return json.loads(cand.decode("utf-8", errors="replace").strip("\x00 "))
            except Exception:
                pass
            try:
                return json.loads(base64.b64decode(cand).decode("utf-8", errors="replace"))
            except Exception:
                pass
    return {}

def _read_mik_sync(path: str, mf=None) -> dict:
    """Energie-Level (1-10) und Cue-Punkte von Mixed In Key aus den Tags.

    MIK schreibt in MP3s TXXX:EnergyLevel plus GEOB-Frames "Energy", "Key" und
    "CuePoints" (JSON, Zeiten in ms). Die Cues liegen auf Phrasen-Anfaengen,
    der erste auf dem ersten Taktschlag — daraus wird das Taktraster genau.
    """
    out: dict = {}
    try:
        if mf is None:
            import mutagen
            mf = mutagen.File(path)
        tags = getattr(mf, "tags", None) or {}
    except Exception:
        return out
    energy = 0
    try:
        if hasattr(tags, "getall"):                       # ID3
            for fr in tags.getall("TXXX"):
                if (fr.desc or "").lower() in ("energylevel", "energy"):
                    energy = int(float(str(fr.text[0]).strip() or 0))
            for fr in tags.getall("GEOB"):
                d = (fr.desc or "").lower()
                if d == "energy" and not energy:
                    energy = int(_mik_json(fr.data).get("energyLevel") or 0)
                elif d == "cuepoints":
                    cues = [float(c.get("time", -1)) for c in _mik_json(fr.data).get("cues", [])]
                    cues = sorted(c for c in cues if c >= 0)
                    if cues:
                        out["mik_cues"] = [int(round(c)) for c in cues[:16]]
        else:                                             # MP4 / Vorbis
            for k in ("----:com.apple.iTunes:EnergyLevel", "----:com.mixedinkey.mixedinkey:energy",
                      "energylevel", "ENERGYLEVEL"):
                v = tags.get(k) if hasattr(tags, "get") else None
                if v:
                    v = v[0] if isinstance(v, list) else v
                    if isinstance(v, (bytes, bytearray)):
                        v = v.decode("utf-8", errors="replace")
                    energy = int(float(str(v).strip() or 0))
                    break
    except Exception:
        pass
    if 1 <= energy <= 10:
        out["energy"] = energy
    return out

def _probe_sync(path: str) -> dict:
    result = {"duration_sec": 0.0, "bitrate_kbps": 0, "bpm": 0,
              "title": Path(path).stem, "artist": "", "album_artist": "",
              "album": "", "genre": "", "key": "",
              "comment": "", "ext": Path(path).suffix.lstrip('.').lower()}
    try:
        r = subprocess.run(
            [core.FFPROBE, "-v", "quiet", "-print_format", "json",
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
        result["key"]          = keys._parse_key(tags.get("tkey") or tags.get("initialkey")
                                            or tags.get("key")) or ""
        if result["duration_sec"] > 0:
            result.update(_read_mik_sync(path))
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
_TAG_META_REV = 4      # 2: Tonart, 3: Energie und Cues von Mixed In Key, 4: BPM aus dem Tag

def _tag_bpm(raw) -> int:
    """BPM-Tag ("174", "173.98", "128,00") als ganze Zahl, 0 wenn leer/unsinnig."""
    try:
        v = float(str(raw or "").strip().replace(",", ".") or 0)
    except ValueError:
        return 0
    return int(round(v)) if 40 <= v <= 250 else 0

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
    mik = _read_mik_sync(path, mf)
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
        "key":          keys._parse_key(_t('TKEY', 'initialkey', '----:com.apple.iTunes:initialkey')) or "",
        "bpm":          _tag_bpm(_t('TBPM', 'bpm', 'tmpo')),
        **mik,
    }

async def _refresh_tag_meta_loop():
    """Beim Start und danach alle 5 Minuten: geaenderte Dateien nachlesen.
    So kommt eine Analyse mit Mixed In Key an, ohne SynthiMIX neu zu starten."""
    while True:
        try:
            await _refresh_tag_meta_task()
        except Exception as e:
            print(f"[library] Tag-Abgleich fehlgeschlagen: {e}", flush=True)
        await asyncio.sleep(300)

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
    FIELDS = ("artist", "album_artist", "album", "genre", "key", "energy", "mik_cues", "bpm")
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
            for k in ("energy", "mik_cues"):             # Mixed In Key gilt immer
                if tags.get(k) and tags[k] != current.get(k):
                    upd[k] = tags[k]
            if "mik_cues" in upd:                         # Phrasen neu aus den Cues
                upd["_rephrase"] = True
            # BPM aus dem Tag (Mixed In Key, rekordbox) schlaegt die eigene
            # grobe Schaetzung. Das Taktraster wird dann neu gemessen.
            if tags.get("bpm") and tags["bpm"] != current.get("bpm"):
                upd["bpm"] = tags["bpm"]
                upd["_regrid"] = True
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
        if upd.pop("_rephrase", False):
            for k in ("phrase_off", "bar_beats", "phrase_src"):
                lt.pop(k, None)
            beatgrid._beatgrid_cache.pop(lt.get("path"), None)
        if upd.pop("_regrid", False):
            for k in beatgrid._GRID_KEYS:
                lt.pop(k, None)
            beatgrid._beatgrid_cache.pop(lt.get("path"), None)
        if any(k not in ("meta_rev", "mtime") for k in upd):
            ergaenzt += 1
        lt.update(upd)
        # Titel in der Warteschlange mitziehen (Tonart/BPM im Player)
        for qt in _state.get("queue", []):
            if qt.get("path") == lt.get("path"):
                for k in ("bpm", "key", "energy"):
                    if k in upd:
                        qt[k] = upd[k]

    store.save_library()
    await core.push_library()
    print(f"[library] Tags abgeglichen: {len(updates)} Dateien, {ergaenzt} geaendert", flush=True)
    if ergaenzt:
        await core.broadcast({"type": "scan_status", "text": f"Tags ergänzt: {ergaenzt} Titel"})
        await asyncio.sleep(4)
        await core.broadcast({"type": "scan_status", "text": ""})

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
    return keys._KEY_NAMES[best[1]] + ('m' if best[2] else ''), best[0] - second[0]

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
        cmd = [core.FFMPEG, "-nostdin", "-v", "error"] + (["-ss", start] if start else []) + \
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
# Auftraege, die waehrend einer laufenden Analyse kamen (None = alle Titel).
# Frueher wurden sie still verworfen — "Analysieren" am Ordner tat dann nichts,
# und der Zaehler zeigte die andere, groessere Analyse.
_analyze_pending: list = []
_unanalyzable_paths: set[str] = set()   # Pfade die dauerhaft nicht analysierbar sind
_ANALYZE_PARALLEL = 3                    # Titel, die gleichzeitig analysiert werden

async def _analyze_library_meta_task(only_paths: list[str] | None = None):
    """Background: fill in missing LUFS and BPM for every library track.

    only_paths schraenkt auf bestimmte Titel ein (Kontextmenue "Analysieren"
    eines Ordners) — nacheinander statt alle auf einmal.
    """
    global _analyze_running, _analyze_cancel
    if _analyze_running:
        _analyze_pending.append(only_paths)
        n = "alle Titel" if only_paths is None else f"{len(only_paths)} Titel"
        await core.broadcast({"type": "scan_status", "text": f"Analyse ({n}) eingereiht — startet nach der laufenden"})
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
            await core.broadcast({"type": "analyze_progress", "done": 0, "total": total})
        # Drei Titel gleichzeitig: jeder Schritt ist ein eigener ffmpeg-Prozess
        # bzw. numpy — frueher streng nacheinander, ~2 s je Titel.
        sem = asyncio.Semaphore(_ANALYZE_PARALLEL)
        prog = {"done": 0}

        async def _one(lt):
          async with sem:
            if _analyze_cancel:
                return
            changed = False
            path = lt.get("path", "")
            need_lufs = lt.get("lufs", -99) <= -90
            need_bpm  = not lt.get("bpm")
            need_key  = not lt.get("key") and lt.get("key_src") != "none"
            try:
                if need_lufs:
                    lufs, main = await loop.run_in_executor(None, _measure_loudness_sync, path)
                    if lufs > -90:
                        lt["lufs"] = lufs
                        if main is not None:
                            lt["lufs_main"] = main
                        changed = True
                    elif lufs == -97.0:
                        # Echter Dekodierfehler → dauerhaft überspringen
                        lt["unanalyzable"] = True
                        _unanalyzable_paths.add(path)
                        store.schedule_save()
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
            prog["done"] += 1
            await core.broadcast({"type": "analyze_progress", "done": prog["done"], "total": total})
            if changed:
                await core.broadcast({"type": "track_meta_update", "track": lt})

        await asyncio.gather(*(_one(lt) for lt in pending))
        done = prog["done"]
        store.save_library()
        await core.push_library()
        await core.broadcast({"type": "analyze_progress", "done": done, "total": total, "finished": True})
    finally:
        _analyze_running = False
        if _analyze_cancel:
            _analyze_pending.clear()
        _analyze_cancel  = False
        if _analyze_pending:
            asyncio.create_task(_analyze_library_meta_task(_analyze_pending.pop(0)))

async def _update_track_meta(path: str, title: str, artist: str):
    """Rewrite ID3/metadata tags in-place using ffmpeg, then update library cache."""
    suffix = Path(path).suffix
    tmp = path + '.__tmp' + suffix
    try:
        proc = await asyncio.create_subprocess_exec(
            core.FFMPEG, '-y', '-i', path,
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
    store.save_library()
    await core.push_library()

async def _auto_add_to_library(path: str):
    if any(lt.get("path") == path for lt in _state["library"]):
        return
    loop  = asyncio.get_running_loop()
    probe = await loop.run_in_executor(None, _probe_sync, path)
    _state["library"].append(_make_library_entry(path, probe))
    store.save_library()
    await core.push_library()

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
    main     = None
    if cached_lufs > -90 and not force:
        lufs = cached_lufs
    else:
        lufs, main = await loop.run_in_executor(None, _measure_loudness_sync, path)
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
            bpm=bpm or probe.get("bpm", 0),
            **({"lufs_main": main} if main is not None else {}))
        _state["library"].append(lib_entry)
        lib_changed = True
    elif lib_entry is not None:
        if lufs > -90 and lib_entry.get("lufs", -99) <= -90:
            lib_entry["lufs"] = lufs;     lib_changed = True
        if main is not None and (force or lib_entry.get("lufs_main") is None):
            lib_entry["lufs_main"] = main; lib_changed = True
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
        store.save_library()
        if lib_entry is not None:
            await core.broadcast({"type": "track_meta_update", "track": lib_entry})

    store.save_queue()
    await core.broadcast({"type": "track_enriched", "path": path, "art": art, "lufs": lufs, "bpm": bpm, "duration_sec": duration})


# ── waveform ─────────────────────────────────────────────────────────────────
_wf_cache: dict[str, list] = {}

# Waveforms zusaetzlich auf der Platte merken: frueher wurde jede nach jedem
# Start neu aus der Datei gerechnet. Je Titel 1000 Werte als Bytes (0-255),
# Schluessel Pfad + Groesse + Aenderungszeit — eine geaenderte Datei rechnet neu.
from concurrent.futures import ThreadPoolExecutor
# Eigene Threads: im allgemeinen Pool stand die Waveform sonst hinter den
# Hintergrund-Analysen (Qualitaet, Tonart, Takt, Phrasen) an
_WF_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="waveform")
_WF_DISK_MAX = 6000
_wf_disk: dict | None = None
_wf_disk_dirty = False
_wf_save_task = None

def _wf_cache_file():
    return core.BASE_DIR / "waveform_cache.json"

def _wf_key(path: str) -> str | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{path.lower()}|{st.st_size}|{int(st.st_mtime)}"

def _wf_disk_dict() -> dict:
    global _wf_disk
    if _wf_disk is None:
        raw = store._load_json(_wf_cache_file(), {})
        _wf_disk = raw if isinstance(raw, dict) else {}
    return _wf_disk

def _save_wf_cache():
    global _wf_disk_dirty
    if not _wf_disk_dirty or _wf_disk is None:
        return
    while len(_wf_disk) > _WF_DISK_MAX:
        _wf_disk.pop(next(iter(_wf_disk)))
    try:
        store._save_json(_wf_cache_file(), _wf_disk)
        _wf_disk_dirty = False
    except Exception:
        pass

async def _save_wf_cache_later():
    global _wf_save_task
    try:
        await asyncio.sleep(10)
        _save_wf_cache()
    finally:
        _wf_save_task = None

async def compute_waveform(path: str, bars: int = 1000) -> list[float]:
    global _wf_disk_dirty, _wf_save_task
    if path in _wf_cache:
        return _wf_cache[path]
    if not os.path.exists(path):
        return []
    key = _wf_key(path) if bars == 1000 else None
    if key:
        hit = _wf_disk_dict().get(key)
        if hit:
            try:
                data = [b / 255 for b in base64.b64decode(hit)]
                _wf_cache[path] = data
                return data
            except Exception:
                pass
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(_WF_POOL, _waveform_sync, path, bars)
    _wf_cache[path] = data
    if key and data:
        d = _wf_disk_dict()
        d.pop(key, None)
        d[key] = base64.b64encode(bytes(min(255, max(0, round(v * 255))) for v in data)).decode("ascii")
        _wf_disk_dirty = True
        if _wf_save_task is None:
            _wf_save_task = asyncio.create_task(_save_wf_cache_later())
    return data

def _waveform_sync(path: str, bars: int) -> list[float]:
    try:
        proc = subprocess.Popen(
            [core.FFMPEG, "-nostdin", "-i", path,
             "-filter:a", "aformat=channel_layouts=mono",
             "-acodec", "pcm_s16le", "-f", "s16le", "-ar", "8000", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        raw = proc.stdout.read()
        proc.wait()
        if not raw or len(raw) < 4:
            return []
        if _numpy_ok():
            # Mit numpy: ein Bruchteil der Zeit der Python-Schleife unten
            import numpy as np
            a = np.frombuffer(raw[:len(raw) & ~1], dtype=np.int16)
            chunk = a.size // bars
            if chunk < 1:
                return []
            x = a[:chunk * bars].astype(np.float32).reshape(bars, chunk)
            rms = np.sqrt((x * x).mean(axis=1))
            mx = float(rms.max()) or 1.0
            return [float(v) for v in rms / mx]
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
                        lt.pop("lufs_main", None)       # misst der Hintergrund neu
                        break
            else:
                errors += 1
        except Exception:
            errors += 1
        done += 1

    store.save_library()
    await core.push_library()
    await ws.send_text(json.dumps({
        "type": "normalize_done",
        "normalized": done - errors,
        "errors": errors
    }))


def _audio_stream_info(path: str) -> tuple[int, int]:
    """Bitrate (kbps) und Abtastrate der ersten Audiospur, 0 wenn unbekannt."""
    try:
        r = subprocess.run(
            [core.FFPROBE, "-v", "quiet", "-print_format", "json", "-show_streams",
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
            [core.FFMPEG, "-nostdin", "-i", path,
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
            [core.FFMPEG, "-nostdin", "-i", path, "-map", "0:a:0", "-map", "0:v?",
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
