"""Downloads: einzelne Links, Playlists (pruefen, laden, verfolgen), Spotify."""
import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from fastapi import WebSocket
from pathlib import Path
from typing import Any
from .core import _NO_WINDOW, _state
from . import channels, core, library, media, search, store, tools

_dl_procs: dict[int, Any] = {}  # session_id → asyncio.Process oder Liste davon (Playlist parallel)
_DL_PARALLEL = 3                  # gleichzeitige yt-dlp-Prozesse bei Playlists

def _is_spotify(url: str) -> bool:
    return "open.spotify.com" in url or "spotify.link" in url


# ── download ─────────────────────────────────────────────────────────────────
_dl_counter = 0

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
                *core._yt(*args, "--no-warnings", "--quiet", "--encoding", "utf-8", url),
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
        core.YTDLP,
        *core._JS_ARGS,
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
    if core.FFMPEG_DIR:
        cmd += ["--ffmpeg-location", core.FFMPEG_DIR]

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
        cmd += ["--playlist-items", "1", search._ytm_search_url(url)]
    else:
        cmd.append(url)
    return cmd

async def _audit_playlist_for_videos(playlist_url: str, progress=None,
                                     known: set | None = None) -> tuple[list[dict], str]:
    """
    Flat-list a playlist. For entries whose title contains video keywords,
    search YTM for an audio replacement. Returns ([{url, title, replaced}], playlist_title).
    Der Name steht in jedem Eintrag der flachen Liste — ein eigener Aufruf
    dafuer (frueher mit 15 s Zeitlimit) entfaellt.

    progress: async (phase, done, total) — "list" beim Einlesen, "search" bei
    der Suche nach Song-Versionen. known: Video-IDs, die nicht mehr gesucht
    werden muessen (verfolgte Playlist).
    """
    pl_title = ""
    base_args = ["--flat-playlist", "-j", "--quiet", "--no-warnings"]
    if core.FFMPEG_DIR:
        base_args += ["--ffmpeg-location", core.FFMPEG_DIR]

    # Flat-list the whole playlist
    entries: list[dict] = []
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *core._yt(*base_args, playlist_url),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=_NO_WINDOW)
        async for raw in proc.stdout:
            try:
                item = json.loads(raw.decode("utf-8", errors="replace"))
                raw_url = item.get("url") or item.get("webpage_url") or item.get("id", "")
                url = search._normalise_yt_url(raw_url)
                if not pl_title:
                    pl_title = (item.get("playlist_title") or item.get("playlist") or "").strip()
                if url:
                    entries.append({"url": url, "title": item.get("title") or "", "replaced": False,
                                    "uploader": item.get("uploader") or item.get("channel") or "",
                                    "duration": item.get("duration") or 0})
                if progress and len(entries) % 25 == 0:
                    await progress("list", len(entries), 0)
            except Exception:
                pass
        await proc.wait()
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            try: proc.kill()
            except Exception: pass
        raise
    except Exception:
        pass

    if not entries:
        return entries, pl_title

    # Eintraege mit Video-Stichwort: Studio-Version von YouTube Music suchen.
    # Frueher per "ytmsearch", das es nicht gibt — ersetzt wurde nie etwas.
    # Frueher 3 gleichzeitig und jedes Mal neu — bei 140 Titeln Minuten.
    sem = asyncio.Semaphore(_AUDIT_PARALLEL)
    done = 0

    async def replace(entry, total):
        nonlocal done
        title = entry["title"] or ""
        query = search._song_query(title)
        if query:
            async with sem:
                song = search._pick_song_version(entry, await search._ytm_song_search(query))
            if song:
                entry["src"]      = entry["url"]
                entry["url"]      = song["url"]
                entry["title"]    = (f'{song["artist"]} - {song["title"]}' if song.get("artist") else song["title"]) or title
                entry["replaced"] = True
        done += 1
        if progress:
            await progress("search", done, total)

    # Nicht nur Titel mit "Official Video": viele Musikvideos heissen einfach
    # "Kuenstler - Titel". Geprueft wird alles, was nicht vom Topic-Kanal
    # (reine Audio-Uploads) kommt und nicht als Audio/Lyrics markiert ist —
    # _pick_song_version nimmt nur eine Song-Version, die wirklich passt.
    def needs_check(e):
        t = e["title"] or ""
        if search._VIDEO_TITLE_RE.search(t):
            return True
        if (e.get("uploader") or "").lower().endswith("- topic"):
            return False
        return not search._AUDIO_TITLE_RE.search(t)

    todo = [e for e in entries if needs_check(e)]
    # Schon in der Sammlung (Verlauf, Bibliothek) oder schon bekannt: keine
    # Suche. Bei grossen Playlists ist meist das meiste schon da.
    if todo:
        todo = await asyncio.get_running_loop().run_in_executor(
            None, _audit_unknown, todo, known or set())
    if progress:
        await progress("search", 0, len(todo))
    try:
        await asyncio.gather(*(replace(e, len(todo)) for e in todo))
    finally:
        search._save_ytm_cache()
    return entries, pl_title


_AUDIT_PARALLEL = 6

