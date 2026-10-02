// Beat-Sync fuer den Uebergang zwischen zwei <audio>-Elementen.
//
// Jeder Titel hat ein Taktraster aus dem Backend: genaues Tempo (bpm, mit
// Nachkommastellen) und die Lage des ersten Schlags (off, Sekunden). Waehrend
// des Uebergangs laeuft der neue Titel im Tempo des alten (playbackRate mit
// erhaltener Tonhoehe), und ein kleiner Regelkreis haelt die Schlaege
// uebereinander, indem er das Tempo leicht nachzieht — solange der neue Titel
// noch leise ist kraeftiger, danach nur fein. Springen (currentTime setzen)
// geht nicht: bei vielen MP3s landet der Sprung bis zu einer Viertel
// Schlaglaenge daneben (gemessen 09/2026), der Fehler kam immer wieder.

const MAX_TEMPO_DIFF = 0.08   // Standard: mehr als 8 % Unterschied, kein Angleichen (einstellbar bis 25 %)
const MEET_RAMP      = 0.4    // "Treffen in der Mitte": der alte Titel gleitet in den ersten 40 % des Uebergangs
const MAX_NUDGE      = 0.04   // Feinkorrektur hoechstens ±4 %
const MAX_NUDGE_FAST = 0.08   // am Anfang des Uebergangs (neuer Titel leise) ±8 %
const CORRECT_SEC    = 1.0    // Phasenfehler in etwa dieser Zeit ausgleichen
const CORRECT_FAST   = 0.6
// Grosser Anfangsfehler (gemessen hoerbar bis 90 ms, der neue Titel startet nie
// ganz genau): solange er noch leise ist, kraeftig nachziehen — vorher dauerte
// das 2-3 s, in denen man zwei Bassdrums hoerte. Die Tonhoehe bleibt dabei.
const CATCH_UP       = 0.25   // bis ±25 %
const CATCH_SEC      = 0.25   // Fehler in etwa dieser Zeit ausgleichen
const CATCH_FROM     = 0.012  // ab 12 ms Versatz
// Kleiner Tempo-Unterschied: ohne Tonhoehen-Erhalt (wie ein Plattenspieler).
// Chromiums Zeitdehnung verschiebt das Hoerbare sonst unregelmaessig um bis zu
// 30 ms gegen die gemeldete Position — die Schlaege wackelten (gemessen 10/2026:
// mit Erhalt -35…+8 ms, ohne +3…+7 ms stabil). Bis 2 % aendert sich die
// Tonhoehe um hoechstens 1/3 Halbton; groessere Unterschiede behalten sie.
const PITCH_FREE     = 0.02
const CATCH_UP_FREE  = 0.12   // ohne Tonhoehen-Erhalt sanfter nachziehen (kein Tonhoehen-Schlenker)

/** Phase im Takt (0…1) zur Zeit t (Sekunden) im Raster {bpm, off}. */
export function phaseOf(t, bpm, off) {
  const p = (t - off) * bpm / 60
  return ((p % 1) + 1) % 1
}

/** Differenz zweier Phasen auf −0.5…0.5 gefaltet. */
export function wrapPhase(d) {
  return d - Math.round(d)
}

/**
 * Tempo-Verhaeltnis, mit dem der naechste Titel laufen muss, um zum laufenden
 * zu passen. Beruecksichtigt halbes/doppeltes Tempo (87 ↔ 174 BPM).
 * Liefert { rate, mult } oder null, wenn die Tempi zu weit auseinander liegen.
 * mult: Faktor fuer das Raster des naechsten Titels (2 = dessen halbe Schlaege zaehlen).
 */
export function tempoMatch(bpmCur, bpmNext, maxDiff = MAX_TEMPO_DIFF) {
  if (!(bpmCur > 30) || !(bpmNext > 30)) return null
  let best = null
  for (const mult of [0.5, 1, 2]) {
    const rate = bpmCur / (bpmNext * mult)
    if (!best || Math.abs(rate - 1) < Math.abs(best.rate - 1)) best = { rate, mult }
  }
  return Math.abs(best.rate - 1) <= maxDiff + 1e-9 ? best : null
}

