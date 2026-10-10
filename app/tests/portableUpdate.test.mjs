// Update im tragbaren Betrieb: nur die Programmdateien auf der Platte ersetzen,
// nie der Windows-Installer (node --test)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const { script, start } = createRequire(import.meta.url)('../electron/portableUpdate.cjs')

test('Skript: Pfade sicher eingesetzt, kein Installer-Aufruf, nichts wird geloescht', () => {
  const s = script({ installer: "C:\\Temp\\O'Neil\\Setup.exe", installDir: 'D:\\Vaki\\Programme\\Synthimix', sevenZip: 'D:\\x\\7za.exe',
                     tmpDir: 'C:\\Temp\\u1', logFile: 'D:\\Vaki\\Programme\\SynthiMIX-Daten\\portable-update.log' })
  assert.ok(s.includes("$installer = 'C:\\Temp\\O''Neil\\Setup.exe'"))             // Hochkomma verdoppelt
  assert.ok(s.includes("$dir = 'D:\\Vaki\\Programme\\Synthimix'"))
  assert.ok(s.includes('robocopy $tmp $dir /E '))
  assert.ok(!/\/MIR|\/PURGE/i.test(s))                                               // nur ersetzen und ergaenzen
  assert.ok(!/--force-run|\/D=|\/currentuser/.test(s))                               // der Installer wird nicht gestartet
  assert.ok(s.includes("'--updated'") && s.includes("'--update-failed'"))
  const ohne = script({ installer: 'a', installDir: 'b', sevenZip: 'c', tmpDir: 'd', logFile: 'e', relaunch: false })
  assert.ok(ohne.includes('# kein Neustart') && !ohne.includes('Start-Process'))
})

test('Start: nur wenn Update-Datei und 7za da sind; Skript mit BOM, losgeloest', () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'synthimix-pupd-'))
  const res = path.join(base, 'resources'); fs.mkdirSync(res)
  const inst = path.join(base, 'Setup.exe'); fs.writeFileSync(inst, 'x')
  const calls = []
  const spawn = (cmd, args, opts) => { calls.push({ cmd, args, opts }); return { unref() {} } }
  const opt = { installer: inst, installDir: path.join(base, 'Prüf Ordner'), resourcesDir: res, dataDir: base, fs, spawn, tmpRoot: base }
  assert.equal(start(opt), false)                                                    // 7za fehlt: nichts tun
  fs.writeFileSync(path.join(res, '7za.exe'), 'x')
  assert.equal(start({ ...opt, installer: path.join(base, 'fehlt.exe') }), false)    // Update-Datei fehlt
  assert.equal(start({ ...opt, installer: null }), false)
  assert.equal(calls.length, 0)
  assert.equal(start(opt), true)
  assert.equal(calls[0].cmd, 'powershell.exe')
  assert.equal(calls[0].opts.detached, true)
  const text = fs.readFileSync(calls[0].args.at(-1))
  assert.deepEqual([...text.subarray(0, 3)], [0xEF, 0xBB, 0xBF])                     // BOM: Umlaute im Pfad
  assert.ok(text.toString('utf-8').includes('Prüf Ordner'))
})
