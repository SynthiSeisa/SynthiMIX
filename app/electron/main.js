const { app, BrowserWindow, ipcMain, dialog, shell, globalShortcut } = require('electron')
const path = require('path')
const { spawn } = require('child_process')
const { autoUpdater } = require('electron-updater')

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged

let mainWindow = null
let pythonProcess = null

function startPythonBackend() {
  let cmd, args, cwd
  if (app.isPackaged) {
    // Packaged: use the bundled backend.exe from resources
    const backendDir = path.join(process.resourcesPath, 'backend')
    const dataDir    = app.getPath('userData')
    cmd  = path.join(backendDir, 'backend.exe')
    args = ['--data-dir', dataDir]
    cwd  = backendDir
  } else {
    cmd  = 'python'
    args = [path.join(__dirname, '../../backend/main.py')]
    cwd  = path.join(__dirname, '../../backend')
  }
  pythonProcess = spawn(cmd, args, { cwd, stdio: ['ignore', 'pipe', 'pipe'] })
  pythonProcess.stdout.on('data', d => console.log('[python]', d.toString().trim()))
  pythonProcess.stderr.on('data', d => console.error('[python]', d.toString().trim()))
  pythonProcess.on('exit', code => console.log('[python] exited', code))
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
      webSecurity: false
    }
  })

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

function setupAutoUpdater() {
  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true

  autoUpdater.on('update-available', (info) => {
    mainWindow?.webContents.send('update-available', info.version)
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
  if (!process.env.YTDL_DEV) startPythonBackend()
  setTimeout(() => {
    createWindow()
    registerMediaKeys()
    if (app.isPackaged) {
      setupAutoUpdater()
      // Update-Check 10 Sekunden nach Start (Backend muss erst hochfahren)
      setTimeout(() => autoUpdater.checkForUpdates().catch(() => {}), 10000)
    }
  }, process.env.YTDL_DEV ? 0 : 1200)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('will-quit', () => globalShortcut.unregisterAll())

app.on('window-all-closed', () => {
  if (pythonProcess) { try { pythonProcess.kill() } catch (e) {} }
  if (process.platform !== 'darwin') app.quit()
})

// Update installieren
ipcMain.on('install-update', () => autoUpdater.quitAndInstall())

// Window controls
ipcMain.on('win-minimize', () => mainWindow?.minimize())
ipcMain.on('win-maximize', () => {
  if (mainWindow) mainWindow.setFullScreen(!mainWindow.isFullScreen())
})
ipcMain.on('win-fullscreen', () => {
  if (mainWindow) mainWindow.setFullScreen(!mainWindow.isFullScreen())
})
ipcMain.on('win-close', () => mainWindow?.close())

// Open file/folder in Explorer
ipcMain.handle('open-path', (e, filePath) => {
  const fs = require('fs')
  if (filePath && fs.existsSync(filePath)) {
    shell.showItemInFolder(filePath)
  } else {
    shell.openPath(path.join(__dirname, '../../Downloads'))
  }
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
  try {
    if (!dirPath) {
      const drives = []
      for (const letter of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')) {
        const p = letter + ':\\'
        try { if (fs.existsSync(p)) drives.push({ name: p, path: p, isDir: true }) } catch {}
      }
      return drives
    }
    const entries = fs.readdirSync(dirPath, { withFileTypes: true })
    return entries
      .filter(e => e.isDirectory() || AUDIO.test(e.name))
      .map(e => ({ name: e.name, path: path.join(dirPath, e.name), isDir: e.isDirectory() }))
      .sort((a, b) => {
        if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
        return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
      })
  } catch {
    return []
  }
})
