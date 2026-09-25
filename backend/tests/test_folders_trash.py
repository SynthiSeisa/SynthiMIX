"""Beobachtete Ordner verwalten und Loeschen ueber den Papierkorb.

Die Papierkorb-Funktion wird in den normalen Tests durch eine Attrappe
ersetzt: sonst laege nach jedem Build eine Testdatei im Papierkorb. Den echten
Papierkorb prueft test_echter_papierkorb, nur mit SYNTHIMIX_TEST_TRASH=1.
"""
import os
import unittest

from tests.support import BackendTest, FakeWS, main


class TrashAttrappe:
    """Steht fuer _move_to_trash: entfernt die Datei oder scheitert auf Wunsch."""

    def __init__(self, gelingt=True):
        self.gelingt, self.aufrufe = gelingt, []

    def __call__(self, path):
        self.aufrufe.append(path)
        if self.gelingt and os.path.exists(path):
            os.remove(path)
            return True
        return False


class TrashTestBase(BackendTest):

    def setUp(self):
        super().setUp()
        self._echt = main._move_to_trash

    def tearDown(self):
        main._move_to_trash = self._echt
        super().tearDown()

    def datei(self, name="song.mp3"):
        f = self.tmp / "Musik" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        return f


class LoeschenTest(TrashTestBase):

    def test_loeschen_geht_in_den_papierkorb_und_aus_der_bibliothek(self):
        f = self.datei()
        main._state["library"] = [{"path": str(f), "title": "song"}]
        main._move_to_trash = trash = TrashAttrappe()
        self.run_async(main.handle_message(FakeWS(), {"type": "library_remove_disk", "path": str(f)}))
        self.assertEqual(trash.aufrufe, [str(f)])
        self.assertEqual(main._state["library"], [])

    def test_fehlgeschlagen_bleibt_der_eintrag(self):
        # Frueher wurde erst der Eintrag entfernt und dann still geloescht —
        # schlug das fehl, war der Titel weg, die Datei aber noch da.
        f = self.datei()
        main._state["library"] = [{"path": str(f), "title": "song"}]
        main._move_to_trash = TrashAttrappe(gelingt=False)
        self.run_async(main.handle_message(FakeWS(), {"type": "library_remove_disk", "path": str(f)}))
        self.assertEqual(len(main._state["library"]), 1)
        self.assertTrue(f.exists())

    def test_schon_verschwundene_datei_nur_aus_bibliothek(self):
        weg = self.tmp / "gibtsnicht.mp3"
        main._state["library"] = [{"path": str(weg), "title": "x"}]
        main._move_to_trash = trash = TrashAttrappe()
        self.run_async(main.handle_message(FakeWS(), {"type": "library_remove_disk", "path": str(weg)}))
        self.assertEqual(trash.aufrufe, [])
        self.assertEqual(main._state["library"], [])

    @unittest.skipUnless(os.environ.get("SYNTHIMIX_TEST_TRASH") == "1",
                         "legt eine Datei in den echten Papierkorb — nur mit SYNTHIMIX_TEST_TRASH=1")
    def test_echter_papierkorb(self):
        f = self.datei("SynthiMIX-Papierkorbtest.txt")
        self.assertTrue(main._move_to_trash(str(f)))
        self.assertFalse(f.exists())


