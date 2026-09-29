"""Fingerprint-Erkennung fuer mehrere Titel: Vorschlagsliste statt Schreiben."""
from tests.support import BackendTest, FakeWS, main


class FingerprintBatchTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._keep = (main._acoustid_identify, main._find_fpcalc, main._state.get("acoustid_api_key"))
        main._state["acoustid_api_key"] = "test"
        main._find_fpcalc = lambda: "fpcalc.exe"

    def tearDown(self):
        main._acoustid_identify, main._find_fpcalc, main._state["acoustid_api_key"] = self._keep
        super().tearDown()

    def _lib(self, *specs):
        lib = []
        for name, title, artist in specs:
            p = self.tmp / f"{name}.mp3"
            p.write_bytes(b"x")
            lib.append({"path": str(p), "title": title, "artist": artist})
        main._state["library"] = lib
        return [lt["path"] for lt in lib]

    def test_vorschlaege(self):
        paths = self._lib(("a", "track01", ""), ("b", "Strobe", "deadmau5"),
                          ("c", "Song (VIP Mix)", "Band"), ("d", "Unbekannt", ""))
        antworten = {
            paths[0]: {"title": "Levels", "artist": "Avicii", "score": 0.97, "via": "AcoustID"},
            paths[1]: {"title": "Strobe", "artist": "Deadmau5", "score": 0.95, "via": "AcoustID"},
            paths[2]: {"title": "Song", "artist": "Band", "score": 0.93, "via": "AcoustID"},
            paths[3]: {"error": "Kein Match bei AcoustID gefunden"},
        }

        async def fake(path, **kw):
            self.assertFalse(kw.get("text_fallback", True))       # Sammel-Modus: keine Textsuchen
            return antworten[path]
        main._acoustid_identify = fake
        ws = FakeWS()
        self.run_async(main._fingerprint_suggest(ws, paths))
        res = ws.of_type("title_suggestions")[-1]
        by = {it["path"]: it for it in res["items"]}
        self.assertEqual(set(by), {paths[0], paths[2]})
        self.assertTrue(by[paths[0]]["sure"])
        self.assertEqual(by[paths[0]]["source"], "97 %")
        self.assertFalse(by[paths[2]]["sure"])        # VIP Mix ginge verloren
        self.assertEqual((res["same"], res["nomatch"], res["checked"]), (1, 1, 4))
        # Es wurde nichts geschrieben
        self.assertEqual(main._state["library"][0]["title"], "track01")

    def test_ohne_key(self):
        main._state["acoustid_api_key"] = ""
        ws = FakeWS()
        self.run_async(main._fingerprint_suggest(ws, self._lib(("a", "x", ""))))
        res = ws.of_type("title_suggestions")[-1]
        self.assertIn("AcoustID-Key", res["error"])
        self.assertEqual(res["fix_tab"], "services")

    def test_ergebnisse_gemerkt(self):
        paths = self._lib(("a", "track01", ""), ("b", "Unbekannt", ""))
        calls = []

        async def fake(path, **kw):
            calls.append(path)
            return {"title": "Levels", "artist": "Avicii", "score": 0.97, "via": "AcoustID"}                 if path == paths[0] else {"error": "Kein Match bei AcoustID gefunden"}
        main._acoustid_identify = fake
        self.run_async(main._fingerprint_suggest(FakeWS(), paths))
        ws = FakeWS()
        self.run_async(main._fingerprint_suggest(ws, paths))
        self.assertEqual(len(calls), 2)                           # zweiter Lauf: nichts neu erkannt
        res = ws.of_type("title_suggestions")[-1]
        self.assertEqual(res["cached"], 2)
        self.assertEqual([it["title"] for it in res["items"]], ["Levels"])
        self.assertEqual(res["nomatch"], 1)

    def test_takt_fuer_anfragen(self):
        import asyncio, time

        async def go():
            gate = main._RateGate(10)
            t0 = time.monotonic()
            await asyncio.gather(*(gate.wait() for _ in range(6)))
            return time.monotonic() - t0
        self.assertGreater(self.run_async(go()), 0.45)           # 6 Anfragen bei 10/s: >= 0,5 s
