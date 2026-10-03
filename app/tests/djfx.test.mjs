// Uebergaenge wie am Mischpult: Verlaeufe von Fader, EQ, Filter und Effekten (node --test)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mixState, ramp, up, down, sweep, spinSpeed, CUT_TYPES, TAIL_BARS, EQMIX_BARS } from '../src/lib/djfx.js'

const steps = (n = 400) => Array.from({ length: n + 1 }, (_, i) => i / n)

test('Grundfunktionen', () => {
  assert.equal(ramp(5, 0, 10), 0.5); assert.equal(ramp(-1, 0, 10), 0); assert.equal(ramp(11, 0, 10), 1)
  assert.equal(up(0), 0); assert.ok(Math.abs(up(1) - 1) < 1e-12); assert.equal(down(0), 1)
  assert.ok(Math.abs(up(0.5) ** 2 + down(0.5) ** 2 - 1) < 1e-12)         // gleiche Leistung
  assert.ok(Math.abs(sweep(20, 2000, 0.5) - 200) < 1e-9)                // logarithmisch
})

for (const type of ['blend', 'eqmix', 'filter']) {
  test(`${type}: anfangs nur alt, am Ende nur neu, ohne Loch und ohne Spruenge`, () => {
    const a = mixState(type, { t: 0 }), z = mixState(type, { t: 1 })
    assert.ok(a.fo === 1 && a.fn < 0.01, JSON.stringify(a))
    assert.ok(z.fo < 0.01 && z.fn > 0.99, JSON.stringify(z))
    let prev = null
    for (const t of steps()) {
      const m = mixState(type, { t })
      const power = m.fo ** 2 + m.fn ** 2
      assert.ok(power >= 0.8, `${type} t=${t}: Leistung ${power.toFixed(2)} (Loch)`)
      assert.ok(power <= 2.0, `${type} t=${t}: Leistung ${power.toFixed(2)} (zu laut)`)
      if (prev) for (const k of ['fo', 'fn']) assert.ok(Math.abs(m[k] - prev[k]) < 0.05, `${type} ${k} springt bei t=${t}`)
      for (const k of ['hiO', 'midO', 'hiN', 'midN']) if (m[k] != null) assert.ok(m[k] <= 0 && m[k] >= -12, `${type} ${k}=${m[k]}`)
      prev = m
    }
  })
}

test('Blend: die Hoehen des neuen kommen erst nach und nach, der alte gibt sie zuerst ab', () => {
  assert.ok(mixState('blend', { t: 0.1 }).hiN < -6)
  assert.ok(Math.abs(mixState('blend', { t: 0.5 }).hiN) < 1e-9)
  assert.ok(mixState('blend', { t: 0.9 }).hiO < -6)
})

test('Langer EQ-Mix: vier Phrasen ueber 32 Takte', () => {
  const at = (bar) => mixState('eqmix', { t: bar / EQMIX_BARS })
  assert.ok(at(4).fn < 0.75 && at(4).hiN < -6)         // Phrase 1: leise, duenn
  assert.ok(at(16).fn > 0.99 && at(16).hiN === 0)       // nach Phrase 2 offen
  assert.ok(at(20).fo > 0.85 && at(20).hiO < -3)        // Phrase 3: alt gibt Hoehen ab
  assert.ok(at(31).fo < 0.2 && at(32).fo < 0.01)        // Phrase 4: alt raus
})

test('Filter: Tiefpass des neuen oeffnet sich, Hochpass des alten steigt', () => {
  assert.ok(mixState('filter', { t: 0 }).lpN <= 300 && mixState('filter', { t: 0.7 }).lpN >= 19000)
  assert.ok(mixState('filter', { t: 0.2 }).hpO <= 21 && mixState('filter', { t: 1 }).hpO >= 900)
})

test('Double Drop: Anlauf duenn und leiser, auf dem Drop voll, danach geht der alte', () => {
  const s = (rel) => mixState('doubledrop', { rel, pre: 16, post: 16 })
  assert.ok(s(-16).fn < 0.05 && s(-16).hpN >= 350)
  assert.ok(s(-1).fn <= 0.7 && s(-1).hpN < 200 && s(-1).hpN > 120)    // Bass bleibt raus (Hochpass)
  assert.equal(s(0).fn, 1); assert.equal(s(0).hpN, 20)
  assert.ok(Math.abs(s(4).fo - 0.9) < 1e-9)                            // beide Drops zusammen
  assert.ok(Math.abs(s(11).fo - 0.9) < 1e-9 && s(11).midO === 0)       // der alte bleibt lange voll hoerbar
  assert.ok(s(16).fo < 0.01)
})

test('Schnitt-Uebergaenge: Echo/Hall aufziehen vor der Eins, danach nur der Ausklang', () => {
  for (const type of CUT_TYPES) {
    const before = mixState(type, { rel: -0.5 }), after = mixState(type, { rel: 0.5 })
    assert.equal(before.fo, undefined); assert.equal(before.fn, undefined)   // die Fader setzt der Player auf der Eins
    assert.ok(TAIL_BARS[type] > 0)
    void after
  }
  assert.ok(mixState('echo', { rel: -1 }).echo === 0 && mixState('echo', { rel: -0.01 }).echo > 0.95)
  assert.equal(mixState('echo', { rel: 0.5 }).echo, 0)                  // nach der Eins kein neues Signal ins Echo
  assert.ok(mixState('echo', { rel: 2 }).echoHp > 1500)                  // Ausklang wird duenner
  assert.ok(mixState('hall', { rel: -0.01 }).hall > 0.95 && mixState('hall', { rel: 0.5 }).hall === 0)
  assert.ok(mixState('roll', { rel: -0.01 }).hpO > 1000)                 // Spannung durch Hochpass
  assert.ok(mixState('roll', { rel: -1 }).hpO > 100 && mixState('roll', { rel: -2 }).hpO <= 21)   // ueber zwei Takte
  assert.ok(mixState('roll', { rel: -0.01 }).hall > 0.7 && mixState('roll', { rel: -1.2 }).hall > 0.25)     // Hall von Anfang an
  assert.ok(mixState('backspin', { rel: -0.8 }).hall > 0.7)                                              // Spin mit Hall, nicht trocken
})

test('Backspin: kurz anreissen, dann auslaufen', () => {
  assert.equal(spinSpeed(0), 0)
  assert.ok(Math.abs(spinSpeed(0.04) - 5) < 1e-9)                       // schnell angerissen
  assert.ok(spinSpeed(0.5) < spinSpeed(0.2) && spinSpeed(0.999) < 0.25)  // laeuft aus, Tonhoehe faellt
  assert.ok(spinSpeed(0.999) > 0.1)                                      // aber dreht bis zum Schluss
})