/**
 * Startposition des naechsten Titels so verschieben, dass er auf derselben
 * Taktphase einsetzt wie der laufende gerade steht. Verschiebt hoechstens
 * einen halben Schlag in jede Richtung.
 */
export function alignStart(startSec, phaseCur, gridNext, mult = 1) {
  const bpm = gridNext.bpm * mult
  const beat = 60 / bpm
  const pn = phaseOf(startSec, bpm, gridNext.off)
  const d = wrapPhase(phaseCur - pn)
  return Math.max(0, startSec + d * beat)
}

/**
 * Einstiegspunkt im naechsten Titel so waehlen, dass er auf derselben
 * Taktphase beginnt wie der laufende in etwa `leadSec` (Zeit fuer Springen und
 * Starten). Der Regelkreis muss dann nur noch Millisekunden ausgleichen.
 * Gibt startSec unveraendert zurueck, wenn die Raster nicht taugen.
 */
export function alignedStart(cur, startSec, gCur, gNext, leadSec = 0.12, maxDiff = MAX_TEMPO_DIFF) {
  if (!cur || !gCur || !gNext || (gCur.conf ?? 0) < 0.4 || (gNext.conf ?? 0) < 0.4) return startSec
  const m = tempoMatch(gCur.bpm * (cur.playbackRate || 1), gNext.bpm, maxDiff)
  if (!m) return startSec
  const nb = gNext.bpm * m.mult, fast = Math.max(gCur.bpm, nb)
  const pc = phaseOf(cur.currentTime + leadSec * (cur.playbackRate || 1), gCur.bpm * Math.round(fast / gCur.bpm), gCur.off)
  return alignStart(Math.max(0, startSec), pc, { bpm: nb * Math.round(fast / nb), off: gNext.off }, 1)
}

/**
 * Regelkreis fuer einen Uebergang anlegen.
 * cur/next: <audio>-Elemente, gCur/gNext: {bpm, off, conf}.
 * opts.tempo: Tempo angleichen, opts.phase: Schlaege uebereinanderlegen,
 * opts.maxDiff: groesster Tempo-Unterschied (0.08 = 8 %), opts.meet: beide
 * Titel gehen je zur Haelfte aufeinander zu (Standard), sonst nur der neue.
 */
