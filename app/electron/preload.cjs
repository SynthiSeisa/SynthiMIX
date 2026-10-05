const { contextBridge, ipcRenderer, webUtils } = require('electron')

contextBridge.exposeInMainWorld('electron', {
  minimize:         () => ipcRenderer.send('win-minimize'),
  maximize:         () => ipcRenderer.send('win-maximize'),
  close:            () => ipcRenderer.send('win-close'),
  toggleFullscreen: () => ipcRenderer.send('win-fullscreen'),
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  pickFile:   (opts) => ipcRenderer.invoke('pick-file', opts ?? null),
  // Dienst (Backend): Zustand fuer Meldungen, neu starten; Programm neu starten
  backendStatus:  () => ipcRenderer.invoke('backend-status'),
  backendRestart: () => ipcRenderer.invoke('backend-restart'),
  relaunch:       () => ipcRenderer.invoke('relaunch'),
  // Tragbarer Betrieb: Daten dieses PCs auf die Platte uebernehmen
  portableInfo:     () => ipcRenderer.invoke('portable-info'),
  portableTakeover: () => ipcRenderer.invoke('portable-takeover'),
  openPath:   (p) => ipcRenderer.invoke('open-path', p),
  listDir:    (p) => ipcRenderer.invoke('list-dir', p ?? null),
  onMediaKey:        (cb) => ipcRenderer.on('media-key',        (_, key)     => cb(key)),
  setPlaying:        (playing) => ipcRenderer.send('player-playing', !!playing),
  // Titel als echte Dateien herausziehen (src/lib/fileDrag.js)
  startDrag:         (paths) => ipcRenderer.send('start-drag', paths),
  pathForFile:       (file) => { try { return webUtils.getPathForFile(file) } catch { return '' } },
  onUpdateAvailable: (cb) => ipcRenderer.on('update-available',  (_, version, size, notes) => cb(version, size, notes)),
  onUpdateProgress:  (cb) => ipcRenderer.on('update-progress',   (_, pct)           => cb(pct)),
  onUpdateDownloaded:(cb) => ipcRenderer.on('update-downloaded', ()                 => cb()),
  onUpdateError:     (cb) => ipcRenderer.on('update-error',      (_, msg)           => cb(msg)),
  downloadUpdate:    ()   => ipcRenderer.send('download-update'),
  installUpdate:     ()   => ipcRenderer.send('install-update'),
  checkUpdate:       ()   => ipcRenderer.invoke('check-update'),
})