class OrdnerTest(TrashTestBase):

    def setUp(self):
        super().setUp()
        self.musik = self.tmp / "Musik"
        self.dnb = self.musik / "Drum and Bass"
        self.extra = self.tmp / "Sammlung"
        for d in (self.dnb, self.extra):
            d.mkdir(parents=True)
        main._state["download_dir"] = str(self.tmp / "Downloads")
        main._state["library"] = [
            {"path": str(self.musik / "a.mp3")},
            {"path": str(self.dnb / "b.mp3")},
            {"path": str(self.dnb / "c.mp3")},
            {"path": str(self.extra / "d.mp3")},
        ]

    def test_uebersicht_zaehlt_titel_und_erkennt_ueberfluessige(self):
        # Genau die Lage beim Nutzer: M:\\Musik und M:\\Musik\\Drum and Bass
        main._state["watched_folders"] = [str(self.musik), str(self.dnb)]
        info = {i["path"]: i for i in main._watched_folders_info()}
        self.assertEqual(info[str(self.musik)]["tracks"], 3)
        self.assertIsNone(info[str(self.musik)]["inside"])
        self.assertEqual(info[str(self.dnb)]["tracks"], 2)
        self.assertEqual(info[str(self.dnb)]["inside"], str(self.musik))

    def test_ueberfluessigen_ordner_entfernen_kostet_keine_titel(self):
        main._state["watched_folders"] = [str(self.musik), str(self.dnb)]
        ws = FakeWS()
        self.run_async(main.handle_message(ws, {"type": "remove_watched_folder",
                                                "folder": str(self.dnb), "dry_run": True}))
        self.assertEqual(ws.of_type("watched_folder_impact")[0]["tracks"], 0)
        self.assertEqual(main._state["watched_folders"], [str(self.musik), str(self.dnb)])  # nur gerechnet

        self.run_async(main.handle_message(ws, {"type": "remove_watched_folder", "folder": str(self.dnb)}))
        self.assertEqual(main._state["watched_folders"], [str(self.musik)])
        self.assertEqual(len(main._state["library"]), 4)

    def test_ordner_entfernen_nimmt_nur_nicht_mehr_abgedeckte_titel(self):
        main._state["watched_folders"] = [str(self.musik), str(self.extra)]
        ws = FakeWS()
        self.run_async(main.handle_message(ws, {"type": "remove_watched_folder",
                                                "folder": str(self.musik), "dry_run": True}))
        self.assertEqual(ws.of_type("watched_folder_impact")[0]["tracks"], 3)
        self.run_async(main.handle_message(ws, {"type": "remove_watched_folder", "folder": str(self.musik)}))
        self.assertEqual([lt["path"] for lt in main._state["library"]], [str(self.extra / "d.mp3")])
        self.assertTrue(self.dnb.exists())   # Dateien und Ordner bleiben

    def test_download_ordner_haelt_seine_titel(self):
        dl = self.musik
        main._state["download_dir"] = str(dl)
        main._state["watched_folders"] = [str(self.musik)]
        self.assertEqual(main._library_paths_lost_without(str(self.musik)), [])

    def test_unterordner_wird_nicht_zusaetzlich_beobachtet(self):
        main._state["watched_folders"] = [str(self.musik)]
        gescannt = []

        async def scan_attrappe(folder):
            gescannt.append(folder)
        echt, main.scan_folder = main.scan_folder, scan_attrappe
        try:
            async def ablauf():
                await main.handle_message(FakeWS(), {"type": "scan_library", "folder": str(self.dnb)})
                await __import__("asyncio").sleep(0.05)   # _scan_all laufen lassen
            self.run_async(ablauf())
        finally:
            main.scan_folder = echt
        self.assertEqual(main._state["watched_folders"], [str(self.musik)])


class AusschliessenTest(TrashTestBase):
    """"Aus Bibliothek ausschliessen" muss halten — der Ordner-Waechter lief
    frueher alle 10 s und holte die Titel sofort zurueck."""

    def setUp(self):
        super().setUp()
        self._excl = list(main._state.get("excluded_folders", []))
        main._state["excluded_folders"] = []
        self.musik = self.tmp / "Musik"
        self.party = self.musik / "Party"
        for n in ("oben.mp3", "Party/drin.mp3", "Party/Unter/tief.mp3"):
            f = self.musik / n
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")
        main._state["watched_folders"] = [str(self.musik)]
        main._state["library"] = [{"path": str(self.musik / n)} for n in
                                  ("oben.mp3", "Party/drin.mp3", "Party/Unter/tief.mp3")]

    def tearDown(self):
        main._state["excluded_folders"] = self._excl
        super().tearDown()

    def test_ausschliessen_entfernt_und_bleibt_draussen(self):
        self.run_async(main.handle_message(FakeWS(), {"type": "exclude_folder",
                                                      "folder": str(self.party)}))
        self.assertEqual([lt["path"] for lt in main._state["library"]], [str(self.musik / "oben.mp3")])
        # Der Waechter findet nur, was nicht ausgeschlossen ist
        neu = main._find_new_audio_paths(str(self.musik), {str(self.musik / "oben.mp3")}, True)
        self.assertEqual(neu, [])
        self.assertIn(str(self.party), main._state["excluded_folders"])

    def test_wieder_aufnehmen(self):
        self.run_async(main.handle_message(FakeWS(), {"type": "exclude_folder", "folder": str(self.party)}))
        self.run_async(main.handle_message(FakeWS(), {"type": "include_folder", "folder": str(self.party)}))
        neu = main._find_new_audio_paths(str(self.musik), {str(self.musik / "oben.mp3")}, True)
        self.assertEqual(len(neu), 2)
