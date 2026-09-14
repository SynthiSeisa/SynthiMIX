"""Musikwuensche: was der Gast zu einem Titel angezeigt bekommt, und was
Annehmen und Ablehnen auf DJ-Seite tun."""
import os
import time

from tests.support import BackendTest, FakeWS, main


class WishStatusTest(BackendTest):

    def test_laeuft_gerade(self):
        main._state["queue"] = [{"title": "Netsky - Rio", "path": "a"}]
        main._state["current_idx"] = 0
        self.assertEqual(main._wish_title_status("Netsky - Rio")["state"], "playing")

    def test_steht_in_der_queue_mit_restzeit(self):
        main._state["queue"] = [{"title": "Laeuft", "path": "a", "duration_sec": 200},
                                {"title": "Dazwischen", "path": "b", "duration_sec": 100},
                                {"title": "Netsky - Rio", "path": "c", "duration_sec": 180}]
        main._state["current_idx"] = 0
        main._state["duration_ms"], main._state["position_ms"] = 200_000, 50_000
        st = main._wish_title_status("Netsky - Rio")
        self.assertEqual(st["state"], "queued")
        self.assertEqual(st["in_sec"], 150 + 100)

    def test_lief_schon(self):
        main._state["play_log"] = [{"title": "Netsky - Rio", "played_at": 1_700_000_000}]
        st = main._wish_title_status("Netsky - Rio")
        self.assertEqual((st["state"], st["at"]), ("played", 1_700_000_000))

    def test_schon_gewuenscht(self):
        main._state["wishes"] = [{"title": "Netsky - Rio", "status": "bereit", "count": 3}]
        st = main._wish_title_status("netsky – rio")
        self.assertEqual((st["state"], st["count"]), ("wished", 3))

    def test_frei(self):
        self.assertEqual(main._wish_title_status("Ganz was anderes")["state"], "free")


class WishDecisionTest(BackendTest):

    def _wish(self, **kw):
        f = self.tmp / "Downloads" / "wunsch.mp3"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        w = {"id": 1, "url": "https://x", "title": "Wunsch", "from": "1.2.3.4",
             "count": 1, "status": "bereit", "path": str(f), "created_at": int(time.time())}
        w.update(kw)
        main._state["wishes"] = [w]
        return w, f

    def test_annehmen_haengt_an_und_entfernt_den_wunsch(self):
        w, f = self._wish()
        main._state["queue"] = [{"title": "vorher", "path": "v"}]
        self.run_async(main.handle_message(FakeWS(), {"type": "wish_accept", "id": 1}))
        self.assertEqual([t["path"] for t in main._state["queue"]], ["v", str(f)])
        self.assertEqual(main._state["wishes"], [])
        self.assertTrue(f.exists())

    def test_annehmen_als_naechster(self):
        w, f = self._wish()
        main._state["queue"] = [{"path": "laeuft"}, {"path": "danach"}]
        main._state["current_idx"] = 0
        self.run_async(main.handle_message(FakeWS(), {"type": "wish_accept", "id": 1, "as_next": True}))
        self.assertEqual([t["path"] for t in main._state["queue"]], ["laeuft", str(f), "danach"])

    def test_ablehnen_schiebt_die_datei_in_den_papierkorb(self):
        w, f = self._wish()
        main._state["library"] = [{"path": str(f), "title": "Wunsch"}]
        # Attrappe statt echtem Papierkorb, sonst landet bei jedem Build eine
        # Testdatei darin
        verschoben = []
        def attrappe(path):
            verschoben.append(path)
            os.remove(path)
            return True
        echt, main._move_to_trash = main._move_to_trash, attrappe
        try:
            self.run_async(main.handle_message(FakeWS(), {"type": "wish_reject", "id": 1}))
        finally:
            main._move_to_trash = echt
        self.assertEqual(verschoben, [str(f)])
        self.assertFalse(f.exists())
        self.assertEqual(main._state["wishes"], [])
        self.assertEqual(main._state["library"], [])
