<script>
  import { settingsOpen, send, backendLogs, notes } from '../stores/ws.js'
  const win = window.electron ?? {}

  let helpOpen = $state(false)
  let logOpen  = $state(false)
  let logFilter = $state('')
  let autoRefresh = $state(true)
  let logEl = $state(null)

  // â”€â”€ Notizblock â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  let notesOpen = $state(false)
  let notesSaved = $state(true)

  $effect(() => {
    if (notesOpen) send({ type: 'get_notes' })
  })

  function onNotesInput(e) {
    notes.set(e.target.value)
    notesSaved = false
    send({ type: 'save_notes', text: e.target.value })
    clearTimeout(_notesSavedTimer)
    _notesSavedTimer = setTimeout(() => notesSaved = true, 600)
  }
  let _notesSavedTimer

  $effect(() => {
    if (logOpen) {
      send({ type: 'get_logs' })
    }
  })

  // Auto-refresh alle 2 Sekunden wenn Panel offen
  $effect(() => {
    if (!logOpen || !autoRefresh) return () => {}
    const id = setInterval(() => send({ type: 'get_logs' }), 2000)
    return () => clearInterval(id)
  })

  // Scroll ans Ende wenn neue Logs kommen
  $effect(() => {
    const _ = $backendLogs
    if (logEl && autoRefresh) {
      setTimeout(() => logEl.scrollTop = logEl.scrollHeight, 20)
    }
  })

  const filteredLogs = $derived(
    logFilter.trim()
      ? $backendLogs.filter(l => l.toLowerCase().includes(logFilter.toLowerCase()))
      : $backendLogs
  )

  function lineClass(line) {
    if (line.includes('FEHLER') || line.includes('Error') || line.includes('error') || line.includes('Exception') || line.includes('Traceback')) return 'log-err'
    if (line.includes('[lufs]') || line.includes('[bpm]') || line.includes('[analyze')) return 'log-analyze'
    if (line.includes('[handler error]') || line.includes('Traceback')) return 'log-err'
    if (line.includes('ffmpeg:')) return 'log-ffmpeg'
    return 'log-info'
  }

  const shortcuts = [
    ['Space',      'Pause / Fortsetzen'],
    ['â† / â†’',     'Â±5 Sekunden springen'],
    ['Strg + â†’',  'NÃ¤chster Track'],
    ['Strg + â†',  'Track-Anfang / Vorheriger'],
    ['N',          'NÃ¤chster Track'],
    ['P',          'Vorheriger Track'],
    ['Entf',       'Markierten Queue-Eintrag entfernen'],
    ['F11',        'Vollbild umschalten'],
  ]

  // â”€â”€ Auto-Update â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  let updateVersion   = $state(null)   // z.B. "1.3.0"
  let updateProgress  = $state(null)   // 0-100 wÃ¤hrend Download
  let updateReady     = $state(false)  // Download abgeschlossen

  $effect(() => {
    win.onUpdateAvailable?.((v) => { updateVersion = v })
    win.onUpdateProgress?.((p) => { updateProgress = p })
    win.onUpdateDownloaded?.(() => { updateReady = true; updateProgress = null })
  })
</script>

