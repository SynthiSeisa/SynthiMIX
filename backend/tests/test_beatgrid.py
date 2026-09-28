"""Taktraster fuer den Beat-Sync: Tempo mit Nachkommastellen und erster Schlag."""
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

    def test_zu_kurz(self):
        p = self.make_kicks("kurz.mp3", 128.0, 0.0, seconds=5)
        self.assertIsNone(main._beatgrid_sync(p, 128))
