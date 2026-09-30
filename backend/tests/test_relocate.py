"""Laufwerk gewechselt: fehlende Titel finden und alle Pfade umstellen."""
import os

from tests.support import BackendTest, main
from synthimix import beatgrid, core, media, relocate, store


class Relocate(BackendTest):

    def _setup(self):
        old, new = str(self.tmp / "M"), str(self.tmp / "X")
        os.makedirs(os.path.join(new, "Musik", "DnB"))
        for n in ("A.mp3", "B.mp3"):
            open(os.path.join(new, "Musik", "DnB", n), "wb").close()
        pa, pb = (os.path.join(old, "Musik", "DnB", n) for n in ("A.mp3", "B.mp3"))
        main._state["library"] = [
            {"path": pa, "title": "A", "bpm": 174, "key": "Fm", "play_count": 7, "lufs": -8.0, "missing": True},
            {"path": pb, "title": "B", "bpm": 172, "play_count": 2, "lufs": -9.0},
            # neuer Ort schon eingelesen: ohne Analyse
            {"path": os.path.join(new, "Musik", "DnB", "B.mp3"), "title": "B", "bpm": 0, "lufs": -99.0, "genre": "DnB"},
        ]
        dl = {"url": "u", "path": pa, "status": "done"}
        main._state["downloads"] = [dl]
        main._state["queue"] = [{"path": pa, "title": "A"}]
        main._state["watched_folders"] = [os.path.join(old, "Musik")]
        main._state["download_dir"] = os.path.join(old, "Musik", "Downloads")
        main._state["favorites"] = [os.path.join(old, "Musik", "DnB")]
        main._state["play_log"] = [{"path": pb, "title": "B"}]
        beatgrid._beatgrid_cache[pa] = {"bpm_f": 174.0}
        store._quality_cache[store._qkey(pa, 200)] = 16.0
        core.PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
        (core.PLAYLISTS_DIR / "Set.m3u").write_text(f"#EXTM3U\n#EXTINF:200,A\n{pa}\n", encoding="utf-8")
        return old, new, dl

    def test_alles_wird_umgestellt(self):
        old, new, dl = self._setup()
        res = relocate.apply(old, new)
        lib = main._state["library"]
        self.assertEqual(len(lib), 2)                           # B zusammengefuehrt
        self.assertEqual(res["merged"], 1)
        a, b = lib
        self.assertTrue(a["path"].startswith(new))
        self.assertEqual((a["bpm"], a["key"], a["play_count"]), (174, "Fm", 7))
        self.assertNotIn("missing", a)                          # Datei ist am neuen Ort da
        self.assertEqual((b["bpm"], b["play_count"], b["lufs"]), (172, 2, -9.0))
        self.assertEqual(b.get("genre"), "DnB")                 # fehlendes Feld vom Doppel
        self.assertTrue(main._state["queue"][0]["path"].startswith(new))
        self.assertTrue(main._state["watched_folders"][0].startswith(new))
        self.assertTrue(main._state["download_dir"].startswith(new))
        self.assertTrue(main._state["favorites"][0].startswith(new))
        self.assertTrue(main._state["play_log"][0]["path"].startswith(new))
        # laufende Downloads behalten ihr Objekt
        self.assertIs(main._state["downloads"][0], dl)
        self.assertTrue(dl["path"].startswith(new))
        self.assertIn(a["path"], beatgrid._beatgrid_cache)
        self.assertIn(store._qkey(a["path"], 200), store._quality_cache)
        self.assertIn(a["path"], (core.PLAYLISTS_DIR / "Set.m3u").read_text(encoding="utf-8"))
        self.assertEqual(res["playlists"], 1)

    def test_nur_ganze_ordnernamen(self):
        self.assertTrue(relocate._has_prefix("M:\\Musik\\a.mp3", "M:\\"))
        self.assertTrue(relocate._has_prefix("m:\\musik", "M:\\Musik"))
        self.assertFalse(relocate._has_prefix("M:\\Musik2\\a.mp3", "M:\\Musik"))
        self.assertFalse(relocate._has_prefix("C:\\Musik", "M:\\"))

    def test_erkennen(self):
        paths = [f"M:\\Musik\\{i}.mp3" for i in range(30)]
        main._state["library"] = [{"path": p, "title": str(i)} for i, p in enumerate(paths)]
        keep_d, keep_e = relocate._drives, relocate.os.path.exists
        relocate._drives = lambda: ["C:", "X:"]
        relocate.os.path.exists = lambda p: str(p).upper().startswith("X:\\MUSIK")
        try:
            items = relocate.detect()
        finally:
            relocate._drives, relocate.os.path.exists = keep_d, keep_e
        self.assertEqual(items, [{"from": "M:\\", "to": "X:\\", "count": 30, "found": 1.0}])

    def test_nichts_wenn_alles_da(self):
        paths = [f"M:\\Musik\\{i}.mp3" for i in range(30)]
        main._state["library"] = [{"path": p, "title": str(i)} for i, p in enumerate(paths)]
        keep = relocate.os.path.exists
        relocate.os.path.exists = lambda p: True
        try:
            self.assertEqual(relocate.detect(), [])
        finally:
            relocate.os.path.exists = keep
