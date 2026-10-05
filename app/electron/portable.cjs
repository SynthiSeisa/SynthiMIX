// Tragbarer Betrieb: liegt das Programm nicht auf dem Systemlaufwerk (externe
// Festplatte, die von PC zu PC wandert), speichert SynthiMIX alles — Bibliothek,
// Einstellungen, Playlists, Werkzeuge — im Ordner "SynthiMIX-Daten" NEBEN dem
// Programmordner. Nicht darin: der Installer ersetzt bei einem Update den
// ganzen Programmordner.
const path = require('path')

const DATA_NAME = 'SynthiMIX-Daten'
// Zwischenspeicher von Chromium und Sperrdateien: nicht mitnehmen
const SKIP = new Set(['Cache', 'Code Cache', 'GPUCache', 'DawnGraphiteCache', 'DawnWebGPUCache', 'DawnCache', 'blob_storage',
                      'Shared Dictionary', 'SharedStorage', 'SharedStorage-wal', 'DIPS', 'DIPS-shm', 'DIPS-wal', 'lockfile',
                      'Network', 'Session Storage', 'Downloads', 'Crashpad', 'logs'])
const DONE = 'settings.json'          // liegt sie im Ziel, ist der Umzug vollstaendig
// Aktualisierte Werkzeuge (ffmpeg, yt-dlp; ueber 300 MB): erst nach dem Start im
// Hintergrund — vorher stand das Programm beim ersten tragbaren Start eine halbe
// Minute ohne Fenster da. Bis dahin laufen die mitgelieferten Werkzeuge.
const LATER = 'tools'

/** Datenordner fuer den tragbaren Betrieb oder null (Programm auf dem Systemlaufwerk). */
function portableDir(execPath, systemDrive = 'C:', p = path.win32) {
  const exeDir = p.dirname(execPath)
  const drive = p.parse(exeDir).root.slice(0, 2).toUpperCase()
  if (!/^[A-Z]:$/.test(drive) || drive === String(systemDrive || 'C:').slice(0, 2).toUpperCase()) return null
  return p.join(p.dirname(exeDir), DATA_NAME)
}

/**
 * Beim ersten tragbaren Start die bisherigen Daten dieses PCs mitnehmen
 * (kopieren — das Original bleibt). settings.json zuletzt: bricht das Kopieren
 * ab, wird es beim naechsten Start wiederholt. Liefert true, wenn kopiert wurde.
 */
function migrate(oldDir, newDir, fs) {
  if (fs.existsSync(path.join(newDir, DONE)) || !fs.existsSync(path.join(oldDir, DONE))) return false
  for (const name of fs.readdirSync(oldDir)) {
    if (SKIP.has(name) || name === DONE || name === LATER || name.startsWith('Sicherung')) continue
    fs.cpSync(path.join(oldDir, name), path.join(newDir, name), { recursive: true, force: true })
  }
  fs.cpSync(path.join(oldDir, DONE), path.join(newDir, DONE))
  return true
}

/**
 * Die Werkzeuge nachholen: erst in einen Zwischenordner, dann umbenennen —
 * eine halb kopierte exe darf nie unter ihrem echten Namen liegen.
 * fsp: fs.promises. Liefert true, wenn kopiert wurde.
 */
async function migrateTools(oldDir, newDir, fsp) {
  const from = path.join(oldDir, LATER), to = path.join(newDir, LATER), tmp = to + '.kopie'
  const exists = (p) => fsp.access(p).then(() => true, () => false)
  if (!(await exists(from)) || (await exists(to))) return false
  await fsp.rm(tmp, { recursive: true, force: true })
  await fsp.cp(from, tmp, { recursive: true })
  await fsp.rename(tmp, to)
  return true
}

// Liegt diese Datei im tragbaren Datenordner, werden beim naechsten Start die
// Daten des PCs uebernommen (Einstellungen → System → Speicherort)
const TAKE_MARK = 'uebernehmen.merker'

/**
 * Die Daten des PCs auf die Platte holen, auch wenn dort schon welche liegen
 * (z. B. weil das Programm zuerst an einem fremden PC gestartet wurde): was
 * auf der Platte liegt, wandert vorher in "Sicherung <stamp>". Nur beim Start,
 * bevor irgendetwas die Dateien offen hat. Liefert true, wenn uebernommen wurde.
 */
function takeOver(oldDir, newDir, fs, stamp) {
  if (!fs.existsSync(path.join(oldDir, DONE))) return false
  const keep = path.join(newDir, `Sicherung ${stamp}`)
  fs.mkdirSync(keep, { recursive: true })
  for (const name of fs.readdirSync(newDir)) {
    if (SKIP.has(name) || name === LATER || name === TAKE_MARK || name.startsWith('Sicherung')) continue
    fs.renameSync(path.join(newDir, name), path.join(keep, name))
  }
  return migrate(oldDir, newDir, fs)
}

/** Zahl der Titel in der Bibliothek eines Datenordners (null: keine/unlesbar). */
function countTracks(dir, fs) {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(dir, 'library_cache.json'), 'utf-8'))
    const list = Array.isArray(d) ? d : (d.tracks || d.library || [])
    return list.length
  } catch { return null }
}

module.exports = { portableDir, migrate, migrateTools, takeOver, countTracks, DATA_NAME, SKIP, TAKE_MARK }
