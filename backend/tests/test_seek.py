"""Spulen: der Player springt nur bei echten Spul-Befehlen (seek_seq) — ein
Titelwechsel per Doppelklick spulte sonst manchmal den laufenden Titel zurueck."""
from tests.support import BackendTest, FakeWS, main


class SeekTest(BackendTest):

    def setUp(self):
        super().setUp()
        self.sent = []
        self._bc = main.core.broadcast

        async def bc(msg):
            self.sent.append(msg)
        main.core.broadcast = bc
        main._state["queue"] = [{"path": f"C:\\x\\{n}.mp3", "title": n} for n in "ABC"]
        main._state["current_idx"] = 0
        main._state["seek_seq"] = 0

    def tearDown(self):
        main.core.broadcast = self._bc
        super().tearDown()

    def states(self):
        return [m["state"] for m in self.sent if m.get("type") == "player_state"]

    def test_titelwechsel_ist_kein_sprung(self):
        main._state["position_ms"] = 95000          # A laeuft bei 1:35
        self.run_async(main.handle_message(FakeWS(), {"type": "play_at", "index": 2}))
        st = self.states()[-1]
        self.assertEqual((st["current_idx"], st["position_ms"]), (2, 0))
        self.assertEqual(st["seek_seq"], 0)                   # kein Spul-Befehl

    def test_spulen_zaehlt(self):
        self.run_async(main.handle_message(FakeWS(), {"type": "seek", "position_ms": 30000}))
        self.run_async(main.handle_message(FakeWS(), {"type": "seek", "position_ms": 0}))
        self.assertEqual([s["seek_seq"] for s in self.states()], [1, 2])
        self.assertEqual(self.states()[-1]["position_ms"], 0)
