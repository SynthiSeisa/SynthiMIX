"""SoundCloud: ein Set (Playlist, Album, EP) wird wie eine Playlist behandelt —
vorher galt der Link als einzelner Titel."""
import json

from tests.support import BackendTest, main
from synthimix import download

SET = "https://soundcloud.com/ukf/sets/rova-undisputed"


class FakeProc:
    """Steht fuer yt-dlp: gibt die Zeilen aus und merkt sich den Aufruf."""
    calls = []

    def __init__(self, lines):
        self._lines = [json.dumps(x).encode() + b"\n" for x in lines]
        self.returncode = 0

    @property
    def stdout(self):
        async def gen():
            for ln in self._lines:
                yield ln
        return gen()

    async def wait(self):
        return 0


class SoundcloudTest(BackendTest):

    def test_links_erkennen(self):
        self.assertTrue(download._is_playlist(SET))
        self.assertTrue(download._is_playlist(SET + "?si=abc"))
        self.assertFalse(download._is_playlist("https://soundcloud.com/ukf/rova-octane"))
        # ein Titel, der aus einem Set heraus geteilt wurde, bleibt ein Titel
        self.assertFalse(download._is_playlist("https://soundcloud.com/ukf/rova-octane?in=ukf/sets/rova-undisputed"))
        self.assertEqual(download._vid_of("https://soundcloud.com/UKF/rova-octane"), "sc:ukf/rova-octane")
        self.assertEqual(download._vid_of("[soundcloud] Extracting URL: https://soundcloud.com/ukf/rova-octane"),
                         "sc:ukf/rova-octane")
        self.assertEqual(download._vid_of(SET), "")
        self.assertEqual(download._vid_of("https://music.youtube.com/watch?v=_5wWXH7SvA8"), "_5wWXH7SvA8")

    def test_set_wird_mit_titel_und_laenge_gelesen_und_nicht_ersetzt(self):
        echt = download.asyncio.create_subprocess_exec
        gesucht = []
        echt_suche = main._find_song_version

        async def fake_exec(*cmd, **kw):
            FakeProc.calls.append(cmd)
            return FakeProc([
                {"webpage_url": "https://soundcloud.com/ukf/rova-octane", "url": "https://cf-media.sndcdn.com/x.mp3",
                 "title": "Rova - Octane", "uploader": "UKF", "duration": 198.9, "playlist_title": "Rova - UNDISPUTED"},
                {"webpage_url": "https://soundcloud.com/ukf/rova-think-i-am", "title": "Rova - Think I Am",
                 "uploader": "UKF", "duration": 176.2, "playlist_title": "Rova - UNDISPUTED"}])

        async def suche(video):
            gesucht.append(video)
        download.asyncio.create_subprocess_exec = fake_exec
        main._find_song_version = suche
        try:
            entries, title = self.run_async(download._audit_playlist_for_videos(SET))
        finally:
            download.asyncio.create_subprocess_exec = echt
            main._find_song_version = echt_suche
        self.assertEqual(title, "Rova - UNDISPUTED")
        self.assertEqual([(e["url"], e["title"]) for e in entries],
                         [("https://soundcloud.com/ukf/rova-octane", "Rova - Octane"),
                          ("https://soundcloud.com/ukf/rova-think-i-am", "Rova - Think I Am")])
        # volle Abfrage statt flacher Liste (die nennt bei SoundCloud keine Titel)
        self.assertNotIn("--flat-playlist", FakeProc.calls[-1])
        self.assertIn("--skip-download", FakeProc.calls[-1])
        # SoundCloud bleibt SoundCloud: keine Suche nach einer Song-Version
        self.assertEqual(gesucht, [])

    def test_kopierschutz_hat_einen_eigenen_grund(self):
        self.assertEqual(download._dl_error_reason("ERROR: [soundcloud] 1236089494: This video is DRM protected"), "drm")
        self.assertIn("Kopiergeschützt", download._DL_REASON_TEXT["drm"])
