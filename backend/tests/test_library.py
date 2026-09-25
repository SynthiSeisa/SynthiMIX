"""Bibliothek: Eintraege, Laden/Speichern und das Abgleichen mit den Tags.

Mehrere dieser Tests sichern Fehler ab, die bis 1.4.2 unbemerkt drinsteckten:
Album und Genre wurden nie gespeichert, load_library hat den Kuenstler bei
jedem Start verworfen, und einzelne Eintrags-Bauer liessen Felder aus.
"""
import os
import time

from tests.support import BackendTest, main

TAG_FIELDS = ("artist", "album_artist", "album", "genre", "key", "key_src", "meta_rev")


class LibraryEntryTest(BackendTest):

    def test_eintrag_hat_alle_tag_felder(self):
        probe = {"title": "T", "artist": "A", "album_artist": "AA", "album": "Al",
                 "genre": "DnB", "key": "F#m", "duration_sec": 10, "bpm": 174}
        e = main._make_library_entry(str(self.tmp / "x.mp3"), probe)
        for f in TAG_FIELDS:
            self.assertIn(f, e)
        self.assertEqual(e["key"], "F♯m")
        self.assertEqual(e["key_src"], "tag")
        self.assertEqual(e["meta_rev"], main._TAG_META_REV)

    def test_eintrag_ohne_tonart_hat_keine_quelle(self):
        e = main._make_library_entry(str(self.tmp / "x.mp3"), {"title": "T"})
        self.assertEqual((e["key"], e["key_src"]), ("", ""))

    def test_laden_verwirft_keine_tag_felder(self):
        # Genau das ging bis 1.4.2 schief: artist fehlte in der Normalisierung
        self.write_library([{
            "path": str(self.tmp / "a.mp3"), "title": "Titel", "artist": "Netsky",
            "album_artist": "Netsky", "album": "Second Nature", "genre": "Drum & Bass",
            "key": "Fm", "key_src": "tag", "meta_rev": 2, "mtime": 5,
        }])
        main.load_library()
        lt = main._state["library"][0]
        self.assertEqual(lt["artist"], "Netsky")
        self.assertEqual(lt["album"], "Second Nature")
        self.assertEqual(lt["genre"], "Drum & Bass")
        self.assertEqual((lt["key"], lt["key_src"]), ("Fm", "tag"))
        self.assertEqual(lt["meta_rev"], 2)

    def test_speichern_und_laden_ueberlebt_neustart(self):
        main._state["library"] = [main._make_library_entry(
            str(self.tmp / "b.mp3"), {"title": "B", "artist": "X", "album": "Y", "genre": "Z", "key": "4A"})]
        main.save_library()
        main._state["library"] = []
        main.load_library()
        lt = main._state["library"][0]
        self.assertEqual((lt["artist"], lt["album"], lt["genre"], lt["key"]), ("X", "Y", "Z", "Fm"))


class ScanTest(BackendTest):

    def test_scan_liest_tags_inklusive_tonart(self):
        folder = self.tmp / "Musik"
        self.make_audio(folder / "song.mp3", tags={
            "artist": "Camo & Krooked", "album": "Mosaik", "genre": "Drum & Bass", "key": "G♯m"})
        entries = main._scan_sync(str(folder))
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["artist"], "Camo & Krooked")
        self.assertEqual(e["album"], "Mosaik")
        self.assertEqual(e["genre"], "Drum & Bass")
        self.assertEqual((e["key"], e["key_src"]), ("G♯m", "tag"))


