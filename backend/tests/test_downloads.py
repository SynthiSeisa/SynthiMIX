"""Downloads: Links, Kommandozeile und vor allem die Frage, ob ein Download
wirklich geklappt hat.

run_download wird dafuer mit einem nachgebauten yt-dlp betrieben, das je nach
Link Erfolg, Fehlschlag oder einen Teilerfolg spielt. So laeuft der echte
Code-Pfad, ohne Netz und ohne YouTube.
"""
import sys
import textwrap

from tests.support import BackendTest, main

FAKE_YTDLP = textwrap.dedent('''
    import os, sys
    args = sys.argv[1:]
    url = args[-1]
    out = args[args.index("-o") + 1]
    if "fail" in url:
        print("ERROR: [youtube] fail: Video unavailable", flush=True)
        sys.exit(1)
    path = out.replace("%(title)s", "Fake Titel").replace("%(ext)s", "mp3")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\\xff\\xfb" + b"\\0" * 2000)
    print("[ExtractAudio] Destination: " + path, flush=True)
    if "partial" in url:
        print("ERROR: Postprocessing: embedding thumbnail failed", flush=True)
        sys.exit(1)
''')


class UrlTest(BackendTest):

    def test_titel_mit_playlist_erkennen(self):
        ja = ["https://www.youtube.com/watch?v=ABC&list=PL1&index=7",
              "https://youtu.be/ABC?si=x&list=PL1",
              "https://music.youtube.com/watch?v=ABC&list=RDAMVM1"]
        nein = ["https://www.youtube.com/playlist?list=PL1",
                "https://www.youtube.com/watch?v=ABC", "Songname"]
        for u in ja:
            with self.subTest(u=u):
                self.assertTrue(main._is_mixed_playlist_url(u))
        for u in nein:
            with self.subTest(u=u):
                self.assertFalse(main._is_mixed_playlist_url(u))

    def test_playlist_teile_entfernen(self):
        self.assertEqual(main._strip_playlist_params(
            "https://www.youtube.com/watch?v=ABC&list=PL1&index=7&start_radio=1"),
            "https://www.youtube.com/watch?v=ABC")
        self.assertEqual(main._strip_playlist_params("https://youtu.be/ABC?si=xy&list=PL1"),
                         "https://youtu.be/ABC?si=xy")

    def test_kommandozeile_einzeltitel_und_playlist(self):
        einzel = main._ytdlp_cmd("https://www.youtube.com/watch?v=ABC", "mp3-best", str(self.tmp))
        liste = main._ytdlp_cmd("https://www.youtube.com/playlist?list=PL1", "mp3-best",
                                str(self.tmp), playlist_folder="Liste")
        self.assertIn("--no-playlist", einzel)
        self.assertIn("--yes-playlist", liste)
        for arg in main._JS_ARGS:
            self.assertIn(arg, einzel)


class RunDownloadTest(BackendTest):

    def setUp(self):
        super().setUp()
        fake = self.tmp / "fake_ytdlp.py"
        fake.write_text(FAKE_YTDLP, "utf-8")
        self._ytdlp, self._js = main.YTDLP, main._JS_ARGS
        # [python, fake_ytdlp.py, -x, ...] — ohne Shell, damit & und % im Link egal sind
        main.YTDLP, main._JS_ARGS = sys.executable, [str(fake)]

    def tearDown(self):
        main.YTDLP, main._JS_ARGS = self._ytdlp, self._js
        super().tearDown()

    def _header(self, url):
        return next(d for d in main._state["downloads"] if d.get("url") == url and d["id"] == d["session"])

    def test_fehlschlag_wird_nicht_als_fertig_gemeldet(self):
        # yt-dlp endet dabei mit Code 1 — das galt frueher als Erfolg
        url = "https://www.youtube.com/watch?v=fail"
        self.assertIsNone(self.run_async(main.run_download(url)))
        h = self._header(url)
        self.assertEqual(h["status"], "error")
        self.assertIn("Video unavailable", h["error_msg"])

    def test_erfolg_landet_in_der_bibliothek(self):
        # Einzeldownloads liefen frueher nie durch _auto_add_to_library
        url = "https://www.youtube.com/watch?v=ok"
        pfad = self.run_async(main.run_download(url))
        self.assertIsNotNone(pfad)
        self.assertEqual(self._header(url)["status"], "done")
        self.assertIn(pfad, [lt["path"] for lt in main._state["library"]])

    def test_teilerfolg_einzeltitel_ohne_fehlertext(self):
        url = "https://www.youtube.com/watch?v=partial"
        self.assertIsNotNone(self.run_async(main.run_download(url)))
        h = self._header(url)
        self.assertEqual(h["status"], "done")
        self.assertEqual(h["error_msg"], "")


class ScanFoldersTest(BackendTest):

    def test_download_ordner_wird_mitgescannt(self):
        dl = self.tmp / "Musik" / "Downloads"
        andere = self.tmp / "Sammlung"
        dl.mkdir(parents=True); andere.mkdir()
        main._state["download_dir"] = str(dl)

        main._state["watched_folders"] = []
        self.assertEqual(main._scan_folders(), [str(dl)])

        main._state["watched_folders"] = [str(andere)]
        self.assertEqual(main._scan_folders(), [str(andere), str(dl)])

        main._state["watched_folders"] = [str(dl)]
        self.assertEqual(main._scan_folders(), [str(dl)])

        # Unterordner eines rekursiv durchsuchten Ordners nicht doppelt
        main._state["watched_folders"] = [str(self.tmp / "Musik")]
        self.assertEqual(main._scan_folders(), [str(self.tmp / "Musik")])
        main._state["scan_recursive"] = False
        self.assertEqual(main._scan_folders(), [str(self.tmp / "Musik"), str(dl)])
