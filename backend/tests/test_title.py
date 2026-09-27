"""Titel aufraeumen: lokale Regeln, Schreiben nur von Titel- und Kuenstler-Tag."""
from tests.support import BackendTest, FakeWS, main


class TitleRulesTest(BackendTest):

    CASES = [
        ("Bruno Mars - When I Was Your Man (Official Music Video)", "", ("Bruno Mars", "When I Was Your Man")),
        ("AC⧸DC - Thunderstruck (Official Video)", "", ("AC/DC", "Thunderstruck")),
        ("'Champagne' by Cavo - Official Music Video", "", ("Cavo", "Champagne")),
        ("Sota - See The Sun | Interaction | Bassrush Records", "", ("Sota", "See The Sun")),
        ("[Bass House] Lektrique X Autodidakt - Shots Fired", "", ("Lektrique X Autodidakt", "Shots Fired")),
        ("Vance Joy - 'Riptide' Official Video", "VanceJoyVEVO", ("Vance Joy", "Riptide")),
        ("Rise Against - Satellite (Rock)", "", ("Rise Against", "Satellite")),
        ("ShockOne - Pray For Me [Monstercat LP Release]", "", ("ShockOne", "Pray For Me")),
        ("Tchami - Praise (ft. Gunna)", "", ("Tchami", "Praise (feat. Gunna)")),
        ("Pursuit of Happiness (Steve Aoki Remix) - Kid Cudi AUDIO", "", ("Pursuit of Happiness (Steve Aoki Remix)", "Kid Cudi")),
    ]
    KEEP = [
        "Meiko - Leave The Lights On (Krot Remix)",
        "Notion - Real (feat. Cecelia) [VIP Mix]",
        "Hybrid Minds - Touch (Original Mix)",
        "Milky Chance - Colorado (Acapella)",
    ]

    def test_regeln(self):
        for title, artist, want in self.CASES:
            with self.subTest(title=title):
                self.assertEqual(main._clean_title_local(title, artist), want)

    def test_echte_angaben_bleiben(self):
        for title in self.KEEP:
            with self.subTest(title=title):
                a, t = main._clean_title_local(title, "")
                self.assertEqual(a + " - " + t, title)

    def test_titel_ohne_kuenstler_behaelt_tag(self):
        self.assertEqual(main._clean_title_local("Serotonin (Lyrics)", "girl in red"), ("girl in red", "Serotonin"))

    def test_vertauscht_ist_unsicher(self):
        main._state["library"] = [{"path": "a.mp3", "title": "Pursuit of Happiness (Steve Aoki Remix) - Kid Cudi"},
                                  {"path": "b.mp3", "title": "Bruno Mars - Grenade (Official Video)"}]
        ws = FakeWS()
        self.run_async(main._title_suggest(ws))
        items = {it["path"]: it for it in ws.of_type("title_suggestions")[0]["items"]}
        self.assertFalse(items["a.mp3"]["sure"])
        self.assertTrue(items["b.mp3"]["sure"])


class TitleWriteTest(BackendTest):

    def test_nur_titel_und_kuenstler(self):
        from mutagen.id3 import ID3, TKEY
        f = self.make_audio(self.tmp / "t.mp3", seconds=2, tones=[(440, 0.5)],
                            tags={"title": "Bruno Mars - Grenade (Official Video)", "artist": "BrunoMarsVEVO", "genre": "Pop"})
        t = ID3(str(f)); t.add(TKEY(encoding=3, text="Dm")); t.save(str(f))
        main._state["library"] = [{"path": str(f), "title": "Bruno Mars - Grenade (Official Video)", "artist": "BrunoMarsVEVO"}]

        async def nop():
            pass
        alt = (main.push_library, main.push_queue)
        main.push_library = main.push_queue = nop
        ws = FakeWS()
        try:
            self.run_async(main._title_apply([{"path": str(f), "title": "Grenade", "artist": "Bruno Mars"}], ws))
        finally:
            main.push_library, main.push_queue = alt
        t = ID3(str(f))
        self.assertEqual((str(t["TIT2"]), str(t["TPE1"]), str(t["TCON"]), str(t["TKEY"])), ("Grenade", "Bruno Mars", "Pop", "Dm"))
        self.assertEqual(main._state["library"][0]["title"], "Grenade")
        self.assertEqual(ws.of_type("title_applied")[0]["ok"], 1)
