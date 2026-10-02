"""Taktraster, Takt und Phrasen fuer den Beat-Sync beim Uebergang."""
import asyncio
import json
import math
import os
import subprocess
from .core import _NO_WINDOW, _state
from . import core, media, store

# ── Taktraster (Beat-Sync beim Uebergang) ────────────────────────────────────
_beatgrid_cache: dict = {}    # Pfad -> Raster, auch fuer Titel ausserhalb der Bibliothek

# Version des Messverfahrens: aeltere Raster werden beim naechsten Abspielen neu gemessen
GRID_REV = 2

# Abspielen im Player (Chromium) gegen Dekodieren mit ffmpeg: Opus und Vorbis
# erklingen etwas frueher, als die Abspielposition anzeigt (Encoder-Vorlauf).
# Gemessen 09/2026 mit Klick-Dateien; MP3, M4A, FLAC und WAV liegen genau.
_PLAYBACK_SHIFT = {".opus": -0.0046, ".webm": -0.0046, ".ogg": -0.0029}

# Baender fuer das Anschlagsignal (von, bis in Hz, Gewicht). An 90 Titeln mit
# Mixed-In-Key-Cues bestimmt: das Tief-Band allein (bis 1.6 das einzige) liegt
# bei modernem DnB oft eine Achtel daneben, weil der Sub-Bass durchlaeuft.
_BANDS = ((180, 1000, 1.0), (1000, 5000, 1.0), (6000, 11025, 0.5), (0, 11025, 1.0))
_SR, _HOP = 22050, 32                       # 1,45 ms je Schritt
# DnB wird ohne Tag oft mit 2/3 des Tempos gemessen (116 statt 174): dann
# liegen die echten Schlaege auf 1/3 und 2/3 des gemessenen. Grenzen an 104
# Titeln (80 echte mit MIK-Tag, 24 kuenstliche) bestimmt: noetig 1,96-2,0,
# sonst hoechstens 1,37.
_TRIPLET_MIN, _TRIPLET_OVER_HALF = 1.7, 1.0
_OFFBEAT_BASS = 3.0                         # so viel mehr Bass auf der Achtel dazwischen: Raster verschieben


