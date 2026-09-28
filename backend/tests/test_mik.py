"""Mixed In Key: Energie-Level und Cue-Punkte aus den Tags uebernehmen."""
import base64
import os
import json

from tests.support import BackendTest, main


class MikTagsTest(BackendTest):

    def make_mik(self, name, energy=7, cues=(58.0, 22127.0, 44196.0), b64=False):
        from mutagen.id3 import ID3, TXXX, GEOB, TKEY
        p = self.make_audio(self.tmp / name, seconds=2)
        id3 = ID3(str(p))
        id3.add(TKEY(encoding=3, text="Em"))
        id3.add(TXXX(encoding=3, desc="EnergyLevel", text=str(energy)))
        data = json.dumps({"cues": [{"time": c, "name": ""} for c in cues],
                           "source": "mixedinkey"}).encode()
        if b64:
            data = base64.b64encode(data)
        id3.add(GEOB(encoding=3, mime="application/json", desc="CuePoints", data=data))
        id3.save(str(p))
        return str(p)

    def test_energie_und_cues(self):
        p = self.make_mik("a.mp3", energy=8)
        m = main._read_mik_sync(p)
        self.assertEqual(m["energy"], 8)
        self.assertEqual(m["mik_cues"], [58, 22127, 44196])

    def test_base64_verpackt(self):
        p = self.make_mik("b.mp3", b64=True)
        self.assertEqual(main._read_mik_sync(p)["mik_cues"][0], 58)

    def test_bibliothek_bekommt_felder(self):
        p = self.make_mik("c.mp3", energy=5)
        tags = main._read_tags_sync(p)
        self.assertEqual(tags["energy"], 5)
        self.assertEqual(tags["key"], "Em")
        entry = main._make_library_entry(p, main._probe_sync(p))
        self.assertEqual(entry["energy"], 5)
        self.assertEqual(entry["mik_cues"][:1], [58])

    def test_ohne_mik_nichts(self):
        p = self.make_audio(self.tmp / "d.mp3", seconds=2)
        self.assertEqual(main._read_mik_sync(str(p)), {})

    def test_energie_in_sortierung(self):
        self.assertGreater(main._energy({"energy": 8}), main._energy({"energy": 5}))
        self.assertAlmostEqual(main._energy({"energy": 5, "bpm": 180}), 0.0)

    def test_tempo_aus_cues(self):
        # 64 Schlaege zwischen erstem und letztem Cue bei 174 BPM
        g = {"bpm_f": 173.98, "beat_off": 0.1, "beat_conf": 0.5}
        beat = 60 / 174.0
        cues = [100, 100 + int(round(64 * beat * 1000))]
        out = main._grid_from_mik(g, cues)
        self.assertAlmostEqual(out["bpm_f"], 174.0, delta=0.01)
        self.assertAlmostEqual(out["beat_off"], 0.1, delta=0.001)   # Phase bleibt gemessen

    def test_cues_passen_nicht(self):
        g = {"bpm_f": 128.0, "beat_off": 0.1, "beat_conf": 0.5}
        # Cues liegen einen halben Schlag neben dem Raster: Tempo wuerde um 0,8 % kippen
        self.assertEqual(main._grid_from_mik(g, [0, 30230]), g)
        self.assertEqual(main._grid_from_mik(g, [0, 1000]), g)      # zu kurz


class MikRefreshTest(BackendTest):

    def test_bpm_aus_dem_tag_nachlesen(self):
        """Nach einer Analyse mit Mixed In Key: BPM aus dem Tag, Raster neu messen."""
        from mutagen.id3 import ID3, TBPM
        p = self.make_audio(self.tmp / "e.mp3", seconds=2)
        id3 = ID3(str(p))
        id3.add(TBPM(encoding=3, text="174"))
        id3.save(str(p))
        entry = main._make_library_entry(str(p), {"duration_sec": 2})
        entry.update({"bpm": 87, "bpm_f": 86.9, "beat_off": 0.1, "beat_conf": 0.5,
                      "mtime": int(os.path.getmtime(p)) - 100})
        main._state["library"] = [entry]
        main._state["queue"] = [{"path": str(p), "bpm": 87}]
        self.run_async(main._refresh_tag_meta_task())
        lt = main._state["library"][0]
        self.assertEqual(lt["bpm"], 174)
        self.assertNotIn("bpm_f", lt)
        self.assertEqual(main._state["queue"][0]["bpm"], 174)

    def test_tag_bpm_werte(self):
        self.assertEqual(main._tag_bpm("173.98"), 174)
        self.assertEqual(main._tag_bpm("128,00"), 128)
        self.assertEqual(main._tag_bpm(""), 0)
        self.assertEqual(main._tag_bpm("0"), 0)
