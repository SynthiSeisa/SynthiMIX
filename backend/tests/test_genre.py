"""Genres: Schreibweisen vereinheitlichen, fehlende vorschlagen, in Dateien schreiben.

Last.fm und Spotify werden ersetzt; geprueft werden Zuordnung, Reihenfolge
der Quellen (Vereinheitlichen, Ordner-Regel, Last.fm) und dass beim Schreiben
nur das Genre-Feld der Datei geaendert wird.
"""
from tests.support import BackendTest, FakeWS, main


class GenreMapTest(BackendTest):

    def test_schreibweisen_werden_ein_hauptgenre(self):
        for raw in ("Drum & Bass", "Drum and Bass", "drum & Bass", "Jungle/Drum'n'Bass", "drum n bass", "Neuro", "Liquid Funk"):
            with self.subTest(raw=raw):
                self.assertEqual(main._genre_map(raw), "Drum & Bass")
        self.assertEqual(main._genre_map("Bass House"), "House")
        self.assertEqual(main._genre_map("Dance & DJ"), "EDM / Dance")
        self.assertEqual(main._genre_map("Rap & Hip-Hop"), "Hip-Hop / Rap")
        self.assertEqual(main._genre_map("post-hardcore"), "Rock / Metal")
        self.assertEqual(main._genre_map("dance pop"), "Pop")
        self.assertEqual(main._genre_map("Mallorca Hits"), "Schlager / Party")
        self.assertIsNone(main._genre_map("Musik Algemein"))

    def test_lastfm_tags_mehrheit_und_anteil(self):
        g, share = main._genre_from_tags([("drum and bass", 100), ("liquid funk", 60), ("electronic", 30), ("seen live", 20)])
        self.assertEqual(g, "Drum & Bass")
        self.assertGreater(share, 0.8)
        self.assertEqual(main._genre_from_tags([("favorites", 100)]), (None, 0.0))

    def test_kuenstler_und_titel_aus_dem_dateititel(self):
        lt = {"title": "Vance Joy - 'Riptide' (Official Video)", "artist": "VanceJoyVEVO", "path": "x.mp3"}
        self.assertEqual(main._artist_title(lt), ("Vance Joy", "'Riptide'"))
        self.assertEqual(main._artist_title({"title": "Serotonin", "artist": "girl in red", "path": "x.mp3"}),
                         ("girl in red", "Serotonin"))


