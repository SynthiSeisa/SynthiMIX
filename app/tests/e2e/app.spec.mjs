// Oberflaechen-Tests gegen ein Test-Backend mit vier erzeugten 128-BPM-Titeln
// (tests/e2e/seed.py). Echte Wiedergabe in Electron, Ton stumm.
import { test, expect, _electron } from '@playwright/test'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const electronPath = createRequire(import.meta.url)('electron')

let app, page

test.beforeAll(async () => {
  app = await _electron.launch({ executablePath: electronPath, args: [path.join(HERE, 'electron-main.cjs')],
                                 env: { ...process.env } })
  page = await app.firstWindow()
  await page.waitForLoadState('domcontentloaded')
  await expect(page.locator('.rows .row').first()).toBeVisible({ timeout: 30000 })
  // Kurze Uebergaenge fuer die Tests: 4 Takte = 7,5 s bei 128 BPM
  await page.evaluate(async () => {
    const url = performance.getEntriesByType('resource').map(e => e.name).find(n => n.includes('/src/stores/ws.js'))
    const m = await import(url)
    m.appSettings.update(s => ({ ...s, cfUnit: 'bars', cfBars: 4, djMode: false }))
  })
})

test.afterAll(async () => { await app?.close() })

// Zugriff auf die Stores der Oberflaeche
async function stores(fn, arg) {
  return page.evaluate(async ([src, a]) => {
    const url = performance.getEntriesByType('resource').map(e => e.name).find(n => n.includes('/src/stores/ws.js'))
    const m = await import(url)
    const read = (st) => { let v; st.subscribe(x => v = x)(); return v }
    return new Function('m', 'read', 'a', `return (async () => { ${src} })()`)(m, read, a)
  }, [fn, arg])
}

// Erster Titel laeuft; danach die Sperre der App abwarten (kein Uebergang in
// den ersten 8 s nach dem letzten Uebergang)
async function startFirst(titles = null) {
  await resetQueue(titles ?? 2)
  await stores(`m.send({ type: 'play_at', index: 0 }); m.send({ type: 'resume' })`)
  const first = await stores(`return read(m.queue)[0].path`)
  await page.waitForFunction((p) => {
    const P = window.__player
    const el = P && (P.which === 'A' ? P.elA : P.elB)
    return el && el.dataset.path === p && !el.paused && P.durMs > 0 && P.triggerMs > 0
  }, first, { timeout: 30000 })
  await page.waitForTimeout(8500)
}

// n: die ersten n Titel der Bibliothek, oder eine Liste von Titeln
async function resetQueue(n = 2) {
  await stores(`
    m.send({ type: 'queue_clear' })
    await new Promise(r => setTimeout(r, 300))
    const lib = read(m.library)
    const pick = Array.isArray(a) ? a.map(x => lib.find(t => t.title === x)) : lib.slice(0, a)
    for (const t of pick) m.send({ type: 'queue_add', path: t.path, title: t.title,
      duration_sec: t.duration_sec, bpm: t.bpm, lufs: t.lufs })
    await new Promise(r => setTimeout(r, 600))
  `, n)
}

test('Bibliothek zeigt die Titel und sortiert per Spaltenklick', async () => {
  await expect(page.locator('.rows .row')).toHaveCount(5)
  const titles = () => page.locator('.rows .row .title, .rows .row [class*="title"]').allInnerTexts()
  const hdr = page.locator('.col-btn', { hasText: 'Titel' }).first()
  await hdr.click()
  const a = await titles()
  await hdr.click()
  const b = await titles()
  expect(a.length).toBeGreaterThan(0)
  expect(b).toEqual([...a].reverse())
})