def _fft_len(n: int) -> int:
    """Kleinste Laenge >= n der Form 2^k, 3*2^k, 5*2^k oder 7*2^k (schnelle FFT, wenig Polster)."""
    best = 1 << (n - 1).bit_length()
    for m in (3, 5, 7):
        k = max(0, (n // m - 1).bit_length())
        while m << k < n:
            k += 1
        best = min(best, m << k)
    return best


def _onset(y, np):
    """Anschlagstaerke je 1,45 ms: Anstieg der logarithmischen Energie, ohne langsame Pegelaenderungen."""
    n = y.size // _HOP
    fr = y[:n * _HOP].reshape(n, _HOP)
    env = np.log1p(200 * np.sqrt((fr * fr).mean(axis=1) + 1e-12))
    env = np.convolve(env, np.ones(3) / 3, mode="same")
    o = np.maximum(0.0, np.diff(env, prepend=env[0]))
    return np.maximum(0.0, o - np.convolve(o, np.ones(128) / 128, mode="same"))


def _near(onset, fps, period, off, tol, np):
    """Anschlags-Energie innerhalb ±tol um die Schlaege eines Rasters."""
    t = np.arange(onset.size) / fps
    d = np.abs(np.mod(t - off + period / 2, period) - period / 2)
    return float(onset[d <= tol].sum())


def _comb(onset, fps, bpms, np):
    """Kammfilter: fuer jedes Tempo die Phase mit der meisten Energie auf den Schlaegen."""
    n = onset.size
    best = (-1.0, 0.0, 0.0, 0.0)            # Summe, bpm, Phase (Frames), Mittel
    for bpm in bpms:
        period = fps * 60.0 / bpm
        nb = int((n - 1) / period)
        if nb < 8:
            continue
        phases = np.arange(int(math.ceil(period)), dtype=np.float64)
        idx = np.minimum(np.rint(phases[:, None] + np.arange(nb)[None, :] * period).astype(np.int64), n - 1)
        sums = onset[idx].sum(axis=1)
        j = int(np.argmax(sums))
        if sums[j] > best[0]:
            best = (float(sums[j]), float(bpm), float(j), float(sums.mean()))
    return best


def _conf_of(b) -> float:
    return 0.0 if b[0] <= 0 else (b[0] - b[3]) / b[0]


def _tempo(onset, fps, bpm_hint, np):
    """Tempo: Autokorrelation + Kamm; Kandidaten aus dem Tag und der eigenen
    Schaetzung, jeweils auch halbes/doppeltes Tempo."""
    m = min(onset.size, int(fps * 120))
    seg = onset[:m] - onset[:m].mean()
    spec = np.fft.rfft(seg, 2 * m)
    ac = np.fft.irfft(spec * np.conj(spec))[:m]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    if hi <= lo + 1 or hi >= m:
        return None
    auto = fps * 60 / (lo + int(np.argmax(ac[lo:hi])))
    centers: list[float] = []
    for c0 in ([float(bpm_hint)] if bpm_hint and bpm_hint > 0 else []) + [auto]:
        for c in (c0, c0 * 2, c0 / 2):
            if 60 <= c <= 200 and all(abs(c - d) > 1.5 for d in centers):
                centers.append(c)
    res = [r_ for r_ in (_comb(onset, fps, np.arange(c - 1.5, c + 1.5001, 0.05), np) for c in centers) if r_[1] > 0]
    if not res:
        return None
    # Halbes Tempo wirkt im Kamm immer etwas "deutlicher": fast gleich gut
    # (85 %) reicht fuer das Tempo aus den Tags, sonst fuer das schnellere
    top = max(_conf_of(r_) for r_ in res)
    good = [r_ for r_ in res if _conf_of(r_) >= 0.85 * top]
    tagged = [r_ for r_ in good if bpm_hint and abs(r_[1] - bpm_hint) <= 2]
    c = tagged[0] if tagged else max(good, key=lambda r_: r_[1])
    bpm = c[1]
    if not tagged and bpm * 1.5 <= 200 and _triplet_up(onset, fps, bpm, np):
        bpm *= 1.5
    fine = _comb(onset, fps, np.arange(bpm - 0.06, bpm + 0.0601, 0.005), np)
    return fine[1], _conf_of(fine)


def _triplet_up(onset, fps, bpm, np) -> bool:
    """Ist bpm nur 2/3 des echten Tempos? Auf einen gemessenen Schlag gefaltet
    liegen die echten Schlaege dann auf 1/3 und 2/3, bei geradem Tempo
    liegt der Zwischenschlag auf 1/2."""
    T, nb = 60.0 / bpm, 48
    t = np.arange(onset.size) / fps
    h = np.bincount((np.mod(t, T) / T * nb).astype(int) % nb, weights=onset, minlength=nb)
    h = np.convolve(np.concatenate([h[-2:], h, h[:2]]), np.ones(3) / 3, mode="same")[2:-2]
    if h.max() <= 0:
        return False
    h = np.roll(h, -int(np.argmax(h))) / h.max()          # Schlag auf 0

    def at(x):
        return float(h[[int(round(x * nb + d)) % nb for d in (-1, 0, 1)]].max())
    tri = at(1 / 3) + at(2 / 3)
    return tri >= _TRIPLET_MIN and tri - at(1 / 2) >= _TRIPLET_OVER_HALF


def _fold_phase(onset, fps, period, np, smooth_ms=5):
    """Lage (s) mit der meisten Anschlags-Energie im auf einen Schlag gefalteten Profil (1-ms-Bins)."""
    nb = max(8, int(round(period * 1000)))
    t = np.arange(onset.size) / fps
    h = np.bincount((np.mod(t, period) / period * nb).astype(int) % nb, weights=onset, minlength=nb)
    k = smooth_ms
    h = np.convolve(np.concatenate([h[-k:], h, h[:k]]), np.ones(k) / k, mode="same")[k:-k]
    j = int(np.argmax(h))
    y0, y1, y2 = h[j - 1], h[j], h[(j + 1) % nb]
    den = y0 - 2 * y1 + y2
    frac = 0.5 * (y0 - y2) / den if den != 0 else 0.0
    return ((j + frac) / nb) * period


def _fit_beats(onset, fps, period, off, dur, np):
    """Jeden Schlag einzeln suchen (Fenster schrumpft 30 -> 15 -> 8 ms, nur die
    deutlichsten zaehlen) und eine robuste Gerade durch die Anschlaege legen:
    Steigung = Schlaglaenge, Achsenabschnitt = Lage. (Periode, Lage, Streuung ms) oder None."""
    T, res = period, None
    for win_ms, keep in ((30, 0.6), (15, 0.5), (8, 0.5)):
        w = max(2, int(win_ms / 1000 * fps))
        ks, ts, amps = [], [], []
        k = int(math.ceil(-off / T))
        while True:
            t = off + k * T
            if t > dur - 0.05:
                break
            c = int(round(t * fps))
            if c - w - 1 >= 0 and c + w + 1 < onset.size:
                seg = onset[c - w:c + w + 1]
                j = int(np.argmax(seg))
                if seg[j] > 0 and 0 < j < seg.size - 1:
                    y0, y1, y2 = seg[j - 1], seg[j], seg[j + 1]
                    den = y0 - 2 * y1 + y2
                    frac = 0.5 * (y0 - y2) / den if den != 0 else 0.0
                    ks.append(k); ts.append((c - w + j + frac) / fps); amps.append(seg[j])
            k += 1
        if len(ks) < 16:
            return None
        ks, ts, amps = np.array(ks, float), np.array(ts), np.array(amps)
        sel = amps >= np.quantile(amps, 1 - keep)
        kk, tt, aa = ks[sel], ts[sel], amps[sel]
        for _ in range(3):                     # Ausreisser (> 3 MAD) raus
            A = np.vstack([kk, np.ones_like(kk)]).T
            W = np.sqrt(aa)
            sol, *_ = np.linalg.lstsq(A * W[:, None], tt * W, rcond=None)
            res = tt - (sol[0] * kk + sol[1])
            mad = np.median(np.abs(res - np.median(res))) + 1e-5
            ok = np.abs(res) <= max(3 * mad, 0.003)
            if ok.all():
                break
            kk, tt, aa = kk[ok], tt[ok], aa[ok]
            if kk.size < 12:
                return None
        T, off = float(sol[0]), float(sol[1])
    return T, off % T, float(np.median(np.abs(res)) * 1000)


def _beatgrid_sync(path: str, bpm_hint: float = 0.0) -> dict | None:
    """Genaues Tempo und Lage des ersten Schlags.

    1. Einmal dekodieren, per FFT in Baender teilen (180-1000 Hz, 1-5 kHz,
       >6 kHz, voll) und daraus ein Anschlagsignal in 1,45-ms-Schritten.
    2. Tempo per Kammfilter (Kandidaten aus dem Tag und eigener Schaetzung).
    3. Lage aus dem auf einen echten Schlag gefalteten Profil (1 ms genau).
    4. Jeden Schlag einzeln suchen, robuste Gerade: Tempo und Lage auf
       Bruchteile einer Millisekunde, keine Drift ueber den Titel.
    5. Halbtempo-Raster (DnB 87): die Eins ist der Halbschlag mit mehr
       Bassdrum und weniger Snare-Koerper (180-1000 Hz).
    Gemessen 09/2026: kuenstliche Titel mit bekanntem Raster max. 4 ms Fehler
    (vorher 90 ms), 90 Titel gegen Mixed-In-Key-Cues median 4 ms Abweichung
    (vorher 10 ms), die Eins bei 87er-Rastern 86 % richtig (vorher 72 %).
    beat_conf (0..1): Deutlichkeit im Kamm, gedaempft durch die Streuung der
    Anschlaege um die Gerade (Live-Schlagzeug) — unter ~0.4 legt der Player
    die Schlaege nicht uebereinander.
    """
    if not media._numpy_ok():
        return None
    import numpy as np
    try:
        r = subprocess.run([core.FFMPEG, "-nostdin", "-v", "error", "-i", path, "-ac", "1", "-ar", str(_SR),
                            "-f", "s16le", "-acodec", "pcm_s16le", "-"],
                           capture_output=True, timeout=120, creationflags=_NO_WINDOW)
    except Exception:
        return None
    x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if x.size < _SR * 20:
        return None
    n = _fft_len(x.size)
    X = np.fft.rfft(x, n)
    f = np.fft.rfftfreq(n, 1 / _SR)

    def band(lo, hi):
        return np.fft.irfft(X * ((f >= lo) & (f < hi)), n)[:x.size].astype(np.float32)

    o = None
    for lo, hi, w in _BANDS:
        ob = _onset(band(lo, hi), np)
        ob = ob / (ob.mean() + 1e-12) * w
        o = ob if o is None else o + ob
    low, lowmid = _onset(band(0, 180), np), _onset(band(180, 1000), np)
    del X
    fps = _SR / _HOP
    n4 = o.size // 4
    tp = _tempo(o[:n4 * 4].reshape(n4, 4).sum(axis=1), fps / 4, bpm_hint, np)
    if not tp:
        return None
    bpm0, conf = tp
    T = 60.0 / bpm0
    half = bpm0 < 100                          # Halbtempo-Raster: zwei echte Schlaege je Rasterschlag
    Tb = T / 2 if half else T
    off = _fold_phase(o, fps, Tb, np)
    fit = _fit_beats(o, fps, Tb, off, x.size / _SR, np)
    if fit and abs(fit[0] - Tb) / Tb < 0.004 and \
            _near(o, fps, fit[0], fit[1], 0.008, np) >= _near(o, fps, Tb, off, 0.008, np):
        Tb, off = fit[0], fit[1]
        # Streuung um die Gerade: elektronisch ~0,5 ms, Live-Schlagzeug ~3 ms
        conf *= min(1.0, max(0.5, 1.0 - max(0.0, fit[2] - 1.5) * 0.17))
    else:
        fit = None
    # Bassdrum ohne Obertoene, Hi-Hat auf der Achtel dazwischen: dann rasten die
    # oberen Baender auf die Hi-Hat ein. Liegt der Bass klar auf der anderen
    # Haelfte, gehoert der Schlag dorthin.
    lo_on, lo_off = _near(low, fps, Tb, off, 0.012, np), _near(low, fps, Tb, off + Tb / 2, 0.012, np)
    if lo_off > _OFFBEAT_BASS * lo_on:
        off += Tb / 2
    T = Tb * 2 if half else Tb
    if half:
        def nd(ob):
            a_, b_ = _near(ob, fps, T, off, 0.012, np), _near(ob, fps, T, off + Tb, 0.012, np)
            return (a_ - b_) / (a_ + b_ + 1e-12)
        if nd(low) - nd(lowmid) < 0:
            off += Tb
    off += _PLAYBACK_SHIFT.get(os.path.splitext(path)[1].lower(), 0.0)
    return {"bpm_f": round(60.0 / T, 4), "beat_off": round(float(off % T), 5),
            "beat_conf": round(max(0.0, min(1.0, conf)), 3), "grid_rev": GRID_REV,
            **({"grid_fit": True} if fit else {})}

_GRID_KEYS = ("bpm_f", "beat_off", "beat_conf", "grid_rev", "phrase_off", "bar_beats", "phrase_src",
              "drops", "drop_src", "drop_rev")
# Haengen am Raster: mit neuem Raster (oder neuen MIK-Cues) neu bestimmen
_PHRASE_KEYS = ("phrase_off", "bar_beats", "phrase_src", "drops", "drop_src", "drop_rev")


# ── Drop-Erkennung ───────────────────────────────────────────────────────────
# Aus Synthi's Mashups (sythi/mashup.py, find_drop) uebernommen und auf alle
# Drops eines Titels erweitert (Double Drop braucht mehrere). Vorher schaetzte
# die Oberflaeche die Drops aus der groben Wellenform (nur Lautstaerke, feste
# dB-Schwellen je 8 Takte) — ohne Bass und ohne die MIK-Cues als Vorschlag.
DROP_SR = 4000                 # reicht fuer Lautheit und Bass (< 150 Hz)
DROP_REV = 1


def _bar_features(x, sr: int, start: float, bar: float, n: int, np):
    """Je Takt: Lautheit (log) und Bass bis 150 Hz (log), beide z-normiert."""
    rms = np.full(n, -12.0)
    low = np.full(n, -12.0)
    for b in range(n):
        seg = x[int((start + b * bar) * sr):int((start + (b + 1) * bar) * sr)]
        if seg.size < 16:
            continue
        spec = np.abs(np.fft.rfft(seg)) ** 2 / seg.size
        k = max(1, int(150 * seg.size / sr))
        rms[b] = np.log(spec.sum() + 1e-9)
        low[b] = np.log(spec[:k].sum() + 1e-9)

    def z(v):
        return (v - np.median(v)) / (np.std(v) + 1e-6)
    return z(rms), z(low)


def _find_drops(x, sr: int, bpm: float, off: float, phrase_off, bar_beats: int = 4,
                cues: list | None = None) -> tuple[list[float], str]:
    """Wo setzen die Drops ein? Sekunden auf Taktanfaengen, frueheste zuerst.

    Je Takt Lautheit und Bass (< 150 Hz). Ein Drop steigt nach einem Build-up
    (davor wenig Bass) deutlich an UND erreicht das Plateau der lautesten Teile
    — ein Sprung vom Intro in den Mittelteil zaehlt nicht. Gesucht wird auf
    MIK-Cues (MIK setzt sie auf Phrasen, einer ist fast immer der Drop) und auf
    8-Takt-Phrasen; zwei Drops liegen mindestens 16 Takte auseinander.
    x: Mono-Signal mit Abtastrate sr. Liefert (Zeiten, Quelle der ersten)."""
    import numpy as np
    dur = x.size / sr
    if not bpm or dur < 30:
        return [], "none"
    bar = 60.0 / bpm * (bar_beats or 4)
    start = (phrase_off if phrase_off is not None else off) % (8 * bar)
    n = int((dur - start) / bar)
    if n < 24:
        return [], "none"
    rms, low = _bar_features(x, sr, start, bar, n, np)
    e = rms + 1.5 * low

    def after(b):
        return float(e[b:b + 8].mean() if b + 8 <= n else e[b:b + 4].mean())

    def rise(b):
        if b < 4 or b + 4 > n:
            return -9.0
        return float(after(b) - e[b - 4:b].mean() + 0.5 * (low[b:b + 4].mean() - low[b - 4:b].mean()))

    plateau = float(np.percentile([e[b:b + 8].mean() for b in range(max(1, n - 7))], 90))

    def is_drop(b):
        return rise(b) >= 1.0 and after(b) >= plateau - 0.6

    hits = []
    for b in sorted({round((c - start) / bar) for c in (cues or [])}):
        if 0 < b < n and is_drop(b):
            hits.append((b, "mik"))
    cand = [b for b in range(int(n * 0.08), int(n * 0.85)) if b % 8 == 0]
    hits += [(b, "energy") for b in cand if is_drop(b)]
    hits.sort(key=lambda h: (h[0], h[1] != "mik"))     # gleicher Takt: der MIK-Cue zaehlt
    out: list[tuple[int, str]] = []
    for b, src in hits:
        if out and b - out[-1][0] < 16:
            continue
        out.append((b, src))
    if not out:
        best = max(cand, key=rise, default=None)
        if best is not None and rise(best) >= 0.8:
            out = [(best, "energy")]
    return [round(start + b * bar, 3) for b, _ in out], (out[0][1] if out else "none")


def _drops_sync(path: str, bpm: float, off: float, phrase_off, bar_beats: int,
                cues_ms: list | None) -> dict:
    """Drops eines Titels messen (dekodiert mit 4 kHz, ~0,3 s)."""
    if not media._numpy_ok():
        return {}
    import numpy as np
    try:
        r = subprocess.run([core.FFMPEG, "-nostdin", "-v", "error", "-i", path, "-ac", "1", "-ar", str(DROP_SR),
                            "-f", "s16le", "-acodec", "pcm_s16le", "-"],
                           capture_output=True, timeout=120, creationflags=_NO_WINDOW)
    except Exception:
        return {}
    x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    cues = [ms / 1000.0 for ms in (cues_ms or []) if ms and ms > 0]
    drops, src = _find_drops(x, DROP_SR, bpm, off, phrase_off, bar_beats, cues)
    return {"drops": drops, "drop_src": src, "drop_rev": DROP_REV}

def _phrase_from_cues(off: float, beat: float, per: int, cues_ms: list) -> int | None:
    """Phrasenlage (Schlag-Index modulo per) aus den Cue-Punkten von Mixed In
    Key — die liegen auf Phrasenanfaengen. Nur wenn sie sich einig sind."""
    idx = [round((c / 1000.0 - off) / beat) for c in cues_ms if c >= 0]
    if len(idx) < 3:
        return None
    counts: dict = {}
    for i in idx:
        counts[i % per] = counts.get(i % per, 0) + 1
    ph, n = max(counts.items(), key=lambda kv: kv[1])
    return ph if n / len(idx) >= 0.6 else None

def _phrase_sync(path: str, bpm: float, off: float, cues_ms: list | None = None) -> dict | None:
    """Taktanfang ("die Eins") und Phrasen (8 Takte) im Raster {bpm, off}.

    Reihenfolge: Cue-Punkte von Mixed In Key (exakt), sonst Energie-Spruenge
    (Breakdown, Drop — die liegen fast immer auf Phrasenanfaengen), bei
    unklarem Ergebnis der erste hoerbare Schlag (Titel beginnen fast immer
    mit einer Phrase). An 17 Titeln mit MIK-Cues gemessen (09/2026): 14
    Phrasen und 15 Taktanfaenge richtig ohne Cues.
    Raster unter 100 BPM gelten als Halbtempo (DnB 87 = 174): ein Takt hat
    dann 2 Rasterschlaege.
    """
    if not media._numpy_ok() or not bpm or bpm <= 0:
        return None
    import numpy as np
    beat = 60.0 / bpm
    bar = 2 if bpm < 100 else 4
    per = 8 * bar
    if cues_ms:
        ph = _phrase_from_cues(off, beat, per, cues_ms)
        if ph is not None:
            return {"phrase_off": round(off + ph * beat, 4), "bar_beats": bar, "phrase_src": "mik"}
    sr = 4000
    try:
        r = subprocess.run([core.FFMPEG, "-nostdin", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr),
                            "-f", "s16le", "-acodec", "pcm_s16le", "-"],
                           capture_output=True, timeout=120, creationflags=_NO_WINDOW)
    except Exception:
        return None
    x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    nb = int((x.size / sr - off) / beat)
    if nb < per * 2:
        return None
    ef = np.empty(nb); el = np.empty(nb)
    for b in range(nb):
        seg = x[int((off + b * beat) * sr):int((off + (b + 1) * beat) * sr)]
        if seg.size < 8:
            ef[b] = el[b] = -20.0
            continue
        spec = np.abs(np.fft.rfft(seg)) ** 2
        lo = max(1, int(150 * seg.size / sr))               # Bass bis 150 Hz
        ef[b] = np.log(spec.sum() / seg.size + 1e-9)
        el[b] = np.log(spec[:lo].sum() / seg.size + 1e-9)
    d = np.zeros(nb)
    for e in (ef, el):
        for w in (bar, 2 * bar):
            dd = np.zeros(nb)
            for b in range(w, nb - w):
                dd[b] = abs(e[b:b + w].mean() - e[b - w:b].mean())
            d += dd / (dd.max() + 1e-9)
    sc = np.array([d[p::per].mean() for p in range(per)])
    best = int(np.argmax(sc))
    srt = np.sort(sc)
    conf = float((srt[-1] - srt[-2]) / (srt[-1] + 1e-9))
    if conf >= 0.1:
        return {"phrase_off": round(off + best * beat, 4), "bar_beats": bar, "phrase_src": "energy"}
    med = float(np.median(ef))
    first = next((b for b in range(nb) if ef[b] > med - 3.0), 0)
    return {"phrase_off": round(off + (first % per) * beat, 4), "bar_beats": bar, "phrase_src": "start"}

def _grid_from_mik(g: dict, cues_ms: list) -> dict:
    """Gemessenes Raster mit den Cue-Punkten von Mixed In Key verfeinern.

    Die Cues liegen auf Taktanfaengen (Phrasen); der Abstand erster–letzter
    Cue liefert das Tempo auf Tausendstel genau. Passt das nicht zur eigenen
    Messung (mehr als 0,5 % Abweichung), bleibt die Messung.
    Die Lage der Schlaege kommt bewusst weiter aus der eigenen Messung: die
    MIK-Cues liegen systematisch 40-60 ms daneben (anderer MP3-Decoder,
    gemessen an 12 Titeln 09/2026). Gemischt mit gemessenen Titeln wuerden
    die Schlaege sonst genau um diesen Versatz stolpern.
    """
    cues = sorted(c / 1000.0 for c in cues_ms if c >= 0)
    bpm = float(g.get("bpm_f") or 0)
    if len(cues) < 2 or bpm <= 0:
        return g
    span = cues[-1] - cues[0]
    beat = 60.0 / bpm
    nb = round(span / beat)
    if nb < 8:
        return g
    bpm_mik = 60.0 * nb / span
    if abs(bpm_mik - bpm) / bpm > 0.005:
        return g
    beat = 60.0 / bpm_mik
    return {**g, "bpm_f": round(bpm_mik, 3), "beat_off": round(float(g.get("beat_off") or 0) % beat, 4),
            "beat_conf": max(float(g.get("beat_conf") or 0), 0.6), "grid_src": "mik"}

async def _send_beatgrid(ws, path: str):
    """Raster aus der Bibliothek/dem Cache oder frisch messen und schicken."""
    lt = next((x for x in _state.get("library", []) if x.get("path") == path), None)
    g = None
    if lt and lt.get("bpm_f") and int(lt.get("grid_rev") or 1) >= GRID_REV:
        g = {k: lt[k] for k in _GRID_KEYS if k in lt}
    elif path in _beatgrid_cache and int(_beatgrid_cache[path].get("grid_rev") or 1) >= GRID_REV:
        g = _beatgrid_cache[path]
    elif os.path.isfile(path):
        hint = (lt or {}).get("bpm") or 0
        if not hint:
            q = next((x for x in _state.get("queue", []) if x.get("path") == path), None)
            hint = (q or {}).get("bpm") or 0
        loop = asyncio.get_running_loop()
        g = await loop.run_in_executor(None, _beatgrid_sync, path, float(hint or 0))
        # Tempo aus den MIK-Cues nur, wenn die eigene Gerade nicht ging
        if g and not g.pop("grid_fit", False) and lt and lt.get("mik_cues"):
            g = _grid_from_mik(g, lt["mik_cues"])
        if g:
            _beatgrid_cache[path] = g
            if lt is not None:
                # neues Raster, neue Lage: Takt, Phrasen und Drops neu bestimmen
                for k in _PHRASE_KEYS:
                    lt.pop(k, None)
                lt.update(g)
                store.save_library()
    elif lt and lt.get("bpm_f"):                # Datei gerade nicht da: altes Raster besser als keins
        g = {k: lt[k] for k in _GRID_KEYS if k in lt}
    # Takt und Phrasen: auch fuer Raster, die vor 1.6 gemessen wurden
    if g and g.get("bpm_f") and "phrase_off" not in g and os.path.isfile(path) \
            and float(g.get("beat_conf") or 0) >= 0.3:
        ph = await asyncio.get_running_loop().run_in_executor(
            None, _phrase_sync, path, float(g["bpm_f"]), float(g.get("beat_off") or 0),
            (lt or {}).get("mik_cues"))
        if ph:
            g = {**g, **ph}
            _beatgrid_cache[path] = g
            if lt is not None:
                lt.update(ph)
                store.save_library()
    # Drops: einmal je Raster messen und speichern
    if g and g.get("bpm_f") and int(g.get("drop_rev") or 0) < DROP_REV and os.path.isfile(path) \
            and float(g.get("beat_conf") or 0) >= 0.3:
        bb = int(g.get("bar_beats") or (2 if float(g["bpm_f"]) < 100 else 4))
        dr = await asyncio.get_running_loop().run_in_executor(
            None, _drops_sync, path, float(g["bpm_f"]), float(g.get("beat_off") or 0),
            g.get("phrase_off"), bb, (lt or {}).get("mik_cues"))
        if dr:
            g = {**g, **dr}
            _beatgrid_cache[path] = g
            if lt is not None:
                lt.update(dr)
                store.save_library()
    try:
        await ws.send_text(json.dumps({"type": "beatgrid", "path": path, **(g or {"bpm_f": 0})}))
    except Exception:
        pass
