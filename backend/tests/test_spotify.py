"""Spotify-Links: Titelliste von Spotify, Ton ueber die eigene Song-Suche —
ohne spotdl (das fand 10/2026 nichts mehr, in der Liste stand "0 / 88")."""
import json

from tests.support import BackendTest, FakeWS, main
from synthimix import download, spotify

PL = "https://open.spotify.com/playlist/7DOgFjXiofkmPLm5OmGsaZ"


def embed(entity):
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script></html>"


def song(vid, artist, title, dur):
    return {"url": f"https://music.youtube.com/watch?v={vid}", "title": title, "artist": artist,
            "uploader": artist, "duration": dur, "kind": "song"}


class SpotifyTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._alt = (spotify._fetch_sync, main._ytm_song_search)
        spotify.not_found.clear()
        spotify.truncated.clear()

    def tearDown(self):
        spotify._fetch_sync = self._alt[0]
        main._ytm_song_search = self._alt[1]
        super().tearDown()

    def fake(self, tracks, hits, title="Best of Sido", kind="playlist"):
        spotify._fetch_sync = lambda url: {"title": title, "kind": kind, "tracks": [dict(t) for t in tracks]}

        async def suche(query, n=5):
            return [dict(r) for key, res in hits.items() if key.lower() in query.lower() for r in res]
        main._ytm_song_search = suche

    def test_links_erkennen(self):
        self.assertEqual(spotify.parse(PL + "?si=83aec21a103e40eb"), ("playlist", "7DOgFjXiofkmPLm5OmGsaZ"))
        self.assertEqual(spotify.parse("https://open.spotify.com/intl-de/track/40DBa4l3FMmGxW3Xv0c4px"),
                         ("track", "40DBa4l3FMmGxW3Xv0c4px"))
        self.assertEqual(spotify.parse("https://open.spotify.com/album/1A2GTWGtFfWp7KSQTwWOyo")[0], "album")
        self.assertIsNone(spotify.parse("https://open.spotify.com/artist/1A2GTWGtFfWp7KSQTwWOyo"))
        self.assertTrue(download._is_playlist("https://open.spotify.com/album/1A2GTWGtFfWp7KSQTwWOyo"))
        self.assertFalse(download._is_playlist("https://open.spotify.com/track/40DBa4l3FMmGxW3Xv0c4px"))

    def test_einbett_seite_lesen(self):
        d = spotify._parse_embed(embed({"name": "Best of Sido", "trackList": [
            {"title": "Astronaut", "subtitle": "Sido,\xa0Andreas Bourani", "duration": 238213, "entityType": "track"},
            {"title": "Folge 1", "subtitle": "Podcast", "duration": 1000, "entityType": "episode"}]}))
        self.assertEqual(d["title"], "Best of Sido")
        self.assertEqual(d["tracks"], [{"title": "Astronaut", "artist": "Sido, Andreas Bourani", "duration": 238}])
        # einzelner Titel: keine Liste, Kuenstler stehen woanders
        d = spotify._parse_embed(embed({"name": "Ganz unten", "artists": [{"name": "Sido"}], "duration": 212062}))
        self.assertEqual(d["tracks"], [{"title": "Ganz unten", "artist": "Sido", "duration": 212}])
        self.assertIsNone(spotify._parse_embed("<html>kein Inhalt</html>"))

    def test_treffer_muss_zu_titel_kuenstler_und_laenge_passen(self):
        t = {"title": "Liebs oder lass es", "artist": "Genetikk, Sido", "duration": 180}
        res = [song("aaaaaaaaaaa", "Genetikk", "Lieb's oder lass es (Instrumental)", 181),
               song("bbbbbbbbbbb", "Sido", "Bljad", 186),
               song("ccccccccccc", "Genetikk & Sido", "Lieb's oder lass es", 182)]
        self.assertEqual(spotify._pick(t, res, True)["url"][-11:], "ccccccccccc")     # Apostroph egal, kein Instrumental
        # Live nur, wenn Spotify es so nennt
        live = [song("ddddddddddd", "Sido", "Endstation (Live)", 184)]
        self.assertIsNone(spotify._pick({"title": "Endstation", "artist": "Sido", "duration": 184}, live, True))
        self.assertIsNotNone(spotify._pick({"title": "Endstation - Live", "artist": "Sido", "duration": 183}, live, True))
        # andere Laenge: erst im zweiten Durchgang
        kurz = [song("eeeeeeeeeee", "Kool Savas & Sido", "Unterschied", 171)]
        u = {"title": "Unterschied", "artist": "Savas, Sido", "duration": 224}
        self.assertIsNone(spotify._pick(u, kurz, True))
        self.assertIsNotNone(spotify._pick(u, kurz, False))

    def test_liste_wird_zu_ladbaren_eintraegen(self):
        self.fake([{"title": "Ganz unten", "artist": "Sido", "duration": 212},
                   {"title": "Astronaut", "artist": "Sido, Andreas Bourani", "duration": 238},
                   {"title": "Gibt es nicht", "artist": "Niemand", "duration": 200}],
                  {"Ganz unten": [song("idaaaaaaaaa", "Sido", "Ganz unten", 213)],
                   "Astronaut": [song("idbbbbbbbbb", "Sido", "Astronaut (feat. Andreas Bourani)", 239)]})
        schritte = []

        async def progress(phase, done, total):
            schritte.append((phase, done, total))
        entries, title = self.run_async(download._audit_playlist_for_videos(PL, progress))
        self.assertEqual(title, "Best of Sido")
        self.assertEqual([(e["url"][-11:], e["title"], e["duration"]) for e in entries],
                         [("idaaaaaaaaa", "Sido - Ganz unten", 213),
                          ("idbbbbbbbbb", "Sido, Andreas Bourani - Astronaut", 239)])
        self.assertEqual(spotify.not_found[PL], ["Niemand - Gibt es nicht"])
        self.assertEqual(schritte[-1], ("search", 3, 3))

    def test_kaestchen_nennt_was_fehlt(self):
        alt = self.tmp / "Sammlung" / "Sido - Ganz unten.mp3"
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_bytes(b"ID3")
        main._state["library"] = [{"path": str(alt), "title": "Sido - Ganz unten", "artist": "Sido", "duration_sec": 212}]
        self.fake([{"title": "Ganz unten", "artist": "Sido", "duration": 212},
                   {"title": "Hamdullah", "artist": "Sido", "duration": 189},
                   {"title": "Gibt es nicht", "artist": "Niemand", "duration": 200}],
                  {"Ganz unten": [song("idaaaaaaaaa", "Sido", "Ganz unten", 213)],
                   "Hamdullah": [song("idccccccccc", "Sido", "Hamdullah", 190)]})
        ws = FakeWS()
        self.run_async(download._spotify_add(PL + "?si=abc", "mp3-best", ws))
        plan = [m for m in ws.sent if m.get("type") == "playlist_plan"][0]
        self.assertEqual(plan["url"], PL)                                    # ohne ?si=
        self.assertEqual((plan["total"], plan["have"], plan["new"]), (2, 1, 1))
        self.assertEqual(plan["not_found"], ["Niemand - Gibt es nicht"])
        self.assertFalse(plan["truncated"])

    def test_nicht_lesbar_gibt_eine_meldung_statt_stillstand(self):
        spotify._fetch_sync = lambda url: None
        ws = FakeWS()
        self.run_async(download._spotify_add(PL, "mp3-best", ws))
        self.assertFalse([m for m in ws.sent if m.get("type") == "playlist_plan"])
        d = main._state["downloads"][0]
        self.assertEqual(d["status"], "error")
        self.assertIn("nicht lesbar", d["status_text"])
        # auch beim Pruefen einer verfolgten Playlist: Fehler, kein Haenger
        main._state["downloads"].clear()
        self.assertIsNone(self.run_async(download.run_download(PL, "mp3-best")))
        self.assertEqual(main._state["downloads"][0]["status"], "error")

    def test_mehr_als_100_titel_wird_gesagt(self):
        self.fake([{"title": f"Lied {i}", "artist": "Sido", "duration": 200} for i in range(100)], {})
        self.run_async(spotify.resolve(PL))
        self.assertTrue(spotify.truncated[PL])
        self.assertEqual(len(spotify.not_found[PL]), 100)
