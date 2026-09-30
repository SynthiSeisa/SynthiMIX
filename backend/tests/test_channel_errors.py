"""Kanal-Link: der echte Grund statt "keine Playlists gefunden", zweiter Versuch ohne JS-Laufzeit."""
import json

from tests.support import BackendTest
from synthimix import channels, core


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, t):
        self.sent.append(json.loads(t))


class ChannelErrors(BackendTest):

    def test_grund_wird_uebersetzt(self):
        f = channels._channel_error_text
        self.assertIn("Anmeldung", f("ERROR: [youtube:tab] @x: Sign in to confirm you're not a bot"))
        self.assertEqual(f("ERROR: [youtube:tab] @gibtsnicht: This channel does not exist."), "Kanal nicht gefunden — Link prüfen")
        self.assertIn("nicht gefunden", f("ERROR: [youtube:tab] @x/playlists: Unable to download API page: HTTP Error 404: Not Found"))
        self.assertIn("yt-dlp", f("[WinError 2] Das System kann die angegebene Datei nicht finden"))
        self.assertEqual(f("ERROR: [youtube:tab] @x: Something odd"), "Something odd")

    def test_plan_zeigt_den_grund(self):
        keep = channels._channel_playlists
        async def boom(base):
            raise channels.ChannelReadError("ERROR: [youtube:tab] @x: HTTP Error 429: Too Many Requests")
        channels._channel_playlists = boom
        try:
            ws = FakeWS()
            self.run_async(channels._plan_channel("https://www.youtube.com/@x", "mp3-best", ws))
        finally:
            channels._channel_playlists = keep
        err = next(m for m in ws.sent if m["type"] == "channel_plan")["error"]
        self.assertIn("ließ sich nicht lesen", err)
        self.assertIn("bremst", err)

    def test_leerer_kanal_eigene_meldung(self):
        keep = channels._channel_playlists
        async def leer(base):
            return "Kanal", []
        channels._channel_playlists = leer
        try:
            ws = FakeWS()
            self.run_async(channels._plan_channel("https://www.youtube.com/@x", "mp3-best", ws))
        finally:
            channels._channel_playlists = keep
        err = next(m for m in ws.sent if m["type"] == "channel_plan")["error"]
        self.assertEqual(err, "Dieser Kanal hat keine öffentlichen Playlists.")

    def test_zweiter_versuch_ohne_js(self):
        calls = []
        keep_read, keep_js = channels._read_tab, core._JS_ARGS
        async def fake(cmd):
            calls.append(cmd)
            if "--js-runtimes" in cmd:
                return None, "ERROR: JS-Laufzeit startet nicht"
            return {"channel": "UKF", "entries": [{"url": "https://www.youtube.com/playlist?list=PL1", "title": "A"}]}, ""
        channels._read_tab = fake
        core._JS_ARGS = ["--js-runtimes", "node:x.exe"]
        try:
            title, pls = self.run_async(channels._channel_playlists("https://www.youtube.com/@ukf"))
        finally:
            channels._read_tab, core._JS_ARGS = keep_read, keep_js
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--js-runtimes", calls[1])
        self.assertEqual(title, "UKF")
        self.assertEqual(len(pls), 1)
