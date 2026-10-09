// Titel als Dateien herausziehen: der eigene Zug wird beim Ablegen wiedererkannt
import { test } from 'node:test'
import assert from 'node:assert/strict'

const sent = []
globalThis.window = {
  addEventListener() {},
  electron: { startDrag: (p) => sent.push(p), pathForFile: (f) => f.path },
}
const { startFileDrag, ownDrag, hasFiles } = await import('../src/lib/fileDrag.js')

const ev = (paths) => ({ dataTransfer: { types: ['Files'], files: paths.map((path) => ({ path })) } })
const startEv = () => { const e = { prevented: false, preventDefault() { this.prevented = true } }; return e }

test('Datei-Zug startet mit den Pfaden und unterdrueckt das HTML-Ziehen', () => {
  const e = startEv()
  assert.equal(startFileDrag(e, [{ path: 'M:\\Musik\\a.mp3' }, { path: 'M:\\Musik\\b.mp3' }]), true)
  assert.equal(e.prevented, true)
  assert.deepEqual(sent.at(-1), ['M:\\Musik\\a.mp3', 'M:\\Musik\\b.mp3'])
})

test('eigener Zug: gleiche Dateien, Reihenfolge und Schreibweise egal, nur einmal', () => {
  startFileDrag(startEv(), [{ path: 'M:\\Musik\\a.mp3', title: 'A' }, { path: 'M:\\Musik\\b.mp3' }], { queueFrom: 3 })
  assert.equal(ownDrag(ev(['m:/musik/b.mp3'])), null)                     // andere Auswahl
  const own = ownDrag(ev(['m:/musik/B.mp3', 'M:\\Musik\\a.mp3']))
  assert.equal(own.queueFrom, 3)
  assert.equal(own.tracks[0].title, 'A')
  assert.equal(ownDrag(ev(['M:\\Musik\\a.mp3', 'M:\\Musik\\b.mp3'])), null) // schon verbraucht
})

test('fremde Dateien (Explorer) sind kein eigener Zug', () => {
  startFileDrag(startEv(), [{ path: 'M:\\Musik\\a.mp3' }])
  assert.equal(ownDrag(ev(['C:\\Users\\x\\Desktop\\neu.mp3'])), null)
  assert.equal(hasFiles(ev(['x'])), true)
  assert.equal(hasFiles({ dataTransfer: { types: ['text/plain'] } }), false)
})

test('ohne Electron: normales HTML-Ziehen', () => {
  const keep = window.electron
  window.electron = undefined
  const e = startEv()
  assert.equal(startFileDrag(e, [{ path: 'a.mp3' }]), false)
  assert.equal(e.prevented, false)
  window.electron = keep
})

test('ganze Playlist ziehen: alle Titel als Dateien, im Fenster als eigener Zug erkannt', async () => {
  const { startPlaylistDrag, draggingPlaylist } = await import('../src/lib/fileDrag.js')
  const keep = window.electron
  const asked = []
  window.electron = { ...keep, startDragPlaylist: (p) => { asked.push(p); return p.includes('Leer') ? [] : ['M:\Musik\a.mp3', 'M:\Musik\b.mp3'] } }
  try {
    const e = startEv()
    assert.equal(startPlaylistDrag(e, 'M:\pl\Set.m3u'), true)
    assert.equal(e.prevented, true)
    assert.deepEqual(asked, ['M:\pl\Set.m3u'])
    assert.equal(draggingPlaylist(), 'M:\pl\Set.m3u')
    // fremde Dateien aus dem Explorer sind nicht dieser Zug
    assert.equal(ownDrag(ev(['C:\neu.mp3'])), null)
    const own = ownDrag(ev(['M:\Musik\b.mp3', 'M:\Musik\a.mp3']))
    assert.equal(own.playlist, 'M:\pl\Set.m3u')
    assert.deepEqual(own.tracks, [])                    // auf eine andere Playlist gelegt: keine Titel anhaengen
    assert.equal(draggingPlaylist(), null)
    // leere Playlist: kein Datei-Zug — das HTML-Ziehen (in einen Ordner) bleibt
    const e2 = startEv()
    assert.equal(startPlaylistDrag(e2, 'M:\pl\Leer.m3u'), false)
    assert.equal(e2.prevented, false)
  } finally { window.electron = keep }
  // ohne Electron: HTML-Ziehen
  const k2 = window.electron
  window.electron = undefined
  try { assert.equal(startPlaylistDrag(startEv(), 'M:\pl\Set.m3u'), false) } finally { window.electron = k2 }
})
