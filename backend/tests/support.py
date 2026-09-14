"""Gemeinsame Hilfen fuer die Backend-Tests.

main.py wird einmal importiert. Der Server startet nur unter __main__, es
werden also keine Ports belegt — ein laufendes SynthiMIX stoert nicht und wird
nicht gestoert. Alle Dateien, die main.py liest oder schreibt, zeigen auf einen
eigenen Temp-Ordner je Test.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
_IMPORT_DIR = tempfile.mkdtemp(prefix="synthimix-test-import-")

if "main" not in sys.modules:
    _argv = sys.argv
    sys.argv = ["main.py", "--data-dir", _IMPORT_DIR]
    sys.path.insert(0, str(BACKEND))
    try:
        import main  # noqa: E402
    finally:
        sys.argv = _argv
main = sys.modules["main"]

_FILES = {
    "QUEUE_FILE": "queue.json", "SETTINGS_FILE": "settings.json",
    "LIB_CACHE": "library_cache.json", "HISTORY_FILE": "history.json",
    "PLAY_LOG_FILE": "play_log.json", "NOTES_FILE": "notes.json",
    "WISHES_FILE": "wishes.json",
}
_LIST_STATE = ("library", "queue", "downloads", "history", "play_log", "wishes")


class BackendTest(unittest.TestCase):
    """Basisklasse: frischer Zustand und eigener Datenordner je Test."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="synthimix-test-"))
        self._saved = {name: getattr(main, name) for name in (*_FILES, "BASE_DIR", "PLAYLISTS_DIR")}
        for name, fname in _FILES.items():
            setattr(main, name, self.tmp / fname)
        main.BASE_DIR = self.tmp
        main.PLAYLISTS_DIR = self.tmp / "playlists"
        self._saved_state = {k: main._state.get(k) for k in (
            *_LIST_STATE, "current_idx", "download_dir", "watched_folders",
            "scan_recursive", "dl_filename_format", "position_ms", "duration_ms")}
        for k in _LIST_STATE:
            main._state[k] = []
        main._state["current_idx"] = -1
        main._state["download_dir"] = str(self.tmp / "Downloads")
        main._state["watched_folders"] = []
        main._state["scan_recursive"] = True
        main._state["dl_filename_format"] = "title"
        main._state["position_ms"] = 0
        main._state["duration_ms"] = 0

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(main, name, value)
        main._state.update(self._saved_state)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── Hilfen ──────────────────────────────────────────────────────────────
    @staticmethod
    def run_async(coro):
        return asyncio.run(coro)

    def require_ffmpeg(self):
        if not (os.path.isfile(main.FFMPEG) or shutil.which(main.FFMPEG)):
            self.skipTest("ffmpeg nicht gefunden (bin/ffmpeg.exe fehlt)")

    def make_audio(self, path: Path, seconds: float = 3.0, tones=None, tags: dict | None = None) -> Path:
        """MP3 per ffmpeg erzeugen: Stille oder Summe von Sinustoenen
        [(frequenz, lautstaerke), ...]. Tags werden per mutagen als ID3 gesetzt."""
        self.require_ffmpeg()
        path.parent.mkdir(parents=True, exist_ok=True)
        if tones:
            inputs, labels = [], []
            for i, (freq, _) in enumerate(tones):
                inputs += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}"]
                labels.append(f"[{i}:a]volume={tones[i][1]}[t{i}]")
            mix = ";".join(labels) + ";" + "".join(f"[t{i}]" for i in range(len(tones))) + \
                  f"amix=inputs={len(tones)}:normalize=0[out]"
            cmd = [main.FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", mix,
                   "-map", "[out]", "-ac", "1", "-b:a", "128k", str(path)]
        else:
            cmd = [main.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                   f"anullsrc=r=44100:cl=mono", "-t", str(seconds), "-b:a", "64k", str(path)]
        subprocess.run(cmd, check=True, capture_output=True, creationflags=main._NO_WINDOW)
        if tags:
            from mutagen.id3 import ID3, TPE1, TPE2, TALB, TCON, TKEY, TIT2
            frames = {"title": TIT2, "artist": TPE1, "album_artist": TPE2,
                      "album": TALB, "genre": TCON, "key": TKEY}
            try:
                id3 = ID3(str(path))
            except Exception:
                id3 = ID3()
            for k, v in tags.items():
                id3.add(frames[k](encoding=3, text=v))
            id3.save(str(path))
        return path

    def write_library(self, entries: list[dict]):
        main.LIB_CACHE.write_text(json.dumps(entries, ensure_ascii=False), "utf-8")


class FakeWS:
    """Steht fuer eine WebSocket-Verbindung und merkt sich, was gesendet wurde."""

    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    def of_type(self, t):
        return [m for m in self.sent if m.get("type") == t]
