"""Tonart: Einlesen der verschiedenen Schreibweisen, Camelot, Vertraeglichkeit,
Erkennung und die harmonische Auswahl."""
import random

from tests.support import BackendTest, main


class ParseKeyTest(BackendTest):

    def test_schreibweisen(self):
        faelle = {
            # rekordbox / musikalisch
            "Fm": "Fm", "F♯m": "F♯m", "F#m": "F♯m", "Gbm": "F♯m", "G♭m": "F♯m",
            "C": "C", "A minor": "Am", "A min": "Am", "Bbmaj": "A♯", "E major": "E",
            "Ebm": "D♯m", "Cb": "B",
            # Mixed In Key (Camelot)
            "4A": "Fm", "08A": "Am", "8B": "C", "12a": "C♯m", "1B": "B",
            # Traktor (Open Key): 1m = Am, jeder Schritt eine Quinte weiter
            "1m": "Am", "1d": "C", "6m": "G♯m", "9m": "Fm", "12d": "F",
        }
        for raw, erwartet in faelle.items():
            with self.subTest(raw=raw):
                self.assertEqual(main._parse_key(raw), erwartet)

    def test_unlesbares(self):
        for raw in ("", None, "o", "13A", "H", "Xm", "keine"):
            with self.subTest(raw=raw):
                self.assertIsNone(main._parse_key(raw))

    def test_bytes_aus_mp4_tags(self):
        self.assertEqual(main._parse_key(b"F#m"), "F♯m")

    def test_camelot_ganzes_rad(self):
        for n in range(1, 13):
            for letter in "AB":
                with self.subTest(camelot=f"{n}{letter}"):
                    self.assertEqual(main._key_to_camelot(main._parse_key(f"{n}{letter}")), (n, letter))


class KeyCompatTest(BackendTest):

    def test_vertraeglichkeit(self):
        self.assertEqual(main._key_compat("Am", "Am"), 3)       # gleich
        self.assertEqual(main._key_compat("Am", "C"), 2)        # Paralleltonart (8A/8B)
        self.assertEqual(main._key_compat("Am", "Em"), 2)       # Nachbar (8A/9A)
        self.assertEqual(main._key_compat("Am", "Dm"), 2)       # Nachbar (8A/7A)
        self.assertEqual(main._key_compat("C♯m", "G♯m"), 2)     # 12A/1A, ueber den Kreis
        self.assertEqual(main._key_compat("Am", "F♯m"), 0)      # 8A/11A
        self.assertEqual(main._key_compat("Am", "G"), 0)        # 8A/9B
        self.assertEqual(main._key_compat("Am", ""), -1)

    def test_harmonische_auswahl_bevorzugt_passende(self):
        main._state["library"] = [{"path": "ref", "key": "Am"}]
        passend = {"path": "p", "key": "Em"}
        daneben = [{"path": f"d{i}", "key": "F♯m"} for i in range(20)]
        random.seed(1)
        for _ in range(30):
            self.assertIs(main._pick_harmonic(daneben + [passend], "ref"), passend)

    def test_harmonische_auswahl_ohne_passende_nimmt_irgendeinen(self):
        main._state["library"] = [{"path": "ref", "key": "Am"}]
        kandidaten = [{"path": "x", "key": "F♯m"}, {"path": "y", "key": ""}]
        self.assertIn(main._pick_harmonic(kandidaten, "ref"), kandidaten)


class FrontendAbgleichTest(BackendTest):
    """Die Vertraeglichkeit gibt es doppelt: in main.py fuer Radio und Auto-Mix,
    in app/src/lib/keys.js fuer die Anzeige. Beide muessen dasselbe sagen,
    sonst zeigt die Queue "passt", wo das Radio anders entscheidet."""

    def test_python_und_javascript_stimmen_ueberein(self):
        import json, shutil, subprocess
        from tests.support import BACKEND
        node = shutil.which("node")
        if not node:
            self.skipTest("node nicht gefunden")
        keys_js = (BACKEND.parent / "app" / "src" / "lib" / "keys.js").resolve().as_uri()
        script = (
            f"const m = await import({json.dumps(keys_js)});"
            "const n=['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'];"
            "const ks=n.flatMap(x=>[x,x+'m']);const lv={same:3,good:2,clash:0,unknown:-1};const o={};"
            "for(const a of ks)for(const b of ks)o[a+'|'+b]=lv[m.keyCompat(a,b).level];"
            "console.log(JSON.stringify(o));")
        r = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True,
                           text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        js = json.loads(r.stdout)
        abweichungen = {k: (v, main._key_compat(*k.split("|"))) for k, v in js.items()
                        if v != main._key_compat(*k.split("|"))}
        self.assertEqual(len(js), 576)
        self.assertEqual(abweichungen, {})


class DetectKeyTest(BackendTest):

    def setUp(self):
        super().setUp()
        if not main._numpy_ok():
            self.skipTest("numpy nicht installiert")

    def test_a_moll_akkord(self):
        f = self.make_audio(self.tmp / "am.mp3", seconds=20, tones=[
            (110.0, 1.0), (220.0, 0.8), (261.63, 0.5), (329.63, 0.5)])
        self.assertEqual(main._detect_key_sync(str(f)), "Am")

    def test_c_dur_akkord(self):
        f = self.make_audio(self.tmp / "c.mp3", seconds=20, tones=[
            (130.81, 1.0), (261.63, 0.8), (329.63, 0.5), (392.0, 0.5)])
        self.assertEqual(main._detect_key_sync(str(f)), "C")

    def test_stille_gibt_keine_tonart(self):
        f = self.make_audio(self.tmp / "still.mp3", seconds=20)
        self.assertEqual(main._detect_key_sync(str(f)), "")

    def test_kaputte_datei_gibt_keine_tonart(self):
        f = self.tmp / "kaputt.mp3"
        f.write_bytes(b"keine musik")
        self.assertEqual(main._detect_key_sync(str(f)), "")