export function createSync(cur, next, gCur, gNext, opts = {}) {
  // Unklare Raster (freies Tempo, Live-Schlagzeug): lieber gar nicht eingreifen
  if (!gCur || !gNext || (gCur.conf ?? 0) < 0.3 || (gNext.conf ?? 0) < 0.3) return null
  const tempoOn = opts.tempo !== false
  const phaseOn = opts.phase !== false && gCur.conf >= 0.4 && gNext.conf >= 0.4
  // Der laufende Titel kann selbst noch angeglichen sein (Tempo gleitet zurueck)
  const curRate = cur.playbackRate || 1
  const m = tempoMatch(gCur.bpm * curRate, gNext.bpm, opts.maxDiff ?? MAX_TEMPO_DIFF)
  if (!m) return null
  if (!tempoOn && !phaseOn) return null
  // Der neue Titel laeuft fest im Verhaeltnis k zum alten — dann bleiben die
  // Schlaege gekoppelt, auch waehrend der alte sein Tempo aendert.
  const k = m.rate / curRate
  const meet = tempoOn && opts.meet !== false
  const curTarget = meet ? meetTarget(k) : curRate
  const base = () => tempoOn ? (cur.playbackRate || 1) * k : 1
  // Ohne Tempoangleich laeuft der naechste Titel eigenstaendig; die Phase
  // haelt dann nur bei nahezu gleichem Tempo — sonst nicht nachregeln.
  const phaseUsable = phaseOn && (tempoOn || Math.abs(m.rate - 1) < 0.01)
  // Verglichen wird auf dem feineren der beiden Raster (87 ↔ 174 BPM:
  // jeder Schlag des langsamen liegt auch im schnellen)
  const nb = gNext.bpm * m.mult, fast = Math.max(gCur.bpm, nb)
  const bpmCurCmp = gCur.bpm * Math.round(fast / gCur.bpm)
  const bpmNext = nb * Math.round(fast / nb)
  const beatNext = 60 / bpmNext
  let lastErr = null
  // Die Zeit eines <audio> springt in Schritten von ein paar Millisekunden:
  // ueber die letzten Messungen mitteln, dazu ein langsamer Anteil, der einen
  // kleinen Fehler im Grundtempo ausgleicht (sonst bleibt ein Restversatz)
  const errs = []
  let integ = 0, lastTick = 0, startReported = false

  const exact = tempoOn && Math.abs(m.rate - 1) <= PITCH_FREE
  try { next.preservesPitch = !exact; cur.preservesPitch = !exact } catch {}
  next.playbackRate = base()

  return {
    get baseRate() { return base() },
    pitchFree: exact,
    phaseOn: phaseUsable,
    curTarget,
    get lastErr() { return lastErr },
    /** Waehrend des Uebergangs regelmaessig aufrufen. fast: neuer Titel noch
     *  leise, t: Fortschritt des Uebergangs 0…1 (fuer das Treffen in der Mitte). */
    tick(fast = true, t = 1) {
      if (meet && !cur.paused) cur.playbackRate = curRate + (curTarget - curRate) * Math.min(1, t / MEET_RAMP)
      if (!phaseUsable || next.paused || cur.paused || next.seeking || cur.seeking) {
        if (tempoOn && !next.paused) next.playbackRate = base()
        return
      }
      const now = performance.now()
      const pc = phaseOf(cur.currentTime, bpmCurCmp, gCur.off)
      const pn = phaseOf(next.currentTime, bpmNext, gNext.off)
      const err = wrapPhase(pc - pn)            // > 0: naechster Titel hinkt hinterher
      errs.push(err)
      if (errs.length > 5) errs.shift()
      const avg = errs.reduce((x, y) => x + y, 0) / errs.length
      lastErr = avg
      // Anfangsfehler melden (Sekunden, > 0: der neue Titel kam zu spaet) —
      // daraus lernt der Player, wie lange Springen und Starten wirklich dauern
      if (!startReported && errs.length >= 4) { startReported = true; opts.onStartError?.(avg * beatNext) }
      const dt = lastTick ? Math.min(0.2, (now - lastTick) / 1000) : 0
      lastTick = now
      // Nur nahe am Ziel aufsummieren — sonst schaukelt der grosse Anfangsfehler
      // den langsamen Anteil auf und er zieht spaeter in die falsche Richtung
      if (Math.abs(avg) < 0.04) integ = Math.max(-0.2, Math.min(0.2, integ + avg * dt))
      else integ *= 0.9
      const big = fast && Math.abs(avg) * beatNext > CATCH_FROM
      const lim = big ? (exact ? CATCH_UP_FREE : CATCH_UP) : fast ? MAX_NUDGE_FAST : MAX_NUDGE
      const nudge = Math.max(-lim, Math.min(lim,
        (avg / (big ? CATCH_SEC : fast ? CORRECT_FAST : CORRECT_SEC) + integ * 0.4) * beatNext))
      next.playbackRate = base() * (1 + nudge)
    },
    /** Nach dem Uebergang: Feinkorrektur aus, Grundtempo halten. */
    settle() { next.playbackRate = base() },
    /** Abgebrochen: der alte Titel spielt weiter und gleitet aufs Original zurueck. */
    abort() { return meet && Math.abs((cur.playbackRate || 1) - 1) > 0.001 ? glideRate(cur, 3000) : () => {} },
  }
}

