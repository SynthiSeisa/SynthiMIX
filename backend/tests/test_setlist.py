"""DJ-Set: rekordbox-Playlist einlesen, Lautheit in Set-Reihenfolge, zurueck als .m3u8."""
from pathlib import Path

from tests.support import BackendTest, FakeWS, main
from synthimix import core, media, setlist


class SetlistTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._keep = (core.PLAYLISTS_DIR, media._measure_loudness_sync)
        core.PLAYLISTS_DIR = self.tmp / "Downloads" / "playlists"
        setlist._measured.clear()
        self.musik = self.tmp / "Musik"
        self.musik.mkdir()

    def tearDown(self):
        core.PLAYLISTS_DIR, media._measure_loudness_sync = self._keep
        super().tearDown()

    def track(self, name, lufs=None, in_lib=True, artist="Sido"):
        p = self.musik / name
        p.write_bytes(b"ID3" + name.encode())
        if in_lib:
            main._state["library"].append({"path": str(p), "title": Path(name).stem, "artist": artist,
                                           "duration_sec": 200, **({"lufs": lufs} if lufs is not None else {"lufs": -99.0})})
        return str(p)

    def rekordbox_datei(self, zeilen, name="Mein Set.m3u8", encoding="utf-8"):
        f = self.tmp / name
        f.write_text("#EXTM3U\n" + "\n".join(zeilen) + "\n", encoding=encoding)
        return str(f)

    def test_einlesen_reihenfolge_und_fehlende(self):
        main._state["library"] = []
        a = self.track("Über.mp3", -8.0)
        b = self.track("Zwei.mp3", -12.0)
        c = self.track("Fremd.mp3", in_lib=False)
        src = self.rekordbox_datei(["#EXTINF:200,Sido - Zwei", b, "#EXTINF:210,Sido - Über", a.upper(),
                                    "#EXTINF:1,weg", str(self.musik / "gibt es nicht.mp3"), c])
        res = setlist.import_playlist(src)
        self.assertTrue(res["ok"])
        self.assertEqual((res["name"], res["total"], res["missing_n"], res["unknown"]), ("Mein Set", 3, 1, 1))
        zeilen = [ln for ln in Path(res["path"]).read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
        # Set-Reihenfolge bleibt; der Pfad in der Schreibweise der Bibliothek
        self.assertEqual(zeilen, [b, a, c])
        # gleicher Name noch einmal: nichts ueberschreiben
        self.assertEqual(setlist.import_playlist(src)["name"], "Mein Set (2)")

    def test_alte_m3u_und_file_adressen(self):
        main._state["library"] = []
        a = self.track("Größe.mp3", -9.0)
        src = self.rekordbox_datei([a], name="alt.m3u", encoding="cp1252")
        self.assertEqual(setlist.import_playlist(src)["total"], 1)
        url = "file:///" + a.replace("\\", "/").replace(" ", "%20").replace("ö", "%C3%B6").replace("ß", "%C3%9F")
        rel = self.rekordbox_datei([url, "Musik/Größe.mp3"], name="adressen.m3u8")
        self.assertEqual(setlist.import_playlist(rel)["total"], 2)

    def test_nichts_gefunden_und_falsche_datei(self):
        src = self.rekordbox_datei([str(self.musik / "weg.mp3")])
        res = setlist.import_playlist(src)
        self.assertFalse(res["ok"])
        self.assertIn("nicht", res["error"])
        self.assertFalse(setlist.import_playlist(str(self.tmp / "x.txt"))["ok"])
        self.assertFalse(list(core.PLAYLISTS_DIR.glob("*.m3u")) if core.PLAYLISTS_DIR.exists() else [])

    def test_lautheit_bekannte_sofort_unbekannte_werden_gemessen(self):
        main._state["library"] = []
        a = self.track("A.mp3", -8.0)
        b = self.track("B.mp3")                       # in der Bibliothek, noch nie gemessen
        c = self.track("C.mp3", in_lib=False)         # nur im Set
        res = setlist.import_playlist(self.rekordbox_datei([a, b, c]))
        gemessen = []

        def messen(path):
            gemessen.append(Path(path).name)
            return (-11.5, -10.0) if path == b else (-14.0, None)
        media._measure_loudness_sync = messen
        ws = FakeWS()
        self.run_async(setlist.loudness(res["path"], ws))
        erste = ws.sent[0]
        self.assertEqual(erste["type"], "playlist_loudness")
        self.assertEqual([(Path(t["path"]).name, t["lufs"], t["in_library"]) for t in erste["tracks"]],
                         [("A.mp3", -8.0, True), ("B.mp3", None, True), ("C.mp3", None, False)])
        self.assertEqual(erste["measuring"], 2)
        self.assertEqual(sorted(gemessen), ["B.mp3", "C.mp3"])
        einzeln = {Path(m["track"]).name: m["lufs"] for m in ws.sent if m["type"] == "playlist_loudness_one"}
        self.assertEqual(einzeln, {"B.mp3": -11.5, "C.mp3": -14.0})
        self.assertEqual(ws.sent[-1]["type"], "playlist_loudness_done")
        self.assertEqual(main._state["library"][1]["lufs"], -11.5)          # in der Bibliothek gemerkt
        # zweites Oeffnen: nichts mehr zu messen, auch nicht der Titel ausserhalb der Bibliothek
        gemessen.clear()
        ws2 = FakeWS()
        self.run_async(setlist.loudness(res["path"], ws2))
        self.assertEqual((ws2.sent[0]["measuring"], gemessen), (0, []))
        self.assertEqual(ws2.sent[0]["tracks"][2]["lufs"], -14.0)

    def test_ausgabe_fuer_rekordbox(self):
        main._state["library"] = []
        a = self.track("Über.mp3", -8.0)
        b = self.track("Zwei.mp3", -12.0, artist="")
        res = setlist.import_playlist(self.rekordbox_datei([b, a]))
        out = setlist.export_playlist(res["path"])
        self.assertTrue(out["ok"])
        dest = Path(out["path"])
        self.assertEqual((dest.parent.name, dest.name, out["total"]), ("rekordbox", "Mein Set.m3u8", 2))
        text = dest.read_bytes().decode("utf-8")
        self.assertEqual([ln for ln in text.splitlines() if ln], ["#EXTM3U", "#EXTINF:200,Zwei", b, "#EXTINF:200,Sido - Über", a])
        # die Ausgabe laesst sich wieder einlesen (hin und zurueck)
        zurueck, fehlt = setlist.parse_playlist_file(str(dest))
        self.assertEqual(([t["path"] for t in zurueck], fehlt), ([b, a], []))
        self.assertFalse(setlist.export_playlist(str(self.tmp / "fehlt.m3u"))["ok"])


class NewPlaylistTest(BackendTest):
    """Gemeldet 10/2026: eine neu angelegte Playlist enthielt gleich die ganze Warteschlange (271 Titel)."""

    def setUp(self):
        super().setUp()
        self._dir = core.PLAYLISTS_DIR
        core.PLAYLISTS_DIR = self.tmp / "Downloads" / "playlists"

    def tearDown(self):
        core.PLAYLISTS_DIR = self._dir
        super().tearDown()

    def zeilen(self, name):
        return [ln for ln in (core.PLAYLISTS_DIR / name).read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]

    def test_neue_playlist_ist_leer_und_ueberschreibt_nichts(self):
        a = self.tmp / "a.mp3"
        a.write_bytes(b"x")
        main._state["queue"] = [{"path": str(a), "title": "A", "duration_sec": 10}]
        self.run_async(main.handle_message(FakeWS(), {"type": "save_playlist", "name": "Set", "empty": True}))
        self.assertEqual(self.zeilen("Set.m3u"), [])
        # mit Haken: die Warteschlange kommt hinein (wie bisher)
        self.run_async(main.handle_message(FakeWS(), {"type": "save_playlist", "name": "Voll"}))
        self.assertEqual(self.zeilen("Voll.m3u"), [str(a)])
        # gleicher Name noch einmal leer anlegen: die volle bleibt, eine zweite entsteht
        self.run_async(main.handle_message(FakeWS(), {"type": "save_playlist", "name": "Voll", "empty": True}))
        self.assertEqual(self.zeilen("Voll.m3u"), [str(a)])
        self.assertEqual(self.zeilen("Voll (2).m3u"), [])


class AddToPlaylistTest(BackendTest):
    """Gemeldet 10/2026: mehrere Titel aus der Warteschlange gezogen — nur einer kam an;
    war der Titel schon drin, blieb die Ansicht bei "Lade Playlist…" stehen."""

    def setUp(self):
        super().setUp()
        self._dir = core.PLAYLISTS_DIR
        core.PLAYLISTS_DIR = self.tmp / "Downloads" / "playlists"
        core.PLAYLISTS_DIR.mkdir(parents=True)
        self.pl = core.PLAYLISTS_DIR / "Set.m3u"
        self.pl.write_text("#EXTM3U\n", encoding="utf-8")
        self.files = []
        for n in ("a", "b", "c"):
            p = self.tmp / f"{n}.mp3"
            p.write_bytes(b"x")
            self.files.append(str(p))

    def tearDown(self):
        core.PLAYLISTS_DIR = self._dir
        super().tearDown()

    def add(self, **msg):
        ws = FakeWS()
        self.run_async(main.handle_message(ws, {"type": "playlist_add_track", "playlist": str(self.pl), **msg}))
        return [m for m in ws.sent if m["type"] == "playlist_content"]

    def test_mehrere_auf_einmal_und_immer_eine_antwort(self):
        a, b, c = self.files
        r = self.add(tracks=[{"path": a, "title": "A", "duration_sec": 10}, {"path": b, "title": "B"}])
        self.assertEqual((r[0]["added"], r[0]["already"], [t["path"] for t in r[0]["tracks"]]), (2, 0, [a, b]))
        # einer schon drin, einer neu, einer doppelt in der Auswahl
        r = self.add(tracks=[{"path": b}, {"path": c, "duration_sec": "12.6"}, {"path": c}])
        self.assertEqual((r[0]["added"], r[0]["already"], len(r[0]["tracks"])), (1, 2, 3))
        # alles schon drin: trotzdem eine Antwort mit dem Inhalt (sonst haengt die Ansicht)
        r = self.add(path=a, title="A", duration_sec=10)
        self.assertEqual((len(r), r[0]["added"], r[0]["already"], len(r[0]["tracks"])), (1, 0, 1, 3))
        zeilen = [ln for ln in self.pl.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
        self.assertEqual(zeilen, [a, b, c])
        self.assertEqual(self.add(tracks=[]), [])
