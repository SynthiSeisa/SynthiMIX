"""Einzelne Videolinks: Musikvideo erkennen und die Song-Version anbieten.

Ohne Netz: yt-dlp-Abfrage und YouTube-Music-Suche werden ersetzt, damit nur
die Entscheidung geprueft wird — wann gefragt wird und welche Version passt.
"""
import asyncio

from tests.support import BackendTest, FakeWS, main

VIDEO = {"title": "girl in red - Serotonin (Official Video)", "uploader": "girl in red", "duration": 205}
SONG = {"url": "https://www.youtube.com/watch?v=SONG", "title": "Serotonin",
        "artist": "girl in red", "uploader": "girl in red", "duration": 179}


class VideoChoiceTest(BackendTest):

    def test_nur_einzelne_youtube_videos_pruefen(self):
        ja = ["https://www.youtube.com/watch?v=ABC", "https://youtu.be/ABC?si=x"]
        nein = ["https://www.youtube.com/playlist?list=PL1", "https://music.youtube.com/watch?v=ABC",
                "https://soundcloud.com/a/b", "Songname"]
        for u in ja:
            with self.subTest(u=u):
                self.assertTrue(main._is_single_youtube_video(u))
        for u in nein:
            with self.subTest(u=u):
                self.assertFalse(main._is_single_youtube_video(u))

    def test_suchbegriff_ohne_videozusaetze_aber_mit_remix(self):
        self.assertEqual(main._song_query("girl in red - Serotonin (Official Video)"), "girl in red - Serotonin")
        self.assertEqual(main._song_query("Meiko - Leave The Lights On (Krot Remix) [Official Music Video]"),
                         "Meiko - Leave The Lights On (Krot Remix)")

    def test_song_version_waehlen(self):
        andere = [
            {"url": "u1", "title": "Serotonin (Live)", "artist": "girl in red", "duration": 190},
            {"url": "u2", "title": "Serotonin", "artist": "Andere Band", "duration": 180},
            {"url": "u3", "title": "Völlig anderer Song", "artist": "girl in red", "duration": 180},
            SONG,
        ]
        self.assertEqual(main._pick_song_version(VIDEO, andere)["url"], SONG["url"])

    def test_remix_video_bekommt_nicht_das_original(self):
        video = {"title": "Meiko - Leave The Lights On (Krot Remix) (Official Video)", "uploader": "x", "duration": 420}
        original = {"url": "o", "title": "Leave The Lights On", "artist": "Meiko", "duration": 230}
        remix = {"url": "r", "title": "Leave The Lights On (Krot Remix)", "artist": "Meiko", "duration": 405}
        self.assertIsNone(main._pick_song_version(video, [original]))
        self.assertEqual(main._pick_song_version(video, [original, remix])["url"], "r")

    def test_wann_gefragt_wird(self):
        self.assertTrue(main._needs_video_choice(VIDEO, SONG))                 # "Official Video"
        self.assertFalse(main._needs_video_choice(VIDEO, None))                # keine Song-Version
        gleich_lang = {"title": "girl in red - Serotonin", "uploader": "girl in red", "duration": 180}
        self.assertFalse(main._needs_video_choice(gleich_lang, SONG))          # reiner Upload, passt
        mit_intro = dict(gleich_lang, duration=200)
        self.assertTrue(main._needs_video_choice(mit_intro, SONG))             # 21 s laenger
        lyrics = dict(mit_intro, title="girl in red - Serotonin (Lyrics)")
        self.assertFalse(main._needs_video_choice(lyrics, SONG))

    def _ablauf(self, video, results):
        ws, gestartet = FakeWS(), []

        async def probe(url):
            return dict(video, url=url)

        async def search(query, n=3):
            return results

        async def download(url, fmt="mp3-best"):
            gestartet.append(url)

        alt = (main._probe_video, main._ytm_song_search, main.run_download)
        main._probe_video, main._ytm_song_search, main.run_download = probe, search, download
        try:
            async def los():
                await main._check_video_then_download("https://www.youtube.com/watch?v=VID", "mp3-best", ws)
                await asyncio.sleep(0)
            self.run_async(los())
        finally:
            main._probe_video, main._ytm_song_search, main.run_download = alt
        return ws, gestartet

    def test_musikvideo_fragt_nach_statt_zu_laden(self):
        ws, gestartet = self._ablauf(VIDEO, [SONG])
        self.assertEqual(gestartet, [])
        frage = ws.of_type("video_choice")
        self.assertEqual(len(frage), 1)
        self.assertEqual(frage[0]["song"]["url"], SONG["url"])
        self.assertEqual(len(ws.of_type("video_check_pending")), 1)
        self.assertEqual(len(ws.of_type("video_check_done")), 1)

    def test_ohne_song_version_wird_einfach_geladen(self):
        ws, gestartet = self._ablauf(VIDEO, [])
        self.assertEqual(gestartet, ["https://www.youtube.com/watch?v=VID"])
        self.assertEqual(ws.of_type("video_choice"), [])
