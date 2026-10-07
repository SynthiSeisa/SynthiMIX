"""Lautheit dauerhaft angleichen (einzelne Titel, ganzer Ordner): alle Tags
bleiben, Passende werden uebersprungen, Originale auf Wunsch in den Papierkorb."""
import json
import subprocess
from pathlib import Path

from tests.support import BackendTest, main
from synthimix import library, media


class NormalizeTest(BackendTest):

    def make(self, name, vol=0.2):
        self.require_ffmpeg()
        p = self.tmp / name
        subprocess.run([main.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration=8,volume={vol}",
                        "-c:a", "libmp3lame", "-b:a", "192k", "-id3v2_version", "3", str(p)],
                       check=True, capture_output=True, creationflags=main._NO_WINDOW)
        from mutagen.id3 import ID3, GEOB, COMM, TIT2
        t = ID3(str(p))
        t.add(GEOB(encoding=0, mime="application/json", desc="CuePoints", data=b'{"cues":[1000,2000]}'))
        t.add(COMM(encoding=3, lang="eng", desc="", text="8A - Energy 7"))
        t.add(TIT2(encoding=3, text="Probe"))
        t.save(str(p), v2_version=3)
        return str(p)

    def frames(self, p):
        from mutagen.id3 import ID3
        return {k.split(":")[0] + (":" + k.split(":", 1)[1] if k.startswith("GEOB") else "") for k in ID3(p).keys()}

    def test_alle_tags_bleiben_und_passende_werden_uebersprungen(self):
        p = self.make("a.mp3")
        vorher = self.frames(p)
        self.assertIn("GEOB:CuePoints", vorher)
        self.assertEqual(media._normalize_one_sync(p, -14.0, -1.5, 0.5, False), "done")
        self.assertEqual(self.frames(p), vorher)                 # Cue-Punkte und Kommentar noch da
        from mutagen.id3 import ID3
        self.assertEqual(ID3(p).version[1], 3)                   # ID3-Version bleibt
        # jetzt auf dem Zielwert: nicht noch einmal neu schreiben
        self.assertEqual(media._normalize_one_sync(p, -14.0, -1.5, 0.5, False), "skipped")

    def test_original_in_den_papierkorb(self):
        p = self.make("b.mp3")
        weg = []
        echt = library._move_to_trash
        library._move_to_trash = lambda path: (weg.append(Path(path).read_bytes()[:3]), __import__("os").remove(path), True)[2]
        try:
            self.assertEqual(media._normalize_one_sync(p, -14.0, -1.5, 0.5, True), "done")
        finally:
            library._move_to_trash = echt
        self.assertEqual(len(weg), 1)
        self.assertTrue((self.tmp / "b.mp3").exists())
        self.assertFalse((self.tmp / "b.mp3.synthimix-neu").exists())
        # laesst sich das Original nicht weglegen, bleibt alles wie es war
        q = self.make("c.mp3")
        alt = Path(q).read_bytes()
        library._move_to_trash = lambda path: False
        try:
            self.assertFalse(media._normalize_one_sync(q, -14.0, -1.5, 0.5, True))
        finally:
            library._move_to_trash = echt
        self.assertEqual(Path(q).read_bytes(), alt)
        self.assertFalse((self.tmp / "c.mp3.synthimix-neu").exists())

    def test_ordner_zaehlt_geaendert_uebersprungen_und_fehlend(self):
        a, b = self.make("x.mp3"), self.make("y.mp3")
        main._state["library"] = [{"path": a, "title": "x", "lufs": -99.0},
                                  {"path": b, "title": "y", "lufs": -14.2}]       # passt schon: nicht anfassen
        alt_b = Path(b).read_bytes()
        sent = []

        class WS:
            async def send_text(self, s):
                sent.append(json.loads(s))
        self.run_async(media._normalize_files([a, b, str(self.tmp / "fehlt.mp3")], -14.0, -1.5, WS(), skip_tol=0.5, trash=False))
        done = sent[-1]
        self.assertEqual(done["type"], "normalize_done")
        self.assertEqual((done["normalized"], done["skipped"], done["errors"], done["total"]), (1, 1, 1, 3))
        self.assertEqual(Path(b).read_bytes(), alt_b)
        self.assertEqual(main._state["library"][0]["lufs"], -14.0)
        self.assertEqual(main._state["library"][0]["mtime"], int(__import__("os").path.getmtime(a)))

    def test_abbrechen(self):
        a = self.make("z.mp3")
        main._state["library"] = [{"path": a, "title": "z", "lufs": -99.0}]
        alt = Path(a).read_bytes()
        sent = []

        class WS:
            async def send_text(self, s):
                sent.append(json.loads(s))
                media._norm_cancel = True                       # gleich nach der ersten Meldung

        async def lauf():
            import asyncio
            task = asyncio.ensure_future(media._normalize_files([a] * 6, -14.0, -1.5, WS(), skip_tol=0.5))
            await task
        self.run_async(lauf())
        self.assertTrue(sent[-1]["cancelled"])
        self.assertLess(sent[-1]["normalized"] + sent[-1]["errors"], 6)
