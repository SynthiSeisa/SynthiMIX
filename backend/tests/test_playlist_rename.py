"""Playlists umbenennen — auch verfolgte (eigener Name bleibt bei der naechsten Pruefung)."""
from tests.support import BackendTest, FakeWS, main


class PlaylistRenameTest(BackendTest):

    def setUp(self):
        super().setUp()
        main.PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
        self.dl = self.tmp / "Downloads"
        main._state["download_dir"] = str(self.dl)
        self._followed = main._state.get("followed")
        main._state["followed"] = []

    def tearDown(self):
        main._state["followed"] = self._followed
        super().tearDown()

    def pl(self, name):
        p = main.PLAYLISTS_DIR / (name + ".m3u")
        p.write_text("#EXTM3U\n#EXTINF:200,A\nC:\\x\\a.mp3\n", "utf-8")
        return p

    def test_umbenennen(self):
        p = self.pl("Alt")
        ws = FakeWS()
        self.run_async(main.handle_message(ws, {"type": "rename_playlist", "path": str(p), "name": "Neu: Sommer"}))
        res = [m for m in ws.sent if m.get("type") == "playlist_renamed"][0]
        self.assertTrue(res["ok"])
        self.assertFalse(p.exists())
        self.assertTrue((main.PLAYLISTS_DIR / "Neu_ Sommer.m3u").exists())     # ":" geht nicht im Dateinamen
        self.assertIn("Neu_ Sommer", [x["name"] for x in main._get_playlists()])

    def test_name_schon_vergeben(self):
        p = self.pl("A"); self.pl("B")
        res = main.library._rename_playlist(str(p), "B")
        self.assertFalse(res["ok"])
        self.assertTrue(p.exists())

    def test_nur_playlists_im_datenordner(self):
        fremd = self.tmp / "irgendwo.m3u"
        fremd.write_text("#EXTM3U\n", "utf-8")
        self.assertFalse(main.library._rename_playlist(str(fremd), "x")["ok"])
        self.assertTrue(fremd.exists())

    def test_verfolgte_playlist_behaelt_den_namen(self):
        p = self.pl("DnB Bangers 2026")
        (self.dl / "DnB Bangers 2026").mkdir(parents=True)
        m3u8 = self.dl / "DnB Bangers 2026" / "DnB Bangers 2026.m3u8"
        m3u8.write_text("#EXTM3U\n", "utf-8")
        f = {"url": "https://www.youtube.com/playlist?list=PLx", "title": "DnB Bangers 2026",
             "folder": "DnB Bangers 2026", "mode": "playlist", "seen": []}
        main._state["followed"] = [f]
        self.assertTrue(main.library._rename_playlist(str(p), "Autofahrt")["ok"])
        self.assertEqual(f["name"], "Autofahrt")
        self.assertTrue((self.dl / "DnB Bangers 2026" / "Autofahrt.m3u8").exists())   # Ordner bleibt
        self.assertFalse(m3u8.exists())
        # Die naechste Pruefung nimmt den eigenen Namen
        calls = []

        async def fake_run(url, fmt, **kw):
            calls.append(kw)
        keep = main.download.run_download
        main.download.run_download = fake_run
        try:
            self.run_async(main.download._follow_check(f))
        finally:
            main.download.run_download = keep
        self.assertEqual(calls[0]["label"], "Autofahrt")

    def test_verfolgte_in_den_einstellungen_umbenennen_und_zurueck(self):
        self.pl("Liquid Abend")
        f = {"url": "https://www.youtube.com/playlist?list=PLy", "title": "Liquid Abend", "folder": "Liquid Abend",
             "mode": "playlist", "seen": []}
        main._state["followed"] = [f]
        self.assertTrue(main.library._rename_followed(f["url"], "Chill")["ok"])
        self.assertEqual(f["name"], "Chill")
        self.assertTrue((main.PLAYLISTS_DIR / "Chill.m3u").exists())
        # leer = wieder der YouTube-Name
        self.assertTrue(main.library._rename_followed(f["url"], "")["ok"])
        self.assertNotIn("name", f)
        self.assertTrue((main.PLAYLISTS_DIR / "Liquid Abend.m3u").exists())


class PlaylistOrtTest(BackendTest):
    """Playlists liegen im Download-Ordner unter "playlists" und ziehen mit um."""

    def test_alte_playlists_ziehen_in_den_download_ordner(self):
        from synthimix import core, library
        alt = self.tmp / "playlists"
        alt.mkdir(parents=True, exist_ok=True)
        (alt / "Party.m3u").write_text("#EXTM3U\n", encoding="utf-8")
        (alt / "Chill.m3u").write_text("#EXTM3U\n", encoding="utf-8")
        dl = self.tmp / "Downloads"
        dl.mkdir(exist_ok=True)
        self.assertEqual(library.sync_playlists_dir(), 2)
        self.assertEqual(core.PLAYLISTS_DIR, dl / "playlists")
        self.assertEqual({p["name"] for p in library._get_playlists()}, {"Party", "Chill"})
        self.assertFalse((alt / "Party.m3u").exists())
        self.assertEqual(library.sync_playlists_dir(), 0)            # schon dort

    def test_ziehen_mit_wenn_der_download_ordner_wechselt_ohne_zu_ueberschreiben(self):
        from synthimix import core, library
        (self.tmp / "Downloads").mkdir(exist_ok=True)
        library.sync_playlists_dir()
        (core.PLAYLISTS_DIR / "Set.m3u").write_text("alt", encoding="utf-8")
        neu = self.tmp / "Musik"
        (neu / "playlists").mkdir(parents=True)
        (neu / "playlists" / "Set.m3u").write_text("schon da", encoding="utf-8")
        main._state["download_dir"] = str(neu)
        self.assertEqual(library.sync_playlists_dir(), 1)
        self.assertEqual((neu / "playlists" / "Set.m3u").read_text("utf-8"), "schon da")
        self.assertEqual((neu / "playlists" / "Set (2).m3u").read_text("utf-8"), "alt")

    def test_download_ordner_nicht_erreichbar_bleibt_beim_alten_ort(self):
        from synthimix import core, library
        vorher = core.PLAYLISTS_DIR
        blocker = self.tmp / "datei"
        blocker.write_text("x", encoding="utf-8")
        main._state["download_dir"] = str(blocker)                   # kein Ordner: anlegen scheitert
        self.assertEqual(library.sync_playlists_dir(), 0)
        self.assertEqual(core.PLAYLISTS_DIR, vorher)
