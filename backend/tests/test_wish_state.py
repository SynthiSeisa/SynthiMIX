"""Wunsch-Status fuer Gaeste, wenn der Titel nicht mehr in der Warteschlange steht."""
import time

from tests.support import BackendTest, main


class WishStateTest(BackendTest):

    def setUp(self):
        super().setUp()
        main._wish_outcomes.clear()
        main._wish_outcomes[7] = {"state": "angenommen", "title": "Song", "path": "w.mp3", "at": time.time() - 60}
        main._state["play_log"] = []

    def tearDown(self):
        main._wish_outcomes.clear()
        super().tearDown()

    def test_vom_dj_wieder_entfernt(self):
        main._state["queue"] = [{"path": "anderer.mp3"}]
        main._state["current_idx"] = 0
        self.assertEqual(main._guest_wish_status(7)["state"], "rejected")

    def test_gespielt_und_automatisch_entfernt(self):
        main._state["queue"] = [{"path": "anderer.mp3"}]
        main._state["current_idx"] = 0
        main._state["play_log"] = [{"path": "w.mp3", "title": "Song", "played_at": int(time.time())}]
        st = main._guest_wish_status(7)
        self.assertEqual(st["state"], "played")

    def test_noch_eingeplant(self):
        main._state["queue"] = [{"path": "a.mp3", "duration_sec": 100}, {"path": "w.mp3", "duration_sec": 100}]
        main._state["current_idx"] = 0
        self.assertEqual(main._guest_wish_status(7)["state"], "queued")


class HistoryPayloadTest(BackendTest):

    def test_geloeschte_dateien_bleiben_markiert(self):
        import os, tempfile
        fd, da = tempfile.mkstemp(suffix=".mp3"); os.close(fd)
        try:
            main._state["history"] = [{"url": "u1", "path": da, "title": "Da"},
                                      {"url": "u2", "path": da + ".weg", "title": "Weg"}]
            items = main._history_payload()["items"]
            self.assertEqual([i["gone"] for i in items], [False, True])
        finally:
            os.remove(da)
