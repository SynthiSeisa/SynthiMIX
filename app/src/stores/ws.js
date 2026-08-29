import { writable } from 'svelte/store'

export const connected   = writable(false)
// Paths that should have intro skipped on next playback (client-side one-shot flags)
export const introSkipPaths = writable(new Set())
export const queue       = writable([])
export const playerState = writable({ playing: false, position_ms: 0, duration_ms: 0, current_idx: -1 })
export const nowPlaying  = writable(null)
export const downloads   = writable([])
export const library     = writable([])
export const scanStatus  = writable('')
export const waveform       = writable([])
export const waveformNext   = writable([])
export const searchResults  = writable(null)   // null = panel closed
export const settings       = writable({ volume: 80, crossfade_s: 8 })
export const playlists      = writable([])
export const downloadTree       = writable({ folders: [], files: [] })
export const downloadTreeLoaded = writable(false)
export const playMode       = writable({ shuffle: false, repeat: 0 })
export const settingsOpen  = writable(false)
export const settingsTab   = writable(null)   // Tab, der beim Oeffnen gezeigt wird

/** Einstellungen oeffnen, wahlweise direkt auf einem bestimmten Tab. */
export function openSettings(tabId = null) {
  if (tabId) settingsTab.set(tabId)
  settingsOpen.set(true)
}
export const playlistFolderEnabled = writable(true)
export const dlFilenameFormat      = writable('title')
export const downloadDir           = writable('')
export const autoScanIntervalMin   = writable(0)
export const favorites             = writable([])
export const automixStatus = writable('')
export const autoMixEnabled = writable(true)
export const normalizeProgress = writable(null)
export const dlHistory = writable([])
export const toolsInfo = writable({ ytdlp_version: null, ffmpeg_version: null, spotdl_version: null })
export const spotifyClientId     = writable('')
export const spotifyClientSecret = writable('')
export const lastfmApiKey        = writable('')
export const acoustidApiKey      = writable('')
export const radioEnabled        = writable(false)
export const radioStatus         = writable(null)   // null | { title, similar_to }
export const trackIdentified     = writable(null)   // null | { title, artist, album, score, path, error }
export const fpcalcInstalling    = writable(false)
export const fpcalcInstallError  = writable(null)
export const wishes              = writable([])   // Musikwuensche der Gaeste
export const playlistChoice      = writable(null)   // null | {pending:true} | {url, format, track_title, playlist_title, count}
export const spotdlInstalling    = writable(false)
export const spotdlInstallText   = writable(null)   // Fortschrittstext während des Downloads
export const spotdlInstallError  = writable(null)
export const updateProgress = writable(null)
export const loudnormOnDl = writable(false)
export const loudnormTarget = writable(-14)
export const loudnormTp = writable(-1.5)
export const scanRecursive      = writable(true)
export const autoRemovePlayed   = writable(false)
export const backendLogs        = writable([])
export const analyzeProgress    = writable(null)  // null | { done, total }
export const livePositionMs     = writable(0)      // live-updated aus Player.svelte (nicht vom Backend)
export const skipNextCrossfade  = writable(false)  // set true before any user-initiated play_at/play_now
export const playlistContent = writable({})   // { [path]: track[] }
export const selectionOwner  = writable('')   // '' | 'library' | 'queue' — only one panel may have a selection at a time
export const notes           = writable('')   // free-text scratchpad, persisted on the backend
export const remoteStatus      = writable(null)  // null | {running, ip, port, url, error}
export const remoteAutostart   = writable(false)

// ── appSettings — persisted in localStorage ───────────────────────────────────
const APP_SETTINGS_DEFAULTS = {
  normalizeVolume: true,
  targetLUFS:      -10,
  bpmAnalysis:     true,
  smartFade:          true,
  fadeAggressiveness: 3,   // legacy, kept for compat
  introAggressiveness: 3,
  outroAggressiveness: 3,
  cfCurve:            'cosine',
  introSkipSec:       0,
  beatAlignCf:        true,
}

function _loadAppSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem('appSettings') || '{}')
    return { ...APP_SETTINGS_DEFAULTS, ...saved }
  } catch {
    return { ...APP_SETTINGS_DEFAULTS }
  }
}

export const appSettings = writable(_loadAppSettings())

// Persist to localStorage whenever appSettings changes
appSettings.subscribe(val => {
  try { localStorage.setItem('appSettings', JSON.stringify(val)) } catch {}
})

// ─────────────────────────────────────────────────────────────────────────────
let ws = null
let reconnectTimer = null

