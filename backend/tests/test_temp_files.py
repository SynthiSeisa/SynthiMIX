"""Liegengebliebene Zwischendateien ("Titel.temp.mp3") sind keine Titel."""
import os
import time

from tests.support import BackendTest, main
from synthimix import core, download, library


class TempFiles(BackendTest):

    def test_erkennung(self):
        for n in ("Song.temp.mp3", "Song.TEMP.m4a", "Song.mp3.__tmp.mp3", "Song.mp3.norm_tmp.mp3"):
            self.assertTrue(core.is_temp_audio(n), n)
        for n in ("Song.mp3", "Temperature.mp3", "Tempest - temp mix.mp3", "Song (temp).flac"):
            self.assertFalse(core.is_temp_audio(n), n)

    def test_scan_ueberspringt_sie(self):
        d = self.tmp / "Musik"
        d.mkdir()
        (d / "Echt.mp3").write_bytes(b"x")
        (d / "Echt.temp.mp3").write_bytes(b"x")
        found = library._find_new_audio_paths(str(d), set(), True)
        self.assertEqual([os.path.basename(p) for p in found], ["Echt.mp3"])
        found = library._find_new_audio_paths(str(d), set(), False)
        self.assertEqual([os.path.basename(p) for p in found], ["Echt.mp3"])

    def test_alte_eintraege_fallen_beim_laden_raus(self):
        main._state["library"] = [{"path": str(self.tmp / "A.mp3"), "title": "A"},
                                  {"path": str(self.tmp / "A.temp.mp3"), "title": "A.temp"}]
        main.save_library()
        main._state["library"] = []
        main.load_library()
        self.assertEqual([os.path.basename(t["path"]) for t in main._state["library"]], ["A.mp3"])

    def test_nur_alte_reste_werden_aufgeraeumt(self):
        d = self.tmp / "Downloads" / "Playlist"
        d.mkdir(parents=True)
        alt, neu, echt = d / "X.temp.mp3", d / "Y.temp.mp3", d / "X.mp3"
        for p in (alt, neu, echt):
            p.write_bytes(b"x")
        t = time.time() - 7200
        os.utime(alt, (t, t))
        found = download._temp_leftovers(str(self.tmp / "Downloads"))
        self.assertEqual([os.path.basename(p) for p in found], ["X.temp.mp3"])
