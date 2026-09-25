"""Fernbedienung und Wunschseite: geheimer Link, Wuensche aus der eigenen
Sammlung, Status fuer Gaeste und was beim Ablehnen geloescht werden darf."""
import json
import os
import time
import unittest

from tests.support import BackendTest, FakeWS, main

try:
    from starlette.testclient import TestClient
except Exception:          # httpx fehlt — dann nur die Tests ohne HTTP
    TestClient = None


class RemoteTestBase(BackendTest):

    def setUp(self):
        super().setUp()
        self._key = main._state.get("remote_key", "")
        main._state["remote_key"] = "geheim123"
        self._outcomes = dict(main._wish_outcomes)
        main._wish_outcomes.clear()
        self._playing = main._state.get("playing", False)

    def tearDown(self):
        main._state["remote_key"] = self._key
        main._wish_outcomes.clear()
        main._wish_outcomes.update(self._outcomes)
        main._state["playing"] = self._playing
        super().tearDown()

    def datei(self, name):
        f = self.tmp / "Musik" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        return str(f)


class GeheimerLinkTest(RemoteTestBase):

    def test_adresse_traegt_den_schluessel(self):
        urls = main._remote_urls("192.168.1.5")
        self.assertEqual(urls["url"], "http://192.168.1.5:8080/?k=geheim123")
        self.assertEqual(urls["wish_url"], "http://192.168.1.5:8080/wunsch")

    def test_schluessel_wird_einmal_erzeugt_und_gemerkt(self):
        main._state["remote_key"] = ""
        k = main._remote_key()
        self.assertGreaterEqual(len(k), 10)
        self.assertEqual(main._remote_key(), k)
        self.assertEqual(json.loads(main.SETTINGS_FILE.read_text("utf-8"))["remote_key"], k)

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_ohne_schluessel_nur_wunschseite(self):
        c = TestClient(main.remote_app)
        r = c.get("/", follow_redirects=False)
        self.assertIn(r.status_code, (302, 307))
        self.assertTrue(r.headers["location"].endswith("/wunsch"))
        self.assertEqual(c.get("/?k=falsch", follow_redirects=False).status_code, 307)
        self.assertEqual(c.get("/manifest.json").status_code, 403)
        self.assertEqual(c.get("/cover?i=0").status_code, 403)
        self.assertEqual(c.get("/wunsch").status_code, 200)

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_mit_schluessel_fernbedienung(self):
        c = TestClient(main.remote_app)
        r = c.get("/?k=geheim123")
        self.assertEqual(r.status_code, 200)
        self.assertIn("var KEY='geheim123'", r.text)
        self.assertNotIn("__KEY__", r.text)
        self.assertEqual(c.get("/manifest.json?k=geheim123").json()["start_url"], "/?k=geheim123")

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_websocket_ohne_schluessel_abgewiesen(self):
        c = TestClient(main.remote_app)
        with self.assertRaises(Exception):
            with c.websocket_connect("/ws") as ws:
                ws.receive_text()
        with c.websocket_connect("/ws?k=geheim123") as ws:
            self.assertEqual(json.loads(ws.receive_text())["type"], "state")


class RemoteStateTest(RemoteTestBase):

    def test_tonart_bpm_und_uebergang_in_der_warteschlange(self):
        a, b, c = self.datei("a.mp3"), self.datei("b.mp3"), self.datei("c.mp3")
        main._state["library"] = [{"path": a, "key": "Am", "bpm": 124},
                                  {"path": b, "key": "Em", "bpm": 126},     # Nachbar: passt
                                  {"path": c, "key": "F♯", "bpm": 128}]     # passt nicht
        main._state["queue"] = [{"path": p, "title": p} for p in (a, b, c)]
        st = json.loads(main._remote_state_payload())
        self.assertEqual([(t["key"], t["bpm"]) for t in st["queue"]],
                         [("Am", 124), ("Em", 126), ("F♯", 128)])
        self.assertEqual([t["compat"] for t in st["queue"]], [-1, 2, 0])

    def test_wuensche_ohne_pfade(self):
        main._state["wishes"] = [{"id": 7, "title": "X", "status": "bereit", "path": "C:\\geheim\\x.mp3",
                                  "from": "1.2.3.4"}]
        st = json.loads(main._remote_state_payload())
        self.assertEqual(st["wishes"][0]["id"], 7)
        self.assertNotIn("path", st["wishes"][0])
        self.assertNotIn("from", st["wishes"][0])