class RefreshTagsTest(BackendTest):

    def _entry(self, path, **kw):
        base = {"path": str(path), "title": "t", "artist": "", "album_artist": "", "album": "",
                "genre": "", "key": "", "key_src": "", "meta_rev": 0,
                "mtime": int(os.path.getmtime(path))}
        base.update(kw)
        return base

    def test_alter_stand_fuellt_nur_leere_felder(self):
        f = self.make_audio(self.tmp / "a.mp3", tags={"artist": "Aus Tag", "album": "Album", "key": "Am"})
        main._state["library"] = [self._entry(f, artist="Von Hand")]
        self.run_async(main._refresh_tag_meta_task())
        lt = main._state["library"][0]
        self.assertEqual(lt["artist"], "Von Hand")          # bleibt
        self.assertEqual(lt["album"], "Album")              # ergaenzt
        self.assertEqual((lt["key"], lt["key_src"]), ("Am", "tag"))
        self.assertEqual(lt["meta_rev"], main._TAG_META_REV)

    def test_geaenderte_datei_uebernimmt_tags(self):
        # Z.B. nachdem Mixed In Key die Datei bearbeitet hat
        f = self.make_audio(self.tmp / "b.mp3", tags={"artist": "Neu", "key": "8A"})
        alt = int(os.path.getmtime(f)) - 100
        main._state["library"] = [self._entry(f, artist="Alt", mtime=alt, meta_rev=main._TAG_META_REV)]
        self.run_async(main._refresh_tag_meta_task())
        lt = main._state["library"][0]
        self.assertEqual(lt["artist"], "Neu")
        self.assertEqual(lt["key"], "Am")
        self.assertEqual(lt["mtime"], int(os.path.getmtime(f)))

    def test_tonart_aus_tag_schlaegt_schaetzung(self):
        f = self.make_audio(self.tmp / "c.mp3", tags={"key": "Fm"})
        alt = int(os.path.getmtime(f)) - 100
        main._state["library"] = [self._entry(f, key="C", key_src="analyse", mtime=alt,
                                               meta_rev=main._TAG_META_REV)]
        self.run_async(main._refresh_tag_meta_task())
        lt = main._state["library"][0]
        self.assertEqual((lt["key"], lt["key_src"]), ("Fm", "tag"))

    def test_unveraenderte_aktuelle_eintraege_bleiben_unberuehrt(self):
        f = self.make_audio(self.tmp / "d.mp3", tags={"artist": "Tag"})
        main._state["library"] = [self._entry(f, artist="Von Hand", meta_rev=main._TAG_META_REV)]
        self.run_async(main._refresh_tag_meta_task())
        self.assertEqual(main._state["library"][0]["artist"], "Von Hand")

    def test_fehlende_datei_wird_spaeter_nochmal_versucht(self):
        weg = self.tmp / "gibtsnicht.mp3"
        main._state["library"] = [{"path": str(weg), "title": "x", "meta_rev": 0, "mtime": 1}]
        self.run_async(main._refresh_tag_meta_task())
        self.assertEqual(main._state["library"][0]["meta_rev"], 0)


class NormalizeTest(BackendTest):
    """Normalisieren darf die Datei nicht schlechter machen als vorher."""

    def test_bitrate_cover_und_tags_bleiben(self):
        self.require_ffmpeg()
        import subprocess
        src = self.make_audio(self.tmp / "roh.mp3", seconds=6, tones=[(440, 0.1), (660, 0.05)],
                              tags={"key": "8A", "title": "Test"})
        cover = self.tmp / "cover.jpg"
        ziel = self.tmp / "song.mp3"
        subprocess.run([main.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64",
                        "-frames:v", "1", str(cover)], check=True, creationflags=main._NO_WINDOW)
        # 320 kbps mit eingebettetem Cover — so sehen gekaufte Titel aus
        subprocess.run([main.FFMPEG, "-y", "-v", "error", "-i", str(src), "-i", str(cover),
                        "-map", "0:a", "-map", "1", "-c:a", "libmp3lame", "-b:a", "320k",
                        "-c:v", "copy", "-disposition:v", "attached_pic", "-map_metadata", "0",
                        "-id3v2_version", "3", str(ziel)], check=True, creationflags=main._NO_WINDOW)

        self.assertTrue(main._normalize_one_sync(str(ziel), -10.0, -1.5))

        kbps, _ = main._audio_stream_info(str(ziel))
        self.assertGreaterEqual(kbps, 300)          # frueher: 128
        probe = subprocess.run([main.FFPROBE, "-v", "quiet", "-print_format", "json",
                                "-show_streams", "-show_format", str(ziel)],
                               capture_output=True, text=True, creationflags=main._NO_WINDOW)
        daten = __import__("json").loads(probe.stdout)
        self.assertIn("video", [s["codec_type"] for s in daten["streams"]])   # Cover
        self.assertEqual(daten["format"]["tags"].get("TKEY"), "8A")
        self.assertAlmostEqual(main._compute_lufs_sync(str(ziel)), -10.0, delta=1.0)
