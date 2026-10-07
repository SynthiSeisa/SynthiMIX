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
    if "flaky" in url:
        # erster Versuch: Zeitueberschreitung, zweiter klappt
        mark = os.path.join(os.path.dirname(out), "flaky.mark")
        os.makedirs(os.path.dirname(mark), exist_ok=True)
        if not os.path.exists(mark):
            open(mark, "w").close()
            print("ERROR: [youtube] flaky: Unable to download webpage: The read operation timed out", flush=True)
            sys.exit(1)
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

    def test_youtube_mix_erkennen(self):
        mix = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1"
        self.assertEqual(main._mix_id(mix), "RDdQw4w9WgXcQ")
        self.assertEqual(main._mix_id("https://www.youtube.com/watch?v=ABC&list=PL1"), "")
        # playlist?list=RD<ID> liefert nichts — ueber das Startvideo oeffnen
        self.assertEqual(main._mix_watch_url("https://www.youtube.com/playlist?list=RDdQw4w9WgXcQ"),
                         "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ")
        self.assertEqual(main._mix_watch_url(mix), mix)

    def test_mix_ohne_anzahl_gilt_als_playlist(self):
        # yt-dlp meldet beim Mix keine Anzahl ("NA") — frueher wurde dann nur der
        # eine Titel geladen, ohne zu fragen
        import asyncio

        class Proc:
            def __init__(self, out): self.out = out
            async def communicate(self): return self.out.encode(), b""

        async def fake_exec(*args, **kw):
            if "--flat-playlist" in args:
                return Proc("Mix - Rick Astley - Never Gonna Give You Up\nNA\n")
            return Proc("Rick Astley - Never Gonna Give You Up\n")

        keep = main.download.asyncio.create_subprocess_exec
        main.download.asyncio.create_subprocess_exec = fake_exec
        try:
            info = self.run_async(main._playlist_probe(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1"))
            normal = self.run_async(main._playlist_probe("https://www.youtube.com/watch?v=ABC&list=PL1"))
        finally:
            main.download.asyncio.create_subprocess_exec = keep
        self.assertEqual((info["count"], info.get("mix")), (main.download.MIX_MAX, True))
        self.assertEqual(normal["count"], 0)                    # echte Playlist ohne Anzahl: wie bisher
        self.assertNotIn("mix", normal)

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

    def test_loudnorm_nur_beim_umwandeln(self):
        # "ffmpeg:" galt fuer alle ffmpeg-Schritte; der Metadaten-Schritt
        # (-c copy) scheiterte am Filter und Tags und Cover fehlten.
        alt = main._state.get("loudnorm_on_dl", False)
        main._state["loudnorm_on_dl"] = True
        try:
            cmd = main._ytdlp_cmd("https://www.youtube.com/watch?v=ABC", "mp3-best", str(self.tmp))
        finally:
            main._state["loudnorm_on_dl"] = alt
        arg = cmd[cmd.index("--postprocessor-args") + 1]
        self.assertTrue(arg.startswith("ExtractAudio+ffmpeg_o:-af loudnorm="), arg)

    def test_dateiname_ohne_na_wenn_der_kuenstler_fehlt(self):
        # "%(artist)s - %(title)s" ergab bei Videos ohne Kuenstler "NA - Titel":
        # der Vorsatz steht jetzt nur da, wenn es ihn gibt
        alt = main._state.get("dl_filename_format", "title")
        try:
            for fmt, feld in (("artist_title", "artist"), ("uploader_title", "uploader")):
                main._state["dl_filename_format"] = fmt
                cmd = main._ytdlp_cmd("https://www.youtube.com/watch?v=ABC", "mp3-best", str(self.tmp))
                tmpl = cmd[cmd.index("-o") + 1]
                self.assertTrue(tmpl.endswith("%(" + feld + "&{} - |)s%(title)s.%(ext)s"), tmpl)
                self.assertNotIn("%(" + feld + ")s", tmpl)
        finally:
            main._state["dl_filename_format"] = alt

    def test_loudnorm_standardmaessig_aus(self):
        main.SETTINGS_FILE.write_text("{}", "utf-8")
        alt = main._state.get("loudnorm_on_dl", False)
        try:
            main.load_settings()
            self.assertFalse(main._state["loudnorm_on_dl"])
        finally:
            main._state["loudnorm_on_dl"] = alt


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

    def test_voruebergehender_fehler_zweiter_versuch(self):
        # Von Hand nochmal laden klappte meist — jetzt macht SynthiMIX das selbst
        keep = dict(main.download._RETRY_WAIT)
        main.download._RETRY_WAIT.update(net=0.01, other=0.01, rate=0.01)
        try:
            url = "https://www.youtube.com/watch?v=flaky"
            self.assertIsNotNone(self.run_async(main.run_download(url)))
        finally:
            main.download._RETRY_WAIT.clear(); main.download._RETRY_WAIT.update(keep)
        heads = [d for d in main._state["downloads"] if d.get("url") == url and d["id"] == d["session"]]
        self.assertEqual([h["status"] for h in heads], ["done"])           # kein Fehler-Eintrag uebrig

    def test_geloeschte_werden_nicht_wiederholt(self):
        keep = dict(main.download._RETRY_WAIT)
        main.download._RETRY_WAIT.update(net=0.01, other=0.01, rate=0.01)
        calls = []
        orig = main.download.run_download

        async def counting(*a, **kw):
            calls.append(kw.get("attempt", 1))
            return await orig(*a, **kw)
        main.download.run_download = counting
        try:
            self.run_async(counting("https://www.youtube.com/watch?v=fail"))
        finally:
            main.download.run_download = orig
            main.download._RETRY_WAIT.clear(); main.download._RETRY_WAIT.update(keep)
        self.assertEqual(calls, [1])                                        # "Video unavailable" = geloescht

    def test_fehlergruende(self):
        r = main._dl_error_reason
        self.assertEqual(r("Unable to download webpage: The read operation timed out"), "net")
        self.assertEqual(r("unable to download video data: HTTP Error 403: Forbidden"), "net")
        self.assertEqual(r("[youtube] x: Video unavailable"), "gone")
        self.assertEqual(r("HTTP Error 429: Too Many Requests"), "rate")
        self.assertEqual(r("Sign in to confirm you're not a bot"), "bot")

    def test_kommandozeile_mit_pausen_zwischen_versuchen(self):
        cmd = main._ytdlp_cmd("https://www.youtube.com/watch?v=ABC", "mp3-best", str(self.tmp))
        self.assertIn("--retry-sleep", cmd)
        self.assertIn("http:exp=1:8", cmd)

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
