"""Radio: passende Titel aus der Bibliothek, Richtung aus den letzten Titeln, ab und zu Neues."""
import random
import time
from collections import Counter

from synthimix import automix
from tests.support import BackendTest, main


def lt(name, artist, key="", bpm=0, genre="", dur=240):
    return {"path": f"{name}.mp3", "title": name, "artist": artist, "key": key, "bpm": bpm,
            "genre": genre, "duration_sec": dur, "lufs": -9.0}


class RadioTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._saved_radio = {k: main._state.get(k) for k in ("radio_enabled", "lastfm_api_key", "auto_mix")}
        self._lfm, self._discover = automix._lfm, automix._discover
        self.lfm = {}
        self.discovered = 0

        async def fake_lfm(kind, artist, title=""):
            return self.lfm.get((kind, artist.lower(), title.lower()), [])

        async def fake_discover():
            self.discovered += 1

        automix._lfm, automix._discover = fake_lfm, fake_discover
        main._state["radio_enabled"] = False
        automix._since_new, automix._since_boost, automix._boost_at = 0, 0, 5
        automix._rng.seed(3)

    def tearDown(self):
        automix._lfm, automix._discover = self._lfm, self._discover
        main._state.update(self._saved_radio)
        super().tearDown()

    def set_queue(self, seeds):
        main._state["library"] = list(main._state["library"]) + seeds
        main._state["queue"] = [automix._queue_entry(s) for s in seeds]
        main._state["current_idx"] = len(seeds) - 1

    def picks(self, n=40):
        seeds = automix._seed_tracks()
        prof = self.run_async(automix._profile(seeds))
        bp, bt = automix._blocked()
        qp = {t["path"] for t in main._state["queue"]}
        return Counter(automix._pick_sync(main._state["library"], prof, bp, bt, qp, False, random.Random(i))[0]["title"]
                       for i in range(n))

    def test_bleibt_in_genre_und_tempo(self):
        seeds = [lt(f"seed{i}", f"S{i}", "Am", 174, "Drum & Bass") for i in range(3)]
        main._state["library"] = [lt("dnb_passt", "D", "Em", 174, "Drum and Bass"),      # 9A, Nachbar
                                  lt("schlager", "E", "Am", 100, "Schlager"),
                                  lt("house", "F", "Am", 126, "House"),
                                  lt("dnb_schief", "G", "F♯", 175, "Drum & Bass")]
        self.set_queue(seeds)
        c = self.picks()
        self.assertGreaterEqual(c["dnb_passt"], 36, c)
        self.assertEqual(c["schlager"] + c["house"], 0, c)

    def test_keine_wiederholung_kein_gleicher_kuenstler(self):
        seeds = [lt("Song X", "S1", "Am", 128, "House"), lt("Letzter", "Kuenstler A", "Am", 128, "House")]
        main._state["library"] = [lt("Noch einer", "Kuenstler A", "Am", 128, "House"),      # gleicher Kuenstler
                                  lt("Gerade gelaufen", "B", "Am", 128, "House"),
                                  lt("Song X (Radio Edit)", "S1", "Am", 128, "House"),     # andere Fassung
                                  lt("Frisch", "C", "Em", 127, "House")]
        main._state["play_log"] = [{"path": "Gerade gelaufen.mp3", "title": "Gerade gelaufen", "artist": "B",
                                    "played_at": int(time.time()) - 600}]
        self.set_queue(seeds)
        self.assertEqual(self.picks(20), Counter({"Frisch": 20}))

    def test_lastfm_findet_titel_in_anderer_schreibweise(self):
        seeds = [lt("Seed Song", "Seed Artist")]
        self.lfm[("track", "seed artist", "seed song")] = [("Sim Artist", "Hidden Gem", 1.0)]
        main._state["library"] = [{"path": "gem.mp3", "title": "Sim Artist - Hidden Gem (Original Mix)",
                                   "duration_sec": 250, "lufs": -9.0}] + \
                                 [lt(f"irgendwas{i}", f"X{i}") for i in range(10)]
        self.set_queue(seeds)
        c = self.picks()
        self.assertGreaterEqual(c["Sim Artist - Hidden Gem (Original Mix)"], 36, c)

    def test_aehnliche_kuenstler_zaehlen(self):
        seeds = [lt("Seed Song", "Seed Artist")]
        self.lfm[("artist", "seed artist", "")] = [("Nah Dran", "", 0.9)]
        main._state["library"] = [lt("Titel", "Nah Dran")] + [lt(f"fremd{i}", f"Y{i}") for i in range(10)]
        self.set_queue(seeds)
        self.assertGreaterEqual(self.picks()["Titel"], 34)

    def test_haelt_drei_vorraetig_und_jeder_fuenfte_ist_neu(self):
        seeds = [lt("start", "S", "Am", 128, "House")]
        main._state["library"] = [lt(f"t{i}", f"K{i}", "Am", 128, "House") for i in range(20)]
        self.set_queue(seeds)
        main._state["radio_enabled"] = True
        self.run_async(automix._fill(automix._AHEAD - automix._remaining()))
        self.assertEqual(automix._remaining(), 3)
        self.run_async(automix._fill(7))
        paths = [t["path"] for t in main._state["queue"]]
        self.assertEqual(len(paths), 11)
        self.assertEqual(len(set(paths)), 11)            # nichts doppelt
        self.assertEqual(self.discovered, 2)             # vor dem 5. und dem 9. Titel

    def test_queue_ende_haengt_einen_an(self):
        seeds = [lt("start", "S", "Am", 128)]
        main._state["library"] = [lt(f"t{i}", f"K{i}", "Am", 128) for i in range(5)]
        self.set_queue(seeds)
        self.run_async(automix._at_queue_end())
        self.assertEqual(len(main._state["queue"]), 2)
        self.run_async(automix._at_queue_end())          # es kommt noch einer: nichts tun
        self.assertEqual(len(main._state["queue"]), 2)

    def test_ohne_passendes_nur_neues(self):
        self.set_queue([lt("start", "S")])
        self.run_async(automix._fill(1))
        self.assertEqual(len(main._state["queue"]), 1)
        self.assertEqual(self.discovered, 1)

    def test_keine_spuren_und_samples(self):
        seeds = [lt("start", "S", "Am", 128, "House")]
        main._state["library"] = [lt(n, "K", "Am", 128, "House") for n in (
            "K - Lied_bass", "K - Lied (vocals)", "K - Lied_other", "1-Drums (No Kick)", "Lied - Wet",
            "looperman-a-123-loop", "K - Lied (Instrumental)")] + [lt("Liquid Bass", "K2", "Am", 128, "House")]
        self.set_queue(seeds)
        self.assertEqual(self.picks(10), Counter({"Liquid Bass": 10}))

    def test_suchtreffer_muss_passen(self):
        r = {"title": "Hidden Gem", "artist": "Sim Artist", "uploader": "Sim Artist", "url": "u"}
        self.assertTrue(automix._matches(r, "Sim Artist", "Hidden Gem"))
        self.assertTrue(automix._matches(r, "Sim Artist feat. Z", "Hidden Gem (Original Mix)"))
        self.assertFalse(automix._matches(r, "Anderer", "Hidden Gem"))
        self.assertFalse(automix._matches(r, "Sim Artist", "Ganz anders"))

    def test_kuenstler_und_titel_trennen(self):
        self.assertEqual(automix._split({"title": "A & B - Lied", "artist": ""}), ("A & B", "Lied"))
        self.assertEqual(automix._split({"title": "A - Lied", "artist": "A"}), ("A", "Lied"))
        self.assertEqual(automix._split({"title": "Lied - Teil 2", "artist": "C"}), ("C", "Lied - Teil 2"))
        self.assertEqual(automix._artists("A & B feat. C"), frozenset({"a", "b", "c"}))
