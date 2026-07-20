<script>
  import { settingsOpen, send, backendLogs, notes } from '../stores/ws.js'
  const win = window.electron ?? {}

  // ── Theme toggle ──────────────────────────────────────────────────────────
  let isDark = $state((localStorage.getItem('synthimix-theme') || 'dark') === 'dark')
  function toggleTheme() {
    isDark = !isDark
    const t = isDark ? 'dark' : 'light'
    localStorage.setItem('synthimix-theme', t)
    document.documentElement.setAttribute('data-theme', t)
  }

  let helpOpen = $state(false)
  let logOpen  = $state(false)
  let logFilter = $state('')
  let autoRefresh = $state(true)
  let logEl = $state(null)

  // ── Notizblock ────────────────────────────────────────────────────────────
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
    ['← / →',     '±5 Sekunden springen'],
    ['Strg + →',  'Nächster Track'],
    ['Strg + ←',  'Track-Anfang / Vorheriger'],
    ['N',          'Nächster Track'],
    ['P',          'Vorheriger Track'],
    ['Entf',       'Markierten Queue-Eintrag entfernen'],
    ['F11',        'Vollbild umschalten'],
  ]

  // ── Auto-Update ───────────────────────────────────────────────────────────
  let updateVersion     = $state(null)
  let updateSize        = $state(null)
  let updateProgress    = $state(null)
  let updateDownloading = $state(false)
  let updateReady       = $state(false)
  let updateDismissed   = $state(false)

  function formatSize(bytes) {
    if (!bytes) return ''
    const mb = bytes / (1024 * 1024)
    return mb >= 1000 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`
  }

  $effect(() => {
    win.onUpdateAvailable?.((v, size) => { updateVersion = v; updateSize = size; updateDismissed = false })
    win.onUpdateProgress?.((p) => { updateProgress = p })
    win.onUpdateDownloaded?.(() => { updateReady = true; updateDownloading = false; updateProgress = null })
    win.onUpdateError?.((msg) => { console.warn('[updater]', msg) })
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
    <span class="version">v1.3.7</span>
  </div>
  <div class="tb-actions" style="-webkit-app-region:no-drag">

    {#if updateReady && updateDismissed}
      <button class="update-btn ready" onclick={() => win.installUpdate?.()} title="Update installieren und neu starten">
        &#8593; v{updateVersion} installieren
      </button>
    {:else if updateProgress !== null && updateDismissed}
      <span class="update-btn downloading">
        &#8595; {updateProgress}%
      </span>
    {/if}

    <div class="log-wrap">
      <button class="tb-btn tb-log" onclick={() => { logOpen = !logOpen; helpOpen = false; notesOpen = false }} title="Backend-Log">&#11035;</button>
      {#if logOpen}
        <div class="log-panel">
          <div class="log-hdr">
            <span>BACKEND LOG</span>
            <input class="log-filter" bind:value={logFilter} placeholder="Filter&#8230;" />
            <label class="log-auto">
              <input type="checkbox" bind:checked={autoRefresh} />
              live
            </label>
            <button onclick={() => send({ type: 'get_logs' })}>&#8635;</button>
            <button onclick={() => logOpen = false}>&#10005;</button>
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
      <button class="tb-btn" onclick={() => { notesOpen = !notesOpen; helpOpen = false; logOpen = false }} title="Notizblock">&#128221;</button>
      {#if notesOpen}
        <div class="notes-panel">
          <div class="notes-hdr">
            <span>NOTIZBLOCK</span>
            <span class="notes-status">{notesSaved ? 'gespeichert' : 'speichert&#8230;'}</span>
            <button onclick={() => notesOpen = false}>&#10005;</button>
          </div>
          <textarea class="notes-body" placeholder="Bugs, Ideen, Probleme w&#228;hrend des Sets notieren&#8230;"
                    value={$notes} oninput={onNotesInput}></textarea>
        </div>
      {/if}
    </div>
    <div class="help-wrap">
      <button class="tb-btn tb-help" onclick={() => { helpOpen = !helpOpen; notesOpen = false }} title="Tastenkürzel">?</button>
      {#if helpOpen}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="help-backdrop" onclick={() => helpOpen = false}></div>
        <div class="help-panel">
          <div class="help-hdr">
            <span>TASTENKÜRZEL</span>
            <button onclick={() => helpOpen = false}>&#10005;</button>
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
    <button class="ctrl theme-btn" onclick={toggleTheme} title={isDark ? 'Helles Theme' : 'Dunkles Theme'}>{isDark ? '☀' : '☾'}</button>
    <button class="tb-btn" onclick={() => settingsOpen.set(true)} title="Einstellungen">&#9881;</button>
  </div>
  <div class="controls" role="toolbar">
    <button onclick={() => win.minimize?.()} aria-label="Minimieren">&#9472;</button>
    <button onclick={() => win.maximize?.()} aria-label="Maximieren">&#9633;</button>
    <button class="close" onclick={() => win.close?.()} aria-label="Schließen">&#10005;</button>
  </div>
</div>

{#if updateVersion && !updateDismissed}
  <div class="upd-overlay" onclick={() => updateDismissed = true} role="dialog">
    <div class="upd-dialog" onclick={(e) => e.stopPropagation()}>
      <div class="upd-icon">{#if updateReady}&#8679;{:else if updateDownloading}&#8595;{:else}&#8679;{/if}</div>
      <div class="upd-body">
        {#if updateReady}
          <div class="upd-title">Update bereit</div>
          <div class="upd-sub">SynthiMIX <strong>v{updateVersion}</strong> wurde heruntergeladen und kann jetzt installiert werden.</div>
        {:else if updateDownloading}
          <div class="upd-title">Wird heruntergeladen&#8230;</div>
          <div class="upd-sub">SynthiMIX <strong>v{updateVersion}</strong>{updateSize ? ` · ${formatSize(updateSize)}` : ''}</div>
          <div class="upd-bar"><div class="upd-bar-fill" style="width:{updateProgress ?? 0}%"></div></div>
          <div class="upd-pct">{updateProgress ?? 0}%</div>
        {:else}
          <div class="upd-title">Update verfügbar</div>
          <div class="upd-sub">SynthiMIX <strong>v{updateVersion}</strong> ist bereit zum Herunterladen{updateSize ? ` (${formatSize(updateSize)})` : ''}.</div>
        {/if}
      </div>
      <div class="upd-actions">
        {#if updateReady}
          <button class="upd-later" onclick={() => updateDismissed = true}>Später</button>
          <button class="upd-install" onclick={() => win.installUpdate?.()}>Jetzt installieren &amp; neu starten</button>
        {:else if updateDownloading}
          <button class="upd-later" onclick={() => updateDismissed = true}>Im Hintergrund</button>
        {:else}
          <button class="upd-later" onclick={() => updateDismissed = true}>Später</button>
          <button class="upd-install" onclick={() => { updateDownloading = true; win.downloadUpdate?.() }}>Herunterladen</button>
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .titlebar {
    display: flex;
    align-items: center;
    height: 32px;
    padding: 0 12px 0 16px;
    background: var(--c-bg2);
    -webkit-app-region: drag;
    flex-shrink: 0;
    border-bottom: 1px solid var(--c-br1);
  }

  .brand { display: flex; align-items: center; gap: 7px; flex-shrink: 0; }
  .logo-icon { width: 22px; height: 22px; flex-shrink: 0; }
  .app-name { font-size: 12px; font-weight: 600; letter-spacing: 0.08em; }
  .nm-synthi { color: var(--c-accent); }
  .nm-mix    { color: var(--c-blue); }
  .version   { font-size: 9px; color: var(--c-tx7); margin-left: 2px; align-self: flex-end; margin-bottom: 3px; }

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
  .tb-btn { background:none; border:none; color:var(--c-tx6); font-size:14px; width:28px; height:28px; cursor:pointer; border-radius:3px; display:flex; align-items:center; justify-content:center; transition:color .1s, background .1s; }
  .tb-btn:hover { color:var(--c-tx2); background:var(--c-br2); }
  .tb-help { font-size:11px; font-weight:700; border:1px solid var(--c-br2); border-radius:50%; width:18px; height:18px; }
  .theme-btn { font-size: 13px; }
  .ctrl { background:none; border:none; color:var(--c-tx6); font-size:14px; width:28px; height:28px; cursor:pointer; border-radius:3px; display:flex; align-items:center; justify-content:center; transition:color .1s, background .1s; }
  .ctrl:hover { color:var(--c-tx2); background:var(--c-br2); }

  .tb-log { font-size: 8px; }

  .log-wrap { position: relative; }
  .log-panel {
    position: absolute; top: calc(100% + 6px); right: 0;
    background: var(--c-bg2); border: 1px solid var(--c-br3);
    border-radius: 5px; z-index: 1000; width: 560px;
    box-shadow: 0 8px 28px rgba(0,0,0,.9);
    display: flex; flex-direction: column; max-height: 480px;
  }
  .log-hdr {
    display: flex; align-items: center; gap: 6px;
    padding: 6px 10px; border-bottom: 1px solid var(--c-br1);
    flex-shrink: 0;
  }
  .log-hdr span { font-size: 9px; font-weight: 700; letter-spacing: .15em; color: var(--c-tx6); flex-shrink: 0; }
  .log-filter {
    flex: 1; background: var(--c-bg3); border: 1px solid var(--c-br2);
    border-radius: 3px; color: var(--c-tx3); font-size: 10px;
    padding: 2px 6px; outline: none; min-width: 0;
  }
  .log-auto { display: flex; align-items: center; gap: 3px; font-size: 10px; color: var(--c-tx6); flex-shrink: 0; cursor: pointer; }
  .log-hdr button {
    background: none; border: 1px solid var(--c-br2); border-radius: 3px;
    color: var(--c-tx6); font-size: 11px; cursor: pointer;
    padding: 1px 6px; transition: color .1s;
  }
  .log-hdr button:hover { color: var(--c-tx3); }
  .log-body {
    overflow-y: auto; flex: 1; padding: 4px 0;
    font-family: 'Consolas', 'Courier New', monospace; font-size: 10px;
  }
  .log-line {
    padding: 1px 10px; white-space: pre-wrap; word-break: break-all;
    line-height: 1.5;
  }
  .log-info    { color: var(--c-tx6); }
  .log-analyze { color: #5a8a5a; }
  .log-ffmpeg  { color: var(--c-tx5); }
  .log-err     { color: var(--c-red); background: var(--c-red-bg); }
  .log-empty   { padding: 20px; text-align: center; color: var(--c-tx7); font-size: 11px; }

  .notes-wrap { position: relative; }
  .notes-panel {
    position: absolute; top: calc(100% + 6px); right: 0;
    background: var(--c-bg2); border: 1px solid var(--c-br3);
    border-radius: 5px; z-index: 1000; width: 360px;
    box-shadow: 0 8px 28px rgba(0,0,0,.9);
    display: flex; flex-direction: column; overflow: hidden;
  }
  .notes-hdr {
    display: flex; align-items: center; gap: 6px;
    padding: 6px 10px; border-bottom: 1px solid var(--c-br1);
    flex-shrink: 0;
  }
  .notes-hdr span:first-child { font-size: 9px; font-weight: 700; letter-spacing: .15em; color: var(--c-tx6); flex-shrink: 0; }
  .notes-status { flex: 1; font-size: 9px; color: var(--c-tx7); text-align: right; }
  .notes-hdr button {
    background: none; border: 1px solid var(--c-br2); border-radius: 3px;
    color: var(--c-tx6); font-size: 11px; cursor: pointer;
    padding: 1px 6px; transition: color .1s;
  }
  .notes-hdr button:hover { color: var(--c-tx3); }
  .notes-body {
    width: 100%; height: 260px; resize: vertical;
    background: var(--c-bg); border: none; outline: none;
    color: var(--c-tx2); font-size: 12px; line-height: 1.5;
    padding: 10px; box-sizing: border-box;
    font-family: 'Segoe UI', system-ui, sans-serif;
  }
  .notes-body::placeholder { color: var(--c-tx7); }

  .help-wrap { position: relative; }
  .help-backdrop { position: fixed; inset: 0; z-index: 900; }
  .help-panel {
    position: absolute; top: calc(100% + 6px); right: 0;
    background: var(--c-bg3); border: 1px solid var(--c-br3);
    border-radius: 5px; z-index: 1000; min-width: 260px;
    box-shadow: 0 8px 28px rgba(0,0,0,.8);
    overflow: hidden;
  }
  .help-hdr {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 12px 6px;
    border-bottom: 1px solid var(--c-br1);
  }
  .help-hdr span { font-size: 9px; font-weight: 700; letter-spacing: .15em; color: var(--c-tx6); }
  .help-hdr button {
    background: none; border: none; color: var(--c-tx6); font-size: 11px;
    cursor: pointer; padding: 2px 4px; border-radius: 2px; transition: color .1s;
  }
  .help-hdr button:hover { color: #e06060; }
  .help-row {
    display: flex; align-items: center; gap: 12px;
    padding: 5px 12px; border-bottom: 1px solid var(--c-br1);
  }
  .help-row:last-child { border-bottom: none; }
  kbd {
    font-family: inherit; font-size: 10px; font-weight: 600;
    color: var(--c-tx3); background: var(--c-bg5); border: 1px solid var(--c-br3);
    border-radius: 3px; padding: 2px 6px; white-space: nowrap;
    min-width: 90px; text-align: center; flex-shrink: 0;
  }
  .help-row span { font-size: 11px; color: var(--c-tx5); }

  .controls { display: flex; -webkit-app-region: no-drag; }
  .controls button {
    background: none; border: none; color: var(--c-tx7);
    width: 36px; height: 32px; font-size: 12px; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background 0.1s, color 0.1s;
  }
  .controls button:hover { background: var(--c-br2); color: var(--c-tx3); }
  .controls button.close:hover { background: #6a1a1a; color: #ff6060; }

  /* ── Update-Popup ────────────────────────────────────────────────────────── */
  .upd-overlay {
    position: fixed; inset: 0; z-index: 9000;
    background: rgba(0,0,0,.55); backdrop-filter: blur(2px);
    display: flex; align-items: center; justify-content: center;
  }
  .upd-dialog {
    background: var(--c-bg3); border: 1px solid var(--c-br3); border-radius: 8px;
    padding: 24px 28px; max-width: 420px; width: 90%;
    box-shadow: 0 20px 60px rgba(0,0,0,.9);
    display: flex; flex-direction: column; gap: 16px;
  }
  .upd-icon { font-size: 32px; color: #3a8a40; text-align: center; line-height: 1; }
  .upd-body { text-align: center; }
  .upd-title {
    font-size: 14px; font-weight: 700; letter-spacing: .08em;
    color: var(--c-tx1); margin-bottom: 8px;
  }
  .upd-sub { font-size: 11px; color: var(--c-tx5); line-height: 1.6; }
  .upd-sub strong { color: var(--c-tx3); }
  .upd-actions { display: flex; gap: 10px; justify-content: center; }
  .upd-later {
    padding: 7px 18px; background: var(--c-bg); border: 1px solid var(--c-br2);
    border-radius: 4px; color: var(--c-tx5); font-size: 11px; cursor: pointer;
    transition: border-color .1s, color .1s;
  }
  .upd-later:hover { border-color: var(--c-tx5); color: var(--c-tx3); }
  .upd-install {
    padding: 7px 20px; background: #0e2a12; border: 1px solid #2a7a2a;
    border-radius: 4px; color: #4ac050; font-size: 11px; font-weight: 600;
    cursor: pointer; transition: background .1s, border-color .1s, color .1s;
  }
  .upd-install:hover { background: #122e16; border-color: #3aba3a; color: #6ae070; }

  .upd-bar {
    height: 4px; background: var(--c-bg5); border-radius: 2px; overflow: hidden;
    margin-top: 10px;
  }
  .upd-bar-fill {
    height: 100%; background: #2a6aaa; border-radius: 2px;
    transition: width .3s ease;
  }
  .upd-pct {
    font-size: 10px; color: var(--c-tx5); text-align: center; margin-top: 4px;
  }
</style>