def _audit_unknown(entries: list[dict], known: set) -> list[dict]:
    """Die Eintraege, fuer die eine Song-Version gesucht werden muss: nicht
    schon bekannt, nicht im Verlauf, nicht in der Bibliothek."""
    hist = {h["url"]: h for h in _state.get("history", [])}
    idx = search._library_index()
    out = []
    for e in entries:
        if _vid_of(e["url"]) in known:
            continue
        h = hist.get(e["url"])
        if h and os.path.exists(h.get("path", "")):
            continue
        if search._library_matches({"title": e.get("title", ""), "duration": e.get("duration")}, limit=1, index=idx):
            continue
        out.append(e)
    return out


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
        video = await search._probe_video(url)
        song = None
        if video and check_dupes:
            matches = search._library_matches(video)
        if video and not matches and search._is_single_youtube_video(url) \
                and "- topic" not in video["uploader"].lower() \
                and (not search._AUDIO_TITLE_RE.search(video["title"]) or search._MV_TITLE_RE.search(video["title"])):
            query = search._song_query(video["title"])
            if query:
                song = search._pick_song_version(video, await search._ytm_song_search(query))
    finally:
        await _send("video_check_done", url=url)

    if matches:
        await _send("dupe_choice", url=url, format=fmt,
                    video={k: video.get(k, "") for k in ("title", "artist", "uploader", "duration")},
                    matches=[{k: m.get(k) for k in ("path", "title", "artist", "folder", "duration_sec",
                                                     "bitrate_kbps", "cutoff_khz")} for m in matches])
        return
    if video and search._needs_video_choice(video, song):
        await _send("video_choice", url=url, format=fmt,
                    video={k: video[k] for k in ("title", "uploader", "duration")},
                    song={"url": song["url"], "title": song["title"],
                          "uploader": song.get("artist") or song["uploader"], "duration": song["duration"]})
        return
    asyncio.create_task(run_download(url, fmt))

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
    await core.push_downloads(force=True)

    loop = asyncio.get_running_loop()
    spotdl_cmd = await loop.run_in_executor(None, tools._find_spotdl_cmd)

    if not spotdl_cmd:
        hdr["status"]      = "error"
        hdr["status_text"] = "spotdl nicht gefunden"
        hdr["error_msg"]   = "spotdl ist nicht installiert"
        # Sagt dem Frontend, welcher Einstellungen-Tab das Problem loest
        hdr["fix_tab"]     = "services"
        await core.push_downloads(force=True)
        return

    out_dir = _state.get("download_dir", str(core.BASE_DIR / "Downloads"))
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
    if core.FFMPEG and core.FFMPEG != "ffmpeg" and os.path.exists(core.FFMPEG):
        cmd += ["--ffmpeg", core.FFMPEG]
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
                await core.push_downloads()
                continue
            m = re.search(r'(?:Downloaded|Skipping)\s+[““”]?([^”“”\n]+?)[““”]?(?:\s+to\s+|$)', line, re.IGNORECASE)
            if m:
                track_done += 1
                title = m.group(1).strip()
                hdr['track_n']     = track_done
                hdr['title']       = title[:80]
                hdr['status_text'] = title[:60]
                hdr['progress']    = (track_done / track_total * 100) if track_total else 50
                await core.push_downloads()
                continue
            m = re.search(r'Downloading\s+(\d+)\s+songs?', line, re.IGNORECASE)
            if m and not track_total:
                track_total = int(m.group(1))
                hdr['track_total'] = track_total
                await core.push_downloads()

        await proc.wait()
        _dl_procs.pop(session_id, None)

        if hdr in _state['downloads']:
            if proc.returncode == 0 or track_done > 0:
                hdr['status']      = 'done'
                hdr['status_text'] = '✓ ' + str(track_done) + ' Titel'
                hdr['progress']    = 100
                asyncio.create_task(library.scan_folder(out_dir))
            else:
                err_msg = 'spotdl Fehler'
                for ln in reversed(error_lines):
                    clean = re.sub(r'\x1b\[[0-9;]*m', '', ln).strip()
                    if clean and len(clean) < 120:
                        err_msg = clean
                        break
                hdr['status']      = 'error'
                hdr['status_text'] = err_msg
            await core.push_downloads(force=True)
    except Exception as exc:
        _dl_procs.pop(session_id, None)
        if hdr in _state["downloads"]:
            hdr["status"]      = "error"
            hdr["status_text"] = f"Fehler: {exc}"
            await core.push_downloads(force=True)


_DL_REASON_TEXT = {
    "geo":   "In diesem Land gesperrt",
    "gone":  "Gelöscht oder privat",
    "rate":  "YouTube bremst gerade – später nochmal",
    "bot":   "YouTube verlangt eine Anmeldung – später nochmal",
    "age":   "Altersbeschränkt – nur mit Anmeldung",
    "other": "Fehler beim Laden",
}
_PLACEHOLDER_TITLE_RE = re.compile(r'^\[(deleted|private|unavailable)\s+video\]$', re.I)

# ── Playlist-Kaestchen: vor dem Laden pruefen und fragen ──────────────────────
# "140 Titel · 132 schon in deiner Sammlung · 8 neu" — dann: als Playlist
# anlegen (nur neue laden, Playlist mit allen), nur neue laden, oder alle laden.
_plans: dict[int, dict] = {}
_plan_counter = 0
_plan_tasks: dict = {}      # url -> laufende Pruefung (abbrechbar)

