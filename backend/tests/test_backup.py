"""Sicherung und Wiederherstellung: alles in eine Datei und wieder zurueck."""
import json
import zipfile

from tests.support import BackendTest, main
from synthimix import backup, core


class BackupTest(BackendTest):

    def setUp(self):
        super().setUp()
        main._state["library"] = [{"path": r"M:\Musik\a.mp3", "title": "a", "bpm_f": 174.0, "drops": [66.3]}]
        main._state["queue"] = [{"path": r"M:\Musik\a.mp3", "title": "a"}]
        main._state["lastfm_api_key"] = "geheim"
        core.PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
        (core.PLAYLISTS_DIR / "Set.m3u").write_text("#EXTM3U\n", encoding="utf-8")
        self.ziel = self.tmp / "Ziel"
        self.ziel.mkdir()

    def test_sicherung_enthaelt_alles(self):
        r = backup.create(str(self.ziel), {"appSettings": '{"djMode":true}'})
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["tracks"], r["playlists"]), (1, 1))
        with zipfile.ZipFile(r["path"]) as z:
            names = set(z.namelist())
            self.assertIn("daten/library_cache.json", names)
            self.assertIn("daten/settings.json", names)
            self.assertIn("playlists/Set.m3u", names)
            self.assertEqual(json.loads(z.read("oberflaeche.json")), {"appSettings": '{"djMode":true}'})
            self.assertEqual(json.loads(z.read("daten/library_cache.json"))[0]["drops"], [66.3])
        info = backup.inspect(r["path"])
        self.assertTrue(info["ok"])
        self.assertEqual((info["tracks"], info["playlists"]), (1, 1))

    def test_keine_sicherung(self):
        p = self.tmp / "irgendwas.zip"
        p.write_bytes(b"kein zip")
        self.assertFalse(backup.inspect(str(p))["ok"])
        self.assertFalse(backup.stage(str(p))["ok"])
        self.assertFalse(backup.create(str(self.tmp / "gibt-es-nicht"))["ok"])

    def test_wiederherstellen_beim_naechsten_start(self):
        r = backup.create(str(self.ziel), {"k": "v"})
        # danach aendert sich alles
        main._state["library"] = []
        main._state["lastfm_api_key"] = ""
        main.store.save_library(); main.store.save_settings()
        (core.PLAYLISTS_DIR / "Set.m3u").write_text("veraendert", encoding="utf-8")
        (core.PLAYLISTS_DIR / "Neu.m3u").write_text("bleibt", encoding="utf-8")
        self.assertFalse(backup.restore_pending())                    # nichts bereitgelegt
        self.assertTrue(backup.stage(r["path"])["ok"])
        self.assertTrue(backup.restore_pending())
        self.assertFalse((core.BASE_DIR / backup.MARK).exists())
        main.store.load_library(); main.store.load_settings()
        self.assertEqual(main._state["library"][0]["title"], "a")
        self.assertEqual(main._state["lastfm_api_key"], "geheim")
        # die vorigen Daten sind nicht weg
        keep = [d for d in core.BASE_DIR.iterdir() if d.is_dir() and d.name.startswith("Sicherung ")]
        self.assertEqual(len(keep), 1)
        self.assertEqual(json.loads((keep[0] / "library_cache.json").read_text("utf-8")), [])
        # Playlists: die aus der Sicherung ersetzt die gleichnamige, andere bleiben
        self.assertEqual(backup.finish_playlists(), 1)
        self.assertEqual((core.PLAYLISTS_DIR / "Set.m3u").read_text("utf-8"), "#EXTM3U\n")
        self.assertEqual((core.PLAYLISTS_DIR / "Neu.m3u").read_text("utf-8"), "bleibt")
        # Einstellungen der Oberflaeche holt sich das Fenster einmal
        self.assertEqual(backup.ui_pending(), {"k": "v"})
        backup.ui_done()
        self.assertIsNone(backup.ui_pending())

    def test_zip_schreibt_nie_aus_dem_datenordner(self):
        boese = self.tmp / "boese.zip"
        with zipfile.ZipFile(boese, "w") as z:
            z.writestr("sicherung.json", json.dumps({"app": "SynthiMIX", "format": 1}))
            z.writestr("daten/../../ausbruch.json", "{}")
            z.writestr("daten/programm.exe", "x")
        self.assertTrue(backup.stage(str(boese))["ok"])
        backup.restore_pending()
        self.assertFalse((self.tmp.parent / "ausbruch.json").exists())
        self.assertFalse((core.BASE_DIR / "programm.exe").exists())
