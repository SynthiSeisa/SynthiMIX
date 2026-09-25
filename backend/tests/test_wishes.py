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
        vorhin = int(time.time()) - 3600
        main._state["play_log"] = [{"title": "Netsky - Rio", "played_at": vorhin}]
        st = main._wish_title_status("Netsky - Rio")
        self.assertEqual((st["state"], st["at"]), ("played", vorhin))

    def test_lief_letzte_woche_zaehlt_nicht(self):
        # Das Play-Log reicht ueber Wochen zurueck; den Gaesten stand sonst
        # "lief um 21:14 Uhr" fuer einen Titel von letzter Woche da.
        main._state["play_log"] = [{"title": "Netsky - Rio", "played_at": int(time.time()) - 7 * 86400}]
        self.assertEqual(main._wish_title_status("Netsky - Rio")["state"], "free")

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


class WishResumeTest(BackendTest):
    """Wuensche, die beim Beenden in Arbeit waren, duerfen nicht haengenbleiben."""

    def test_fertig_geladener_wunsch_wird_bereit(self):
        f = self.tmp / "da.mp3"
        f.write_bytes(b"x")
        main._state["wishes"] = [{"id": 1, "status": "analysiert", "path": str(f), "url": "https://x"}]
        main._resume_wishes()
        self.assertEqual(main._state["wishes"][0]["status"], "bereit")

    def test_abgebrochener_download_wird_neu_gestartet(self):
        gestartet = []
        async def attrappe(w):
            gestartet.append(w["id"])
        echt, main._process_wish = main._process_wish, attrappe
        try:
            async def ablauf():
                main._state["wishes"] = [
                    {"id": 1, "status": "laedt", "path": None, "url": "https://x"},
                    {"id": 2, "status": "fehler", "path": None, "url": "https://y"},
                    {"id": 3, "status": "bereit", "path": None, "url": "https://z"}]
                main._resume_wishes()
                await __import__("asyncio").sleep(0)
            self.run_async(ablauf())
        finally:
            main._process_wish = echt
        self.assertEqual(gestartet, [1])
