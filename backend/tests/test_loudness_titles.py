"""Lautheit des Hauptteils und nach 30 Zeichen abgeschnittene Titel."""
import subprocess

from tests.support import BackendTest, main


class MainLoudnessTest(BackendTest):

    def test_ruhiges_intro_zaehlt_nicht(self):
        # 20 s um 8 dB leiser (wie ein Breakdown, ueber der R128-Schwelle), dann laut: ganz gemessen liegt der
        # Wert deutlich unter dem lauten Teil, der Hauptteil trifft ihn
        self.require_ffmpeg()
        p = self.tmp / "intro.mp3"
        subprocess.run([main.FFMPEG, "-y", "-v", "error",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                        "-filter_complex", "[0:a]volume=0.2[a];[1:a]volume=0.5[b];[a][b]concat=n=2:v=0:a=1[out]",
                        "-map", "[out]", "-ac", "1", "-b:a", "128k", str(p)],
                       check=True, capture_output=True, creationflags=main._NO_WINDOW)
        ganz, haupt = main._measure_loudness_sync(str(p))
        laut, _ = main._measure_loudness_sync(str(self.make_audio(self.tmp / "laut.mp3", seconds=20, tones=[(440, 0.5)])))
        self.assertIsNotNone(haupt)
        self.assertLess(abs(haupt - laut), 1.0)
        self.assertGreater(haupt - ganz, 1.5)

    def test_kurzer_titel_ohne_hauptteil(self):
        p = self.make_audio(self.tmp / "kurz.mp3", seconds=1.5, tones=[(440, 0.5)])
        _, haupt = main._measure_loudness_sync(str(p))
        self.assertIsNone(haupt)

    def test_hauptteil_bleibt_beim_laden(self):
        main._state["library"] = [{"path": str(self.tmp / "a.mp3"), "title": "A", "lufs": -9.0, "lufs_main": -7.5}]
        main.save_library()
        main._state["library"] = []
        main.load_library()
        self.assertEqual(main._state["library"][0].get("lufs_main"), -7.5)


class CompleteTitleTest(BackendTest):

    def test_abgeschnitten_aus_dateiname(self):
        f = main._complete_title
        self.assertEqual(f("Ivy Lab - Sunday Crunk (Mefjus", r"M:\x\Ivy Lab - Sunday Crunk (Mefjus Remix).mp3"),
                         "Ivy Lab - Sunday Crunk (Mefjus Remix)")
        self.assertEqual(f("BTK, Maztek & Optiv - Footprin", r"M:\x\03. BTK, Maztek & Optiv - Footprint.mp3"),
                         "BTK, Maztek & Optiv - Footprint")

    def test_nichts_aendern(self):
        f = main._complete_title
        # vollstaendig (Dateiname endet mit dem Titel)
        self.assertEqual(f("Circles (feat. Jon Lillygreen)", r"M:\x\46. BMotion - Circles (feat. Jon Lillygreen).mp3"),
                         "Circles (feat. Jon Lillygreen)")
        # Kopie "-1-1-1" ist kein abgeschnittener Titel
        self.assertEqual(f("Dimension - UK (skrillex edit)", r"M:\x\Dimension - UK (skrillex edit)-1-1-1.mp3"),
                         "Dimension - UK (skrillex edit)")
        # nicht 30 Zeichen: nie anfassen
        self.assertEqual(f("Kurz", r"M:\x\Kurz und laenger.mp3"), "Kurz")

    def test_emoji_zeichensalat(self):
        self.assertEqual(main._fix_mojibake("Meanwhile in Austria. \u00f0\u0178\u2021\u00a6"), "Meanwhile in Austria. \U0001F1E6")
