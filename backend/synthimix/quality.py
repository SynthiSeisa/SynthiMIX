"""Qualitaet: Bandbreite messen, bessere Versionen suchen und einsetzen."""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from fastapi import WebSocket
from pathlib import Path
from .core import _NO_WINDOW, _state
from . import beatgrid, core, library, media, search, store

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
    if not media._numpy_ok():
        return 0.0
    import numpy as np
    sr, n = 44100, 8192
    start = max(0.0, (duration or 0) * 0.4)
    cmd = [core.FFMPEG, "-nostdin", "-v", "error", "-ss", f"{start:.1f}", "-t", "10", "-i", path,
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
    await core.broadcast({"type": "quality_scan", "done": 0, "total": total})

    async def one(lt):
        nonlocal done
        async with sem:
            path = lt["path"]
            if not os.path.exists(path):
                return                        # Laufwerk fehlt: spaeter nochmal
            known = store._quality_cache.get(store._qkey(path, lt.get("duration_sec")))
            if known is not None:
                lt["cutoff_khz"] = float(known)
            else:
                lt["cutoff_khz"] = await loop.run_in_executor(
                    None, _cutoff_khz_sync, path, lt.get("duration_sec") or 0)
                store._remember_cutoff(lt)
            done += 1
            # Hochgerechnete sofort zeigen — die ganze Bibliothek geht nur alle
            # 500 Titel raus, bei einer neuen Bibliothek wirkte es sonst minutenlang,
            # als wuerde nichts gemessen oder gespeichert
            if 5 < lt["cutoff_khz"] < _CUTOFF_UPSCALED_KHZ:
                await core.broadcast({"type": "track_meta_update",
                                 "track": {"path": path, "cutoff_khz": lt["cutoff_khz"]}})
            if done % 25 == 0:
                store.schedule_save()
                await core.broadcast({"type": "quality_scan", "done": done, "total": total})
            if done % 500 == 0:
                await core.push_library()

    await asyncio.gather(*(one(lt) for lt in pending))
    store.save_library()
    await core.push_library()
    await core.broadcast({"type": "quality_scan", "done": done, "total": total, "finished": True})
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
    q = (query or "").strip() or search._song_query((lt or {}).get("title") or Path(path).stem)

    async def send(results, final):
        try:
            await ws.send_text(json.dumps({"type": "quality_candidates", "path": path, "query": q,
                                           "results": search._public(results), "final": final}))
        except Exception:
            pass

    songs, videos = await search._songs_and_videos(q, 5, 8)
    await send(search._merge_songs_first(songs, videos), not songs)
    if songs:
        await search._ytm_fill_details(songs)
        songs = [r for r in songs if not search._is_unwanted_result(r) and not search.LIVE_RE.search(r.get("title", ""))]
        await send(search._merge_songs_first(songs, videos), True)


# Format der alten Datei → yt-dlp --audio-format (gleiche Endung, gleicher Name)
_REPLACE_FORMATS = {"mp3": "mp3", "m4a": "m4a", "flac": "flac", "wav": "wav",
                    "opus": "opus", "ogg": "vorbis", "aac": "aac"}
_replace_running: set[str] = set()


async def _download_replacement(url: str, ext: str, tmpdir: str) -> tuple[str | None, str]:
    """Neue Version ohne eigene Tags und Cover in tmpdir laden (die kommen von
    der alten Datei). Liefert (Pfad oder None, Fehlermeldung von yt-dlp)."""
    cmd = core._yt("-x", "--audio-format", _REPLACE_FORMATS[ext], "--audio-quality", "0",
              "--no-playlist", "--newline", "--encoding", "utf-8",
              "-o", os.path.join(tmpdir, "neu.%(ext)s"))
    if core.FFMPEG_DIR:
        cmd += ["--ffmpeg-location", core.FFMPEG_DIR]
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
        if not await loop.run_in_executor(None, library._move_to_trash, path):
            try: os.remove(staged)
            except OSError: pass
            await status("error", "Die alte Datei ließ sich nicht in den Papierkorb legen. Nichts ersetzt.")
            return
        try:
            os.replace(staged, path)
        except OSError as e:
            await status("error", f"Neue Datei liegt unter {staged}, die alte im Papierkorb ({e}).")
            return

        probe = await loop.run_in_executor(None, media._probe_sync, path)
        dur = probe.get("duration_sec") or lt.get("duration_sec") or 0
        lt["duration_sec"] = dur
        lt["bitrate_kbps"] = probe.get("bitrate_kbps") or 0
        lt["lufs"]         = -99.0
        lt.pop("lufs_main", None)
        lt["mtime"]        = int(os.path.getmtime(path))
        lt["meta_rev"]     = media._TAG_META_REV
        lt.pop("unanalyzable", None)
        for k in beatgrid._GRID_KEYS:                          # neue Datei, neues Raster
            lt.pop(k, None)
        beatgrid._beatgrid_cache.pop(path, None)
        media._unanalyzable_paths.discard(path)
        lt["cutoff_khz"]   = await loop.run_in_executor(None, _cutoff_khz_sync, path, dur)
        store._remember_cutoff(lt)
        for item in _state["queue"]:
            if item.get("path") == path:
                item["lufs"] = -99.0
                item["duration_sec"] = dur
                item["bitrate_kbps"] = lt["bitrate_kbps"]
        media._wf_cache.pop(path, None)
        store.save_library()
        store.save_queue()
        await core.broadcast({"type": "track_meta_update", "track": lt})
        asyncio.create_task(media._enrich_track(path))       # Lautheit neu messen
        await status("done", f"Ersetzt: {lt['bitrate_kbps']} kbps, Höhen bis {lt['cutoff_khz']} kHz.")
    finally:
        _replace_running.discard(path)
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Qualitaet: viele Titel auf einmal ersetzen ───────────────────────────────
_qbatch_cancel = False
_QBATCH_PARALLEL = 2


def _auto_candidate(lt: dict, results: list[dict]) -> tuple[dict | None, bool]:
    """Besten Ersatz fuer einen Bibliothekstitel waehlen, ohne Rueckfrage:
    gleicher Song und gleiche Fassung (Remix/Edit/VIP), keine Live-Aufnahmen
    oder Musikvideos, hoechstens 30 s Laengenunterschied. Studio-Version vor
    YouTube-Upload, dann die kleinste Laengenabweichung. Zweiter Wert: sicher?
    Ohne bekannten Kuenstler ("Miracle") nie — gleichnamige Songs gibt es viele."""
    title = lt.get("title", "")
    a_want, s_want = search._dupe_parts(title, lt.get("artist") or lt.get("album_artist") or "")
    if len(s_want) < 2:
        return None, False
    version = set(s_want.split()) & search._VERSION_WORDS
    vdur = lt.get("duration_sec") or 0
    best = None
    for r in results:
        dur = r.get("duration") or 0
        rt = r.get("title", "")
        if not dur or search.LIVE_RE.search(rt) or search._MV_TITLE_RE.search(rt):
            continue
        ra, rs = search._dupe_parts(rt, r.get("artist") or r.get("uploader") or "")
        if (set(rs.split()) & search._VERSION_WORDS) != version:
            continue
        sim = SequenceMatcher(None, s_want, rs).ratio()
        if sim < 0.8:
            continue
        diff = abs(dur - vdur) if vdur else 0
        if vdur and diff > 30:
            continue
        artist_ok = not a_want or not ra or a_want in ra or ra in a_want \
            or SequenceMatcher(None, a_want, ra).ratio() >= 0.6
        # Kuenstler-Tag ist oft der YouTube-Kanal: dann muessen Titel und Laenge sehr genau passen
        if not artist_ok and not (sim >= 0.95 and vdur and diff <= 3):
            continue
        sure = bool(a_want and ra and artist_ok) and sim >= 0.9
        key = (0 if r.get("kind") == "song" else 1, diff)
        if best is None or key < best[0]:
            best = (key, r, sure)
    return (best[1], best[2]) if best else (None, False)


async def _quality_batch(paths: list[str], ws: WebSocket):
    """Fuer viele Titel je einen Ersatz vorschlagen (Liste zum Pruefen)."""
    global _qbatch_cancel
    _qbatch_cancel = False

    async def send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    by_path = {x.get("path"): x for x in _state["library"]}
    sem = asyncio.Semaphore(_QBATCH_PARALLEL)
    total, done = len(paths), 0
    await send("quality_batch_progress", phase="search", done=0, total=total)

    async def one(path):
        nonlocal done
        lt = by_path.get(path)
        if lt is None or _qbatch_cancel:
            return
        async with sem:
            if _qbatch_cancel:
                return
            cand, sure = None, False
            try:
                songs, videos = await search._songs_and_videos(search._song_query(lt.get("title", "")), 4, 6)
                if songs:
                    await search._ytm_fill_details(songs)
                cand, sure = _auto_candidate(lt, search._merge_songs_first(songs, videos))
            except Exception as e:
                print(f"[quality] Sammelsuche {Path(path).name}: {e}", flush=True)
            done += 1
            await send("quality_batch_item", path=path, title=lt.get("title", ""),
                       duration=lt.get("duration_sec") or 0,
                       candidate=search._public([cand])[0] if cand else None, sure=sure)
            await send("quality_batch_progress", phase="search", done=done, total=total)

    await asyncio.gather(*(one(p) for p in paths))
    await send("quality_batch_done", cancelled=_qbatch_cancel)


class _ForwardWS:
    """Leitet Nachrichten weiter und merkt sich den letzten Ersetzen-Status."""
    def __init__(self, ws):
        self.ws, self.state, self.text = ws, "", ""

    async def send_text(self, text):
        try:
            m = json.loads(text)
            if m.get("type") == "quality_replace_status":
                self.state, self.text = m.get("state", ""), m.get("text", "")
        except Exception:
            pass
        try: await self.ws.send_text(text)
        except Exception: pass


async def _quality_batch_replace(items: list, ws: WebSocket):
    """Nacheinander ersetzen, abbrechbar. Jeder Titel wie beim Einzel-Ersetzen:
    gleicher Name und Pfad, Tags bleiben, alte Datei in den Papierkorb."""
    global _qbatch_cancel
    _qbatch_cancel = False

    async def send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass

    ok, failed, total = 0, [], len(items)
    for n, it in enumerate(items):
        if _qbatch_cancel:
            break
        path, url = it.get("path", ""), it.get("url", "")
        title = next((x.get("title", "") for x in _state["library"] if x.get("path") == path), path)
        await send("quality_batch_progress", phase="replace", done=n, total=total, current=title)
        fw = _ForwardWS(ws)
        await _quality_replace(path, url, fw)
        if fw.state == "done":
            ok += 1
        else:
            failed.append({"title": title, "text": fw.text})
    await send("quality_batch_replaced", ok=ok, failed=failed[:30], failed_count=len(failed),
               cancelled=_qbatch_cancel)
