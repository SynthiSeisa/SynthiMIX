// Electron fuer die Oberflaechen-Tests: laedt nur die Test-Oberflaeche,
// startet kein Backend (das kommt aus global-setup.mjs), Ton stumm, eigener
// leerer Profilordner (keine Einstellungen aus frueheren Laeufen).
const { app, BrowserWindow } = require('electron')
const fs = require('fs')
const os = require('os')
const path = require('path')

app.setPath('userData', fs.mkdtempSync(path.join(os.tmpdir(), 'synthimix-e2e-ui-')))

app.whenReady().then(() => {
  const win = new BrowserWindow({
    width: 1280, height: 800, show: false,
    // wie in der echten App: lokale Audiodateien duerfen geladen werden
    webPreferences: { webSecurity: false, backgroundThrottling: false, autoplayPolicy: 'no-user-gesture-required' },
  })
  win.webContents.setAudioMuted(true)
  win.loadURL(process.env.E2E_URL)
})
