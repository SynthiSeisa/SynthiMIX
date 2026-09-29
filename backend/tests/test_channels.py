"""Kanaele verfolgen: Auswahl, neue Playlists, Ablage unter Downloads/<Kanal>/."""
from tests.support import BackendTest, FakeWS, main
from synthimix import channels, download


def pl(i, title=None):
    return {"url": f"https://www.youtube.com/playlist?list=PLtest{i}", "title": title or f"Playlist {i}", "thumb": ""}


class ChannelTest(BackendTest):

    def setUp(self):
        super().setUp()
        self._keep = (channels._channel_playlists, download._follow_check_all, main._state.get("followed"),
                      main._state.get("followed_channels"))
        main._state["followed"] = []
        main._state["followed_channels"] = []
        self.listing = ("Test Kanal", [pl(1), pl(2), pl(3)])
        self.checked = []

        async def fake_list(base):
            return self.listing
        channels._channel_playlists = fake_list

    def tearDown(self):
        (channels._channel_playlists, download._follow_check_all,
         main._state["followed"], main._state["followed_channels"]) = self._keep
        super().tearDown()

    def test_kanal_adresse(self):
        b = channels._channel_base
        self.assertEqual(b("https://www.youtube.com/@UKFDrumandBass/playlists"), "https://www.youtube.com/@UKFDrumandBass")
        self.assertEqual(b("https://music.youtube.com/channel/UCabc_123"), "https://www.youtube.com/channel/UCabc_123")
        self.assertEqual(b("https://youtube.com/c/Name/videos"), "https://www.youtube.com/c/Name")
        self.assertIsNone(b("https://www.youtube.com/playlist?list=PL1"))
        self.assertIsNone(b("https://www.youtube.com/watch?v=abcdefghijk"))
        self.assertFalse(download._is_playlist("https://www.youtube.com/@x/playlists") and channels._channel_base("x"))

    def _plan_and_follow(self, selected, auto_new=True):
        ws = FakeWS()
        self.run_async(channels._plan_channel("https://www.youtube.com/@test/playlists", "mp3-best", ws))
        plan = ws.of_type("channel_plan")[-1]
        self.assertEqual(plan["title"], "Test Kanal")
        self.assertEqual(len(plan["playlists"]), 3)

        async def fake_check(urls=None):
            self.checked.append(urls)
        download._follow_check_all = fake_check

        async def go():
            await channels._channel_follow({"plan_id": plan["plan_id"], "selected": selected, "auto_new": auto_new})
            await __import__("asyncio").sleep(0)
        self.run_async(go())
        return plan

    def test_auswahl_verfolgen_und_laden(self):
        self._plan_and_follow([pl(1)["url"], pl(3)["url"]])
        f = main._state["followed"]
        self.assertEqual([x["url"] for x in f], [pl(1)["url"], pl(3)["url"]])
        self.assertTrue(all(x["mode"] == "playlist" and x["parent"] == "Test Kanal" for x in f))
        ch = main._state["followed_channels"][0]
        self.assertEqual(ch["excluded"], [pl(2)["url"]])
        self.assertEqual(self.checked, [[pl(1)["url"], pl(3)["url"]]])     # alles laden

    def test_neue_playlist_automatisch_oder_als_vorschlag(self):
        self._plan_and_follow([pl(1)["url"], pl(3)["url"]])
        ch = main._state["followed_channels"][0]
        self.listing = ("Test Kanal", [pl(1), pl(2), pl(3), pl(4, "Neu")])
        urls = self.run_async(channels._channel_check(ch))
        self.assertIn(pl(4)["url"], urls)                                 # automatisch dazu
        self.assertNotIn(pl(2)["url"], urls)                              # abgewaehlt bleibt draussen
        ch["auto_new"] = False
        self.listing = ("Test Kanal", [pl(1), pl(2), pl(3), pl(4), pl(5, "Noch neuer")])
        urls = self.run_async(channels._channel_check(ch))
        self.assertNotIn(pl(5)["url"], urls)
        self.assertEqual([p["title"] for p in ch["pending"]], ["Noch neuer"])

    def test_abwaehlen_und_entfernen(self):
        plan = self._plan_and_follow([pl(1)["url"], pl(2)["url"]])
        self.run_async(channels._plan_channel("https://www.youtube.com/@test", "mp3-best", FakeWS()))
        pid = max(channels._channel_plans)
        self.run_async(channels._channel_follow({"plan_id": pid, "selected": [pl(2)["url"]], "auto_new": True}))
        self.assertEqual([x["url"] for x in main._state["followed"]], [pl(2)["url"]])
        self.run_async(channels._channel_remove(plan["url"]))
        self.assertEqual(main._state["followed"], [])
        self.assertEqual(main._state["followed_channels"], [])

    def test_gespeichert(self):
        self._plan_and_follow([pl(1)["url"]])
        main._state["followed_channels"] = []
        main.load_settings()
        self.assertEqual(main._state["followed_channels"][0]["title"], "Test Kanal")
