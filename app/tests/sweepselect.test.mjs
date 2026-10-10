// Markieren mit gehaltener Maustaste (node --test)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { rawIndexAt, rangePaths, edgeScroll, isSweepMove } from '../src/lib/sweepselect.js'

test('Zeile unter dem Zeiger, auch ueber und unter der Liste', () => {
  assert.equal(rawIndexAt(100, 100, 28), 0)
  assert.equal(rawIndexAt(100 + 28 * 3 + 5, 100, 28), 3)
  assert.equal(rawIndexAt(90, 100, 28), -1)
  assert.equal(rawIndexAt(100 + 28 * 60, 100, 28), 60)
})

test('Bereich in beide Richtungen, mit Strg bleibt die vorhandene Markierung', () => {
  const p = ['a', 'b', 'c', 'd', 'e']
  assert.deepEqual([...rangePaths(p, 1, 3)], ['b', 'c', 'd'])
  assert.deepEqual([...rangePaths(p, 3, 1)], ['b', 'c', 'd'])
  assert.deepEqual([...rangePaths(p, 2, 2)], ['c'])
  assert.deepEqual([...rangePaths(p, 3, 4, ['a'])].sort(), ['a', 'd', 'e'])
})

test('aus der leeren Flaeche unter der Liste: nur was wirklich ueberstrichen ist', () => {
  const p = ['a', 'b', 'c', 'd', 'e']
  assert.deepEqual([...rangePaths(p, 9, 3)], ['d', 'e'])        // unten angesetzt, bis Zeile 4 hochgezogen
  assert.deepEqual([...rangePaths(p, 9, 7)], [])                // nur im Leeren bewegt: nichts markiert
  assert.deepEqual([...rangePaths(p, -3, 1)], ['a', 'b'])       // ueber die Liste hinaus nach oben
  assert.deepEqual([...rangePaths([], 0, 3)], [])
})

test('nach oben/unten wird markiert, seitwaerts wird der Titel bewegt', () => {
  assert.equal(isSweepMove(1, 6), true)
  assert.equal(isSweepMove(6, 1), false)
  assert.equal(isSweepMove(-2, -8), true)
  assert.equal(isSweepMove(5, 5), false)                // schraeg: Titel bewegen (Playlist links unten)
  assert.equal(isSweepMove(3, 5), false)
  assert.equal(isSweepMove(2, 5), true)
  assert.equal(isSweepMove(0, 0), false)
})

test('am Rand mitrollen, weiter draussen schneller', () => {
  assert.equal(edgeScroll(300, 100, 600), 0)
  assert.ok(edgeScroll(110, 100, 600) < 0)
  assert.ok(edgeScroll(590, 100, 600) > 0)
  assert.ok(edgeScroll(700, 100, 600) > edgeScroll(590, 100, 600))
  assert.equal(edgeScroll(-5000, 100, 600), -26)
})
