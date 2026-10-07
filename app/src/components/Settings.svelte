<script>
  import { untrack } from 'svelte'
  import { theme, density } from '../lib/prefs.js'
  import KeyChip from './KeyChip.svelte'
  import BlendSketch from './BlendSketch.svelte'
  import { settings, settingsOpen, settingsTab, appSettings, send, toolsInfo, updateProgress,
           loudnormOnDl, loudnormTarget, loudnormTp, autoMixEnabled, playMode,
           playlistFolderEnabled, dlFilenameFormat, downloadDir, remoteStatus,
           autoScanIntervalMin, scanRecursive, remoteAutostart, dataPlace, diagnoseResult, backupState, dumpUi,
           spotifyClientId, spotifyClientSecret,
           lastfmApiKey, acoustidApiKey,
           fpcalcInstalling, fpcalcInstallError,
           watchedFolders, watchedFolderImpact, ytdlpAutoupdate, excludedFolders, toolUpdates, servicesTest, setupOpen, changelog, followed, followedChannels, followTracks, playlistRenamed, dlParallel, relocateState } from '../stores/ws.js'
  import { infoHints } from '../lib/infohints.js'

  let tab = $state('playback')

  // ── Ausgabegeraet (Wiedergabe) ─────────────────────────────────────────────
  let outDevices = $state([])
  async function loadDevices() {
    try {
      const d = await navigator.mediaDevices.enumerateDevices()
      outDevices = d.filter(x => x.kind === 'audiooutput' && x.deviceId !== 'default' && x.deviceId !== 'communications')
    } catch { outDevices = [] }
  }
  $effect(() => {
    if (tab !== 'playback') return
    loadDevices()
    const h = () => loadDevices()
    navigator.mediaDevices?.addEventListener?.('devicechange', h)
    return () => navigator.mediaDevices?.removeEventListener?.('devicechange', h)
  })
  const devMissing = $derived(!!$appSettings.outputDevice && !outDevices.some(d => d.deviceId === $appSettings.outputDevice))
  function setDevice(id) {
    const d = outDevices.find(x => x.deviceId === id)
    appSettings.update(s => ({ ...s, outputDevice: id, outputDeviceLabel: d?.label ?? '' }))
  }

  // ── Uebergangslaenge ──────────────────────────────────────────────────────
  const CF_BARS = [0, 4, 8, 16, 32]
  const barSec = (bars, bpm) => Math.round(bars * 4 * 60 / bpm)

  // ── Dienste: je Dienst eine Karte, aufklappbar ────────────────────────────
  let svcOpen = $state(null)
  // Tragbarer Betrieb: liegen auf diesem PC eigene Daten, die man auf die Platte holen kann?
  let portInfo = $state(null)
  let takeAsk = $state(false)
  $effect(() => {
    if (tab !== 'system') return
    window.electron?.portableInfo?.()?.then(i => { portInfo = i }).catch(() => {})
  })
  function takeOverPc() { takeAsk = false; window.electron?.portableTakeover?.() }

  // Sicherung: alles in eine Datei / aus einer Datei zurueck
  const canBackup = typeof window !== 'undefined' && !!window.electron?.pickFolder
  async function makeBackup() {
    const dir = await window.electron?.pickFolder?.()
    if (!dir) return
    backupState.set({ step: 'busy' })
    send({ type: 'backup_create', dir, ui: dumpUi() })
  }
  async function pickRestore() {
    const p = await window.electron?.pickFile?.({ title: 'Sicherung wählen', filters: [{ name: 'SynthiMIX-Sicherung', extensions: ['zip'] }] })
    if (!p) return
    backupState.set({ step: 'busy' })
    send({ type: 'backup_inspect', path: p })
  }
  function doRestore() {
    const p = $backupState?.info?.path
    if (p) { backupState.set({ step: 'busy' }); send({ type: 'backup_restore', path: p }) }
  }
  const fmtBackupDate = (iso) => { const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' }) }

  // Diagnose: prueft im Backend ffmpeg, Analyse, Ordner und eine Waveform
  let diagCopied = $state(false)
  function runDiagnose() { diagnoseResult.set('busy'); send({ type: 'get_diagnose' }) }
  async function copyDiagnose() {
    const r = $diagnoseResult
    if (!r?.report) return
    try { await navigator.clipboard.writeText(r.report); diagCopied = true; setTimeout(() => { diagCopied = false }, 2500) } catch {}
  }

  // Nach Updates suchen (nur in der installierten App)
  let updCheck = $state(null)        // null | 'busy' | { status, version?, message? }
  async function checkUpdate() {
    const w = window.electron
    if (!w?.checkUpdate) { updCheck = { status: 'dev' }; return }
    updCheck = 'busy'
    try { updCheck = await w.checkUpdate() } catch (e) { updCheck = { status: 'error', message: String(e) } }
  }
  function toggleCard(id) { svcOpen = svcOpen === id ? null : id }
  const svcState = $derived.by(() => {
    const t = $servicesTest ?? {}
    const res = (r, fallback) => r ? { cls: r.ok ? 'ok' : 'err', text: r.ok ? 'Verbunden' : 'Fehler' } : fallback
    const tool = (ver, upd) => !ver ? { cls: 'err', text: 'Fehlt' }
      : upd?.available ? { cls: 'busy', text: 'Update verfügbar' } : { cls: 'ok', text: 'Bereit' }
    return {
      ytdlp: $updateProgress && $updateProgress.tool !== 'ffmpeg' ? { cls: 'busy', text: 'Wird aktualisiert…' } : tool($toolsInfo.ytdlp_version, $toolUpdates.ytdlp),
      ffmpeg: $updateProgress?.tool === 'ffmpeg' ? { cls: 'busy', text: 'Wird aktualisiert…' } : tool($toolsInfo.ffmpeg_version, $toolUpdates.ffmpeg),
      // Spotify-Links brauchen nichts; die Zugangsdaten sind nur fuer die Genre-Abfrage
      spotify: !($spotifyClientId && $spotifyClientSecret) ? { cls: 'off', text: 'Nicht eingerichtet' }
        : res(t.spotify, { cls: 'ok', text: 'Eingetragen' }),
      lastfm: !$lastfmApiKey ? { cls: 'off', text: 'Nicht eingerichtet' } : res(t.lastfm, { cls: 'ok', text: 'Key eingetragen' }),
      acoustid: !$toolsInfo.fpcalc_found ? { cls: 'off', text: 'fpcalc fehlt' }
        : !$acoustidApiKey ? { cls: 'off', text: 'Key fehlt' } : res(t.acoustid, { cls: 'ok', text: 'Bereit' }),
    }
  })

  // ── Verfolgte Playlists (frueher als Kasten ueber den Downloads) ─────────
  const FOLLOW_MODE = { playlist: 'als Playlist', new: 'nur neue', folder: 'kompletter Ordner' }
  // Playlists eines Kanals stehen eingeklappt unter dem Kanal
  let chOpen = $state(new Set())
  const soloFollowed = $derived($followed.filter(f => !f.channel))
  function chToggle(url) { const s = new Set(chOpen); s.has(url) ? s.delete(url) : s.add(url); chOpen = s }
  // Verfolgte Playlist umbenennen (eigener Name statt des YouTube-Namens)
  let fRename = $state(null)        // { url, name }
  let fRenameErr = $state(null)     // { url, text }
  function startFollowRename(f) { fRename = { url: f.url, name: f.name || f.title }; fRenameErr = null }
  function commitFollowRename() {
    const r = fRename
    fRename = null
    if (r) send({ type: 'follow_rename', url: r.url, name: r.name.trim(), src: 'settings' })
  }
  function focusSelect(el) { el.focus(); el.select() }
  $effect(() => {
    const r = $playlistRenamed
    if (!r || r.ok || r.src !== 'settings') return
    untrack(() => { fRenameErr = { text: r.error || 'Umbenennen ging nicht.' }; setTimeout(() => { fRenameErr = null }, 4000) })
  })

  // Aufgeklappte Playlist: ihre Titel (Stand der letzten Pruefung) mit Zustand
  let ftOpen = $state(new Set())
  function ftToggle(url) {
    const s = new Set(ftOpen)
    if (s.has(url)) s.delete(url)
    else { s.add(url); send({ type: 'follow_tracks', url }) }
    ftOpen = s
  }
  // Nach einer Pruefung die offene Liste neu holen
  let ftChecked = {}
  $effect(() => {
    for (const f of $followed) {
      if (ftOpen.has(f.url) && f.last_check && ftChecked[f.url] !== f.last_check) {
        if (ftChecked[f.url] !== undefined) send({ type: 'follow_tracks', url: f.url })
        ftChecked[f.url] = f.last_check
      }
    }
  })
  const FT = {
    folder:  { ico: 'ti-folder-check',   lbl: 'im Ordner' },
    lib:     { ico: 'ti-music',          lbl: 'in der Sammlung, nicht im Ordner' },
    fail:    { ico: 'ti-alert-triangle', lbl: 'fehlgeschlagen — wird wieder versucht' },
    gone:    { ico: 'ti-ban',            lbl: 'gelöscht oder privat — übersprungen' },
    missing: { ico: 'ti-trash',          lbl: 'nicht mehr da (selbst gelöscht)' },
  }
  function ftSummary(items) {
    const n = {}
    for (const it of items) n[it.s] = (n[it.s] || 0) + 1
    return Object.keys(FT).filter(k => n[k]).map(k => `${n[k]} ${FT[k].lbl.split(' — ')[0]}`)
  }
  function followAgo(ts) {
    if (!ts) return 'noch nie geprüft'
    const min = Math.round((Date.now() / 1000 - ts) / 60)
    if (min < 1) return 'gerade geprüft'
    if (min < 60) return `vor ${min} min`
    const h = Math.round(min / 60)
    return h < 48 ? `vor ${h} h` : `vor ${Math.round(h / 24)} Tagen`
  }

  // ── Beobachtete Ordner ────────────────────────────────────────────────────
  // Bisher liess sich ein Ordner hinzufuegen, aber weder ansehen noch wieder
  // entfernen. Vor dem Entfernen wird ausgerechnet, wie viele Titel dann aus
  // der Bibliothek verschwinden (die Dateien selbst bleiben).
  let pendingRemove = $state(null)   // { folder, tracks: Zahl | null solange gerechnet wird }
  function askRemoveFolder(folder) {
    pendingRemove = { folder, tracks: null }
    send({ type: 'remove_watched_folder', folder, dry_run: true })
  }
  $effect(() => {
    const imp = $watchedFolderImpact
    if (!imp) return
    untrack(() => {
      if (pendingRemove?.folder === imp.folder && pendingRemove.tracks === null)
        pendingRemove = { folder: imp.folder, tracks: imp.tracks }
    })
  })
  function confirmRemoveFolder() {
    send({ type: 'remove_watched_folder', folder: pendingRemove.folder })
    pendingRemove = null
  }
  async function addWatchedFolder() {
    const folder = await window.electron?.pickFolder?.()
    if (folder) send({ type: 'scan_library', folder })
  }
  // Wird die Seite aus einer Fehlermeldung heraus geoeffnet, springt sie
  // direkt auf den passenden Tab statt den Nutzer suchen zu lassen.
  $effect(() => {
    if ($settingsTab) {
      tab = $settingsTab; settingsTab.set(null)
      // Programme stehen als Karten unter Dienste: die mit dem Update gleich aufklappen
      if (tab === 'services') {
        const u = untrack(() => $toolUpdates)
        svcOpen = u.ytdlp?.available ? 'ytdlp' : u.ffmpeg?.available ? 'ffmpeg' : svcOpen
      }
      // Kommt man ueber den Update-Punkt am Zahnrad, gleich zum Hinweis scrollen
      setTimeout(() => document.querySelector('.upd-note, [data-upd]')
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 60)
    }
  })

  const TABS = [
    { id: 'playback', label: 'Wiedergabe', icon: 'ti-player-play' },
    { id: 'fade',     label: 'Blend',      icon: 'ti-adjustments-horizontal' },
    { id: 'download', label: 'Download',   icon: 'ti-download' },
    { id: 'services', label: 'Dienste',    icon: 'ti-plug' },
    { id: 'look',     label: 'Darstellung', icon: 'ti-palette' },
    { id: 'system',   label: 'System',     icon: 'ti-settings' },
    { id: 'remote',   label: 'Remote',     icon: 'ti-device-mobile' },
    { id: 'info',     label: 'Info',       icon: 'ti-info-circle' },
  ]

  function close() { settingsOpen.set(false) }

  // Tabs mit offenem Update-Hinweis bekommen einen Punkt
  const tabNotice = $derived({
    // Programme (yt-dlp, ffmpeg) stehen alle unter Dienste
    services: !!($toolUpdates.ytdlp?.available || $toolUpdates.ytdlp_updated
                  || $toolUpdates.ffmpeg?.available || $toolUpdates.ffmpeg_updated),
  })
  let toolCheckRunning = $state(false)
  function checkToolUpdates() {
    toolCheckRunning = true
    send({ type: 'check_tool_updates' })
    setTimeout(() => toolCheckRunning = false, 8000)
  }
  $effect(() => { void $toolUpdates; toolCheckRunning = false })

  // ── Was ist neu: Release-Notes aller Versionen ──────────────────────────
  const APP_VERSION = __APP_VERSION__
  $effect(() => { if (tab === 'info' && $changelog === null) send({ type: 'get_changelog' }) })
  // Release-Notes sind schlichtes Markdown: Ueberschriften, **fett**, Listen
  // mit eingerueckten Folgezeilen. Hier ohne HTML-Einschleusen in Bloecke zerlegt.
  function parseNotes(body) {
    const out = []
    let ul = null, para = null
    for (const raw of (body || '').replace(/\r/g, '').split('\n')) {
      const line = raw.trimEnd()
      if (!line.trim()) { ul = null; para = null; continue }
      if (/^#{1,6}\s/.test(line)) {
        ul = null; para = null
        if (!/^#+\s+Neues in/i.test(line)) out.push({ t: 'h', x: line.replace(/^#+\s*/, '') })
        continue
      }
      const bold = line.match(/^\*\*(.+)\*\*:?$/)
      if (bold) { ul = null; para = null; out.push({ t: 'h', x: bold[1] }); continue }
      const li = line.match(/^\s*[-*•]\s+(.*)$/)
      if (li) {
        if (!ul) { ul = { t: 'ul', items: [] }; out.push(ul); para = null }
        ul.items.push(li[1]); continue
      }
      if (ul && /^\s+/.test(raw)) { ul.items[ul.items.length - 1] += ' ' + line.trim(); continue }
      if (para) para.x += ' ' + line.trim()
      else { ul = null; para = { t: 'p', x: line.trim() }; out.push(para) }
    }
    return out
  }
  function splitBold(t) { return t.split('**').map((x, i) => [i % 2 === 1, x]) }
  function fmtDate(d) { return d && d.length >= 10 ? `${d.slice(8, 10)}.${d.slice(5, 7)}.${d.slice(0, 4)}` : '' }
  // ffmpeg-Builds heissen "N-125258-g…-20260624": fuer Menschen das Datum
  function fmtBuild(d) { return d && d.length === 8 ? `${d.slice(6)}.${d.slice(4, 6)}.${d.slice(0, 4)}` : (d ?? '') }
  function fmtFfmpeg(v) {
    if (!v) return '—'
    const m = v.match(/(20\d{2})(\d{2})(\d{2})\b/)
    return m ? 'Build ' + fmtBuild(m[0]) : v
  }

  // ── Queue-Ende: virtuelles Radio aus zwei stores ──────────────────────────
  const queueEnd = $derived(
    $autoMixEnabled ? 'automix' : $playMode.repeat === 2 ? 'repeat' : 'stop'
  )
  function setQueueEnd(val) {
    if (val === 'automix') {
      autoMixEnabled.set(true)
      playMode.update(pm => ({ ...pm, repeat: 0 }))
      send({ type: 'set_auto_mix', value: true })
      send({ type: 'set_repeat', value: 0 })
    } else if (val === 'repeat') {
      autoMixEnabled.set(false)
      playMode.update(pm => ({ ...pm, repeat: 2 }))
      send({ type: 'set_auto_mix', value: false })
      send({ type: 'set_repeat', value: 2 })
    } else {
      autoMixEnabled.set(false)
      playMode.update(pm => ({ ...pm, repeat: 0 }))
      send({ type: 'set_auto_mix', value: false })
      send({ type: 'set_repeat', value: 0 })
    }
  }

  function sendLoudnorm() {
    send({ type: 'set_loudnorm_dl', enabled: $loudnormOnDl,
           target: $loudnormTarget, true_peak: $loudnormTp })
  }

  function pickDownloadFolder() {
    window.electron?.pickFolder?.()?.then(p => {
      if (p) {
        downloadDir.set(p)
        send({ type: 'set_download_folder', path: p })
      }
    })
  }

  function setPlaylistFolder(enabled) {
    playlistFolderEnabled.set(enabled)
    send({ type: 'set_playlist_folder', enabled })
  }

  // Spotify credentials — local edit state so we don't spam the backend on every keystroke
  let spotifyCidEdit  = $state('')
  let spotifyCsecEdit = $state('')
  $effect(() => { spotifyCidEdit  = $spotifyClientId  })
  $effect(() => { spotifyCsecEdit = $spotifyClientSecret })
  function saveSpotifyCreds() {
    spotifyClientId.set(spotifyCidEdit)
    spotifyClientSecret.set(spotifyCsecEdit)
    send({ type: 'set_spotify_creds', client_id: spotifyCidEdit, client_secret: spotifyCsecEdit })
  }

  // Services (Last.fm + AcoustID) — local edit state
  let lastfmKeyEdit   = $state('')
  let acoustidKeyEdit = $state('')
  // Nur uebernehmen, solange nichts eingetippt wurde — sonst ueberschrieb ein
  // Neuverbinden die Eingabe. Gespeichert wird beim Verlassen des Feldes; frueher
  // nur per Knopf, und wer ihn uebersah, verlor den Key beim Schliessen.
  let servicesDirty = false
  $effect(() => { const a = $lastfmApiKey, b = $acoustidApiKey; if (!servicesDirty) { lastfmKeyEdit = a; acoustidKeyEdit = b } })
  let testing = $state(false)
  function testServices() {
    saveServices()
    testing = true
    servicesTest.set(null)
    send({ type: 'test_services' })
  }
  $effect(() => { if ($servicesTest) testing = false })
  function saveServices() {
    servicesDirty = false
    lastfmApiKey.set(lastfmKeyEdit.trim())
    acoustidApiKey.set(acoustidKeyEdit.trim())
    send({ type: 'set_services', lastfm_api_key: lastfmKeyEdit.trim(), acoustid_api_key: acoustidKeyEdit.trim() })
  }

  function setFilenameFormat(fmt) {
    dlFilenameFormat.set(fmt)
    send({ type: 'set_dl_filename_format', format: fmt })
  }

  const AUTO_SCAN_OPTIONS = [
    { value: 0,  label: 'Aus' },
    { value: 15, label: '15 Min' },
    { value: 30, label: '30 Min' },
    { value: 60, label: '1 Std' },
    { value: 120, label: '2 Std' },
  ]

  function setAutoScanInterval(min) {
    autoScanIntervalMin.set(min)
    send({ type: 'set_auto_scan_interval', minutes: min })
  }

  function exportSettings() {
    send({ type: 'export_settings' })
  }

  function importSettings() {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.json'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      try {
        const text = await file.text()
        const data = JSON.parse(text)
        send({ type: 'import_settings', data })
      } catch { /* invalid JSON */ }
    }
    input.click()
  }

  import QRCode from 'qrcode'

  let urlCopied = $state(false)
  function copyRemoteUrl() {
    navigator.clipboard.writeText($remoteStatus?.url ?? '')
    urlCopied = true
    setTimeout(() => urlCopied = false, 1800)
  }
  let wishCopied = $state(false)
  function copyWishUrl() {
    navigator.clipboard.writeText($remoteStatus?.wish_url ?? '')
    wishCopied = true
    setTimeout(() => wishCopied = false, 1800)
  }
  // Fernbedienung und Musikwuensche einzeln an/aus (gemeinsamer Server)
  const svcRemote = $derived(!!$remoteStatus?.running && ($remoteStatus?.services?.remote ?? true))
  const svcWishes = $derived(!!$remoteStatus?.running && ($remoteStatus?.services?.wishes ?? true))
  function toggleSvc(service, on) { send({ type: on ? 'remote_start' : 'remote_stop', service }) }

  let qrCanvas     = $state(null)
  let wishQrCanvas = $state(null)
  // Der Wunsch-QR ist der, den man ausdruckt. Er bleibt gueltig, solange der
  // Rechner dieselbe Adresse behaelt — dafuer im Router eine feste IP
  // vergeben, sonst zeigt der Zettel beim naechsten Mal ins Leere.
  // Eigene Adresse vom Backend: die Fernbedienungs-URL traegt jetzt den
  // geheimen Schluessel, daran darf "/wunsch" nicht angehaengt werden.
  const wishUrl = $derived($remoteStatus?.wish_url ?? '')
  function newRemoteKey() {
    if (confirm('Neuen Link für die Fernbedienung erzeugen?\n' +
                'Der alte Link und der alte QR-Code funktionieren danach nicht mehr — am Handy einmal neu scannen.'))
      send({ type: 'remote_new_key' })
  }

  function malen(canvas, url) {
    if (!canvas || !url) return
    // Dunkel auf hell: invertierte Codes (hell auf dunkel) lesen nicht alle
    // Kamera-Apps, und der Wunsch-QR wird ausgedruckt.
    QRCode.toCanvas(canvas, url, {
      width: 160, margin: 2,
      color: { dark: '#000000', light: '#ffffff' }
    })
  }
  $effect(() => { malen(qrCanvas, $remoteStatus?.url) })
  $effect(() => { malen(wishQrCanvas, wishUrl) })
