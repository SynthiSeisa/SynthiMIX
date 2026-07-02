const { app, BrowserWindow, ipcMain, shell } = require('electron')
const path = require('path')
const { spawn } = require('child_process')

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
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      webSecurity: false  // allow file:// audio src
    }
  })

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }
}

app.whenReady().then(() => {
  startPythonBackend()
  // Give backend a moment to start
  setTimeout(createWindow, 800)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (pythonProcess) pythonProcess.kill()
  if (process.platform !== 'darwin') app.quit()
})

// Window controls (frameless)
ipcMain.on('win-minimize', () => mainWindow?.minimize())
ipcMain.on('win-maximize', () => {
  if (mainWindow?.isMaximized()) mainWindow.unmaximize()
  else mainWindow?.maximize()
})
ipcMain.on('win-close', () => mainWindow?.close())