def _folder_name(title: str) -> str | None:
    t = (title or "").strip()
    return (re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', t)[:80].rstrip(' .') or None) if t else None

def _plan_stats(entries: list[dict]) -> dict:
    """Wie viele Titel sind neu, schon in der Sammlung, doppelt in der Playlist."""
    hist = {h["url"]: h for h in reversed(_state.get("history", []))}
    idx = search._library_index()
    seen_vid, seen_keys = set(), []
    have = dupes = 0
    for e in entries:
        vid = _vid_of(e["url"])
        key = search._song_key(e.get("title", ""), "", e.get("duration"))
        if (vid and vid in seen_vid) or any(search._same_song(key, k) for k in seen_keys):
            dupes += 1
            continue
        if vid:
            seen_vid.add(vid)
        seen_keys.append(key)
        h = hist.get(e["url"])
        if (h and os.path.exists(h.get("path", ""))) or \
                search._library_matches({"title": e.get("title", ""), "duration": e.get("duration")}, limit=1, index=idx):
            have += 1
    total = len(entries)
    return {"total": total, "have": have, "dupes": dupes, "new": total - have - dupes}

async def _plan_playlist(url: str, fmt: str, ws):
    async def _send(t, **kw):
        try: await ws.send_text(json.dumps({"type": t, **kw}))
        except Exception: pass
    global _plan_counter
    await _send("playlist_plan_pending", url=url)
    last = [0.0]

    async def progress(phase, done, total):
        now = time.monotonic()
        if done and done != total and now - last[0] < 0.25:
            return
        last[0] = now
        await _send("playlist_plan_progress", url=url, phase=phase, done=done, total=total)

    _plan_tasks[url] = asyncio.current_task()
    try:
        entries, title = await _audit_playlist_for_videos(url, progress)
    except asyncio.CancelledError:
        await _send("playlist_plan_cancel", url=url)
        return
    except Exception:
        entries, title = [], ""
    finally:
        _plan_tasks.pop(url, None)
    if not entries:
        # Pruefung ging nicht: wie frueher einfach laden
        await _send("playlist_plan_cancel", url=url)
        asyncio.create_task(run_download(url, fmt))
        return
    if not title:
        try: title = (await _playlist_probe(url)).get("playlist_title") or ""
        except Exception: title = ""
    stats = await asyncio.get_running_loop().run_in_executor(None, _plan_stats, entries)
    _plan_counter += 1
    pid = _plan_counter
    now = time.time()
    for k in [k for k, v in _plans.items() if now - v["ts"] > 3600]:
        _plans.pop(k, None)
    _plans[pid] = {"url": url, "fmt": fmt, "title": title, "entries": entries, "ts": now}
    await _send("playlist_plan", plan_id=pid, url=url, format=fmt, title=title,
                followed=any(f["url"] == url for f in _state.get("followed", [])), **stats)

def _playlist_tracks(entries: list[dict], lib_idx: list) -> list[tuple]:
    """Pfade aller Titel einer Playlist in deren Reihenfolge: geladen, schon in
    der Sammlung (Verlauf oder Abgleich) oder als doppelter Eintrag."""
    hist = {h["url"]: h for h in reversed(_state.get("history", []))}
    lib = {lt.get("path"): lt for lt in _state.get("library", [])}
    out, seen = [], set()
    for e in entries:
        p = e.get("_path") or (e.get("_dupe_of") or {}).get("_path")
        if not p:
            h = hist.get(e["url"])
            if h and os.path.exists(h.get("path", "")):
                p = h["path"]
        if not p:
            m = search._library_matches({"title": e.get("title", ""), "duration": e.get("duration")}, limit=1, index=lib_idx)
            p = m[0]["path"] if m else None
        if p and p not in seen and os.path.exists(p):
            seen.add(p)
            lt = lib.get(p, {})
            out.append((p, lt.get("title") or Path(p).stem, int(lt.get("duration_sec") or e.get("duration") or -1)))
    return out

def _link_into(src: str, target_dir: str) -> tuple[str, str]:
    """Vorhandene Datei in einen Ordner bringen, ohne neu zu laden.
    Hardlink (gleiche Datei, kein Platz), sonst Kopie (anderes Laufwerk,
    FAT/USB). Liefert (Pfad, "link" | "copy" | "da")."""
    os.makedirs(target_dir, exist_ok=True)
    dest = os.path.join(target_dir, os.path.basename(src))
    if os.path.exists(dest):
        # Liegt schon da — derselbe Song (frueherer Lauf), nichts tun
        return dest, "da"
    try:
        os.link(src, dest)
        return dest, "link"
    except OSError:
        shutil.copy2(src, dest)
        return dest, "copy"

def _add_linked_entry(src_lt: dict, dest: str, how: str):
    """Bibliothekseintrag fuer die verknuepfte/kopierte Datei — mit allen
    Messwerten der Quelle, damit nichts neu analysiert werden muss."""
    if any(lt.get("path") == dest for lt in _state["library"]):
        return
    e = {k: v for k, v in src_lt.items() if k not in ("missing", "quality_ok")}
    e["path"] = dest
    e["folder"] = Path(dest).parent.name
    e["play_count"] = 0
    e["fid"] = src_lt.get("fid") or media._file_id(src_lt.get("path", "")) if how == "link" else media._file_id(dest)
    if how == "link" and not src_lt.get("fid") and e["fid"]:
        src_lt["fid"] = e["fid"]
    _state["library"].append(e)

def _write_m3u(dest: Path, tracks: list[tuple]):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for path, title, dur in tracks:
            f.write(f"#EXTINF:{dur},{title}\n{path}\n")

async def _save_as_playlist(name: str, folder: str | None, tracks: list[tuple]) -> int:
    """SynthiMIX-Playlist (links unter "Playlisten") plus .m3u8 im Download-
    Ordner fuer rekordbox / Virtual DJ. Liefert die Anzahl Titel."""
    if not tracks or not name:
        return 0
    safe = re.sub(r'[<>:"/\\|?*]', '_', name).strip(' .')[:80] or "Playlist"
    _write_m3u(core.PLAYLISTS_DIR / (safe + ".m3u"), tracks)
    out_dir = Path(_state.get("download_dir", str(core.BASE_DIR / "Downloads")))
    try:
        _write_m3u((out_dir / folder if folder else out_dir) / (safe + ".m3u8"), tracks)
    except Exception as e:
        print(f"[playlist] .m3u8 nicht geschrieben: {e}", flush=True)
    await core.broadcast({"type": "playlists", "items": library._get_playlists()})
    return len(tracks)

# ── Playlists verfolgen ──────────────────────────────────────────────────────
# Eine YouTube-(Music-) oder Spotify-Playlist wird beim Start und auf
# Knopfdruck geprueft: nur neue Titel werden geladen (Studio-Version statt
# Musikvideo, nichts, was schon in der Bibliothek ist), danach direkt analysiert.
_follow_running: set[str] = set()

def _vid_of(u: str) -> str:
    m = re.search(r'(?:[?&]v=|youtu\.be/|/shorts/)([\w-]{11})', u or "")
    return m.group(1) if m else ""

def _follow_public() -> list[dict]:
    out = []
    for f in _state.get("followed", []):
        g = {k: v for k, v in f.items() if k != "seen" and not k.startswith("_")}
        g["known"] = len(f.get("seen") or [])
        g["checking"] = f["url"] in _follow_running
        out.append(g)
    return out

async def _push_followed():
    await core.broadcast({"type": "followed", "items": _follow_public(), "channels": channels._channels_public()})

async def _follow_check(f: dict):
    url = f["url"]
    if url in _follow_running:
        return
    _follow_running.add(url)
    await _push_followed()
    try:
        fmt = f.get("fmt") or "mp3-best"
        if _is_spotify(url):
            vorher = {lt.get("path") for lt in _state["library"]}
            await run_spotify_download(url, fmt)
            await asyncio.sleep(2)
            neu = [lt["path"] for lt in _state["library"] if lt.get("path") not in vorher]
        else:
            await run_download(url, fmt, follow=f, mode=f.get("mode") or "new")
            neu = f.pop("_new_paths", [])
            seen = list(f.get("seen") or [])
            seen += [v for v in f.pop("_seen_add", []) if v not in seen]
            f["seen"] = seen[-5000:]
        f["last_check"] = int(time.time())
        f["last_new"] = len(neu)
        if neu:
            await asyncio.sleep(2)            # Bibliothek traegt neue Dateien nach
            asyncio.create_task(media._analyze_library_meta_task(neu))
    except Exception as e:
        print(f"[verfolgen] {url}: {e}", flush=True)
    finally:
        _follow_running.discard(url)
        store.save_settings()
        await _push_followed()

async def _follow_check_all(urls: list[str] | None = None):
    # Verfolgte Kanaele zuerst: neue Playlists kommen so gleich mit dran
    if urls is None:
        for ch in list(_state.get("followed_channels", [])):
            await channels._channel_check(ch)
    else:
        extra = []
        for u in urls:
            ch = channels._channel_of(u)
            if ch:
                extra += await channels._channel_check(ch)
        urls = list(urls) + extra
    for f in list(_state.get("followed", [])):
        if urls is None or f["url"] in urls:
            await _follow_check(f)

async def _follow_startup():
    await asyncio.sleep(120)          # erst Start, Tag-Abgleich und Wiedergabe
    if _state.get("followed") or _state.get("followed_channels"):
        print(f"[verfolgen] pruefe {len(_state.get('followed', []))} Playlist(s), "
              f"{len(_state.get('followed_channels', []))} Kanal/Kanaele", flush=True)
        await _follow_check_all()

def _dl_error_reason(msg: str) -> str:
    """yt-dlp-Fehlermeldung → Grund (Schluessel von _DL_REASON_TEXT)."""
    m = (msg or "").lower()
    if ("in your country" in m or "geo restrict" in m or "geo-restrict" in m
            or "not available in your location" in m):
        return "geo"
    if "try again later" in m or "content isn't available" in m or "http error 429" in m or "rate-limit" in m:
        return "rate"
    if "confirm you" in m and "bot" in m:
        return "bot"
    if "confirm your age" in m or "age-restricted" in m or "inappropriate for some users" in m:
        return "age"
    if ("private video" in m or "has been removed" in m or "video unavailable" in m
            or "no longer available" in m or "account associated" in m or "terminated" in m):
        return "gone"
    return "other"

async def run_download(url: str, fmt_id: str = "mp3-best", entries: list[dict] | None = None,
                       folder: str | None = None, label: str | None = None,
                       follow: dict | None = None, mode: str = "new",
                       follow_new: bool = False) -> str | None:
    """Returns the final output path on success, None on failure.

    entries/folder/label: fertige Titelliste statt einer Adresse (z. B.
    "Fehlgeschlagene nochmal laden") — wird wie eine Playlist geladen."""
    global _dl_counter
    out_dir = _state.get("download_dir", str(core.BASE_DIR / "Downloads"))
    os.makedirs(out_dir, exist_ok=True)

    # ── session label + playlist folder name ─────────────────────────────────
    playlist_folder: str | None = None
    if entries is not None:
        playlist_folder = folder
        slabel = label or "Erneut laden"
    elif url.startswith("http") and _is_playlist(url):
        # Vorlaeufige Beschriftung; den echten Namen liefert gleich die Playlist-Pruefung
        m = re.search(r'list=([^&]+)', url)
        slabel = f"Playlist · {m.group(1)[:28]}" if m else url[:60]
    elif url.startswith("http"):
        slabel = url[:60]
    else:
        slabel = url[:60]

    async def _playlist_title_fallback() -> str:
        try:
            pr = await asyncio.create_subprocess_exec(
                *core._yt('--print', 'playlist_title', '--playlist-items', '1',
                     '--no-warnings', '--quiet', '--encoding', 'utf-8', url),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                creationflags=_NO_WINDOW)
            out, _ = await asyncio.wait_for(pr.communicate(), timeout=30)
            t = out.decode("utf-8", errors='replace').strip().splitlines()[0] if out else ''
            return "" if t in ('NA', 'N/A') else t
        except Exception:
            return ""

    def _use_playlist_title(title: str):
        """Ordnername und Beschriftung aus dem Playlist-Namen."""
        nonlocal playlist_folder, slabel
        title = (title or "").strip()
        if not title:
            return
        playlist_folder = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)[:80].rstrip(' .') or None
        # Playlists eines verfolgten Kanals: Downloads/<Kanal>/<Playlist>
        parent = (follow or {}).get("parent")
        if playlist_folder and parent:
            parent = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', parent)[:80].rstrip(' .')
            if parent:
                playlist_folder = os.path.join(parent, playlist_folder)
        slabel = title[:60]
        hdr["session_label"] = slabel
        hdr["title"] = slabel
        hdr["folder"] = playlist_folder

    # ── session header item  (id == session_id identifies it as header) ───────
    _dl_counter += 1
    session_id = _dl_counter

    # For single-track HTTP URLs, title starts empty and gets filled from filename
    hdr_title = slabel if (entries is not None or not (url.startswith("http") and not _is_playlist(url))) else ""
    hdr: dict = {
        "id":            session_id,
        "session":       session_id,
        "session_label": slabel,
        "title":         hdr_title,
        "url":           url,
        "fmt":           fmt_id,
        "folder":        playlist_folder,
        "follow":        bool(follow),
        "mode":          mode,
        "path":          None,
        "track_n":       0,
        "track_total":   0,
        "progress":      0,
        "status":        "active",
        "status_text":   "Starte…",
        "error_msg":     "",
    }
    _state["downloads"].insert(0, hdr)      # always at top of list
    await core.push_downloads(force=True)

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
            asyncio.create_task(media._auto_add_to_library(item["path"]))

    track_n     = 0
    track_total = 0
    last_pct    = -1.0
    cur         = hdr   # points to the current track item (or header for singles)
    err_lines: list[str] = []   # ERROR-Zeilen von yt-dlp, fuer die Fehlermeldung

    # Build URL→history lookup once for O(1) smart-skip (newest entry wins)
    _hist_by_url = {h["url"]: h for h in reversed(_state.get("history", []))}

    # Smart-skip: only for single tracks (not playlists — playlists may have new entries)
    if entries is None and not (url.startswith("http") and _is_playlist(url)):
        h = _hist_by_url.get(url)
        if h and h.get("bitrate_kbps", 0) >= 192:
            p = h.get("path", "")
            if p and os.path.exists(p):
                hdr["status"]      = "done"
                hdr["status_text"] = f"⏭ Bereits vorhanden ({h['bitrate_kbps']} kbps)"
                hdr["progress"]    = 100
                hdr["path"]        = p
                hdr["title"]       = h.get("title") or Path(p).stem
                await core.push_downloads(force=True)
                await media._auto_add_to_library(p)
                return p  # return path so callers (e.g. automix) can still queue it

    # ── Playlist: flat-list once, replace video entries, then download per-URL ─
    # Using per-URL mode always avoids a second internal flat-list by yt-dlp.
    _audited_entries: list[dict] | None = None
    if entries is not None:
        _audited_entries = [dict(e) for e in entries]
    elif url.startswith("http") and _is_playlist(url):
        hdr["status_text"] = "Playlist analysieren…"
        await core.push_downloads(force=True)
        async def _an_progress(phase, done, total):
            txt = (f"Playlist einlesen… {done}" if phase == "list"
                   else f"Song-Versionen suchen… {done}/{total}" if total else "Playlist analysieren…")
            if hdr.get("status_text") != txt:
                hdr["status_text"] = txt
                await core.push_downloads()
        entries, pl_title = await _audit_playlist_for_videos(
            url, _an_progress, known=set((follow or {}).get("seen") or []))
        _use_playlist_title(pl_title or await _playlist_title_fallback())
        if entries:
            replaced_count = sum(1 for e in entries if e["replaced"])
            _audited_entries = entries  # always use per-URL mode → one flat-list total
            hdr["status_text"] = f"Starte… ({replaced_count} Videos ersetzt)" if replaced_count else "Starte…"
        else:
            hdr["status_text"] = "Starte…"
        await core.push_downloads(force=True)

    # ── Playlist: mehrere yt-dlp-Prozesse parallel ─────────────────────────
    # Frueher ein Prozess je Titel, nacheinander: bei 140 Titeln sass man
    # Minuten allein auf Programmstarts. Jetzt teilen sich _DL_PARALLEL
    # Prozesse die Titel (je eine Liste per -a), zugeordnet wird die Ausgabe
    # ueber die Video-ID in "[youtube] Extracting URL: …".
    if _audited_entries is not None:
        track_total = len(_audited_entries)
        hdr["track_total"] = track_total
        state = {"done": 0, "final": None, "failed": [], "new": []}
        _dl_procs[session_id] = []

        async def _finish(item: dict, entry: dict, ok: bool, skipped: bool = False):
            if item.get("status") == "done" or item.get("_fin"):
                return
            item["_fin"] = True
            path = item.get("path")
            ok = ok and bool(path) and os.path.exists(path)
            _done(item, ok=ok, skipped=skipped)
            item["url"] = item.get("url") or entry.get("url", "")
            if ok and entry.get("_orig"):
                item["status_text"] = "✓ Ersatz geladen"
                item["title"] = (item.get("title") or "")[:80]
                item.pop("reason", None)
            if not ok and not skipped:
                reason = _dl_error_reason(item.get("error_msg") or "")
                item["status"] = "error"
                item["reason"] = reason
                item["status_text"] = _DL_REASON_TEXT[reason]
                state["failed"].append((item, entry.get("_orig") or entry))
            if item.get("_counted"):
                await core.push_downloads(force=True)
                return
            item["_counted"] = True
            state["done"] += 1
            hdr["track_n"] = state["done"]
            hdr["status_text"] = f"{state['done']} / {track_total}"
            hdr["progress"] = round(state["done"] / track_total * 100)
            if ok:
                state["final"] = path
                (entry.get("_orig") or entry)["_path"] = path
                if not skipped:
                    state["new"].append(path)
                    probe = await asyncio.get_running_loop().run_in_executor(None, media._probe_sync, path)
                    store._append_history(entry["url"], item.get("title", ""), path, probe.get("bitrate_kbps", 0))
                    await core.broadcast(store._history_payload())
            await core.push_downloads(force=True)

        def _vid(u: str) -> str:
            m = re.search(r'(?:[?&]v=|youtu\.be/|/shorts/)([\w-]{11})', u or "")
            return m.group(1) if m else ""

        # Gar nicht erst laden:
        # - schon geladen (Verlauf mit guter Qualitaet)
        # - derselbe Song steht ein zweites Mal in der Playlist (z. B. Musikvideo
        #   und Lyric-Video) — sonst lagen zwei Dateien desselben Songs im Ordner
        # - der Song ist schon in der Bibliothek
        lib_idx = await asyncio.get_running_loop().run_in_executor(None, search._library_index)
        seen_vid: set = set()
        seen_keys: list = []
        seen_keys_e: list = []                          # (Schluessel, Eintrag)
        first_by_vid: dict = {}
        todo = []
        # Verfolgte Playlist: schon einmal verarbeitete Titel gar nicht anfassen —
        # auch wenn die Datei inzwischen geloescht wurde (dann bewusst)
        known = set((follow or {}).get("seen") or [])
        all_entries = list(_audited_entries)          # fuer die Playlist mit allen Titeln
        if follow is not None:
            vor = len(_audited_entries)
            _audited_entries = [e for e in _audited_entries
                                if _vid(e["url"]) not in known and _vid(e.get("src") or "") not in known]
            track_total = len(_audited_entries)
            hdr["track_total"] = track_total
            if not _audited_entries:
                hdr["status"] = "done"; hdr["progress"] = 100
                hdr["status_text"] = f"✓ nichts Neues ({vor} bekannt)"
                if mode == "playlist":
                    n_pl = await _save_as_playlist(label or slabel, playlist_folder,
                                                   _playlist_tracks(all_entries, lib_idx))
                    if n_pl:
                        hdr["status_text"] += f" · Playlist mit {n_pl} Titeln"
                follow["_new_paths"] = []
                follow["_seen_add"] = []
                _dl_procs.pop(session_id, None)
                await core.push_downloads(force=True)
                return None
        target_dir = os.path.join(out_dir, playlist_folder) if playlist_folder else out_dir
        lib_by_path = {lt.get("path"): lt for lt in _state["library"]}
        linked_any = False

        async def _link_existing(src_lt: dict, entry: dict) -> None:
            nonlocal linked_any
            dest, how = await asyncio.get_running_loop().run_in_executor(
                None, _link_into, src_lt["path"], target_dir)
            item = _new_track(Path(dest).stem)
            item["path"] = dest
            await _finish(item, entry, ok=True, skipped=True)
            item["status_text"] = {"link": "↪ verknüpft", "copy": "⧉ kopiert"}.get(how, "✓ schon im Ordner")
            if how in ("link", "copy"):
                _add_linked_entry(src_lt, dest, how)
                linked_any = True
            state["linked"] = state.get("linked", 0) + 1

        for entry in _audited_entries:
            h = _hist_by_url.get(entry["url"])
            p = (h or {}).get("path", "")
            if h and h.get("bitrate_kbps", 0) >= 192 and p and os.path.exists(p) and mode != "all":
                if mode == "folder":
                    await _link_existing(lib_by_path.get(p) or {"path": p, "title": Path(p).stem}, entry)
                    continue
                item = _new_track(h.get("title") or Path(p).stem)
                item["path"] = p
                await _finish(item, entry, ok=True, skipped=True)
                continue
            vid = _vid(entry["url"])
            key = search._song_key(entry.get("title", ""), "", entry.get("duration"))
            first = first_by_vid.get(vid) if vid else None
            if first is None:
                first = next((fe for k, fe in seen_keys_e if search._same_song(key, k)), None)
            if first is not None:
                entry["_dupe_of"] = first
                item = _new_track(entry.get("title") or "")
                await _finish(item, entry, ok=False, skipped=True)
                item["status_text"] = "↷ doppelt in der Playlist"
                state["skipped_dupe"] = state.get("skipped_dupe", 0) + 1
                continue
            if vid:
                seen_vid.add(vid)
                first_by_vid[vid] = entry
            seen_keys.append(key)
            seen_keys_e.append((key, entry))
            m = [] if mode == "all" else search._library_matches(
                {"title": entry.get("title", ""), "duration": entry.get("duration")}, limit=1, index=lib_idx)
            if m and mode == "folder":
                await _link_existing(m[0], entry)
                continue
            if m:
                item = _new_track(entry.get("title") or "")
                item["path"] = m[0]["path"]
                await _finish(item, entry, ok=True, skipped=True)
                item["status_text"] = "✓ schon in der Bibliothek"
                state["skipped_lib"] = state.get("skipped_lib", 0) + 1
                continue
            todo.append(entry)
        if linked_any:
            store.save_library()
            await core.push_library()

        async def _batch(batch: list[dict], n: int):
            if not batch or hdr not in _state["downloads"]:
                return
            by_id = {_vid(e["url"]): e for e in batch}
            listfile = Path(tempfile.gettempdir()) / f"synthimix-dl-{session_id}-{n}.txt"
            listfile.write_text("\n".join(e["url"] for e in batch) + "\n", "utf-8")
            cmd = _ytdlp_cmd(batch[0]["url"], fmt_id, out_dir, playlist_folder, force_folder=True)
            cmd = cmd[:-1] + ["-a", str(listfile)]           # URL durch die Liste ersetzen
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            ep = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env=env, creationflags=_NO_WINDOW)
            _dl_procs[session_id].append(ep)
            cur_item, cur_entry, last_pct = None, None, -1.0
            try:
                async for raw in ep.stdout:
                    if hdr not in _state["downloads"]:
                        core._kill_quietly(ep)
                        break
                    line = raw.decode('utf-8', errors='replace').strip()
                    if not line:
                        continue
                    try:
                        if "Extracting URL:" in line:
                            e = by_id.get(_vid(line))
                            if e is not None:
                                if cur_item is not None:
                                    await _finish(cur_item, cur_entry, ok=True)
                                cur_entry, last_pct = e, -1.0
                                if e.get("_item") is not None:
                                    cur_item = e["_item"]
                                    cur_item.update(status="active", progress=0, path=None,
                                                    error_msg="", _fin=False,
                                                    status_text=e.get("_note") or "…")
                                else:
                                    cur_item = _new_track(e["title"])
                                    cur_item["url"] = e["url"]
                                if e.get("replaced") and not e.get("_item"):
                                    cur_item["status_text"] = "Audio-Version"
                                await core.push_downloads(force=True)
                            continue
                        if cur_item is None:
                            continue
                        if "[download] Destination:" in line:
                            fname = line.split("Destination:", 1)[1].strip()
                            cur_item["title"] = Path(fname).stem[:80]; cur_item["path"] = fname
                        elif "[ExtractAudio] Destination:" in line:
                            fname = line.split("Destination:", 1)[1].strip()
                            cur_item["path"] = fname; cur_item["title"] = Path(fname).stem[:80]
                        elif "has already been downloaded" in line:
                            fname = line.split("] ", 1)[-1].split(" has")[0].strip()
                            cur_item["title"] = Path(fname).stem[:80]; cur_item["path"] = fname
                            await _finish(cur_item, cur_entry, ok=True, skipped=True)
                        elif line.startswith("ERROR"):
                            cur_item["error_msg"] = line[:200]
                        elif "[download]" in line and "%" in line:
                            pct = float(line.split("%")[0].split()[-1])
                            if abs(pct - last_pct) >= 4.0:
                                last_pct = pct; cur_item["progress"] = pct
                                cur_item["status_text"] = f"{pct:.0f}%"
                                await core.push_downloads()
                    except Exception:
                        pass
                await ep.wait()
                if cur_item is not None:
                    await _finish(cur_item, cur_entry, ok=True)
            finally:
                try: listfile.unlink()
                except Exception: pass

        batches = [todo[i::_DL_PARALLEL] for i in range(_DL_PARALLEL)]
        await asyncio.gather(*(_batch(b, n) for n, b in enumerate(batches)))

        # Gesperrt (Land) oder geloescht: eine andere Version desselben Songs
        # suchen, die hier verfuegbar ist. Gebremst: nach einer Pause noch
        # einmal. Umgangen wird nichts — nur ein anderer Upload genommen.
        for runde in range(2):
            if hdr not in _state["downloads"]:
                break
            failed, state["failed"] = state["failed"], []
            second: list[dict] = []
            wait_rate = False
            for item, orig in failed:
                reason = item.get("reason")
                if reason == "rate" and runde == 0:
                    second.append({**orig, "_item": item, "_note": "zweiter Versuch…"})
                    wait_rate = True
                    continue
                if reason not in ("geo", "gone"):
                    continue
                t = (orig.get("title") or "").strip()
                if not t or _PLACEHOLDER_TITLE_RE.match(t):
                    continue
                cands = orig.get("_cands")
                if cands is None:
                    hdr["status_text"] = f"Suche Ersatz: {t[:40]}"
                    await core.push_downloads(force=True)
                    try:
                        found = await search._ytm_songs(search._song_query(t) or t, 5, details=True)
                    except Exception:
                        found = []
                    dur = orig.get("duration") or 0
                    cands = [c for c in found if _vid(c.get("url", "")) and _vid(c["url"]) != _vid(orig["url"])
                             and (not dur or not c.get("duration") or abs(c["duration"] - dur) <= 20)]
                    orig["_cands"] = cands
                if runde < len(cands):
                    c = cands[runde]
                    second.append({"url": c["url"], "title": c.get("title") or t, "replaced": True,
                                   "_item": item, "_orig": orig, "_note": "Ersatz wird geladen…"})
            if not second:
                break
            if wait_rate:
                hdr["status_text"] = "YouTube bremst — kurze Pause…"
                await core.push_downloads(force=True)
                await asyncio.sleep(45)
            await _batch(second, 90 + runde)     # ein Prozess, schonend

        fehl = [d for d in _state["downloads"] if d.get("session") == session_id
                and d.get("id") != session_id and d.get("status") == "error"]
        ersetzt = sum(1 for d in _state["downloads"] if d.get("session") == session_id
                      and d.get("status_text") == "✓ Ersatz geladen")
        ok_n = state["done"] - len(fehl)
        hdr["status"] = "done"; hdr["progress"] = 100
        hdr["failed_n"] = len(fehl)
        if follow is not None:
            # Alles ausser Fehlgeschlagenem gilt als verarbeitet
            fehl_urls = {d.get("url") for d in fehl}
            # Auch die Original-ID (vor dem Ersetzen): dann muss beim naechsten
            # Pruefen fuer bekannte Titel nichts mehr gesucht werden
            follow["_seen_add"] = [v for e in _audited_entries if e["url"] not in fehl_urls
                                   for v in (_vid(e["url"]), _vid(e.get("src") or "")) if v]
            follow["_new_paths"] = list(state["new"])
        vorhanden = state.get("skipped_lib", 0) + state.get("skipped_dupe", 0)
        verkn = state.get("linked", 0)
        ok_n -= vorhanden + verkn
        hdr["status_text"] = (f"✓ {ok_n} neu" + (f" · {verkn} verknüpft" if verkn else "")
                              + (f" · {vorhanden} schon da" if vorhanden else "")
                              + (f" · {ersetzt} ersetzt" if ersetzt else "")
                              + (f" · {len(fehl)} nicht verfügbar" if fehl else ""))
        if mode == "folder":
            # Reihenfolge der Playlist fuer rekordbox / Virtual DJ
            tracks = _playlist_tracks(all_entries, lib_idx)
            if tracks:
                safe = re.sub(r'[<>:"/\\|?*]', '_', label or slabel).strip(' .')[:80] or "Playlist"
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, _write_m3u, Path(target_dir) / (safe + ".m3u8"), tracks)
                except Exception as e:
                    print(f"[playlist] .m3u8 nicht geschrieben: {e}", flush=True)
        if mode == "playlist":
            # Neue Dateien sind im Index noch nicht drin — sie tragen ihren Pfad am Eintrag
            n_pl = await _save_as_playlist(label or slabel, playlist_folder,
                                           _playlist_tracks(all_entries, lib_idx))
            if n_pl:
                hdr["status_text"] += f" · Playlist mit {n_pl} Titeln"
        if follow_new and url.startswith("http") and not any(f["url"] == url for f in _state.setdefault("followed", [])):
            fehl_urls = {d.get("url") for d in fehl}
            _state["followed"].append({
                "url": url, "title": (label or slabel)[:80], "fmt": fmt_id, "folder": playlist_folder,
                "mode": mode if mode != "all" else "folder", "added": int(time.time()),
                "last_check": int(time.time()), "last_new": ok_n,
                "seen": [v for e in all_entries if e["url"] not in fehl_urls
                         for v in (_vid(e["url"]), _vid(e.get("src") or "")) if v],
            })
            store.save_settings()
            await _push_followed()
        _dl_procs.pop(session_id, None)
        await core.push_downloads(force=True)
        return state["final"]

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
                    await core.push_downloads(force=True)

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
                    await core.push_downloads(force=True)

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
                    await core.push_downloads(force=True)

                # ── destination file ──────────────────────────────────────────
                elif "[download] Destination:" in line:
                    fname        = line.split("Destination:", 1)[1].strip()
                    cur["title"] = Path(fname).stem[:80]
                    cur["path"]  = fname
                    if cur is hdr:
                        hdr["title"] = cur["title"]
                    await core.push_downloads()

                # ── extracted audio path ──────────────────────────────────────
                elif "[ExtractAudio] Destination:" in line:
                    fname        = line.split("Destination:", 1)[1].strip()
                    cur["path"]  = fname
                    cur["title"] = Path(fname).stem[:80]
                    if cur is hdr:
                        hdr["path"]  = fname
                        hdr["title"] = cur["title"]
                    await core.push_downloads(force=True)

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
                        await core.push_downloads()   # throttled to 200 ms

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
        probe = await loop.run_in_executor(None, media._probe_sync, hdr["path"])
        store._append_history(url, hdr.get("title", ""), hdr["path"], probe.get("bitrate_kbps", 0))
        await core.broadcast(store._history_payload())
        # Einzelne Downloads durchlaufen kein _done() — ohne das hier fehlten
        # sie in der Bibliothek, bis irgendwann ein Scan lief.
        await media._auto_add_to_library(hdr["path"])

    await core.push_downloads(force=True)
    return hdr.get("path") if hdr.get("path") and os.path.exists(hdr.get("path", "")) else None
