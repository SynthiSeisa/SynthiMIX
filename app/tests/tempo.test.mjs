// Groesserer Tempo-Unterschied (einstellbar) und "Treffen in der Mitte"
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { tempoMatch, createSync, meetTarget, meetRate, bassSwapPlan, swapEligible, barLen, phaseOf } from '../src/lib/beatsync.js'

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`)
const el = (t = 0, rate = 1) => ({ currentTime: t, playbackRate: rate, paused: false, seeking: false })

test('Grenze einstellbar: 128 -> 150 BPM erst ab 15 %', () => {
  assert.equal(tempoMatch(128, 150), null)                 // Standard 8 %
  assert.equal(tempoMatch(128, 150, 0.12), null)
  near(tempoMatch(128, 150, 0.15).rate, 128 / 150)
  assert.equal(tempoMatch(128, 174, 0.25), null)           // House -> DnB: bleibt normaler Blend
  near(tempoMatch(128, 70, 0.25).rate, 128 / 140, 1e-9)  // Halbtempo-Raster zaehlt doppelt
})

test('Treffen in der Mitte: beide weichen gleich weit ab', () => {
  const k = 128 / 144                                     // neuer Titel muss auf 128 runter
  const rc = meetTarget(k), rn = rc * k
  near(rc * 128, rn * 144)                                // gleiches Tempo
  near(rc, 1 / rn)                                        // alt schneller, neu langsamer, gleich weit
  assert.ok(rc > 1 && rn < 1)
  near(meetRate(el(0, 1), { bpm: 128 }, { bpm: 144 }, 0.15), rc)
  assert.equal(meetRate(el(0, 1), { bpm: 128 }, { bpm: 144 }, 0.08), null)
})

test('createSync: alter Titel gleitet in 40 % aufs Ziel, neuer bleibt gekoppelt', () => {
  const cur = el(10), next = el(3)
  const g1 = { bpm: 128, off: 0, conf: 0.9 }, g2 = { bpm: 144, off: 0, conf: 0.9 }
  const s = createSync(cur, next, g1, g2, { tempo: true, phase: false, maxDiff: 0.15 })
  near(next.playbackRate, 128 / 144)                      // Start: neuer voll angepasst
  s.tick(true, 0.2)
  const k = 128 / 144
  near(cur.playbackRate, 1 + (meetTarget(k) - 1) * 0.5)
  near(next.playbackRate, cur.playbackRate * k)
  s.tick(false, 0.9)
  near(cur.playbackRate, meetTarget(k))
  near(next.playbackRate * 144, cur.playbackRate * 128)
  s.settle()
  near(next.playbackRate, Math.sqrt(k))
  // ohne Treffen: alter Titel bleibt unberuehrt
  const c2 = el(10), n2 = el(3)
  createSync(c2, n2, g1, g2, { tempo: true, phase: false, maxDiff: 0.15, meet: false }).tick(true, 1)
  near(c2.playbackRate, 1)
})

test('Abbruch: alter Titel gleitet zurueck aufs Original', async () => {
  const cur = el(10), next = el(3)
  const s = createSync(cur, next, { bpm: 128, off: 0, conf: 0.9 }, { bpm: 140, off: 0, conf: 0.9 },
                       { tempo: true, phase: false, maxDiff: 0.12 })
  s.tick(true, 1)
  assert.ok(cur.playbackRate > 1.01)
  const stop = s.abort()
  await new Promise(r => setTimeout(r, 3300))
  stop()
  near(cur.playbackRate, 1)
})

test('Bass-Tausch mit Tempo-Rampe liegt trotzdem auf einer Eins', () => {
  const g = { bpm: 128, off: 0.1, phrase: 0.1, barBeats: 4 }
  const bar = barLen(g), now = 0.1 + 50 * bar + 0.3
  const r1 = 1.06, fade = 16, R = 0.4 * fade
  const p = bassSwapPlan(now, 1, g, fade, { r1, sec: R })
  // Titelzeit am Start des Tauschs ueber die Rampe nachrechnen
  const adv = t => t <= R ? t + (r1 - 1) * t * t / (2 * R) : R + (r1 - 1) * R / 2 + r1 * (t - R)
  const ph = phaseOf(now + adv(p.delay), 60 / bar, g.phrase)
  assert.ok(Math.min(ph, 1 - ph) < 1e-4, `Phase ${ph}`)
  near(adv(p.delay + p.len) - adv(p.delay), p.bars * bar, 1e-4)
  assert.ok(p.delay + p.len <= fade - 0.2)
})

test('Bass-Tausch folgt der eingestellten Grenze', () => {
  const cur = el(0, 1), g1 = { bpm: 128, conf: 0.9 }, g2 = { bpm: 145, conf: 0.9 }
  assert.ok(!swapEligible(cur, g1, g2, { beatAlignCf: true, maxTempoDiff: 8 }))
  assert.ok(swapEligible(cur, g1, g2, { beatAlignCf: true, maxTempoDiff: 15 }))
})
