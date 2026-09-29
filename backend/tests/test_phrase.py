"""Takt und Phrasen im Raster (Uebergang auf Phrasenanfang, Bass-Tausch)."""
import math
import struct
import wave

from tests.support import BackendTest, FakeWS, main


def write_track(path, bpm=128.0, off=0.1, seconds=150, loud_from=8, per=32, sr=22050):
    """Klick auf jedem Schlag; laute und leise Abschnitte wechseln alle
    `per` Schlaege, der erste Wechsel bei Schlag `loud_from`."""
    beat = 60.0 / bpm
    n = int(seconds * sr)
    buf = [0.0] * n
    b = 0
    while True:
        t0 = off + b * beat
        if t0 >= seconds - beat:
            break
        loud = ((b - loud_from) // per) % 2 == 0 if b >= loud_from else False
        amp = 0.8 if loud else 0.12
        s0 = int(t0 * sr)
        for i in range(int(0.06 * sr)):          # kurzer Bass-Schlag
            if s0 + i < n:
                buf[s0 + i] += amp * math.sin(2 * math.pi * 60 * i / sr) * (1 - i / (0.06 * sr))
        b += 1
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v)) * 32000)) for v in buf))


class PhraseTest(BackendTest):

    def test_cues_einig(self):
        beat = 60 / 128
        cues = [int((0.1 + k * 32 * beat + 8 * beat) * 1000) + 50 for k in range(5)]   # MIK liegt ~50 ms daneben
        self.assertEqual(main._phrase_from_cues(0.1, beat, 32, cues), 8)
        self.assertIsNone(main._phrase_from_cues(0.1, beat, 32, [100, 1300, 2900, 7000]))

    def test_energie(self):
        self.require_ffmpeg()
        p = self.tmp / "t.wav"
        write_track(p, loud_from=8)
        r = main._phrase_sync(str(p), 128.0, 0.1)
        self.assertEqual(r["bar_beats"], 4)
        self.assertEqual(r["phrase_src"], "energy")
        self.assertEqual(round((r["phrase_off"] - 0.1) / (60 / 128)) % 32, 8)

    def test_halbtempo(self):
        self.require_ffmpeg()
        p = self.tmp / "dnb.wav"
        write_track(p, bpm=87.0, loud_from=4, per=16)
        r = main._phrase_sync(str(p), 87.0, 0.1)
        self.assertEqual(r["bar_beats"], 2)
        self.assertEqual(round((r["phrase_off"] - 0.1) / (60 / 87)) % 16, 4)

    def test_raster_bekommt_phrasen(self):
        self.require_ffmpeg()
        p = self.tmp / "t.wav"
        write_track(p, loud_from=8)
        beat = 60 / 128
        main._state["library"] = [{"path": str(p), "title": "t", "bpm_f": 128.0, "beat_off": 0.1, "beat_conf": 0.8,
                                   "mik_cues": [int((0.1 + (8 + 32 * k) * beat) * 1000) for k in range(4)]}]
        ws = FakeWS()
        self.run_async(main._send_beatgrid(ws, str(p)))
        g = ws.of_type("beatgrid")[-1]
        self.assertEqual(g["phrase_src"], "mik")
        self.assertAlmostEqual(g["phrase_off"], 0.1 + 8 * beat, places=2)
        self.assertIn("phrase_off", main._state["library"][0])
