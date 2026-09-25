"""Taegliche Pruefung auf neue Versionen von yt-dlp und spotdl.

Netz und Programme werden durch Attrappen ersetzt: getestet wird, was die App
daraus macht — still aktualisieren, Hinweis zeigen oder gar nichts.
"""
from tests.support import BackendTest, FakeWS, main


class ToolUpdateTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._echt = {n: getattr(main, n) for n in (
            "_ytdlp_version_sync", "_ytdlp_latest_tag", "_update_ytdlp",
            "_find_spotdl_cmd", "_spotdl_version_sync", "_spotdl_latest_tag")}
        self._zustand = {k: main._state.get(k) for k in ("tool_updates", "ytdlp_autoupdate", "ytdlp_last_check")}
        main._state["tool_updates"] = {}
        self.ytdlp = {"lokal": "2026.07.04", "neueste": "2026.09.12"}
        self.spotdl = None                     # None = nicht installiert
        self.updates = []

        main._ytdlp_version_sync = lambda: self.ytdlp["lokal"]
        main._ytdlp_latest_tag = lambda: self.ytdlp["neueste"]
        async def update(ws=None):
            self.updates.append("yt-dlp")
            self.ytdlp["lokal"] = self.ytdlp["neueste"]
            return True
        main._update_ytdlp = update
        main._find_spotdl_cmd = lambda: ["spotdl.exe"] if self.spotdl else None
        main._spotdl_version_sync = lambda: self.spotdl[0]
        main._spotdl_latest_tag = lambda: self.spotdl[1]

    def tearDown(self):
        for n, f in self._echt.items():
            setattr(main, n, f)
        main._state.update(self._zustand)
        super().tearDown()

    def pruefen(self):
        self.run_async(main._tools_check_once())
        return main._state["tool_updates"]

    def test_ohne_automatik_hinweis_statt_nichts(self):
        # Frueher wurde ohne Automatik gar nicht geprueft
        main._state["ytdlp_autoupdate"] = False
        tu = self.pruefen()
        self.assertEqual(self.updates, [])
        self.assertEqual(tu["ytdlp"], {"latest": "2026.09.12", "available": True})

    def test_mit_automatik_aktualisieren_und_melden(self):
        main._state["ytdlp_autoupdate"] = True
        tu = self.pruefen()
        self.assertEqual(self.updates, ["yt-dlp"])
        self.assertFalse(tu["ytdlp"]["available"])
        self.assertEqual((tu["ytdlp_updated"]["from"], tu["ytdlp_updated"]["to"]),
                         ("2026.07.04", "2026.09.12"))

    def test_waehrend_eines_downloads_nur_hinweis(self):
        main._state["ytdlp_autoupdate"] = True
        main._state["downloads"] = [{"id": 1, "status": "active"}]
        tu = self.pruefen()
        self.assertEqual(self.updates, [])
        self.assertTrue(tu["ytdlp"]["available"])

    def test_aktuell_kein_hinweis(self):
        self.ytdlp["neueste"] = self.ytdlp["lokal"]
        self.assertFalse(self.pruefen()["ytdlp"]["available"])

    def test_spotdl_veraltet(self):
        self.spotdl = ("4.4.2", "v4.4.3")
        tu = self.pruefen()
        self.assertEqual(tu["spotdl"], {"current": "4.4.2", "latest": "4.4.3", "available": True})

    def test_ohne_spotdl_kein_eintrag(self):
        main._state["tool_updates"] = {"spotdl": {"available": True}}
        self.assertNotIn("spotdl", self.pruefen())

    def test_ergebnis_ueberlebt_neustart(self):
        main._state["ytdlp_autoupdate"] = False
        self.pruefen()
        main._state["tool_updates"] = {}
        main.load_settings()
        self.assertTrue(main._state["tool_updates"]["ytdlp"]["available"])

    def test_hinweis_wegklicken(self):
        main._state["tool_updates"] = {"ytdlp_updated": {"from": "a", "to": "b", "at": 1}}
        self.run_async(main.handle_message(FakeWS(), {"type": "dismiss_ytdlp_updated"}))
        self.assertNotIn("ytdlp_updated", main._state["tool_updates"])

    def test_spotdl_nicht_waehrend_spotify_download_ersetzen(self):
        main._state["downloads"] = [{"id": 1, "status": "active", "session_label": "Spotify"}]
        ws = FakeWS()
        self.run_async(main._install_spotdl(ws))
        self.assertEqual(ws.of_type("spotdl_install_error")[0]["text"],
                         "Erst nach dem laufenden Spotify-Download aktualisieren")
