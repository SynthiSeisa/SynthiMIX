"""Dienste: Testergebnis ueber Neustart/Update, spotdl-Status bei langsamem Start."""
import subprocess

from tests.support import BackendTest, main
from synthimix import store, tools, core


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


class SpotdlStatus(BackendTest):

    def test_langsamer_start_gilt_trotzdem_als_installiert(self):
        exe = self.tmp / "spotdl.exe"
        exe.write_bytes(b"MZ" + b"\0" * 100)
        keep_local, keep_run = core.SPOTDL_LOCAL, tools.subprocess.run
        core.SPOTDL_LOCAL = exe
        tools._spotdl_ver_cache.clear()
        def slow(*a, **kw):
            raise subprocess.TimeoutExpired(a[0], kw.get("timeout", 0))
        tools.subprocess.run = slow
        try:
            self.assertEqual(tools._spotdl_version_sync(), "installiert")
        finally:
            core.SPOTDL_LOCAL, tools.subprocess.run = keep_local, keep_run

    def test_version_wird_gemerkt(self):
        exe = self.tmp / "spotdl.exe"
        exe.write_bytes(b"MZ" + b"\0" * 100)
        keep_local, keep_run = core.SPOTDL_LOCAL, tools.subprocess.run
        core.SPOTDL_LOCAL = exe
        tools._spotdl_ver_cache.clear()
        calls = []
        class R:
            stdout, stderr, returncode = "4.2.11\n", "", 0
        def fake(*a, **kw):
            calls.append(1)
            return R()
        tools.subprocess.run = fake
        try:
            self.assertEqual(tools._spotdl_version_sync(), "4.2.11")
            self.assertEqual(tools._spotdl_version_sync(), "4.2.11")
            self.assertEqual(len(calls), 1)
        finally:
            core.SPOTDL_LOCAL, tools.subprocess.run = keep_local, keep_run
