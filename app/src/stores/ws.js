import { writable, get } from 'svelte/store'

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
export const waveformThird  = writable({ path: '', data: [] })   // Titel nach dem naechsten
// Taktraster je Pfad fuer den Beat-Sync: { bpm, off, conf } (bpm 0 = unbekannt)
export const beatGrids      = writable({})
// Ergebnis von "Harmonisch sortieren": { count, boosts, unknown_keys, at }
export const harmonicResult = writable(null)
export const searchResults  = writable(null)   // null = panel closed
export const settings       = writable({ volume: 80, crossfade_s: 8 })
export const playlists      = writable([])
export const downloadTree       = writable({ folders: [], files: [] })
export const downloadTreeLoaded = writable(false)
export const playMode       = writable({ shuffle: false, repeat: 0 })
export const settingsOpen  = writable(false)
export const settingsTab   = writable(null)   // Tab, der beim Oeffnen gezeigt wird

/** Einstellungen oeffnen, wahlweise direkt auf einem bestimmten Tab. */
// Zuletzt angefragte Waveform je Art (waveform / waveform_next / waveform_third)
const _wfWant = {}
export function requestWaveform(kind, path) {
  _wfWant[kind] = path
  send({ type: 'get_' + kind, path })
}

export function openSettings(tabId = null) {
  if (tabId) settingsTab.set(tabId)
  settingsOpen.set(true)
}
export const playlistFolderEnabled = writable(true)
export const dlParallel = writable(3)          // gleichzeitige Downloads bei Playlists (1-6)
export const dlFilenameFormat      = writable('title')
export const downloadDir           = writable('')
export const autoScanIntervalMin   = writable(0)
// Wo die Daten liegen; on: tragbarer Betrieb (neben dem Programm auf der Festplatte)
export const dataPlace             = writable({ on: false, dir: '' })
// Ergebnis der Diagnose (Einstellungen → System): null, 'busy' oder { checks, report, ok }
export const diagnoseResult        = writable(null)
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
export const wishesOpen          = writable(false) // Wunschliste offen (Titelleiste, Hinweis-Leiste)
export const wishesLoaded        = writable(false) // erste Wunschliste vom Backend da
// Release-Notes aller Versionen (von GitHub, offline aus dem Cache): [{tag, name, date, body}]
export const changelog           = writable(null)
// Verfolgte Playlists: [{url, title, fmt, folder, last_check, last_new, known, checking}]
export const followed            = writable([])
// Verfolgte Kanaele: [{url, title, auto_new, last_check, last_new, playlists, pending:[{url,title}], checking}]
export const followedChannels    = writable([])
export const followTracks        = writable({})   // url -> { ts, items: [{t, s}] | null } (aufgeklappte Playlists)
// Auswahl der Playlists eines Kanals: null | {plan_id, url, title, playlists:[{url,title,thumb,followed}], existing, auto_new, excluded} | {error}
export const channelPlan         = writable(null)
// "Jetzt mischen" von der Fernbedienung: Zeitstempel der Anforderung
export const mixNowRequest       = writable(0)
// Eigener Store: im settings-Store landen nur volume/crossfade_s, dort kam der
// Wert nie an — der Schalter stand in 1.4.2 dadurch immer auf aus.
export const ytdlpAutoupdate     = writable(true)
export const watchedFolders      = writable([])   // [{path, exists, tracks, inside}]
export const watchedFolderImpact = writable(null) // {folder, tracks} — Antwort auf dry_run
export const excludedFolders     = writable([])   // aus der Bibliothek ausgeschlossene Ordner
// Ergebnis der taeglichen Pruefung: { ytdlp: {latest, available}, spotdl: {current, latest, available},
// ytdlp_updated: {from, to, at} } — daraus kommen Punkt am Zahnrad und Hinweise
export const toolUpdates         = writable({})
// Playlist-Kaestchen vor dem Laden: Warteliste fertiger Pruefungen
// [{plan_id, url, format, title, total, have, new, dupes, followed}]
export const playlistPlans       = writable([])
// Laufende Pruefungen (blockieren nichts): [{url, phase: 'list'|'search', done, total}]
export const planChecks          = writable([])
export const playlistChoice      = writable(null)   // null | {pending:true} | {url, format, track_title, playlist_title, count}
// Musikvideo-Links: Warteliste der Rueckfragen und laufende Pruefungen
export const videoChoices        = writable([])     // [{url, format, video:{title,uploader,duration}, song:{url,title,uploader,duration}}]
export const videoCheckPending   = writable(0)
// Download eines Songs, der schon in der Bibliothek liegt: Warteliste der Rueckfragen
export const dupeChoices         = writable([])     // [{url, format, video, matches:[{path,title,…}]}]
export const revealPath          = writable(null)   // Bibliothek springt zu diesem Titel
// Genres ergaenzen: {busy, progress:{done,total}, suggestions, applying:{done,total}, applied}
export const genreState          = writable({})
// Sammel-Ersetzen: {phase, done, total, current, items:{[path]: {title, duration, candidate, sure}}, finished, result}
export const qualityBatch        = writable({})
// Fenster der Sammelsuche offen? Geschlossen laeuft die Suche im Hintergrund
// weiter (kleine Anzeige unten rechts) und das Fenster kommt wieder, wenn die
// Vorschlaege fertig sind.
export const qualityBatchOpen    = writable(false)
// Laufwerk gewechselt: Vorschlaege [{from, to, count, found}] und Stand
export const relocateSuggest     = writable([])
export const relocateState       = writable(null)   // null | {busy} | {done, tracks, merged, playlists} | {none}
export function startQualityBatch(paths) {
  const cur = get(qualityBatch)
  if (cur.phase === 'search' || cur.phase === 'replace') { qualityBatchOpen.set(true); return }
  qualityBatch.set({ phase: 'search', done: 0, total: paths.length, items: {}, paths })
  qualityReplace.update(m => { const n = { ...m }; for (const p of paths) delete n[p]; return n })
  send({ type: 'quality_batch', paths })
  qualityBatchOpen.set(true)
}
export const servicesTest        = writable(null)   // {lastfm, acoustid, fpcalc, spotify: {ok, text}}
export const titleState          = writable({})
export const setupOpen           = writable(false)  // Einrichtungs-Assistent     // Titel aufraeumen: {busy, progress, suggestions, applying, applied}
// Qualitaetspruefung: Fortschritt der Bandbreiten-Messung, Kandidaten und Ersetzen
export const qualityScan         = writable(null)   // null | {done, total}
export const qualityCandidates   = writable(null)   // {path, query, results, final}
export const qualityReplace      = writable({})     // {[path]: {state, text}}
export const spotdlInstalling    = writable(false)
export const spotdlInstallText   = writable(null)   // Fortschrittstext während des Downloads
export const spotdlInstallError  = writable(null)
export const updateProgress = writable(null)
export const loudnormOnDl = writable(false)
export const loudnormTarget = writable(-10)
export const loudnormTp = writable(-1.5)
export const scanRecursive      = writable(true)
export const autoRemovePlayed   = writable(false)
export const backendLogs        = writable([])
export const analyzeProgress    = writable(null)  // null | { done, total }
export const livePositionMs     = writable(0)      // live-updated aus Player.svelte (nicht vom Backend)
export const skipNextCrossfade  = writable(false)  // set true before any user-initiated play_at/play_now
export const playlistContent = writable({})   // { [path]: track[] }
export const playlistRenamed = writable(null) // Antwort auf rename_playlist / follow_rename
export const selectionOwner  = writable('')   // '' | 'library' | 'queue' — only one panel may have a selection at a time
export const notes           = writable('')   // free-text scratchpad, persisted on the backend
export const remoteStatus      = writable(null)  // null | {running, ip, port, url, error}
export const remoteAutostart   = writable(false)

