"""Playlist-Pruefung: Song-Versionen nur fuer Unbekanntes suchen, Suche merken."""
from tests.support import BackendTest, main


class AuditSpeedTest(BackendTest):

    def test_bekanntes_wird_nicht_gesucht(self):
        vorhanden = self.tmp / "vorhanden.mp3"
        vorhanden.write_bytes(b"x")
        main._state["library"] = [{"path": str(self.tmp / "lib.mp3"), "title": "Blinding Lights",
                                   "artist": "The Weeknd", "duration_sec": 200}]
        main._state["history"] = [{"url": "https://www.youtube.com/watch?v=verlauf0001",
                                   "path": str(vorhanden)}]
        e = lambda vid, t: {"url": f"https://www.youtube.com/watch?v={vid}", "title": t, "duration": 200}
        entries = [
            e("bibliothek1", "The Weeknd - Blinding Lights (Official Video)"),
            e("verlauf0001", "Irgendwer - Schon geladen"),
            e("bekannt0001", "Jemand - Verfolgt und bekannt"),
            e("ganzneu0001", "Neue Band - Neuer Song (Official Video)"),
        ]
        rest = main._audit_unknown(entries, {"bekannt0001"})
        self.assertEqual([x["url"][-11:] for x in rest], ["ganzneu0001"])

    def test_suche_wird_gemerkt(self):
        calls = []

        async def fake_songs(query, n=8, details=True):
            calls.append(query)
            return [] if query == "nichts" else [{"url": "https://music.youtube.com/watch?v=song0000001",
                                                  "title": "Song", "artist": "Band", "duration": 180}]
        orig = main._ytm_songs
        main._ytm_songs = fake_songs
        try:
            a = self.run_async(main._ytm_song_search("Band Song"))
            b = self.run_async(main._ytm_song_search("band song "))
            self.assertEqual(a[0]["url"], b[0]["url"])
            self.assertEqual(calls, ["Band Song"])
            # Leeres Ergebnis (evtl. kein Netz) wird nicht gemerkt
            self.run_async(main._ytm_song_search("nichts"))
            self.run_async(main._ytm_song_search("nichts"))
            self.assertEqual(calls.count("nichts"), 2)
            # Ueberlebt einen Neustart
            main._save_ytm_cache()
            main._ytm_cache = None
            self.run_async(main._ytm_song_search("Band Song"))
            self.assertEqual(calls.count("Band Song"), 1)
        finally:
            main._ytm_songs = orig
