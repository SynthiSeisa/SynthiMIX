const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electron', {
  minimize:   () => ipcRenderer.send('win-minimize'),
  maximize:   () => ipcRenderer.send('win-maximize'),
  close:      () => ipcRenderer.send('win-close'),
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
})
