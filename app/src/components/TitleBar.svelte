<script>
  import { settingsOpen, openSettings, send, backendLogs, notes, wishes, toolUpdates } from '../stores/ws.js'
  import WishesDialog from './WishesDialog.svelte'
  import { theme } from '../lib/prefs.js'
  const win = window.electron ?? {}

  // ── Musikwuensche ─────────────────────────────────────────────────────────
  let wishesOpen = $state(false)
  // Nur was noch Aufmerksamkeit braucht — Fehler zaehlen mit, damit sie
  // nicht unbemerkt liegenbleiben.
  const offeneWuensche = $derived($wishes.length)

  // ── Update-Hinweise ───────────────────────────────────────────────────────
  // Nur ein Punkt am Zahnrad — beim Auflegen soll kein Fenster aufgehen.
  // Der Klick fuehrt direkt auf den Tab, in dem der Hinweis steht.
  const updTab = $derived(
    $toolUpdates.ytdlp?.available || $toolUpdates.ytdlp_updated ? 'system'
    : $toolUpdates.spotdl?.available ? 'download' : null)
  const updTitle = $derived(
    $toolUpdates.ytdlp?.available ? `Neue yt-dlp-Version ${$toolUpdates.ytdlp.latest}`
    : $toolUpdates.spotdl?.available ? `Neue spotdl-Version ${$toolUpdates.spotdl.latest}`
    : $toolUpdates.ytdlp_updated ? `yt-dlp wurde auf ${$toolUpdates.ytdlp_updated.to} aktualisiert`
    : '')

  // ── Theme ────────────────────────────────────────────────────────────────
  const isDark = $derived($theme === 'dark')
  function toggleTheme() { theme.set(isDark ? 'light' : 'dark') }

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
    ['Space',        'Pause / Fortsetzen'],
    ['← / →',       '±5 Sekunden springen'],
    ['Strg + →',    'Nächster Track'],
    ['Strg + ←',    'Track-Anfang / Vorheriger'],
    ['N',            'Nächster Track'],
    ['P',            'Vorheriger Track'],
    ['Entf',         'Markierte Queue-Einträge entfernen'],
    ['Strg + A',     'Alle auswählen (Queue / Bibliothek)'],
    ['Shift + Klick','Bereich auswählen'],
    ['Escape',       'Auswahl aufheben'],
    ['F11',          'Vollbild umschalten'],
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
    <span class="version">v1.4.0</span>
  </div>
  <div class="tb-actions" style="-webkit-app-region:no-drag">

    {#if updateReady && updateDismissed}
      <button class="btn btn-primary btn-sm" onclick={() => win.installUpdate?.()} title="Update installieren und neu starten">
        &#8593; v{updateVersion} installieren
      </button>
    {:else if updateProgress !== null && updateDismissed}
      <span class="upd-chip">&#8595; {updateProgress}%</span>
    {/if}

    <div class="pop-wrap">
      <button class="btn btn-icon btn-sm" class:is-active={logOpen}
              onclick={() => { logOpen = !logOpen; helpOpen = false; notesOpen = false }}
              title="Backend-Log" aria-label="Backend-Log"><i class="ti ti-terminal-2"></i></button>
      {#if logOpen}
        <div class="pop log-panel">
          <div class="pop-hdr">
            <span class="eyebrow">Backend-Log</span>
            <input class="pop-input" bind:value={logFilter} placeholder="Filter&#8230;" aria-label="Log filtern" />
            <label class="pop-check">
              <input type="checkbox" bind:checked={autoRefresh} />
              live
            </label>
            <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'get_logs' })} title="Neu laden" aria-label="Neu laden"><i class="ti ti-refresh"></i></button>
            <button class="btn btn-icon btn-sm" onclick={() => logOpen = false} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
          </div>
          <div class="log-body" bind:this={logEl}>
            {#each filteredLogs as line}
              <div class="log-line {lineClass(line)}">{line}</div>
            {:else}
              <div class="pop-empty">Keine Logs</div>
            {/each}
          </div>
        </div>
      {/if}
    </div>
    <div class="pop-wrap">
      <button class="btn btn-icon btn-sm" class:is-active={notesOpen}
              onclick={() => { notesOpen = !notesOpen; helpOpen = false; logOpen = false }}
              title="Notizblock" aria-label="Notizblock"><i class="ti ti-notes"></i></button>
      {#if notesOpen}
        <div class="pop notes-panel">
          <div class="pop-hdr">
            <span class="eyebrow">Notizblock</span>
            <span class="pop-status">{notesSaved ? 'gespeichert' : 'speichert&#8230;'}</span>
            <button class="btn btn-icon btn-sm" onclick={() => notesOpen = false} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
          </div>
          <textarea class="notes-body" placeholder="Bugs, Ideen, Probleme w&#228;hrend des Sets notieren&#8230;"
                    value={$notes} oninput={onNotesInput} aria-label="Notizen"></textarea>
        </div>
      {/if}
    </div>
    <div class="pop-wrap">
      <button class="btn btn-icon btn-sm" class:is-active={helpOpen}
              onclick={() => { helpOpen = !helpOpen; notesOpen = false }}
              title="Tastenkürzel" aria-label="Tastenkürzel"><i class="ti ti-help"></i></button>
      {#if helpOpen}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="help-backdrop" onclick={() => helpOpen = false}></div>
        <div class="pop help-panel">
          <div class="pop-hdr">
            <span class="eyebrow">Tastenkürzel</span>
            <span class="pop-status"></span>
            <button class="btn btn-icon btn-sm" onclick={() => helpOpen = false} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
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

    <span class="tb-sep" aria-hidden="true"></span>

    <button class="btn btn-icon btn-sm" onclick={toggleTheme}
            title={isDark ? 'Helles Theme' : 'Dunkles Theme'} aria-label="Theme umschalten">
      <i class="ti {isDark ? 'ti-sun' : 'ti-moon'}"></i>
    </button>
    <button class="btn btn-icon btn-sm has-badge" onclick={() => wishesOpen = !wishesOpen}
            title="Musikw&#252;nsche der G&#228;ste" aria-label="Musikwünsche">
      <i class="ti ti-music"></i>{#if offeneWuensche}<span class="badge">{offeneWuensche}</span>{/if}
    </button>
    <button class="btn btn-icon btn-sm has-badge" onclick={() => updTab ? openSettings(updTab) : settingsOpen.set(true)}
            title={updTitle ? 'Einstellungen · ' + updTitle : 'Einstellungen'} aria-label="Einstellungen">
      <i class="ti ti-settings"></i>{#if updTab}<span class="dot"></span>{/if}
    </button>
  </div>
  <div class="controls" role="toolbar">
    <button onclick={() => win.minimize?.()} aria-label="Minimieren"><i class="ti ti-minus"></i></button>
    <button onclick={() => win.maximize?.()} aria-label="Maximieren"><i class="ti ti-maximize"></i></button>
    <button class="close" onclick={() => win.close?.()} aria-label="Schließen"><i class="ti ti-x"></i></button>
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
          <button class="btn" onclick={() => updateDismissed = true}>Später</button>
          <button class="btn btn-primary" onclick={() => win.installUpdate?.()}>Jetzt installieren &amp; neu starten</button>
        {:else if updateDownloading}
          <button class="btn" onclick={() => updateDismissed = true}>Im Hintergrund</button>
        {:else}
          <button class="btn" onclick={() => updateDismissed = true}>Später</button>
          <button class="btn btn-primary" onclick={() => { updateDownloading = true; win.downloadUpdate?.() }}>Herunterladen</button>
        {/if}
      </div>
    </div>
  </div>
{/if}

{#if wishesOpen}
  <WishesDialog onclose={() => wishesOpen = false} />
{/if}

<style>
  .titlebar {
    display: flex; align-items: center; gap: var(--sp-2);
    height: 36px; padding: 0 0 0 var(--sp-4);
    background: var(--c-bg2);
    border-bottom: 1px solid var(--c-br1);
    -webkit-app-region: drag;
    flex-shrink: 0;
  }

  .brand { display: flex; align-items: center; gap: var(--sp-2); flex-shrink: 0; }
  .logo-icon { width: 22px; height: 22px; flex-shrink: 0; }
  .app-name { font-size: var(--fs-body); font-weight: 700; letter-spacing: .06em; }
  .nm-synthi { color: var(--c-accent-tx); }
  .nm-mix    { color: var(--c-blue-tx); }
  .version   { font-size: var(--fs-cap); color: var(--c-tx5); font-variant-numeric: tabular-nums; }

  .tb-actions { display: flex; align-items: center; gap: 2px; margin-left: auto; -webkit-app-region: no-drag; }
  .tb-sep { width: 1px; height: 18px; background: var(--c-br2); margin: 0 var(--sp-1); }

  /* Zaehler und Hinweis-Punkt an Icon-Knoepfen */
  .has-badge { position: relative; }
  .badge {
    position: absolute; top: 0; right: -2px;
    min-width: 16px; height: 16px; padding: 0 4px; border-radius: 8px;
    background: var(--c-accent); color: var(--c-on-accent);
    font: 700 var(--fs-cap)/16px 'Segoe UI', system-ui, sans-serif; text-align: center;
    box-shadow: 0 0 0 2px var(--c-bg2);
  }
  .dot {
    position: absolute; top: 3px; right: 3px; width: 8px; height: 8px; border-radius: 50%;
    background: var(--c-accent); box-shadow: 0 0 0 2px var(--c-bg2);
  }
  .upd-chip {
    font-size: var(--fs-sm); font-weight: 600; padding: 3px 8px; border-radius: var(--r-s);
    color: var(--c-green-tx); border: 1px solid var(--c-green-br); background: var(--c-green-bg);
    font-variant-numeric: tabular-nums;
  }

  /* ── Aufklapp-Panels (Log, Notizen, Tastenkuerzel) ───────────────────── */
  .pop-wrap { position: relative; }
  .pop {
    position: absolute; top: calc(100% + 6px); right: 0; z-index: 1000;
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: var(--r-l);
    box-shadow: 0 12px 32px rgba(0,0,0,.45);
    display: flex; flex-direction: column; overflow: hidden;
  }
  .pop-hdr {
    display: flex; align-items: center; gap: var(--sp-2);
    padding: var(--sp-2) var(--sp-2) var(--sp-2) var(--sp-3);
    border-bottom: 1px solid var(--c-br1); flex-shrink: 0;
  }
  .pop-hdr .eyebrow { flex-shrink: 0; }
  .pop-status { flex: 1; font-size: var(--fs-sm); color: var(--c-tx5); text-align: right; }
  .pop-input {
    flex: 1; min-width: 0; height: var(--btn-h-sm); padding: 0 var(--sp-2);
    background: var(--c-bg); border: 1px solid var(--c-br2); border-radius: var(--r-s);
    color: var(--c-tx2); font-size: var(--fs-sm);
  }
  .pop-input:focus { border-color: var(--c-accent); }
  .pop-check { display: flex; align-items: center; gap: 4px; font-size: var(--fs-sm); color: var(--c-tx4); flex-shrink: 0; cursor: pointer; }
  .pop-empty { padding: var(--sp-5); text-align: center; color: var(--c-tx5); font-size: var(--fs-sm); }

  .log-panel { width: 600px; max-height: 480px; }
  .log-body {
    overflow-y: auto; flex: 1; padding: var(--sp-1) 0;
    font-family: 'Consolas', 'Cascadia Mono', 'Courier New', monospace; font-size: var(--fs-cap);
    user-select: text;
  }
  .log-line { padding: 1px var(--sp-3); white-space: pre-wrap; word-break: break-all; line-height: 1.5; }
  .log-info    { color: var(--c-tx4); }
  .log-analyze { color: var(--c-green-tx); }
  .log-ffmpeg  { color: var(--c-tx5); }
  .log-err     { color: var(--c-red-tx); background: var(--c-red-bg); }

  .notes-panel { width: 380px; }
  .notes-body {
    width: 100%; height: 260px; resize: vertical;
    background: var(--c-bg); border: none;
    color: var(--c-tx2); font-size: var(--fs-body); line-height: 1.5;
    padding: var(--sp-3);
    font-family: 'Segoe UI', system-ui, sans-serif;
    user-select: text;
  }
  .notes-body::placeholder { color: var(--c-tx6); }

  .help-backdrop { position: fixed; inset: 0; z-index: 900; }
  .help-panel { min-width: 300px; }
  .help-row {
    display: flex; align-items: center; gap: var(--sp-3);
    padding: 6px var(--sp-3); border-bottom: 1px solid var(--c-br1);
  }
  .help-row:last-child { border-bottom: none; }
  kbd {
    font-family: inherit; font-size: var(--fs-sm); font-weight: 600;
    color: var(--c-tx2); background: var(--c-bg); border: 1px solid var(--c-br3);
    border-radius: var(--r-s); padding: 2px 6px; white-space: nowrap;
    min-width: 96px; text-align: center; flex-shrink: 0;
  }
  .help-row span { font-size: var(--fs-sm); color: var(--c-tx3); }

  /* ── Fensterknoepfe: wie unter Windows ueblich, randlos und voll hoch ── */
  .controls { display: flex; align-self: stretch; -webkit-app-region: no-drag; margin-left: var(--sp-2); }
  .controls button {
    background: none; border: none; color: var(--c-tx4);
    width: 44px; font-size: 14px; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background .1s, color .1s;
  }
  .controls button:hover { background: var(--c-hover); color: var(--c-tx1); }
  .controls button.close:hover { background: #c42b1c; color: #fff; }

  /* ── Update-Popup ────────────────────────────────────────────────────── */
  .upd-overlay {
    position: fixed; inset: 0; z-index: 9000;
    background: rgba(0,0,0,.55); backdrop-filter: blur(2px);
    display: flex; align-items: center; justify-content: center;
  }
  .upd-dialog {
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: var(--r-l);
    padding: var(--sp-5); max-width: 440px; width: 90%;
    box-shadow: 0 20px 60px rgba(0,0,0,.6);
    display: flex; flex-direction: column; gap: var(--sp-4);
  }
  .upd-icon { font-size: 32px; color: var(--c-green-tx); text-align: center; line-height: 1; }
  .upd-body { text-align: center; }
  .upd-title { font-size: var(--fs-h); font-weight: 700; color: var(--c-tx1); margin-bottom: var(--sp-2); }
  .upd-sub { font-size: var(--fs-body); color: var(--c-tx3); line-height: 1.55; }
  .upd-sub strong { color: var(--c-tx1); }
  .upd-actions { display: flex; gap: var(--sp-2); justify-content: center; flex-wrap: wrap; }
  .upd-bar { height: 6px; background: var(--c-bg2); border-radius: 3px; overflow: hidden; margin-top: var(--sp-3); }
  .upd-bar-fill { height: 100%; background: var(--c-accent); border-radius: 3px; transition: width .3s ease; }
  .upd-pct { font-size: var(--fs-sm); color: var(--c-tx4); text-align: center; margin-top: var(--sp-1); font-variant-numeric: tabular-nums; }
</style>
