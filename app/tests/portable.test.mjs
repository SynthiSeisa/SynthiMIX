// Tragbarer Betrieb: Datenordner neben dem Programm, Daten des PCs mitnehmen (node --test)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const { portableDir, migrate, migrateTools, DATA_NAME } = createRequire(import.meta.url)('../electron/portable.cjs')

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

test('Werkzeuge kommen erst nach dem Start nach, nie halb kopiert', async () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'synthimix-portable-'))
  const alt = path.join(base, 'alt'), neu = path.join(base, 'neu')
  fs.mkdirSync(path.join(alt, 'tools', 'ffmpeg-20261001'), { recursive: true })
  fs.writeFileSync(path.join(alt, 'tools', 'ffmpeg-20261001', 'ffmpeg.exe'), 'gross')
  fs.writeFileSync(path.join(alt, 'settings.json'), '{}')
  fs.mkdirSync(neu)
  assert.equal(migrate(alt, neu, fs), true)
  assert.ok(!fs.existsSync(path.join(neu, 'tools')))                       // nicht vor dem Fenster
  fs.mkdirSync(path.join(neu, 'tools.kopie'))                              // Rest eines abgebrochenen Versuchs
  fs.writeFileSync(path.join(neu, 'tools.kopie', 'halb.exe'), 'x')
  assert.equal(await migrateTools(alt, neu, fs.promises), true)
  assert.equal(fs.readFileSync(path.join(neu, 'tools', 'ffmpeg-20261001', 'ffmpeg.exe'), 'utf-8'), 'gross')
  assert.ok(!fs.existsSync(path.join(neu, 'tools.kopie')) && !fs.existsSync(path.join(neu, 'tools', 'halb.exe')))
  assert.equal(await migrateTools(alt, neu, fs.promises), false)           // schon da
  assert.equal(await migrateTools(path.join(base, 'leer'), path.join(base, 'neu3'), fs.promises), false)
})

test('Daten dieses PCs uebernehmen: was auf der Platte lag, bleibt als Sicherung', async () => {
  const { takeOver, countTracks, TAKE_MARK } = createRequire(import.meta.url)('../electron/portable.cjs')
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'synthimix-portable-'))
  const pc = path.join(base, 'pc'), platte = path.join(base, 'platte')
  fs.mkdirSync(pc); fs.mkdirSync(path.join(platte, 'tools'), { recursive: true }); fs.mkdirSync(path.join(platte, 'Cache'))
  fs.writeFileSync(path.join(pc, 'settings.json'), '{"von":"pc"}')
  fs.writeFileSync(path.join(pc, 'library_cache.json'), JSON.stringify([{ path: 'a' }, { path: 'b' }, { path: 'c' }]))
  fs.writeFileSync(path.join(platte, 'settings.json'), '{"von":"fremd"}')
  fs.writeFileSync(path.join(platte, 'library_cache.json'), '[]')
  fs.writeFileSync(path.join(platte, 'tools', 'ffmpeg.exe'), 'x')
  fs.writeFileSync(path.join(platte, TAKE_MARK), '')
  assert.equal(countTracks(pc, fs), 3); assert.equal(countTracks(platte, fs), 0); assert.equal(countTracks(path.join(base, 'nix'), fs), null)
  assert.equal(takeOver(pc, platte, fs, '2026-10-05 10-00-00'), true)
  assert.equal(fs.readFileSync(path.join(platte, 'settings.json'), 'utf-8'), '{"von":"pc"}')
  assert.equal(countTracks(platte, fs), 3)
  const keep = path.join(platte, 'Sicherung 2026-10-05 10-00-00')
  assert.equal(fs.readFileSync(path.join(keep, 'settings.json'), 'utf-8'), '{"von":"fremd"}')
  assert.ok(fs.existsSync(path.join(platte, 'tools', 'ffmpeg.exe')))                    // Werkzeuge bleiben
  assert.ok(fs.existsSync(path.join(platte, 'Cache')) && !fs.existsSync(path.join(keep, 'Cache')))
  // ohne Daten auf dem PC passiert nichts
  assert.equal(takeOver(path.join(base, 'nix'), platte, fs, 'x'), false)
  assert.equal(fs.readFileSync(path.join(platte, 'settings.json'), 'utf-8'), '{"von":"pc"}')
})
