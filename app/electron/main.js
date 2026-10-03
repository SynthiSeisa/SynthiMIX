const { app, BrowserWindow, ipcMain, dialog, shell, globalShortcut, nativeImage, nativeTheme } = require('electron')
const path = require('path')
const { spawn } = require('child_process')
const { autoUpdater } = require('electron-updater')
const fs = require('fs')
const portable = require('./portable.cjs')

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged

// Tragbarer Betrieb (Programm auf einer Festplatte, die von PC zu PC wandert):
// alle Daten neben dem Programm, siehe portable.cjs. Muss vor allem anderen
// stehen — auch die Ein-Instanz-Sperre liegt im Datenordner.
let portableData = null
if (app.isPackaged) {
  try {
    const dir = portable.portableDir(process.execPath, process.env.SystemDrive)
    if (dir) {
      fs.mkdirSync(dir, { recursive: true })
      fs.accessSync(dir, fs.constants.W_OK)
      if (portable.migrate(app.getPath('userData'), dir, fs)) console.log('[tragbar] Daten mitgenommen nach', dir)
      app.setPath('userData', dir)
      portableData = dir
    }
  } catch (e) {
    console.error('[tragbar] nicht moeglich, Daten bleiben auf diesem PC:', e.message)
  }
}

let mainWindow = null

// Vorschaubild in der Taskleiste: Chromium hoert auf zu zeichnen, sobald es
// das Fenster fuer verdeckt haelt — beim Hovern zeigte Windows dann kein
// Live-Bild. Die Verdeckt-Erkennung aus, Hintergrund nicht drosseln (Player).
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion')
let pythonProcess = null
let quitting = false          // beim Beenden das Backend nicht neu starten
let backendRestarts = []      // Zeitpunkte der letzten Neustarts

// Nur eine SynthiMIX-Instanz. Ein zweiter Start haengte sich sonst an das
// Backend der ersten — zwei Fenster spielten dieselbe Warteschlange ab und
// schickten beide beim Crossfade "naechster Titel".
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  })
}

function startPythonBackend() {
  let cmd, args, cwd
  if (app.isPackaged) {
    // Packaged: use the bundled backend.exe from resources
    const backendDir = path.join(process.resourcesPath, 'backend')
    const dataDir    = app.getPath('userData')
    cmd  = path.join(backendDir, 'backend.exe')
    args = ['--data-dir', dataDir, '--electron-exe', process.execPath]
    if (portableData) args.push('--portable')
    cwd  = backendDir
  } else {
    cmd  = 'python'
    args = [path.join(__dirname, '../../backend/main.py'), '--electron-exe', process.execPath]
    cwd  = path.join(__dirname, '../../backend')
  }
  pythonProcess = spawn(cmd, args, { cwd, stdio: ['ignore', 'pipe', 'pipe'] })
  pythonProcess.stdout.on('data', d => console.log('[python]', d.toString().trim()))
  pythonProcess.stderr.on('data', d => console.error('[python]', d.toString().trim()))
  pythonProcess.on('exit', code => {
    console.log('[python] exited', code)
    pythonProcess = null
    if (quitting) return
    // Stirbt das Backend mitten im Auflegen, lief die Musik zwar weiter, aber
    // ohne Warteschlange, Downloads und Crossfade-Steuerung. Neu starten —
    // das Frontend verbindet sich von selbst wieder. Hoechstens 5x pro Minute,
    // damit ein dauerhaft kaputtes Backend nicht in einer Schleife haengt.
    const now = Date.now()
    backendRestarts = backendRestarts.filter(t => now - t < 60000)
    if (backendRestarts.length >= 5) {
      console.error('[python] startet immer wieder neu, gebe auf')
      return
    }
    backendRestarts.push(now)
    setTimeout(() => { if (!quitting) startPythonBackend() }, 1000)
  })
}