/**
 * Treffen in der Mitte: Tempo-Faktor des laufenden Titels, wenn der naechste
 * im Verhaeltnis k zu ihm laeuft und beide gleich weit (im Verhaeltnis) vom
 * Original abweichen sollen: alt 1/sqrt(k), neu sqrt(k).
 */
export function meetTarget(k) {
  return 1 / Math.sqrt(k)
}

/** Wie meetTarget, direkt aus den Rastern; null = kein Angleichen moeglich. */
export function meetRate(cur, gCur, gNext, maxDiff = MAX_TEMPO_DIFF) {
  if (!cur || !gCur || !gNext) return null
  const r = cur.playbackRate || 1
  const m = tempoMatch(gCur.bpm * r, gNext.bpm, maxDiff)
  return m ? meetTarget(m.rate / r) : null
}

/**
 * Tempo nach dem Uebergang langsam zurueck auf das Original bringen.
 * Gibt eine Funktion zum Abbrechen zurueck.
 */
export function glideRate(el, ms = 20000) {
  const from = el.playbackRate
  if (Math.abs(from - 1) < 0.001) { el.playbackRate = 1; return () => {} }
  const t0 = performance.now()
  const iv = setInterval(() => {
    const t = Math.min(1, (performance.now() - t0) / ms)
    el.playbackRate = from + (1 - from) * t
    if (t >= 1) clearInterval(iv)
  }, 100)
  return () => clearInterval(iv)
}

// ── Takt, Phrasen, Bass-Tausch (1.6) ─────────────────────────────────────────
// Das Raster kann zusaetzlich die Lage der Phrasen kennen (phrase: Sekunden
// eines Phrasenanfangs, barBeats: Rasterschlaege je Takt — 2 bei Halbtempo-
// Rastern wie DnB 87). Aus dem Backend (_phrase_sync).

/** Taktlaenge in Sekunden (Titelzeit). */
export function barLen(g) {
  const beats = g.barBeats || (g.bpm < 100 ? 2 : 4)
  return beats * 60 / g.bpm
}

/**
 * Zeitpunkt t (Sekunden im Titel) auf einen Phrasenanfang schieben: bevorzugt
 * 16 Takte, dann 8, zuletzt ein Taktanfang — hoechstens maxBars Takte weit
 * und nur innerhalb [lo, hi]. Ohne Phrasen im Raster: t unveraendert.
 * units: welche Rastergroessen (in Takten) in Frage kommen.
 */
export function snapToPhrase(t, g, lo = 0, hi = Infinity, maxBars = 8, units = [16, 8, 1]) {
  if (!g || !(g.bpm > 0) || g.phrase == null) return t
  const bar = barLen(g)
  for (const unit of units) {
    const step = unit * bar
    const k = Math.round((t - g.phrase) / step)
    let best = null
    for (const kk of [k - 1, k, k + 1]) {
      const c = g.phrase + kk * step
      const d = Math.abs(c - t)
      if (c >= lo && c <= hi && d <= maxBars * bar + 1e-6 && (best === null || d < Math.abs(best - t))) best = c
    }
    if (best !== null) return best
  }
  return t
}

/**
 * Wie alignedStart, aber auf den Takt genau: der naechste Titel setzt an
 * derselben Stelle im Takt ein wie der laufende gerade steht (Eins auf Eins).
 * Faellt auf den Schlag zurueck, wenn ein Raster keine Phrasen kennt oder die
 * Takte nicht zueinander passen.
 */
