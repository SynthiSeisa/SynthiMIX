// Meldung, wenn der Dienst nicht laeuft oder die Platte weg ist (node --test)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { noticeFor } from '../src/lib/backendNotice.js'

test('verbunden und alles da: keine Meldung', () => {
  assert.equal(noticeFor({ connected: true, status: { running: true } }), null)
  assert.equal(noticeFor({ connected: true, status: null }), null)
})

test('kurz ohne Verbindung (Start): noch nichts, dann Hinweis, dann Fehler', () => {
  assert.equal(noticeFor({ connected: false, downFor: 3, status: { running: true } }), null)
  assert.equal(noticeFor({ connected: false, downFor: 8, status: { running: true } }).level, 'info')
  const n = noticeFor({ connected: false, downFor: 25, status: { running: false } })
  assert.equal(n.level, 'error'); assert.ok(n.retry); assert.match(n.text, /läuft nicht/)
})

test('Port belegt und aufgegeben werden beim Namen genannt', () => {
  const p = noticeFor({ connected: false, downFor: 8, status: { portBusy: true, gaveUp: true } })
  assert.equal(p.level, 'error'); assert.match(p.text, /8765/)
  const g = noticeFor({ connected: false, downFor: 8, status: { gaveUp: true } })
  assert.equal(g.level, 'error'); assert.match(g.title, /startet nicht/)
})

test('Platte abgezogen: Meldung auch bei stehender Verbindung', () => {
  const n = noticeFor({ connected: true, status: { dataGone: true, portable: true, dataDir: 'E:\\SynthiMIX-Daten' } })
  assert.equal(n.level, 'error'); assert.match(n.title, /Festplatte/); assert.match(n.text, /E:\\SynthiMIX-Daten/)
  assert.equal(n.retry, false)
})

test('im Browser ohne Electron: nach 20 s ein Fehler ohne Knopf', () => {
  const n = noticeFor({ connected: false, downFor: 30, status: null })
  assert.equal(n.level, 'error'); assert.equal(n.retry, false)
})
