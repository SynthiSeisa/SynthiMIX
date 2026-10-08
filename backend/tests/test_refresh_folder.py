"""Ordner aktualisieren: geaenderte Titel/Tags neu lesen, Neues aufnehmen,
Geloeschtes als fehlend markieren — nur im gewaehlten Ordner."""
import os
import time

from tests.support import BackendTest, main
from synthimix import library


class RefreshFolderTest(BackendTest):

    def by_name(self):
        return {os.path.basename(t["path"]): t for t in main._state["library"]}

    def test_geaenderter_titel_neu_und_geloescht(self):
        self.require_ffmpeg()
        root = self.tmp / "Musik"
        (root / "A").mkdir(parents=True)
        (root / "B").mkdir()
        a = self.make_audio(root / "A" / "eins.mp3", tags={"title": "Alter Titel", "artist": "Wer"})
        b = self.make_audio(root / "B" / "zwei.mp3", tags={"title": "Zwei", "artist": "Wer"})
        main._state["library"] = []
        main._state["watched_folders"] = [str(root)]
        self.run_async(library.scan_folder(str(root)))
        self.assertEqual(self.by_name()["eins.mp3"]["title"], "Alter Titel")
        # eigene Messwerte am Eintrag duerfen das Aktualisieren ueberleben
        self.by_name()["zwei.mp3"]["lufs"] = -9.5

        # Titel in einem anderen Programm geaendert (rekordbox, Mp3tag)
        from mutagen.id3 import ID3, TIT2
        t = ID3(str(a))
        t.add(TIT2(encoding=3, text="Neuer Titel"))
        t.save(str(a))
        os.utime(a, (time.time() + 5, time.time() + 5))
        neu = self.make_audio(root / "A" / "drei.mp3", tags={"title": "Drei"})
        os.remove(b)                                   # liegt im Nachbarordner

        # nur Ordner A aktualisieren: B wird nicht angefasst
        self.run_async(library.scan_folder(str(root / "A"), True))
        lib = self.by_name()
        self.assertEqual(lib["eins.mp3"]["title"], "Neuer Titel")
        self.assertIn("drei.mp3", lib)
        self.assertFalse(lib["zwei.mp3"].get("missing"))
        self.assertEqual(lib["zwei.mp3"]["lufs"], -9.5)

        # alles aktualisieren: jetzt faellt auf, dass zwei.mp3 fehlt; nichts doppelt
        self.run_async(library.scan_folder(str(root), True))
        lib = self.by_name()
        self.assertTrue(lib["zwei.mp3"].get("missing"))
        self.assertEqual(len(main._state["library"]), 3)
        self.assertTrue(os.path.exists(neu))

    def test_ohne_unterordner_bleiben_die_unterordner_unberuehrt(self):
        root = self.tmp / "Musik"
        (root / "tief").mkdir(parents=True)
        oben, unten = root / "oben.mp3", root / "tief" / "unten.mp3"
        oben.write_bytes(b"x"); unten.write_bytes(b"x")
        main._state["library"] = []
        self.run_async(library.scan_folder(str(root), True))
        os.remove(oben); os.remove(unten)
        self.run_async(library.scan_folder(str(root), False))
        lib = self.by_name()
        self.assertTrue(lib["oben.mp3"].get("missing"))
        self.assertFalse(lib["unten.mp3"].get("missing"))    # nicht angesehen, also nicht als fehlend markiert

    def test_nur_ordner_der_bibliothek(self):
        root = self.tmp / "Musik"
        (root / "A").mkdir(parents=True)
        fremd = self.tmp / "Fremd"
        fremd.mkdir()
        main._state["watched_folders"] = [str(root)]
        self.assertTrue(library.in_library_folders(str(root)))
        self.assertTrue(library.in_library_folders(str(root / "A")))
        self.assertFalse(library.in_library_folders(str(fremd)))
        self.assertFalse(library.in_library_folders(str(self.tmp / "Musik2")))