export function barAlignedStart(cur, startSec, gCur, gNext, leadSec = 0.12, maxDiff = MAX_TEMPO_DIFF) {
  if (!cur || !gCur || !gNext || gCur.phrase == null || gNext.phrase == null)
    return alignedStart(cur, startSec, gCur, gNext, leadSec, maxDiff)
  if ((gCur.conf ?? 0) < 0.4 || (gNext.conf ?? 0) < 0.4) return startSec
  const rate = cur.playbackRate || 1
  const m = tempoMatch(gCur.bpm * rate, gNext.bpm, maxDiff)
  if (!m) return startSec
  const barC = barLen(gCur), barN = barLen(gNext)
  // Echte Taktlaenge nach dem Angleichen muss gleich sein (sonst nur Schlaege)
  if (Math.abs((barN / m.rate) / (barC / rate) - 1) > 0.03)
    return alignedStart(cur, startSec, gCur, gNext, leadSec, maxDiff)
  const pc = phaseOf(cur.currentTime + leadSec * rate, 60 / barC, gCur.phrase)
  const pn = phaseOf(Math.max(0, startSec), 60 / barN, gNext.phrase)
  let s = Math.max(0, startSec) + wrapPhase(pc - pn) * barN
  if (s < 0) s += barN
  return s
}

/** Laeuft der Uebergang im Takt? (Voraussetzung fuer den Bass-Tausch) */
export function swapEligible(cur, gCur, gNext, cfg = {}) {
  if (!cfg.beatAlignCf) return false
  if (!gCur || !gNext || (gCur.conf ?? 0) < 0.4 || (gNext.conf ?? 0) < 0.4) return false
  const m = tempoMatch(gCur.bpm * (cur?.playbackRate || 1), gNext.bpm, (cfg.maxTempoDiff ?? 8) / 100)
  if (!m) return false
  return cfg.tempoMatch !== false || Math.abs(m.rate - 1) < 0.01
}

/**
 * Bass-Tausch planen: weich ueber 2 Takte (1 Takt, wenn 2 laenger als ~4 s
 * dauern), um die Mitte des Uebergangs, auf einem Taktanfang des laufenden
 * Titels. curTime: Position des laufenden Titels, rate: dessen Tempo,
 * fadeSec: Laenge des Uebergangs, ramp: {r1, sec} wenn das Tempo des laufenden
 * Titels in den ersten sec Sekunden linear auf r1 gleitet (Treffen in der
 * Mitte). Liefert {delay, len, bars} in Sekunden Echtzeit ab jetzt oder null.
 */
export function bassSwapPlan(curTime, rate, g, fadeSec, ramp = null) {
  if (!g || !(g.bpm > 0) || !(fadeSec > 0)) return null
  rate = rate || 1
  const r1 = ramp?.r1 ?? rate, R = ramp?.sec ?? 0
  // Titelzeit <-> Echtzeit unter der Tempo-Rampe
  const adv = (t) => t <= R ? rate * t + (r1 - rate) * t * t / (2 * R) : rate * R + (r1 - rate) * R / 2 + r1 * (t - R)
  const real = (d) => { let lo = 0, hi = fadeSec * 4 + d * 4 + 10; for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2; if (adv(mid) < d) lo = mid; else hi = mid } return (lo + hi) / 2 }
  const bar = barLen(g)
  const origin = g.phrase ?? g.off ?? 0
  const barReal = bar / (R ? r1 : rate)
  for (const bars of (2 * barReal <= 4.2 ? [2, 1] : [1])) {
    const approxLen = bars * barReal
    if (approxLen + 0.5 > fadeSec) continue
    const want = curTime + adv(fadeSec / 2 - approxLen / 2)      // Titelzeit
    let k = Math.round((want - origin) / bar)
    const at = (kk) => {
      const start = origin + kk * bar
      const delay = real(start - curTime)
      return { delay, len: real(start - curTime + bars * bar) - delay }
    }
    let p = at(k)
    while (p.delay + p.len > fadeSec - 0.2 && at(k - 1).delay >= 0.3) p = at(--k)
    while (p.delay < 0.3) p = at(++k)
    if (p.delay + p.len <= fadeSec - 0.2) return { ...p, bars }
  }
  return null
}
