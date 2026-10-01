"""BPM ohne Tag: aus dem Taktraster, alte Schaetzungen werden neu bestimmt,
BPM aus dem Tag (Mixed In Key) bleiben."""
import subprocess

from tests.support import BackendTest, main
from synthimix import media


class BpmTest(BackendTest):

    def kicks(self, name, bpm, seconds=60.0, tbpm=None):
        """Bassdrum auf jedem Schlag (mit Anschlag), optional mit BPM-Tag."""
        self.require_ffmpeg()
        path = self.tmp / name
        beat = 60.0 / bpm
        expr = f"(sin(2*PI*60*t)+0.3*sin(2*PI*1500*t))*exp(-25*mod(t-0.1,{beat}))*gte(t,0.1)*0.7"
        cmd = [main.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={seconds}"]
        if tbpm:
            cmd += ["-metadata", f"TBPM={tbpm}"]
        subprocess.run(cmd + ["-b:a", "192k", str(path)], check=True, capture_output=True,
                       creationflags=main._NO_WINDOW)
        return str(path)

    def entry(self, path, **kw):
        return {"path": path, "title": "T", "duration_sec": 60, "lufs": -9.0,
                "meta_rev": media._TAG_META_REV, **kw}

    def test_alte_schaetzung_wird_neu_bestimmt(self):
        p = self.kicks("alt.mp3", 128)
        lt = self.entry(p, bpm=99)                       # grob daneben (altes Verfahren)
        main._state["library"] = [lt]
        main._state["queue"] = [{"path": p, "bpm": 99}]
        self.assertEqual(self.run_async(media._bpm_fix_once()), 1)
        self.assertEqual(lt["bpm"], 128)
        self.assertEqual(lt["bpm_src"], "analyse")
        self.assertAlmostEqual(lt["bpm_f"], 128, delta=0.1)        # Raster gleich mit
        self.assertEqual(lt["grid_rev"], main.beatgrid.GRID_REV)
        self.assertEqual(main._state["queue"][0]["bpm"], 128)
        # Nur einmal
        self.assertEqual(self.run_async(media._bpm_fix_once()), 0)

    def test_bpm_aus_dem_tag_bleibt(self):
        p = self.kicks("tag.mp3", 128)
        lt = self.entry(p, bpm=64, bpm_src="tag")
        main._state["library"] = [lt]
        self.assertEqual(self.run_async(media._bpm_fix_once()), 0)
        self.assertEqual(lt["bpm"], 64)

    def test_erst_nach_dem_tag_abgleich(self):
        p = self.kicks("neu.mp3", 128)
        lt = self.entry(p, bpm=99, meta_rev=media._TAG_META_REV - 1)
        main._state["library"] = [lt]
        self.assertEqual(self.run_async(media._bpm_fix_once()), 0)

    def test_tag_abgleich_merkt_die_herkunft(self):
        p = self.kicks("mik.mp3", 128, tbpm="128")
        lt = self.entry(p, bpm=128, meta_rev=media._TAG_META_REV - 1)
        main._state["library"] = [lt]
        self.run_async(main._refresh_tag_meta_task())
        self.assertEqual(lt["bpm_src"], "tag")
        self.assertEqual(self.run_async(media._bpm_fix_once()), 0)

    def test_spaeter_mit_mik_analysiert_gewinnt_der_tag(self):
        # Erst von SynthiMIX geschaetzt (174), danach schreibt MIK 87 in die Datei
        p = self.kicks("spaeter.mp3", 128, tbpm="87")
        import os
        lt = self.entry(p, bpm=174, bpm_src="analyse", bpm_rev=media._BPM_REV,
                        mtime=int(os.path.getmtime(p)) - 60)        # Datei seitdem geaendert
        main._state["library"] = [lt]
        self.run_async(main._refresh_tag_meta_task())
        self.assertEqual((lt["bpm"], lt["bpm_src"]), (87, "tag"))
        self.assertEqual(self.run_async(media._bpm_fix_once()), 0)

    def test_tag_tempo_bleibt_auch_wenn_das_raster_anders_misst(self):
        # MIK sagt 87 (halbes DnB-Tempo) — die eigene Messung darf das nicht ersetzen
        p = self.kicks("halb.mp3", 174, tbpm="87")
        lt = self.entry(p, bpm=0, meta_rev=media._TAG_META_REV - 1)
        main._state["library"] = [lt]
        self.run_async(main._refresh_tag_meta_task())
        self.run_async(media._bpm_fix_once())
        self.assertEqual((lt["bpm"], lt["bpm_src"]), (87, "tag"))

    def test_aus_bei_ausgeschalteter_analyse(self):
        p = self.kicks("aus.mp3", 128)
        lt = self.entry(p, bpm=99)
        main._state["library"] = [lt]
        keep = main._state.get("bpm_analysis", True)
        main._state["bpm_analysis"] = False
        try:
            self.assertEqual(self.run_async(media._bpm_fix_once()), 0)
        finally:
            main._state["bpm_analysis"] = keep
        self.assertEqual(lt["bpm"], 99)