test('Einstellungen: DJ-Schalter und verfolgte Playlists', async () => {
  await page.getByRole('button', { name: 'Einstellungen', exact: true }).click()
  await page.locator('.tab-btn', { hasText: 'Blend' }).click()
  await expect(page.getByText('Bass tauschen (EQ)')).toBeVisible()
  await expect(page.getByText('Auf Phrasen einrasten')).toBeVisible()
  await page.locator('.tab-btn', { hasText: 'Download' }).click()
  await expect(page.getByText('Verfolgte Playlists und Kanäle', { exact: true })).toBeVisible()
  await expect(page.getByText(/Noch keine\./)).toBeVisible()
  await expect(page.getByText('Gleichzeitige Downloads')).toBeVisible()
  // Dienste als Karten, Details aufklappbar
  await page.locator('.tab-btn', { hasText: 'Dienste' }).click()
  // yt-dlp, ffmpeg, Spotify, Last.fm, AcoustID — Programme stehen hier statt unter System
  await expect(page.locator('.svc')).toHaveCount(5)
  await page.locator('.svc-head', { hasText: 'AcoustID' }).click()
  await expect(page.getByText('Application-Key', { exact: true })).toBeVisible()
  await page.locator('.svc-head', { hasText: 'yt-dlp' }).click()
  await expect(page.locator('.svc.open .svc-body').getByText('Version', { exact: true })).toBeVisible()
  await expect(page.getByText('yt-dlp und ffmpeg automatisch aktuell halten')).toBeVisible()
  // Ausgabegeraet und Uebergangslaenge
  await page.locator('.tab-btn', { hasText: 'Wiedergabe' }).click()
  await expect(page.getByLabel('Ausgabegerät')).toBeVisible()
  await expect(page.getByLabel('Ausgabegerät').locator('option').first()).toHaveText('Windows-Standard')
  await page.locator('.tab-btn', { hasText: 'Blend' }).click()
  await expect(page.getByText('Länge in Takten')).toBeVisible()
  await page.keyboard.press('Escape')
})

test('Uebergang: Einstieg auf der Phrase und weicher Bass-Tausch im Takt', async () => {
  await stores(`m.appSettings.update(s => ({ ...s, beatAlignCf: true, tempoMatch: true, phraseAlign: true, bassSwap: true }))`)
  await startFirst()
  // Raster (mit Phrasen) fuer beide Titel abwarten
  await page.waitForFunction(async () => {
    const url = performance.getEntriesByType('resource').map(e => e.name).find(n => n.includes('/src/stores/ws.js'))
    const m = await import(url)
    let g; m.beatGrids.subscribe(x => g = x)()
    return Object.values(g).filter(x => x.phrase != null).length >= 2
  }, null, { timeout: 30000 })

  const r = await page.evaluate(async () => {
    const P = window.__player
    const sleep = ms => new Promise(res => setTimeout(res, ms))
    await sleep(800)
    const trig = P.triggerMs
    const oldEl = P.which === 'A' ? P.elA : P.elB
    const newEl = oldEl === P.elA ? P.elB : P.elA
    oldEl.currentTime = trig / 1000 - 2
    const s = []
    const t0 = performance.now()
    while (performance.now() - t0 < 13000) {
      // hoerbare Stelle (currentTime + gemessener Sprungfehler, lib/audiblepos.js)
      s.push({ cf: P.cfActive, swap: P.bassSwapOn, eqOld: oldEl === P.elA ? P.eqA : P.eqB,
               eqNew: oldEl === P.elA ? P.eqB : P.eqA, tOld: P.posOf(oldEl), tNew: newEl.paused ? null : P.posOf(newEl) })
      await sleep(50)
    }
    return { trig, s }
  })
  const beat = 60 / 128, bar = 4 * beat, phrase = 0.1 + 8 * beat
  // 4 Takte bei 128 BPM
  expect(await page.evaluate(() => window.__player.cfEff)).toBeCloseTo(4 * bar, 2)
  // Mix-Punkt liegt auf einer Phrasengrenze (8 Takte)
  const k = (r.trig / 1000 - phrase) / (8 * bar)
  expect(Math.abs(k - Math.round(k))).toBeLessThan(0.01)
  expect(r.s.some(x => x.swap)).toBe(true)
  // neuer Titel erst ohne Bass (erste Sekunde; der geplante Wert greift nach ein paar ms)
  const first = r.s.findIndex(x => x.cf)
  expect(Math.min(...r.s.slice(first, first + 20).map(x => x.eqNew))).toBeLessThan(-29)
  const end = r.s.filter(x => x.cf).at(-1)
  expect(end.eqNew).toBeGreaterThan(-1)                   // am Ende: Bass vom neuen Titel
  expect(end.eqOld).toBeLessThan(-29)
  // Eins auf Eins: Taktlage beider Titel waehrend des Uebergangs
  const ph = t => { const p = (t - phrase) / bar; return p - Math.floor(p) }
  const diffs = r.s.filter(x => x.cf && x.tNew != null).map(x => { const d = ph(x.tOld) - ph(x.tNew); return Math.abs(d - Math.round(d)) })
  diffs.sort((x, y) => x - y)
  expect(diffs[Math.floor(diffs.length / 2)]).toBeLessThan(0.05)
})

