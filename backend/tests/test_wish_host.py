"""Feste Adresse fuer den gedruckten Musikwunsch-Code."""
import json
from unittest import mock
from tests.support import BackendTest, FakeWS, main
from synthimix import remote


class WishHostTest(BackendTest):

    def setUp(self):
        super().setUp()
        main._state["wish_host"] = ""
        self._ip = mock.patch.object(remote, "_get_local_ip", return_value="10.0.0.7")
        self._ip.start()

    def tearDown(self):
        self._ip.stop()
        main._state["wish_host"] = ""
        super().tearDown()

    def test_link_ip_oder_name(self):
        p = remote.parse_wish_host
        self.assertEqual(p("http://192.168.0.50:8080/wunsch"), ("192.168.0.50", ""))
        self.assertEqual(p(" 192.168.0.50 "), ("192.168.0.50", ""))
        self.assertEqual(p("dj-laptop.fritz.box"), ("dj-laptop.fritz.box", ""))
        self.assertEqual(p(""), ("", ""))
        self.assertEqual(p("192.168.0.50:9000")[0], "")
        self.assertIn("8080", p("192.168.0.50:9000")[1])
        self.assertTrue(p("hallo welt")[1])

    def test_link_auch_bei_ausgeschaltetem_server(self):
        msg = main._remote_status_msg()
        self.assertFalse(msg["running"])
        self.assertEqual(msg["wish_url"], "http://10.0.0.7:8080/wunsch")
        self.assertEqual(msg["ip"], "10.0.0.7")
        self.assertNotIn("url", msg)                 # Fernbedienung nur bei laufendem Server

    def test_feste_adresse_wird_gemerkt(self):
        self.run_async(main.handle_message(FakeWS(), {"type": "set_wish_host",
                                                      "value": "http://192.168.0.50:8080/wunsch"}))
        self.assertEqual(main._state["wish_host"], "192.168.0.50")
        msg = main._remote_status_msg()
        self.assertEqual(msg["wish_url"], "http://192.168.0.50:8080/wunsch")
        self.assertEqual(msg["ip"], "10.0.0.7")      # daran sieht die App, dass es nicht passt
        saved = json.loads(main.SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["wish_host"], "192.168.0.50")

    def test_falsche_eingabe_aendert_nichts(self):
        main._state["wish_host"] = "192.168.0.50"
        ws = FakeWS()
        self.run_async(main.handle_message(ws, {"type": "set_wish_host", "value": "192.168.0.50:9000"}))
        self.assertEqual(main._state["wish_host"], "192.168.0.50")
        self.assertTrue(any("wish_host_error" in m for m in ws.sent))

    def test_leer_heisst_automatisch(self):
        main._state["wish_host"] = "192.168.0.50"
        self.run_async(main.handle_message(FakeWS(), {"type": "set_wish_host", "value": ""}))
        self.assertEqual(main._remote_status_msg()["wish_url"], "http://10.0.0.7:8080/wunsch")
