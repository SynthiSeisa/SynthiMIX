// Lautheit eines DJ-Sets: Bezugswert und Einordnung (node --test)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { analyze, setReference } from '../src/lib/setloudness.js'

const set = [{ path: 'a', lufs: -8 }, { path: 'b', lufs: -8.4 }, { path: 'c', lufs: -13.6 }, { path: 'd', lufs: -4.3 }, { path: 'e', lufs: null }]

test('Bezugswert ist der Median, auf halbe dB — ein Ausreisser zieht ihn nicht mit', () => {
  assert.equal(setReference(set), -8)                       // Median von -13.6 -8.4 -8 -4.3 = -8.2
  assert.equal(setReference([{ lufs: -9.1 }]), -9)
  assert.equal(setReference([{ lufs: null }, { lufs: -99 }]), null)
})

test('gegen das Set: zu leise, zu laut, passt, noch nicht gemessen', () => {
  const r = analyze(set, { mode: 'set', tol: 1.5 })
  assert.equal(r.ref, -8)
  assert.deepEqual(r.rows.map(x => x.state), ['ok', 'ok', 'quiet', 'loud', 'unknown'])
  assert.deepEqual(r.rows.map(x => x.delta), [0, -0.4, -5.6, 3.7, null])
  assert.deepEqual([r.quiet, r.loud, r.ok, r.unknown], [1, 1, 2, 1])
})

test('gegen einen festen Zielwert und mit anderer Toleranz', () => {
  const r = analyze(set, { mode: 'fixed', target: -10, tol: 2 })
  assert.equal(r.ref, -10)
  assert.deepEqual(r.rows.map(x => x.state), ['ok', 'ok', 'quiet', 'loud', 'unknown'])
  assert.deepEqual(analyze(set, { mode: 'fixed', target: -10, tol: 1 }).rows.map(x => x.state), ['loud', 'loud', 'quiet', 'loud', 'unknown'])
})

test('leeres oder ungemessenes Set', () => {
  assert.deepEqual(analyze([], {}).rows, [])
  assert.equal(analyze([{ path: 'x', lufs: null }], {}).rows[0].state, 'unknown')
})
