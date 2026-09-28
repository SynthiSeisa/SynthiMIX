"""Harmonisch sortieren: passende Tonarten, nahes Tempo, gelegentliche Energie-Schuebe."""
import json
import random

from tests.support import BackendTest, main


class _WS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def t(name, key, bpm=128, lufs=-9.0):
    return {"path": name + ".mp3", "title": name, "key": key, "bpm": bpm, "lufs": lufs}


class HarmonicOrderTest(BackendTest):

    def test_schritte_passen(self):
        start = t("start", "Am")                        # 8A
        # Es gibt einen Weg ganz ohne Bruch: 9A, 10A, 10B, 11B — der Ausreisser zuletzt
        tracks = [t("clash", "F♯"), t("11B", "A"), t("9A", "Em"), t("10B", "D"), t("10A", "Bm")]
        order, _ = main._harmonic_order(start, tracks, random.Random(1))
        steps = [main._key_step(a["key"], b["key"]) for a, b in zip([start] + order, order)]
        # Der unpassende Titel landet ganz hinten, davor nur passende Schritte
        self.assertEqual(order[-1]["title"], "clash")
        for st in steps[:-1]:
            self.assertIn(st, ("same", "relative", "up1", "down1"))

    def test_tempo_nah(self):
        start = t("start", "Am", 128)
        tracks = [t("dnb", "Am", 174), t("house", "Am", 126), t("house2", "Am", 130)]
        order, _ = main._harmonic_order(start, tracks, random.Random(1))
        self.assertEqual(order[-1]["title"], "dnb")

    def test_energie_schub(self):
        # Viele gleiche Tonarten, dazu Titel zwei Stufen hoeher und lauter
        start = t("start", "Am", 124, -10)
        same = [t(f"s{i}", "Am", 124, -10) for i in range(8)]
        up = [t(f"up{i}", "Bm", 126, -7) for i in range(2)]      # 8A -> 10A
        order, boosts = main._harmonic_order(start, same + up, random.Random(2))
        self.assertTrue(boosts)
        first = min(boosts)
        self.assertGreaterEqual(first, 3)                      # nicht sofort
        self.assertTrue(order[first]["title"].startswith("up"))

    def test_halbes_tempo_zaehlt_gleich(self):
        self.assertAlmostEqual(main._tempo_gap(87, 174), 0.0)
        self.assertLess(main._tempo_gap(128, 126), 0.02)

    def test_nachricht_sortiert_nur_kommende(self):
        main._state["library"] = []
        main._state["queue"] = [t("gespielt", "Am"), t("laeuft", "Am"),
                                t("clash", "F♯"), t("gut", "Em"), t("auch", "Am")]
        main._state["queue"][0]["played"] = True
        main._state["current_idx"] = 1
        ws = _WS()
        self.run_async(main.handle_message(ws, {"type": "queue_harmonic"}))
        titles = [x["title"] for x in main._state["queue"]]
        self.assertEqual(titles[:2], ["gespielt", "laeuft"])
        self.assertEqual(titles[-1], "clash")
        res = [m for m in ws.sent if m.get("type") == "harmonic_result"][0]
        self.assertEqual(res["count"], 3)
