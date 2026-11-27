"""Absicherung (2026-11-27): Backend nur fuers eigene Fenster, Gaeste nur
einzelne YouTube-Titel, Remote-Entfernen des laufenden Titels, IP-Pruefung."""
import json
import unittest
from unittest import mock

from tests.support import main
from tests.test_remote import RemoteTestBase, TestClient
from synthimix import remote


class OriginTest(unittest.TestCase):

    def test_herkunft(self):
        ok = main._origin_ok
        self.assertTrue(ok(None))                        # kein Browser (Tests, Probelauf)
        self.assertTrue(ok("file://"))                   # gebautes Fenster (in Electron nachgemessen)
        self.assertTrue(ok("http://localhost:5175"))     # Vorschau
        self.assertTrue(ok("http://127.0.0.1:5173"))
        for bad in ("null", "https://example.com", "http://localhost.evil.com",
                    "http://localhost:5173.evil.com", "http://192.168.0.5:5173", ""):
            self.assertFalse(ok(bad), bad)

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_fremde_seite_wird_abgewiesen(self):
        from starlette.websockets import WebSocketDisconnect
        c = TestClient(main.app)
        with self.assertRaises(WebSocketDisconnect):
            with c.websocket_connect("/ws", headers={"origin": "https://example.com"}) as ws:
                ws.receive_text()
        with c.websocket_connect("/ws", headers={"origin": "file://"}) as ws:
            ws.send_text(json.dumps({"type": "get_notes"}))
            self.assertEqual(json.loads(ws.receive_text())["type"], "notes")


class WishUrlTest(RemoteTestBase):

    def test_nur_einzelne_youtube_titel(self):
        u = remote._wish_video_url
        self.assertEqual(u("https://music.youtube.com/watch?v=abcDEF12345"),
                         "https://music.youtube.com/watch?v=abcDEF12345")
        # Playlist-Teile fallen weg: geladen wird nur der eine Titel
        self.assertEqual(u("https://www.youtube.com/watch?v=abcDEF12345&list=RDabc&index=3"),
                         "https://www.youtube.com/watch?v=abcDEF12345")
        self.assertEqual(u("https://youtu.be/abcDEF12345?si=x"), "https://www.youtube.com/watch?v=abcDEF12345")
        self.assertEqual(u("https://m.youtube.com/watch?feature=x&v=abcDEF12345"),
                         "https://www.youtube.com/watch?v=abcDEF12345")
        for bad in ("https://www.youtube.com/playlist?list=PL123",
                    "https://evil.com/watch?v=abcDEF12345",
                    "https://youtube.com.evil.com/watch?v=abcDEF12345",
                    "https://www.youtube.com/watch?v=abc",
                    "--exec calc", "ytsearch:netsky", ""):
            self.assertEqual(u(bad), "", bad)

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_playlist_vom_gast_wird_nicht_geladen(self):
        main._state["wishes"] = []
        with mock.patch.object(remote, "_process_wish", mock.AsyncMock()) as proc:
            c = TestClient(main.remote_app)
            with c.websocket_connect("/wunsch/ws") as ws:
                ws.send_json({"type": "wish_add", "url": "https://www.youtube.com/playlist?list=PL1", "title": "Alles"})
                ws.send_json({"type": "wish_add", "url": "https://evil.com/x.exe", "title": "Datei"})
                ws.send_json({"type": "wish_add", "url": "https://music.youtube.com/watch?v=abcDEF12345&list=RD1",
                              "title": "Netsky - Rio"})
                seen = []
                msg = json.loads(ws.receive_text())
                while msg["type"] != "wish_ack":
                    seen.append(msg["type"])
                    msg = json.loads(ws.receive_text())
        # Abgelehnte bekommen eine Antwort (sonst glaubt der Gast, es hat geklappt)
        self.assertEqual(seen.count("wish_deny"), 2)
        self.assertEqual([w["url"] for w in main._state["wishes"]], ["https://music.youtube.com/watch?v=abcDEF12345"])
        self.assertEqual(proc.call_count, 1)


class RemoteRemoveCurrentTest(RemoteTestBase):

    @unittest.skipIf(TestClient is None, "httpx nicht installiert")
    def test_laufenden_titel_entfernen_ueberspringt_keinen(self):
        main._state["queue"] = [{"path": self.datei(n + ".mp3"), "title": n} for n in ("A", "B", "C")]
        main._state["current_idx"] = 0
        c = TestClient(main.remote_app)
        with c.websocket_connect("/ws?k=geheim123") as ws:
            ws.receive_text()
            ws.send_json({"type": "queue_remove", "index": 0, "path": main._state["queue"][0]["path"]})
            ws.send_json({"type": "ping_test_sync"})     # unbekannt: nur zum Abwarten
            for _ in range(3):
                if len(main._state["queue"]) == 2:
                    break
                ws.receive_text()
        q, ci = main._state["queue"], main._state["current_idx"]
        self.assertEqual([t["title"] for t in q], ["B", "C"])
        # "Naechster" ist Index + 1 → muss B sein, nicht C
        self.assertEqual(q[ci + 1]["title"], "B")


class WishHostIpTest(unittest.TestCase):

    def test_ungueltige_ip(self):
        p = remote.parse_wish_host
        self.assertEqual(p("192.168.0.50"), ("192.168.0.50", ""))
        for bad in ("192.168.0.500", "999.1.1.1", "192.168.0", "1.2.3.4.5"):
            self.assertEqual(p(bad)[0], "", bad)
            self.assertTrue(p(bad)[1], bad)
