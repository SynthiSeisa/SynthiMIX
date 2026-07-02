import { app, BrowserWindow, ipcMain } from 'electron'
import { createRequire } from 'module'
import { fileURLToPath } from 'url'
import path from 'path'

const require = createRequire(import.meta.url)
const { spawn } = require('child_process')
const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged

let mainWindow = null
let pythonProcess = null

function startPythonBackend() {
  const scriptPath = path.join(__dirname, '../../backend/main.py')
  pythonProcess = spawn('python', [scriptPath], {
    cwd: path.join(__dirname, '../../backend'),
    stdio: ['ignore', 'pipe', 'pipe']
  })
  pythonProcess.stdout.on('data', d => console.log('[python]', d.toString().trim()))
  pythonProcess.stderr.on('data', d => console.error('[python]', d.toString().trim()))
  pythonProcess.on('exit', code => console.log('[python] exited', code))
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    frame: false,
    backgroundColor: '#080c14',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      webSecurity: false
    }
  })

  const port = process.env.VITE_PORT || '5173'
  if (isDev) {
    mainWindow.loadURL(`http://localhost:${port}`)
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }
}

app.whenReady().then(() => {
  startPythonBackend()
  setTimeout(createWindow, 800)
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (pythonProcess) pythonProcess.kill()
  if (process.platform !== 'darwin') app.quit()
})

ipcMain.on('win-minimize', () => mainWindow?.minimize())
ipcMain.on('win-maximize', () => {
  if (mainWindow?.isMaximized()) mainWindow.unmaximize()
  else mainWindow?.maximize()
})
ipcMain.on('win-close', () => mainWindow?.close())
