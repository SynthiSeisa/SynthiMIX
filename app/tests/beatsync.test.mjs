// Rechenteil von Beat-Sync, Phrasen und Bass-Tausch (node --test, ohne Abhaengigkeiten)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { barLen, snapToPhrase, barAlignedStart, swapEligible, bassSwapPlan, phaseOf } from '../src/lib/beatsync.js'

const g128 = { bpm: 128, off: 0.1, conf: 0.8, phrase: 0.1, barBeats: 4 }
const bar128 = 4 * 60 / 128                    // 1,875 s

test('Taktlaenge, Halbtempo zaehlt 2 Schlaege', () => {
  assert.equal(barLen(g128), bar128)
  assert.equal(barLen({ bpm: 87 }), 2 * 60 / 87)
  assert.ok(Math.abs(barLen({ bpm: 87 }) - barLen({ bpm: 174 })) < 1e-9)
})

test('Phrase: naechste 16-Takt-Grenze, hoechstens 8 Takte weit', () => {
  const p16 = 0.1 + 16 * bar128 * 3              // 3. Phrase
  assert.ok(Math.abs(snapToPhrase(p16 + 5 * bar128, g128) - p16) < 1e-9)
  assert.ok(Math.abs(snapToPhrase(p16 - 7 * bar128, g128) - p16) < 1e-9)
  // Grenze liegt hinter hi: 8-Takt-Raster, sonst Taktanfang
  const t = p16 - 3 * bar128
  const s = snapToPhrase(t, g128, 0, p16 - 1)
  assert.ok(Math.abs(s - (p16 - 8 * bar128)) < 1e-9)
  // ohne Phrasen: unveraendert
  assert.equal(snapToPhrase(42, { bpm: 128, off: 0.1 }), 42)
})

test('Eins auf Eins: Einstieg an derselben Stelle im Takt', () => {
  const cur = { currentTime: 0.1 + 20 * bar128 + 0.5, playbackRate: 1 }   // 0,5 s nach einer Eins
  const gN = { bpm: 128, off: 0.3, conf: 0.9, phrase: 0.3, barBeats: 4 }
  const s = barAlignedStart(cur, 30, g128, gN, 0)
  const pc = phaseOf(cur.currentTime, 60 / bar128, g128.phrase)
  const pn = phaseOf(s, 60 / bar128, gN.phrase)
  assert.ok(Math.abs(pc - pn) < 1e-6)
  assert.ok(Math.abs(s - 30) <= bar128 / 2 + 1e-9)
})

test('Eins auf Eins auch 87 (Halbtempo) gegen 174', () => {
  const g174 = { bpm: 174, off: 0.05, conf: 0.9, phrase: 0.05, barBeats: 4 }
  const g87 = { bpm: 87, off: 0.2, conf: 0.9, phrase: 0.2, barBeats: 2 }
  const cur = { currentTime: 60.7, playbackRate: 1 }
  const s = barAlignedStart(cur, 12, g174, g87, 0)
  const b = barLen(g174)
  assert.ok(Math.abs(phaseOf(cur.currentTime, 60 / b, g174.phrase) - phaseOf(s, 60 / b, g87.phrase)) < 1e-6)
})

test('Bass-Tausch nur im Takt', () => {
  const cur = { playbackRate: 1 }
  const cfg = { beatAlignCf: true, tempoMatch: true }
  assert.ok(swapEligible(cur, g128, { bpm: 126, conf: 0.7 }, cfg))
  assert.ok(!swapEligible(cur, g128, { bpm: 100, conf: 0.9 }, cfg))          // Tempo zu weit weg
  assert.ok(!swapEligible(cur, g128, { bpm: 128, conf: 0.2 }, cfg))          // Raster unsicher
  assert.ok(!swapEligible(cur, g128, { bpm: 128, conf: 0.9 }, { beatAlignCf: false }))
  assert.ok(!swapEligible(cur, g128, { bpm: 124, conf: 0.9 }, { beatAlignCf: true, tempoMatch: false }))
})

test('Bass-Tausch: 2 Takte in der Mitte, auf einer Eins', () => {
  const now = 0.1 + 100 * bar128 + 0.4
  const plan = bassSwapPlan(now, 1, g128, 16)
  assert.equal(plan.bars, 2)
  assert.ok(Math.abs(plan.len - 2 * bar128) < 1e-9)
  assert.ok(plan.delay >= 0.3 && plan.delay + plan.len <= 16 - 0.2)
  const startTrack = now + plan.delay
  assert.ok(Math.abs(phaseOf(startTrack, 60 / bar128, g128.phrase)) < 1e-6 ||
            Math.abs(phaseOf(startTrack, 60 / bar128, g128.phrase) - 1) < 1e-6)
  // langsam (90 BPM, 4er-Takt ~2,7 s): nur 1 Takt
  assert.equal(bassSwapPlan(10, 1, { bpm: 90, off: 0, barBeats: 4 }, 16).bars, 1)
  // zu kurzer Uebergang: kein Tausch
  assert.equal(bassSwapPlan(10, 1, g128, 1.5), null)
})
