"""Musikvideos im Bestand: Ordner durchsuchen, Song-Fassung vorschlagen,
nach dem Ersetzen Datei und Titel ohne "(Official Video)"."""
import json
import os

from tests.support import BackendTest, main
from synthimix import core, quality, search


class VideoReplaceTest(BackendTest):

    def setUp(self):
        super().setUp()
        self.musik = self.tmp / "Musik"
        (self.musik / "Unter").mkdir(parents=True)
        self.files = {}
        for rel in ["A - Eins (Official Video).mp3", "B - Zwei.mp3", "Unter/C - Drei [Official Music Video].mp3",
                    "Unter/D - Vier (Official Video)_bass.mp3", "E - Fuenf M／V.mp3"]:
            p = self.musik / rel
            p.write_bytes(b"x")
            self.files[rel] = str(p)
        main._state["library"] = [{"path": p, "title": os.path.splitext(os.path.basename(p))[0], "artist": "", "duration_sec": 200}
                                  for p in self.files.values()]
        main._state["library"].append({"path": str(self.musik / "weg (Official Video).mp3"), "title": "weg (Official Video)", "missing": True})
        main._state["queue"] = [{"path": self.files["A - Eins (Official Video).mp3"], "title": "A - Eins (Official Video)"}]

    def test_namen_bereinigen(self):
        for alt, neu in [("SKRILLEX - Bangarang feat. Sirah [Official Music Video]", "SKRILLEX - Bangarang feat. Sirah"),
                         ("A - B (Official Video) [Label]", "A - B [Label]"),
                         ("A - B (Krot Remix) - Official Video", "A - B (Krot Remix)"),
                         ("Captain Jack - Captain Jack (Official Video 1995)", "Captain Jack - Captain Jack"),
                         ("A - B (offizielles Musikvideo)", "A - B"),
                         ("Tour de France (Live)", "Tour de France (Live)"),       # kein Video-Zusatz: bleibt
                         ("MV", "MV")]:                                              # nie leer
            self.assertEqual(quality.clean_name(alt), neu)

    def test_ordner_durchsuchen_mit_und_ohne_unterordner(self):
        ohne = [os.path.basename(p) for p in quality.video_scan(str(self.musik), False)]
        self.assertEqual(ohne, ["A - Eins (Official Video).mp3", "E - Fuenf M／V.mp3"])
        mit = [os.path.basename(p) for p in quality.video_scan(str(self.musik), True)]
        self.assertEqual(mit, ["A - Eins (Official Video).mp3", "E - Fuenf M／V.mp3", "C - Drei [Official Music Video].mp3"])
        # Einzelspuren (Stems), fehlende Dateien und normale Titel sind nicht dabei

    def test_suche_schlaegt_die_song_fassung_vor(self):
        sent = []

        class WS:
            async def send_text(self, s):
                sent.append(json.loads(s))

        async def finde(video):
            return {"url": "u", "title": "Eins", "artist": "A", "duration": 240, "kind": "song"} if "Eins" in video["title"] else None
        echt = search._find_song_version
        search._find_song_version = finde
        try:
            paths = [self.files["A - Eins (Official Video).mp3"], self.files["E - Fuenf M／V.mp3"]]
            self.run_async(quality._quality_batch(paths, WS(), "video"))
        finally:
            search._find_song_version = echt
        items = {m["path"]: m for m in sent if m["type"] == "quality_batch_item"}
        a = items[paths[0]]
        self.assertEqual(a["candidate"]["url"], "u")
        self.assertTrue(a["sure"])
        self.assertEqual(a["new_name"], "A - Eins")
        self.assertIsNone(items[paths[1]]["candidate"])
        self.assertEqual(sent[-1]["type"], "quality_batch_done")

    def test_umbenennen_zieht_alles_mit(self):
        alt = self.files["A - Eins (Official Video).mp3"]
        core.PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
        pl = core.PLAYLISTS_DIR / "Set.m3u"
        pl.write_text("\n".join(["#EXTM3U", alt, ""]), encoding="utf-8")
        neu = quality._rename_clean(alt)
        self.assertEqual(os.path.basename(neu), "A - Eins.mp3")
        self.assertTrue(os.path.exists(neu) and not os.path.exists(alt))
        lt = next(t for t in main._state["library"] if t["path"] == neu)
        self.assertEqual(lt["title"], "A - Eins")
        self.assertEqual(main._state["queue"][0], {"path": neu, "title": "A - Eins"})
        pl = core.PLAYLISTS_DIR / "Set.m3u"          # die Playlists wohnen im Download-Ordner
        self.assertIn(neu, pl.read_text(encoding="utf-8"))
        self.assertNotIn(alt, pl.read_text(encoding="utf-8"))

    def test_umbenennen_ueberschreibt_nichts(self):
        alt = self.files["A - Eins (Official Video).mp3"]
        (self.musik / "A - Eins.mp3").write_bytes(b"schon da")
        neu = quality._rename_clean(alt)
        self.assertEqual(os.path.basename(neu), "A - Eins (2).mp3")
        self.assertEqual((self.musik / "A - Eins.mp3").read_bytes(), b"schon da")
