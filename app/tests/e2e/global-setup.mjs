// Oberflaechen-Tests: eigenes Backend (Port 8769) mit Testdaten und eigene
// Vite-Oberflaeche (Port 5176). Nie 8765 — dort laeuft das echte SynthiMIX.
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import net from 'node:net'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const APP = path.resolve(HERE, '..', '..')
const BACKEND = path.resolve(APP, '..', 'backend')
export const BACKEND_PORT = 8769
export const UI_PORT = 5176

function waitPort(port, ms = 60000) {
  const until = Date.now() + ms
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const s = net.connect(port, '127.0.0.1')
      s.once('connect', () => { s.destroy(); resolve() })
      s.once('error', () => {
        s.destroy()
        if (Date.now() > until) reject(new Error(`Port ${port} kommt nicht hoch`))
        else setTimeout(tryOnce, 300)
      })
    }
    tryOnce()
  })
}

function portFree(port) {
  return new Promise(resolve => {
    const s = net.connect(port, '127.0.0.1')
    s.once('connect', () => { s.destroy(); resolve(false) })
    s.once('error', () => { s.destroy(); resolve(true) })
  })
}

export default async function globalSetup() {
  for (const p of [BACKEND_PORT, UI_PORT]) {
    if (!(await portFree(p))) throw new Error(`Port ${p} ist belegt — laeuft noch ein alter Testlauf?`)
  }
  const data = mkdtempSync(path.join(tmpdir(), 'synthimix-e2e-'))
  const seed = spawnSync('python', [path.join(HERE, 'seed.py'), data], { encoding: 'utf8' })
  if (seed.status !== 0) throw new Error('Testdaten: ' + seed.stderr)

  const procs = []
  const backend = spawn('python', ['main.py', '--data-dir', data], {
    cwd: BACKEND, env: { ...process.env, SYNTHIMIX_PORT: String(BACKEND_PORT) }, stdio: 'ignore', windowsHide: true,
  })
  procs.push(backend)
  const vite = spawn(process.execPath, [path.join(APP, 'node_modules', 'vite', 'bin', 'vite.js'),
    '--port', String(UI_PORT), '--strictPort'], {
    cwd: APP, env: { ...process.env, VITE_WS_PORT: String(BACKEND_PORT) }, stdio: 'ignore', windowsHide: true,
  })
  procs.push(vite)
  await Promise.all([waitPort(BACKEND_PORT), waitPort(UI_PORT)])
  process.env.E2E_URL = `http://localhost:${UI_PORT}/`

  return async () => {
    for (const p of procs) {
      if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(p.pid), '/T', '/F'], { stdio: 'ignore' })
      else p.kill()
    }
    try { rmSync(data, { recursive: true, force: true }) } catch {}
  }
}
