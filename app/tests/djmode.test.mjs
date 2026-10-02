// DJ-Modus: Drop-Erkennung, Double-Drop-Plan, Auswahl (node --test)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { detectDrops, doubleDropPlan, chooseTransition, hash01, rollSteps } from '../src/lib/djmode.js'
import { barLen } from '../src/lib/beatsync.js'

const g = { bpm: 174, off: 0, conf: 0.9, phrase: 0, barBeats: 4 }
const bar = barLen(g)

// Titel aus Abschnitten [Takte, Pegel] -> Waveform mit 1000 Punkten
function track(parts) {
  const bars = parts.reduce((s, [n]) => s + n, 0)
  const dur = bars * bar
  const wf = []
  for (let i = 0; i < 1000; i++) {
    let t = (i + 0.5) / 1000 * bars, lvl = 0
    for (const [n, l] of parts) { if (t < n) { lvl = l; break } t -= n }
    wf.push(lvl)
  }
  return { wf, dur }
}

test('Drops: Wechsel von ruhig nach laut auf Phrasengrenzen', () => {
  // Intro 16, Drop 32, Break 16, Drop 32, Outro 16
  const { wf, dur } = track([[16, 0.3], [32, 1], [16, 0.3], [32, 1], [16, 0.3]])
  const d = detectDrops(wf, g, dur)
  assert.equal(d.length, 2)
  assert.ok(Math.abs(d[0] - 16 * bar) < bar / 2)
  assert.ok(Math.abs(d[1] - 64 * bar) < bar / 2)
})

test('Drops: gleichmaessig laut = keine', () => {
  const { wf, dur } = track([[96, 0.9]])
  assert.deepEqual(detectDrops(wf, g, dur), [])
  assert.deepEqual(detectDrops(wf, { ...g, conf: 0.1 }, dur), [])
})

test('Drops: Mixed-In-Key-Cue in der Naehe wird uebernommen', () => {
  const { wf, dur } = track([[16, 0.3], [48, 1], [16, 0.3]])
  const cue = 16 * bar + 0.2
  const d = detectDrops(wf, g, dur, [cue * 1000])
  assert.ok(Math.abs(d[0] - cue) < 1e-9)
})

test('Double Drop: 16 Takte Anlauf, Drops fallen zusammen', () => {
  const a = track([[16, 0.3], [32, 1], [16, 0.3], [32, 1], [16, 0.3]])
  const b = track([[24, 0.3], [48, 1], [16, 0.3]])
  const cur = { drops: detectDrops(a.wf, g, a.dur), g, dur: a.dur }
  const next = { drops: detectDrops(b.wf, g, b.dur), g, dur: b.dur }
  const p = doubleDropPlan(cur, next)
  assert.ok(p)
  assert.equal(p.pre, 16)
  // Drop des laufenden (zweiter) minus 16 Takte, naechster: sein Drop minus 16 Takte
  assert.ok(Math.abs(p.trig - (64 - 16) * bar) < bar / 2)
  assert.ok(Math.abs(p.intro - (24 - 16) * bar) < bar / 2)
  assert.ok(Math.abs(p.trig + p.pre * bar - p.drop1) < 1e-9)
  assert.ok(Math.abs(p.intro + p.pre * bar - p.drop2) < 1e-9)
  assert.equal(p.swapAt, 0.5)
})

test('Double Drop: zu grosser Tempo-Unterschied oder zu spaet = keiner', () => {
  const a = track([[16, 0.3], [32, 1], [16, 0.3]])
  const cur = { drops: detectDrops(a.wf, g, a.dur), g, dur: a.dur }
  const next = { drops: [16 * bar], g: { ...g, bpm: 128 }, dur: 200 }
  assert.equal(doubleDropPlan(cur, next), null)
  // Startpunkt schon vorbei
  const next2 = { drops: [16 * bar], g, dur: 200 }
  assert.equal(doubleDropPlan(cur, next2, 0.08, a.dur), null)
})

test('Auswahl: passend zur Situation, stabil je Titelpaar', () => {
  const cfg = { amount: 1, types: {} }
  assert.equal(chooseTransition(cfg, { seed: 'x', eligible: false }), 'blend')
  assert.equal(chooseTransition(cfg, { seed: 'x', eligible: true, keyClash: true }), 'echo')
  assert.equal(chooseTransition(cfg, { seed: 'x', eligible: true, ddPossible: true }), 'doubledrop')
  const t = chooseTransition(cfg, { seed: 'a|b', eligible: true })
  assert.ok(['filter', 'roll', 'echo'].includes(t))
  assert.equal(chooseTransition(cfg, { seed: 'a|b', eligible: true }), t)
  // abgeschaltete Arten werden nie gewaehlt
  const only = { amount: 1, types: { doubledrop: false, echo: false, roll: false } }
  assert.equal(chooseTransition(only, { seed: 'q', eligible: true, keyClash: true, ddPossible: true }), 'filter')
  // Haeufigkeit 0 = immer normal
  assert.equal(chooseTransition({ amount: 0 }, { seed: 'q', eligible: true, ddPossible: true }), 'blend')
})

test('Haeufigkeit: etwa der eingestellte Anteil', () => {
  let n = 0
  for (let i = 0; i < 2000; i++) if (chooseTransition({ amount: 0.4 }, { seed: 's' + i, eligible: true }) !== 'blend') n++
  assert.ok(n > 700 && n < 900, String(n))
  assert.ok(hash01('abc') >= 0 && hash01('abc') < 1)
})

test('Loop-Roll: 1, ½, ¼, ⅛ Schlag im letzten Takt', () => {
  assert.deepEqual(rollSteps(4).map(s => s.len), [1, 0.5, 0.25, 0.125])
  assert.deepEqual(rollSteps(4).map(s => s.at), [0, 2, 3, 3.5])
})

test('Drops: gemessene Drops aus dem Backend haben Vorrang vor der Wellenform', () => {
  const { wf, dur } = track([[16, 0.3], [32, 1], [16, 0.3], [32, 1], [16, 0.3]])
  const gm = { ...g, drops: [10.5, 99.2, dur + 5] }
  assert.deepEqual(detectDrops(wf, gm, dur), [10.5, 99.2])          // ausserhalb des Titels faellt weg
  assert.deepEqual(detectDrops([], gm, dur), [10.5, 99.2])           // auch ohne Wellenform
  assert.deepEqual(detectDrops(wf, { ...gm, drops: [] }, dur), [])   // gemessen: keine Drops
  assert.deepEqual(detectDrops(wf, { ...gm, conf: 0.2 }, dur), [])   // unsicheres Raster: keine
})
