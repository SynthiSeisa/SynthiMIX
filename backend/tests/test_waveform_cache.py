"""Waveform: mit numpy gerechnet, auf der Platte gemerkt, eigener Thread-Pool."""
from tests.support import BackendTest, main


class WaveformCacheTest(BackendTest):

    def test_gemerkt_ueber_neustart(self):
        p = self.make_audio(self.tmp / "t.mp3", seconds=4, tones=[(220, 0.5)])
        a = self.run_async(main.compute_waveform(str(p)))
        self.assertEqual(len(a), 1000)
        self.assertAlmostEqual(max(a), 1.0, places=2)
        self.run_async(__import__("asyncio").sleep(0))
        main._save_wf_cache()
        self.assertTrue(main._wf_cache_file().exists())
        # "Neustart": Speicher leer, Datei wird nicht mehr gelesen
        main._wf_cache.clear()
        main._wf_disk = None
        keep = main._waveform_sync
        main._waveform_sync = lambda *a: self.fail("haette aus dem Speicher kommen sollen")
        try:
            b = self.run_async(main.compute_waveform(str(p)))
        finally:
            main._waveform_sync = keep
        self.assertEqual(len(b), 1000)
        self.assertLess(max(abs(x - y) for x, y in zip(a, b)), 1 / 255 + 1e-9)

    def test_geaenderte_datei_rechnet_neu(self):
        p = self.make_audio(self.tmp / "t.mp3", seconds=4, tones=[(220, 0.5)])
        k1 = main._wf_key(str(p))
        self.make_audio(p, seconds=6, tones=[(330, 0.5)])
        self.assertNotEqual(k1, main._wf_key(str(p)))
