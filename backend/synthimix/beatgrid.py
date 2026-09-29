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

def _beatgrid_sync(path: str, bpm_hint: float = 0.0) -> dict | None:
    """Genaues Tempo und Lage des ersten Schlags.

    Tiefpass (Bassdrum), Anstiege der Lautstaerke als Huellkurve, dann ein
    Kammfilter ueber den ganzen Titel: fuer Tempi nahe der Schaetzung und jede
    Phase wird aufsummiert, wie viel Anstieg genau auf den Schlaegen liegt.
    Kandidaten sind die BPM aus den Tags und eine eigene Schaetzung, jeweils
    auch halbes/doppeltes Tempo; es gewinnt das Raster mit der deutlichsten
    Spitze. beat_conf (0..1) sagt, wie klar das Raster ist — unter ~0.4
    (Live-Schlagzeug, freies Tempo) legt der Player die Schlaege nicht
    uebereinander.
    """
    if not media._numpy_ok():
        return None
    import numpy as np
    sr, hop = 11025, 64            # 5,8 ms Aufloesung
    try:
        r = subprocess.run([core.FFMPEG, "-nostdin", "-v", "error", "-i", path,
                            "-af", "lowpass=f=180,aformat=channel_layouts=mono",
                            "-ac", "1", "-ar", str(sr), "-f", "s16le", "-acodec", "pcm_s16le", "-"],
                           capture_output=True, timeout=120, creationflags=_NO_WINDOW)
    except Exception:
        return None
    x = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if x.size < sr * 20:
        return None
    n = x.size // hop
    fr = x[:n * hop].reshape(n, hop)
    env = np.log1p(200 * np.sqrt((fr * fr).mean(axis=1) + 1e-12))
    onset = np.maximum(0.0, np.diff(env, prepend=env[0]))
    k = 32                          # langsame Pegelaenderungen raus
    if onset.size > k:
        onset = np.maximum(0.0, onset - np.convolve(onset, np.ones(k) / k, mode="same"))
    fps = sr / hop

    def comb(bpms):
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

    def conf_of(b):
        return 0.0 if b[0] <= 0 else (b[0] - b[3]) / b[0]

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
    results = [comb(np.arange(c - 1.5, c + 1.5001, 0.05)) for c in centers]
    results = [r_ for r_ in results if r_[1] > 0]
    if not results:
        return None
    # Halbes Tempo wirkt im Kamm immer etwas "deutlicher" (weniger Schlaege,
    # hoehere Spitze). Deshalb: fast gleich gut (85 %) reicht fuer das Tempo
    # aus den Tags, sonst fuer das doppelte Tempo.
    top = max(conf_of(r_) for r_ in results)
    good = [r_ for r_ in results if conf_of(r_) >= 0.85 * top]
    tagged = [r_ for r_ in good if bpm_hint and abs(r_[1] - bpm_hint) <= 2]
    coarse = tagged[0] if tagged else max(good, key=lambda r_: r_[1])
    fine = comb(np.arange(coarse[1] - 0.06, coarse[1] + 0.0601, 0.005))
    period = fps * 60.0 / fine[1]
    return {"bpm_f": round(fine[1], 3),
            "beat_off": round((fine[2] % period) / fps, 4),
            "beat_conf": round(max(0.0, min(1.0, conf_of(fine))), 3)}

_GRID_KEYS = ("bpm_f", "beat_off", "beat_conf", "phrase_off", "bar_beats", "phrase_src")

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
    if lt and lt.get("bpm_f"):
        g = {k: lt[k] for k in _GRID_KEYS if k in lt}
    elif path in _beatgrid_cache:
        g = _beatgrid_cache[path]
    elif os.path.isfile(path):
        hint = (lt or {}).get("bpm") or 0
        if not hint:
            q = next((x for x in _state.get("queue", []) if x.get("path") == path), None)
            hint = (q or {}).get("bpm") or 0
        loop = asyncio.get_running_loop()
        g = await loop.run_in_executor(None, _beatgrid_sync, path, float(hint or 0))
        if g and lt and lt.get("mik_cues"):
            g = _grid_from_mik(g, lt["mik_cues"])
        if g:
            _beatgrid_cache[path] = g
            if lt is not None:
                lt.update(g)
                store.save_library()
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
    try:
        await ws.send_text(json.dumps({"type": "beatgrid", "path": path, **(g or {"bpm_f": 0})}))
    except Exception:
        pass