// ── appSettings — persisted in localStorage ───────────────────────────────────
const APP_SETTINGS_DEFAULTS = {
  normalizeVolume: true,
  cfLoudMatch:     true,
  djMode:          true,       // kreative Uebergaenge (Double Drop, Filter, Echo, Roll)
  djAmount:        0.4,        // Anteil der passenden Uebergaenge, die kreativ werden
  djTypes:         { doubledrop: true, filter: true, echo: true, roll: true },       // beim Uebergang den Einstieg an den alten Titel angleichen
  targetLUFS:      -10,
  bpmAnalysis:     true,
  smartFade:          true,
  fadeAggressiveness: 3,   // legacy, kept for compat
  introAggressiveness: 3,
  outroAggressiveness: 3,
  cfCurve:            'cosine',
  introSkipSec:       0,
  beatAlignCf:        true,
  tempoMatch:         true,    // Tempo des naechsten Titels im Uebergang angleichen
  phraseAlign:        true,    // Uebergang auf Phrasenanfang (16/8 Takte, hoechstens 8 Takte verschoben)
  bassSwap:           true,    // Bass weich tauschen (EQ), nur wenn der Uebergang im Takt laeuft
  maxTempoDiff:       8,       // groesster Tempo-Unterschied fuers Angleichen in % (4-25)
  cfUnit:             'bars',  // Uebergangslaenge in Takten ('bars') oder Sekunden ('sec')
  cfBars:             16,      // Takte (0 = aus); ohne bekannte BPM gelten die Sekunden
  outputDevice:       '',      // Ausgabegeraet (deviceId), '' = Windows-Standard
  outputDeviceLabel:  '',      // Name dazu, falls sich die Kennung aendert
  playerShowLufs:     false,   // LUFS-Stand im Player neben BPM zeigen
  dupeAllowFolders:   [],      // Ordner, in denen Kopien nicht als Duplikat zaehlen
  keyNotation:        'musical',   // Tonart als 'musical' (F♯m) oder 'camelot' (11A)
  pauseFadeMs:        500,   // Aus-/Einblenden bei Pause und Fortsetzen
  volumeFadeMs:       200,   // Lautstaerke-Aenderungen glaetten
  // Was die Warteschlange je Zeile zeigt (Titel und Dauer immer)
  qShowKey:           false,
  qShowBpm:           false,
  qShowEta:           true,
  qShowPlays:         false,
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
  // VITE_WS_PORT kommt nur aus "npm run dev:wstest" (.env.wstest). Laeuft das
  // installierte SynthiMIX, belegt es 8765 — eine normale Dev-Vorschau haengt
  // sich dann still an dessen Backend mit den echten Daten. Im gebauten
  // Programm ist die Variable nicht gesetzt, dort bleibt es bei 8765.
  const port = import.meta.env.VITE_WS_PORT || '8765'
  ws = new WebSocket(`ws://127.0.0.1:${port}/ws`)

  ws.onopen = () => {
    connected.set(true)
    clearTimeout(reconnectTimer)
    ws.send(JSON.stringify({ type: 'get_state' }))
    ws.send(JSON.stringify({ type: 'get_history' }))
    ws.send(JSON.stringify({ type: 'check_tools' }))
    ws.send(JSON.stringify({ type: 'get_download_tree' }))
    ws.send(JSON.stringify({ type: 'get_watched_folders' }))   // Musikordner in der Navigation
    ws.send(JSON.stringify({ type: 'get_followed' }))
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
      case 'library_delta': {
        // Nur geaenderte / entfernte Titel (siehe push_library im Backend)
        const up = new Map((msg.upsert ?? []).map(t => [t.path, t]))
        const del = new Set(msg.remove ?? [])
        library.update(l => {
          const out = []
          for (const t of l) {
            if (del.has(t.path)) continue
            const n = up.get(t.path)
            if (n) { out.push(n); up.delete(t.path) } else out.push(t)
          }
          for (const t of up.values()) out.push(t)
          return out
        })
        if (del.size > 0) {
          downloadTree.update(dt => ({
            folders: dt.folders.map(f => ({ ...f, tracks: f.tracks.filter(t => !del.has(t.path)) })),
            files: (dt.files ?? []).filter(t => !del.has(t.path)),
          }))
          send({ type: 'get_history' })
        }
        break
      }
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
          // Download-Verlauf neu holen: geloeschte Dateien bleiben dort sichtbar
          // (ausgegraut), das Backend prueft, ob die Datei noch existiert
          send({ type: 'get_history' })
        }
        break
      }
      case 'scan_status':   scanStatus.set(msg.text); break
      // Waveforms kommen nebenher — eine aeltere Antwort darf keine neuere ueberschreiben
      case 'relocate_suggest':
        relocateSuggest.set(msg.items ?? [])
        if (msg.manual && !(msg.items ?? []).length) relocateState.set({ none: true })
        break
      case 'relocated': {
        // Auch Pfade in der Oberflaeche (angeheftete Ordner, zuletzt offene Ansicht)
        const fix = (v) => typeof v === 'string' && v.toLowerCase().startsWith(msg.from.toLowerCase()) ? msg.to + v.slice(msg.from.length)
          : Array.isArray(v) ? v.map(fix) : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).map(([k, x]) => [fix(k), fix(x)])) : v
        try {
          for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i), raw = localStorage.getItem(k)
            if (!raw || !raw.toLowerCase().includes(msg.from.toLowerCase().replace(/\\/g, '\\\\'))) continue
            try { localStorage.setItem(k, JSON.stringify(fix(JSON.parse(raw)))) } catch {}
          }
        } catch {}
        relocateSuggest.update(l => l.filter(x => x.from !== msg.from))
        relocateState.set({ done: true, tracks: msg.tracks, merged: msg.merged, playlists: msg.playlists })
        send({ type: 'get_state' })
        break
      }
      case 'waveform':        if (_wfWant.waveform === undefined || msg.path === _wfWant.waveform) waveform.set(msg.data ?? []); break
      case 'waveform_next':   if (_wfWant.waveform_next === undefined || msg.path === _wfWant.waveform_next) waveformNext.set(msg.data ?? []); break
      case 'waveform_third':  if (_wfWant.waveform_third === undefined || msg.path === _wfWant.waveform_third) waveformThird.set({ path: msg.path ?? '', data: msg.data ?? [] }); break
      case 'harmonic_result': harmonicResult.set({ ...msg, at: Date.now() }); break
      case 'diagnose': diagnoseResult.set({ checks: msg.checks ?? [], report: msg.report ?? '', ok: !!msg.ok }); break
      case 'onset_ref': { const w = _refWait.get(msg.id); if (w) { _refWait.delete(msg.id); w(msg) } break }
      case 'beatgrid':
        beatGrids.update(g => ({ ...g, [msg.path]: { bpm: msg.bpm_f || 0, off: msg.beat_off || 0, conf: msg.beat_conf || 0,
          phrase: msg.phrase_off ?? null, barBeats: msg.bar_beats || 0, phraseSrc: msg.phrase_src || '',
          // Drops aus dem Backend (Lautheit + Bass je Takt, MIK-Cues); null = noch nicht gemessen
          drops: Array.isArray(msg.drops) ? msg.drops : null } }))
        break
      case 'playlists':       playlists.set(msg.items ?? []); break
      case 'playlist_renamed': playlistRenamed.set({ ...msg, at: Date.now() }); break
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
        if (msg.dl_parallel               !== undefined) dlParallel.set(msg.dl_parallel)
        if (msg.dl_filename_format        !== undefined) dlFilenameFormat.set(msg.dl_filename_format)
        if (msg.download_dir              !== undefined) downloadDir.set(msg.download_dir)
        if (msg.auto_scan_interval_min    !== undefined) autoScanIntervalMin.set(msg.auto_scan_interval_min)
        if (msg.data_dir                  !== undefined) dataPlace.set({ on: !!msg.portable, dir: msg.data_dir })
        if (msg.favorites                 !== undefined) favorites.set(msg.favorites)
        if (msg.remote_autostart          !== undefined) remoteAutostart.set(msg.remote_autostart)
        if (msg.ytdlp_autoupdate          !== undefined) ytdlpAutoupdate.set(msg.ytdlp_autoupdate)
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
      // Zusammenfuehren statt ersetzen: das taegliche yt-dlp-Update und die
      // fpcalc-Installation schicken nur ihr eigenes Feld — die anderen
      // Versionen standen danach als "fehlt" in den Einstellungen.
      case 'tools_info': { const { type, ...info } = msg; toolsInfo.update(t => ({ ...t, ...info })); break }
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
      case 'quality_scan':
        qualityScan.set(msg.finished ? null : { done: msg.done, total: msg.total })
        break
      case 'quality_candidates':      qualityCandidates.set(msg); break
      case 'quality_replace_status':  qualityReplace.update(m => ({ ...m, [msg.path]: { state: msg.state, text: msg.text } })); break
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
      case 'wishes':                  wishes.set(msg.items || []); wishesLoaded.set(true); break
      case 'changelog':               changelog.set(msg.items || []); break
      case 'followed':                followed.set(msg.items || []); followedChannels.set(msg.channels || []); break
      case 'follow_tracks':           followTracks.update(m => ({ ...m, [msg.url]: { ts: msg.ts, items: msg.items ?? null } })); break
      case 'mix_now':                 mixNowRequest.set(Date.now()); break
      case 'channel_plan':
        planChecks.update(l => l.filter(c => c.url !== msg.src))
        channelPlan.set(msg)
        break
      case 'watched_folders':         watchedFolders.set(msg.items || []); break
      case 'excluded_folders':        excludedFolders.set(msg.items || []); break
      case 'tool_updates':            toolUpdates.set(msg.items || {}); break
      case 'watched_folder_impact':   watchedFolderImpact.set({ folder: msg.folder, tracks: msg.tracks }); break
      case 'playlist_plan_pending':   planChecks.update(l => [...l.filter(c => c.url !== msg.url), { url: msg.url, kind: msg.kind ?? 'playlist', phase: 'list', done: 0, total: 0 }]); break
      case 'playlist_plan_progress':  planChecks.update(l => l.map(c => c.url === msg.url ? { ...c, phase: msg.phase, done: msg.done, total: msg.total } : c)); break
      case 'playlist_plan_cancel':    planChecks.update(l => l.filter(c => c.url !== msg.url)); break
      case 'playlist_plan':
        planChecks.update(l => l.filter(c => c.url !== msg.url))
        playlistPlans.update(l => [...l.filter(pl => pl.url !== msg.url), msg])
        break
      case 'playlist_choice_pending': playlistChoice.set({ pending: true }); break
      case 'playlist_choice_cancel':  playlistChoice.set(null); break
      case 'playlist_choice':         playlistChoice.set({ ...msg, pending: false }); break
      case 'video_check_pending':     videoCheckPending.update(n => n + 1); break
      case 'video_check_done':        videoCheckPending.update(n => Math.max(0, n - 1)); break
      case 'video_choice':            videoChoices.update(l => [...l, msg]); break
      case 'dupe_choice':             dupeChoices.update(l => [...l, msg]); break
      case 'quality_batch_progress':  qualityBatch.update(s => ({ ...s, phase: msg.phase, done: msg.done, total: msg.total, current: msg.current ?? '' })); break
      case 'quality_batch_item':      qualityBatch.update(s => ({ ...s, items: { ...(s.items ?? {}), [msg.path]: msg } })); break
      case 'quality_batch_done':      qualityBatch.update(s => ({ ...s, phase: 'review', cancelled: msg.cancelled })); qualityBatchOpen.set(true); break
      case 'quality_batch_replaced':  qualityBatch.update(s => ({ ...s, phase: 'finished', result: msg })); qualityBatchOpen.set(true); break
      case 'services_test':           servicesTest.set(msg); break
      case 'title_progress':          titleState.update(s => ({ ...s, progress: { done: msg.done, total: msg.total } })); break
      case 'title_suggestions':       titleState.update(s => ({ ...s, busy: false, progress: null, suggestions: msg })); break
      case 'title_apply_progress':    titleState.update(s => ({ ...s, applying: { done: msg.done, total: msg.total } })); break
      case 'title_applied':           titleState.update(s => ({ ...s, applying: null, applied: msg })); break
      case 'genre_progress':          genreState.update(s => ({ ...s, progress: { done: msg.done, total: msg.total } })); break
      case 'genre_suggestions':       genreState.update(s => ({ ...s, busy: false, progress: null, suggestions: msg })); break
      case 'genre_apply_progress':    genreState.update(s => ({ ...s, applying: { done: msg.done, total: msg.total } })); break
      case 'genre_applied':           genreState.update(s => ({ ...s, applying: null, applied: msg })); break
      case 'spotdl_install_progress': spotdlInstalling.set(true);  spotdlInstallError.set(null); spotdlInstallText.set(msg.text ?? null); break
      case 'spotdl_install_done':     spotdlInstalling.set(false); spotdlInstallText.set(null); if (msg.version) toolsInfo.update(t => ({ ...t, spotdl_version: msg.version })); break
      case 'spotdl_install_error':    spotdlInstalling.set(false); spotdlInstallText.set(null); spotdlInstallError.set(msg.text ?? 'Fehler'); break
    }
  }
}

// Huellkurven einer Stelle, exakt dekodiert (Messung der hoerbaren Stelle, lib/audiblepos.js)
const _refWait = new Map()
let _refId = 0
const _f32 = (b64) => { const s = atob(b64 || ''), u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return new Float32Array(u.buffer) }
export function requestOnsetRef(path, start, dur) {
  return new Promise((resolve, reject) => {
    const id = ++_refId
    const to = setTimeout(() => { _refWait.delete(id); reject(new Error('timeout')) }, 20000)
    _refWait.set(id, (msg) => { clearTimeout(to); msg.ok ? resolve({ start: msg.start, hi: _f32(msg.hi), lo: _f32(msg.lo) }) : reject(new Error('ref')) })
    send({ type: 'get_onset_ref', id, path, start, dur })
  })
}

export function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj))
  }
}

connect()
