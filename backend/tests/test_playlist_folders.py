"""Ordner fuer Playlisten: anlegen, Playlist hineinlegen, umbenennen, loeschen —
die .m3u-Dateien selbst bleiben, wo sie sind."""
from tests.support import BackendTest, FakeWS, main
from synthimix import core, library, store


class PlaylistFoldersTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._dir = core.PLAYLISTS_DIR
        core.PLAYLISTS_DIR = self.tmp / "Downloads" / "playlists"
        core.PLAYLISTS_DIR.mkdir(parents=True)
        main._state["playlist_folders"] = {"folders": [], "of": {}}
        for name in ("Neuro", "Liquid", "Hochzeit"):
            (core.PLAYLISTS_DIR / f"{name}.m3u").write_text("#EXTM3U\n", encoding="utf-8")

    def tearDown(self):
        core.PLAYLISTS_DIR = self._dir
        main._state["playlist_folders"] = {"folders": [], "of": {}}
        super().tearDown()

    def pfad(self, name):
        return str(core.PLAYLISTS_DIR / f"{name}.m3u")

    def ordner(self):
        return {p["name"]: p["folder"] for p in library._get_playlists()}

    def test_anlegen_hineinlegen_herausnehmen(self):
        self.assertTrue(library.playlist_folder_add("  DnB  ")["ok"])
        self.assertFalse(library.playlist_folder_add("dnb")["ok"])            # gibt es schon
        self.assertFalse(library.playlist_folder_add("   ")["ok"])
        self.assertTrue(library.playlist_set_folder(self.pfad("Neuro"), "DnB")["ok"])
        self.assertTrue(library.playlist_set_folder(self.pfad("Liquid"), "DnB")["ok"])
        self.assertFalse(library.playlist_set_folder(self.pfad("Neuro"), "Gibt es nicht")["ok"])
        self.assertFalse(library.playlist_set_folder(self.pfad("Fehlt"), "DnB")["ok"])
        self.assertEqual(self.ordner(), {"Neuro": "DnB", "Liquid": "DnB", "Hochzeit": ""})
        # die Dateien liegen unveraendert im Playlisten-Ordner
        self.assertEqual(sorted(p.name for p in core.PLAYLISTS_DIR.iterdir()), ["Hochzeit.m3u", "Liquid.m3u", "Neuro.m3u"])
        library.playlist_set_folder(self.pfad("Liquid"), "")
        self.assertEqual(self.ordner()["Liquid"], "")

    def test_ordner_und_playlist_umbenennen_ordner_loeschen(self):
        library.playlist_folder_add("DnB")
        library.playlist_folder_add("Feiern")
        library.playlist_set_folder(self.pfad("Neuro"), "DnB")
        self.assertFalse(library.playlist_folder_rename("DnB", "feiern")["ok"])
        self.assertTrue(library.playlist_folder_rename("DnB", "Drum and Bass")["ok"])
        self.assertEqual(library.playlist_folders(), ["Drum and Bass", "Feiern"])
        self.assertEqual(self.ordner()["Neuro"], "Drum and Bass")
        # Playlist umbenennen: sie bleibt in ihrem Ordner
        res = library._rename_playlist(self.pfad("Neuro"), "Neurofunk")
        self.assertTrue(res["ok"])
        self.assertEqual(self.ordner()["Neurofunk"], "Drum and Bass")
        # Ordner loeschen: die Playlist bleibt, steht wieder ohne Ordner da
        self.assertTrue(library.playlist_folder_delete("Drum and Bass")["ok"])
        self.assertEqual(self.ordner(), {"Neurofunk": "", "Liquid": "", "Hochzeit": ""})
        self.assertTrue((core.PLAYLISTS_DIR / "Neurofunk.m3u").exists())

    def test_nachrichten_und_ueber_neustart(self):
        ws = FakeWS()
        self.run_async(main.handle_message(ws, {"type": "playlist_folder_add", "name": "DnB"}))
        self.run_async(main.handle_message(ws, {"type": "playlist_set_folder", "path": self.pfad("Neuro"), "folder": "DnB"}))
        self.assertTrue(all(m["ok"] for m in ws.sent if m["type"] == "playlist_folder_result"))
        main._state["playlist_folders"] = {"folders": [], "of": {}}
        store.load_settings()
        self.assertEqual(library.playlist_folders(), ["DnB"])
        self.assertEqual(self.ordner()["Neuro"], "DnB")
        ws2 = FakeWS()
        self.run_async(main.handle_message(ws2, {"type": "playlist_folder_add", "name": "DnB"}))
        self.assertFalse(ws2.sent[0]["ok"])
