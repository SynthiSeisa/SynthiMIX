"""Taktraster fuer den Beat-Sync: Tempo mit Nachkommastellen und erster Schlag."""
import asyncio
import json
import subprocess

from tests.support import BackendTest, main


class BeatgridTest(BackendTest):

    def make_kicks(self, name: str, bpm: float, first: float, seconds: float = 60.0):
        """Bassdrum-artige Schlaege (60 Hz, kurz abklingend) ab `first` Sekunden."""
        self.require_ffmpeg()
        path = self.tmp / name
        beat = 60.0 / bpm
        expr = f"sin(2*PI*60*t)*exp(-25*mod(t-{first},{beat}))*gte(t,{first})*0.8"
        subprocess.run([main.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"aevalsrc='{expr}':s=44100:d={seconds}", "-b:a", "192k", str(path)],
                       check=True, capture_output=True, creationflags=main._NO_WINDOW)
        return str(path)

    def check(self, g, bpm, first):
        self.assertIsNotNone(g)
        self.assertAlmostEqual(g["bpm_f"], bpm, delta=0.05)
        beat = 60.0 / g["bpm_f"]
        # Lage im Takt: Abstand zum echten ersten Schlag (modulo Schlaglaenge)
        d = (g["beat_off"] - first) % beat
        self.assertLess(min(d, beat - d), 0.015)
        self.assertGreater(g["beat_conf"], 0.4)

    def test_house_mit_tag(self):
        p = self.make_kicks("house.mp3", 126.0, 0.31)
        self.check(main._beatgrid_sync(p, 126), 126.0, 0.31)

    def test_krumme_bpm_ohne_tag(self):
        p = self.make_kicks("krumm.mp3", 123.4, 0.07)
        self.check(main._beatgrid_sync(p, 0), 123.4, 0.07)

    def test_tag_nur_ganzzahlig(self):
        # Tags haben oft nur ganze BPM; das Raster muss trotzdem genau sitzen
        p = self.make_kicks("dnb.mp3", 174.3, 0.12)
        g = main._beatgrid_sync(p, 174)
        # halbes Tempo ist ebenso gueltig (gleiche Schlaege, jeder zweite)
        if g and g["bpm_f"] < 100:
            g = {**g, "bpm_f": g["bpm_f"] * 2}
        self.check(g, 174.3, 0.12)

    def test_halbtempo_eins_auf_bassdrum(self):
        # DnB-Muster: Bassdrum auf 1, Snare (Rauschen 180-5000 Hz) auf 2 — das
        # 87er-Raster muss auf der Bassdrum beginnen, nicht auf der Snare
        self.require_ffmpeg()
        path = self.tmp / "dnb_eins.mp3"
        T, first = 60.0 / 87.0, 0.2
        kick = f"sin(2*PI*55*t)*exp(-20*mod(t-{first},{T}))*gte(t,{first})*0.8"
        snare = f"(random(0)-0.5)*exp(-30*mod(t-{first}-{T / 2},{T}))*gte(t,{first}+{T / 2})*0.9"
        subprocess.run([main.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"aevalsrc='{kick}+{snare}':s=44100:d=60", "-af", "highpass=f=30",
                        "-b:a", "192k", str(path)], check=True, capture_output=True, creationflags=main._NO_WINDOW)
        g = main._beatgrid_sync(str(path), 87)
        self.check(g, 87.0, first)
        self.assertEqual(g["grid_rev"], main.beatgrid.GRID_REV)

    def test_hihat_auf_der_achtel_zieht_nicht_weg(self):
        # Weiche Bassdrum ohne Obertoene, Hi-Hat genau dazwischen: der Schlag
        # gehoert auf die Bassdrum, nicht auf die Hi-Hat
        self.require_ffmpeg()
        path = self.tmp / "hihat.wav"
        T, first = 60.0 / 128.0, 0.1
        kick = f"sin(2*PI*55*mod(t-{first},{T}))*max(0,1-mod(t-{first},{T})/0.07)*gte(t,{first})*0.8"
        hat = f"sin(2*PI*3000*mod(t-{first}-{T / 2},{T}))*max(0,1-mod(t-{first}-{T / 2},{T})/0.03)*gte(t,{first}+{T / 2})*0.1"
        subprocess.run([main.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"aevalsrc='{kick}+{hat}':s=22050:d=60", str(path)],
                       check=True, capture_output=True, creationflags=main._NO_WINDOW)
        g = main._beatgrid_sync(str(path), 128)
        self.check(g, 128.0, first)
        self.assertIsInstance(g["beat_off"], float)

    def test_altes_raster_wird_neu_gemessen(self):
        p = self.make_kicks("alt.mp3", 126.0, 0.31)
        main._state["library"] = [{"path": p, "bpm": 126, "bpm_f": 125.5, "beat_off": 0.2, "beat_conf": 0.9,
                                   "phrase_off": 0.2, "bar_beats": 4, "phrase_src": "audio"}]
        sent = []

        class WS:
            async def send_text(self, s):
                sent.append(json.loads(s))

        asyncio.run(main.beatgrid._send_beatgrid(WS(), p))
        lt = main._state["library"][0]
        self.assertEqual(lt["grid_rev"], main.beatgrid.GRID_REV)
        self.check(lt, 126.0, 0.31)
        self.assertAlmostEqual(sent[-1]["bpm_f"], lt["bpm_f"])

    def test_zu_kurz(self):
        p = self.make_kicks("kurz.mp3", 128.0, 0.0, seconds=5)
        self.assertIsNone(main._beatgrid_sync(p, 128))