function connect() {
  ws = new WebSocket('ws://127.0.0.1:8765/ws')

  ws.onopen = () => {
    connected.set(true)
    clearTimeout(reconnectTimer)
    ws.send(JSON.stringify({ type: 'get_state' }))
    ws.send(JSON.stringify({ type: 'get_history' }))
    ws.send(JSON.stringify({ type: 'check_tools' }))
    ws.send(JSON.stringify({ type: 'get_download_tree' }))
  }

  ws.onclose = () => {
    connected.set(false)
    reconnectTimer = setTimeout(connect, 2000)
  }

  ws.onerror = () => ws.close()

  ws.onmessage = ({ data }) => {
    const msg = JSON.parse(data)
    switch (msg.type) {
      case 'queue':
        queue.set(msg.items)
        if (msg.current_idx !== undefined)
          playerState.update(s => ({ ...s, current_idx: msg.current_idx }))
        break
      case 'player_state':
        playerState.set(msg.state)
        playMode.update(pm => ({
          shuffle: msg.state.shuffle ?? pm.shuffle,
          repeat:  msg.state.repeat  ?? pm.repeat,
        }))
        break
      case 'now_playing':   nowPlaying.set(msg.track); break
      case 'downloads':     downloads.set(msg.items); break
      case 'library': {
        // If paths were removed from library, also remove them from download tree
        let prevPaths
        library.subscribe(l => { prevPaths = new Set(l.map(t => t.path)) })()
        // Deduplicate by path — backend may send the same file from overlapping watched folders
        const uniqueTracks = [...new Map(msg.tracks.map(t => [t.path, t])).values()]
        const newPaths = new Set(uniqueTracks.map(t => t.path))
        const deleted = [...prevPaths].filter(p => !newPaths.has(p))
        library.set(uniqueTracks)
        if (deleted.length > 0) {
          const del = new Set(deleted)
          downloadTree.update(dt => ({
            folders: dt.folders.map(f => ({ ...f, tracks: f.tracks.filter(t => !del.has(t.path)) })),
            files: dt.files.filter(f => !del.has(f.path))
          }))
          // Also remove from download history so history view updates immediately
          dlHistory.update(h => h.filter(e => !del.has(e.path)))
        }
        break
      }
      case 'scan_status':   scanStatus.set(msg.text); break
      case 'waveform':        waveform.set(msg.data ?? []); break
      case 'waveform_next':   waveformNext.set(msg.data ?? []); break
      case 'playlists':       playlists.set(msg.items ?? []); break
      case 'download_tree':   downloadTree.set(msg.tree ?? { folders: [], files: [] }); downloadTreeLoaded.set(true); break
      case 'search_results':  searchResults.set(msg); break
      case 'settings':
        // Partial settings messages (e.g. set_normalize_volume) omit volume/crossfade_s —
        // use update() so undefined fields don't overwrite existing values with NaN.
        if (msg.volume !== undefined || msg.crossfade_s !== undefined)
          settings.update(s => ({
            volume:      msg.volume      ?? s.volume,
            crossfade_s: msg.crossfade_s ?? s.crossfade_s,
          }))
        if (msg.scan_recursive            !== undefined) scanRecursive.set(msg.scan_recursive)
        if (msg.auto_remove_played        !== undefined) autoRemovePlayed.set(msg.auto_remove_played)
        if (msg.auto_mix                  !== undefined) autoMixEnabled.set(msg.auto_mix)
        if (msg.loudnorm_on_dl            !== undefined) loudnormOnDl.set(msg.loudnorm_on_dl)
        if (msg.loudnorm_target           !== undefined) loudnormTarget.set(msg.loudnorm_target)
        if (msg.loudnorm_tp               !== undefined) loudnormTp.set(msg.loudnorm_tp)
        if (msg.playlist_folder_enabled   !== undefined) playlistFolderEnabled.set(msg.playlist_folder_enabled)
        if (msg.dl_filename_format        !== undefined) dlFilenameFormat.set(msg.dl_filename_format)
        if (msg.download_dir              !== undefined) downloadDir.set(msg.download_dir)
        if (msg.auto_scan_interval_min    !== undefined) autoScanIntervalMin.set(msg.auto_scan_interval_min)
        if (msg.favorites                 !== undefined) favorites.set(msg.favorites)
        if (msg.remote_autostart          !== undefined) remoteAutostart.set(msg.remote_autostart)
        // bpm_analysis and normalize settings are authoritative from backend
        if (msg.bpm_analysis              !== undefined)
          appSettings.update(s => ({ ...s, bpmAnalysis: msg.bpm_analysis }))
        if (msg.normalize_volume          !== undefined)
          appSettings.update(s => ({ ...s, normalizeVolume: msg.normalize_volume }))
        if (msg.target_lufs               !== undefined)
          appSettings.update(s => ({ ...s, targetLUFS: msg.target_lufs }))
        if (msg.spotify_client_id         !== undefined) spotifyClientId.set(msg.spotify_client_id)
        if (msg.spotify_client_secret     !== undefined) spotifyClientSecret.set(msg.spotify_client_secret)
        if (msg.lastfm_api_key            !== undefined) lastfmApiKey.set(msg.lastfm_api_key)
        if (msg.acoustid_api_key          !== undefined) acoustidApiKey.set(msg.acoustid_api_key)
        if (msg.radio_enabled             !== undefined) radioEnabled.set(msg.radio_enabled)
        break
      case 'playlist_content':
        playlistContent.update(m => ({ ...m, [msg.path]: msg.tracks ?? [] }))
        break
      case 'track_enriched':
        nowPlaying.update(t => t?.path === msg.path
          ? { ...t, art: msg.art ?? t?.art, lufs: msg.lufs ?? t?.lufs, bpm: msg.bpm || t?.bpm,
              duration_sec: msg.duration_sec || t?.duration_sec }
          : t)
        queue.update(q => q.map(t => t.path === msg.path ? {
          ...t,
          ...(msg.lufs ? { lufs: msg.lufs } : {}),
          ...(msg.bpm  ? { bpm:  msg.bpm  } : {}),
          ...(msg.duration_sec ? { duration_sec: msg.duration_sec } : {}),
        } : t))
        library.update(l => l.map(t => t.path === msg.path ? {
          ...t,
          ...(msg.lufs ? { lufs: msg.lufs } : {}),
          ...(msg.bpm  ? { bpm:  msg.bpm  } : {}),
          ...(msg.duration_sec ? { duration_sec: msg.duration_sec } : {}),
        } : t))
        break
      case 'automix_status': automixStatus.set(msg.text ?? ''); break
      case 'history': dlHistory.set(msg.items ?? []); break
      case 'normalize_progress': normalizeProgress.set(msg); break
      case 'normalize_done': normalizeProgress.set(null); break
      case 'tools_info': toolsInfo.set(msg); break
      case 'update_progress':
        updateProgress.set(msg)
        if (msg.pct === 100 || msg.pct === -1)
          setTimeout(() => updateProgress.set(null), 3000)
        break
      case 'scan_recursive':     scanRecursive.set(msg.enabled); break
      case 'auto_remove_played': autoRemovePlayed.set(msg.enabled); break
      case 'logs': backendLogs.set(msg.lines ?? []); break
      case 'notes': notes.set(msg.text ?? ''); break
      case 'favorites': favorites.set(msg.items ?? []); break
      case 'remote_status': remoteStatus.set(msg); break
      case 'settings_export': {
        const blob = new Blob([JSON.stringify(msg.data, null, 2)], { type: 'application/json' })
        const a = document.createElement('a')
        a.href = URL.createObjectURL(blob)
        a.download = 'synthimix-settings.json'
        a.click()
        URL.revokeObjectURL(a.href)
        break
      }
      case 'track_meta_update':
        library.update(l => l.map(t => t.path === msg.track?.path ? { ...t, ...msg.track } : t))
        break
      case 'analyze_progress':
        if (msg.finished) analyzeProgress.set(null)
        else analyzeProgress.set({ done: msg.done, total: msg.total })
        break
      case 'radio_status':  radioEnabled.set(msg.enabled); break
      case 'radio_added':   radioStatus.set({ title: msg.title, similar_to: msg.similar_to }); setTimeout(() => radioStatus.set(null), 5000); break
      case 'track_identified': trackIdentified.set(msg); break
      case 'fpcalc_install_progress': fpcalcInstalling.set(true);  fpcalcInstallError.set(null); break
      case 'fpcalc_install_done':     fpcalcInstalling.set(false); break
      case 'fpcalc_install_error':    fpcalcInstalling.set(false); fpcalcInstallError.set(msg.text ?? 'Fehler'); break
      case 'wishes':                  wishes.set(msg.items || []); break
      case 'playlist_choice_pending': playlistChoice.set({ pending: true }); break
      case 'playlist_choice_cancel':  playlistChoice.set(null); break
      case 'playlist_choice':         playlistChoice.set({ ...msg, pending: false }); break
      case 'spotdl_install_progress': spotdlInstalling.set(true);  spotdlInstallError.set(null); spotdlInstallText.set(msg.text ?? null); break
      case 'spotdl_install_done':     spotdlInstalling.set(false); spotdlInstallText.set(null); if (msg.version) toolsInfo.update(t => ({ ...t, spotdl_version: msg.version })); break
      case 'spotdl_install_error':    spotdlInstalling.set(false); spotdlInstallText.set(null); spotdlInstallError.set(msg.text ?? 'Fehler'); break
    }
  }
}

export function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj))
  }
}

connect()
