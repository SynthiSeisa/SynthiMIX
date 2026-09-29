"""Testdaten fuer die Oberflaechen-Tests: ein paar kurze 128-BPM-Titel mit
Phrasen (laute/leise Abschnitte alle 8 Takte) und eine Bibliothek dazu.

python seed.py <datenordner>
"""
import json
import math
import struct
import sys
import time
import wave
from pathlib import Path

BPM, OFF, SR, SECONDS = 128.0, 0.1, 22050, 75


def write_track(path: Path, loud_from: int, pitch: float, bpm: float = BPM):
    beat = 60.0 / bpm
    n = int(SECONDS * SR)
    buf = [0.0] * n
    b = 0
    while OFF + b * beat < SECONDS - beat:
        loud = b >= loud_from and ((b - loud_from) // 32) % 2 == 0
        amp = 0.8 if loud else 0.15
        s0 = int((OFF + b * beat) * SR)
        for i in range(int(0.07 * SR)):                 # Bassdrum
            if s0 + i < n:
                buf[s0 + i] += amp * math.sin(2 * math.pi * 55 * i / SR) * (1 - i / (0.07 * SR))
        for i in range(int(0.03 * SR)):                 # Hi-Hat auf der Achtel
            j = s0 + int(beat / 2 * SR) + i
            if j < n:
                buf[j] += 0.1 * math.sin(2 * math.pi * pitch * i / SR) * (1 - i / (0.03 * SR))
        b += 1
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32000)) for v in buf))


def main(data: Path):
    music = data / "Musik"
    music.mkdir(parents=True, exist_ok=True)
    tracks = []
    # "Echo" hat 144 BPM (12,5 % schneller) fuer den Test mit groesserem Tempo-Unterschied
    for title, artist, pitch, bpm in [("Alpha", "Test Crew", 3000, BPM), ("Bravo", "Test Crew", 3500, BPM),
                                      ("Charlie", "Beispiel", 4000, BPM), ("Delta", "Beispiel", 4500, BPM),
                                      ("Echo", "Schneller", 5000, 144.0)]:
        p = music / f"{artist} - {title}.wav"
        if not p.exists():
            write_track(p, loud_from=8, pitch=pitch, bpm=bpm)
        tracks.append({"path": str(p), "title": title, "artist": artist, "folder": "Musik",
                       "duration_sec": float(SECONDS), "lufs": -10.0, "bpm": int(bpm), "bitrate_kbps": 352,
                       "ext": "wav", "mtime": int(p.stat().st_mtime), "play_count": 0, "key": "8A", "key_src": "tag"})
    (data / "library_cache.json").write_text(json.dumps(tracks, ensure_ascii=False), "utf-8")
    (data / "queue.json").write_text(json.dumps({"items": [], "current_idx": -1}), "utf-8")
    # Kein Werkzeug-Update im Test (wuerde yt-dlp herunterladen)
    (data / "settings.json").write_text(json.dumps({"crossfade_s": 8, "volume": 80, "auto_mix": False,
                                                    "bpm_analysis": False, "watched_folders": [],
                                                    "ytdlp_autoupdate": False,
                                                    "ytdlp_last_check": int(time.time())}), "utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
