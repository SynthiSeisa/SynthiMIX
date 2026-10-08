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

        # alles aktualisieren: zwei.mp3 ist geloescht (der Ordner ist noch da) → Eintrag weg
        self.run_async(library.scan_folder(str(root), True))
        lib = self.by_name()
        self.assertNotIn("zwei.mp3", lib)
        self.assertEqual(len(main._state["library"]), 2)
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
        self.assertNotIn("oben.mp3", lib)
        self.assertIn("unten.mp3", lib)                      # Unterordner nicht angesehen: bleibt
        self.assertFalse(lib["unten.mp3"].get("missing"))

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


class GoneFilesTest(BackendTest):
    """Gemeldet 10/2026: im Ordner lagen 7 Dateien, die App zeigte 50 — geloeschte
    Dateien und Zwischendateien eines anderen Programms blieben in der Bibliothek."""

    def lib(self, *paths, **extra):
        main._state["library"] = [{"path": str(p), "title": os.path.basename(str(p)), **extra} for p in paths]

    def test_zwischendateien_anderer_programme_sind_keine_titel(self):
        for name in (".Sido - Faded (Mashup).tmp.wav", "~neu.mp3", "Titel.tmp.mp3", "Titel.temp.mp3"):
            self.assertFalse(library._is_audio_file(name), name)
        for name in ("Sido - Faded (Mashup).mp3", ".38 Special - Hold On Loosely.mp3", "Tmp - Lied.wav", "A~B.mp3"):
            self.assertTrue(library._is_audio_file(name), name)

    def test_geloeschte_und_zwischendateien_verschwinden_beim_waechter(self):
        root = self.tmp / "Mashups"
        (root / "Stems").mkdir(parents=True)
        da = root / "bleibt.mp3"; da.write_bytes(b"x")
        tmp = root / ".halb.tmp.wav"; tmp.write_bytes(b"x")          # liegt noch da, ist aber kein Titel
        self.lib(da, tmp, root / "weg.mp3", root / "Stems" / "A Vocals.wav", root / "Alt" / "ordner auch weg.mp3")
        gone, sure = library._really_gone(str(root))
        self.assertTrue(sure)
        self.assertEqual(library._drop_entries(gone), 4)
        self.assertEqual([os.path.basename(t["path"]) for t in main._state["library"]], ["bleibt.mp3"])
        self.assertTrue(tmp.exists())                                # die Datei selbst wird nicht angefasst

    def test_platte_nicht_da_oder_auffaellig_viele_nichts_loeschen(self):
        weg = self.tmp / "Platte ab"
        self.lib(weg / "a.mp3", weg / "b.mp3")
        self.assertEqual(library._really_gone(str(weg)), ([], False))           # Ordner nicht erreichbar
        self.run_async(library.scan_folder(str(self.tmp), True)) if False else None
        # Ordner da, aber fast alles "weg" (Netzlaufwerk haengt): nur markieren, nicht loeschen
        root = self.tmp / "Musik"
        root.mkdir()
        (root / "da.mp3").write_bytes(b"x")
        self.lib(root / "da.mp3", *[root / f"t{i}.mp3" for i in range(40)])
        gone, sure = library._really_gone(str(root))
        self.assertEqual((len(gone), sure), (40, False))
        self.run_async(library.scan_folder(str(root), True))
        self.assertEqual(len(main._state["library"]), 41)
        self.assertEqual(sum(1 for t in main._state["library"] if t.get("missing")), 40)

    def test_verschobene_datei_behaelt_messwerte(self):
        root = self.tmp / "Musik"
        (root / "neu").mkdir(parents=True)
        ziel = root / "neu" / "lied.mp3"; ziel.write_bytes(b"x")
        main._state["library"] = [
            {"path": str(root / "lied.mp3"), "title": "Lied", "fid": "vol:42", "bpm": 174.0, "lufs": -8.5, "play_count": 7},
            {"path": str(ziel), "title": "Lied", "fid": "vol:42", "bpm": 0, "lufs": -99.0, "play_count": 0, "folder": "neu"}]
        gone, sure = library._really_gone(str(root))
        library._drop_entries(gone)
        (t,) = main._state["library"]
        self.assertEqual((t["path"], t["bpm"], t["lufs"], t["play_count"], t["folder"]), (str(ziel), 174.0, -8.5, 7, "neu"))