test('Groesserer Tempo-Unterschied (128 -> 144 BPM, Grenze 15 %): beide treffen sich in der Mitte', async () => {
  await stores(`m.appSettings.update(s => ({ ...s, beatAlignCf: true, tempoMatch: true, phraseAlign: true, bassSwap: true, maxTempoDiff: 15 }))`)
  await startFirst(['Alpha', 'Echo'])
  await page.waitForFunction(async () => {
    const url = performance.getEntriesByType('resource').map(e => e.name).find(n => n.includes('/src/stores/ws.js'))
    const m = await import(url)
    let g, q; m.beatGrids.subscribe(x => g = x)(); m.queue.subscribe(x => q = x)()
    return q.length === 2 && q.every(t => g[t.path]?.phrase != null)
  }, null, { timeout: 30000 })
  const grids = await stores(`const g = read(m.beatGrids); return read(m.queue).map(t => g[t.path])`)
  const r = await page.evaluate(async () => {
    const P = window.__player
    const sleep = ms => new Promise(res => setTimeout(res, ms))
    const oldEl = P.which === 'A' ? P.elA : P.elB
    const newEl = oldEl === P.elA ? P.elB : P.elA
    oldEl.currentTime = P.triggerMs / 1000 - 2
    const s = []
    const t0 = performance.now()
    while (performance.now() - t0 < 13000) {
      s.push({ cf: P.cfActive, swap: P.bassSwapOn, rOld: oldEl.playbackRate, rNew: newEl.playbackRate,
               tOld: P.posOf(oldEl), tNew: newEl.paused ? null : P.posOf(newEl) })
      await sleep(50)
    }
    return s
  })
  const [gA, gE] = grids
  expect(gE.bpm).toBeGreaterThan(140)
  const k = gA.bpm / gE.bpm
  const during = r.filter(x => x.cf && x.tNew != null)
  const late = during.slice(Math.floor(during.length * 0.6))
  const med = (xs) => { const a = [...xs].sort((p, q) => p - q); return a[Math.floor(a.length / 2)] }
  // alter Titel schneller, neuer langsamer — je etwa um die Haelfte
  expect(med(late.map(x => x.rOld))).toBeCloseTo(1 / Math.sqrt(k), 2)
  expect(med(late.map(x => x.rNew))).toBeCloseTo(Math.sqrt(k), 1)
  // gleiches Tempo
  expect(med(late.map(x => x.rOld * gA.bpm / (x.rNew * gE.bpm)))).toBeCloseTo(1, 2)
  // Eins auf Eins und Bass-Tausch
  const bar = (g) => (g.barBeats || 4) * 60 / g.bpm
  const ph = (t, g) => { const p = (t - g.phrase) / bar(g); return p - Math.floor(p) }
  const diffs = during.map(x => { const d = ph(x.tOld, gA) - ph(x.tNew, gE); return Math.abs(d - Math.round(d)) })
  expect(med(diffs)).toBeLessThan(0.05)
  expect(r.some(x => x.swap)).toBe(true)
  await stores(`m.appSettings.update(s => ({ ...s, maxTempoDiff: 8 }))`)
})

