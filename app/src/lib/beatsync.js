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

const MAX_TEMPO_DIFF = 0.08   // mehr als 8 % Unterschied: kein Angleichen
const MAX_NUDGE      = 0.04   // Feinkorrektur hoechstens ±4 %
const MAX_NUDGE_FAST = 0.08   // am Anfang des Uebergangs (neuer Titel leise) ±8 %
const CORRECT_SEC    = 1.0    // Phasenfehler in etwa dieser Zeit ausgleichen
const CORRECT_FAST   = 0.6

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
export function tempoMatch(bpmCur, bpmNext) {
  if (!(bpmCur > 30) || !(bpmNext > 30)) return null
  let best = null
  for (const mult of [0.5, 1, 2]) {
    const rate = bpmCur / (bpmNext * mult)
    if (!best || Math.abs(rate - 1) < Math.abs(best.rate - 1)) best = { rate, mult }
  }
  return Math.abs(best.rate - 1) <= MAX_TEMPO_DIFF ? best : null
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
export function alignedStart(cur, startSec, gCur, gNext, leadSec = 0.12) {
  if (!cur || !gCur || !gNext || (gCur.conf ?? 0) < 0.4 || (gNext.conf ?? 0) < 0.4) return startSec
  const m = tempoMatch(gCur.bpm * (cur.playbackRate || 1), gNext.bpm)
  if (!m) return startSec
  const nb = gNext.bpm * m.mult, fast = Math.max(gCur.bpm, nb)
  const pc = phaseOf(cur.currentTime + leadSec * (cur.playbackRate || 1), gCur.bpm * Math.round(fast / gCur.bpm), gCur.off)
  return alignStart(Math.max(0, startSec), pc, { bpm: nb * Math.round(fast / nb), off: gNext.off }, 1)
}

/**
 * Regelkreis fuer einen Uebergang anlegen.
 * cur/next: <audio>-Elemente, gCur/gNext: {bpm, off, conf}.
 * opts.tempo: Tempo angleichen, opts.phase: Schlaege uebereinanderlegen.
 */
export function createSync(cur, next, gCur, gNext, opts = {}) {
  // Unklare Raster (freies Tempo, Live-Schlagzeug): lieber gar nicht eingreifen
  if (!gCur || !gNext || (gCur.conf ?? 0) < 0.3 || (gNext.conf ?? 0) < 0.3) return null
  const tempoOn = opts.tempo !== false
  const phaseOn = opts.phase !== false && gCur.conf >= 0.4 && gNext.conf >= 0.4
  // Der laufende Titel kann selbst noch angeglichen sein (Tempo gleitet zurueck)
  const curRate = cur.playbackRate || 1
  const m = tempoMatch(gCur.bpm * curRate, gNext.bpm)
  if (!m) return null
  const baseRate = tempoOn ? m.rate : 1
  if (!tempoOn && !phaseOn) return null
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
  let integ = 0, lastTick = 0

  try { next.preservesPitch = true } catch {}
  next.playbackRate = baseRate

  return {
    baseRate,
    phaseOn: phaseUsable,
    get lastErr() { return lastErr },
    /** Waehrend des Uebergangs regelmaessig aufrufen. fast: neuer Titel noch leise. */
    tick(fast = true) {
      if (!phaseUsable || next.paused || cur.paused || next.seeking || cur.seeking) return
      const now = performance.now()
      const pc = phaseOf(cur.currentTime, bpmCurCmp, gCur.off)
      const pn = phaseOf(next.currentTime, bpmNext, gNext.off)
      const err = wrapPhase(pc - pn)            // > 0: naechster Titel hinkt hinterher
      errs.push(err)
      if (errs.length > 5) errs.shift()
      const avg = errs.reduce((x, y) => x + y, 0) / errs.length
      lastErr = avg
      const dt = lastTick ? Math.min(0.2, (now - lastTick) / 1000) : 0
      lastTick = now
      // Nur nahe am Ziel aufsummieren — sonst schaukelt der grosse Anfangsfehler
      // den langsamen Anteil auf und er zieht spaeter in die falsche Richtung
      if (Math.abs(avg) < 0.04) integ = Math.max(-0.2, Math.min(0.2, integ + avg * dt))
      else integ *= 0.9
      const lim = fast ? MAX_NUDGE_FAST : MAX_NUDGE
      const nudge = Math.max(-lim, Math.min(lim,
        (avg / (fast ? CORRECT_FAST : CORRECT_SEC) + integ * 0.4) * beatNext))
      next.playbackRate = baseRate * (1 + nudge)
    },
    /** Nach dem Uebergang: Feinkorrektur aus, Grundtempo halten. */
    settle() { next.playbackRate = baseRate },
  }
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
