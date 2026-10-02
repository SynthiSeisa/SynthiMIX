// DJ-Modus: kreative Uebergaenge, wenn Tempo und Takt passen.
//
// Idee nach dem offenen Auto-DJ von Len Vande Veire (github.com/lenvdv/auto-dj):
// Ein Titel wird auf Phrasengrenzen in laute und ruhige Abschnitte geteilt;
// ein Drop ist der Wechsel von ruhig nach laut. Beim Double Drop laeuft der
// neue Titel die Takte vor seinem Drop leise und ohne Bass unter dem alten mit,
// auf dem Drop fallen beide zusammen, der Bass wird getauscht, danach blendet
// der alte aus.
//
// Hier nur Rechnen (ohne Web Audio) — getestet in tests/djmode.test.mjs.

import { barLen, tempoMatch } from './beatsync.js'

export const DJ_TYPES = ['doubledrop', 'filter', 'echo', 'roll']
export const DJ_LABEL = { blend: 'Normal', doubledrop: 'Double Drop', filter: 'Filter', echo: 'Echo-Out', roll: 'Loop-Roll' }

/** Energie (Mittel der Quadrate) eines Zeitbereichs der Waveform. */
function energy(wf, dur, a, b) {
  const n = wf.length
  const i0 = Math.max(0, Math.floor(a / dur * n)), i1 = Math.min(n, Math.ceil(b / dur * n))
  if (i1 <= i0) return 0
  let e = 0
  for (let i = i0; i < i1; i++) e += wf[i] * wf[i]
  return e / (i1 - i0)
}

/**
 * Drops finden: Phrasengrenzen (alle 8 Takte), vor denen es ruhig war und nach
 * denen es laut wird und laut bleibt. wf: Waveform (0…1), g: Taktraster,
 * dur: Laenge in s, cues: Mixed-In-Key-Cues in ms (optional, zum Feinjustieren).
 * Liefert Zeitpunkte in s, aufsteigend.
 */
export function detectDrops(wf, g, dur, cues = null) {
  if (!g || !(g.bpm > 0) || !(dur > 0) || (g.conf ?? 0) < 0.4) return []
  // Im Backend aus dem Audio gemessen (Lautheit und Bass je Takt, MIK-Cues) —
  // genauer als die Schaetzung aus der groben Wellenform unten
  if (Array.isArray(g.drops)) return g.drops.filter(t => t > 0 && t < dur)
  if (!wf?.length) return []
  const bar = barLen(g)
  const origin = g.phrase ?? g.off ?? 0
  // Mittel der lauteren Haelfte = "Hauptteil"
  const sorted = [...wf].map(x => x * x).sort((a, b) => a - b)
  const top = sorted.slice(Math.floor(sorted.length / 2))
  const main = top.reduce((s, x) => s + x, 0) / Math.max(1, top.length)
  if (!(main > 0)) return []
  // Pegel je 8-Takt-Phrase in dB zum Hauptteil. Ein Drop: die Phrase ist
  // (fast) so laut wie der Hauptteil, die davor war leiser, in den 24 Takten
  // davor gab es einen ruhigen Teil (Break/Intro), und es bleibt danach laut.
  // Gemessen an echten DnB-Titeln: der Build-up direkt davor liegt oft nur
  // 2-3 dB unter dem Drop, der Break davor 4-15 dB.
  const phrase = 8 * bar
  const db = (a, b) => 10 * Math.log10(Math.max(1e-9, energy(wf, dur, a, b)) / main)
  const starts = []
  for (let b = origin; b + phrase <= dur + 1e-6; b += phrase) starts.push(b)
  const lv = starts.map(b => db(b, b + phrase))
  const drops = []
  for (let i = 1; i < starts.length - 1; i++) {
    const quietBefore = Math.min(...lv.slice(Math.max(0, i - 3), i))
    if (lv[i] >= -1.5 && lv[i - 1] <= -1.8 && quietBefore <= -4 && lv[i + 1] >= -2.5) {
      if (drops.length && starts[i] - drops[drops.length - 1] < 16 * bar) continue
      drops.push(starts[i])
    }
  }
  if (cues?.length) {
    // Mixed In Key setzt Cues oft genau auf den Drop: bis 1 Takt Abstand uebernehmen
    return drops.map(d => {
      const c = cues.map(ms => ms / 1000).find(t => Math.abs(t - d) <= bar * 1.01)
      return c ?? d
    })
  }
  return drops
}

