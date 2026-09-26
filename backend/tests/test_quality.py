"""Qualitaet: Grenzfrequenz messen und eine bessere Version unter demselben
Namen einsetzen.

Die Messung laeuft an erzeugtem Rauschen: einmal verlustfrei, einmal als
96-kbps-MP3, die auf 320 kbps hochgerechnet wurde. Beim
Ersetzen werden Download und Papierkorb ersetzt; geprueft wird, dass Pfad,
Tags und Wiedergabezaehler bleiben und die alte Datei im Papierkorb landet.
"""
import os
import shutil
import subprocess

from tests.support import BackendTest, FakeWS, main


class QualityTest(BackendTest):

    def _noise(self, name, seconds=12, bitrate="320k", fmt="mp3"):
        self.require_ffmpeg()
        path = self.tmp / f"{name}.{fmt}"
        cmd = [main.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
               f"anoisesrc=d={seconds}:c=pink:r=44100:a=0.3", "-ac", "2"]
        cmd += (["-b:a", bitrate] if fmt == "mp3" else []) + [str(path)]
        subprocess.run(cmd, check=True, capture_output=True, creationflags=main._NO_WINDOW)
        return path

    # ── Messung ─────────────────────────────────────────────────────────────
    def _hochgerechnet(self):
        """Wie ein YouTube-Konverter: niedrige Bitrate, dann als 320 kbps gespeichert."""
        klein = self._noise("klein", bitrate="96k")
        gross = self.tmp / "hochgerechnet.mp3"
        subprocess.run([main.FFMPEG, "-y", "-v", "error", "-i", str(klein), "-b:a", "320k", str(gross)],
                       check=True, capture_output=True, creationflags=main._NO_WINDOW)
        return gross

    def test_grenzfrequenz_trennt_hochgerechnete_dateien(self):
        dumpf = main._cutoff_khz_sync(str(self._hochgerechnet()), 12)
        voll = main._cutoff_khz_sync(str(self._noise("voll", fmt="wav")), 12)
        self.assertLess(dumpf, main._CUTOFF_UPSCALED_KHZ)
        self.assertGreater(voll, 20.0)

    def test_stille_ist_nicht_messbar(self):
        stille = self.make_audio(self.tmp / "stille.mp3", seconds=12)
        self.assertEqual(main._cutoff_khz_sync(str(stille), 12), 0.0)

    def test_feld_bleibt_beim_laden_erhalten(self):
        self.write_library([{"path": str(self.tmp / "a.mp3"), "title": "A", "cutoff_khz": 16.1},
                            {"path": str(self.tmp / "b.mp3"), "title": "B"}])
        main.load_library()
        by = {t["title"]: t for t in main._state["library"]}
        self.assertEqual(by["A"]["cutoff_khz"], 16.1)
        self.assertNotIn("cutoff_khz", by["B"])

    def test_kurze_dateien_werden_nicht_geprueft(self):
        main._state["library"] = [
            {"path": "x/sample.wav", "title": "FX", "duration_sec": 8},
            {"path": "x/song.mp3", "title": "Song", "duration_sec": 200},
            {"path": "x/fertig.mp3", "title": "Fertig", "duration_sec": 200, "cutoff_khz": 20.1}]
        self.assertEqual([t["title"] for t in main._quality_pending()], ["Song"])

    # ── Tags ────────────────────────────────────────────────────────────────
    def _tagged_old(self):
        from mutagen.id3 import ID3, COMM, APIC, TKEY, TIT2, TPE1
        old = self.make_audio(self.tmp / "Musik" / "Artist - Song.mp3", seconds=3, tones=[(440, 0.5)])
        tags = ID3(str(old))
        tags.add(TIT2(encoding=3, text="Song"))
        tags.add(TPE1(encoding=3, text="Artist"))
        tags.add(TKEY(encoding=3, text="8A"))
        tags.add(COMM(encoding=3, lang="eng", desc="", text="8A - Energy 7"))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=b"\xff\xd8\xff\xe0cover"))
        tags.save(str(old))
        return old

    def test_tags_werden_vollstaendig_uebernommen(self):
        from mutagen.id3 import ID3
        old = self._tagged_old()
        new = self.make_audio(self.tmp / "neu.mp3", seconds=3, tones=[(880, 0.5)],
                              tags={"title": "YouTube-Titel (Official Video)", "artist": "Kanal"})
        self.assertTrue(main._copy_tags_sync(str(old), str(new)))
        t = ID3(str(new))
        self.assertEqual(str(t["TIT2"]), "Song")
        self.assertEqual(str(t["TPE1"]), "Artist")
        self.assertEqual(str(t["TKEY"]), "8A")
        self.assertEqual(t.getall("COMM")[0].text[0], "8A - Energy 7")
        self.assertEqual(t.getall("APIC")[0].data, b"\xff\xd8\xff\xe0cover")

    # ── Ersetzen ────────────────────────────────────────────────────────────
    def _replace(self, old, entry_extra=None, queue_current=False):
        trash = self.tmp / "Papierkorb"
        trash.mkdir(exist_ok=True)
        main._state["library"] = [{"path": str(old), "title": "Song", "duration_sec": 3.0,
                                   "bitrate_kbps": 320, "lufs": -8.0, "play_count": 7,
                                   "cutoff_khz": 16.0, **(entry_extra or {})}]
        if queue_current:
            main._state["queue"] = [{"path": str(old), "title": "Song"}]
            main._state["current_idx"] = 0

        async def download(url, ext, tmpdir):
            src = self.make_audio(self.tmp / "quelle.mp3", seconds=3, tones=[(880, 0.5)])
            dst = os.path.join(tmpdir, "neu.mp3")
            shutil.copy(src, dst)
            return dst, ""

        def to_trash(path):
            shutil.move(path, trash / os.path.basename(path))
            return True

        async def enrich(path, force=False):
            pass

        alt = (main._download_replacement, main._move_to_trash, main._enrich_track)
        main._download_replacement, main._move_to_trash, main._enrich_track = download, to_trash, enrich
        ws = FakeWS()
        try:
            self.run_async(main._quality_replace(str(old), "https://music.youtube.com/watch?v=X", ws))
        finally:
            main._download_replacement, main._move_to_trash, main._enrich_track = alt
        return ws, trash

    def test_ersetzen_behaelt_name_pfad_tags_und_zaehler(self):
        from mutagen.id3 import ID3
        old = self._tagged_old()
        ws, trash = self._replace(old)
        states = [m["state"] for m in ws.of_type("quality_replace_status")]
        self.assertEqual(states[-1], "done", ws.sent)
        self.assertTrue(old.exists())                                  # gleicher Pfad
        self.assertTrue((trash / old.name).exists())                   # alte im Papierkorb
        self.assertEqual(str(ID3(str(old))["TKEY"]), "8A")             # Tags uebernommen
        lt = main._state["library"][0]
        self.assertEqual(lt["path"], str(old))
        self.assertEqual(lt["play_count"], 7)
        self.assertEqual(lt["lufs"], -99.0)                            # wird neu gemessen
        self.assertFalse(any(f.endswith(".synthimix-neu") for f in os.listdir(old.parent)))

    def test_geladener_titel_wird_nicht_ersetzt(self):
        old = self._tagged_old()
        ws, trash = self._replace(old, queue_current=True)
        self.assertEqual(ws.of_type("quality_replace_status")[-1]["state"], "error")
        self.assertFalse((trash / old.name).exists())

    def test_fehlender_download_laesst_alles_wie_es_war(self):
        old = self._tagged_old()
        before = old.read_bytes()

        async def kein_download(url, ext, tmpdir):
            return None, "Video unavailable"

        alt = main._download_replacement
        main._download_replacement = kein_download
        main._state["library"] = [{"path": str(old), "title": "Song"}]
        ws = FakeWS()
        try:
            self.run_async(main._quality_replace(str(old), "u", ws))
        finally:
            main._download_replacement = alt
        self.assertEqual(ws.of_type("quality_replace_status")[-1]["state"], "error")
        self.assertEqual(old.read_bytes(), before)
