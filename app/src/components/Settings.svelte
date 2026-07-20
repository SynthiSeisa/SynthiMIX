<script>
  import { settings, settingsOpen, appSettings, send, toolsInfo, updateProgress,
           loudnormOnDl, loudnormTarget, loudnormTp, autoMixEnabled, playMode,
           playlistFolderEnabled, dlFilenameFormat, downloadDir, remoteStatus,
           autoScanIntervalMin, scanRecursive, remoteAutostart } from '../stores/ws.js'

  let tab = $state('playback')

  const TABS = [
    { id: 'playback', label: 'Wiedergabe', icon: '▶' },
    { id: 'fade',     label: 'Blend',      icon: '⇌' },
    { id: 'download', label: 'Download',   icon: '↓' },
    { id: 'system',   label: 'System',     icon: '⚙' },
    { id: 'remote',   label: 'Remote',     icon: '⊕' },
    { id: 'info',     label: 'Info',       icon: 'ℹ' },
  ]

  function close() { settingsOpen.set(false) }

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

  let qrCanvas = $state(null)
  $effect(() => {
    const url = $remoteStatus?.url
    if (qrCanvas && url) {
      QRCode.toCanvas(qrCanvas, url, {
        width: 160, margin: 2,
        color: { dark: '#e07800', light: '#070c18' }
      })
    }
  })
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
        <span class="hdr-appname"><span style="color:#e07800">Synthi</span><span style="color:#3b82f6">MIX</span></span>
        <span class="hdr-title">Einstellungen</span>
      </div>
      <button class="close-btn" onclick={close}>✕</button>
    </div>

    <div class="layout">

      <!-- Left tab nav -->
      <nav class="tab-nav">
        {#each TABS as t}
          <button class="tab-btn {tab === t.id ? 'active' : ''}" onclick={() => tab = t.id}>
            <span class="tab-icon">{t.icon}</span>
            <span class="tab-label">{t.label}</span>
          </button>
        {/each}
      </nav>

      <!-- Right content -->
      <div class="content">

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
            <div class="group-title">Normalisierung</div>
            <div class="row">
              <span class="lbl">Lautstärke angleichen</span>
              <button class="tog {$appSettings.normalizeVolume ? 'on' : ''}"
                onclick={() => appSettings.update(s => ({...s, normalizeVolume: !s.normalizeVolume}))}
                title={$appSettings.normalizeVolume ? 'Aktiv' : 'Inaktiv'}></button>
            </div>
            {#if $appSettings.normalizeVolume}
              <div class="row indent">
                <span class="lbl">Ziel-LUFS</span>
                <input type="range" min="-23" max="-8" step="1" value={$appSettings.targetLUFS}
                  oninput={(e) => appSettings.update(s => ({...s, targetLUFS: +e.target.value}))} />
                <span class="val">{$appSettings.targetLUFS} LUFS</span>
              </div>
            {/if}
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
            <div class="radio-group">
              {#each [['stop','Stopp'],['automix','Auto-Mix'],['repeat','Wiederholen']] as [val, label]}
                <button class="radio-opt {queueEnd === val ? 'active' : ''}"
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
                oninput={(e) => send({ type: 'set_crossfade', seconds: +e.target.value })} />
              <span class="val">{$settings.crossfade_s === 0 ? 'aus' : $settings.crossfade_s + 's'}</span>
            </div>
          </div>

          <div class="group">
            <div class="group-title">Überblend-Kurve</div>
            <div class="hint">Bestimmt den Lautstärkeverlauf während des Übergangs.</div>
            <div class="radio-group">
              {#each [['cosine','Kosinus'],['linear','Linear'],['scurve','S-Kurve']] as [val, label]}
                <button class="radio-opt {$appSettings.cfCurve === val ? 'active' : ''}"
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
                  oninput={(e) => { loudnormTarget.set(+e.target.value); sendLoudnorm() }} />
                <span class="val">{$loudnormTarget} LUFS</span>
              </div>
              <div class="row indent">
                <span class="lbl">True Peak</span>
                <input type="range" min="-9" max="-0.5" step="0.5" value={$loudnormTp}
                  oninput={(e) => { loudnormTp.set(+e.target.value); sendLoudnorm() }} />
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
              <button class="action-btn" onclick={pickDownloadFolder}>Ordner wählen</button>
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
                  <button class="seg-btn {$autoScanIntervalMin === opt.value ? 'active' : ''}"
                          onclick={() => setAutoScanInterval(opt.value)}>
                    {opt.label}
                  </button>
                {/each}
              </div>
            </div>
            <p class="hint-text">Bibliotheksordner automatisch neu scannen</p>
            <div class="row" style="margin-top: 8px">
              <span class="lbl">Unterordner</span>
              <label class="toggle-wrap">
                <input type="checkbox" checked={$scanRecursive}
                       onchange={(e) => send({ type: 'set_scan_recursive', enabled: e.target.checked })} />
                <span class="toggle-lbl">beim Scannen einschließen</span>
              </label>
            </div>
          </div>

          <div class="group">
            <div class="group-title">Konfiguration</div>
            <div class="row">
              <span class="lbl">Einstellungen</span>
              <div class="row-btns">
                <button class="action-btn" onclick={exportSettings} title="Als JSON-Datei herunterladen">↓ Exportieren</button>
                <button class="action-btn" onclick={importSettings} title="JSON-Datei einlesen">↑ Importieren</button>
              </div>
            </div>
          </div>

          <div class="group">
            <div class="group-title">Tools</div>

            <div class="tool-row">
              <span class="tool-name">yt-dlp</span>
              <span class="tool-ver">{$toolsInfo.ytdlp_version ?? '—'}</span>
              <button class="action-btn"
                onclick={() => send({ type: 'update_ytdlp' })}
                disabled={!!$updateProgress}>
                ↑ Update
              </button>
            </div>
            <div class="tool-row">
              <span class="tool-name">ffmpeg</span>
              <span class="tool-ver">{$toolsInfo.ffmpeg_version ?? '—'}</span>
            </div>
            {#if $updateProgress}
              <div class="upd-status">{$updateProgress.text}</div>
            {/if}
          </div>

        <!-- ── REMOTE ─────────────────────────────────────────────────── -->
        {:else if tab === 'remote'}

          <div class="group">
            <div class="group-title">Handy-Fernbedienung</div>
            <p class="remote-desc">Startet einen lokalen Server im WLAN. Öffne die angezeigte URL im Browser deines Handys — kein Internet, keine App nötig.</p>
            <label class="row-toggle">
              <span class="row-label">Bei Start automatisch starten</span>
              <input type="checkbox" checked={$remoteAutostart}
                onchange={(e) => send({ type: 'set_remote_autostart', value: e.target.checked })} />
            </label>

            {#if $remoteStatus?.running}
              <div class="remote-status on">
                <span class="remote-dot on"></span>
                Server läuft
              </div>
              <div class="remote-url">
                <span class="url-label">URL:</span>
                <span class="url-val" ondblclick={() => window.electron?.openPath($remoteStatus?.url)}
                      title="Doppelklick: im Browser öffnen">{$remoteStatus.url}</span>
                <button class="copy-url-btn {urlCopied ? 'copied' : ''}" onclick={copyRemoteUrl} title="In Zwischenablage kopieren">
                  {urlCopied ? '✓' : '⎘'}
                </button>
              </div>
              <div class="remote-url-actions">
                <span class="remote-hint">Im Handy-Browser öffnen (gleiches WLAN)</span>
                <button class="action-btn" onclick={() => window.electron?.openPath($remoteStatus?.url)} title="Im Standard-Browser öffnen">Im Browser öffnen ↗</button>
              </div>
              <div class="qr-wrap">
                <canvas bind:this={qrCanvas} class="qr-canvas"></canvas>
                <span class="qr-hint">Mit Kamera scannen</span>
              </div>
              <button class="action-btn danger" onclick={() => send({ type: 'remote_stop' })}>Server stoppen</button>
            {:else}
              <div class="remote-status off">
                <span class="remote-dot off"></span>
                {$remoteStatus?.error ? 'Fehler: ' + $remoteStatus.error : 'Gestoppt'}
              </div>
              <button class="action-btn" onclick={() => send({ type: 'remote_start' })}>Server starten</button>
            {/if}
          </div>

          <div class="group">
            <div class="group-title">Funktionen</div>
            <div class="info-row">📱 Aktueller Track + Steuerung (Play/Pause/Skip)</div>
            <div class="info-row">🔊 Lautstärke regulieren</div>
            <div class="info-row">📋 Warteschlange anzeigen, Reihenfolge ändern, Tracks entfernen</div>
            <div class="info-row">🔍 Bibliothek durchsuchen und Tracks hinzufügen</div>
          </div>

        <!-- ── INFO ───────────────────────────────────────────────────── -->
        {:else if tab === 'info'}

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
    background: rgba(0,0,0,.72); backdrop-filter: blur(3px);
    display: flex; align-items: center; justify-content: center;
  }

  .panel {
    background: var(--c-bg3); border: 1px solid var(--c-br2); border-radius: 8px;
    width: 660px; height: min(84vh, 560px);
    display: flex; flex-direction: column;
    box-shadow: 0 20px 60px rgba(0,0,0,.85);
    overflow: hidden;
  }

  /* ── Header ────────────────────────────────────────────────────────────── */
  .hdr {
    display: flex; align-items: center; padding: 13px 20px;
    border-bottom: 1px solid var(--c-br1); flex-shrink: 0;
    background: var(--c-bg2);
  }
  .hdr-brand { display: flex; align-items: center; gap: 8px; }
  .hdr-icon  { width: 26px; height: 26px; flex-shrink: 0; }
  .hdr-appname { font-size: 14px; font-weight: 600; letter-spacing: 0.06em; }
  .hdr-title {
    font-size: 10px; font-weight: 600; letter-spacing: 1.5px; color: var(--c-tx6);
    padding-left: 10px; margin-left: 2px; border-left: 1px solid var(--c-br2);
  }
  .close-btn {
    margin-left: auto; background: none; border: none; color: var(--c-tx7);
    font-size: 13px; cursor: pointer; padding: 4px 8px; border-radius: 3px;
    transition: color .1s, background .1s;
  }
  .close-btn:hover { color: var(--c-red); background: var(--c-red-bg); }

  /* ── Two-column layout ─────────────────────────────────────────────────── */
  .layout {
    display: flex; flex: 1; overflow: hidden;
  }

  /* ── Tab nav ────────────────────────────────────────────────────────────── */
  .tab-nav {
    width: 130px; flex-shrink: 0;
    background: var(--c-bg2);
    border-right: 1px solid var(--c-br1);
    display: flex; flex-direction: column;
    padding: 8px 0;
    overflow-y: auto;
  }

  .tab-btn {
    display: flex; align-items: center; gap: 8px;
    padding: 9px 14px;
    background: none; border: none;
    color: var(--c-tx5); font-size: 12px; text-align: left;
    cursor: pointer; transition: background .1s, color .1s;
    border-left: 3px solid transparent;
  }
  .tab-btn:hover { background: var(--c-sel); color: var(--c-tx3); }
  .tab-btn.active {
    background: var(--c-sel); color: var(--c-accent);
    border-left-color: var(--c-accent);
  }
  .tab-icon { font-size: 13px; width: 16px; text-align: center; flex-shrink: 0; }
  .tab-label { font-size: 11px; font-weight: 500; }

  /* ── Content area ───────────────────────────────────────────────────────── */
  .content {
    flex: 1; overflow-y: auto; padding: 6px 0;
  }

  .group {
    padding: 14px 22px 16px;
    border-bottom: 1px solid var(--c-bg2);
  }
  .group:last-child { border-bottom: none; }

  .group-title {
    font-size: 9px; font-weight: 700; letter-spacing: 1.8px;
    color: var(--c-tx5); text-transform: uppercase; margin-bottom: 10px;
  }

  .hint {
    font-size: 10px; color: var(--c-tx7); line-height: 1.5;
    margin-top: 2px;
  }

  /* ── Rows ───────────────────────────────────────────────────────────────── */
  .row {
    display: flex; align-items: center; gap: 12px;
    padding: 5px 0; min-height: 30px;
  }
  .row.indent { padding-left: 16px; }
  .row.dimmed { opacity: 0.4; pointer-events: none; }

  .lbl {
    font-size: 12px; color: var(--c-tx4); flex: 1; min-width: 0;
  }

  .val {
    font-size: 11px; color: var(--c-tx5);
    min-width: 58px; text-align: right; flex-shrink: 0;
    font-variant-numeric: tabular-nums;
  }
  .val.agg { min-width: 100px; }

  input[type=range] {
    width: 120px; flex-shrink: 0;
    accent-color: var(--c-accent); height: 2px; cursor: pointer;
  }

  /* ── Pill toggle ────────────────────────────────────────────────────────── */
  .tog {
    width: 36px; height: 20px; border-radius: 10px;
    background: var(--c-br2); border: 1px solid var(--c-br2);
    cursor: pointer; position: relative; flex-shrink: 0;
    transition: background .18s, border-color .18s;
  }
  .tog::after {
    content: ''; position: absolute;
    top: 3px; left: 3px;
    width: 12px; height: 12px; border-radius: 50%;
    background: var(--c-tx6);
    transition: left .18s, background .18s;
  }
  .tog.on { background: var(--c-accent); border-color: #c06000; }
  .tog.on::after { left: 19px; background: #fff; }

  /* ── Radio groups ───────────────────────────────────────────────────────── */
  .radio-group { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  .radio-opt {
    padding: 4px 14px; border-radius: 4px;
    border: 1px solid var(--c-br2); background: none;
    color: var(--c-tx5); font-size: 11px; cursor: pointer;
    transition: all .12s;
  }
  .radio-opt:hover { border-color: var(--c-tx7); color: var(--c-tx3); }
  .radio-opt.active { border-color: var(--c-accent); color: var(--c-accent); background: #e0780010; }

  /* Vertical radio (filename format) */
  .radio-group.vertical { flex-direction: column; gap: 4px; margin-top: 8px; }
  .radio-opt-v {
    display: flex; align-items: baseline; gap: 10px;
    padding: 7px 12px; border-radius: 4px;
    border: 1px solid var(--c-br2); background: none;
    cursor: pointer; text-align: left; transition: all .12s;
  }
  .radio-opt-v:hover { border-color: var(--c-tx7); background: var(--c-sel); }
  .radio-opt-v.active { border-color: var(--c-accent); background: #e0780008; }
  .ro-label { font-size: 12px; color: var(--c-tx4); flex-shrink: 0; }
  .radio-opt-v.active .ro-label { color: var(--c-accent); }
  .ro-example { font-size: 10px; color: var(--c-tx7); font-family: monospace; }
  .radio-opt-v.active .ro-example { color: var(--c-accent); }

  /* ── Action button ──────────────────────────────────────────────────────── */
  .action-btn {
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: 3px;
    color: var(--c-tx5); font-size: 11px; padding: 4px 14px; cursor: pointer;
    transition: border-color .1s, color .1s; flex-shrink: 0;
  }
  .action-btn:hover:not(:disabled) { border-color: var(--c-accent); color: var(--c-accent); }
  .action-btn:disabled { color: var(--c-tx7); cursor: default; }

  /* ── Segment buttons (Auto-Scan etc.) ───────────────────────────────────── */
  .btn-group { display: flex; gap: 4px; flex-wrap: wrap; }
  .seg-btn {
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: 3px;
    color: var(--c-tx5); font-size: 11px; padding: 3px 10px; cursor: pointer;
    transition: border-color .1s, color .1s;
  }
  .seg-btn:hover { border-color: var(--c-accent); color: var(--c-accent); }
  .seg-btn.active { border-color: var(--c-accent); color: var(--c-accent); background: var(--c-act-bg); }
  .hint-text { font-size: 10px; color: var(--c-tx7); margin: 4px 0 0; }
  .toggle-wrap { display: flex; align-items: center; gap: 7px; cursor: pointer; }
  .toggle-wrap input[type=checkbox] { accent-color: var(--c-accent); width: 14px; height: 14px; cursor: pointer; }
  .toggle-lbl { font-size: 11px; color: var(--c-tx4); }
  .row-btns { display: flex; gap: 6px; }

  /* ── Tools ──────────────────────────────────────────────────────────────── */
  .tool-row {
    display: flex; align-items: center; gap: 10px; padding: 5px 0; font-size: 11px;
  }
  .tool-name { color: var(--c-tx4); width: 56px; flex-shrink: 0; }
  .tool-ver  { color: var(--c-tx6); flex: 1; font-family: monospace; font-size: 10px; }
  .upd-status { font-size: 10px; color: var(--c-accent); padding: 6px 0; }

  /* ── Shortcuts table ────────────────────────────────────────────────────── */
  .shortcuts {
    width: 100%; border-collapse: collapse; margin-top: 4px;
  }
  .shortcuts td {
    padding: 5px 8px; font-size: 11px; border-bottom: 1px solid var(--c-bg2);
    vertical-align: middle;
  }
  .shortcuts tr:last-child td { border-bottom: none; }
  .shortcuts td:first-child {
    color: var(--c-tx4); font-family: monospace; font-size: 10px;
    white-space: nowrap; width: 140px;
    background: var(--c-bg); border-radius: 3px; padding: 4px 8px;
  }
  .shortcuts td:last-child { color: var(--c-tx5); padding-left: 14px; }

  /* ── Remote Tab ─────────────────────────────────────────────────────────── */
  .remote-desc { font-size: 11px; color: var(--c-tx5); line-height: 1.6; margin-bottom: 10px; }
  .remote-status { display: flex; align-items: center; gap: 7px; font-size: 12px; margin-bottom: 10px; }
  .remote-status.on { color: #75d595; }
  .remote-status.off { color: var(--c-tx5); }
  .remote-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .remote-dot.on  { background: #75d595; }
  .remote-dot.off { background: var(--c-tx6); }
  .remote-url { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .url-label { font-size: 10px; color: var(--c-tx5); flex-shrink: 0; }
  .url-val   { font-size: 13px; color: var(--c-blue); font-family: monospace; font-weight: 600; flex: 1; }
  .copy-url-btn {
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: 3px;
    color: var(--c-tx5); font-size: 13px; width: 26px; height: 22px;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; transition: border-color .1s, color .1s;
  }
  .copy-url-btn:hover { border-color: var(--c-tx5); color: var(--c-tx3); }
  .copy-url-btn.copied { border-color: var(--c-green); color: #75d595; }
  .remote-url-actions { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .remote-hint { font-size: 10px; color: var(--c-tx6); }
  .url-val { cursor: default; }
  .url-val:hover { color: var(--c-tx2); }
  .qr-wrap { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px 0 8px; }
  .qr-canvas { border-radius: 6px; border: 1px solid var(--c-br2); }
  .qr-hint { font-size: 9px; color: var(--c-tx6); letter-spacing: .06em; }
  .action-btn.danger { border-color: var(--c-red); color: var(--c-red); }
  .action-btn.danger:hover { border-color: var(--c-red); color: var(--c-red); }
  .info-row { font-size: 12px; color: var(--c-tx5); padding: 5px 0; border-bottom: 1px solid var(--c-br1); }
  .info-row:last-child { border-bottom: none; }
</style>