class GenreSuggestTest(BackendTest):

    def setUp(self):
        super().setUp()
        L = self.tmp / "Musik"
        main._state["library"] = [
            {"path": str(L / "Liquid" / "a.mp3"), "title": "A - Eins", "genre": "", "duration_sec": 200},
            {"path": str(L / "Allgemein" / "b.mp3"), "title": "B - Zwei", "genre": "Music", "duration_sec": 200},
            {"path": str(L / "Allgemein" / "c.mp3"), "title": "C - Drei", "genre": "drum n bass", "duration_sec": 200},
            {"path": str(L / "Allgemein" / "d.mp3"), "title": "D - Vier", "genre": "Pop", "duration_sec": 200},
            {"path": str(L / "stems" / "e.mp3"), "title": "E - Fuenf (Vocals)", "genre": "", "duration_sec": 200},
            {"path": str(L / "FX" / "f.wav"), "title": "Riser", "genre": "", "duration_sec": 6},
        ]
        main._state["lastfm_api_key"] = "key"
        main._state["spotify_client_id"] = ""
        self._alt = main._lfm_tags_sync
        self.calls = []

        def fake(method, key, artist, title=""):
            self.calls.append((method, artist, title))
            return [("house", 100), ("deep house", 40)] if artist == "B" else []
        main._lfm_tags_sync = fake

    def tearDown(self):
        main._lfm_tags_sync = self._alt
        main._state["lastfm_api_key"] = ""
        super().tearDown()

    def _run(self, rules=None):
        if rules is not None:
            main._genre_rules_save(rules)
        ws = FakeWS()
        self.run_async(main._genre_suggest(ws))
        return {it["path"].split("\\")[-1].split("/")[-1]: it for it in ws.of_type("genre_suggestions")[0]["items"]}, ws

    def test_quellen_in_der_richtigen_reihenfolge(self):
        liquid = str(self.tmp / "Musik" / "Liquid")
        items, ws = self._run({liquid: "Drum & Bass"})
        self.assertEqual((items["a.mp3"]["genre"], items["a.mp3"]["source"]), ("Drum & Bass", "Ordner"))
        self.assertEqual((items["b.mp3"]["genre"], items["b.mp3"]["source"]), ("House", "Last.fm"))
        self.assertTrue(items["b.mp3"]["sure"])
        self.assertEqual((items["c.mp3"]["genre"], items["c.mp3"]["source"]), ("Drum & Bass", "Vereinheitlicht"))
        self.assertNotIn("d.mp3", items)          # hat schon ein Hauptgenre
        self.assertNotIn("e.mp3", items)          # Stems nicht
        self.assertNotIn("f.wav", items)          # Samples nicht
        folders = {f["name"]: f for f in ws.of_type("genre_suggestions")[0]["folders"]}
        self.assertEqual(folders["Liquid"]["auto"], "Drum & Bass")

    def test_ordnername_gilt_ohne_regel_und_keine_regel_schaltet_ab(self):
        items, _ = self._run()
        self.assertEqual((items["a.mp3"]["genre"], items["a.mp3"]["source"]), ("Drum & Bass", "Ordner"))
        items, _ = self._run({str(self.tmp / "Musik" / "Liquid"): ""})
        self.assertNotEqual(items.get("a.mp3", {}).get("source"), "Ordner")

    def test_online_antworten_werden_zwischengespeichert(self):
        self._run()
        n = len(self.calls)
        self._run()
        self.assertEqual(len(self.calls), n)


class GenreWriteTest(BackendTest):

    def test_nur_das_genre_feld_aendert_sich(self):
        from mutagen.id3 import ID3, TKEY, COMM
        f = self.make_audio(self.tmp / "t.mp3", seconds=2, tones=[(440, 0.5)],
                            tags={"title": "Titel", "artist": "Kuenstler", "genre": "Music"})
        t = ID3(str(f)); t.add(TKEY(encoding=3, text="8A")); t.add(COMM(encoding=3, lang="eng", desc="", text="Energy 7")); t.save(str(f))
        self.assertTrue(main._write_genre_sync(str(f), "Drum & Bass"))
        t = ID3(str(f))
        self.assertEqual(str(t["TCON"]), "Drum & Bass")
        self.assertEqual(str(t["TIT2"]), "Titel")
        self.assertEqual(str(t["TKEY"]), "8A")
        self.assertEqual(t.getall("COMM")[0].text[0], "Energy 7")

    def test_uebernehmen_schreibt_datei_und_bibliothek_ohne_geladenen_titel(self):
        a = self.make_audio(self.tmp / "a.mp3", seconds=2, tones=[(440, 0.5)])
        b = self.make_audio(self.tmp / "b.mp3", seconds=2, tones=[(440, 0.5)])
        main._state["library"] = [{"path": str(a), "title": "A", "genre": ""}, {"path": str(b), "title": "B", "genre": ""}]
        main._state["queue"] = [{"path": str(b)}]
        main._state["current_idx"] = 0

        async def nop():
            pass
        alt = main.push_library
        main.push_library = nop
        ws = FakeWS()
        try:
            self.run_async(main._genre_apply([{"path": str(a), "genre": "House"}, {"path": str(b), "genre": "House"},
                                              {"path": str(a), "genre": "Kein Genre"}], ws))
        finally:
            main.push_library = alt
        done = ws.of_type("genre_applied")[0]
        self.assertEqual((done["ok"], done["skipped"]), (1, 1))
        self.assertEqual(main._state["library"][0]["genre"], "House")
        self.assertEqual(main._state["library"][1]["genre"], "")
