const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electron', {
  minimize:   () => ipcRenderer.send('win-minimize'),
  maximize:   () => ipcRenderer.send('win-maximize'),
  close:      () => ipcRenderer.send('win-close'),
  pickFolder: () => ipcRenderer.invoke('pick-folder'),

  // Auto-updater
  onUpdateAvailable: (cb) => ipcRenderer.on('update-available',  (_e, v, size) => cb(v, size)),
  onUpdateProgress:  (cb) => ipcRenderer.on('update-progress',   (_e, p)       => cb(p)),
  onUpdateDownloaded:(cb) => ipcRenderer.on('update-downloaded',  ()            => cb()),
  onUpdateError:     (cb) => ipcRenderer.on('update-error',      (_e, msg)     => cb(msg)),
  downloadUpdate:    ()   => ipcRenderer.send('download-update'),
  installUpdate:     ()   => ipcRenderer.send('install-update'),
  toggleFullscreen:  ()   => ipcRenderer.send('win-toggle-fullscreen'),
})
