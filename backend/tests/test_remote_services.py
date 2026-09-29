"""Fernbedienung und Musikwuensche einzeln ein- und ausschalten."""
from tests.support import BackendTest, FakeWS, main


class RemoteServicesTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._keep = (main._start_remote_server, main._stop_remote_server, dict(main._remote_services))
        self.gestartet = self.gestoppt = 0

        async def start(ws):
            self.gestartet += 1
        async def stop():
            self.gestoppt += 1
        main._start_remote_server, main._stop_remote_server = start, stop
        main._remote_services.update(remote=False, wishes=False)

    def tearDown(self):
        main._start_remote_server, main._stop_remote_server, saved = self._keep
        main._remote_services.clear(); main._remote_services.update(saved)
        super().tearDown()

    def send(self, msg):
        self.run_async(main.handle_message(FakeWS(), msg))

    def test_einzeln_an_und_aus(self):
        self.send({"type": "remote_start", "service": "wishes"})
        self.assertEqual(main._remote_services, {"remote": False, "wishes": True})
        self.send({"type": "remote_start", "service": "remote"})
        self.assertEqual(main._remote_services, {"remote": True, "wishes": True})
        self.send({"type": "remote_stop", "service": "remote"})
        self.assertEqual(main._remote_services, {"remote": False, "wishes": True})
        self.assertEqual(self.gestoppt, 0)                     # Server laeuft fuer die Wuensche weiter
        self.send({"type": "remote_stop", "service": "wishes"})
        self.assertEqual(self.gestoppt, 1)                     # jetzt ist keiner mehr an
        self.assertEqual(main._state["remote_services_saved"], {"remote": False, "wishes": True})

    def test_ohne_angabe_beide(self):
        self.send({"type": "remote_start"})
        self.assertEqual(main._remote_services, {"remote": True, "wishes": True})

    def test_seiten_je_dienst(self):
        main._remote_services.update(remote=False, wishes=True)
        self.assertEqual(self.run_async(main.wish_page()).status_code, 200)
        key = main._remote_key()
        r = self.run_async(main.remote_index(k=key))
        self.assertEqual(r.status_code, 307)                   # Fernbedienung aus → zu den Wuenschen
        main._remote_services.update(remote=False, wishes=False)
        self.assertEqual(self.run_async(main.wish_page()).status_code, 503)
        self.assertEqual(self.run_async(main.remote_index(k=key)).status_code, 503)
        main._remote_services.update(remote=True)
        self.assertEqual(self.run_async(main.remote_index(k=key)).status_code, 200)