<div class="titlebar">
  <div class="brand">
    <svg class="logo-icon" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <circle cx="20" cy="20" r="19" fill="#0d1a2e"/>
      <rect x="5"  y="16" width="4" height="9"  rx="1.5" fill="#e07800"/>
      <rect x="11" y="10" width="4" height="15" rx="1.5" fill="#e07800"/>
      <rect x="17" y="13" width="4" height="12" rx="1.5" fill="#f59332"/>
      <rect x="23" y="7"  width="4" height="18" rx="1.5" fill="#e07800"/>
      <rect x="29" y="11" width="4" height="14" rx="1.5" fill="#f59332"/>
      <line x1="20" y1="29" x2="20" y2="35" stroke="#3b82f6" stroke-width="2" stroke-linecap="round"/>
      <polyline points="16,32 20,36 24,32" fill="none" stroke="#3b82f6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    <span class="app-name"><span class="nm-synthi">Synthi</span><span class="nm-mix">MIX</span></span>
    <span class="version">v1.3</span>
  </div>
  <div class="tb-actions" style="-webkit-app-region:no-drag">

    {#if updateReady}
      <button class="update-btn ready" onclick={() => win.installUpdate?.()} title="Update installieren und neu starten">
        â†‘ v{updateVersion} installieren
      </button>
    {:else if updateProgress !== null}
      <span class="update-btn downloading">
        â†“ {updateProgress}%
      </span>
    {:else if updateVersion}
      <span class="update-btn available" title="Update wird heruntergeladenâ€¦">
        â†“ v{updateVersion}
      </span>
    {/if}

    <div class="log-wrap">
      <button class="tb-btn tb-log" onclick={() => { logOpen = !logOpen; helpOpen = false; notesOpen = false }} title="Backend-Log">â¬›</button>
      {#if logOpen}
        <div class="log-panel">
          <div class="log-hdr">
            <span>BACKEND LOG</span>
            <input class="log-filter" bind:value={logFilter} placeholder="Filterâ€¦" />
            <label class="log-auto">
              <input type="checkbox" bind:checked={autoRefresh} />
              live
            </label>
            <button onclick={() => send({ type: 'get_logs' })}>â†º</button>
            <button onclick={() => navigator.clipboard.writeText(filteredLogs.join('\n'))} title="Alles kopieren">âŽ˜</button>
            <button onclick={() => logOpen = false}>âœ•</button>
          </div>
          <div class="log-body" bind:this={logEl}>
            {#each filteredLogs as line}
              <div class="log-line {lineClass(line)}">{line}</div>
            {:else}
              <div class="log-empty">Keine Logs</div>
            {/each}
          </div>
        </div>
      {/if}
    </div>
    <div class="notes-wrap">
      <button class="tb-btn tb-notes" onclick={() => { notesOpen = !notesOpen; helpOpen = false; logOpen = false }} title="Notizblock">
        <svg viewBox="0 0 14 14" width="13" height="13" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="1.5" y="1.5" width="9" height="11" rx="1" stroke="currentColor" stroke-width="1.2"/>
          <line x1="4" y1="5" x2="8.5" y2="5" stroke="currentColor" stroke-width="1"/>
          <line x1="4" y1="7.2" x2="8.5" y2="7.2" stroke="currentColor" stroke-width="1"/>
          <line x1="4" y1="9.4" x2="7" y2="9.4" stroke="currentColor" stroke-width="1"/>
          <path d="M10.5 8.5 L12.5 6.5 L13.5 7.5 L11.5 9.5 L10 10 Z" fill="currentColor"/>
        </svg>
      </button>
      {#if notesOpen}
        <div class="notes-panel">
          <div class="notes-hdr">
            <span>NOTIZBLOCK</span>
            <span class="notes-status">{notesSaved ? 'gespeichert' : 'speichertâ€¦'}</span>
            <button onclick={() => notesOpen = false}>âœ•</button>
          </div>
          <textarea class="notes-body" placeholder="Bugs, Ideen, Probleme wÃ¤hrend des Sets notierenâ€¦"
                    value={$notes} oninput={onNotesInput}></textarea>
        </div>
      {/if}
    </div>
    <div class="help-wrap">
      <button class="tb-btn tb-help" onclick={() => { helpOpen = !helpOpen; notesOpen = false }} title="TastenkÃ¼rzel">?</button>
      {#if helpOpen}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="help-backdrop" onclick={() => helpOpen = false}></div>
        <div class="help-panel">
          <div class="help-hdr">
            <span>TASTENKÃœRZEL</span>
            <button onclick={() => helpOpen = false}>âœ•</button>
          </div>
          {#each shortcuts as [key, desc]}
            <div class="help-row">
              <kbd>{key}</kbd>
              <span>{desc}</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>
    <button class="tb-btn" onclick={() => settingsOpen.set(true)} title="Einstellungen">âš™</button>
  </div>
  <div class="controls" role="toolbar">
    <button onclick={() => win.minimize?.()} aria-label="Minimieren">â”€</button>
    <button onclick={() => win.maximize?.()} aria-label="Maximieren">â–¡</button>
    <button class="close" onclick={() => win.close?.()} aria-label="SchlieÃŸen">âœ•</button>
  </div>
</div>

<style>
  .titlebar {
    display: flex;
    align-items: center;
    height: 32px;
    padding: 0 12px 0 16px;
    background: #060a10;
    -webkit-app-region: drag;
    flex-shrink: 0;
    border-bottom: 1px solid #101828;
  }

  .brand { display: flex; align-items: center; gap: 7px; flex-shrink: 0; }
  .logo-icon { width: 22px; height: 22px; flex-shrink: 0; }
  .app-name { font-size: 12px; font-weight: 600; letter-spacing: 0.08em; }
  .nm-synthi { color: #e07800; }
  .nm-mix    { color: #3b82f6; }
  .version   { font-size: 9px; color: #2a3a54; margin-left: 2px; align-self: flex-end; margin-bottom: 3px; }

  /* Update-Benachrichtigung */
  .update-btn {
    font-size: 10px; font-weight: 600; border-radius: 3px;
    padding: 3px 8px; cursor: default; white-space: nowrap;
    border: 1px solid transparent; letter-spacing: 0.05em;
  }
  .update-btn.available    { color: #60a0e0; border-color: #1a3a60; background: #0d1a2e; }
  .update-btn.downloading  { color: #60c060; border-color: #1a3a1a; background: #0d1a0d; }
  .update-btn.ready        { color: #fff; border-color: #2a7a2a; background: #1a5a1a; cursor: pointer; }
  .update-btn.ready:hover  { background: #1e6e1e; }

  .tb-actions { display:flex; align-items:center; gap:4px; margin-left:auto; -webkit-app-region:no-drag; }
  .tb-btn { background:none; border:none; color:#3a5070; font-size:14px; width:28px; height:28px; cursor:pointer; border-radius:3px; display:flex; align-items:center; justify-content:center; transition:color .1s, background .1s; }
  .tb-btn:hover { color:#c8d8f0; background:#141e30; }
  .tb-help { font-size:11px; font-weight:700; border:1px solid #1a2838; border-radius:50%; width:18px; height:18px; }

  /* Log button */
  .tb-log { font-size: 8px; }

  /* Log panel */
  .log-wrap { position: relative; }
  .log-panel {
    position: absolute; top: calc(100% + 6px); right: 0;
    background: #040810; border: 1px solid #1a2d48;
    border-radius: 5px; z-index: 1000; width: 560px;
    box-shadow: 0 8px 28px rgba(0,0,0,.9);
    display: flex; flex-direction: column; max-height: 480px;
  }
  .log-hdr {
    display: flex; align-items: center; gap: 6px;
    padding: 6px 10px; border-bottom: 1px solid #0e1828;
    flex-shrink: 0;
  }
  .log-hdr span { font-size: 9px; font-weight: 700; letter-spacing: .15em; color: #3a5068; flex-shrink: 0; }
  .log-filter {
    flex: 1; background: #060c18; border: 1px solid #1a2838;
    border-radius: 3px; color: #7a9ab8; font-size: 10px;
    padding: 2px 6px; outline: none; min-width: 0;
  }
  .log-auto { display: flex; align-items: center; gap: 3px; font-size: 10px; color: #3a5068; flex-shrink: 0; cursor: pointer; }
  .log-hdr button {
    background: none; border: 1px solid #1a2838; border-radius: 3px;
    color: #3a5068; font-size: 11px; cursor: pointer;
    padding: 1px 6px; transition: color .1s;
  }
  .log-hdr button:hover { color: #8aaac8; }
  .log-body {
    overflow-y: auto; flex: 1; padding: 4px 0;
    font-family: 'Consolas', 'Courier New', monospace; font-size: 10px;
    user-select: text; -webkit-user-select: text;
  }
  .log-line {
    padding: 1px 10px; white-space: pre-wrap; word-break: break-all;
    line-height: 1.5; user-select: text; -webkit-user-select: text;
  }
  .log-info    { color: #3a5870; }
  .log-analyze { color: #5a8a5a; }
  .log-ffmpeg  { color: #4a6a8a; }
  .log-err     { color: #c04040; background: #0e0606; }
  .log-empty   { padding: 20px; text-align: center; color: #2a3a54; font-size: 11px; }

  /* Notizblock */
  .notes-wrap { position: relative; }
  .notes-panel {
    position: absolute; top: calc(100% + 6px); right: 0;
    background: #040810; border: 1px solid #1a2d48;
    border-radius: 5px; z-index: 1000; width: 360px;
    box-shadow: 0 8px 28px rgba(0,0,0,.9);
    display: flex; flex-direction: column;
  }
  .notes-hdr {
    display: flex; align-items: center; gap: 6px;
    padding: 6px 10px; border-bottom: 1px solid #0e1828;
    flex-shrink: 0;
  }
  .notes-hdr span:first-child { font-size: 9px; font-weight: 700; letter-spacing: .15em; color: #3a5068; flex-shrink: 0; }
  .notes-status { flex: 1; font-size: 9px; color: #2a4058; text-align: right; }
  .notes-hdr button {
    background: none; border: 1px solid #1a2838; border-radius: 3px;
    color: #3a5068; font-size: 11px; cursor: pointer;
    padding: 1px 6px; transition: color .1s;
  }
  .notes-hdr button:hover { color: #8aaac8; }
  .notes-body {
    width: 100%; height: 260px; resize: vertical;
    background: #03060c; border: none; outline: none;
    color: #c8d8f0; font-size: 12px; line-height: 1.5;
    padding: 10px; box-sizing: border-box;
    font-family: 'Segoe UI', system-ui, sans-serif;
  }
  .notes-body::placeholder { color: #2a3a54; }

  /* Help popup */
  .help-wrap { position: relative; }
  .help-backdrop { position: fixed; inset: 0; z-index: 900; }
  .help-panel {
    position: absolute; top: calc(100% + 6px); right: 0;
    background: #070d18; border: 1px solid #1a2d48;
    border-radius: 5px; z-index: 1000; min-width: 260px;
    box-shadow: 0 8px 28px rgba(0,0,0,.8);
    overflow: hidden;
  }
  .help-hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 12px 6px;
    border-bottom: 1px solid #0e1828;
  }
  .help-hdr span { font-size: 9px; font-weight: 700; letter-spacing: .15em; color: #3a5068; }
  .help-hdr button {
    background: none; border: none; color: #3a5068; font-size: 11px;
    cursor: pointer; padding: 2px 4px; border-radius: 2px; transition: color .1s;
  }
  .help-hdr button:hover { color: #e06060; }
  .help-row {
    display: flex; align-items: center; gap: 12px;
    padding: 5px 12px; border-bottom: 1px solid #080d18;
  }
  .help-row:last-child { border-bottom: none; }
  kbd {
    font-family: inherit; font-size: 10px; font-weight: 600;
    color: #8aaac8; background: #0c1828; border: 1px solid #1a3048;
    border-radius: 3px; padding: 2px 6px; white-space: nowrap;
    min-width: 90px; text-align: center; flex-shrink: 0;
  }
  .help-row span { font-size: 11px; color: #5a7890; }

  .controls { display: flex; -webkit-app-region: no-drag; }
  .controls button {
    background: none; border: none; color: #3a4a64;
    width: 36px; height: 32px; font-size: 12px; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background 0.1s, color 0.1s;
  }
  .controls button:hover { background: #141e30; color: #8a9ab4; }
  .controls button.close:hover { background: #6a1a1a; color: #ff6060; }
</style>
