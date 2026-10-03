// Hoerbare Stelle: Versatz zwischen gemessener Huelle und Referenz finden (node --test)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { feature, bestLag } from '../src/lib/audiblepos.js'

// Huelle wie Musik: Schlaege alle 343 ms (175 BPM), Snare lauter, dazu
// unregelmaessige Akzente (Fills, Gesang) — 1 Wert je ms
function music(ms, seed = 1) {
  const e = new Float32Array(ms)
  let s = seed
  const rnd = () => ((s = (s * 1103515245 + 12345) >>> 0) / 4294967296)
  for (let i = 0; i < ms; i++) e[i] = 1e-4 * (1 + 0.3 * rnd())
  for (let b = 0; b * 343 < ms; b++) {
    const amp = b % 2 ? 0.05 : 0.02
    for (let k = 0; k < 60 && b * 343 + k < ms; k++) e[b * 343 + k] += amp * Math.exp(-k / 15)
  }
  for (let j = 0; j < ms / 400; j++) {
    const at = Math.floor(rnd() * ms), amp = 0.03 * rnd()
    for (let k = 0; k < 40 && at + k < ms; k++) e[at + k] += amp * Math.exp(-k / 10)
  }
  return e
}

for (const lagMs of [-355, -237, 0, 52, 376]) {
  test(`Versatz ${lagMs} ms wird gefunden`, () => {
    const full = music(20000, 7), lo = music(20000, 99)
    const r0 = 5.0                                   // Referenz ab 5 s im Titel
    const ref = [full.slice(5000, 11000), lo.slice(5000, 11000)].map(feature)
    // gemeldet: 6.2 s, hoerbar ist aber 6.2 + lag
    const m0 = 6.2, start = Math.round((m0 + lagMs / 1000) * 1000)
    const meas = [full.slice(start, start + 3000), lo.slice(start, start + 3000)].map(feature)
    const r = bestLag(meas, m0, ref, r0)
    assert.ok(Math.abs(r.lag * 1000 - lagMs) <= 2, `gefunden ${r.lag * 1000} ms`)
    assert.ok(r.score > 0.9 && r.ratio > 1.08, JSON.stringify(r))
  })
}

test('Nur Rauschen: keine eindeutige Spitze', () => {
  let s = 3
  const rnd = () => ((s = (s * 1103515245 + 12345) >>> 0) / 4294967296)
  const noise = (n) => Float32Array.from({ length: n }, () => 1e-4 * (1 + rnd()))
  const r = bestLag([feature(noise(3000)), feature(noise(3000))], 6.2, [feature(noise(6000)), feature(noise(6000))], 5.0)
  assert.ok(!(r.score >= 0.3 && r.ratio >= 1.08), JSON.stringify(r))
})