class WunschAusSammlungTest(RemoteTestBase):

    def test_suche_findet_titel_in_der_bibliothek(self):
        p = self.datei("rio.mp3")
        main._state["library"] = [{"path": p, "title": "Rio", "artist": "Netsky"},
                                  {"path": self.datei("x.mp3"), "title": "Anderes", "artist": "Wer"}]
        treffer = main._wish_library_hits("netsky rio")
        self.assertEqual([t["path"] for t in treffer], [p])
        self.assertNotIn("\\", main._lib_wish_id(p))

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_wunsch_aus_der_sammlung_ist_sofort_bereit(self):
        p = self.datei("rio.mp3")
        main._state["library"] = [{"path": p, "title": "Netsky - Rio", "lufs": -9.0}]
        c = TestClient(main.remote_app)
        with c.websocket_connect("/wunsch/ws") as ws:
            ws.send_json({"type": "wish_add", "lib": main._lib_wish_id(p), "title": "egal"})
            ack = json.loads(ws.receive_text())
        self.assertEqual(ack["type"], "wish_ack")
        w = main._state["wishes"][0]
        self.assertEqual((w["status"], w["path"], w["from_library"]), ("bereit", p, True))
        self.assertEqual(ack["id"], w["id"])

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_fremder_pfad_wird_nicht_angenommen(self):
        c = TestClient(main.remote_app)
        with c.websocket_connect("/wunsch/ws") as ws:
            ws.send_json({"type": "wish_add", "lib": "deadbeef0000", "path": "C:\\Windows\\x", "title": "x"})
            ws.send_json({"type": "wish_info", "ids": []})
            self.assertEqual(json.loads(ws.receive_text())["type"], "wish_info")
        self.assertEqual(main._state["wishes"], [])


class AblehnenLoeschtNurEigenesTest(RemoteTestBase):

    def _ablehnen(self, **wunsch):
        p = self.datei("song.mp3")
        main._state["wishes"] = [{"id": 1, "title": "Song", "status": "bereit", "path": p, **wunsch}]
        verschoben = []
        echt, main._move_to_trash = main._move_to_trash, lambda path: verschoben.append(path) or True
        try:
            self.run_async(main.handle_message(FakeWS(), {"type": "wish_reject", "id": 1}))
        finally:
            main._move_to_trash = echt
        return verschoben

    def test_titel_aus_der_sammlung_bleibt(self):
        self.assertEqual(self._ablehnen(from_library=True), [])

    def test_vorher_schon_geladener_titel_bleibt(self):
        self.assertEqual(self._ablehnen(keep_file=True), [])

    def test_frisch_geladener_titel_geht_in_den_papierkorb(self):
        self.assertEqual(len(self._ablehnen()), 1)


class GastStatusTest(RemoteTestBase):

    def test_angenommen_und_eingereiht(self):
        p = self.datei("wunsch.mp3")
        main._state["wishes"] = [{"id": 3, "title": "Wunsch", "status": "bereit", "path": p}]
        main._state["queue"] = [{"path": "laeuft", "title": "L", "duration_sec": 200}]
        main._state["current_idx"] = 0
        main._state["duration_ms"], main._state["position_ms"] = 200_000, 20_000
        self.run_async(main.handle_message(FakeWS(), {"type": "wish_accept", "id": 3}))
        st = main._guest_wish_status(3)
        self.assertEqual((st["state"], st["in_sec"]), ("queued", 180))

    def test_abgelehnt(self):
        main._state["wishes"] = [{"id": 4, "title": "Nein", "status": "fehler", "path": None}]
        self.assertEqual(main._guest_wish_status(4)["state"], "failed")
        self.run_async(main.handle_message(FakeWS(), {"type": "wish_reject", "id": 4}))
        self.assertEqual(main._guest_wish_status(4)["state"], "rejected")

    def test_laeuft_gerade_und_danach(self):
        main._state["queue"] = [{"path": "a", "title": "A", "played": True},
                                {"path": "b", "title": "B"}, {"path": "c", "title": "C"},
                                {"path": "d", "title": "D"}, {"path": "e", "title": "E"}]
        main._state["current_idx"], main._state["playing"] = 1, True
        info = main._guest_now_next()
        self.assertEqual(info["now"]["title"], "B")
        self.assertEqual([t["title"] for t in info["next"]], ["C", "D", "E"])
        self.assertNotIn("path", info["now"])


class ProcessWishTest(RemoteTestBase):

    def test_vorhandene_datei_wird_als_behalten_markiert(self):
        p = self.datei("alt.mp3")
        main._state["history"] = [{"url": "https://x", "path": p, "bitrate_kbps": 256}]
        async def download(url, fmt):
            return p
        async def enrich(path, force=False):
            pass
        echt = (main.run_download, main._enrich_track)
        main.run_download, main._enrich_track = download, enrich
        try:
            w = {"id": 9, "url": "https://x", "title": "Alt", "status": "neu"}
            main._state["wishes"] = [w]
            self.run_async(main._process_wish(w))
        finally:
            main.run_download, main._enrich_track = echt
        self.assertEqual((w["status"], w["keep_file"]), ("bereit", True))
