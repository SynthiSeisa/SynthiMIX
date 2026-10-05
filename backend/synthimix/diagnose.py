"""Diagnose: warum geht an diesem PC etwas nicht?

Abspielen macht das Fenster selbst — Waveform, Analyse, Einlesen und Sortieren
macht das Backend mit ffmpeg/ffprobe und numpy. Laeuft davon etwas an einem
PC nicht (Virenscanner blockt die exe, alte CPU, Datei nicht lesbar), sah man
bisher nur "Waveform fehlt". Der Bericht zeigt, woran es liegt.
"""
import os
import platform
import subprocess
import sys
import time
from .core import _NO_WINDOW, _state
from . import core, keys, library, media, net


def _run_tool(exe: str) -> dict:
    """Laesst sich das Werkzeug starten? {ok, text, ms}"""
    t0 = time.monotonic()
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15, creationflags=_NO_WINDOW)
        first = (r.stdout or r.stderr or "").strip().splitlines()[:1]
        ok = r.returncode == 0 and bool(first)
        text = first[0][:120] if first else f"keine Ausgabe (Code {r.returncode})"
        if not ok and r.returncode:
            text = f"Code {r.returncode}: {text}"
    except FileNotFoundError:
        ok, text = False, "nicht gefunden"
    except subprocess.TimeoutExpired:
        ok, text = False, "antwortet nicht (15 s) — vom Virenscanner aufgehalten?"
    except OSError as e:
        ok, text = False, f"laesst sich nicht starten: {e}"
    return {"ok": ok, "text": text, "ms": int((time.monotonic() - t0) * 1000), "path": exe}


def _waveform_test(path: str) -> dict:
    t0 = time.monotonic()
    try:
        with open(path, "rb") as f:
            f.read(4096)
    except OSError as e:
        return {"ok": False, "text": f"Datei nicht lesbar: {e}", "path": path}
    data = media._waveform_sync(path, 1000)
    ms = int((time.monotonic() - t0) * 1000)
    return {"ok": len(data) == 1000, "text": f"{len(data)} Balken in {ms} ms" if data else "ffmpeg lieferte kein Audio",
            "ms": ms, "path": path}


def run_sync() -> dict:
    """Alles pruefen (dauert ein paar Sekunden). Liefert {checks: [{name, ok, text}], report}."""
    checks: list[dict] = []

    def add(name, ok, text, **more):
        checks.append({"name": name, "ok": bool(ok), "text": str(text), **more})

    for name, exe in (("ffmpeg", core.FFMPEG), ("ffprobe", core.FFPROBE)):
        r = _run_tool(exe)
        add(name, r["ok"], f"{r['text']} · {r['ms']} ms · {r['path']}")
    try:
        import numpy as np
        add("Analyse (numpy)", True, np.__version__)
    except Exception as e:
        add("Analyse (numpy)", False, f"laedt nicht: {e}")

    n = net.check()
    add("Internet (Zertifikate)", n["ok"], n["text"])
    # Geht die Uhr des PCs falsch, scheitert jede gesicherte Verbindung
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        if n.get("date"):
            off = abs((datetime.now(timezone.utc) - parsedate_to_datetime(n["date"])).total_seconds())
            add("Uhrzeit des PCs", off < 300, "stimmt" if off < 300 else f"weicht {int(off // 60)} Minuten ab — Datum und Uhrzeit in Windows pruefen")
    except Exception:
        pass

    # Download-Ordner: erreichbar, beschreibbar, im tragbaren Betrieb auf der Platte?
    dl = _state.get("download_dir") or str(core.BASE_DIR / "Downloads")
    if not os.path.isdir(dl):
        add("Download-Ordner", False, f"{dl}: gibt es an diesem PC nicht — unter Einstellungen → Download neu waehlen")
    else:
        try:
            t = os.path.join(dl, ".schreibtest")
            with open(t, "w", encoding="utf-8") as f:
                f.write("x")
            os.remove(t)
            same = os.path.splitdrive(dl)[0].upper() == os.path.splitdrive(str(core.BASE_DIR))[0].upper()
            add("Download-Ordner", True, dl + ("" if same or not core.PORTABLE else " — liegt nicht auf der tragbaren Platte, reist also nicht mit"))
        except OSError as e:
            add("Download-Ordner", False, f"{dl}: nicht beschreibbar ({e})")

    # Datenordner beschreibbar?
    try:
        probe = core.BASE_DIR / ".schreibtest"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        add("Datenordner", True, f"{core.BASE_DIR}" + (" (tragbar)" if core.PORTABLE else ""))
    except OSError as e:
        add("Datenordner", False, f"{core.BASE_DIR}: nicht beschreibbar ({e})")

    lib = _state.get("library", [])
    present = [t for t in lib if not t.get("missing")]
    n_key = sum(1 for t in present if keys._key_to_camelot(t.get("key")))
    n_dur = sum(1 for t in present if (t.get("duration_sec") or 0) > 0)
    add("Bibliothek", bool(lib), f"{len(lib)} Titel, {len(lib) - len(present)} fehlen, "
        f"{n_key} mit Tonart, {n_dur} mit Laenge")
    if present and n_dur < len(present) * 0.9:
        add("Titel ohne Laenge", False, f"{len(present) - n_dur} Titel liessen sich beim Einlesen nicht pruefen")
    if present and n_key < 2:
        add("Harmonisch sortieren", False, "kaum Titel mit Tonart — ohne Tonart gibt es nichts zu sortieren")

    for f in _state.get("watched_folders", []):
        add("Ordner", os.path.isdir(f), f if os.path.isdir(f) else f"{f}: nicht erreichbar")
    bad = list(library.scan_unreadable)
    if bad:
        add("Beim Einlesen nicht lesbar", False, f"{len(bad)} Ordner/Dateien, z. B. {bad[0]}")

    sample = next((t["path"] for t in present if os.path.exists(t.get("path", ""))), None)
    if sample:
        w = _waveform_test(sample)
        add("Waveform-Test", w["ok"], f"{w['text']} · {os.path.basename(sample)}")
    else:
        add("Waveform-Test", False, "kein Titel zum Testen in der Bibliothek")

    head = (f"SynthiMIX Diagnose · {time.strftime('%d.%m.%Y %H:%M')} · {platform.platform()} · "
            f"{platform.machine()} · {os.cpu_count()} Kerne · Python {sys.version.split()[0]}")
    report = "\n".join([head] + [f"[{'ok' if c['ok'] else 'FEHLER'}] {c['name']}: {c['text']}" for c in checks])
    return {"checks": checks, "report": report, "ok": all(c["ok"] for c in checks)}
