"""YouTube-Music-Suche: Songs zuerst, YouTube dahinter, Details kommen nach.

"ytmsearch" gibt es in yt-dlp nicht — jede Suche fiel bis 1.5 still auf die
normale YouTube-Suche zurueck. Hier ohne Netz: die yt-dlp-Aufrufe werden
ersetzt, geprueft werden Reihenfolge, Doppelte und der zweistufige Versand.
"""
from tests.support import BackendTest, FakeWS, main


def _song(vid, title):
    return {"url": f"https://music.youtube.com/watch?v={vid}", "title": title, "uploader": "",
            "duration": 0, "kind": "song", "artist": "", "_score": 100}


def _video(vid, title, dur=200):
    return {"url": f"https://www.youtube.com/watch?v={vid}", "title": title, "uploader": "Kanal",
            "duration": dur, "_score": 0}


class YtmSearchTest(BackendTest):

    def test_kein_ytmsearch_mehr(self):
        src = (main.Path(main.__file__)).read_text("utf-8")
        aufrufe = [z for z in src.splitlines() if 'f"ytmsearch' in z or "'ytmsearch" in z]
        self.assertEqual(aufrufe, [])

    def test_video_id(self):
        self.assertEqual(main._video_id("https://music.youtube.com/watch?v=2NiyrtYegso"), "2NiyrtYegso")
        self.assertEqual(main._video_id("https://youtu.be/2NiyrtYegso?si=x"), "2NiyrtYegso")
        self.assertEqual(main._video_id("https://soundcloud.com/a/b"), "")

    def test_songs_zuerst_ohne_doppelte(self):
        songs = [_song("AAAAAAA", "Wake Me Up")]
        videos = [_video("AAAAAAA", "Avicii - Wake Me Up"), _video("BBBBBBB", "Wake Me Up (DnB Bootleg)")]
        merged = main._merge_songs_first(songs, videos)
        self.assertEqual([main._video_id(r["url"]) for r in merged], ["AAAAAAA", "BBBBBBB"])
        self.assertEqual([r["kind"] for r in merged], ["song", "video"])

    def test_download_per_songname_nutzt_song_suche(self):
        cmd = main._ytdlp_cmd("Avicii Wake Me Up", "mp3-best", str(self.tmp))
        self.assertIn("https://music.youtube.com/search?q=Avicii+Wake+Me+Up#songs", cmd)
        self.assertEqual(cmd[cmd.index("--playlist-items") + 1], "1")

    def _mit_ersatz(self, songs, videos, fn):
        async def ytm(query, n=8, details=True):
            return [dict(s) for s in songs]

        async def yt(cmd):
            return [dict(v) for v in videos]

        async def fill(rs):
            for r in rs:
                r["artist"] = r["uploader"] = "Avicii"
                r["duration"] = 247
            return rs

        alt = (main._ytm_songs, main._run_search_cmd, main._ytm_fill_details)
        main._ytm_songs, main._run_search_cmd, main._ytm_fill_details = ytm, yt, fill
        try:
            ws = FakeWS()
            self.run_async(fn(ws))
            return ws
        finally:
            main._ytm_songs, main._run_search_cmd, main._ytm_fill_details = alt

    def test_downloads_suche_zweistufig(self):
        ws = self._mit_ersatz([_song("AAAAAAA", "Wake Me Up")],
                              [_video("CCCCCCC", "Avicii - Wake Me Up (Official Video)"),
                               _video("BBBBBBB", "Wake Me Up (DnB Bootleg)")],
                              lambda ws: main.do_search("wake me up", ws))
        erst, dann = ws.of_type("search_results")
        self.assertFalse(erst["final"])
        self.assertTrue(dann["final"])
        self.assertEqual(dann["results"][0]["uploader"], "Avicii")
        self.assertEqual(dann["results"][0]["duration"], 247)
        # Musikvideo raus, Bootleg (gibt es nur auf YouTube) bleibt
        self.assertEqual([r["title"] for r in dann["results"]], ["Wake Me Up", "Wake Me Up (DnB Bootleg)"])
        self.assertFalse(any(k.startswith("_") for r in dann["results"] for k in r))

    def test_ohne_songs_nur_eine_antwort(self):
        ws = self._mit_ersatz([], [_video("BBBBBBB", "Wake Me Up (DnB Bootleg)")],
                              lambda ws: main.do_search("wake me up", ws))
        self.assertEqual(len(ws.of_type("search_results")), 1)
        self.assertTrue(ws.of_type("search_results")[0]["final"])

    def test_wunsch_zeigt_kuenstler_und_titel(self):
        ws = self._mit_ersatz([_song("AAAAAAA", "Wake Me Up")], [],
                              lambda ws: main._do_wish_search("zzqqxx wake", ws))
        letzte = ws.of_type("wish_results")[-1]
        self.assertTrue(letzte["final"])
        self.assertEqual(letzte["results"][0]["title"], "Avicii - Wake Me Up")
