const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electron', {
  minimize:         () => ipcRenderer.send('win-minimize'),
  maximize:         () => ipcRenderer.send('win-maximize'),
  close:            () => ipcRenderer.send('win-close'),
  toggleFullscreen: () => ipcRenderer.send('win-fullscreen'),
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  openPath:   (p) => ipcRenderer.invoke('open-path', p),
  listDir:    (p) => ipcRenderer.invoke('list-dir', p ?? null),
  onMediaKey: (cb) => ipcRenderer.on('media-key', (_, key) => cb(key)),
})
