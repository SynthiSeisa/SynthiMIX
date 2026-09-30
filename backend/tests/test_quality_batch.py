"""Sammel-Ersetzen, "Passt so" und die Analyse-Warteschlange."""

from tests.support import BackendTest, FakeWS, main


def _song(title, artist, dur, url="u"):
    return {"url": url, "title": title, "artist": artist, "uploader": artist, "duration": dur, "kind": "song"}


def _video(title, uploader, dur, url="v"):
    return {"url": url, "title": title, "uploader": uploader, "duration": dur, "kind": "video"}


class AutoCandidateTest(BackendTest):

    def test_song_version_vor_upload_und_gleiche_fassung(self):
        lt = {"title": "Avicii - Wake Me Up (Official Video)", "artist": "AviciiVEVO", "duration_sec": 272}
        res = [_video("Avicii - Wake Me Up (Lyrics)", "Lyric Kanal", 247, "lyr"),
               _song("Wake Me Up (Live)", "Avicii", 260, "live"),
               _song("Wake Me Up", "Avicii", 247, "song")]
        cand, sure = main._auto_candidate(lt, res)
        self.assertEqual(cand["url"], "song")
        self.assertTrue(sure)

    def test_remix_bekommt_keinen_originalsong(self):
        lt = {"title": "Meiko - Leave The Lights On (Krot Remix)", "duration_sec": 405}
        cand, _ = main._auto_candidate(lt, [_song("Leave The Lights On", "Meiko", 230)])
        self.assertIsNone(cand)

    def test_ohne_kuenstler_nie_sicher(self):
        lt = {"title": "Miracle", "artist": "", "duration_sec": 220}
        cand, sure = main._auto_candidate(lt, [_song("Miracle", "Cascada", 218)])
        self.assertIsNotNone(cand)
        self.assertFalse(sure)

    def test_deutlich_andere_laenge_wird_nicht_genommen(self):
        lt = {"title": "Vance Joy - Riptide", "duration_sec": 204}
        cand, _ = main._auto_candidate(lt, [_song("Riptide", "Vance Joy", 260)])
        self.assertIsNone(cand)


class QualityBatchTest(BackendTest):

    def test_passt_so_bleibt_beim_laden_erhalten(self):
        self.write_library([{"path": str(self.tmp / "a.mp3"), "title": "A", "quality_ok": True},
                            {"path": str(self.tmp / "b.mp3"), "title": "B", "quality_ok": False}])
        main.load_library()
        by = {t["title"]: t for t in main._state["library"]}
        self.assertTrue(by["A"]["quality_ok"])
        self.assertNotIn("quality_ok", by["B"])

    def test_sammel_ersetzen_nacheinander_und_abbrechbar(self):
        main._state["library"] = [{"path": f"p{i}", "title": f"T{i}"} for i in range(3)]
        seen = []

        async def fake_replace(path, url, ws):
            seen.append(path)
            if path == "p1":
                main._qbatch_cancel = True          # waehrend Titel 2 wird abgebrochen
            await ws.send_text('{"type": "quality_replace_status", "path": "%s", "state": "done", "text": "ok"}' % path)

        alt = main._quality_replace
        main._quality_replace = fake_replace
        ws = FakeWS()
        try:
            self.run_async(main._quality_batch_replace(
                [{"path": f"p{i}", "url": "u"} for i in range(3)], ws))
        finally:
            main._quality_replace = alt
        self.assertEqual(seen, ["p0", "p1"])
        done = ws.of_type("quality_batch_replaced")[0]
        self.assertEqual(done["ok"], 2)
        self.assertTrue(done["cancelled"])


class AnalyzeQueueTest(BackendTest):

    def test_auftrag_waehrend_analyse_wird_eingereiht(self):
        sent = []

        async def bc(msg):
            sent.append(msg)

        alt = main.broadcast
        main.broadcast = bc
        main._analyze_running = True
        main._analyze_pending.clear()
        try:
            self.run_async(main._analyze_library_meta_task(["x.mp3"]))
        finally:
            main.broadcast = alt
            main._analyze_running = False
        self.assertEqual(main._analyze_pending, [["x.mp3"]])
        self.assertIn("eingereiht", sent[-1]["text"])
        main._analyze_pending.clear()
