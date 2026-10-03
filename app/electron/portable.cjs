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
    if (SKIP.has(name) || name === DONE || name.startsWith('Sicherung')) continue
    fs.cpSync(path.join(oldDir, name), path.join(newDir, name), { recursive: true, force: true })
  }
  fs.cpSync(path.join(oldDir, DONE), path.join(newDir, DONE))
  return true
}

module.exports = { portableDir, migrate, DATA_NAME, SKIP }