/**
 * Double Drop planen. cur/next: { drops, g, dur }. Liefert null oder
 * { trig, intro, pre, post, len, swapAt } — trig/intro in Titelzeit (s) von
 * laufendem/naechstem Titel, pre/post/len in Takten, swapAt = pre/len.
 * minTrig: frueheste Startzeit im laufenden Titel (z. B. jetzt + Vorlauf).
 */
export function doubleDropPlan(cur, next, maxDiff = 0.08, minTrig = 0) {
  if (!cur?.g || !next?.g || !cur.drops?.length || !next.drops?.length) return null
  if (!tempoMatch(cur.g.bpm, next.g.bpm, maxDiff)) return null
  const barC = barLen(cur.g), barN = barLen(next.g)
  const post = 16
  // Drop des naechsten Titels: der erste mit mindestens 8 Takten Anlauf
  const d2 = next.drops.find(d => d >= 8 * barN - 1e-6)
  if (d2 == null) return null
  const pre = Math.min(16, Math.floor((d2 + 1e-6) / barN / 8) * 8)
  if (pre < 8) return null
  // Drop des laufenden: der letzte, nach dem noch 16 Takte bleiben und vor dem
  // genug Anlauf liegt
  const cands = cur.drops.filter(d => d + post * barC <= cur.dur && d - pre * barC >= minTrig)
  if (!cands.length) return null
  const d1 = cands[cands.length - 1]
  return { trig: d1 - pre * barC, intro: d2 - pre * barN, drop1: d1, drop2: d2,
           pre, post, len: pre + post, swapAt: pre / (pre + post) }
}

/** Kleiner, stabiler Hash (0…1) — dieselbe Titel-Kombination waehlt immer gleich. */
export function hash01(s) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) }
  return ((h >>> 0) % 100000) / 100000
}

/**
 * Welcher Uebergang? cfg: { amount (0…1), types: {doubledrop, filter, echo, roll} }
 * ctx: { seed, eligible, keyClash, ddPossible, lastType }
 * Passend zur Situation: Echo-Out, wenn die Tonarten nicht passen; Double
 * Drop, wenn er geht; sonst Filter oder Loop-Roll im Wechsel.
 */
export function chooseTransition(cfg, ctx) {
  if (!ctx.eligible) return 'blend'
  const on = (t) => cfg.types?.[t] !== false
  if (hash01(ctx.seed + '#amount') >= (cfg.amount ?? 0.4)) return 'blend'
  if (ctx.keyClash && on('echo')) return 'echo'
  if (ctx.ddPossible && on('doubledrop')) return 'doubledrop'
  const rest = ['filter', 'roll', 'echo'].filter(t => on(t))
  if (!rest.length) return 'blend'
  const pool = rest.length > 1 ? rest.filter(t => t !== ctx.lastType) : rest
  return pool[Math.floor(hash01(ctx.seed + '#type') * pool.length)]
}

/**
 * Loop-Roll: Laengen (in Schlaegen) je Abschnitt des letzten Takts vor der
 * Grenze. Liefert [{at, len}] — at: Schlaege ab Beginn des Rolls.
 */
export function rollSteps(beatsPerBar = 4) {
  const b = beatsPerBar
  return [{ at: 0, len: 1 }, { at: b / 2, len: 0.5 }, { at: b * 0.75, len: 0.25 }, { at: b * 0.875, len: 0.125 }]
}
