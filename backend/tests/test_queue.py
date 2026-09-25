"""Warteschlange: der Zeiger auf den laufenden Titel muss bei allen Umbauten
auf demselben Titel bleiben, und Wiederholen/Zufall ueberleben einen Neustart."""
import json
import random

from tests.support import BackendTest, FakeWS, main


class ZeigerTest(BackendTest):

    def test_markierte_mischen_behaelt_den_laufenden_titel(self):
        # Frueher wurde der laufende Titel erst NACH dem Mischen gesucht —
        # gefunden wurde dann der, der jetzt an seiner Stelle stand.
        for seed in range(30):
            random.seed(seed)
            main._state["queue"] = [{"path": p, "title": p} for p in "abcdef"]
            main._state["current_idx"] = 2          # "c" laeuft
            self.run_async(main.handle_message(FakeWS(), {"type": "queue_shuffle_selected",
                                                          "indices": [1, 2, 3, 4]}))
            ci = main._state["current_idx"]
            self.assertEqual(main._state["queue"][ci]["path"], "c", f"seed {seed}")

    def _gespeicherte_queue(self, namen, laufend, fehlend):
        items = []
        for n in namen:
            f = self.tmp / f"{n}.mp3"
            if n not in fehlend:
                f.write_bytes(b"x")
            items.append({"path": str(f), "title": n})
        main.QUEUE_FILE.write_text(json.dumps({"items": items, "current_idx": namen.index(laufend),
                                               "position_ms": 0}))
        main.load_queue()
        return [t["title"] for t in main._state["queue"]]

    def test_neustart_mit_fehlender_datei_davor(self):
        titel = self._gespeicherte_queue("abc", laufend="b", fehlend="a")
        self.assertEqual(titel[main._state["current_idx"]], "b")

    def test_neustart_laufender_titel_fehlt_selbst(self):
        titel = self._gespeicherte_queue("abcd", laufend="b", fehlend="b")
        self.assertEqual(titel[main._state["current_idx"]], "c")

    def test_neustart_letzter_titel_fehlt(self):
        titel = self._gespeicherte_queue("abc", laufend="c", fehlend="c")
        self.assertEqual(titel[main._state["current_idx"]], "b")


class WiedergabeModusTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._modus = (main._state.get("repeat", 0), main._state.get("shuffle", False))

    def tearDown(self):
        main._state["repeat"], main._state["shuffle"] = self._modus
        super().tearDown()

    def test_wiederholen_und_zufall_bleiben_nach_neustart(self):
        self.run_async(main.handle_message(FakeWS(), {"type": "set_repeat", "value": 2}))
        self.run_async(main.handle_message(FakeWS(), {"type": "set_shuffle", "value": True}))
        main._state["repeat"], main._state["shuffle"] = 0, False
        main.load_settings()
        self.assertEqual((main._state["repeat"], main._state["shuffle"]), (2, True))


class RemoteStatusTest(BackendTest):

    def test_autostart_umschalten_meldet_keinen_gestoppten_server(self):
        gesendet = []
        async def mitschreiben(msg):
            gesendet.append(msg)
        echt, main.broadcast = main.broadcast, mitschreiben
        try:
            self.run_async(main.handle_message(FakeWS(), {"type": "set_remote_autostart", "value": True}))
        finally:
            main.broadcast = echt
            main._state["remote_autostart"] = False
        self.assertFalse([m for m in gesendet if m.get("type") == "remote_status"])
