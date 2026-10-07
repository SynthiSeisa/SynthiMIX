"""Dienste: Testergebnis ueber Neustart/Update."""
from tests.support import BackendTest, main
from synthimix import store, core


class ServiceTestsRemembered(BackendTest):

    def test_ok_bleibt_ueber_neustart(self):
        main._state["lastfm_api_key"] = "abc123"
        store.remember_service_tests({"lastfm": {"ok": True, "text": "Verbunden."},
                                      "acoustid": {"ok": False, "text": "Kein Key eingetragen."}})
        # "Neustart": Einstellungen neu laden
        main._state.pop("service_ok", None)
        store.load_settings()
        known = store.known_service_tests()
        self.assertTrue(known["lastfm"]["ok"])
        self.assertIn("zuletzt getestet", known["lastfm"]["text"])
        self.assertNotIn("acoustid", known)
        # Im gespeicherten Stand steht nur ein Hash, nie der Key
        self.assertNotIn("abc123", (core.SETTINGS_FILE).read_text(encoding="utf-8").split('"service_ok"')[1])

    def test_neuer_key_gilt_als_ungetestet(self):
        main._state["lastfm_api_key"] = "abc123"
        store.remember_service_tests({"lastfm": {"ok": True, "text": "Verbunden."}})
        main._state["lastfm_api_key"] = "anderer"
        self.assertNotIn("lastfm", store.known_service_tests())

    def test_fehlschlag_loescht_alten_erfolg(self):
        main._state["lastfm_api_key"] = "abc123"
        store.remember_service_tests({"lastfm": {"ok": True, "text": "Verbunden."}})
        store.remember_service_tests({"lastfm": {"ok": False, "text": "Keine Verbindung."}})
        self.assertNotIn("lastfm", store.known_service_tests())


class ServiceCheckOnStart(BackendTest):
    """Beim Start werden die eingetragenen Dienste einmal echt geprueft."""

    def setUp(self):
        super().setUp()
        from synthimix import tags
        self.tags = tags
        self._keep = (tags._test_services, core.broadcast, tags._services_checked,
                      {k: main._state.get(k) for k in ("lastfm_api_key", "acoustid_api_key",
                                                         "spotify_client_id", "service_ok")})
        self.sent, self.answers = [], []

        async def fake_test():
            return self.answers.pop(0)

        async def fake_broadcast(msg):
            self.sent.append(msg)

        tags._test_services, core.broadcast = fake_test, fake_broadcast
        tags._services_checked = False
        main._state.update(lastfm_api_key="abc123", acoustid_api_key="", spotify_client_id="", service_ok={})

    def tearDown(self):
        self.tags._test_services, core.broadcast, self.tags._services_checked, st = self._keep
        for k, v in st.items():
            if v is None:
                main._state.pop(k, None)
            else:
                main._state[k] = v
        super().tearDown()

    def test_verbunden_wird_gemeldet_und_gemerkt(self):
        self.answers = [{"lastfm": {"ok": True, "text": "Verbunden."},
                         "acoustid": {"ok": False, "text": "Kein Key eingetragen."}}]
        self.run_async(self.tags._check_services_on_start())
        self.assertEqual(self.sent, [{"type": "services_test", "lastfm": {"ok": True, "text": "Verbunden."}}])
        self.assertIn("lastfm", store.known_service_tests())
        # nur einmal je Start
        self.run_async(self.tags._check_services_on_start())
        self.assertEqual(len(self.sent), 1)

    def test_ohne_netz_bleibt_der_gemerkte_stand_und_es_wird_nochmal_versucht(self):
        store.remember_service_tests({"lastfm": {"ok": True, "text": "Verbunden."}})
        self.answers = [{"lastfm": {"ok": False, "text": "Keine Verbindung zu Last.fm."}},
                        {"lastfm": {"ok": True, "text": "Verbunden."}}]
        self.run_async(self.tags._check_services_on_start(wait_s=0))
        self.assertTrue(self.sent[0]["lastfm"]["ok"])            # gemerkter Stand, kein Fehler
        self.assertEqual(self.sent[1]["lastfm"]["text"], "Verbunden.")
        self.assertIn("lastfm", store.known_service_tests())

    def test_abgelehnter_key_wird_gemeldet(self):
        store.remember_service_tests({"lastfm": {"ok": True, "text": "Verbunden."}})
        self.answers = [{"lastfm": {"ok": False, "text": "Last.fm lehnt den Key ab: Invalid API key"}}]
        self.run_async(self.tags._check_services_on_start())
        self.assertFalse(self.sent[0]["lastfm"]["ok"])
        self.assertNotIn("lastfm", store.known_service_tests())
