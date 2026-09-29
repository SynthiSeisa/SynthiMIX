"""Bibliothek an die Oberflaeche: nur Aenderungen statt der ganzen Liste."""
from tests.support import BackendTest, FakeWS, main


class LibraryDeltaTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._keep_clients = set(main.clients)
        main.clients.clear()
        main._lib_sent.clear()
        main._state["library"] = [{"path": f"C:/m/{i}.mp3", "title": f"T{i}"} for i in range(10)]

    def tearDown(self):
        main.clients.clear()
        main.clients.update(self._keep_clients)
        main._lib_sent.clear()
        super().tearDown()

    def test_erst_ganz_dann_aenderungen(self):
        ws = FakeWS()
        main.clients.add(ws)
        self.run_async(main.send_library_full(ws))
        self.assertEqual(len(ws.of_type("library")[-1]["tracks"]), 10)

        # nichts geaendert: nichts schicken
        self.run_async(main.push_library())
        self.assertEqual(len(ws.sent), 1)

        main._state["library"][3]["title"] = "Neu"
        del main._state["library"][7]
        main._state["library"].append({"path": "C:/m/x.mp3", "title": "X"})
        self.run_async(main.push_library())
        d = ws.of_type("library_delta")[-1]
        self.assertEqual({t["path"] for t in d["upsert"]}, {"C:/m/3.mp3", "C:/m/x.mp3"})
        self.assertEqual(d["remove"], ["C:/m/7.mp3"])

    def test_viel_geaendert_ganze_liste(self):
        ws = FakeWS()
        main.clients.add(ws)
        self.run_async(main.send_library_full(ws))
        for lt in main._state["library"][:6]:
            lt["title"] += "!"
        self.run_async(main.push_library())
        self.assertEqual(ws.sent[-1]["type"], "library")

    def test_neue_verbindung_bekommt_alles(self):
        ws = FakeWS()
        main.clients.add(ws)
        self.run_async(main.push_library())
        self.assertEqual(ws.sent[-1]["type"], "library")
