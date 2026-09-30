"""Fernbedienung (neu) und Wunschseite: Seiten aus Dateien, App-Symbol,
passende Titel, Jetzt mischen, Name und Nachricht beim Wunsch."""
import json
import unittest

from tests.support import FakeWS, main
from tests.test_remote import RemoteTestBase, TestClient


class RemoteV2Test(RemoteTestBase):

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_seiten_und_symbol(self):
        c = TestClient(main.remote_app)
        r = c.get("/?k=geheim123")
        self.assertEqual(r.status_code, 200)
        self.assertIn("var KEY='geheim123'", r.text)
        self.assertNotIn("__KEY__", r.text)
        for tab in ("Läuft", "Warteschlange", "Suche", "Wünsche"):
            self.assertIn(tab, r.text.replace("&#228;", "ä").replace("&#252;", "ü"))
        self.assertIn("Wunsch senden", c.get("/wunsch").text)
        png = c.get("/icon-192.png")
        self.assertEqual((png.status_code, png.headers["content-type"], png.content[:4]), (200, "image/png", b"\x89PNG"))
        self.assertEqual(c.get("/icon-77.png").status_code, 404)
        icons = c.get("/manifest.json?k=geheim123").json()["icons"]
        self.assertEqual([i["sizes"] for i in icons], ["192x192", "512x512"])

    def _lib(self):
        main._state["library"] = [
            {"path": self.datei("a.mp3"), "title": "Laeuft", "artist": "A", "key": "8A", "bpm": 174},
            {"path": self.datei("b.mp3"), "title": "Passt", "artist": "B", "key": "9A", "bpm": 172},
            {"path": self.datei("c.mp3"), "title": "Gleich", "artist": "C", "key": "8A", "bpm": 87},
            {"path": self.datei("d.mp3"), "title": "Falsche Tonart", "artist": "D", "key": "3B", "bpm": 174},
            {"path": self.datei("e.mp3"), "title": "Zu langsam", "artist": "E", "key": "8A", "bpm": 128},
            {"path": self.datei("f.mp3"), "title": "Kommt schon", "artist": "F", "key": "8A", "bpm": 174},
        ]
        lib = main._state["library"]
        main._state["queue"] = [{"path": lib[0]["path"], "title": "Laeuft"}, {"path": lib[5]["path"], "title": "Kommt schon"}]
        main._state["current_idx"] = 0

    def test_passende_titel(self):
        self._lib()
        res = main._remote_lib_search("", True)
        self.assertEqual([r["title"] for r in res], ["Gleich", "Passt"])     # gleiche Tonart zuerst, Halbtempo zaehlt
        self.assertEqual([r["compat"] for r in res], [3, 2])
        alle = main._remote_lib_search("a", False)
        self.assertTrue(any(r["title"] == "Falsche Tonart" and r["compat"] == 0 for r in alle))

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_jetzt_mischen_und_sortieren_erreichen_die_app(self):
        self._lib()
        app_ws = FakeWS()
        keep = set(main.clients)
        main.clients.clear(); main.clients.add(app_ws)
        try:
            c = TestClient(main.remote_app)
            with c.websocket_connect("/ws?k=geheim123") as ws:
                self.assertEqual(json.loads(ws.receive_text())["type"], "state")
                ws.send_json({"type": "mix_now"})
                ws.send_json({"type": "search_library", "query": "", "match": True})
                msg = json.loads(ws.receive_text())
                while msg["type"] != "search_results":
                    msg = json.loads(ws.receive_text())
            self.assertEqual([r["title"] for r in msg["results"]], ["Gleich", "Passt"])
            self.assertEqual(len(app_ws.of_type("mix_now")), 1)
        finally:
            main.clients.clear(); main.clients.update(keep)

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_wunsch_mit_name_und_nachricht(self):
        p = self.datei("rio.mp3")
        main._state["library"] = [{"path": p, "title": "Netsky - Rio", "lufs": -9.0}]
        c = TestClient(main.remote_app)
        with c.websocket_connect("/wunsch/ws") as ws:
            ws.send_json({"type": "wish_add", "lib": main._lib_wish_id(p), "title": "x",
                          "name": "  Anna\n", "note": "Für das\x00 Brautpaar " + "x" * 200})
            json.loads(ws.receive_text())
        w = main._state["wishes"][0]
        self.assertEqual(w["names"], ["Anna"])
        self.assertTrue(w["note"].startswith("Für das Brautpaar"))
        self.assertLessEqual(len(w["note"]), 80)
        # Zweiter Gast wuenscht dasselbe: Name kommt dazu
        with c.websocket_connect("/wunsch/ws") as ws:
            ws.send_json({"type": "wish_add", "lib": main._lib_wish_id(p), "title": "x", "name": "Tom"})
            json.loads(ws.receive_text())
        self.assertEqual((w["count"], w["names"]), (2, ["Anna", "Tom"]))
        st = json.loads(main._remote_state_payload())
        self.assertEqual(st["wishes"][0]["names"], ["Anna", "Tom"])
