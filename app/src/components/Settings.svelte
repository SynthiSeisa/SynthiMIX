<script>
  import { untrack } from 'svelte'
  import { theme, density } from '../lib/prefs.js'
  import KeyChip from './KeyChip.svelte'
  import BlendSketch from './BlendSketch.svelte'
  import { settings, settingsOpen, settingsTab, appSettings, send, toolsInfo, updateProgress,
           loudnormOnDl, loudnormTarget, loudnormTp, autoMixEnabled, playMode,
           playlistFolderEnabled, dlFilenameFormat, downloadDir, remoteStatus,
           autoScanIntervalMin, scanRecursive, remoteAutostart,
           spotifyClientId, spotifyClientSecret,
           lastfmApiKey, acoustidApiKey,
           fpcalcInstalling, fpcalcInstallError,
           spotdlInstalling, spotdlInstallError, spotdlInstallText,
           watchedFolders, watchedFolderImpact, ytdlpAutoupdate, excludedFolders, toolUpdates, servicesTest, setupOpen, changelog, followed } from '../stores/ws.js'
  import { infoHints } from '../lib/infohints.js'

  let tab = $state('playback')

  // ── Verfolgte Playlists (frueher als Kasten ueber den Downloads) ─────────
  const FOLLOW_MODE = { playlist: 'als Playlist', new: 'nur neue', folder: 'kompletter Ordner' }
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
    system:   !!($toolUpdates.ytdlp?.available || $toolUpdates.ytdlp_updated || $toolUpdates.spotdl_updated
                  || $toolUpdates.ffmpeg?.available || $toolUpdates.ffmpeg_updated),
    services: !!$toolUpdates.spotdl?.available,
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
            <div class="group-title">Pegel</div>

            <div class="row">
              <span class="lbl">Lautstärke</span>
              <input type="range" min="0" max="100" value={$settings.volume}
                oninput={(e) => send({ type: 'set_volume', value: +e.target.value })} />
              <span class="val">{$settings.volume}</span>
            </div>
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
            <div class="hint">Den Stand im Player („≋ −8.1 → {$appSettings.targetLUFS} LUFS“) blendest du unter Darstellung → Player ein. Auf der Fernbedienung lässt sich die Angleichung weiter schalten.</div>
          </div>

          <div class="group">
            <div class="group-title">BPM-Schätzung</div>
            <div class="row">
              <span class="lbl">BPM beim Abspielen analysieren</span>
              <button class="tog {$appSettings.bpmAnalysis ? 'on' : ''}"
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
            <div class="hint">Was passiert wenn die Warteschlange leer ist.</div>
            <div class="btn-group radio-group">
              {#each [['stop','Stopp'],['automix','Auto-Mix'],['repeat','Wiederholen']] as [val, label]}
                <button class="btn btn-sm" class:is-active={queueEnd === val}
                        onclick={() => setQueueEnd(val)}>
                  {label}
                </button>
              {/each}
            </div>
            <div class="hint" style="margin-top:6px">
              {#if queueEnd === 'stop'}Wiedergabe endet nach dem letzten Track
              {:else if queueEnd === 'automix'}Sucht automatisch ähnliche Songs weiter
              {:else}Queue startet von vorne (alle Tracks){/if}
            </div>
          </div>

        <!-- ── BLEND ───────────────────────────────────────────────────── -->
        {:else if tab === 'fade'}

          <div class="group">
            <div class="group-title">Überblendzeit</div>
            <div class="hint">Wie lange der Übergang zwischen zwei Tracks dauert. Bei 0 wird direkt umgeschaltet.</div>
            <div class="row" style="margin-top:8px">
              <span class="lbl">Dauer</span>
              <input type="range" min="0" max="15" step="1" value={$settings.crossfade_s}
                onchange={(e) => send({ type: 'set_crossfade', seconds: +e.target.value })}
                oninput={(e) => settings.update(s => ({ ...s, crossfade_s: +e.target.value }))} />
              <span class="val">{$settings.crossfade_s === 0 ? 'aus' : $settings.crossfade_s + 's'}</span>
            </div>
          </div>

          <div class="group">
            <div class="group-title">Überblend-Kurve</div>
            <div class="hint">Bestimmt den Lautstärkeverlauf während des Übergangs.</div>
            <div class="btn-group radio-group">
              {#each [['cosine','Kosinus'],['linear','Linear'],['scurve','S-Kurve']] as [val, label]}
                <button class="btn btn-sm" class:is-active={$appSettings.cfCurve === val}
                        onclick={() => appSettings.update(s => ({...s, cfCurve: val}))}>
                  {label}
                </button>
              {/each}
            </div>
            <div class="hint" style="margin-top:6px">
              {#if $appSettings.cfCurve === 'cosine'}Weiche Sinuskurve — natürlichster Klang, empfohlen
              {:else if $appSettings.cfCurve === 'linear'}Gleichmäßiger Abfall — direkter, teils hörbar
              {:else}Starke S-Kurve — lange stille Mitte, harte Ein- und Ausgänge{/if}
            </div>
            <BlendSketch kind="curve" value={$appSettings.cfCurve ?? 'cosine'} />
          </div>

          <div class="group">
            <div class="group-title">Intelligenter Fade</div>
            <div class="hint">Analysiert die Waveform automatisch und zeigt graue Balken: das Intro (Track startet erst beim Beat) und die Mix-Zone am Outro (wo der Übergang läuft).</div>

            <div class="row" style="margin-top:10px">
              <span class="lbl">Aktiv</span>
              <button class="tog {$appSettings.smartFade ? 'on' : ''}"
                onclick={() => appSettings.update(s => ({...s, smartFade: !s.smartFade}))}></button>
            </div>

            <!-- INTRO -->
            <div class="row {$appSettings.smartFade ? '' : 'dimmed'}" style="margin-top:8px">
              <span class="lbl" title="Wie viel vom erkannten Intro übersprungen wird. Sanft = nur ein Teil, Stark = das ganze Intro bis zum Beat.">Intro überspringen</span>
              <input type="range" min="1" max="5" step="1"
                     value={$appSettings.introAggressiveness ?? 3}
                     disabled={!$appSettings.smartFade}
                     oninput={(e) => appSettings.update(s => ({...s, introAggressiveness: +e.target.value}))} />
              <span class="val agg">
                {(['Sehr sanft','Sanft','Mittel','Stark','Sehr stark'])[($appSettings.introAggressiveness ?? 3) - 1]}
              </span>
            </div>
            <div class="hint {$appSettings.smartFade ? '' : 'dimmed'}">
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
            <div class="row {$appSettings.smartFade ? '' : 'dimmed'}" style="margin-top:8px">
              <span class="lbl" title="Wie früh der Übergang vor der erkannten Outro-Stille beginnt. Stark = deutlich früher, längere Überlappung.">Outro-Start</span>
              <input type="range" min="1" max="5" step="1"
                     value={$appSettings.outroAggressiveness ?? 3}
                     disabled={!$appSettings.smartFade}
                     oninput={(e) => appSettings.update(s => ({...s, outroAggressiveness: +e.target.value}))} />
              <span class="val agg">
                {(['Sehr sanft','Sanft','Mittel','Stark','Sehr stark'])[($appSettings.outroAggressiveness ?? 3) - 1]}
              </span>
            </div>
            <div class="hint {$appSettings.smartFade ? '' : 'dimmed'}">
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
            <div class="row" style="margin-top:8px">
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
              <div class="hint keep">
                Bei 128 BPM: {Math.round(128 / (1 + td / 100))}–{Math.round(128 * (1 + td / 100))} BPM.
                {#if td > 12}Ab etwa 12 % hört man das Dehnen etwas (klingt leicht verwaschen).{:else}Bis hierhin fällt das Angleichen kaum auf.{/if}
              </div>
            {/if}
            <div class="hint" style="margin-top:4px">
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
            <div class="row" style="margin-top:8px">
              <span class="lbl">Bass tauschen (EQ)</span>
              <button class="tog {$appSettings.bassSwap !== false ? 'on' : ''}" aria-label="Bass tauschen"
                disabled={!$appSettings.beatAlignCf}
                onclick={() => appSettings.update(s => ({...s, bassSwap: s.bassSwap === false}))}></button>
            </div>
            <div class="hint">Der neue Titel läuft zuerst ohne Bass mit; in der Mitte des Übergangs wird der Bass über 1–2 Takte weich getauscht — so wummern nie zwei Bassdrums gleichzeitig. Nur wenn der Übergang im Takt läuft; sonst wird wie bisher nur die Lautstärke geblendet.</div>
            {#if !$appSettings.beatAlignCf}<div class="hint keep">Braucht „Schläge übereinanderlegen“.</div>{/if}
          </div>

        <!-- ── DOWNLOAD ────────────────────────────────────────────────── -->
        {:else if tab === 'download'}

          <div class="group">
            <div class="group-title">Playlist-Download</div>

            <div class="row">
              <span class="lbl">In eigenem Ordner speichern</span>
              <button class="tog {$playlistFolderEnabled ? 'on' : ''}"
                onclick={() => setPlaylistFolder(!$playlistFolderEnabled)}></button>
            </div>
            <div class="hint">
              {#if $playlistFolderEnabled}
                Downloads/&lt;Playlist-Name&gt;/Song.mp3
              {:else}
                Alle Downloads landen direkt im Download-Ordner
              {/if}
            </div>
          </div>

          <div class="group">
            <div class="group-title">Verfolgte Playlists</div>
            <div class="hint">Werden beim Start und auf Knopfdruck auf neue Titel geprüft; neue Titel werden automatisch geladen.</div>
            {#if $followed.length}
              <div class="follow-list">
                {#each $followed as f (f.url)}
                  <div class="follow-row">
                    <i class="ti ti-bookmark-filled follow-ico" aria-hidden="true"></i>
                    <div class="follow-main">
                      <span class="follow-name" title={f.url}>{f.title}</span>
                      <span class="follow-meta">
                        {#if f.checking}<i class="ti ti-refresh spin" aria-hidden="true"></i>{' wird geprüft…'}{:else}{followAgo(f.last_check)}{/if}{#if !f.checking && f.last_check && f.last_new}{' · '}<b>{f.last_new} neu</b>{/if}{` · ${f.known} bekannt`}{FOLLOW_MODE[f.mode] ? ` · ${FOLLOW_MODE[f.mode]}` : ''}
                      </span>
                    </div>
                    <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'follow_check', url: f.url })} disabled={f.checking}
                            title="Jetzt auf neue Titel prüfen" aria-label="Jetzt prüfen"><i class="ti ti-refresh"></i></button>
                    <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'follow_remove', url: f.url })}
                            title="Nicht mehr verfolgen (geladene Titel bleiben)" aria-label="Nicht mehr verfolgen"><i class="ti ti-x"></i></button>
                  </div>
                {/each}
              </div>
              <div>
                <button class="btn btn-sm" onclick={() => send({ type: 'follow_check' })}
                        disabled={$followed.every(f => f.checking)}><i class="ti ti-refresh"></i> Alle jetzt prüfen</button>
              </div>
            {:else}
              <div class="follow-empty">Noch keine. Beim Laden einer Playlist „Playlist verfolgen“ anhaken oder in den Downloads bei einer Playlist auf „Verfolgen“ klicken.</div>
            {/if}
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
            <div class="group-title">Lautstärke beim Download normalisieren</div>

            <div class="row">
              <span class="lbl">Loudnorm aktiv</span>
              <button class="tog {$loudnormOnDl ? 'on' : ''}"
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
            <div class="group-title">Speicherort</div>
            <div class="row">
              <span class="lbl" title={$downloadDir || 'Standard: Downloads/'}
                style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                {$downloadDir ? $downloadDir.split(/[\\/]/).pop() || $downloadDir : 'Downloads/'}
              </span>
              <button class="btn btn-sm" onclick={pickDownloadFolder}>Ordner wählen</button>
            </div>
          </div>

        <!-- ── DIENSTE ─────────────────────────────────────────────────── -->
        {:else if tab === 'services'}

          <div class="group">
            <div class="group-title">Spotify-Download (via spotdl)</div>
            <div class="row">
              <span class="lbl">spotdl</span>
              {#if $toolsInfo.spotdl_version && $spotdlInstalling}
                <span class="val st-busy">{$spotdlInstallText ?? 'Wird aktualisiert…'}</span>
              {:else if $toolsInfo.spotdl_version}
                <span class="val st-ok">✓ v{$toolsInfo.spotdl_version}</span>
                {#if $toolUpdates.spotdl?.available}
                  <button class="btn btn-sm btn-primary" data-upd onclick={() => send({ type: 'install_spotdl' })}
                          title="Lädt die neue Version (~46 MB) und ersetzt die alte erst, wenn der Download vollständig ist">
                    Auf {$toolUpdates.spotdl.latest} aktualisieren
                  </button>
                {/if}
              {:else if $spotdlInstalling}
                <span class="val st-busy">{$spotdlInstallText ?? 'Wird installiert…'}</span>
              {:else}
                <span class="val st-muted">nicht gefunden</span>
                <button class="btn btn-sm btn-primary" onclick={() => send({ type: 'install_spotdl' })}>
                  Installieren
                </button>
              {/if}
            </div>
            {#if $spotdlInstallError}
              <div class="row">
                <span class="lbl"></span>
                <span class="val st-err">{$spotdlInstallError}</span>
              </div>
            {/if}
            <div class="hint" style="margin-top:6px">
              Spotify lädt via YouTube Music — kein direkter Spotify-Stream.
              „Installieren“ lädt spotdl einmalig als eigenständiges Programm (~46 MB) herunter;
              Python wird dafür nicht benötigt.
              Client-ID &amp; Secret sind optional (höhere Rate-Limits):
            </div>
            <div class="row" style="margin-top:8px">
              <span class="lbl">Client-ID</span>
              <input class="field field-sm" type="text" placeholder="optional"
                     bind:value={spotifyCidEdit} onchange={saveSpotifyCreds} />
            </div>
            <div class="row">
              <span class="lbl">Client-Secret</span>
              <input class="field field-sm" type="password" placeholder="optional"
                     bind:value={spotifyCsecEdit} onchange={saveSpotifyCreds} />
            </div>
            <div class="row">
              <span class="lbl"></span>
              <button class="btn btn-sm" onclick={saveSpotifyCreds}>Speichern</button>
            </div>
          </div>

          <div class="group">
            <div class="group-title">Last.fm — Radio-Modus</div>
            <div class="hint">
              Radio-Modus füllt die Queue automatisch mit ähnlichen Tracks aus deiner Bibliothek.
              API-Key kostenlos unter <strong>last.fm/api/account/create</strong>.
            </div>
            <div class="row" style="margin-top:8px">
              <span class="lbl">API-Key</span>
              <input class="field field-sm" type="text" placeholder="32-stelliger Hex-Key"
                     bind:value={lastfmKeyEdit} oninput={() => servicesDirty = true} onchange={saveServices} />
            </div>
            {#if $servicesTest?.lastfm}<div class="row"><span class="lbl"></span><span class="val {$servicesTest.lastfm.ok ? 'st-ok' : 'st-err'}">{$servicesTest.lastfm.ok ? '✓' : '✗'} {$servicesTest.lastfm.text}</span></div>{/if}
          </div>

          <div class="group">
            <div class="group-title">AcoustID — Fingerprint-Erkennung</div>
            <div class="hint">
              Erkennt Tracks anhand des Audioinhalts (via AcoustID + MusicBrainz).
              Braucht <strong>Chromaprint (fpcalc)</strong> und einen kostenlosen <strong>Application-Key</strong>:
              acoustid.org → anmelden → <strong>„Register your application“</strong>. Der Key von deiner Benutzerseite funktioniert hier nicht.
            </div>
            <div class="row" style="margin-top:8px">
              <span class="lbl">fpcalc</span>
              {#if $toolsInfo.fpcalc_found}
                <span class="val st-ok">✓ gefunden</span>
              {:else if $fpcalcInstalling}
                <span class="val st-busy">Wird installiert…</span>
              {:else}
                <span class="val st-muted">nicht gefunden</span>
                <button class="btn btn-sm btn-primary" onclick={() => send({ type: 'download_fpcalc' })}>
                  Installieren
                </button>
              {/if}
            </div>
            {#if $fpcalcInstallError}
              <div class="row">
                <span class="lbl"></span>
                <span class="val st-err">{$fpcalcInstallError}</span>
              </div>
            {/if}
            <div class="row">
              <span class="lbl">API-Key</span>
              <input class="field field-sm" type="text" placeholder="AcoustID Application-Key"
                     bind:value={acoustidKeyEdit} oninput={() => servicesDirty = true} onchange={saveServices} />
            </div>
            {#if $servicesTest?.acoustid}<div class="row"><span class="lbl"></span><span class="val {$servicesTest.acoustid.ok ? 'st-ok' : 'st-err'}">{$servicesTest.acoustid.ok ? '✓' : '✗'} {$servicesTest.acoustid.text}</span></div>{/if}
          </div>

          <div class="row" style="padding:0 4px">
            <span class="lbl"></span>
            <button class="btn btn-sm btn-primary" onclick={testServices} disabled={testing}>
              <i class="ti {testing ? 'ti-refresh spin' : 'ti-plug'}"></i> {testing ? 'Teste…' : 'Speichern und Verbindung testen'}
            </button>
          </div>
          {#if $servicesTest?.spotify}
            <div class="row" style="padding:0 4px"><span class="lbl">Spotify</span><span class="val {$servicesTest.spotify.ok ? 'st-ok' : 'st-err'}">{$servicesTest.spotify.ok ? '✓' : '✗'} {$servicesTest.spotify.text}</span></div>
          {/if}

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
            <div class="row" style="margin-top: 8px">
              <span class="lbl">Unterordner</span>
              <label class="toggle-wrap">
                <input type="checkbox" checked={$scanRecursive}
                       onchange={(e) => send({ type: 'set_scan_recursive', enabled: e.target.checked })} />
                <span class="toggle-lbl">beim Scannen einschließen</span>
              </label>
            </div>

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
            <div class="row" style="margin-top: 6px">
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

          <div class="group">
            <div class="group-title">Tools</div>

            <div class="tool-row">
              <span class="tool-name">yt-dlp</span>
              <span class="tool-ver">{$toolsInfo.ytdlp_version ?? '—'}</span>
              <button class="btn btn-sm"
                onclick={() => send({ type: 'update_ytdlp' })}
                disabled={!!$updateProgress}>
                <i class="ti ti-refresh"></i> Aktualisieren
              </button>
            </div>
            {#if $toolUpdates.ytdlp?.available}
              <div class="notice upd-note">
                Neue Version {$toolUpdates.ytdlp.latest} verfügbar{$ytdlpAutoupdate ? ' — wird installiert, sobald kein Download läuft' : ''}.
                Ältere Versionen scheitern bei YouTube früher oder später mit „403 Forbidden".
              </div>
            {/if}

            <div class="tool-row">
              <span class="tool-name">ffmpeg</span>
              <span class="tool-ver" title={$toolsInfo.ffmpeg_version ?? ''}>{fmtFfmpeg($toolsInfo.ffmpeg_version)}</span>
            </div>
            {#if $toolUpdates.ffmpeg?.available}
              <div class="notice upd-note">
                Neuerer ffmpeg-Build vom {fmtBuild($toolUpdates.ffmpeg.latest)}{$ytdlpAutoupdate ? ' — wird automatisch geladen (ca. 200 MB)' : ' verfügbar'}.
              </div>
            {/if}

            {#if $toolUpdates.ytdlp_updated || $toolUpdates.spotdl_updated || $toolUpdates.ffmpeg_updated}
              <div class="notice ok upd-note">
                Automatisch aktualisiert:
                {[$toolUpdates.ytdlp_updated && `yt-dlp ${$toolUpdates.ytdlp_updated.from} → ${$toolUpdates.ytdlp_updated.to}`,
                  $toolUpdates.spotdl_updated && `spotdl ${$toolUpdates.spotdl_updated.from} → ${$toolUpdates.spotdl_updated.to}`,
                  $toolUpdates.ffmpeg_updated && `ffmpeg ${fmtBuild($toolUpdates.ffmpeg_updated.from)} → ${fmtBuild($toolUpdates.ffmpeg_updated.to)}`]
                  .filter(Boolean).join(' · ')}
                <button class="btn btn-sm" onclick={() => send({ type: 'dismiss_ytdlp_updated' })}>OK</button>
              </div>
            {/if}
            <div class="row">
              <span class="lbl">Werkzeuge automatisch aktuell halten</span>
              <button class="tog {$ytdlpAutoupdate ? 'on' : ''}"
                onclick={() => send({ type: 'set_ytdlp_autoupdate', value: !$ytdlpAutoupdate })}></button>
            </div>
            <div class="hint" style="margin-bottom:10px">
              yt-dlp und spotdl werden täglich geprüft und ersetzt, sobald kein Download läuft —
              YouTube ändert regelmäßig etwas, wodurch ältere Versionen mit „403 Forbidden" abbrechen.
              ffmpeg wird wöchentlich geprüft und erst neu geladen, wenn der eigene Build mehr als
              zwei Monate alt ist (das Paket ist groß). Ohne Automatik gibt es stattdessen einen
              Hinweis (Punkt am Zahnrad). Ein selbst installiertes spotdl bleibt unangetastet.
            </div>
            <div class="row">
              <span class="lbl">Auf Updates prüfen</span>
              <button class="btn btn-sm" onclick={checkToolUpdates} disabled={toolCheckRunning}>
                {toolCheckRunning ? 'Prüfe…' : 'Jetzt prüfen'}
              </button>
            </div>
            {#if $updateProgress}
              <div class="upd-status">{$updateProgress.text}</div>
            {/if}
          </div>

        <!-- ── REMOTE ─────────────────────────────────────────────────── -->
        {:else if tab === 'remote'}

          <div class="group">
            <div class="group-title">Handy-Dienste</div>
            <p class="remote-desc">Fernbedienung und Musikwünsche laufen über einen kleinen Server im WLAN — kein Internet, keine App. Beide lassen sich einzeln ein- und ausschalten.</p>
            <label class="row-toggle">
              <span class="row-label">Beim Start die zuletzt eingeschalteten wieder starten</span>
              <input type="checkbox" checked={$remoteAutostart}
                onchange={(e) => send({ type: 'set_remote_autostart', value: e.target.checked })} />
            </label>
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
            <div class="group-title">Auto-Mix</div>
            <div class="hint">Sucht ähnliche Songs aus der Bibliothek wenn die Queue leer ist. Aktivierbar im Queue-Menü oder unter Wiedergabe → Queue-Ende.</div>
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
  .content { flex: 1; overflow-y: auto; padding: var(--sp-4) var(--sp-5) var(--sp-6); display: flex; flex-direction: column; gap: var(--sp-5); }
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
  .hint strong { color: var(--c-tx1); }
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
  .toggle-wrap, .row-toggle { display: flex; align-items: center; gap: var(--sp-2); cursor: pointer; font-size: var(--fs-body); color: var(--c-tx1); }
  .row-toggle { justify-content: space-between; min-height: var(--btn-h); }
  .toggle-lbl, .row-label { color: var(--c-tx1); }
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
  .tool-row { display: flex; align-items: center; gap: var(--sp-3); min-height: var(--btn-h); }
  .tool-name { width: 72px; font-size: var(--fs-body); font-weight: 600; color: var(--c-tx1); }
  .tool-ver { flex: 1; font-family: Consolas, 'Cascadia Mono', monospace; font-size: var(--fs-sm); color: var(--c-tx3); }
  .upd-note { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
  .upd-status { font-size: var(--fs-sm); color: var(--c-accent-tx); }

  /* Darstellung */
  .key-samples { display: flex; gap: var(--sp-2); flex-wrap: wrap; font-size: var(--fs-body); }

  /* Remote */
  .remote-desc { font-size: var(--fs-sm); line-height: 1.5; color: var(--c-tx3); max-width: 60ch; }
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
