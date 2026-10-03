// Tragbarer Betrieb: Datenordner neben dem Programm, Daten des PCs mitnehmen (node --test)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const { portableDir, migrate, DATA_NAME } = createRequire(import.meta.url)('../electron/portable.cjs')

test('auf dem Systemlaufwerk: kein tragbarer Betrieb', () => {
  assert.equal(portableDir('C:\\Users\\A\\AppData\\Local\\Programs\\SynthiMIX\\SynthiMIX.exe', 'C:'), null)
  assert.equal(portableDir('c:\\Tools\\SynthiMIX\\SynthiMIX.exe', 'C:'), null)
})

test('auf einer anderen Platte: Daten neben dem Programmordner, nicht darin', () => {
  assert.equal(portableDir('M:\\Programme\\SynthiMIX\\SynthiMIX.exe', 'C:'), 'M:\\Programme\\' + DATA_NAME)
  assert.equal(portableDir('E:\\SynthiMIX\\SynthiMIX.exe', 'C:'), 'E:\\' + DATA_NAME)
  assert.equal(portableDir('E:\\SynthiMIX.exe', 'C:'), 'E:\\' + DATA_NAME)
  // Windows liegt ausnahmsweise auf D: — dann ist D: das Systemlaufwerk
  assert.equal(portableDir('D:\\SynthiMIX\\SynthiMIX.exe', 'D:'), null)
})

test('Netzpfade ohne Laufwerksbuchstaben: kein tragbarer Betrieb', () => {
  assert.equal(portableDir('\\\\server\\freigabe\\SynthiMIX\\SynthiMIX.exe', 'C:'), null)
})

test('erster tragbarer Start nimmt die Daten des PCs mit, ohne Zwischenspeicher', () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'synthimix-portable-'))
  const alt = path.join(base, 'alt'), neu = path.join(base, 'neu')
  fs.mkdirSync(path.join(alt, 'playlists'), { recursive: true })
  fs.mkdirSync(path.join(alt, 'Cache'), { recursive: true })
  fs.mkdirSync(path.join(alt, 'Local Storage', 'leveldb'), { recursive: true })
  fs.writeFileSync(path.join(alt, 'settings.json'), '{"volume":50}')
  fs.writeFileSync(path.join(alt, 'library_cache.json'), '[]')
  fs.writeFileSync(path.join(alt, 'playlists', 'a.m3u'), '#EXTM3U')
  fs.writeFileSync(path.join(alt, 'Cache', 'gross'), 'x')
  fs.writeFileSync(path.join(alt, 'Local Storage', 'leveldb', '000001.log'), 'ui')
  fs.writeFileSync(path.join(alt, 'lockfile'), '')
  fs.mkdirSync(neu)
  assert.equal(migrate(alt, neu, fs), true)
  assert.equal(fs.readFileSync(path.join(neu, 'settings.json'), 'utf-8'), '{"volume":50}')
  assert.ok(fs.existsSync(path.join(neu, 'library_cache.json')))
  assert.ok(fs.existsSync(path.join(neu, 'playlists', 'a.m3u')))
  assert.ok(fs.existsSync(path.join(neu, 'Local Storage', 'leveldb', '000001.log')))     // Einstellungen der Oberflaeche
  assert.ok(!fs.existsSync(path.join(neu, 'Cache')) && !fs.existsSync(path.join(neu, 'lockfile')))
  assert.ok(fs.existsSync(path.join(alt, 'settings.json')))                               // das Original bleibt
  // zweiter Start: schon da, nichts ueberschreiben
  fs.writeFileSync(path.join(neu, 'settings.json'), '{"volume":80}')
  assert.equal(migrate(alt, neu, fs), false)
  assert.equal(fs.readFileSync(path.join(neu, 'settings.json'), 'utf-8'), '{"volume":80}')
  // frischer PC ohne eigene Daten: nichts zu holen
  assert.equal(migrate(path.join(base, 'leer'), path.join(base, 'neu2'), fs), false)
})
