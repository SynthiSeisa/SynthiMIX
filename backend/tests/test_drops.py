"""Drop-Erkennung (aus Synthi's Mashups): Lautheit und Bass je Takt, MIK-Cues,
alle Drops eines Titels auf Phrasengrenzen."""
import unittest

import numpy as np

from tests.support import BackendTest, main
from synthimix import beatgrid

SR = beatgrid.DROP_SR
BPM = 128.0
BAR = 4 * 60.0 / BPM


def _track(parts, sr=SR, seed=1):
    """intro = Kick + Hi-Hat leise, build = kein Bass, Rauschen steigt,
    drop = Kick + Bass laut, break = leise Flaeche ohne Bass."""
    rng = np.random.default_rng(seed)
    beat = 60.0 / BPM
    out = []
    for bars, kind in parts:
        n = int(bars * BAR * sr)
        t = np.arange(n) / sr
        ph = (t % beat) / beat
        kick = np.sin(2 * np.pi * 55 * t) * np.exp(-ph * 12)
        hat = rng.normal(0, 1, n) * np.exp(-((t + beat / 2) % beat) / beat * 30)
        if kind == "intro":
            y = 0.25 * kick + 0.05 * hat
        elif kind == "build":
            y = rng.normal(0, 1, n) * np.linspace(0.02, 0.12, n) + 0.05 * hat
            y = y - np.convolve(y, np.ones(40) / 40, mode="same")
        elif kind == "drop":
            y = 0.6 * kick + 0.35 * np.sin(2 * np.pi * 45 * t) + 0.1 * hat
        else:
            y = 0.04 * np.sin(2 * np.pi * 440 * t) + 0.02 * rng.normal(0, 1, n)
        out.append(y.astype(np.float32))
    return np.concatenate(out)


STRUCT = [(16, "intro"), (8, "build"), (16, "drop"), (16, "break"), (8, "build"), (16, "drop"), (16, "intro")]
DROP1, DROP2 = 24 * BAR, 64 * BAR


class FindDropsTest(unittest.TestCase):

    def test_beide_drops_erster_zuerst(self):
        drops, src = beatgrid._find_drops(_track(STRUCT), SR, BPM, 0.0, 0.0)
        self.assertEqual(src, "energy")
        self.assertEqual(len(drops), 2)
        self.assertAlmostEqual(drops[0], DROP1, delta=0.01)
        self.assertAlmostEqual(drops[1], DROP2, delta=0.01)

    def test_nur_auf_phrasen(self):
        x = np.concatenate([np.zeros(int(2 * BAR * SR), np.float32), _track(STRUCT)])
        drops, _ = beatgrid._find_drops(x, SR, BPM, 0.0, 2 * BAR)
        self.assertAlmostEqual(drops[0], DROP1 + 2 * BAR, delta=0.01)

    def test_mik_cue_auf_dem_drop(self):
        drops, src = beatgrid._find_drops(_track(STRUCT), SR, BPM, 0.0, 0.0, cues=[0.0, DROP1 + 0.045])
        self.assertEqual(src, "mik")
        self.assertAlmostEqual(drops[0], DROP1, delta=0.01)      # auf den Takt, nicht 45 ms daneben

    def test_kein_drop_ohne_aufbau(self):
        drops, src = beatgrid._find_drops(_track([(64, "intro")]), SR, BPM, 0.0, 0.0)
        self.assertEqual((drops, src), ([], "none"))

    def test_zu_kurz(self):
        self.assertEqual(beatgrid._find_drops(_track([(8, "drop")]), SR, BPM, 0.0, 0.0), ([], "none"))


class DropsInLibraryTest(BackendTest):

    def test_drops_mit_dem_raster_gespeichert(self):
        self.require_ffmpeg()
        import wave
        p = self.tmp / "drop.wav"
        x = (np.clip(_track(STRUCT, sr=22050), -1, 1) * 30000).astype(np.int16)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(x.tobytes())
        lt = {"path": str(p), "title": "Drop", "bpm": 128, "duration_sec": 140}
        main._state["library"] = [lt]
        sent = []

        class WS:
            async def send_text(self, s):
                import json
                sent.append(json.loads(s))
        self.run_async(beatgrid._send_beatgrid(WS(), str(p)))
        self.assertEqual(lt["drop_rev"], beatgrid.DROP_REV)
        self.assertGreaterEqual(len(lt["drops"]), 1)
        self.assertAlmostEqual(lt["drops"][0], DROP1, delta=BAR / 4)
        self.assertEqual(sent[-1]["drops"], lt["drops"])
        # bleibt beim Laden der Bibliothek erhalten
        main.store.save_library()
        main._state["library"] = []
        main.store.load_library()
        self.assertEqual(main._state["library"][0]["drops"], lt["drops"])
