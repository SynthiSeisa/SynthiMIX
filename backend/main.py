"""
SynthiMIX-Backend — FastAPI + WebSocket.

Einstiegspunkt: App, Nachrichten der Oberflaeche (handle_message),
WebSocket. Die eigentliche Arbeit steckt im Paket synthimix/.
"""
import asyncio
import json
import os
import random
import re
import sys
import time
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from synthimix.core import _state, clients
from synthimix import automix, beatgrid, channels, core, download, keys, library, media, quality, relocate, remote, search, store, tags, tools

# ── app ──────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    store.load_queue()
    store.load_library()
    store.load_settings()
    store.load_history()
    store.load_play_log()
    store.load_notes()
    remote.load_wishes()
    remote._resume_wishes()
    core.spawn(library._watcher_loop())
    core.spawn(library._auto_scan_loop())
    core.spawn(tools._ytdlp_autoupdate_loop())
    core.spawn(media._refresh_tag_meta_loop())
    core.spawn(quality._quality_scan_loop())
    core.spawn(media._loud_main_loop())
    core.spawn(media._bpm_fix_loop())
    core.spawn(download._temp_cleanup_loop())
    core.spawn(download._follow_startup())
    core.spawn(library._fileid_scan_task())
    print(f"[backend] ready on ws://127.0.0.1:{core._BACKEND_PORT}/ws", flush=True)
    yield
    # Beenden (auch vor einem Update): nichts Gemessenes verlieren
    try:
        store.save_library()
        store._save_quality_cache()
        search._save_ytm_cache()
        media._save_wf_cache()
    except Exception:
        pass

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── message handler ───────────────────────────────────────────────────────────
_DL_AUDIO_EXT = {'.mp3', '.opus', '.m4a', '.flac', '.wav', '.ogg', '.aac', '.wma'}

def _download_tree_sync(dl_dir: Path) -> dict:
    """Ordnerbaum des Download-Ordners (4 Ebenen tief). os.scandir: unter
    Windows liefert es Typ und Zeiten gleich mit, ohne eigene Abfrage je Datei."""
    def entries(path):
        try:
            with os.scandir(path) as it:
                items = list(it)
        except OSError:
            return [], []
        dirs = sorted((e for e in items if e.is_dir() and not e.name.startswith('.')), key=lambda e: e.name.lower())
        files = []
        for e in items:
            if e.is_file() and os.path.splitext(e.name)[1].lower() in _DL_AUDIO_EXT and not core.is_temp_audio(e.name):
                try:
                    files.append({"path": e.path, "name": os.path.splitext(e.name)[0], "mtime": e.stat().st_mtime})
                except OSError:
                    pass
        return dirs, files

    def folder(path: str, name: str, depth: int) -> dict:
        dirs, files = entries(path)
        return {"name": name, "path": path,
                "tracks": sorted(files, key=lambda x: x["name"].lower()),
                "folders": [folder(d.path, d.name, depth + 1) for d in dirs] if depth < 4 else []}

    tree = {"folders": [], "files": []}
    if dl_dir.exists():
        dirs, files = entries(str(dl_dir))
        tree["folders"] = [folder(d.path, d.name, 0) for d in dirs]
        tree["files"] = sorted(files, key=lambda x: x["name"].lower())
    return tree

async def _send_download_tree(ws: WebSocket):
    dl_dir = Path(_state.get("download_dir", str(core.BASE_DIR / "Downloads")))
    tree = await asyncio.get_running_loop().run_in_executor(None, _download_tree_sync, dl_dir)
    try:
        await ws.send_text(json.dumps({"type": "download_tree", "tree": tree}))
    except Exception:
        pass

async def _send_waveform(ws: WebSocket, kind: str, path: str):
    """waveform / waveform_next / waveform_third (Titel nach dem naechsten:
    gleitet waehrend des Uebergangs mit hoch)."""
    data = await media.compute_waveform(path) if path else []
    try:
        await ws.send_text(json.dumps({"type": kind, "path": path, "data": data}))
    except Exception:
        pass

async def _send_relocate(ws: WebSocket, manual: bool):
    items = await asyncio.get_running_loop().run_in_executor(None, relocate.detect)
    if items or manual:
        try:
            await ws.send_text(json.dumps({"type": "relocate_suggest", "items": items, "manual": manual}))
        except Exception:
            pass

async def _send_changelog(ws: WebSocket):
    items = await asyncio.get_running_loop().run_in_executor(None, tools._changelog_sync)
    try:
        await ws.send_text(json.dumps({"type": "changelog", "items": items}))
    except Exception:
        pass

