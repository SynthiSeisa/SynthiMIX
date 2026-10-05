"""Einlesen grosser Ordner (parallel, in Portionen, unveraenderte ueberspringen)
und tragbarer Betrieb (Laufwerksbuchstabe aendert sich von PC zu PC)."""
import os

from tests.support import BackendTest, main
from synthimix import core, library, media, relocate


class ScanTest(BackendTest):

    def make_tree(self, n_dirs=5, per_dir=30):
        root = self.tmp / "Musik"
        for d in range(n_dirs):
            sub = root / f"Ordner {d}" / "tief"
            sub.mkdir(parents=True)
            for k in range(per_dir):
                (sub / f"Titel {d}-{k}.mp3").write_bytes(b"kein echtes mp3")
        return root

    def test_alle_unterordner_werden_eingelesen(self):
        root = self.make_tree()                         # 150 Dateien, mehr als eine Portion
        main._state["library"] = []
        self.run_async(library.scan_folder(str(root)))
        paths = {t["path"] for t in main._state["library"]}
        self.assertEqual(len(paths), 150)
        self.assertEqual(len(main._state["library"]), 150)     # nichts doppelt
        self.assertEqual({os.path.basename(os.path.dirname(os.path.dirname(p))) for p in paths},
                         {f"Ordner {d}" for d in range(5)})

    def test_unveraenderte_dateien_werden_nicht_nochmal_geprueft(self):
        root = self.make_tree(2, 10)
        main._state["library"] = []
        self.run_async(library.scan_folder(str(root)))
        calls = []
        echt = media._probe_sync
        media._probe_sync = lambda p: (calls.append(p), echt(p))[1]
        try:
            neu = root / "Ordner 0" / "tief" / "dazu.mp3"
            neu.write_bytes(b"x")
            self.run_async(library.scan_folder(str(root)))
        finally:
            media._probe_sync = echt
        self.assertEqual(calls, [str(neu)])                     # nur die neue Datei
        self.assertEqual(len(main._state["library"]), 21)

    def test_geloeschte_datei_wird_als_fehlend_markiert_und_kommt_wieder(self):
        root = self.make_tree(1, 3)
        main._state["library"] = []
        self.run_async(library.scan_folder(str(root)))
        weg = root / "Ordner 0" / "tief" / "Titel 0-1.mp3"
        weg.unlink()
        self.run_async(library.scan_folder(str(root)))
        lt = next(t for t in main._state["library"] if t["path"] == str(weg))
        self.assertTrue(lt.get("missing"))
        weg.write_bytes(b"kein echtes mp3")
        os.utime(weg, (lt["mtime"], lt["mtime"]))
        self.run_async(library.scan_folder(str(root)))
        self.assertFalse(lt.get("missing", False))

    def test_fortschritt_bei_vielen_dateien(self):
        root = self.make_tree(3, 40)
        main._state["library"] = []
        texts = []
        echt = core.broadcast

        async def mitschreiben(msg):
            if msg.get("type") == "scan_status":
                texts.append(msg.get("text", ""))
        core.broadcast = mitschreiben
        try:
            self.run_async(library.scan_folder(str(root)))
        finally:
            core.broadcast = echt
        self.assertTrue(any("/ 120" in t for t in texts), texts)
        self.assertTrue(any(t.startswith("Fertig") and "+120 neu" in t for t in texts), texts)


class PortableTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._portable = core.PORTABLE
        main._state["library"] = [{"path": r"Q:\Musik\a.mp3", "title": "a"}, {"path": r"C:\Fest\b.mp3", "title": "b"}]
        main._state["watched_folders"] = [r"Q:\Musik"]
        main._state["download_dir"] = r"Q:\Musik\Neu"
        main._state["queue"] = [{"path": r"Q:\Musik\a.mp3", "title": "a"}]

    def tearDown(self):
        core.PORTABLE = self._portable
        main._state.pop("portable_drive", None)
        super().tearDown()

    def test_ohne_tragbaren_betrieb_passiert_nichts(self):
        core.PORTABLE = False
        main._state["portable_drive"] = "Q:"
        self.assertIsNone(relocate.portable_start("E:"))
        self.assertEqual(main._state["library"][0]["path"], r"Q:\Musik\a.mp3")

    def test_erster_start_merkt_sich_das_laufwerk(self):
        core.PORTABLE = True
        main._state["portable_drive"] = ""
        self.assertIsNone(relocate.portable_start("Q:"))
        self.assertEqual(main._state["portable_drive"], "Q:")
        self.assertEqual(main._state["library"][0]["path"], r"Q:\Musik\a.mp3")

    def test_anderer_buchstabe_stellt_alle_pfade_um(self):
        core.PORTABLE = True
        main._state["portable_drive"] = "Q:"
        res = relocate.portable_start("e:")
        self.assertIsNotNone(res)
        self.assertEqual(main._state["portable_drive"], "E:")
        self.assertEqual(main._state["library"][0]["path"], r"E:\Musik\a.mp3")
        self.assertEqual(main._state["library"][1]["path"], r"C:\Fest\b.mp3")     # anderes Laufwerk bleibt
        self.assertEqual(main._state["watched_folders"], [r"E:\Musik"])
        self.assertEqual(main._state["download_dir"], r"E:\Musik\Neu")
        self.assertEqual(main._state["queue"][0]["path"], r"E:\Musik\a.mp3")
        # bleibt nach dem Neustart
        main.store.load_settings()
        self.assertEqual(main._state["portable_drive"], "E:")
        # gleicher Buchstabe: nichts zu tun
        self.assertIsNone(relocate.portable_start("E:"))


class PortablePlaylistTest(BackendTest):

    def test_playlists_im_download_ordner_bekommen_die_neuen_pfade(self):
        """Die Platte heisst an diesem PC anders: die Playlists liegen im
        Download-Ordner auf der Platte und muessen dort umgeschrieben werden."""
        alt, neu = self.tmp / "PlatteAlt", self.tmp / "PlatteNeu"
        (neu / "Musik" / "playlists").mkdir(parents=True)
        (neu / "Musik" / "a.mp3").write_bytes(b"x")
        pl = neu / "Musik" / "playlists" / "Set.m3u"
        pl.write_text("\n".join(["#EXTM3U", "#EXTINF:200,A", str(alt / "Musik" / "a.mp3"), ""]), encoding="utf-8")
        main._state["library"] = [{"path": str(alt / "Musik" / "a.mp3"), "title": "a"}]
        main._state["download_dir"] = str(alt / "Musik")
        main._state["watched_folders"] = [str(alt / "Musik")]
        res = relocate.apply(str(alt) + os.sep, str(neu) + os.sep)
        self.assertEqual(core.PLAYLISTS_DIR, neu / "Musik" / "playlists")
        self.assertEqual(res["playlists"], 1)
        self.assertIn(str(neu / "Musik" / "a.mp3"), pl.read_text(encoding="utf-8"))
        self.assertNotIn(str(alt), pl.read_text(encoding="utf-8"))


class DiagnoseTest(BackendTest):

    def test_bericht_nennt_was_nicht_geht(self):
        from synthimix import diagnose
        main._state["library"] = [{"path": str(self.tmp / "fehlt.mp3"), "title": "x", "missing": True}]
        echt = core.FFMPEG
        core.FFMPEG = str(self.tmp / "gibt-es-nicht.exe")
        try:
            r = diagnose.run_sync()
        finally:
            core.FFMPEG = echt
        by = {c["name"]: c for c in r["checks"]}
        self.assertFalse(by["ffmpeg"]["ok"])
        self.assertIn("nicht gefunden", by["ffmpeg"]["text"])
        self.assertTrue(by["Datenordner"]["ok"])
        self.assertFalse(by["Waveform-Test"]["ok"])              # kein Titel da, mit dem man testen koennte
        self.assertFalse(r["ok"])
        self.assertIn("[FEHLER] ffmpeg", r["report"])
        self.assertIn("1 Titel, 1 fehlen", r["report"])
