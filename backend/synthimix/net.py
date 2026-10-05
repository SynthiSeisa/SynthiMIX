"""HTTPS fuer alle Abrufe des Backends (Werkzeug-Updates, Last.fm, AcoustID …).

Python prueft Zertifikate gegen den Windows-Speicher — und der ist an frisch
aufgesetzten oder selten aktualisierten PCs lueckenhaft: Windows laedt fehlende
Stammzertifikate erst nach, wenn ein Windows-eigenes Programm die Seite
aufruft, Python loest das nicht aus. Folge (gemeldet 10/2026 an einem fremden
PC): "CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate" bei
jedem Abruf.

Deshalb: (1) zusaetzlich die mitgelieferte Mozilla-Liste (certifi), (2) bleibt
es beim Zertifikatsfehler, einmal je Seite das Windows-eigene curl aufrufen —
das stoesst das Nachladen an — und noch einmal versuchen.
install() haengt das fuer urllib.request.urlopen/urlretrieve global ein.
"""
import http.client
import os
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_primed: dict[str, bool] = {}          # Seite -> hat das Nachladen geklappt?


def make_context() -> ssl.SSLContext:
    """Windows-Speicher plus mitgelieferte Zertifikatsliste (falls vorhanden)."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception as e:
        print(f"[netz] eigene Zertifikatsliste fehlt: {e}", flush=True)
    return ctx


def is_cert_error(e: BaseException) -> bool:
    reason = getattr(e, "reason", e)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def _windows_curl() -> str | None:
    p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
    return p if os.path.isfile(p) else None


def prime(host: str) -> bool:
    """Windows das fehlende Stammzertifikat fuer diese Seite nachladen lassen
    (curl aus Windows prueft ueber Schannel). Einmal je Seite."""
    if not host:
        return False
    if host not in _primed:
        curl, ok = _windows_curl(), False
        if curl:
            try:
                r = subprocess.run([curl, "-s", "-o", "NUL", "-I", "--max-time", "12", f"https://{host}/"],
                                   capture_output=True, timeout=20, creationflags=_NO_WINDOW)
                ok = r.returncode == 0
            except Exception:
                ok = False
        _primed[host] = ok
        print(f"[netz] Zertifikat fuer {host} ueber Windows nachgeladen: {'ja' if ok else 'nein'}", flush=True)
    return _primed[host]


class _Https(urllib.request.HTTPSHandler):
    def https_open(self, req):
        try:
            return self.do_open(http.client.HTTPSConnection, req, context=self._context)
        except urllib.error.URLError as e:
            if not is_cert_error(e):
                raise
            host = urllib.parse.urlsplit(req.full_url).hostname or ""
            if not prime(host):
                raise
            self._context = make_context()          # Windows-Speicher neu einlesen
            return self.do_open(http.client.HTTPSConnection, req, context=self._context)


def install():
    """Fuer alle urllib-Abrufe des Backends einhaengen (einmal beim Start)."""
    urllib.request.install_opener(urllib.request.build_opener(_Https(context=make_context())))


def check(url: str = "https://api.github.com/") -> dict:
    """Fuer die Diagnose: klappt ein gesicherter Abruf? {ok, text}"""
    try:
        import certifi  # noqa: F401
        own = "mit eigener Zertifikatsliste"
    except Exception:
        own = "ohne eigene Zertifikatsliste"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SynthiMIX"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return {"ok": True, "text": f"gesicherte Verbindung steht ({r.status}), {own}", "date": r.headers.get("Date")}
    except urllib.error.HTTPError as e:
        return {"ok": True, "text": f"gesicherte Verbindung steht (Antwort {e.code}), {own}", "date": e.headers.get("Date")}
    except Exception as e:
        hint = " — Zertifikat fehlt in Windows und liess sich nicht nachladen" if is_cert_error(e) else ""
        return {"ok": False, "text": f"{getattr(e, 'reason', e)}{hint} ({own})"}