async def handle_message(ws: WebSocket, msg: dict):
    t = msg.get("type")

    if t == "get_state":
        # Laufwerksbuchstabe geaendert? Nebenher pruefen und ggf. vorschlagen
        core.spawn(_send_relocate(ws, manual=False))
        await ws.send_text(json.dumps({"type": "queue", "items": _state["queue"],
                                       "current_idx": _state["current_idx"]}))
        await core.push_player()
        await core.send_library_full(ws)
        await ws.send_text(json.dumps({"type": "downloads", "items": core.downloads_public()}))
        await ws.send_text(json.dumps({"type": "playlists", "items": library._get_playlists()}))
        # Ohne das hier haette ein frisch gestarteter Client die Wuensche erst
        # gesehen, wenn sich der naechste geaendert hat.
        await ws.send_text(json.dumps({"type": "wishes", "items": _state.get("wishes", [])}))
        await ws.send_text(json.dumps({"type": "watched_folders", "items": library._watched_folders_info()}))
        await ws.send_text(json.dumps({"type": "excluded_folders",
                                       "items": _state.get("excluded_folders", [])}))
        await ws.send_text(json.dumps({"type": "tool_updates",
                                       "items": _state.get("tool_updates", {})}))
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
            "dl_parallel":             _state.get("dl_parallel", 3),
            "dl_filename_format":      _state.get("dl_filename_format", "title"),
            "download_dir":            _state.get("download_dir", str(core.BASE_DIR / "Downloads")),
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
        # Zuletzt erfolgreiche Tests (gleicher Key) — sonst stand nach jedem
        # Neustart nur "Key eingetragen" da und man musste neu testen
        known = store.known_service_tests()
        if known:
            await ws.send_text(json.dumps({"type": "services_test", **known}))
        # ... und beim ersten Verbinden nach dem Start einmal echt pruefen
        core.spawn(tags._check_services_on_start())
        if _state.get("remote_autostart") and remote._remote_server is None:
            saved = _state.get("remote_services_saved") or {"remote": True, "wishes": True}
            remote._remote_services.update(remote=bool(saved.get("remote")), wishes=bool(saved.get("wishes")))
            if any(remote._remote_services.values()):
                core.spawn(remote._start_remote_server(ws))
        else:
            await ws.send_text(json.dumps(remote._remote_status_msg()))

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
        if not library._queue_is_duplicate(track["path"], track["title"]):
            was_idle = _state["current_idx"] == -1
            _state["queue"].append(track)
            store.save_queue()
            if was_idle:
                _state["current_idx"] = len(_state["queue"]) - 1
                _state["playing"] = True
                await core.push_queue()
                await core.push_player()
                await core.broadcast({"type": "now_playing", "track": track})
                core.spawn(media._enrich_track(track["path"]))
            else:
                await core.push_queue()

    elif t == "queue_remove":
        idx = msg.get("index", -1)
        if 0 <= idx < len(_state["queue"]):
            _state["queue"].pop(idx)
            if _state["current_idx"] >= idx:
                _state["current_idx"] = max(-1, _state["current_idx"] - 1)
            store.save_queue()
            await core.push_queue()  # carries current_idx — no push_player needed

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
            store._record_play(track["path"], track.get("title", ""), track_artist)
            store.save_queue()
            store.save_library()
            await core.push_player()
            await core.push_queue()
            await core.push_library()
            now = {**track}
            await core.broadcast({"type": "now_playing", "track": now})
            core.spawn(media._enrich_track(track["path"]))
            # Pre-enrich next track so Deck 2 has real LUFS before crossfade
            nxt_idx = idx + 1
            if 0 <= nxt_idx < len(_state["queue"]):
                nxt_path = _state["queue"][nxt_idx].get("path", "")
                if nxt_path and nxt_path not in media._unanalyzable_paths and _state["queue"][nxt_idx].get("lufs", -99) <= -90:
                    core.spawn(media._enrich_track(nxt_path))
            # Queue-Ende: Radio — beim letzten Titel schon den naechsten anhaengen
            elif nxt_idx >= len(_state["queue"]) and _state.get("auto_mix", True):
                core.spawn(automix._at_queue_end())

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
        store._record_play(track["path"], track.get("title", ""), track_artist)
        store.save_queue()
        store.save_library()
        await core.push_queue()
        await core.push_player()
        await core.broadcast({"type": "now_playing", "track": track})
        core.spawn(media._enrich_track(track["path"]))
        # Pre-enrich next track so Deck 2 has real LUFS before crossfade
        nxt_idx = _state["current_idx"] + 1
        if 0 <= nxt_idx < len(_state["queue"]):
            nxt_path = _state["queue"][nxt_idx].get("path", "")
            if nxt_path and _state["queue"][nxt_idx].get("lufs", -99) <= -90:
                core.spawn(media._enrich_track(nxt_path))
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
            store.save_queue()
            await core.push_queue()
            _state["wishes"] = [x for x in _state["wishes"] if x.get("id") != wid]
            # Merken, damit der Gast sieht, wann sein Titel kommt
            remote._wish_outcomes[wid] = {"state": "angenommen", "title": w.get("title", ""),
                                   "path": w["path"], "at": time.time()}
            remote.save_wishes()
            await remote.push_wishes()

    elif t == "wish_reject":
        wid = msg.get("id")
        w = next((x for x in _state.get("wishes", []) if x.get("id") == wid), None)
        if w:
            # Heruntergeladene Datei mitnehmen — abgelehnte Wuensche sollen
            # den Download-Ordner nicht vollmuellen.
            p = w.get("path", "")
            # Nur loeschen, was der Wunsch selbst heruntergeladen hat — nie einen
            # Titel aus der eigenen Sammlung oder einen, der schon vorher da war.
            eigen = not w.get("from_library") and not w.get("keep_file")
            if p and os.path.exists(p) and eigen and msg.get("delete_file", True):
                if await asyncio.get_running_loop().run_in_executor(None, library._move_to_trash, p):
                    _state["library"] = [lt for lt in _state["library"] if lt.get("path") != p]
                    store.save_library()
                    await core.push_library()
                else:
                    print(f"[wishes] konnte {p} nicht in den Papierkorb verschieben", flush=True)
            _state["wishes"] = [x for x in _state["wishes"] if x.get("id") != wid]
            remote._wish_outcomes[wid] = {"state": "abgelehnt", "title": w.get("title", "")}
            remote.save_wishes()
            await remote.push_wishes()

    elif t == "enrich_track":
        path = msg.get("path", "")
        force = bool(msg.get("force", False))
        if path and os.path.exists(path):
            core.spawn(media._enrich_track(path, force=force))

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
        await core.push_player()

    elif t == "resume":
        _state["playing"] = True
        ci = _state["current_idx"]
        if 0 <= ci < len(_state["queue"]):
            track = _state["queue"][ci]
            if not track.get("played"):
                track["played"]    = True
                track["played_at"] = int(time.time())
                track["play_count"] = track.get("play_count", 0) + 1
                store.save_queue()
                await core.push_queue()
        await core.push_player()

    elif t == "seek":
        _state["position_ms"] = msg.get("position_ms", 0)
        await core.push_player()

    elif t == "position_update":
        _state["position_ms"] = msg.get("position_ms", 0)
        _state["duration_ms"] = msg.get("duration_ms", _state["duration_ms"])
        if remote._remote_clients:
            core.spawn(remote._broadcast_remote_pos())

    elif t == "seek_relative":
        delta  = int(msg.get("delta_ms", 0))
        new_pos = max(0, _state["position_ms"] + delta)
        if _state["duration_ms"] > 0:
            new_pos = min(new_pos, _state["duration_ms"] - 200)
        _state["position_ms"] = new_pos
        await core.push_player()
    elif t == "library_remove_disk_many":
        # Mehrere Titel auf einmal: ein Papierkorb-Aufruf, einmal speichern und
        # schicken. Frueher je Titel alles einzeln — bei 190 Kopien Minuten.
        paths = [p for p in (msg.get("paths") or []) if isinstance(p, str) and p]
        if paths:
            failed = set(await asyncio.get_running_loop().run_in_executor(None, library._move_many_to_trash, paths))
            gone = set(paths) - failed
            _state["library"] = [lt for lt in _state["library"] if lt.get("path") not in gone]
            store.save_library()
            await core.push_library()
            if failed:
                names = ", ".join(Path(p).name for p in list(failed)[:3])
                more = f" und {len(failed) - 3} weitere" if len(failed) > 3 else ""
                await core.broadcast({"type": "scan_status",
                                 "text": f"Nicht in den Papierkorb (in Benutzung?): {names}{more}"})
            else:
                await core.broadcast({"type": "scan_status", "text": f"{len(gone)} Titel in den Papierkorb verschoben"})
                await asyncio.sleep(4)
                await core.broadcast({"type": "scan_status", "text": ""})
    elif t == "set_volume":
        _state["volume"] = min(100, max(0, int(float(msg.get("value", 80)))))
        store.save_settings()
        await core.broadcast({"type": "settings", "volume": _state["volume"], "crossfade_s": _state["crossfade_s"]})
        if remote._remote_clients:
            core.spawn(remote._broadcast_remote_state())

    elif t == "set_normalize_volume":
        _state["normalize_volume"] = bool(msg.get("value", True))
        if "target_lufs" in msg:
            _state["target_lufs"] = float(msg["target_lufs"])
        store.save_settings()
        await core.broadcast({"type": "settings", "normalize_volume": _state["normalize_volume"], "target_lufs": _state["target_lufs"]})
        if remote._remote_clients:
            core.spawn(remote._broadcast_remote_state())

    elif t == "set_crossfade":
        _state["crossfade_s"] = msg.get("seconds", 4)
        store.save_settings()
        await core.broadcast({"type": "settings", "volume": _state["volume"], "crossfade_s": _state["crossfade_s"]})

    elif t == "add_favorite":
        fav_path = msg.get("path", "")
        fav_name = msg.get("name", Path(fav_path).name if fav_path else "")
        if fav_path and os.path.isdir(fav_path):
            favs = _state.get("favorites", [])
            if not any(f["path"] == fav_path for f in favs):
                favs.append({"name": fav_name, "path": fav_path})
                _state["favorites"] = favs
                store.save_settings()
        await ws.send_text(json.dumps({"type": "favorites", "items": _state.get("favorites", [])}))

    elif t == "remove_favorite":
        fav_path = msg.get("path", "")
        _state["favorites"] = [f for f in _state.get("favorites", []) if f["path"] != fav_path]
        store.save_settings()
        await ws.send_text(json.dumps({"type": "favorites", "items": _state.get("favorites", [])}))

    elif t == "set_auto_scan_interval":
        _state["auto_scan_interval_min"] = max(0, int(msg.get("minutes", 0)))
        store.save_settings()
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
            "dl_parallel":              _state.get("dl_parallel", 3),
            "dl_filename_format":       _state.get("dl_filename_format", "title"),
            "download_dir":             _state.get("download_dir", str(core.BASE_DIR / "Downloads")),
            "auto_scan_interval_min":   _state.get("auto_scan_interval_min", 0),
        }
        await ws.send_text(json.dumps({"type": "settings_export", "data": payload}))

    elif t == "import_settings":
        data = msg.get("data", {})
        if isinstance(data, dict):
            for key, default in [
                ("volume", 80), ("crossfade_s", 8.0), ("bpm_analysis", True),
                ("scan_recursive", True), ("auto_mix", True), ("loudnorm_on_dl", False),
                ("loudnorm_target", -10.0), ("loudnorm_tp", -1.5),
                ("playlist_folder_enabled", True), ("dl_filename_format", "title"), ("dl_parallel", 3),
                ("download_dir", str(core.BASE_DIR / "Downloads")), ("auto_scan_interval_min", 0),
            ]:
                if key in data:
                    _state[key] = type(default)(data[key]) if not isinstance(default, bool) else bool(data[key])
            if "watched_folders" in data and isinstance(data["watched_folders"], list):
                _state["watched_folders"] = data["watched_folders"]
            store.save_settings()
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
                "dl_parallel":             _state.get("dl_parallel", 3),
                "dl_filename_format":      _state.get("dl_filename_format", "title"),
                "download_dir":            _state.get("download_dir", str(core.BASE_DIR / "Downloads")),
                "auto_scan_interval_min":  _state.get("auto_scan_interval_min", 0),
            }))

    elif t in ("get_waveform", "get_waveform_next", "get_waveform_third"):
        # Nebenher: frueher wartete jede weitere Anfrage des Fensters, bis die
        # Waveform fertig war. Die Oberflaeche prueft den Pfad der Antwort.
        core.spawn(_send_waveform(ws, t[4:], msg.get("path", "")))

    elif t == "get_changelog":
        # Fragt GitHub — bei langsamer Leitung Sekunden. Nicht in der Reihe warten.
        core.spawn(_send_changelog(ws))

    elif t == "get_beatgrid":
        path = msg.get("path", "")
        if path:
            core.spawn(beatgrid._send_beatgrid(ws, path))


    elif t == "scan_library":
        folder = msg.get("folder", "")
        if folder and os.path.isdir(folder):
            # Liegt der Ordner schon in einem rekursiv beobachteten, wird er
            # ohnehin mit durchsucht — als eigener Eintrag waere er nur doppelt.
            schon_drin = folder in _state["watched_folders"] or (
                _state.get("scan_recursive", True)
                and any(library._path_in_folder(folder, f, True) for f in _state["watched_folders"]))
            if not schon_drin:
                _state["watched_folders"].append(folder)
                store.save_settings()
                await core.broadcast({"type": "watched_folders", "items": library._watched_folders_info()})

        async def _scan_all():
            # Ohne Ordnerauswahl alles neu einlesen — vorher passierte hier
            # schlicht nichts, etwa wenn der Knopf aus dem Remote kam.
            for f in library._scan_folders():
                await library.scan_folder(f)
        core.spawn(_scan_all())

    elif t == "get_watched_folders":
        await ws.send_text(json.dumps({"type": "watched_folders", "items": library._watched_folders_info()}))

    elif t == "remove_watched_folder":
        folder = msg.get("folder", "")
        if folder in _state.get("watched_folders", []):
            lost = library._library_paths_lost_without(folder)
            if msg.get("dry_run"):
                # Nur ausrechnen, was verschwinden wuerde — fuer die Rueckfrage
                await ws.send_text(json.dumps({"type": "watched_folder_impact",
                                               "folder": folder, "tracks": len(lost)}))
            else:
                # Dateien bleiben unangetastet; nur die Bibliothek vergisst sie
                _state["watched_folders"] = [f for f in _state["watched_folders"] if f != folder]
                store.save_settings()
                if lost:
                    weg = set(lost)
                    _state["library"] = [lt for lt in _state["library"] if lt.get("path") not in weg]
                    store.save_library()
                    await core.push_library()
                await core.broadcast({"type": "watched_folders", "items": library._watched_folders_info()})

    elif t == "exclude_folder":
        # "Aus Bibliothek ausschliessen": Eintraege entfernen UND merken —
        # vorher holte der Ordner-Waechter die Titel nach 10 s zurueck.
        folder = msg.get("folder", "")
        if folder:
            if folder not in _state.setdefault("excluded_folders", []):
                _state["excluded_folders"].append(folder)
                store.save_settings()
            vorher = len(_state["library"])
            _state["library"] = [lt for lt in _state["library"] if not library._is_excluded(lt.get("path", ""))]
            if len(_state["library"]) != vorher:
                store.save_library()
                await core.push_library()
            await core.broadcast({"type": "excluded_folders", "items": _state["excluded_folders"]})
            await core.broadcast({"type": "watched_folders", "items": library._watched_folders_info()})

    elif t == "include_folder":
        # Wieder aufnehmen — der Waechter liest die Titel in den naechsten
        # Sekunden von selbst neu ein.
        folder = msg.get("folder", "")
        if folder in _state.get("excluded_folders", []):
            _state["excluded_folders"] = [f for f in _state["excluded_folders"] if f != folder]
            store.save_settings()
            await core.broadcast({"type": "excluded_folders", "items": _state["excluded_folders"]})

    elif t == "set_scan_recursive":
        _state["scan_recursive"] = bool(msg.get("enabled", True))
        store.save_settings()
        await ws.send_text(json.dumps({"type": "scan_recursive",
                                       "enabled": _state["scan_recursive"]}))

    elif t == "search":
        query = msg.get("query", "").strip()
        if query:
            core._start_search(ws, "search", search.do_search(query, ws))

    elif t == "download_add":
        url    = msg.get("url", "").strip()
        fmt    = msg.get("format", "mp3-best")
        choice = msg.get("playlist_choice")   # None | "single" | "all"
        # "song"/"video": im Dialog entschieden, dann nicht noch einmal pruefen.
        # dupe_checked: "Trotzdem laden" trotz Treffer in der Bibliothek.
        video_choice = msg.get("video_choice")
        dupe_checked = bool(msg.get("dupe_checked"))
        if url:
            if download._is_spotify(url):
                core.spawn(download.run_spotify_download(url, fmt))
            elif msg.get("plan_id") is not None:
                # Entscheidung aus dem Playlist-Kaestchen: nur neue / alle / als Playlist
                plan = download._plans.pop(int(msg["plan_id"]), None)
                if plan:
                    core.spawn(download.run_download(
                        plan["url"], fmt, entries=plan["entries"], folder=download._folder_name(plan["title"]),
                        label=plan["title"] or None, mode=msg.get("plist_mode") or "new",
                        follow_new=bool(msg.get("follow"))))
            elif channels._channel_base(url) and not msg.get("direct"):
                # Kanal-Link: Playlists des Kanals zur Auswahl (verfolgen)
                if url not in download._plan_tasks:
                    core.spawn(channels._plan_channel(url, fmt, ws))
            elif choice is None and download._is_mixed_playlist_url(url):
                core.spawn(download._ask_playlist_choice(url, fmt, ws))
            elif choice != "single" and url.startswith("http") and download._is_playlist(url) and not msg.get("direct"):
                if url not in download._plan_tasks:
                    core.spawn(download._plan_playlist(url, fmt, ws))
            else:
                if choice == "single":
                    url = download._strip_playlist_params(url)
                if video_choice is None and search._is_single_link(url):
                    core.spawn(download._check_video_then_download(url, fmt, ws, check_dupes=not dupe_checked))
                else:
                    core.spawn(download.run_download(url, fmt))

    elif t == "set_spotify_creds":
        _state["spotify_client_id"]     = str(msg.get("client_id", "")).strip()
        _state["spotify_client_secret"] = str(msg.get("client_secret", "")).strip()
        store.save_settings()

    elif t == "set_services":
        if "lastfm_api_key"  in msg: _state["lastfm_api_key"]  = str(msg["lastfm_api_key"]).strip()
        if "acoustid_api_key" in msg: _state["acoustid_api_key"] = str(msg["acoustid_api_key"]).strip()
        store.save_settings()

    elif t == "test_services":
        res = await tags._test_services()
        store.remember_service_tests(res)
        await ws.send_text(json.dumps({"type": "services_test", **res}))

    elif t == "set_radio":
        _state["radio_enabled"] = bool(msg.get("enabled", False))
        store.save_settings()
        await core.broadcast({"type": "radio_status", "enabled": _state["radio_enabled"]})
        if remote._remote_clients:
            core.spawn(remote._broadcast_remote_state())
        if _state["radio_enabled"]:
            core.spawn(automix._check_radio_queue())

    elif t == "identify_track":
        path = msg.get("path", "")
        if path and os.path.exists(path):
            result = await tags._acoustid_identify(path)
            await ws.send_text(json.dumps({"type": "track_identified", **result}))

    elif t == "download_fpcalc":
        core.spawn(tools._download_fpcalc(ws))

    elif t == "install_spotdl":
        core.spawn(tools._install_spotdl(ws))

    elif t == "download_stop":
        # Kill entire session (all tracks) and terminate subprocess
        sid = msg.get("session_id")
        if sid is not None:
            procs = download._dl_procs.get(sid)
            for proc in (procs if isinstance(procs, list) else [procs] if procs else []):
                try: proc.kill()
                except Exception: pass
            _state["downloads"] = [d for d in _state["downloads"]
                                    if d.get("session", d.get("id")) != sid]
        await core.push_downloads()

    elif t == "get_followed":
        await ws.send_text(json.dumps({"type": "followed", "items": download._follow_public(),
                                       "channels": channels._channels_public()}))

    elif t == "playlist_plan_abort":
        task = download._plan_tasks.get(msg.get("url") or "")
        if task:
            task.cancel()

    elif t == "follow_add":
        # Aus einer geladenen Playlist heraus: deren Titel gelten als bekannt
        url = (msg.get("url") or "").strip()
        sid = msg.get("session_id")
        if url.startswith("http") and not any(f["url"] == url for f in _state.setdefault("followed", [])):
            hdr = next((d for d in _state["downloads"] if d.get("id") == sid), None) or {}
            seen = []
            for d in _state["downloads"]:
                if d.get("session") == sid and d.get("id") != sid and d.get("status") == "done":
                    v = download._vid_of(d.get("url") or "")
                    if v and v not in seen:
                        seen.append(v)
            _state["followed"].append({
                "url": url, "title": (msg.get("title") or hdr.get("session_label") or url)[:80],
                "fmt": msg.get("format") or hdr.get("fmt") or "mp3-best",
                "folder": hdr.get("folder"), "added": int(time.time()),
                "last_check": int(time.time()), "last_new": 0, "seen": seen,
            })
            store.save_settings()
        await download._push_followed()

    elif t == "follow_remove":
        url = msg.get("url") or ""
        _state["followed"] = [f for f in _state.get("followed", []) if f["url"] != url]
        store.save_settings()
        await download._push_followed()

    elif t == "mix_now":
        # Von der Fernbedienung: Uebergang zum naechsten Titel auf der naechsten Eins
        await core.broadcast({"type": "mix_now"})

    elif t == "channel_follow":
        core.spawn(channels._channel_follow(msg))

    elif t == "channel_plan_open":
        # Auswahl der Playlists eines verfolgten Kanals erneut oeffnen
        ch = channels._channel_of(msg.get("url") or "")
        if ch and ch["url"] not in download._plan_tasks:
            core.spawn(channels._plan_channel(ch["url"], ch.get("fmt") or "mp3-best", ws))

    elif t == "channel_set":
        ch = channels._channel_of(msg.get("url") or "")
        if ch:
            if "auto_new" in msg:
                ch["auto_new"] = bool(msg["auto_new"])
            store.save_settings()
            await download._push_followed()

    elif t == "channel_remove":
        await channels._channel_remove(msg.get("url") or "")

    elif t == "follow_tracks":
        # Titel einer verfolgten Playlist (Stand der letzten Pruefung)
        url = msg.get("url", "")
        await ws.send_text(json.dumps({"type": "follow_tracks", "url": url,
                                       **(download.follow_tracks(url) or {"items": None})}))

    elif t == "follow_check":
        url = msg.get("url")
        core.spawn(download._follow_check_all([url] if url else None))

    elif t == "download_retry_failed":
        # z. B. zuhause, wenn unterwegs vieles gesperrt war
        sid = msg.get("session_id")
        hdr = next((d for d in _state["downloads"] if d.get("id") == sid), None)
        failed = [d for d in _state["downloads"] if d.get("session") == sid and d.get("id") != sid
                  and d.get("status") == "error" and d.get("url")]
        if hdr and failed:
            _state["downloads"] = [d for d in _state["downloads"] if d not in failed]
            hdr["failed_n"] = 0
            await core.push_downloads(force=True)
            entries = [{"url": d["url"], "title": d.get("title") or "", "replaced": False, "duration": 0}
                       for d in failed]
            core.spawn(download.run_download("", hdr.get("fmt") or "mp3-best", entries=entries,
                                             folder=hdr.get("folder"),
                                             label=f"Nochmal: {hdr.get('session_label') or 'Downloads'}"[:60],
                                             retry=True))

    elif t == "download_cancel":
        # Remove a single finished/error item from the list (doesn't kill subprocess)
        dl_id = msg.get("id")
        _state["downloads"] = [d for d in _state["downloads"] if d.get("id") != dl_id]
        await core.push_downloads()

    elif t == "download_clear_done":
        _state["downloads"] = [d for d in _state["downloads"] if d.get("status") == "active"]
        await core.push_downloads()

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
        if not library._queue_is_duplicate(track["path"], track["title"]):
            _state["queue"].insert(pos, track)
            if _state["current_idx"] >= pos:
                _state["current_idx"] += 1
            store.save_queue()
            await core.push_queue()  # carries current_idx — no push_player needed

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
        if not library._queue_is_duplicate(track["path"], track["title"]):
            pos = max(0, _state["current_idx"] + 1)
            _state["queue"].insert(pos, track)
            store.save_queue()
            await core.push_queue()

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
            store.save_queue()
            await core.push_queue()  # carries current_idx — no push_player needed

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
            store.save_queue()
            await core.push_queue()
            await core.push_player()

    elif t == "queue_shuffle_selected":
        indices = msg.get("indices", [])
        q = _state["queue"]
        ci = _state["current_idx"]
        valid = sorted({i for i in indices if 0 <= i < len(q)})
        if len(valid) > 1:
            # Laufenden Titel VOR dem Mischen merken — vorher wurde erst danach
            # nachgesehen und dabei der Titel gefunden, der nun an seiner
            # Stelle steht; der Zeiger zeigte dann auf etwas anderes.
            cur_path = q[ci]["path"] if 0 <= ci < len(q) else None
            tracks = [q[i] for i in valid]
            random.shuffle(tracks)
            for i, idx in enumerate(valid):
                q[idx] = tracks[i]
            if cur_path:
                _state["current_idx"] = next((i for i, t in enumerate(q) if t.get("path") == cur_path), ci)
            store.save_queue()
            await core.push_queue()
            await core.push_player()

    elif t == "queue_mark_unplayed":
        for item in _state["queue"]:
            item["played"] = False
        store.save_queue()
        await core.push_queue()

    elif t == "queue_remove_played":
        ci  = _state["current_idx"]
        cur_path = _state["queue"][ci]["path"] if 0 <= ci < len(_state["queue"]) else None
        _state["queue"] = [t for t in _state["queue"] if not t.get("played") or t.get("path") == cur_path]
        # Recalculate current_idx
        if cur_path:
            _state["current_idx"] = next((i for i, t in enumerate(_state["queue"]) if t.get("path") == cur_path), -1)
        else:
            _state["current_idx"] = -1
        store.save_queue()
        await core.push_queue()
        await core.push_player()

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
            norm = library._norm_queue_title(qt.get("title", ""))
            if norm and norm in seen_titles:
                continue
            seen_paths.add(p)
            if norm:
                seen_titles.add(norm)
            deduped.append(qt)
        _state["queue"] = deduped
        _state["current_idx"] = next((i for i, t in enumerate(_state["queue"]) if t.get("path") == cur_path), -1) \
            if cur_path else -1
        store.save_queue()
        await core.push_queue()
        await core.push_player()

    elif t == "set_auto_remove_played":
        _state["auto_remove_played"] = bool(msg.get("enabled", False))
        store.save_settings()
        await core.broadcast({"type": "auto_remove_played", "enabled": _state["auto_remove_played"]})

    elif t == "queue_harmonic":
        # Nur was noch kommt (ungespielt, nach dem laufenden Titel) umsortieren
        q   = _state["queue"]
        ci  = _state["current_idx"]
        lib = {lt.get("path"): lt for lt in _state.get("library", [])}
        def info(qt):
            lt = lib.get(qt.get("path"), {})
            return {**qt, "key": lt.get("key") or qt.get("key") or "",
                    "energy": lt.get("energy") or qt.get("energy") or 0,
                    "bpm": lt.get("bpm") or qt.get("bpm") or 0,
                    "bpm_f": lt.get("bpm_f") or 0,
                    "lufs": lt.get("lufs", qt.get("lufs", -99))}
        sel = msg.get("indices")
        if isinstance(sel, list) and sel:
            # Nur die markierten Titel, auf ihren bisherigen Plaetzen; der laufende
            # bleibt stehen. Anschluss an den Titel direkt vor dem ersten markierten.
            pos = sorted({i for i in sel if isinstance(i, int) and 0 <= i < len(q) and i != ci})
            future = [(i, q[i]) for i in pos]
            ref = pos[0] - 1 if pos else -1
            start_i = ref if 0 <= ref < len(q) else ci
        else:
            future = [(i, q[i]) for i in range(ci + 1, len(q)) if not q[i].get("played")]
            start_i = ci
        cur_path = q[ci].get("path") if 0 <= ci < len(q) else None
        boosts: set[int] = set()
        if len(future) > 1:
            idxs, items = zip(*future)
            infos = [info(qt) for qt in items]
            back = {id(x): qt for x, qt in zip(infos, items)}
            start = info(q[start_i]) if 0 <= start_i < len(q) else None
            order, boosts = await asyncio.get_running_loop().run_in_executor(None, keys._harmonic_order, start, infos)
            for n, (i, x) in enumerate(zip(idxs, order)):
                qt = back[id(x)]
                if n in boosts:
                    qt["energy_boost"] = True
                else:
                    qt.pop("energy_boost", None)
                q[i] = qt
            if cur_path:
                _state["current_idx"] = next((i for i, t in enumerate(q) if t.get("path") == cur_path), ci)
            store.save_queue()
            await core.push_queue()
        unknown = sum(1 for _, qt in future if not keys._key_to_camelot(info(qt).get("key")))
        await ws.send_text(json.dumps({"type": "harmonic_result", "count": len(future),
                                       "boosts": len(boosts), "unknown_keys": unknown}))

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
        store.save_queue()
        await core.push_queue()

    elif t == "queue_clear":
        _state["queue"].clear()
        _state["current_idx"] = -1
        _state["playing"]     = False
        store.save_queue()
        await core.push_queue()
        await core.push_player()

    elif t == "set_shuffle":
        _state["shuffle"] = bool(msg.get("value", False))
        store.save_settings()
        await core.push_player()

    elif t == "set_repeat":
        _state["repeat"] = int(msg.get("value", 0)) % 3
        store.save_settings()
        await core.push_player()

    elif t == "save_playlist":
        name = (msg.get("name") or "").strip()
        if name:
            core.PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r'[<>:"/\\|?*]', '_', name)
            pl_path = core.PLAYLISTS_DIR / (safe + ".m3u")
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
                                           "items": library._get_playlists()}))
            if msg.get("clear_after"):
                _state["queue"] = []
                _state["current_idx"] = -1
                _state["playing"] = False
                store.save_queue()
                await core.push_queue()
                await core.push_player()

    elif t == "load_playlist":
        pl_path = msg.get("path", "")
        if os.path.exists(pl_path):
            tracks = library._parse_m3u(pl_path)
            _state["queue"].extend(tracks)
            store.save_queue()
            await core.push_queue()

    elif t == "get_download_tree":
        # Im Hintergrund-Thread: beim ganzen Musikordner dauerte das Einlesen
        # ~0,4 s und hielt so lange den ganzen Server an (auch die Waveform)
        core.spawn(_send_download_tree(ws))
    elif t == "get_playlist_content":
        pl_path = msg.get("path", "")
        if os.path.exists(pl_path):
            pl_tracks = library._parse_m3u(pl_path)
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
                pl_tracks = library._parse_m3u(pl_path)
                lib_by_path = {lt["path"]: lt for lt in _state["library"]}
                result = [lib_by_path.get(pt["path"], pt) for pt in pl_tracks]
                await ws.send_text(json.dumps({"type": "playlist_content",
                                               "path": pl_path, "tracks": result}))

    elif t == "delete_playlist":
        pl_path = msg.get("path", "")
        if pl_path and os.path.exists(pl_path):
            await asyncio.get_running_loop().run_in_executor(None, library._move_to_trash, pl_path)
            await ws.send_text(json.dumps({"type": "playlists",
                                           "items": library._get_playlists()}))

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
                pl_tracks = library._parse_m3u(pl_path)
                lib_by_path = {lt["path"]: lt for lt in _state["library"]}
                result = [lib_by_path.get(pt["path"], pt) for pt in pl_tracks]
                await ws.send_text(json.dumps({"type": "playlist_content",
                                               "path": pl_path, "tracks": result}))
            except Exception as e:
                print(f"[playlist_remove_track] {e}")
    elif t == "title_suggest":
        paths = msg.get("paths")
        core.spawn(tags._title_suggest(ws, online=bool(msg.get("online", False)),
                                           only=set(paths) if isinstance(paths, list) and paths else None))

    elif t == "title_apply":
        core.spawn(tags._title_apply(msg.get("items") or [], ws))

    elif t == "fingerprint_suggest":
        paths = msg.get("paths")
        if isinstance(paths, list) and paths:
            core.spawn(tags._fingerprint_suggest(ws, [p for p in paths if isinstance(p, str)]))

    elif t == "title_cancel":
        tags._title_cancel = True

    elif t == "genre_suggest":
        if "folder_rules" in msg:
            tags._genre_rules_save(msg.get("folder_rules") or {})
        core.spawn(tags._genre_suggest(ws, online=bool(msg.get("online", True))))

    elif t == "genre_apply":
        core.spawn(tags._genre_apply(msg.get("items") or [], ws))

    elif t == "genre_cancel":
        tags._genre_cancel = True

    elif t == "quality_ok":
        path = msg.get("path", "")
        lt = next((x for x in _state["library"] if x.get("path") == path), None)
        if lt is not None:
            # False statt entfernen: das Frontend fuehrt Eintraege zusammen
            lt["quality_ok"] = bool(msg.get("ok", True))
            store.schedule_save()
            await core.broadcast({"type": "track_meta_update", "track": lt})

    elif t == "quality_batch":
        paths = [str(x) for x in (msg.get("paths") or [])]
        if paths:
            core.spawn(quality._quality_batch(paths, ws))

    elif t == "quality_batch_replace":
        core.spawn(quality._quality_batch_replace(msg.get("items") or [], ws))

    elif t == "quality_batch_cancel":
        quality._qbatch_cancel = True

    elif t == "quality_candidates":
        path = msg.get("path", "")
        if path:
            core.spawn(quality._quality_candidates(path, msg.get("query", ""), ws))

    elif t == "quality_replace":
        path, url = msg.get("path", ""), msg.get("url", "")
        if path and url:
            core.spawn(quality._quality_replace(path, url, ws))

    elif t == "analyze_library_meta":
        paths = msg.get("paths")
        core.spawn(media._analyze_library_meta_task(
            [str(p) for p in paths] if isinstance(paths, list) else None))

    elif t == "cancel_analyze":
        media._analyze_cancel = True

    elif t == "library_update_meta":
        path   = msg.get("path", "")
        title  = (msg.get("title") or "").strip()
        artist = (msg.get("artist") or "").strip()
        if path and os.path.exists(path) and title:
            core.spawn(media._update_track_meta(path, title, artist))

    elif t == "set_bpm_analysis":
        _state["bpm_analysis"] = bool(msg.get("enabled", True))
        store.save_settings()

    elif t == "automix_trigger":
        if _state.get("auto_mix", True):
            core.spawn(automix._at_queue_end())

    elif t == "set_auto_mix":
        _state["auto_mix"] = bool(msg.get("value", True))
        store.save_settings()
        # Kann jetzt auch vom Handy kommen — App und Fernbedienung nachziehen
        await core.broadcast({"type": "settings", "auto_mix": _state["auto_mix"]})
        if remote._remote_clients:
            core.spawn(remote._broadcast_remote_state())

    elif t == "get_history":
        await ws.send_text(json.dumps(store._history_payload()))

    elif t == "clear_play_history":
        for lt in _state["library"]:
            lt["play_count"] = 0
        store.save_library()
        await core.push_library()

    elif t == "remote_start":
        # service: "remote" | "wishes"; ohne Angabe beide (wie frueher)
        svc = msg.get("service")
        for k in (remote._remote_services if not svc else [svc]):
            if k in remote._remote_services:
                remote._remote_services[k] = True
        _state["remote_services_saved"] = dict(remote._remote_services)
        store.save_settings()
        await remote._start_remote_server(ws)

    elif t == "remote_stop":
        svc = msg.get("service")
        for k in (remote._remote_services if not svc else [svc]):
            if k in remote._remote_services:
                remote._remote_services[k] = False
        # Offene Verbindungen des abgeschalteten Dienstes trennen
        if not remote._remote_services["remote"]:
            for rws in list(remote._remote_clients):
                try: await rws.close(code=1001)
                except Exception: pass
            remote._remote_clients.clear()
        if not remote._remote_services["wishes"]:
            for wws in list(remote._wish_clients):
                try: await wws.close(code=1001)
                except Exception: pass
            remote._wish_clients.clear()
        if any(remote._remote_services.values()):
            _state["remote_services_saved"] = dict(remote._remote_services)
            store.save_settings()
            await core.broadcast(remote._remote_status_msg())
        else:
            await remote._stop_remote_server()
            await core.broadcast(remote._remote_status_msg())

    elif t == "remote_new_key":
        # Falls der Link in falsche Haende geraten ist: neuer Schluessel,
        # verbundene Fernbedienungen fliegen raus und muessen neu scannen.
        _state["remote_key"] = ""
        remote._remote_key()
        for rws in list(remote._remote_clients):
            try: await rws.close(code=1008)
            except Exception: pass
        remote._remote_clients.clear()
        if remote._remote_server is not None:
            await core.broadcast(remote._remote_status_msg())

    elif t == "set_ytdlp_autoupdate":
        _state["ytdlp_autoupdate"] = bool(msg.get("value", True))
        store.save_settings()
        await ws.send_text(json.dumps({"type": "settings",
                                       "ytdlp_autoupdate": _state["ytdlp_autoupdate"]}))

    elif t == "set_remote_autostart":
        _state["remote_autostart"] = bool(msg.get("value", False))
        store.save_settings()
        # Frueher folgte hier ein pauschales remote_status running=False — die
        # Einstellungen zeigten dann "gestoppt" und keinen QR-Code mehr,
        # obwohl der Server weiterlief. Am laufenden Server aendert sich nichts.
        await ws.send_text(json.dumps({"type": "settings", "remote_autostart": _state["remote_autostart"]}))

    elif t == "get_notes":
        await ws.send_text(json.dumps({"type": "notes", "text": _state["notes"]}))

    elif t == "save_notes":
        _state["notes"] = str(msg.get("text", ""))
        store.schedule_notes_save()

    elif t == "normalize_files":
        paths = msg.get("paths", [])
        target_lufs = float(msg.get("target_lufs", -14.0))
        target_tp   = float(msg.get("target_tp", -1.5))
        if paths:
            core.spawn(media._normalize_files(paths, target_lufs, target_tp, ws))

    elif t == "get_logs":
        await ws.send_json({"type": "logs", "lines": list(core._log_buffer)})

    elif t == "check_tools":
        core.spawn(tools._check_tools(ws))

    elif t == "relocate_detect":
        core.spawn(_send_relocate(ws, manual=True))

    elif t == "relocate_apply":
        old, new = str(msg.get("from") or ""), str(msg.get("to") or "")
        if old and new and old.lower() != new.lower():
            res = await asyncio.get_running_loop().run_in_executor(None, relocate.apply, old, new)
            await core.push_library()
            await core.broadcast({"type": "relocated", "from": old, "to": new, **res})

    elif t == "update_ytdlp":
        core.spawn(tools._update_ytdlp(ws))

    elif t == "update_ffmpeg":
        core.spawn(tools._update_ffmpeg(ws))

    elif t == "check_tool_updates":
        core.spawn(tools._tools_check_once(force=True))

    elif t == "dismiss_ytdlp_updated":
        # Ein OK fuer alle Hinweise "wurde aktualisiert"
        for k in ("ytdlp_updated", "spotdl_updated", "ffmpeg_updated"):
            _state.setdefault("tool_updates", {}).pop(k, None)
        store.save_settings()
        await core.broadcast({"type": "tool_updates", "items": _state["tool_updates"]})

    elif t == "set_loudnorm_dl":
        _state["loudnorm_on_dl"]  = bool(msg.get("enabled", True))
        _state["loudnorm_target"] = float(msg.get("target", -10.0))
        _state["loudnorm_tp"]     = float(msg.get("true_peak", _state.get("loudnorm_tp", -1.5)))
        store.save_settings()

    elif t == "set_download_folder":
        folder = msg.get("path", "")
        if folder and os.path.isdir(folder):
            _state["download_dir"] = folder
            store.save_settings()

    elif t == "set_playlist_folder":
        _state["playlist_folder_enabled"] = bool(msg.get("enabled", True))
        store.save_settings()

    elif t == "set_dl_parallel":
        try:
            _state["dl_parallel"] = max(1, min(6, int(msg.get("value", 3))))
        except (TypeError, ValueError):
            pass
        store.save_settings()

    elif t == "set_dl_filename_format":
        _state["dl_filename_format"] = str(msg.get("format", "title"))
        store.save_settings()


remote.handle_message = handle_message

# ── WebSocket endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            data = await ws.receive_text()
            # Fehler in einer Nachricht (auch kaputtes JSON) duerfen nie die
            # ganze Verbindung beenden — daran haengt die Wiedergabe
            try:
                msg = json.loads(data)
                if not isinstance(msg, dict):
                    continue
                await handle_message(ws, msg)
            except Exception:
                import traceback
                print(f"[handler error] {data[:80]!r}", file=sys.stderr)
                traceback.print_exc()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        clients.discard(ws)
        core._lib_sent.pop(ws, None)


if __name__ == "__main__":
    # Keine Kompression: nur lokal, und sie kostete bei der ganzen Bibliothek
    # (~1,5 MB) rund 0,4 s — so lange stand jede weitere Anfrage an
    uvicorn.run(app, host="127.0.0.1", port=core._BACKEND_PORT, log_level="warning",
                ws_per_message_deflate=False)
