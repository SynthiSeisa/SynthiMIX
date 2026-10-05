"""Gesicherte Abrufe: eigene Zertifikatsliste und Nachladen ueber Windows,
wenn das Stammzertifikat im Windows-Speicher fehlt."""
import ssl
import unittest
import urllib.error
import urllib.request

from synthimix import net


class NetTest(unittest.TestCase):

    def setUp(self):
        net._primed.clear()

    def test_eigene_zertifikatsliste_ist_geladen(self):
        # die Mozilla-Liste hat weit ueber 100 Stammzertifikate
        self.assertGreater(net.make_context().cert_store_stats()["x509_ca"], 100)

    def test_zertifikatsfehler_wird_erkannt(self):
        e = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"))
        self.assertTrue(net.is_cert_error(e))
        self.assertFalse(net.is_cert_error(urllib.error.URLError(TimeoutError("timed out"))))

    def _handler(self, results):
        calls = []
        h = net._Https(context=net.make_context())

        def do_open(conn, req, **kw):
            calls.append(req.full_url)
            r = results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        h.do_open = do_open
        return h, calls

    def test_nach_dem_nachladen_wird_noch_einmal_versucht(self):
        cert = urllib.error.URLError(ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED"))
        h, calls = self._handler([cert, "antwort"])
        primed = []
        echt = net.prime
        net.prime = lambda host: (primed.append(host), True)[1]
        try:
            self.assertEqual(h.https_open(urllib.request.Request("https://api.github.com/x")), "antwort")
        finally:
            net.prime = echt
        self.assertEqual(primed, ["api.github.com"])
        self.assertEqual(len(calls), 2)

    def test_klappt_das_nachladen_nicht_bleibt_der_fehler(self):
        cert = urllib.error.URLError(ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED"))
        h, calls = self._handler([cert])
        echt = net.prime
        net.prime = lambda host: False
        try:
            with self.assertRaises(urllib.error.URLError):
                h.https_open(urllib.request.Request("https://api.github.com/x"))
        finally:
            net.prime = echt
        self.assertEqual(len(calls), 1)

    def test_andere_fehler_loesen_kein_nachladen_aus(self):
        h, calls = self._handler([urllib.error.URLError(TimeoutError("timed out"))])
        echt = net.prime
        net.prime = lambda host: self.fail("darf nicht nachladen")
        try:
            with self.assertRaises(urllib.error.URLError):
                h.https_open(urllib.request.Request("https://api.github.com/x"))
        finally:
            net.prime = echt

    def test_nachladen_nur_einmal_je_seite(self):
        runs = []
        echt_run, echt_curl = net.subprocess.run, net._windows_curl
        net._windows_curl = lambda: "curl.exe"

        class R:
            returncode = 0
        net.subprocess.run = lambda *a, **k: (runs.append(a[0]), R())[1]
        try:
            self.assertTrue(net.prime("example.org"))
            self.assertTrue(net.prime("example.org"))
        finally:
            net.subprocess.run, net._windows_curl = echt_run, echt_curl
        self.assertEqual(len(runs), 1)
        self.assertIn("https://example.org/", runs[0])
