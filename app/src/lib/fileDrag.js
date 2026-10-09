// Titel als echte Dateien aus SynthiMIX herausziehen — nach FL Studio, in den
// Explorer, nach rekordbox. Dafuer startet Electron einen Windows-Datei-Zug
// (webContents.startDrag), der das HTML-Ziehen ersetzt. Was gezogen wird,
// merkt sich die App hier: Warteschlange und Playlists erkennen ihren eigenen
// Zug beim Ablegen an den Dateipfaden wieder. Ohne Electron (Browser-Vorschau,
// Tests) bleibt alles beim HTML-Ziehen.

let current = null          // { paths, tracks, queueFrom? }

const norm = (p) => (p || '').replace(/\//g, '\\').toLowerCase()
const same = (a, b) => a.length === b.length && [...a].map(norm).sort().join('\n') === [...b].map(norm).sort().join('\n')

/** Datei-Zug starten. false: kein Electron — dann normal per HTML ziehen. */
export function startFileDrag(e, tracks, extra = {}) {
  const api = typeof window !== 'undefined' ? window.electron : null
  const paths = tracks.map((t) => t?.path).filter(Boolean)
  if (!api?.startDrag || !paths.length) return false
  e.preventDefault()
  current = { paths, tracks, ...extra }
  api.startDrag(paths)
  return true
}

/**
 * Eine ganze Playlist als Dateien ziehen (alle ihre Titel) — auf eine Playlist
 * in rekordbox, in den Explorer. false: kein Electron oder die Playlist ist
 * leer — dann normal per HTML ziehen (das Verschieben in einen Ordner geht so).
 */
export function startPlaylistDrag(e, plPath) {
  const api = typeof window !== 'undefined' ? window.electron : null
  if (!api?.startDragPlaylist || !plPath) return false
  const paths = api.startDragPlaylist(plPath)
  if (!Array.isArray(paths) || !paths.length) return false
  e.preventDefault()
  current = { paths, tracks: [], playlist: plPath }
  return true
}

/** Wird gerade eine eigene Playlist gezogen? (zum Hervorheben der Ziele) */
export const draggingPlaylist = () => current?.playlist ?? null

/** Werden Dateien gezogen (eigener Zug oder aus dem Explorer)? */
export const hasFiles = (e) => !!e.dataTransfer?.types?.includes('Files')

/** Pfade der abgelegten Dateien. */
export function droppedPaths(e) {
  const pf = typeof window !== 'undefined' ? window.electron?.pathForFile : null
  return [...(e.dataTransfer?.files ?? [])].map((f) => (pf ? pf(f) : '') || '').filter(Boolean)
}

/** Beim Ablegen: der eigene Zug (dieselben Dateien) oder null. */
export function ownDrag(e) {
  if (!current || !same(droppedPaths(e), current.paths)) return null
  const c = current
  current = null
  return c
}

/** Nur fuer Tests */
export function _setCurrent(c) { current = c }

if (typeof window !== 'undefined') {
  // Ein neuer Klick beendet einen alten Zug (z. B. ausserhalb abgelegt)
  window.addEventListener('pointerdown', () => { current = null }, true)
  // Dateien neben einem Ziel: nichts tun (Zeiger "nicht erlaubt") statt sie im
  // Fenster zu oeffnen. Ziele (Warteschlange, Playlists) behandeln sie vorher.
  window.addEventListener('dragover', (e) => {
    if (hasFiles(e) && !e.defaultPrevented) { e.preventDefault(); e.dataTransfer.dropEffect = 'none' }
  })
  window.addEventListener('drop', (e) => { if (hasFiles(e) && !e.defaultPrevented) e.preventDefault() })
}