</script>

<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="overlay" onclick={close} role="dialog">
  <div class="panel" onclick={(e) => e.stopPropagation()}>

    <!-- Header -->
    <div class="hdr">
      <div class="hdr-brand">
        <svg class="hdr-icon" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <circle cx="20" cy="20" r="19" fill="#0d1a2e"/>
          <rect x="5"  y="16" width="4" height="9"  rx="1.5" fill="#e07800"/>
          <rect x="11" y="10" width="4" height="15" rx="1.5" fill="#e07800"/>
          <rect x="17" y="13" width="4" height="12" rx="1.5" fill="#f59332"/>
          <rect x="23" y="7"  width="4" height="18" rx="1.5" fill="#e07800"/>
          <rect x="29" y="11" width="4" height="14" rx="1.5" fill="#f59332"/>
          <line x1="20" y1="29" x2="20" y2="35" stroke="#3b82f6" stroke-width="2" stroke-linecap="round"/>
          <polyline points="16,32 20,36 24,32" fill="none" stroke="#3b82f6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span class="hdr-appname"><span class="nm-synthi">Synthi</span><span class="nm-mix">MIX</span></span>
        <span class="hdr-title">Einstellungen</span>
      </div>
      <button class="btn btn-icon close-btn" onclick={close} title="Schließen" aria-label="Einstellungen schließen"><i class="ti ti-x"></i></button>
    </div>

    <div class="layout">

      <!-- Left tab nav -->
      <nav class="tab-nav">
        {#each TABS as t}
          <button class="tab-btn {tab === t.id ? 'active' : ''}" onclick={() => tab = t.id} aria-current={tab === t.id ? 'page' : undefined}>
            <i class="ti {t.icon} tab-icon" aria-hidden="true"></i>
            <span class="tab-label">{t.label}</span>
            {#if tabNotice[t.id]}<span class="tab-dot"></span>{/if}
          </button>
        {/each}
      </nav>

      <!-- Right content -->
      <!-- Lange Erklaerungen liegen hinter einem ⓘ am Gruppentitel (lib/infohints.js) -->
      <div class="content" use:infoHints>

        <!-- ── WIEDERGABE ──────────────────────────────────────────────── -->
        {#if tab === 'playback'}

          <div class="group">
            <div class="group-title">Ausgabe</div>
            <div class="row">
              <span class="lbl">Ausgabegerät</span>
              <select class="field field-sm" value={$appSettings.outputDevice ?? ''} onchange={(e) => setDevice(e.currentTarget.value)}
                      aria-label="Ausgabegerät">
                <option value="">Windows-Standard</option>
                {#each outDevices as d (d.deviceId)}<option value={d.deviceId}>{d.label || 'Audiogerät'}</option>{/each}
                {#if devMissing}<option value={$appSettings.outputDevice}>{$appSettings.outputDeviceLabel || 'Gerät'} (nicht verbunden)</option>{/if}
              </select>
            </div>
            {#if devMissing}<div class="hint keep warn">Das gewählte Gerät ist nicht verbunden — gespielt wird über den Windows-Standard, bis es wieder da ist.</div>{/if}
            <div class="row">
              <span class="lbl">Lautstärke</span>
              <input type="range" min="0" max="100" value={$settings.volume}
                oninput={(e) => send({ type: 'set_volume', value: +e.target.value })} />
              <span class="val">{$settings.volume}</span>
            </div>
            <div class="hint">Die ganze Wiedergabe läuft über das gewählte Gerät, z. B. ein USB-Audio-Interface. Fehlt es (Box aus, abgesteckt), spielt SynthiMIX über den Windows-Standard weiter und schaltet zurück, sobald es wieder da ist.</div>
          </div>

          <div class="group">
            <div class="group-title">Sanft bedienen</div>
            <div class="row">
              <span class="lbl">Pause ausblenden</span>
              <input type="range" min="0" max="2000" step="100" value={$appSettings.pauseFadeMs ?? 500}
                     oninput={(e) => appSettings.update(s => ({ ...s, pauseFadeMs: +e.target.value }))} />
              <span class="val">{($appSettings.pauseFadeMs ?? 500) === 0 ? 'aus' : (($appSettings.pauseFadeMs ?? 500) / 1000).toFixed(1) + ' s'}</span>
            </div>
            <div class="row">
              <span class="lbl">Lautstärke glätten</span>
              <input type="range" min="0" max="1000" step="50" value={$appSettings.volumeFadeMs ?? 200}
                     oninput={(e) => appSettings.update(s => ({ ...s, volumeFadeMs: +e.target.value }))} />
              <span class="val">{($appSettings.volumeFadeMs ?? 200) === 0 ? 'aus' : (($appSettings.volumeFadeMs ?? 200) / 1000).toFixed(2) + ' s'}</span>
            </div>
            <div class="hint">Bei Pause blendet der Titel aus statt abrupt zu stoppen, beim Fortsetzen wieder ein. Lautstärke-Änderungen gleiten statt zu springen.</div>
          </div>

          <div class="group">
            <div class="group-title">Normalisierung</div>
            <div class="row">
              <span class="lbl">Lautstärke angleichen</span>
              <button class="tog {$appSettings.normalizeVolume ? 'on' : ''}"
                onclick={() => { const v = !$appSettings.normalizeVolume; appSettings.update(s => ({...s, normalizeVolume: v})); send({type:'set_normalize_volume', value: v}) }}
                title={$appSettings.normalizeVolume ? 'Aktiv' : 'Inaktiv'}></button>
            </div>
            {#if $appSettings.normalizeVolume}
              <div class="row indent">
                <span class="lbl">Ziel-LUFS</span>
                <input type="range" min="-23" max="-5" step="1" value={$appSettings.targetLUFS}
                  oninput={(e) => { const v = +e.target.value; appSettings.update(s => ({...s, targetLUFS: v})) }}
                  onchange={(e) => send({type:'set_normalize_volume', value: $appSettings.normalizeVolume, target_lufs: +e.target.value})} />
                <span class="val">{$appSettings.targetLUFS} LUFS</span>
              </div>
            {/if}
            <div class="row">
              <span class="lbl">Beim Übergang angleichen</span>
              <button class="tog {$appSettings.cfLoudMatch !== false ? 'on' : ''}"
                onclick={() => appSettings.update(s => ({...s, cfLoudMatch: s.cfLoudMatch === false}))}
                title={$appSettings.cfLoudMatch !== false ? 'Aktiv' : 'Inaktiv'}></button>
            </div>
            <div class="hint">Gemessen wird der Hauptteil eines Titels (Drops, Refrains), nicht der ganze Song — ruhige Intros und Breakdowns ziehen den Wert nicht mehr nach unten. Beim Übergang steigt der neue Titel so laut ein, wie der alte gerade klingt, und gleitet dann auf seinen normalen Pegel.</div>
            <div class="hint">Den Stand im Player („≋ −8.1 → {$appSettings.targetLUFS} LUFS“) blendest du unter Darstellung → Player ein. Auf der Fernbedienung lässt sich die Angleichung weiter schalten.</div>
          </div>

          <div class="group">
            <div class="group-title">BPM-Schätzung</div>
            <div class="row">
              <span class="lbl">BPM beim Abspielen analysieren</span>
              <button class="tog {$appSettings.bpmAnalysis ? 'on' : ''}" aria-label="BPM beim Abspielen analysieren"
                onclick={() => {
                  const next = !$appSettings.bpmAnalysis
                  appSettings.update(s => ({...s, bpmAnalysis: next}))
                  send({ type: 'set_bpm_analysis', enabled: next })
                }}></button>
            </div>
            <div class="hint">Berechnet BPM während der Wiedergabe. Deaktivieren spart etwas CPU.</div>
          </div>

          <div class="group">
            <div class="group-title">Queue-Ende</div>
            <div class="row">
              <span class="lbl">Wenn die Warteschlange leer ist</span>
              <div class="btn-group">
                {#each [['stop','Stopp'],['automix','Radio'],['repeat','Wiederholen']] as [val, label]}
                  <button class="btn btn-sm" class:is-active={queueEnd === val}
                          onclick={() => setQueueEnd(val)}>
                    {label}
                  </button>
                {/each}
              </div>
            </div>
            <div class="hint keep sub">
              {#if queueEnd === 'stop'}Wiedergabe endet nach dem letzten Track
              {:else if queueEnd === 'automix'}Spielt passende Titel weiter — meist aus der Bibliothek, ab und zu etwas Neues
              {:else}Queue startet von vorne (alle Tracks){/if}
            </div>
          </div>

        <!-- ── BLEND ───────────────────────────────────────────────────── -->
        {:else if tab === 'fade'}

          <div class="group">
            <div class="group-title">Übergangslänge</div>
            <div class="row">
              <span class="lbl">Messen in</span>
              <div class="btn-group">
                <button class="btn btn-sm" class:is-active={($appSettings.cfUnit ?? 'bars') === 'bars'}
                        onclick={() => appSettings.update(s => ({ ...s, cfUnit: 'bars' }))}>Takten</button>
                <button class="btn btn-sm" class:is-active={$appSettings.cfUnit === 'sec'}
                        onclick={() => appSettings.update(s => ({ ...s, cfUnit: 'sec' }))}>Sekunden</button>
              </div>
            </div>
            {#if ($appSettings.cfUnit ?? 'bars') === 'bars'}
              {@const bars = $appSettings.cfBars ?? 16}
              <div class="row">
                <span class="lbl">Länge in Takten</span>
                <div class="btn-group">
                  {#each CF_BARS as b}
                    <button class="btn btn-sm" class:is-active={bars === b}
                            onclick={() => appSettings.update(s => ({ ...s, cfBars: b }))}>{b === 0 ? 'aus' : b}</button>
                  {/each}
                </div>
              </div>
              <div class="hint keep sub">
                {#if bars === 0}Kein Übergang — es wird direkt umgeschaltet.
                {:else}Bei 128 BPM etwa {barSec(bars, 128)} s, bei 174 BPM etwa {barSec(bars, 174)} s. Titel ohne bekanntes Tempo: {$settings.crossfade_s || 8} s.{/if}
              </div>
            {:else}
              <div class="row">
                <span class="lbl">Dauer</span>
                <input type="range" min="0" max="15" step="1" value={$settings.crossfade_s}
                  onchange={(e) => send({ type: 'set_crossfade', seconds: +e.target.value })}
                  oninput={(e) => settings.update(s => ({ ...s, crossfade_s: +e.target.value }))} />
                <span class="val">{$settings.crossfade_s === 0 ? 'aus' : $settings.crossfade_s + ' s'}</span>
              </div>
            {/if}
            <div class="hint">Wie lange der Übergang zwischen zwei Titeln dauert. In Takten ist er bei jedem Tempo musikalisch gleich lang (16 Takte = eine typische Phrase); das Tempo kommt aus dem gemessenen Taktraster, sonst aus dem BPM-Tag.</div>
          </div>

          <div class="group">
            <div class="group-title">Überblend-Kurve</div>
            <div class="row">
              <span class="lbl">Lautstärkeverlauf</span>
              <div class="btn-group">
                {#each [['cosine','Kosinus'],['linear','Linear'],['scurve','S-Kurve']] as [val, label]}
                  <button class="btn btn-sm" class:is-active={$appSettings.cfCurve === val}
                          onclick={() => appSettings.update(s => ({...s, cfCurve: val}))}>
                    {label}
                  </button>
                {/each}
              </div>
            </div>
            <div class="hint keep sub">
              {#if $appSettings.cfCurve === 'cosine'}Weiche Sinuskurve — natürlichster Klang, empfohlen
              {:else if $appSettings.cfCurve === 'linear'}Gleichmäßiger Abfall — direkter, teils hörbar
              {:else}Starke S-Kurve — lange stille Mitte, harte Ein- und Ausgänge{/if}
            </div>
            <BlendSketch kind="curve" value={$appSettings.cfCurve ?? 'cosine'} />
          </div>

          <div class="group">
            <div class="group-title">Intelligenter Fade</div>
            <div class="hint">Analysiert die Waveform automatisch und zeigt graue Balken: das Intro (Track startet erst beim Beat) und die Mix-Zone am Outro (wo der Übergang läuft).</div>

            <div class="row">
              <span class="lbl">Aktiv</span>
              <button class="tog {$appSettings.smartFade ? 'on' : ''}" aria-label="Intelligenter Fade aktiv"
                onclick={() => appSettings.update(s => ({...s, smartFade: !s.smartFade}))}></button>
            </div>

            <!-- INTRO -->
            <div class="row {$appSettings.smartFade ? '' : 'dimmed'}">
              <span class="lbl" title="Wie viel vom erkannten Intro übersprungen wird. Sanft = nur ein Teil, Stark = das ganze Intro bis zum Beat.">Intro überspringen</span>
              <input type="range" min="1" max="5" step="1"
                     value={$appSettings.introAggressiveness ?? 3}
                     disabled={!$appSettings.smartFade}
                     oninput={(e) => appSettings.update(s => ({...s, introAggressiveness: +e.target.value}))} />
              <span class="val agg">
                {(['Sehr sanft','Sanft','Mittel','Stark','Sehr stark'])[($appSettings.introAggressiveness ?? 3) - 1]}
              </span>
            </div>
            <div class="hint keep sub {$appSettings.smartFade ? '' : 'dimmed'}">
              {#if ($appSettings.introAggressiveness ?? 3) === 1}
                Sehr sanft — überspringt nur ~30% des erkannten Intros
              {:else if ($appSettings.introAggressiveness ?? 3) === 2}
                Sanft — überspringt ~50% des Intros
              {:else if ($appSettings.introAggressiveness ?? 3) === 3}
                Mittel — überspringt ~70% des Intros
              {:else if ($appSettings.introAggressiveness ?? 3) === 4}
                Stark — überspringt ~85% des Intros
              {:else}
                Sehr stark — springt direkt zum Beat-Einsatz (ganzes Intro)
              {/if}
            </div>
            <BlendSketch kind="intro" value={$appSettings.introAggressiveness ?? 3} dimmed={!$appSettings.smartFade} />

            <!-- OUTRO -->
            <div class="row {$appSettings.smartFade ? '' : 'dimmed'}">
              <span class="lbl" title="Wie früh der Übergang vor der erkannten Outro-Stille beginnt. Stark = deutlich früher, längere Überlappung.">Outro-Start</span>
              <input type="range" min="1" max="5" step="1"
                     value={$appSettings.outroAggressiveness ?? 3}
                     disabled={!$appSettings.smartFade}
                     oninput={(e) => appSettings.update(s => ({...s, outroAggressiveness: +e.target.value}))} />
              <span class="val agg">
                {(['Sehr sanft','Sanft','Mittel','Stark','Sehr stark'])[($appSettings.outroAggressiveness ?? 3) - 1]}
              </span>
            </div>
            <div class="hint keep sub {$appSettings.smartFade ? '' : 'dimmed'}">
              {#if ($appSettings.outroAggressiveness ?? 3) === 1}
                Sehr sanft — Übergang startet genau am erkannten Stille-Punkt
              {:else if ($appSettings.outroAggressiveness ?? 3) === 2}
                Sanft — startet ~0.5× Blendzeit vor der Stille
              {:else if ($appSettings.outroAggressiveness ?? 3) === 3}
                Mittel — startet ~1× Blendzeit vor der Stille
              {:else if ($appSettings.outroAggressiveness ?? 3) === 4}
                Stark — startet ~1.5× Blendzeit vor der Stille
              {:else}
                Sehr stark — startet ~2× Blendzeit früher, lange Überlappung
              {/if}
            </div>
            <BlendSketch kind="outro" value={$appSettings.outroAggressiveness ?? 3} dimmed={!$appSettings.smartFade} />
          </div>

          <div class="group">
            <div class="group-title">Auf den Takt</div>
            <div class="hint">SynthiMIX misst für den laufenden und den nächsten Titel das genaue Tempo und wo die Schläge liegen (etwa 1 s je Titel).</div>
            <div class="row">
              <span class="lbl">Schläge übereinanderlegen</span>
              <button class="tog {$appSettings.beatAlignCf ? 'on' : ''}" aria-label="Schläge übereinanderlegen"
                onclick={() => appSettings.update(s => ({...s, beatAlignCf: !s.beatAlignCf}))}></button>
            </div>
            <div class="row">
              <span class="lbl">Tempo angleichen</span>
              <button class="tog {$appSettings.tempoMatch !== false ? 'on' : ''}" aria-label="Tempo angleichen"
                onclick={() => appSettings.update(s => ({...s, tempoMatch: s.tempoMatch === false}))}></button>
            </div>
            {#if $appSettings.tempoMatch !== false}
              {@const td = $appSettings.maxTempoDiff ?? 8}
              <div class="row indent">
                <span class="lbl">Tempo angleichen bis</span>
                <input type="range" min="4" max="25" step="1" value={td} aria-label="Größter Tempo-Unterschied"
                  oninput={(e) => appSettings.update(s => ({...s, maxTempoDiff: +e.target.value}))} />
                <span class="val">±{td} %</span>
              </div>
              <div class="hint keep sub">
                Bei 128 BPM: {Math.round(128 / (1 + td / 100))}–{Math.round(128 * (1 + td / 100))} BPM.
                {#if td > 12}Ab etwa 12 % hört man das Dehnen etwas (klingt leicht verwaschen).{:else}Bis hierhin fällt das Angleichen kaum auf.{/if}
              </div>
            {/if}
            <div class="hint">
              {#if $appSettings.beatAlignCf && $appSettings.tempoMatch !== false}
                Der Übergang beginnt auf einem Schlag. Beide Titel gehen im Tempo je zur Hälfte aufeinander zu (Tonhöhe bleibt), die Schläge werden laufend übereinandergezogen. Danach gleitet der neue Titel in etwa 20 s zurück aufs Original. Bei mehr als {$appSettings.maxTempoDiff ?? 8} % Tempo-Unterschied oder unklarem Takt (Live-Schlagzeug) wird nur geblendet.
              {:else if $appSettings.beatAlignCf}
                Der Übergang beginnt auf einem Schlag; ohne Tempo-Angleich halten die Schläge nur bei fast gleichem Tempo.
              {:else if $appSettings.tempoMatch !== false}
                Nur das Tempo wird angeglichen, die Schläge liegen nicht unbedingt übereinander.
              {:else}
                Der Übergang startet zum festen Zeitpunkt ohne Takt-Ausrichtung.
              {/if}
            </div>
            <BlendSketch kind="beat" value={$appSettings.beatAlignCf} />
          </div>

          <div class="group">
            <div class="group-title">Wie ein DJ</div>
            <div class="row">
              <span class="lbl">Auf Phrasen einrasten</span>
              <button class="tog {$appSettings.phraseAlign !== false ? 'on' : ''}" aria-label="Auf Phrasen einrasten"
                disabled={!$appSettings.beatAlignCf}
                onclick={() => appSettings.update(s => ({...s, phraseAlign: s.phraseAlign === false}))}></button>
            </div>
            <div class="hint">Der Übergang beginnt auf einem Phrasenanfang (alle 16 bzw. 8 Takte, meist dort, wo ein neuer Teil des Songs beginnt) und wird dafür höchstens 8 Takte verschoben. Der neue Titel steigt ebenfalls am Anfang einer Phrase ein, Eins auf Eins. Phrasen kommen aus den Cue-Punkten von Mixed In Key, sonst aus der eigenen Messung. Selbst gezogene Mix-Zonen rasten nur auf die nächste Eins.</div>
            <div class="row">
              <span class="lbl">Bass tauschen (EQ)</span>
              <button class="tog {$appSettings.bassSwap !== false ? 'on' : ''}" aria-label="Bass tauschen"
                disabled={!$appSettings.beatAlignCf}
                onclick={() => appSettings.update(s => ({...s, bassSwap: s.bassSwap === false}))}></button>
            </div>
            <div class="hint">Der neue Titel läuft zuerst ohne Bass mit; in der Mitte des Übergangs wird der Bass über 1–2 Takte weich getauscht — so wummern nie zwei Bassdrums gleichzeitig. Nur wenn der Übergang im Takt läuft; sonst wird wie bisher nur die Lautstärke geblendet.</div>
            {#if !$appSettings.beatAlignCf}<div class="hint keep warn">Braucht „Schläge übereinanderlegen“ (Auf den Takt).</div>{/if}
          </div>

          <div class="group">
            <div class="group-title">DJ-Modus</div>
            <div class="row">
              <span class="lbl">Kreative Übergänge</span>
              <button class="tog {$appSettings.djMode ? 'on' : ''}" aria-label="DJ-Modus"
                disabled={!$appSettings.beatAlignCf}
                onclick={() => appSettings.update(s => ({...s, djMode: !s.djMode}))}></button>
            </div>
            {#if $appSettings.djMode}
              <div class="row indent">
                <span class="lbl">Wie oft</span>
                <div class="btn-group">
                  {#each [[0.2, 'Selten'], [0.4, 'Ab und zu'], [0.7, 'Oft'], [1, 'Immer']] as [v, l]}
                    <button class="btn btn-sm" class:is-active={($appSettings.djAmount ?? 0.4) === v}
                            onclick={() => appSettings.update(s => ({ ...s, djAmount: v }))}>{l}</button>
                  {/each}
                </div>
              </div>
              {#each [['doubledrop', 'Double Drop'], ['eqmix', 'Langer EQ-Mix (32 Takte)'], ['filter', 'Filter-Übergang'], ['echo', 'Echo-Out'],
                      ['hall', 'Hall-Ausklang'], ['roll', 'Loop-Roll'], ['backspin', 'Backspin']] as [k, l]}
                <div class="row indent">
                  <span class="lbl">{l}</span>
                  <button class="tog {($appSettings.djTypes ?? {})[k] !== false ? 'on' : ''}" aria-label={l}
                    onclick={() => appSettings.update(s => ({ ...s, djTypes: { ...(s.djTypes ?? {}), [k]: (s.djTypes ?? {})[k] === false } }))}></button>
                </div>
              {/each}
            {/if}
            <div class="hint">Nur wenn Tempo und Takt beider Titel passen. Gewählt wird passend zur Situation: Echo-Out, Hall-Ausklang, Backspin oder Loop-Roll, wenn die Tonarten nicht zusammenpassen (dann klingen sie nie gleichzeitig) — Backspin und Loop-Roll kommen nur dann und bei großem Tempo-Unterschied; Double Drop ab und zu, wenn in beiden Titeln ein Drop erkannt ist (die Drops fallen genau zusammen, der Bass wird auf dem Drop getauscht); sonst vor allem der lange EQ-Mix oder Filter. Bei großem Tempo-Unterschied (ab 3 %) immer ein kurzer Schnitt — nie Double Drop oder lange Überblendung. Beim nächsten Titel im Player steht, was kommt — ein Klick darauf wählt eine andere Art.</div>
            {#if !$appSettings.beatAlignCf}<div class="hint keep warn">Braucht „Schläge übereinanderlegen“ (Auf den Takt).</div>{/if}
          </div>

        <!-- ── DOWNLOAD ────────────────────────────────────────────────── -->
        {:else if tab === 'download'}

          <div class="group">
            <div class="group-title">Speicherort</div>
            <div class="row">
              <span class="lbl ellip" title={$downloadDir || 'Standard: Downloads/'}>
                {$downloadDir ? $downloadDir.split(/[\\/]/).pop() || $downloadDir : 'Downloads/'}
              </span>
              <button class="btn btn-sm" onclick={pickDownloadFolder}>Ordner wählen</button>
            </div>
          </div>

          <div class="group">
            <div class="group-title">Dateiname</div>
            <div class="radio-group vertical">
              {#each [
                ['title',          'Titel',              '%(title)s'],
                ['uploader_title', 'Kanal – Titel',      '%(uploader)s – %(title)s'],
                ['artist_title',   'Künstler – Titel',   '%(artist)s – %(title)s'],
              ] as [val, label, example]}
                <button class="radio-opt-v {$dlFilenameFormat === val ? 'active' : ''}"
                        onclick={() => setFilenameFormat(val)}>
                  <span class="ro-label">{label}</span>
                  <span class="ro-example">{example}</span>
                </button>
              {/each}
            </div>
          </div>

          <div class="group">
            <div class="group-title">Playlist-Download</div>

            <div class="row">
              <span class="lbl">In eigenem Ordner speichern</span>
              <button class="tog {$playlistFolderEnabled ? 'on' : ''}" aria-label="In eigenem Ordner speichern"
                onclick={() => setPlaylistFolder(!$playlistFolderEnabled)}></button>
            </div>
            <div class="hint keep sub">
              {#if $playlistFolderEnabled}
                Downloads/&lt;Playlist-Name&gt;/Song.mp3
              {:else}
                Alle Downloads landen direkt im Download-Ordner
              {/if}
            </div>
            <div class="row">
              <span class="lbl">Gleichzeitige Downloads</span>
              <input type="range" min="1" max="6" step="1" value={$dlParallel} aria-label="Gleichzeitige Downloads"
                oninput={(e) => dlParallel.set(+e.target.value)}
                onchange={(e) => send({ type: 'set_dl_parallel', value: +e.target.value })} />
              <span class="val">{$dlParallel}</span>
            </div>
            <div class="hint">Wie viele Titel einer Playlist gleichzeitig geladen werden. Mehr ist schneller; bei zu vielen bremst YouTube eher oder verlangt eine Bestätigung. Standard: 3.</div>
          </div>

          <div class="group">
            <div class="group-title">Lautstärke beim Download normalisieren</div>

            <div class="row">
              <span class="lbl">Loudnorm aktiv</span>
              <button class="tog {$loudnormOnDl ? 'on' : ''}" aria-label="Loudnorm aktiv"
                onclick={() => { loudnormOnDl.update(v => !v); sendLoudnorm() }}></button>
            </div>
            <div class="hint">Fügt einen ffmpeg loudnorm-Pass zu yt-dlp hinzu (etwas langsamer)</div>

            {#if $loudnormOnDl}
              <div class="row indent">
                <span class="lbl">Ziel-LUFS</span>
                <input type="range" min="-23" max="-6" step="1" value={$loudnormTarget}
                  oninput={(e) => loudnormTarget.set(+e.target.value)} onchange={sendLoudnorm} />
                <span class="val">{$loudnormTarget} LUFS</span>
              </div>
              <div class="row indent">
                <span class="lbl">True Peak</span>
                <input type="range" min="-9" max="-0.5" step="0.5" value={$loudnormTp}
                  oninput={(e) => loudnormTp.set(+e.target.value)} onchange={sendLoudnorm} />
                <span class="val">{$loudnormTp} dBTP</span>
              </div>
            {/if}
          </div>

          <div class="group">
            {#snippet fRenameField(f)}
              <div class="follow-main">
                <input class="f-rename" bind:value={fRename.name} use:focusSelect aria-label="Eigener Name der Playlist" placeholder={f.title}
                       onkeydown={(e) => { if (e.key === 'Enter') commitFollowRename(); else if (e.key === 'Escape') { e.stopPropagation(); fRename = null } }}
                       onblur={commitFollowRename} />
                <span class="follow-meta">Enter speichert · leer = YouTube-Name „{f.title}“</span>
              </div>
            {/snippet}
            {#snippet ftView(f)}
              {@const d = $followTracks[f.url]}
              <div class="ftracks">
                {#if d === undefined}
                  <div class="ft-note">Lädt…</div>
                {:else if !d.items}
                  <div class="ft-note">Die Titelliste gibt es ab der nächsten Prüfung.
                    <button class="btn btn-sm" onclick={() => send({ type: 'follow_check', url: f.url })} disabled={f.checking}>Jetzt prüfen</button></div>
                {:else}
                  <div class="ft-sum">{#each ftSummary(d.items) as s}<span>{s}</span>{/each}</div>
                  <ol class="ft-list">
                    {#each d.items as it, i (i)}
                      <li class={it.s} title={FT[it.s]?.lbl}><i class="ti {FT[it.s]?.ico}" aria-hidden="true"></i><span>{it.t}</span></li>
                    {/each}
                  </ol>
                {/if}
              </div>
            {/snippet}
            <div class="group-title">Verfolgte Playlists und Kanäle</div>
            {#if fRenameErr}<div class="notice error" role="alert">{fRenameErr.text}</div>{/if}
            <div class="hint">Werden beim Start und auf Knopfdruck im Hintergrund geprüft; in den Downloads erscheint nur, was wirklich geladen wird. Klick auf eine Playlist zeigt ihre Titel. Ganzen Kanal verfolgen: Kanal-Link (z. B. youtube.com/@name) in die Download-Zeile einfügen.</div>
            {#each $followedChannels as c (c.url)}
              <div class="follow-list ch">
                <div class="follow-row">
                  <i class="ti ti-broadcast follow-ico" aria-hidden="true"></i>
                  <div class="follow-main">
                    <span class="follow-name" title={c.url}>{c.title}</span>
                    <span class="follow-meta">
                      {#if c.checking}<i class="ti ti-refresh spin" aria-hidden="true"></i>{' wird geprüft…'}{:else}{followAgo(c.last_check)}{/if}{` · ${c.playlists} Playlists`}
                    </span>
                  </div>
                  <label class="ch-auto" title="Neue Playlists des Kanals automatisch verfolgen und laden — aus: nur als Vorschlag">
                    <input type="checkbox" checked={c.auto_new} onchange={(e) => send({ type: 'channel_set', url: c.url, auto_new: e.currentTarget.checked })} /> neue automatisch
                  </label>
                  <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'channel_plan_open', url: c.url })}
                          title="Playlists auswählen" aria-label="Playlists auswählen"><i class="ti ti-list-check"></i></button>
                  <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'follow_check', url: c.url })} disabled={c.checking}
                          title="Kanal jetzt prüfen (neue Playlists und Titel)" aria-label="Kanal jetzt prüfen"><i class="ti ti-refresh"></i></button>
                  <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'channel_remove', url: c.url })}
                          title="Kanal und seine Playlists nicht mehr verfolgen (geladene Titel bleiben)" aria-label="Kanal nicht mehr verfolgen"><i class="ti ti-x"></i></button>
                </div>
                {#if c.pending?.length}
                  <div class="ch-pending">
                    <i class="ti ti-sparkles" aria-hidden="true"></i>
                    {c.pending.length} neue Playlist{c.pending.length > 1 ? 's' : ''}: {c.pending.slice(0, 3).map(p => p.title).join(', ')}{c.pending.length > 3 ? ' …' : ''}
                    <button class="btn btn-sm" onclick={() => send({ type: 'channel_plan_open', url: c.url })}>Auswählen</button>
                  </div>
                {/if}
                {#if c.playlists}
                  <button class="ch-expand" onclick={() => chToggle(c.url)} aria-expanded={chOpen.has(c.url)}>
                    <i class="ti ti-chevron-right fchev" class:open={chOpen.has(c.url)} aria-hidden="true"></i>
                    {chOpen.has(c.url) ? 'Playlists ausblenden' : `${c.playlists} Playlists anzeigen`}
                  </button>
                  {#if chOpen.has(c.url)}
                    {#each $followed.filter(f => f.channel === c.url) as f (f.url)}
                      <div class="follow-row sub">
                        {#if fRename?.url === f.url}
                          {@render fRenameField(f)}
                        {:else}
                        <button class="follow-main ftoggle" onclick={() => ftToggle(f.url)} aria-expanded={ftOpen.has(f.url)} title="Titel anzeigen">
                          <span class="follow-name" title={f.name ? `YouTube: ${f.title}` : f.url}><i class="ti ti-chevron-right fchev" class:open={ftOpen.has(f.url)} aria-hidden="true"></i> {f.name || f.title}</span>
                          <span class="follow-meta">
                            {#if f.checking}<i class="ti ti-refresh spin" aria-hidden="true"></i>{' wird geprüft…'}{:else}{followAgo(f.last_check)}{/if}{#if !f.checking && f.last_check && f.last_new}{' · '}<b>{f.last_new} neu</b>{/if}{` · ${f.known} bekannt`}
                          </span>
                          {#if !f.checking && f.last_result}<span class="follow-res">{f.last_result}</span>{/if}
                        </button>
                        {/if}
                        <button class="btn btn-icon btn-sm" onclick={() => startFollowRename(f)}
                                title="Eigenen Namen vergeben" aria-label="Eigenen Namen vergeben"><i class="ti ti-pencil"></i></button>
                        <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'follow_check', url: f.url })} disabled={f.checking}
                                title="Jetzt auf neue Titel prüfen" aria-label="Jetzt prüfen"><i class="ti ti-refresh"></i></button>
                        <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'follow_remove', url: f.url })}
                                title="Nicht mehr verfolgen (geladene Titel bleiben)" aria-label="Nicht mehr verfolgen"><i class="ti ti-x"></i></button>
                      </div>
                      {#if ftOpen.has(f.url)}{@render ftView(f)}{/if}
                    {/each}
                  {/if}
                {/if}
              </div>
            {/each}
            {#if soloFollowed.length}
              <div class="follow-list">
                {#each soloFollowed as f (f.url)}
                  <div class="follow-row">
                    <i class="ti ti-bookmark-filled follow-ico" aria-hidden="true"></i>
                    {#if fRename?.url === f.url}
                      {@render fRenameField(f)}
                    {:else}
                    <button class="follow-main ftoggle" onclick={() => ftToggle(f.url)} aria-expanded={ftOpen.has(f.url)} title="Titel anzeigen">
                      <span class="follow-name" title={f.name ? `YouTube: ${f.title}` : f.url}><i class="ti ti-chevron-right fchev" class:open={ftOpen.has(f.url)} aria-hidden="true"></i> {f.name || f.title}</span>
                      <span class="follow-meta">
                        {#if f.checking}<i class="ti ti-refresh spin" aria-hidden="true"></i>{' wird geprüft…'}{:else}{followAgo(f.last_check)}{/if}{#if !f.checking && f.last_check && f.last_new}{' · '}<b>{f.last_new} neu</b>{/if}{` · ${f.known} bekannt`}{FOLLOW_MODE[f.mode] ? ` · ${FOLLOW_MODE[f.mode]}` : ''}
                      </span>
                      {#if !f.checking && f.last_result}<span class="follow-res">{f.last_result}</span>{/if}
                    </button>
                    {/if}
                    <button class="btn btn-icon btn-sm" onclick={() => startFollowRename(f)}
                            title="Eigenen Namen vergeben" aria-label="Eigenen Namen vergeben"><i class="ti ti-pencil"></i></button>
                    <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'follow_check', url: f.url })} disabled={f.checking}
                            title="Jetzt auf neue Titel prüfen" aria-label="Jetzt prüfen"><i class="ti ti-refresh"></i></button>
                    <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'follow_remove', url: f.url })}
                            title="Nicht mehr verfolgen (geladene Titel bleiben)" aria-label="Nicht mehr verfolgen"><i class="ti ti-x"></i></button>
                  </div>
                  {#if ftOpen.has(f.url)}{@render ftView(f)}{/if}
                {/each}
              </div>
            {:else if !$followedChannels.length}
              <div class="follow-empty">Noch keine. Beim Laden einer Playlist „Playlist verfolgen“ anhaken, in den Downloads bei einer Playlist auf „Verfolgen“ klicken oder einen Kanal-Link einfügen.</div>
            {/if}
            {#if $followed.length || $followedChannels.length}
              <div>
                <button class="btn btn-sm" onclick={() => send({ type: 'follow_check' })}
                        disabled={$followed.length > 0 && $followed.every(f => f.checking)}><i class="ti ti-refresh"></i> Alle jetzt prüfen</button>
              </div>
            {/if}
          </div>

        <!-- ── DIENSTE ─────────────────────────────────────────────────── -->
        {:else if tab === 'services'}

          <div class="svc-intro">yt-dlp und ffmpeg braucht SynthiMIX selbst; die übrigen Dienste sind optional und schalten einzelne Funktionen frei.</div>
          <div class="svc-list">

            <div class="svc" class:open={svcOpen === 'ytdlp'}>
              <button class="svc-head" onclick={() => toggleCard('ytdlp')} aria-expanded={svcOpen === 'ytdlp'}>
                <i class="ti ti-download svc-ico" aria-hidden="true"></i>
                <span class="svc-name">yt-dlp<span class="svc-sub">lädt von YouTube, YouTube Music, SoundCloud …</span></span>
                <span class="svc-state {svcState.ytdlp.cls}">{svcState.ytdlp.text}</span>
                <i class="ti ti-chevron-down svc-chev" aria-hidden="true"></i>
              </button>
              {#if svcOpen === 'ytdlp'}
                <div class="svc-body">
                  <div class="row">
                    <span class="lbl">Version</span>
                    <span class="val {$toolsInfo.ytdlp_version ? 'st-ok' : ''}">{$toolsInfo.ytdlp_version ?? '—'}</span>
                    <button class="btn btn-sm {$toolUpdates.ytdlp?.available ? 'btn-primary' : ''}"
                            onclick={() => send({ type: 'update_ytdlp' })} disabled={!!$updateProgress}>
                      <i class="ti ti-refresh"></i> {$toolUpdates.ytdlp?.available ? `Auf ${$toolUpdates.ytdlp.latest} aktualisieren` : 'Aktualisieren'}
                    </button>
                  </div>
                  {#if $updateProgress && $updateProgress.tool !== 'ffmpeg'}<div class="svc-note">{$updateProgress.text}</div>{/if}
                  <div class="svc-note">Bei „403 Forbidden“ hilft meist ein Update.</div>
                </div>
              {/if}
            </div>

            <div class="svc" class:open={svcOpen === 'ffmpeg'}>
              <button class="svc-head" onclick={() => toggleCard('ffmpeg')} aria-expanded={svcOpen === 'ffmpeg'}>
                <i class="ti ti-wave-sine svc-ico" aria-hidden="true"></i>
                <span class="svc-name">ffmpeg<span class="svc-sub">Umwandeln, Lautheit, Waveform, Analyse</span></span>
                <span class="svc-state {svcState.ffmpeg.cls}">{svcState.ffmpeg.text}</span>
                <i class="ti ti-chevron-down svc-chev" aria-hidden="true"></i>
              </button>
              {#if svcOpen === 'ffmpeg'}
                <div class="svc-body">
                  <div class="row">
                    <span class="lbl">Version</span>
                    <span class="val {$toolsInfo.ffmpeg_version ? 'st-ok' : ''}" title={$toolsInfo.ffmpeg_version ?? ''}>{fmtFfmpeg($toolsInfo.ffmpeg_version) || '—'}</span>
                    <button class="btn btn-sm {$toolUpdates.ffmpeg?.available ? 'btn-primary' : ''}"
                            onclick={() => send({ type: 'update_ffmpeg' })} disabled={!!$updateProgress}
                            title="Lädt den neuesten Build (BtbN, ca. 100–200 MB) und schaltet erst um, wenn er läuft">
                      <i class="ti ti-refresh"></i> {$toolUpdates.ffmpeg?.available ? `Build vom ${fmtBuild($toolUpdates.ffmpeg.latest)} laden` : 'Aktualisieren'}
                    </button>
                  </div>
                  {#if $updateProgress?.tool === 'ffmpeg'}<div class="svc-note">{$updateProgress.text}</div>{/if}
                  <div class="svc-note">Mitgeliefert mit SynthiMIX. Neue Builds kommen in den Datenordner (tools/) und werden nur benutzt, wenn sie starten.</div>
                </div>
              {/if}
            </div>

            <div class="svc" class:open={svcOpen === 'spotify'}>
              <button class="svc-head" onclick={() => toggleCard('spotify')} aria-expanded={svcOpen === 'spotify'}>
                <i class="ti ti-brand-spotify svc-ico" aria-hidden="true"></i>
                <span class="svc-name">Spotify<span class="svc-sub">Genres · Spotify-Links laden auch ohne Zugangsdaten</span></span>
                <span class="svc-state {svcState.spotify.cls}">{svcState.spotify.text}</span>
                <i class="ti ti-chevron-down svc-chev" aria-hidden="true"></i>
              </button>
              {#if svcOpen === 'spotify'}
                <div class="svc-body">
                  <div class="svc-note">Spotify-Links (Titel, Album, Playlist) einfach bei den Downloads einfügen — SynthiMIX liest die Titelliste und lädt die Songs über YouTube Music. Dafür ist hier nichts einzurichten.</div>
                  <div class="row">
                    <span class="lbl">Client-ID <span class="opt">optional</span></span>
                    <input class="field field-sm" type="text" bind:value={spotifyCidEdit} onchange={saveSpotifyCreds} />
                  </div>
                  <div class="row">
                    <span class="lbl">Client-Secret <span class="opt">optional</span></span>
                    <input class="field field-sm" type="password" bind:value={spotifyCsecEdit} onchange={saveSpotifyCreds} />
                  </div>
                  <div class="svc-note">Client-ID und Secret (developer.spotify.com) braucht nur die Genre-Abfrage über Spotify.</div>
                  {#if $servicesTest?.spotify}<div class="svc-note {$servicesTest.spotify.ok ? 'ok' : 'err'}">{$servicesTest.spotify.ok ? '✓' : '✗'} {$servicesTest.spotify.text}</div>{/if}
                </div>
              {/if}
            </div>

            <div class="svc" class:open={svcOpen === 'lastfm'}>
              <button class="svc-head" onclick={() => toggleCard('lastfm')} aria-expanded={svcOpen === 'lastfm'}>
                <i class="ti ti-brand-lastfm svc-ico" aria-hidden="true"></i>
                <span class="svc-name">Last.fm<span class="svc-sub">Radio-Modus · Schreibweise beim Titel-Aufräumen · Genres</span></span>
                <span class="svc-state {svcState.lastfm.cls}">{svcState.lastfm.text}</span>
                <i class="ti ti-chevron-down svc-chev" aria-hidden="true"></i>
              </button>
              {#if svcOpen === 'lastfm'}
                <div class="svc-body">
                  <div class="row">
                    <span class="lbl">API-Key</span>
                    <input class="field field-sm" type="text" placeholder="32 Zeichen"
                           bind:value={lastfmKeyEdit} oninput={() => servicesDirty = true} onchange={saveServices} />
                  </div>
                  <div class="svc-note">Kostenlos unter <strong>last.fm/api/account/create</strong>.</div>
                  {#if $servicesTest?.lastfm}<div class="svc-note {$servicesTest.lastfm.ok ? 'ok' : 'err'}">{$servicesTest.lastfm.ok ? '✓' : '✗'} {$servicesTest.lastfm.text}</div>{/if}
                  <div class="svc-actions">
                    <button class="btn btn-sm" onclick={testServices} disabled={testing}>
                      <i class="ti {testing ? 'ti-refresh spin' : 'ti-plug'}"></i> {testing ? 'Teste…' : 'Testen'}
                    </button>
                  </div>
                </div>
              {/if}
            </div>

            <div class="svc" class:open={svcOpen === 'acoustid'}>
              <button class="svc-head" onclick={() => toggleCard('acoustid')} aria-expanded={svcOpen === 'acoustid'}>
                <i class="ti ti-fingerprint svc-ico" aria-hidden="true"></i>
                <span class="svc-name">AcoustID<span class="svc-sub">Titel am Klang erkennen (Fingerprint)</span></span>
                <span class="svc-state {svcState.acoustid.cls}">{svcState.acoustid.text}</span>
                <i class="ti ti-chevron-down svc-chev" aria-hidden="true"></i>
              </button>
              {#if svcOpen === 'acoustid'}
                <div class="svc-body">
                  <div class="row">
                    <span class="lbl">Programm fpcalc</span>
                    {#if $toolsInfo.fpcalc_found}
                      <span class="val st-ok">Gefunden</span>
                    {:else if $fpcalcInstalling}
                      <span class="val st-busy">Wird installiert…</span>
                    {:else}
                      <button class="btn btn-sm btn-primary" onclick={() => send({ type: 'download_fpcalc' })}>Installieren</button>
                    {/if}
                  </div>
                  {#if $fpcalcInstallError}<div class="svc-note err">{$fpcalcInstallError}</div>{/if}
                  <div class="row">
                    <span class="lbl">Application-Key</span>
                    <input class="field field-sm" type="text" placeholder="AcoustID Application-Key"
                           bind:value={acoustidKeyEdit} oninput={() => servicesDirty = true} onchange={saveServices} />
                  </div>
                  <div class="svc-note">acoustid.org → anmelden → <strong>„Register your application“</strong>. Der Key von deiner Benutzerseite funktioniert hier nicht.</div>
                  {#if $servicesTest?.acoustid}<div class="svc-note {$servicesTest.acoustid.ok ? 'ok' : 'err'}">{$servicesTest.acoustid.ok ? '✓' : '✗'} {$servicesTest.acoustid.text}</div>{/if}
                  <div class="svc-actions">
                    <button class="btn btn-sm" onclick={testServices} disabled={testing}>
                      <i class="ti {testing ? 'ti-refresh spin' : 'ti-plug'}"></i> {testing ? 'Teste…' : 'Testen'}
                    </button>
                  </div>
                </div>
              {/if}
            </div>
          </div>

          <div class="group svc-auto">
            {#if $toolUpdates.ytdlp_updated || $toolUpdates.ffmpeg_updated}
              <div class="notice ok upd-note">
                Automatisch aktualisiert:
                {[$toolUpdates.ytdlp_updated && `yt-dlp ${$toolUpdates.ytdlp_updated.from} → ${$toolUpdates.ytdlp_updated.to}`,
                  $toolUpdates.ffmpeg_updated && `ffmpeg ${fmtBuild($toolUpdates.ffmpeg_updated.from)} → ${fmtBuild($toolUpdates.ffmpeg_updated.to)}`]
                  .filter(Boolean).join(' · ')}
                <button class="btn btn-sm" onclick={() => send({ type: 'dismiss_ytdlp_updated' })}>OK</button>
              </div>
            {/if}
            <div class="row">
              <span class="lbl">yt-dlp und ffmpeg automatisch aktuell halten</span>
              <button class="tog {$ytdlpAutoupdate ? 'on' : ''}" aria-label="yt-dlp und ffmpeg automatisch aktuell halten"
                onclick={() => send({ type: 'set_ytdlp_autoupdate', value: !$ytdlpAutoupdate })}></button>
            </div>
            <div class="row">
              <span class="lbl">Jetzt nach neuen Versionen suchen</span>
              <button class="btn btn-sm" onclick={checkToolUpdates} disabled={toolCheckRunning}>
                <i class="ti ti-refresh" class:spin={toolCheckRunning}></i> {toolCheckRunning ? 'Prüfe…' : 'Jetzt prüfen'}
              </button>
            </div>
            <div class="hint">Täglich (ffmpeg wöchentlich), nie während eines Downloads. SynthiMIX selbst: System → Updates.</div>
          </div>

        <!-- ── DARSTELLUNG ─────────────────────────────────────────────── -->
        {:else if tab === 'look'}

          <div class="group">
            <div class="group-title">Theme</div>
            <div class="row">
              <span class="lbl">Farben</span>
              <div class="btn-group">
                <button class="btn btn-sm" class:is-active={$theme === 'dark'} onclick={() => theme.set('dark')}><i class="ti ti-moon"></i> Dunkel</button>
                <button class="btn btn-sm" class:is-active={$theme === 'light'} onclick={() => theme.set('light')}><i class="ti ti-sun"></i> Hell</button>
              </div>
            </div>
            <div class="hint">Hell ist für draußen bei Sonne gedacht. Umschalten geht auch oben in der Titelleiste (Sonne/Mond).</div>
          </div>

          <div class="group">
            <div class="group-title">Dichte</div>
            <div class="row">
              <span class="lbl">Bibliothek und Queue</span>
              <div class="btn-group">
                <button class="btn btn-sm" class:is-active={$density === 'compact'} onclick={() => density.set('compact')}><i class="ti ti-baseline-density-small"></i> Kompakt</button>
                <button class="btn btn-sm" class:is-active={$density === 'comfortable'} onclick={() => density.set('comfortable')}><i class="ti ti-baseline-density-large"></i> Komfortabel</button>
              </div>
            </div>
            <div class="hint">
              {#if $density === 'compact'}Kompakt: niedrige Zeilen, viele Titel auf einmal — gut zum Aufräumen zuhause.
              {:else}Komfortabel: größere Zeilen, Schrift und Klickziele — gut aus Abstand und live am Laptop.{/if}
            </div>
          </div>

          <div class="group">
            <div class="group-title">Player</div>
            <div class="row">
              <span class="lbl">Lautheit (LUFS) anzeigen</span>
              <button class="tog {$appSettings.playerShowLufs ? 'on' : ''}" aria-label="Lautheit im Player anzeigen"
                onclick={() => appSettings.update(s => ({ ...s, playerShowLufs: !s.playerShowLufs }))}></button>
            </div>
            <div class="hint">Zeigt neben BPM, wie laut der Titel ist und worauf er angeglichen wird, z. B. „≋ −8.1 → −10 LUFS“.</div>
          </div>

          <div class="group">
            <div class="group-title">Tonart</div>
            <div class="row">
              <span class="lbl">Schreibweise</span>
              <div class="btn-group">
                <button class="btn btn-sm" class:is-active={($appSettings.keyNotation ?? 'musical') === 'musical'}
                        onclick={() => appSettings.update(s => ({ ...s, keyNotation: 'musical' }))}>Noten</button>
                <button class="btn btn-sm" class:is-active={$appSettings.keyNotation === 'camelot'}
                        onclick={() => appSettings.update(s => ({ ...s, keyNotation: 'camelot' }))}>Camelot</button>
              </div>
            </div>
            <div class="row key-preview">
              <span class="lbl">Vorschau</span>
              <span class="key-samples">
                <KeyChip key="Am" src="tag" />
                <KeyChip key="C" src="tag" />
                <KeyChip key="F♯m" src="tag" />
                <KeyChip key="D♯" src="analyse" />
              </span>
            </div>
            <div class="hint">
              Noten wie in rekordbox („F♯m“), Camelot wie in Mixed In Key („11A“). Die Farbe
              gehört zur Camelot-Nummer, passende Tonarten stehen damit farblich nah beieinander.
              Kursiv mit „~“: selbst geschätzt, nicht aus dem Tag.
            </div>
          </div>

        <!-- ── SYSTEM ──────────────────────────────────────────────────── -->
        {:else if tab === 'system'}

          {#if $dataPlace.dir}
            <div class="group">
              <div class="group-title">Speicherort</div>
              <div class="row"><span class="lbl">{$dataPlace.dir}</span></div>
              {#if $dataPlace.on}
                <div class="hint keep">Tragbarer Betrieb: Bibliothek, Einstellungen und Playlists liegen neben dem Programm auf dieser Festplatte und reisen mit. Bekommt die Platte an einem anderen PC einen anderen Laufwerksbuchstaben, stellt SynthiMIX die Pfade beim Start von selbst um.</div>
                {#if portInfo?.pcHasData}
                  <div class="row">
                    <span class="lbl">Auf diesem PC liegen eigene Daten{portInfo.pcTracks != null ? ` (${portInfo.pcTracks} Titel` + (portInfo.driveTracks != null ? `, auf der Festplatte ${portInfo.driveTracks}` : '') + ')' : ''}</span>
                    {#if !takeAsk}
                      <button class="btn btn-sm" onclick={() => takeAsk = true}><i class="ti ti-download"></i> Daten dieses PCs übernehmen</button>
                    {:else}
                      <span class="row-acts">
                        <button class="btn btn-sm btn-primary" onclick={takeOverPc}>Ja, übernehmen und neu starten</button>
                        <button class="btn btn-sm" onclick={() => takeAsk = false}>Abbrechen</button>
                      </span>
                    {/if}
                  </div>
                  <div class="hint">Holt Bibliothek, Einstellungen und Warteschlange dieses PCs auf die Festplatte — gedacht für den Fall, dass SynthiMIX zuerst an einem anderen PC gestartet wurde. Was jetzt auf der Festplatte liegt, bleibt im Datenordner unter „Sicherung …“ erhalten. SynthiMIX startet dafür neu.</div>
                {/if}
              {:else}
                <div class="hint keep">Bibliothek, Einstellungen und Playlists liegen auf diesem PC. Ist SynthiMIX auf einer anderen Festplatte als dem Systemlaufwerk installiert, speichert es alles dort neben dem Programm (Ordner „SynthiMIX-Daten“) — die Platte lässt sich dann an jedem PC anstecken.</div>
              {/if}
            </div>
          {/if}

          {#if canBackup}
            <div class="group">
              <div class="group-title">Sicherung</div>
              <div class="row">
                <span class="lbl">Alles in eine Datei sichern</span>
                <span class="row-acts">
                  <button class="btn btn-sm" onclick={makeBackup} disabled={$backupState?.step === 'busy'}><i class="ti ti-download"></i> Sicherung erstellen …</button>
                  <button class="btn btn-sm" onclick={pickRestore} disabled={$backupState?.step === 'busy'}><i class="ti ti-refresh"></i> Wiederherstellen …</button>
                </span>
              </div>
              {#if $backupState?.step === 'busy'}
                <div class="hint keep">Einen Moment …</div>
              {:else if $backupState?.step === 'done'}
                <div class="hint keep">Gesichert: {$backupState.tracks} Titel, {$backupState.playlists} Playlists → {$backupState.path}</div>
              {:else if $backupState?.step === 'ask'}
                <div class="hint keep">Sicherung vom {fmtBackupDate($backupState.info.created)}: {$backupState.info.tracks} Titel, {$backupState.info.playlists} Playlists. Wiederherstellen ersetzt Bibliothek, Einstellungen, Warteschlange und Verlauf; die jetzigen Daten bleiben im Datenordner unter „Sicherung …“. SynthiMIX startet dafür neu.</div>
                <div class="row">
                  <span class="lbl"></span>
                  <span class="row-acts">
                    <button class="btn btn-sm btn-primary" onclick={doRestore}>Ja, wiederherstellen und neu starten</button>
                    <button class="btn btn-sm" onclick={() => backupState.set(null)}>Abbrechen</button>
                  </span>
                </div>
              {:else if $backupState?.step === 'restarting'}
                <div class="hint keep">SynthiMIX startet neu …</div>
              {:else if $backupState?.step === 'error'}
                <div class="hint keep">Nicht möglich: {$backupState.error}</div>
              {/if}
              <div class="hint">Enthält die Bibliothek mit allen Analysen, Warteschlange, Verlauf, Playlists, verfolgte Playlists, alle Einstellungen und die Schlüssel der Dienste — nicht die Musikdateien. Für eine Neuinstallation oder einen anderen PC. Die Datei enthält deine Schlüssel im Klartext: nicht weitergeben.</div>
            </div>
          {/if}

          <div class="group">
            <div class="group-title">Diagnose</div>
            <div class="row">
              <span class="lbl">Geht etwas nicht (Waveform, Einlesen, Sortieren)?</span>
              <button class="btn btn-sm" onclick={runDiagnose} disabled={$diagnoseResult === 'busy'}>
                <i class="ti ti-refresh" class:spin={$diagnoseResult === 'busy'}></i> Prüfen
              </button>
            </div>
            {#if $diagnoseResult && $diagnoseResult !== 'busy'}
              <div class="diag">
                {#each $diagnoseResult.checks as c}
                  <div class="diag-row" class:bad={!c.ok}>
                    <i class="ti {c.ok ? 'ti-check' : 'ti-alert-triangle'}"></i>
                    <b>{c.name}</b><span>{c.text}</span>
                  </div>
                {/each}
              </div>
              <div class="row">
                <span class="lbl">{$diagnoseResult.ok ? 'Alles in Ordnung.' : 'Es gibt Auffälligkeiten.'}</span>
                <button class="btn btn-sm" onclick={copyDiagnose}><i class="ti ti-copy"></i> {diagCopied ? 'Kopiert' : 'Bericht kopieren'}</button>
              </div>
            {/if}
            <div class="hint">Prüft, ob ffmpeg und die Analyse auf diesem PC laufen, ob Ordner erreichbar und lesbar sind und ob sich eine Waveform berechnen lässt. Abspielen macht das Fenster selbst — Waveform, Einlesen und Sortieren brauchen diese Werkzeuge.</div>
          </div>

          <div class="group">
            <div class="group-title">Updates</div>
            <div class="row">
              <span class="lbl">SynthiMIX v{APP_VERSION}</span>
              <button class="btn btn-sm" onclick={checkUpdate} disabled={updCheck === 'busy'}>
                <i class="ti ti-refresh" class:spin={updCheck === 'busy'}></i> Nach Updates suchen
              </button>
            </div>
            {#if updCheck && updCheck !== 'busy'}
              {#if updCheck.status === 'available'}
                <div class="hint keep">v{updCheck.version} ist da — das Update-Fenster oben zeigt, was neu ist. Herunterladen läuft im Hintergrund, installiert wird auf Knopfdruck oder beim nächsten Beenden.</div>
              {:else if updCheck.status === 'none'}
                <div class="hint keep">Du hast die neueste Version.</div>
              {:else if updCheck.status === 'dev'}
                <div class="hint keep">Nur in der installierten App.</div>
              {:else}
                <div class="hint keep warn">Prüfen ging nicht: {updCheck.message}</div>
              {/if}
            {/if}
            <div class="hint">Geprüft wird auch von selbst: beim Start und alle 6 Stunden.</div>
          </div>

          <div class="group">
            <div class="group-title">Bibliothek</div>
            <div class="row">
              <span class="lbl">Auto-Scan</span>
              <div class="btn-group">
                {#each AUTO_SCAN_OPTIONS as opt}
                  <button class="btn btn-sm" class:is-active={$autoScanIntervalMin === opt.value}
                          onclick={() => setAutoScanInterval(opt.value)}>
                    {opt.label}
                  </button>
                {/each}
              </div>
            </div>
            <p class="hint">Bibliotheksordner automatisch neu scannen</p>
            <div class="row">
              <span class="lbl">Unterordner beim Scannen einschließen</span>
              <button class="tog {$scanRecursive ? 'on' : ''}" aria-label="Unterordner beim Scannen einschließen"
                onclick={() => send({ type: 'set_scan_recursive', enabled: !$scanRecursive })}></button>
            </div>

            <div class="row">
              <span class="lbl">Laufwerk gewechselt?</span>
              <button class="btn btn-sm" onclick={() => { relocateState.set(null); send({ type: 'relocate_detect' }) }}>
                <i class="ti ti-folder-search"></i> Fehlende Titel suchen
              </button>
            </div>
            {#if $relocateState?.none}<p class="hint keep">Nichts gefunden — entweder fehlt nichts, oder die Titel liegen nicht einfach unter einem anderen Laufwerksbuchstaben.</p>{/if}
            <p class="hint">Hat sich der Buchstabe der Musik-Platte geändert, stellt SynthiMIX alle Pfade um — BPM, Tonart und Wiedergaben bleiben. Vorher keinen beobachteten Ordner entfernen.</p>

            <div class="wf-head">Beobachtete Ordner</div>
            {#if !$watchedFolders.length}
              <p class="hint">Noch keine. Ordner über „Scannen" in der Bibliothek oder hier hinzufügen.</p>
            {/if}
            {#each $watchedFolders as wf (wf.path)}
              <div class="wf-row">
                <div class="wf-info">
                  <span class="wf-path" title={wf.path}>{wf.path}</span>
                  <span class="wf-meta">
                    {wf.tracks} Titel
                    {#if !wf.exists}<span class="wf-warn"> · Ordner nicht gefunden</span>{/if}
                    {#if wf.inside}<span class="wf-warn" title={wf.inside}> · liegt in einem anderen beobachteten Ordner und wird dort schon mitgescannt — kann weg</span>{/if}
                  </span>
                  {#if pendingRemove?.folder === wf.path}
                    <span class="wf-confirm">
                      {#if pendingRemove.tracks === null}prüfe…
                      {:else if pendingRemove.tracks === 0}Aus der Liste entfernen — kein Titel verschwindet aus der Bibliothek.
                      {:else}{pendingRemove.tracks} Titel verschwinden aus der Bibliothek. Die Dateien bleiben auf der Platte.{/if}
                    </span>
                  {/if}
                </div>
                {#if pendingRemove?.folder === wf.path}
                  <button class="btn btn-sm btn-danger" disabled={pendingRemove.tracks === null}
                          onclick={confirmRemoveFolder}>Entfernen</button>
                  <button class="btn btn-sm" onclick={() => pendingRemove = null}>Abbrechen</button>
                {:else}
                  <button class="btn btn-sm" onclick={() => askRemoveFolder(wf.path)}>Entfernen</button>
                {/if}
              </div>
            {/each}
            <div class="row">
              <span class="lbl"></span>
              <button class="btn btn-sm" onclick={addWatchedFolder}><i class="ti ti-folder-plus"></i> Ordner hinzufügen</button>
            </div>

            {#if $excludedFolders.length}
              <div class="wf-head">Ausgeschlossene Ordner</div>
              <p class="hint">Titel aus diesen Ordnern holt die Bibliothek nicht mehr herein.</p>
              {#each $excludedFolders as ex (ex)}
                <div class="wf-row">
                  <div class="wf-info"><span class="wf-path" title={ex}>{ex}</span></div>
                  <button class="btn btn-sm" onclick={() => send({ type: 'include_folder', folder: ex })}>Wieder aufnehmen</button>
                </div>
              {/each}
            {/if}
          </div>

          <div class="group">
            <div class="group-title">Konfiguration</div>
            <div class="row">
              <span class="lbl">Einstellungen</span>
              <div class="row-btns">
                <button class="btn btn-sm" onclick={exportSettings} title="Als JSON-Datei herunterladen"><i class="ti ti-download"></i> Exportieren</button>
                <button class="btn btn-sm" onclick={importSettings} title="JSON-Datei einlesen"><i class="ti ti-folder"></i> Importieren</button>
              </div>
            </div>
          </div>

        <!-- ── REMOTE ─────────────────────────────────────────────────── -->
        {:else if tab === 'remote'}

          <div class="group">
            <div class="group-title">Handy-Dienste</div>
            <p class="hint">Fernbedienung und Musikwünsche laufen über einen kleinen Server im WLAN — kein Internet, keine App. Beide lassen sich einzeln ein- und ausschalten.</p>
            <div class="row">
              <span class="lbl">Beim Start die zuletzt eingeschalteten wieder starten</span>
              <button class="tog {$remoteAutostart ? 'on' : ''}" aria-label="Handy-Dienste beim Start wieder starten"
                onclick={() => send({ type: 'set_remote_autostart', value: !$remoteAutostart })}></button>
            </div>
            {#if $remoteStatus?.error}
              <div class="remote-status off"><span class="remote-dot off"></span> Fehler: {$remoteStatus.error}</div>
            {/if}
          </div>

          <div class="group svc-card" class:on={svcRemote}>
            <div class="group-title">Fernbedienung</div>
            <div class="svc-head">
              <span class="remote-status {svcRemote ? 'on' : 'off'}"><span class="remote-dot {svcRemote ? 'on' : 'off'}"></span>{svcRemote ? 'Läuft' : 'Aus'}</span>
              <button class="btn btn-sm {svcRemote ? 'btn-danger' : 'btn-primary'}" onclick={() => toggleSvc('remote', !svcRemote)}>
                {svcRemote ? 'Stoppen' : 'Starten'}
              </button>
            </div>
            {#if svcRemote}
              <div class="remote-url">
                <span class="url-val" ondblclick={() => window.electron?.openPath($remoteStatus?.url)}
                      title="Doppelklick: im Browser öffnen">{$remoteStatus.url}</span>
                <button class="btn btn-icon btn-sm" class:is-active={urlCopied} onclick={copyRemoteUrl}
                        title="In Zwischenablage kopieren" aria-label="Adresse kopieren">
                  <i class="ti {urlCopied ? 'ti-check' : 'ti-copy'}"></i>
                </button>
                <button class="btn btn-icon btn-sm" onclick={() => window.electron?.openPath($remoteStatus?.url)}
                        title="Im Browser öffnen" aria-label="Im Browser öffnen"><i class="ti ti-external-link"></i></button>
              </div>
              <div class="svc-qr">
                <canvas bind:this={qrCanvas} class="qr-canvas"></canvas>
                <button class="btn btn-sm" onclick={newRemoteKey}
                        title="Falls der Fernbedienungs-Link in falsche Hände geraten ist">Neuen Link erzeugen</button>
              </div>
            {/if}
            <div class="hint">Der Code enthält einen geheimen Schlüssel — nur damit kommt man auf die Fernbedienung. Wer den Link kürzt, landet bei den Musikwünschen (falls eingeschaltet).</div>
          </div>

          <div class="group svc-card" class:on={svcWishes}>
            <div class="group-title">Musikwünsche</div>
            <div class="svc-head">
              <span class="remote-status {svcWishes ? 'on' : 'off'}"><span class="remote-dot {svcWishes ? 'on' : 'off'}"></span>{svcWishes ? 'Läuft' : 'Aus'}</span>
              <button class="btn btn-sm {svcWishes ? 'btn-danger' : 'btn-primary'}" onclick={() => toggleSvc('wishes', !svcWishes)}>
                {svcWishes ? 'Stoppen' : 'Starten'}
              </button>
            </div>
            {#if svcWishes}
              <div class="remote-url">
                <span class="url-val" ondblclick={() => window.electron?.openPath(wishUrl)}
                      title="Doppelklick: im Browser öffnen">{wishUrl}</span>
                <button class="btn btn-icon btn-sm" class:is-active={wishCopied} onclick={copyWishUrl}
                        title="In Zwischenablage kopieren" aria-label="Adresse kopieren">
                  <i class="ti {wishCopied ? 'ti-check' : 'ti-copy'}"></i>
                </button>
                <button class="btn btn-icon btn-sm" onclick={() => window.electron?.openPath(wishUrl)}
                        title="Im Browser öffnen" aria-label="Im Browser öffnen"><i class="ti ti-external-link"></i></button>
              </div>
              <div class="svc-qr">
                <canvas bind:this={wishQrCanvas} class="qr-canvas"></canvas>
              </div>
            {/if}
            <div class="hint">Den Code kann man ausdrucken und aufhängen. Damit er gültig bleibt, sollte der Rechner im Router eine feste IP bekommen. Ist die Seite aus, sehen Gäste „ausgeschaltet“.</div>
          </div>

          <div class="group">
            <div class="group-title">Funktionen</div>
            <div class="info-row"><i class="ti ti-player-play"></i> Laufender Titel, Play/Pause, Weiter, Spulen</div>
            <div class="info-row"><i class="ti ti-volume"></i> Lautstärke und Normalisierung</div>
            <div class="info-row"><i class="ti ti-list"></i> Warteschlange: umsortieren, als nächstes, jetzt mischen, entfernen</div>
            <div class="info-row"><i class="ti ti-music"></i> Musikwünsche der Gäste annehmen oder ablehnen</div>
            <div class="info-row"><i class="ti ti-search"></i> Bibliothek und YouTube durchsuchen, Titel einreihen</div>
          </div>

        <!-- ── INFO ───────────────────────────────────────────────────── -->
        {:else if tab === 'info'}

          {#snippet inline(text)}{#each splitBold(text) as [b, part]}{#if b}<b>{part}</b>{:else}{part}{/if}{/each}{/snippet}
          <div class="group">
            <div class="group-title">Was ist neu</div>
            {#if $changelog === null}
              <div class="hint keep"><i class="ti ti-refresh spin"></i> Lade die Änderungen…</div>
            {:else if !$changelog.length}
              <div class="hint keep">Keine Verbindung zu GitHub — die Änderungen erscheinen, sobald SynthiMIX einmal online war.</div>
            {:else}
              <div class="cl-list">
                {#each $changelog as rel, i (rel.tag)}
                  <details class="cl" open={i === 0}>
                    <summary>
                      <span class="cl-ver">{rel.tag}</span>
                      {#if rel.tag === 'v' + APP_VERSION}<span class="cl-cur">installiert</span>{/if}
                      <span class="cl-date">{fmtDate(rel.date)}</span>
                    </summary>
                    <div class="cl-body">
                      {#each parseNotes(rel.body) as blk}
                        {#if blk.t === 'h'}<div class="cl-h">{@render inline(blk.x)}</div>
                        {:else if blk.t === 'ul'}<ul>{#each blk.items as li}<li>{@render inline(li)}</li>{/each}</ul>
                        {:else}<p>{@render inline(blk.x)}</p>{/if}
                      {/each}
                    </div>
                  </details>
                {/each}
              </div>
            {/if}
          </div>

          <div class="group">
            <div class="group-title">Einrichtung</div>
            <div class="row">
              <span class="lbl">Assistent</span>
              <button class="btn btn-sm" onclick={() => { settingsOpen.set(false); setupOpen.set(true) }}><i class="ti ti-wand"></i> Einrichtung starten</button>
            </div>
            <div class="hint">Führt in sechs Schritten durch Aussehen, Musikordner, Downloads, Übergänge, Anzeige und Dienste.</div>
          </div>

          <div class="group">
            <div class="group-title">Tastenkürzel</div>
            <table class="shortcuts">
              <tbody>
                <tr><td>Space</td><td>Pause / Weiter</td></tr>
                <tr><td>← / →</td><td>±5 Sekunden spulen</td></tr>
                <tr><td>Strg + →</td><td>Nächster Track</td></tr>
                <tr><td>Strg + ←</td><td>Track-Anfang (nochmals: vorheriger Track)</td></tr>
                <tr><td>N</td><td>Nächster Track</td></tr>
                <tr><td>P</td><td>Vorheriger Track</td></tr>
                <tr><td>Entf</td><td>Markierte Queue-Einträge entfernen</td></tr>
                <tr><td>Strg + A</td><td>Alle markieren (Queue / Bibliothek)</td></tr>
                <tr><td>Esc</td><td>Markierung aufheben</td></tr>
              </tbody>
            </table>
          </div>

          <div class="group">
            <div class="group-title">Medientasten</div>
            <div class="hint">Play/Pause · Nächster · Vorheriger · Stop — funktionieren global (auch wenn App minimiert ist)</div>
          </div>

          <div class="group">
            <div class="group-title">Radio</div>
            <div class="hint">Hält die Warteschlange mit passenden Titeln gefüllt: Richtung aus den letzten Titeln, Tonart, Tempo, Energie und Genre wie beim harmonischen Sortieren; mit Last.fm-Key auch ähnliche Titel und Künstler. Jeder 5. Titel ist neu und wird im Hintergrund geladen. Radio-Knopf in der Warteschlange (dauerhaft) oder Wiedergabe → Queue-Ende → Radio (erst am Ende).</div>
          </div>

        {/if}
      </div>
    </div>
  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(0,0,0,.66);   /* ohne backdrop-filter, siehe ui.css .dlg-overlay */
    display: flex; align-items: center; justify-content: center;
  }
  .panel {
    width: min(780px, calc(100vw - 32px)); height: min(88vh, 680px);
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: var(--r-l);
    box-shadow: 0 24px 64px rgba(0,0,0,.55);
    display: flex; flex-direction: column; overflow: hidden;
  }

  /* Kopf */
  .hdr {
    display: flex; align-items: center; gap: var(--sp-3);
    padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); flex-shrink: 0;
    border-bottom: 1px solid var(--c-br1); background: var(--c-bg2);
  }
  .hdr-brand { display: flex; align-items: center; gap: var(--sp-2); }
  .hdr-icon { width: 26px; height: 26px; flex-shrink: 0; }
  .hdr-appname { font-size: var(--fs-lg); font-weight: 700; letter-spacing: .04em; }
  .nm-synthi { color: var(--c-accent-tx); }
  .nm-mix { color: var(--c-blue-tx); }
  .hdr-title { font-size: var(--fs-h); font-weight: 600; color: var(--c-tx1); padding-left: var(--sp-3); border-left: 1px solid var(--c-br2); }
  .close-btn { margin-left: auto; }

  .layout { flex: 1; display: flex; min-height: 0; }

  /* Tabs links */
  .tab-nav {
    width: 184px; flex-shrink: 0; display: flex; flex-direction: column; gap: 2px;
    padding: var(--sp-3) var(--sp-2); background: var(--c-bg2); border-right: 1px solid var(--c-br1);
    overflow-y: auto;
  }
  .tab-btn {
    position: relative; display: flex; align-items: center; gap: var(--sp-3);
    height: 36px; padding: 0 var(--sp-3); border: none; border-radius: var(--r-m);
    background: none; color: var(--c-tx2); font: 600 var(--fs-body) 'Segoe UI', system-ui, sans-serif;
    text-align: left; cursor: pointer;
  }
  .tab-btn:hover { background: var(--c-hover); color: var(--c-tx1); }
  .tab-btn.active { background: var(--c-act-bg); color: var(--c-accent-tx); }
  .tab-btn.active::before {
    content: ""; position: absolute; left: 0; top: 8px; bottom: 8px; width: 3px; border-radius: 2px; background: var(--c-accent);
  }
  .tab-icon { font-size: 16px; flex-shrink: 0; }
  .tab-label { flex: 1; }
  .tab-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--c-accent); flex-shrink: 0; }

  /* Inhalt */
  .content { flex: 1; overflow-y: auto; scrollbar-gutter: stable; padding: var(--sp-4) var(--sp-5) var(--sp-6); display: flex; flex-direction: column; gap: var(--sp-5); }
  .group { display: flex; flex-direction: column; gap: var(--sp-2); }
  .group + .group { padding-top: var(--sp-5); border-top: 1px solid var(--c-br1); }
  .group-title {
    font-size: var(--fs-cap); font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    color: var(--c-tx4); margin-bottom: 2px;
  }
  .row { display: flex; align-items: center; gap: var(--sp-3); min-height: var(--btn-h); }
  .row.indent { padding-left: var(--sp-4); }
  .lbl { flex: 1; min-width: 0; font-size: var(--fs-body); color: var(--c-tx1); }
  .val { font-size: var(--fs-body); color: var(--c-tx2); font-variant-numeric: tabular-nums; min-width: 64px; text-align: right; }
  .val.agg { min-width: 84px; }
  .row input[type="range"] { width: 200px; flex-shrink: 0; }
  .row .field { width: 280px; flex-shrink: 0; }
  .hint { font-size: var(--fs-sm); line-height: 1.5; color: var(--c-tx3); max-width: 60ch; }
  .diag { display: flex; flex-direction: column; gap: 4px; margin: 6px 0; }
  .diag-row { display: grid; grid-template-columns: 18px 150px 1fr; gap: 8px; align-items: start; font-size: var(--fs-sm); color: var(--c-tx2); }
  .diag-row b { font-weight: 600; color: var(--c-tx1); }
  .row-acts { display: inline-flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
  .diag-row span { overflow-wrap: anywhere; }
  .diag-row .ti { color: var(--c-ok, #46a758); margin-top: 2px; }
  .diag-row.bad .ti, .diag-row.bad b { color: var(--c-warn, #e5a000); }
  /* Erklaerung direkt zu einer Zeile: buendig unter dem Label, dicht dran */
  .hint.sub { margin-top: calc(-1 * var(--sp-1)); }
  .hint.warn { color: var(--c-warn-tx); }
  .lbl.ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row select.field { width: 280px; flex-shrink: 0; }
  /* ── Dienste als Karten ─────────────────────────────────────────────── */
  .svc-auto { margin-top: var(--sp-4); }
  .svc-intro { font-size: var(--fs-sm); color: var(--c-tx3); margin-bottom: var(--sp-3); }
  .svc-list { display: flex; flex-direction: column; gap: var(--sp-2); }
  .svc { border: 1px solid var(--c-br1); border-radius: var(--r-m); background: var(--c-bg2); }
  .svc.open { border-color: var(--c-br2); }
  .svc-head { display: flex; align-items: center; gap: var(--sp-3); width: 100%; padding: 10px 12px;
              border: none; background: none; cursor: pointer; font: inherit; text-align: left; color: inherit; }
  .svc-head:hover .svc-name { color: var(--c-tx1); }
  .svc-ico { font-size: 20px; color: var(--c-tx3); flex-shrink: 0; }
  .svc-name { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px;
              font-size: var(--fs-body); font-weight: 600; color: var(--c-tx1); }
  .svc-sub { font-size: var(--fs-sm); font-weight: 400; color: var(--c-tx3); }
  .svc-state { font-size: var(--fs-sm); font-weight: 600; white-space: nowrap; }
  .svc-state::before { content: ''; display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px;
                       background: currentColor; vertical-align: 1px; }
  .svc-state.ok { color: var(--c-green-tx); }
  .svc-state.err { color: var(--c-red-tx); }
  .svc-state.busy { color: var(--c-accent-tx); }
  .svc-state.off { color: var(--c-tx4); }
  .svc-chev { color: var(--c-tx4); transition: transform .15s; flex-shrink: 0; }
  .svc.open .svc-chev { transform: rotate(180deg); }
  .svc-body { display: flex; flex-direction: column; gap: var(--sp-2); padding: 4px 12px 12px 44px; border-top: 1px solid var(--c-br1); }
  .svc-body .row:first-child { margin-top: var(--sp-2); }
  .svc-note { font-size: var(--fs-sm); line-height: 1.5; color: var(--c-tx3); }
  .svc-note strong { color: var(--c-tx1); }
  .svc-note.ok { color: var(--c-green-tx); }
  .svc-note.err { color: var(--c-red-tx); }
  .svc-actions { display: flex; justify-content: flex-end; }
  .opt { font-size: var(--fs-cap); color: var(--c-tx4); margin-left: 4px; }
  .dimmed { opacity: .5; }
  .st-ok { color: var(--c-green-tx); font-weight: 600; }
  .st-busy { color: var(--c-accent-tx); }
  .st-muted { color: var(--c-tx4); }
  .st-err { color: var(--c-red-tx); font-size: var(--fs-sm); text-align: left; }
  .row-btns { display: flex; gap: var(--sp-2); }
  .radio-group { margin-top: 2px; }

  /* Schalter (Pill-Toggle) — gross genug zum Treffen */
  .tog {
    position: relative; width: 44px; height: 24px; flex-shrink: 0; cursor: pointer;
    border-radius: 12px; border: 1px solid var(--c-br3); background: var(--c-bg2);
    transition: background .15s, border-color .15s;
  }
  .tog::after {
    content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%;
    background: var(--c-tx4); transition: transform .15s, background .15s;
  }
  .tog.on { background: var(--c-accent); border-color: var(--c-accent); }
  .tog.on::after { transform: translateX(20px); background: var(--c-on-accent); }
  .tog:hover { border-color: var(--c-tx6); }
  .tog:disabled { opacity: .4; cursor: default; }

  /* Auswahl mit Beispiel (Dateiname) */
  .radio-group.vertical, .vertical { display: flex; flex-direction: column; gap: var(--sp-1); }
  .radio-opt-v {
    display: flex; align-items: baseline; justify-content: space-between; gap: var(--sp-3);
    min-height: var(--btn-h); padding: 6px var(--sp-3); border-radius: var(--r-m);
    border: 1px solid var(--c-br3); background: var(--c-bg5); cursor: pointer; text-align: left;
    font-family: inherit;
  }
  .radio-opt-v:hover { background: var(--c-hover); }
  .radio-opt-v.active { border-color: var(--c-accent); background: var(--c-act-bg); }
  .ro-label { font-size: var(--fs-body); font-weight: 600; color: var(--c-tx1); }
  .radio-opt-v.active .ro-label { color: var(--c-accent-tx); }
  .ro-example { font-family: Consolas, 'Cascadia Mono', monospace; font-size: var(--fs-sm); color: var(--c-tx3); }

  /* Checkbox mit Text */
  input[type="checkbox"] { width: 16px; height: 16px; }

  /* Beobachtete Ordner */
  .wf-head { font-size: var(--fs-sm); font-weight: 700; color: var(--c-tx2); margin-top: var(--sp-3); }
  .wf-row { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-2) 0; border-top: 1px solid var(--c-br1); }
  .wf-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .wf-path { font-size: var(--fs-body); color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .wf-meta { font-size: var(--fs-sm); color: var(--c-tx3); }
  .wf-warn { color: var(--c-warn-tx); }
  .wf-confirm { font-size: var(--fs-sm); color: var(--c-tx2); }

  /* Tools */
  .upd-note { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }

  /* Darstellung */
  .key-samples { display: flex; gap: var(--sp-2); flex-wrap: wrap; font-size: var(--fs-body); }

  /* Remote */
  .remote-status { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-body); font-weight: 600; }
  .remote-status.on { color: var(--c-green-tx); }
  .remote-status.off { color: var(--c-tx3); }
  .remote-dot { width: 10px; height: 10px; border-radius: 50%; }
  .remote-dot.on { background: var(--c-green-tx); }
  .remote-dot.off { background: var(--c-tx6); }
  .remote-url {
    display: flex; align-items: center; gap: var(--sp-2);
    padding: 6px 6px 6px var(--sp-3); border-radius: var(--r-m); background: var(--c-bg2); border: 1px solid var(--c-br2);
  }
  .url-label { font-size: var(--fs-sm); color: var(--c-tx4); }
  .url-val { flex: 1; min-width: 0; font-family: Consolas, 'Cascadia Mono', monospace; font-size: var(--fs-body); color: var(--c-blue-tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; user-select: text; }
  .remote-url-actions { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
  .remote-hint { flex: 1; font-size: var(--fs-sm); color: var(--c-tx3); }
  .qr-row { display: flex; justify-content: center; gap: var(--sp-5); flex-wrap: wrap; padding: var(--sp-2) 0; }
  .qr-wrap { display: flex; flex-direction: column; align-items: center; gap: var(--sp-2); }
  .qr-canvas { border-radius: var(--r-m); background: #fff; }
  .qr-hint { font-size: var(--fs-cap); font-weight: 700; letter-spacing: .08em; color: var(--c-tx3); }
  .svc-head { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-2); }
  .svc-qr { display: flex; align-items: flex-end; gap: var(--sp-3); flex-wrap: wrap; }
  .svc-card.on { border-left: 3px solid var(--c-green-tx); padding-left: var(--sp-3); }
  .info-row { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-body); color: var(--c-tx2); }
  .info-row .ti { font-size: 16px; color: var(--c-tx4); }

  /* Tastenkuerzel */
  .shortcuts { border-collapse: collapse; font-size: var(--fs-body); }
  .shortcuts td { padding: 6px var(--sp-3) 6px 0; border-bottom: 1px solid var(--c-br1); color: var(--c-tx2); }
  .shortcuts td:first-child {
    font-weight: 600; color: var(--c-tx1); white-space: nowrap;
  }
  .spin { display: inline-block; animation: spin 1s linear infinite; }
  /* ── Verfolgte Playlists ─────────────────────────────────────────────── */
  .follow-list { display: flex; flex-direction: column; border: 1px solid var(--c-br1); border-radius: var(--r-m); background: var(--c-bg2); }
  .follow-row { display: flex; align-items: center; gap: var(--sp-2); padding: 6px 6px 6px 10px; }
  .follow-row + .follow-row { border-top: 1px solid var(--c-br1); }
  .follow-ico { color: var(--c-accent-tx); flex-shrink: 0; }
  .follow-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  .follow-name { font-size: var(--fs-body); font-weight: 600; color: var(--c-tx1); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .follow-meta { font-size: var(--fs-sm); color: var(--c-tx3); }
  .follow-meta b { color: var(--c-accent-tx); }
  .f-rename { font: inherit; font-weight: 600; color: var(--c-tx1); background: var(--c-bg1); border: 1px solid var(--c-accent);
              border-radius: var(--r-s, 4px); padding: 2px 6px; outline: none; min-width: 0; }
  .ftoggle { background: none; border: none; padding: 0; margin: 0; text-align: left; cursor: pointer; color: inherit; font: inherit; }
  .ftoggle:hover .follow-name { color: var(--c-accent-tx); }
  .ftoggle .fchev { font-size: 12px; color: var(--c-tx4); vertical-align: -1px; }
  .follow-res { font-size: var(--fs-sm); color: var(--c-tx4); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ftracks { border-top: 1px solid var(--c-br1); padding: 6px 10px 8px 34px; }
  .ft-note { font-size: var(--fs-sm); color: var(--c-tx3); display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
  .ft-sum { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: var(--fs-sm); color: var(--c-tx3); margin-bottom: 4px; }
  .ft-list { list-style: none; margin: 0; padding: 0; max-height: 240px; overflow-y: auto; }
  .ft-list li { display: flex; align-items: center; gap: 6px; padding: 2px 0; font-size: var(--fs-sm); color: var(--c-tx2); min-width: 0; }
  .ft-list li span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ft-list li i { flex: none; font-size: 13px; }
  .ft-list li.folder i { color: var(--c-green-tx); }
  .ft-list li.lib i, .ft-list li.missing i { color: var(--c-tx4); }
  .ft-list li.fail i { color: var(--c-warn-tx); }
  .ft-list li.gone i { color: var(--c-red-tx); }
  .ft-list li.gone span, .ft-list li.missing span { color: var(--c-tx4); text-decoration: line-through; }
  .follow-list.ch { margin-bottom: var(--sp-2); }
  .follow-row.sub { padding-left: 34px; border-top: 1px solid var(--c-br1); }
  .ch-auto { display: inline-flex; align-items: center; gap: 4px; font-size: var(--fs-sm); color: var(--c-tx3); white-space: nowrap; cursor: pointer; }
  .ch-pending { display: flex; align-items: center; gap: 8px; padding: 6px 10px 6px 34px; font-size: var(--fs-sm); color: var(--c-accent-tx); border-top: 1px solid var(--c-br1); }
  .ch-pending .btn { margin-left: auto; }
  .ch-expand { display: flex; align-items: center; gap: 6px; width: 100%; padding: 5px 10px 6px 30px; border: none; border-top: 1px solid var(--c-br1);
               background: none; cursor: pointer; font: inherit; font-size: var(--fs-sm); color: var(--c-tx3); text-align: left; }
  .ch-expand:hover { color: var(--c-tx1); }
  .fchev { transition: transform .12s; }
  .fchev.open { transform: rotate(90deg); }
  .follow-empty { font-size: var(--fs-sm); color: var(--c-tx4); padding: 8px 10px; border: 1px dashed var(--c-br2); border-radius: var(--r-m); }
  @keyframes spin { to { transform: rotate(360deg); } }
  /* ── Was ist neu ─────────────────────────────────────────────────────── */
  .cl-list { display: flex; flex-direction: column; gap: 6px; }
  .cl { border: 1px solid var(--c-br1); border-radius: var(--r-m); background: var(--c-bg2); }
  .cl summary {
    display: flex; align-items: center; gap: 10px; padding: 8px 12px; cursor: pointer;
    list-style: none; font-weight: 700; color: var(--c-tx1);
  }
  .cl summary::-webkit-details-marker { display: none; }
  .cl summary::before { content: '›'; color: var(--c-tx4); transition: transform .12s; display: inline-block; width: 10px; }
  .cl[open] summary::before { transform: rotate(90deg); }
  .cl-ver { font-variant-numeric: tabular-nums; }
  .cl-cur { font-size: var(--fs-cap); font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--c-accent-tx); }
  .cl-date { margin-left: auto; font-weight: 400; font-size: var(--fs-sm); color: var(--c-tx4); font-variant-numeric: tabular-nums; }
  .cl-body { padding: 0 14px 12px 32px; font-size: var(--fs-body); color: var(--c-tx2); line-height: 1.5; max-width: 62ch; }
  .cl-h { margin: 10px 0 2px; font-weight: 700; color: var(--c-tx1); }
  .cl-body ul { margin: 2px 0; padding-left: 18px; }
  .cl-body li { margin: 2px 0; }
  .cl-body p { margin: 8px 0 0; color: var(--c-tx3); }
  .cl-body b { color: var(--c-tx1); }
</style>