function createSplash() {
  const splash = new BrowserWindow({
    width: 340,
    height: 300,
    frame: false,
    transparent: false,
    backgroundColor: '#060a10',
    resizable: false,
    center: true,
    alwaysOnTop: true,
    webPreferences: { contextIsolation: true }
  })
  splash.loadFile(path.join(__dirname, 'splash.html'))
  splash.webContents.on('did-finish-load', () => {
    splash.webContents.executeJavaScript(
      `document.querySelector('.ver').textContent = 'v${app.getVersion()}'`
    )
  })
  return splash
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    frame: false,
    backgroundColor: '#080c14',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      webSecurity: false,
      backgroundThrottling: false
    }
  })
  mainWindow.on('show', () => updateThumbar())
  // Eine neben ein Ziel fallengelassene Datei darf die App nicht ersetzen
  // (Chromium oeffnet sie sonst im Fenster). Die App selbst navigiert nie.
  mainWindow.webContents.on('will-navigate', (e) => e.preventDefault())

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools({ mode: 'detach' })
    mainWindow.webContents.on('console-message', (e, level, msg) => {
      if (msg.includes('Autofill')) return
      if (level >= 3) console.error('[renderer]', msg)
    })
    mainWindow.once('ready-to-show', () => {
      mainWindow.setFullScreen(true)
      mainWindow.show()
    })
  } else {
    const splash = createSplash()
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
    mainWindow.once('ready-to-show', () => {
      setTimeout(() => {
        splash.destroy()
        mainWindow.setFullScreen(true)
        mainWindow.show()
      }, 2600)
    })
  }
}

// ── Knoepfe im Vorschaubild der Taskleiste: Zurueck, Pause/Play, Weiter ─────
// Symbole werden hier gezeichnet (32 px, 4-fach abgetastet) — keine Bilddateien.
let thumbPlaying = false
function glyph(kind, dark, rgb = null) {
  const S = 32, SS = 4, buf = Buffer.alloc(S * S * 4)
  const tri = (ax, ay, bx, by, cx, cy) => (x, y) => {
    const d1 = (x - bx) * (ay - by) - (ax - bx) * (y - by)
    const d2 = (x - cx) * (by - cy) - (bx - cx) * (y - cy)
    const d3 = (x - ax) * (cy - ay) - (cx - ax) * (y - ay)
    return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0))
  }
  const rect = (x0, y0, x1, y1) => (x, y) => x >= x0 && x <= x1 && y >= y0 && y <= y1
  const ell = (cx, cy, rx, ry) => (x, y) => ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1
  const shapes = {
    note:  [ell(12, 23, 6, 4.5), rect(16, 5, 18.5, 23), tri(18.5, 5, 26, 10, 18.5, 13)],
    play:  [tri(10, 7, 10, 25, 25, 16)],
    pause: [rect(9, 8, 13.5, 24), rect(18.5, 8, 23, 24)],
    next:  [tri(7, 8, 7, 24, 19, 16), rect(20.5, 8, 24, 24)],
    prev:  [tri(25, 8, 25, 24, 13, 16), rect(8, 8, 11.5, 24)],
  }[kind]
  const [r, g, b] = rgb || (dark ? [255, 255, 255] : [30, 30, 30])
  for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
    let hit = 0
    for (let sy = 0; sy < SS; sy++) for (let sx = 0; sx < SS; sx++) {
      const px = x + (sx + 0.5) / SS, py = y + (sy + 0.5) / SS
      if (shapes.some(f => f(px, py))) hit++
    }
    const a = Math.round(255 * hit / (SS * SS)), i = (y * S + x) * 4
    // BGRA, vormultipliziert
    buf[i] = Math.round(b * a / 255); buf[i + 1] = Math.round(g * a / 255); buf[i + 2] = Math.round(r * a / 255); buf[i + 3] = a
  }
  return nativeImage.createFromBitmap(buf, { width: S, height: S, scaleFactor: 2 })
}
function updateThumbar() {
  if (process.platform !== 'win32' || !mainWindow || mainWindow.isDestroyed()) return
  const dark = nativeTheme.shouldUseDarkColors
  const send = (key) => mainWindow?.webContents.send('media-key', key)
  try {
    mainWindow.setThumbarButtons([
      { tooltip: 'Vorheriger Titel', icon: glyph('prev', dark), click: () => send('prev') },
      { tooltip: thumbPlaying ? 'Pause' : 'Abspielen', icon: glyph(thumbPlaying ? 'pause' : 'play', dark),
        click: () => send('play_pause') },
      { tooltip: 'Nächster Titel', icon: glyph('next', dark), click: () => send('next') },
    ])
  } catch (_) {}
}
// ── Titel als Dateien herausziehen (FL Studio, Explorer, rekordbox …) ─────────
let dragIcon = null
ipcMain.on('start-drag', (e, paths) => {
  const fs = require('fs')
  const files = (Array.isArray(paths) ? paths : []).filter(p => typeof p === 'string' && fs.existsSync(p))
  if (!files.length) return
  dragIcon = dragIcon || glyph('note', true, [255, 154, 51])
  try { e.sender.startDrag({ file: files[0], files, icon: dragIcon }) } catch (_) {}
})