test('Jetzt mischen (Fernbedienung): Uebergang startet auf der naechsten Eins', async () => {
  await stores(`m.appSettings.update(s => ({ ...s, beatAlignCf: true, tempoMatch: true, phraseAlign: true, bassSwap: true, maxTempoDiff: 8 }))`)
  await startFirst()
  await page.waitForFunction(async () => {
    const url = performance.getEntriesByType('resource').map(e => e.name).find(n => n.includes('/src/stores/ws.js'))
    const m = await import(url)
    let g, q; m.beatGrids.subscribe(x => g = x)(); m.queue.subscribe(x => q = x)()
    return q.length === 2 && q.every(t => g[t.path]?.phrase != null)
  }, null, { timeout: 30000 })
  // Mitten im Titel, weit vor der Mix-Zone
  const r = await page.evaluate(async () => {
    const P = window.__player
    const sleep = ms => new Promise(res => setTimeout(res, ms))
    const el = P.which === 'A' ? P.elA : P.elB
    el.currentTime = 20.3
    await sleep(400)
    const url = performance.getEntriesByType('resource').map(e => e.name).find(n => n.includes('/src/stores/ws.js'))
    const m = await import(url)
    const t0 = el.currentTime
    m.send({ type: 'mix_now' })                     // wie von der Fernbedienung
    let start = null, swap = false
    const tEnd = performance.now() + 9000
    while (performance.now() < tEnd) {
      if (P.cfActive && start === null) start = el.currentTime
      if (P.bassSwapOn) swap = true
      await sleep(20)
    }
    return { t0, start, swap }
  })
  expect(r.start).not.toBeNull()
  const bar = 4 * 60 / 128, phrase = 0.1 + 8 * 60 / 128
  const ph = ((r.start - phrase) / bar) % 1
  expect(Math.min(ph, 1 - ph)).toBeLessThan(0.12)            // auf einer Eins (Messraster 20 ms + timeupdate)
  expect(r.start - r.t0).toBeLessThan(bar * 1.6)              // spaetestens die uebernaechste Eins
  expect(r.swap).toBe(true)
  // Uebergang zu Ende laufen lassen, sonst laeuft er in den naechsten Test
  await page.waitForFunction(() => !window.__player.cfActive, null, { timeout: 15000 })
})

test('Pause mitten im Uebergang haelt beide Titel an', async () => {
  await startFirst()
  await page.evaluate(() => { const P = window.__player; const el = P.which === 'A' ? P.elA : P.elB; el.currentTime = P.triggerMs / 1000 - 0.5 })
  await page.waitForFunction(() => window.__player.cfActive, null, { timeout: 10000 })
  await page.waitForTimeout(1500)
  await stores(`m.send({ type: 'pause' })`)
  await page.waitForTimeout(2000)
  const st = await page.evaluate(() => ({ a: window.__player.elA.paused, b: window.__player.elB.paused }))
  expect(st).toEqual({ a: true, b: true })
})

test('Doppelklick auf einen anderen Titel: der laufende springt nicht an den Anfang', async () => {
  await startFirst(3)
  // Spulen ueber das Backend: dessen letzte Position ist danach nicht 0 —
  // genau dann spulte der Titelwechsel den laufenden Titel frueher zurueck
  await stores(`m.send({ type: 'seek', position_ms: 20000 })`)
  await page.waitForFunction(() => { const P = window.__player; const el = P.which === 'A' ? P.elA : P.elB; return el.currentTime > 19 }, null, { timeout: 10000 })
  await page.waitForTimeout(1000)
  const r = await page.evaluate(async () => {
    const P = window.__player
    const old = P.which === 'A' ? P.elA : P.elB
    const rows = [...document.querySelectorAll('.queue-list .row')]
    rows[2].dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    let min = old.currentTime
    const t0 = performance.now()
    while (performance.now() - t0 < 2500) {
      if (!old.paused) min = Math.min(min, old.currentTime)
      await new Promise(res => setTimeout(res, 20))
    }
    const neu = old === P.elA ? P.elB : P.elA
    return { min, neuPath: neu.dataset.path || '', neuPlaying: !neu.paused }
  })
  expect(r.min).toBeGreaterThan(15)                  // nie zurueck an den Anfang
  expect(r.neuPlaying).toBe(true)                    // Uebergang laeuft
  expect(r.neuPath).toContain('Charlie')
})

test('Lange Warteschlange zeichnet nur sichtbare Zeilen', async () => {
  await stores(`const lib = read(m.library); m.queue.set(Array.from({ length: 1500 }, (_, i) => ({ ...lib[i % lib.length], title: 'Titel ' + i })))`)
  const rows = await page.locator('.queue-list .row').count()
  expect(rows).toBeLessThan(150)
  await page.locator('.queue-list').evaluate(el => { el.scrollTop = el.scrollHeight / 2; el.dispatchEvent(new Event('scroll')) })
  // Mitte der Liste: Zeilen um Titel 750 herum sind gezeichnet
  await expect(page.locator('.queue-list .row .title', { hasText: /^Titel 7[0-9]{2}$/ }).first()).toBeVisible()
  await resetQueue(2)
})
