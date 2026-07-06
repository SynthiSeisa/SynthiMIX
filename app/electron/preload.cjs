const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electron', {
  minimize:         () => ipcRenderer.send('win-minimize'),
  maximize:         () => ipcRenderer.send('win-maximize'),
  close:            () => ipcRenderer.send('win-close'),
  toggleFullscreen: () => ipcRenderer.send('win-fullscreen'),
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  openPath:   (p) => ipcRenderer.invoke('open-path', p),
  listDir:    (p) => ipcRenderer.invoke('list-dir', p ?? null),
  onMediaKey:        (cb) => ipcRenderer.on('media-key',        (_, key)     => cb(key)),
  onUpdateAvailable: (cb) => ipcRenderer.on('update-available',  (_, version) => cb(version)),
  onUpdateProgress:  (cb) => ipcRenderer.on('update-progress',   (_, pct)     => cb(pct)),
  onUpdateDownloaded:(cb) => ipcRenderer.on('update-downloaded', ()           => cb()),
  onUpdateError:     (cb) => ipcRenderer.on('update-error',     (_, msg)      => cb(msg)),
  installUpdate:     ()   => ipcRenderer.send('install-update'),
})