ipcMain.on('player-playing', (_, playing) => {
  if (thumbPlaying === !!playing) return
  thumbPlaying = !!playing
  updateThumbar()
})
nativeTheme.on('updated', () => updateThumbar())

function registerMediaKeys() {
  const send = (key) => mainWindow?.webContents.send('media-key', key)
  ;[
    ['MediaPlayPause',    'play_pause'],
    ['MediaNextTrack',    'next'],
    ['MediaPreviousTrack','prev'],
    ['MediaStop',         'stop'],
  ].forEach(([accel, key]) => {
    try { globalShortcut.register(accel, () => send(key)) } catch (_) {}
  })
}

// Release-Notes kommen vom GitHub-Feed als HTML — fuer das Update-Fenster in
// schlichten Text wandeln (Ueberschriften, Aufzaehlungen, Absaetze bleiben)
function notesToText(n) {
  if (!n) return ''
  if (Array.isArray(n)) n = n.map(x => x.note || '').join('\n')
  // Zeilenumbrueche im Quelltext sind nur Umbruch der Markdown-Datei → Leerzeichen
  return String(n)
    .replace(/\r?\n/g, ' ').replace(/<br\s*\/?>/gi, ' ')
    .replace(/<h\d[^>]*>/gi, '\n\n').replace(/<\/h\d>/gi, '\n')
    .replace(/<li[^>]*>/gi, '\n• ').replace(/<p[^>]*>/gi, '\n\n').replace(/<\/(p|ul|ol)>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/[ \t]{2,}/g, ' ').replace(/[ \t]*\n[ \t]*/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}

function setupAutoUpdater() {
  // Tragbar: das Update gehoert auf die Festplatte, nicht in den Standardordner
  // des PCs, an dem sie gerade haengt (dort ist nichts installiert)
  if (portableData) autoUpdater.installDirectory = path.dirname(process.execPath)
  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = true

  autoUpdater.on('update-available', (info) => {
    const size = info.files?.[0]?.size ?? null
    mainWindow?.webContents.send('update-available', info.version, size, notesToText(info.releaseNotes))
  })
  autoUpdater.on('download-progress', (p) => {
    mainWindow?.webContents.send('update-progress', Math.round(p.percent))
  })
  autoUpdater.on('update-downloaded', () => {
    mainWindow?.webContents.send('update-downloaded')
  })
  autoUpdater.on('error', (err) => {
    console.log('[updater] Fehler:', err.message)
    mainWindow?.webContents.send('update-error', err.message)
  })
}

app.whenReady().then(() => {
  if (!gotLock) return
  if (!process.env.YTDL_DEV) startPythonBackend()
  setTimeout(() => {
    createWindow()
    registerMediaKeys()
    if (app.isPackaged) {
      setupAutoUpdater()
      // Update-Check 10 Sekunden nach Start (Backend muss erst hochfahren),
      // danach alle 6 Stunden — die App laeuft beim Auflegen oft die ganze Nacht
      setTimeout(() => autoUpdater.checkForUpdates().catch(() => {}), 10000)
      setInterval(() => autoUpdater.checkForUpdates().catch(() => {}), 6 * 3600 * 1000)
    }
  }, process.env.YTDL_DEV ? 0 : 1200)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('before-quit', () => { quitting = true })
app.on('will-quit', () => globalShortcut.unregisterAll())

app.on('window-all-closed', () => {
  quitting = true
  if (pythonProcess) { try { pythonProcess.kill() } catch (e) {} }
  if (process.platform !== 'darwin') app.quit()
})

// Update herunterladen / installieren
ipcMain.on('download-update', () => autoUpdater.downloadUpdate().catch(() => {}))
ipcMain.on('install-update',  () => autoUpdater.quitAndInstall())
// "Nach Updates suchen" (Einstellungen → Info). Ist eins da, meldet sich
// zusaetzlich das Update-Fenster wie beim automatischen Check.
ipcMain.handle('check-update', async () => {
  if (!app.isPackaged) return { status: 'dev' }
  try {
    const r = await autoUpdater.checkForUpdates()
    const v = r?.updateInfo?.version
    const avail = r?.isUpdateAvailable ?? (!!v && v !== app.getVersion())
    return avail ? { status: 'available', version: v } : { status: 'none', version: app.getVersion() }
  } catch (e) {
    return { status: 'error', message: String(e?.message ?? e).slice(0, 200) }
  }
})

// Window controls
ipcMain.on('win-minimize', () => mainWindow?.minimize())
ipcMain.on('win-maximize', () => {
  if (mainWindow) mainWindow.setFullScreen(!mainWindow.isFullScreen())
})
ipcMain.on('win-fullscreen', () => {
  if (mainWindow) mainWindow.setFullScreen(!mainWindow.isFullScreen())
})
ipcMain.on('win-close', () => mainWindow?.close())

// Datei im Explorer zeigen bzw. URL im Browser oeffnen
ipcMain.handle('open-path', (e, target) => {
  const fs = require('fs')
  if (!target) return
  // Die Remote-Adresse kommt auch hier an. Als Datei gesucht fand sie sich nie,
  // und "Im Browser oeffnen" tat deshalb schlicht nichts.
  if (/^https?:\/\//i.test(target)) return shell.openExternal(target)
  target = path.win32.normalize(String(target))
  let st = null
  try { st = fs.statSync(target) } catch {}
  if (st?.isDirectory()) return shell.openPath(target)
  if (st) {
    // explorer.exe /select: markiert die Datei und kommt zuverlaessig nach
    // vorne; showItemInFolder als Rueckfall
    if (process.platform === 'win32') {
      try {
        require('child_process').spawn('explorer.exe', ['/select,', target], { detached: true, stdio: 'ignore' }).unref()
        return
      } catch {}
    }
    return shell.showItemInFolder(target)
  }
  // Datei verschoben oder geloescht: wenigstens den Ordner zeigen, in dem sie
  // lag. Der fruehere Rueckfall auf ../../Downloads existiert im Installer nicht.
  const dir = path.dirname(target)
  if (fs.existsSync(dir)) return shell.openPath(dir)
})

// Folder picker
ipcMain.handle('pick-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: 'Musikordner auswählen'
  })
  return result.canceled ? null : result.filePaths[0]
})

// List directory contents for the filesystem browser (null → list drives)
ipcMain.handle('list-dir', async (e, dirPath) => {
  const fs    = require('fs')
  const AUDIO = /\.(mp3|flac|wav|m4a|ogg|aac|opus|wma)$/i
  // Zwischendateien abgebrochener Downloads ("Titel.temp.mp3") sind keine Titel
  const TEMP  = /\.(temp|__tmp|norm_tmp)\.[a-z0-9]{2,5}$/i
  try {
    if (!dirPath) {
      const drives = []
      for (const letter of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')) {
        const p = letter + ':\\'
        try { if (fs.existsSync(p)) drives.push({ name: p, path: p, isDir: true }) } catch {}
      }
      return drives
    }
    // asynchron: ein langsames Netz- oder USB-Laufwerk haelt sonst das ganze Fenster an
    const entries = await fs.promises.readdir(dirPath, { withFileTypes: true })
    return entries
      .filter(e => e.isDirectory() || (AUDIO.test(e.name) && !TEMP.test(e.name)))
      .map(e => ({ name: e.name, path: path.join(dirPath, e.name), isDir: e.isDirectory() }))
      .sort((a, b) => {
        if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
        return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
      })
  } catch {
    return []
  }
})
