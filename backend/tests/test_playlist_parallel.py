"""Playlist-Download: parallele yt-dlp-Prozesse, Ordner mit Playlist-Namen.

yt-dlp wird durch ein kleines Skript ersetzt, das dieselben Zeilen ausgibt
und leere Dateien anlegt — es wird nichts aus dem Netz geladen.
"""
import sys
import textwrap

from tests.support import BackendTest, main

FAKE = textwrap.dedent(r'''
    import sys, os, time, re
    args = sys.argv[1:]
    out = args[args.index("-o") + 1]
    urls = [l.strip() for l in open(args[args.index("-a") + 1], encoding="utf-8") if l.strip()]
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
        log.write(f"start {os.getpid()} {len(urls)}\n")
    for u in urls:
        vid = re.search(r"v=([\w-]{11})", u).group(1)
        print(f"[youtube] Extracting URL: {u}", flush=True)
        if vid.startswith("kaputt"):
            print(f"ERROR: [youtube] {vid}: Video unavailable", flush=True)
            continue
        src = out.replace("%(title)s", "Titel " + vid).replace("%(ext)s", "webm")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        print(f"[download] Destination: {src}", flush=True)
        for p in (10, 50, 100):
            print(f"[download]  {p}.0% of 3.00MiB at 1.00MiB/s ETA 00:01", flush=True)
            time.sleep(0.02)
        mp3 = src[:-4] + "mp3"
        open(mp3, "wb").write(b"ID3")
        print(f"[ExtractAudio] Destination: {mp3}", flush=True)
    sys.exit(1 if any("kaputt" in u for u in urls) else 0)
''')


class PlaylistParallelTest(BackendTest):

    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake_ytdlp.py"
        self.fake.write_text(FAKE, "utf-8")
        bat = self.tmp / "yt-dlp.bat"
        bat.write_text(f'@"{sys.executable}" "{self.fake}" %*\n', "utf-8")
        self.log = self.tmp / "fake.log"
        import os
        os.environ["FAKE_LOG"] = str(self.log)
        self._saved_tools = (main.YTDLP, main._JS_ARGS, main._audit_playlist_for_videos, main.FFMPEG_DIR, main._ytm_songs)
        self.ersatz = []          # Antwort der (ersetzten) Song-Suche

        async def songs(query, n=8, details=True):
            return list(self.ersatz)
        main._ytm_songs = songs
        main.YTDLP, main._JS_ARGS, main.FFMPEG_DIR = str(bat), [], ""
        main._state["download_dir"] = str(self.tmp / "Downloads")
        main._state["playlist_folder_enabled"] = True
        main._state["downloads"] = []
        main._state["history"] = []
        main._state["library"] = []

    def tearDown(self):
        main.YTDLP, main._JS_ARGS, main._audit_playlist_for_videos, main.FFMPEG_DIR, main._ytm_songs = self._saved_tools
        super().tearDown()

    def fake_audit(self, ids, title):
        async def audit(url):
            return ([{"url": f"https://www.youtube.com/watch?v={i}", "title": f"Video {i}",
                      "replaced": False, "uploader": "", "duration": 200} for i in ids], title)
        main._audit_playlist_for_videos = audit

    def test_parallel_und_ordnername(self):
        ids = [f"id{n:09d}" for n in range(7)]
        self.fake_audit(ids, 'Party: Mix/2026?')
        final = self.run_async(main.run_download("https://www.youtube.com/playlist?list=PLtest", "mp3-best"))
        folder = self.tmp / "Downloads" / "Party_ Mix_2026_"
        self.assertTrue(folder.is_dir(), list((self.tmp / "Downloads").iterdir()))
        self.assertEqual(len(list(folder.glob("*.mp3"))), 7)
        hdr = main._state["downloads"][0]
        self.assertEqual(hdr["session_label"], "Party: Mix/2026?")
        self.assertIn("7 Tracks", hdr["status_text"])
        tracks = [d for d in main._state["downloads"] if d["id"] != hdr["id"]]
        self.assertEqual(len(tracks), 7)
        self.assertTrue(all(t["status"] == "done" for t in tracks))
        # Jeder Eintrag hat seine eigene Datei (Zuordnung ueber die Video-ID)
        self.assertEqual(sorted(t["path"].rsplit("Titel ", 1)[1][:11] for t in tracks), ids)
        self.assertEqual(len(main._state["history"]), 7)
        starts = [l for l in self.log.read_text("utf-8").splitlines() if l.startswith("start")]
        self.assertEqual(len(starts), main._DL_PARALLEL)
        self.assertTrue(final and final.endswith(".mp3"))

    def test_nicht_verfuegbar(self):
        self.fake_audit(["idaaaaaaaaa", "kaputt00000", "idbbbbbbbbb"], "Liste")
        self.run_async(main.run_download("https://www.youtube.com/playlist?list=PLtest2", "mp3-best"))
        tracks = [d for d in main._state["downloads"] if d["id"] != main._state["downloads"][0]["id"]]
        by = {t["title"][:12]: t["status"] for t in tracks}
        self.assertEqual(sorted(by.values()), ["done", "done", "error"])
        err = [t for t in tracks if t["status"] == "error"][0]
        self.assertEqual(err["reason"], "gone")
        self.assertEqual(err["status_text"], "Gelöscht oder privat")
        self.assertEqual(main._state["downloads"][0]["failed_n"], 1)

    def test_ersatz_wird_geladen(self):
        # Gesperrt/geloescht: eine andere Version desselben Songs wird genommen
        self.ersatz = [{"url": "https://music.youtube.com/watch?v=ersatz00001", "title": "Song", "duration": 200}]
        self.fake_audit(["idaaaaaaaaa", "kaputt00000"], "Liste")
        self.run_async(main.run_download("https://www.youtube.com/playlist?list=PLtest3", "mp3-best"))
        hdr = main._state["downloads"][0]
        tracks = [d for d in main._state["downloads"] if d["id"] != hdr["id"]]
        self.assertEqual(len(tracks), 2)
        self.assertTrue(all(t["status"] == "done" for t in tracks))
        self.assertIn("✓ Ersatz geladen", [t["status_text"] for t in tracks])
        self.assertIn("1 ersetzt", hdr["status_text"])

    def test_fehlgeschlagene_nochmal(self):
        import asyncio
        from tests.support import FakeWS
        self.fake_audit(["idaaaaaaaaa", "kaputt00000"], "Liste")

        async def ablauf():
            await main.run_download("https://www.youtube.com/playlist?list=PLtest4", "mp3-best")
            sid = main._state["downloads"][0]["id"]
            main._audit_playlist_for_videos = None          # darf nicht gebraucht werden
            await main.handle_message(FakeWS(), {"type": "download_retry_failed", "session_id": sid})
            for _ in range(200):
                neu = [d for d in main._state["downloads"] if str(d.get("session_label", "")).startswith("Nochmal")]
                if neu and neu[0].get("status") == "done":
                    return sid, neu[0]
                await asyncio.sleep(0.05)
            return sid, None

        sid, neu = self.run_async(ablauf())
        self.assertIsNotNone(neu)
        self.assertEqual(neu["folder"], "Liste")                 # gleicher Ordner wie vorher
        alt = [d for d in main._state["downloads"] if d.get("session") == sid and d["id"] != sid]
        self.assertEqual([d["status"] for d in alt], ["done"])  # Fehlgeschlagener ist umgezogen
        self.assertIn("1 nicht verfügbar", neu["status_text"])  # Attrappe: bleibt kaputt
