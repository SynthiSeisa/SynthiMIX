"""_acoustid_identify mit nachgebautem fpcalc und AcoustID (kein Netz)."""
import asyncio
import io
import json
import urllib.request

from tests.support import BackendTest, main


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class AcoustidFlowTest(BackendTest):

    def setUp(self):
        super().setUp()
        fake = self.tmp / "fpcalc.bat"
        fake.write_text('@echo {"fingerprint":"AQAAtest","duration":200}\n', "ascii")
        self._keep = (main._find_fpcalc, urllib.request.urlopen, main._state.get("acoustid_api_key"))
        main._find_fpcalc = lambda: str(fake)
        main._state["acoustid_api_key"] = "test"
        self.urls = []
        self.answer = {}

        def urlopen(req, timeout=10):
            u = req.full_url if hasattr(req, "full_url") else req
            self.urls.append(u)
            return _Resp(json.dumps(self.answer(u) if callable(self.answer) else self.answer).encode())
        urllib.request.urlopen = urlopen
        self.song = self.tmp / "song.mp3"
        self.song.write_bytes(b"x")

    def tearDown(self):
        main._find_fpcalc, urllib.request.urlopen, main._state["acoustid_api_key"] = self._keep
        super().tearDown()

    def test_treffer_mit_grenzen(self):
        self.answer = {"status": "ok", "results": [{"score": 0.93, "recordings": [
            {"title": "Levels", "artists": [{"name": "Avicii"}], "releasegroups": [{"title": "Levels"}]}]}]}

        async def go():
            sem = asyncio.Semaphore(2)
            r = await main._acoustid_identify(str(self.song), text_fallback=False, fp_sem=sem,
                                              api_gate=main._RateGate(3), mb_gate=main._RateGate(1))
            return r, sem._value
        r, frei = self.run_async(go())
        self.assertEqual((r["title"], r["artist"], r["via"]), ("Levels", "Avicii", "AcoustID"))
        self.assertEqual(frei, 2)                                  # fpcalc-Platz wieder frei
        self.assertEqual(len(self.urls), 1)

    def test_ohne_textsuche(self):
        # Treffer ohne Metadaten: im Sammel-Modus keine MusicBrainz-/Last.fm-Suche
        def answer(u):
            if "acoustid" in u:
                return {"status": "ok", "results": [{"score": 0.9, "recordings": []}]}
            if "musicbrainz" in u:
                return {"recordings": []}
            return {"results": {"trackmatches": {"track": []}}}      # Last.fm
        self.answer = answer
        main._state["library"] = [{"path": str(self.song), "title": "Levels", "artist": "Avicii"}]
        main._state["lastfm_api_key"] = "x"
        r = self.run_async(main._acoustid_identify(str(self.song), text_fallback=False))
        self.assertEqual(r["error"], "Kein Match bei AcoustID gefunden")
        self.assertEqual(len(self.urls), 1)
        # Einzel-Erkennung (Standard) sucht weiter wie bisher
        self.urls.clear()
        self.run_async(main._acoustid_identify(str(self.song)))
        self.assertGreater(len(self.urls), 1)
