"""Automatisches Aktualisieren von ffmpeg und spotdl."""
import os
import shutil
import zipfile

from tests.support import BackendTest, main


class FfmpegUpdateTest(BackendTest):

    def setUp(self):
        super().setUp()
        self.require_ffmpeg()
        self._saved_tools = main.FFMPEG_TOOLS_DIR
        self._saved_ff = (main.FFMPEG, main.FFPROBE)
        main.FFMPEG_TOOLS_DIR = self.tmp / "tools"
        # Nachgebautes BtbN-Paket aus der vorhandenen ffmpeg.exe
        self.zip = self.tmp / "paket.zip"
        with zipfile.ZipFile(self.zip, "w") as z:
            z.write(main.FFMPEG, "ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe")
            z.write(main.FFPROBE, "ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe")
            z.writestr("ffmpeg-master-latest-win64-gpl/LICENSE.txt", "x")
        self._saved_retrieve = main._urllib_req.urlretrieve
        main._urllib_req.urlretrieve = lambda url, dest, *a: shutil.copy(self.zip, dest)

    def tearDown(self):
        main._urllib_req.urlretrieve = self._saved_retrieve
        main.FFMPEG_TOOLS_DIR = self._saved_tools
        main.FFMPEG, main.FFPROBE = self._saved_ff
        super().tearDown()

    def test_installiert_in_eigenen_ordner(self):
        folder = main._install_ffmpeg_sync({"date": "20260927", "url": "x"})
        self.assertTrue(folder.endswith("ffmpeg-20260927"))
        self.assertTrue(os.path.isfile(os.path.join(folder, "ffmpeg.exe")))
        self.assertTrue(os.path.isfile(os.path.join(folder, "ffprobe.exe")))
        # Nichts Halbfertiges liegen lassen
        rest = [p.name for p in main.FFMPEG_TOOLS_DIR.iterdir()]
        self.assertEqual(rest, ["ffmpeg-20260927"])

    def test_neueste_wird_genommen_alte_weg(self):
        main._install_ffmpeg_sync({"date": "20260101", "url": "x"})
        main._install_ffmpeg_sync({"date": "20260927", "url": "x"})
        main._use_newest_ffmpeg()
        self.assertIn("ffmpeg-20260927", main.FFMPEG)
        self.assertIn("ffmpeg-20260927", main.FFPROBE)
        self.assertEqual([p.name for p in main.FFMPEG_TOOLS_DIR.iterdir()], ["ffmpeg-20260927"])

    def test_kaputtes_paket_aendert_nichts(self):
        with zipfile.ZipFile(self.zip, "w") as z:
            z.writestr("irgendwas/bin/ffmpeg.exe", "keine exe")
        self.assertEqual(main._install_ffmpeg_sync({"date": "20260927", "url": "x"}), "")
        self.assertEqual(list(main.FFMPEG_TOOLS_DIR.iterdir()), [])

    def test_versionsdatum(self):
        self.assertEqual(main._ffmpeg_build_date("N-125258-gdf94900c98-20260624"), "20260624")
        self.assertEqual(main._ffmpeg_build_date("7.1-essentials_build-www.gyan.dev"), "")
        self.assertEqual(main._days_between("20260624", "20260927"), 95)
