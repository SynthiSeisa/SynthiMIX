<script>
  import { get }    from 'svelte/store'
  import { onMount } from 'svelte'
  import { connected, playerState, queue, send, settingsOpen } from './stores/ws.js'
  import TitleBar  from './components/TitleBar.svelte'
  import Player    from './components/Player.svelte'
  import Queue     from './components/Queue.svelte'
  import Library   from './components/Library.svelte'
  import Downloads from './components/Downloads.svelte'
  import Settings  from './components/Settings.svelte'
  import PlaylistChoiceDialog from './components/PlaylistChoiceDialog.svelte'

  // Apply saved theme on load
  const _savedTheme = localStorage.getItem('synthimix-theme') || 'dark'
  document.documentElement.setAttribute('data-theme', _savedTheme)

  onMount(() => {
    window.electron?.onMediaKey?.((key) => {
      const state = get(playerState)
      const q     = get(queue)
      switch (key) {
        case 'play_pause':
          send({ type: state.playing ? 'pause' : 'resume' })
          break
        case 'next':
          if (state.current_idx + 1 < q.length)
            send({ type: 'play_at', index: state.current_idx + 1 })
          break
        case 'prev':
          if (state.position_ms > 3000) send({ type: 'seek', position_ms: 0 })
          else if (state.current_idx > 0)
            send({ type: 'play_at', index: state.current_idx - 1 })
          break
        case 'stop':
          send({ type: 'pause' })
          break
      }
    })
  })

  function handleKey(e) {
    const tag = e.target?.tagName ?? ''
    if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target?.isContentEditable) return

    const state = get(playerState)
    const q     = get(queue)

    switch (e.code) {
      case 'Space':
        e.preventDefault()
        send({ type: state.playing ? 'pause' : 'resume' })
        break
      case 'ArrowLeft':
        e.preventDefault()
        if (e.ctrlKey || e.metaKey) {
          if (state.position_ms > 3000) send({ type: 'seek', position_ms: 0 })
          else if (state.current_idx > 0)
            send({ type: 'play_at', index: state.current_idx - 1 })
        } else {
          send({ type: 'seek_relative', delta_ms: -5000 })
        }
        break
      case 'ArrowRight':
        e.preventDefault()
        if (e.ctrlKey || e.metaKey) {
          if (state.current_idx + 1 < q.length)
            send({ type: 'play_at', index: state.current_idx + 1 })
        } else {
          send({ type: 'seek_relative', delta_ms: 5000 })
        }
        break
      case 'KeyN':
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault()
          if (state.current_idx + 1 < q.length)
            send({ type: 'play_at', index: state.current_idx + 1 })
        }
        break
      case 'KeyP':
        if (!e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault()
          if (state.current_idx > 0)
            send({ type: 'play_at', index: state.current_idx - 1 })
        }
        break
      case 'F11':
        e.preventDefault()
        window.electron?.toggleFullscreen?.()
        break
    }
  }

  // ── Horizontal splitter (Library | Right panel) ──────────────────────────
  let leftPct = $state(58)
  function startHResize(e) {
    const root = e.currentTarget.parentElement
    const rect = root.getBoundingClientRect()
    function onMove(me) {
      leftPct = Math.max(25, Math.min(75, (me.clientX - rect.left) / rect.width * 100))
    }
    function onUp() {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  // ── Vertical splitter in right panel (Queue | Downloads) ─────────────────
  let dlCollapsed = $state(false)
  let queuePct    = $state(62)   // % of right panel height for queue
  function startVResize(e) {
    const root = e.currentTarget.parentElement
    const rect = root.getBoundingClientRect()
    function onMove(me) {
      queuePct = Math.max(30, Math.min(85, (me.clientY - rect.top) / rect.height * 100))
    }
    function onUp() {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }
</script>

<svelte:window onkeydown={handleKey} />

<div class="app">
  <TitleBar />

  <div class="player-bar">
    <Player />
  </div>

  <!-- Main content: horizontal split -->
  <div class="main">

    <!-- LEFT: Library -->
    <div class="pane-left" style="width: {leftPct}%">
      <Library />
    </div>

    <!-- Horizontal drag handle -->
    <div class="h-handle" onmousedown={startHResize} role="separator" aria-orientation="vertical"></div>

    <!-- RIGHT: Queue + Downloads -->
    <div class="pane-right">

      <!-- Queue -->
      <div class="queue-pane" style="height: {dlCollapsed ? '100%' : queuePct + '%'}">
        <Queue />
      </div>

      {#if !dlCollapsed}
        <!-- Vertical drag handle -->
        <div class="v-handle" onmousedown={startVResize} role="separator" aria-orientation="horizontal">
          <span class="dl-label">⬇ DOWNLOADS</span>
          <button class="collapse-btn" onclick={() => dlCollapsed = true} title="Zuklappen">▼</button>
        </div>

        <!-- Downloads -->
        <div class="dl-pane" style="height: {100 - queuePct}%">
          <Downloads />
        </div>
      {:else}
        <!-- Collapsed: just show header to re-expand -->
        <div class="dl-collapsed" onclick={() => dlCollapsed = false}
             role="button" tabindex="0"
             onkeydown={(e) => e.key === 'Enter' && (dlCollapsed = false)}>
          <span class="dl-label">⬇ DOWNLOADS</span>
          <span class="collapse-btn">▲</span>
        </div>
      {/if}

    </div>
  </div>

  <!-- Status dot -->
  <span class="conn-dot {$connected ? 'on' : 'off'}"
        title={$connected ? 'Verbunden' : 'Verbindet…'}></span>

  {#if $settingsOpen}<Settings />{/if}

  <PlaylistChoiceDialog />
</div>

<style>
  /* ── CSS Custom Properties ────────────────────────────────────────────── */
  :global(:root) {
    --c-bg:     #0a0e18;
    --c-bg2:    #080c16;
    --c-bg3:    #090e1c;
    --c-bg4:    #080d20;
    --c-bg5:    #0e1624;
    --c-br1:    #121e30;
    --c-br2:    #1e2e44;
    --c-br3:    #243650;
    --c-tx1:    #ecf2ff;
    --c-tx2:    #d0dcf0;
    --c-tx3:    #a0bcd4;
    --c-tx4:    #80a0bc;
    --c-tx5:    #6088a8;
    --c-tx6:    #5078a0;
    --c-tx7:    #406090;
    --c-act-bg: #0e0a00;
    --c-act-tx: #f0a040;
    --c-sel:    #0e1c38;
    --c-hover:  #101828;
    --c-accent: #e07800;
    --c-accent2:#ff9020;
    --c-blue:   #3b82f6;
    --c-green:  #2a8a4a;
    --c-red:    #c04040;
    --c-red-bg: #1a0808;
    /* Waveform wird auf Canvas gezeichnet und liest diese Werte zur Laufzeit */
    --c-wf-head:       rgba(255,255,255,0.90);
    --c-wf-zone:       rgba(86,96,112,0.62);
    --c-wf-zone-edge:  rgba(150,166,190,0.55);
    --c-wf-zone-label: rgba(196,208,226,0.82);
    /* Farbige Flaechen: Grund / Rahmen / Schrift je Bedeutung. Im dunklen
       Theme dunkle Kacheln mit heller Schrift, im hellen genau andersherum. */
    --c-green-br: #2a6a30;  --c-green-tx: #4a9a50;  --c-green-bg: #0e1a10;
    --c-red-br:   #8a3030;  --c-red-tx:   #e06060;
    --c-blue-br:  #2a5888;  --c-blue-tx:  #6aa0e0;  --c-blue-bg:  #0e1a2c;
    --c-warn-br:  #604020;  --c-warn-tx:  #e08040;  --c-warn-bg:  #1a1008;
  }
  :global(:root[data-theme="light"]) {
    --c-bg:     #f4f0eb;
    --c-bg2:    #ebe7e1;
    --c-bg3:    #e5e1db;
    --c-bg4:    #edeae5;
    --c-bg5:    #ffffff;
    --c-br1:    #d0ccc6;
    --c-br2:    #b8b4ae;
    --c-br3:    #a0a09a;
    --c-tx1:    #0a0806;
    --c-tx2:    #1e1a14;
    --c-tx3:    #3a342a;
    --c-tx4:    #4e4840;
    --c-tx5:    #5e5650;
    --c-tx6:    #6a6058;
    --c-tx7:    #7a7068;
    --c-act-bg: #fff4e0;
    --c-act-tx: #8a4800;
    --c-sel:    #ddeeff;
    --c-hover:  #e0dcd6;
    --c-accent: #c86000;
    --c-accent2:#e07800;
    --c-blue:   #1a5cb0;
    --c-green:  #1a7030;
    --c-red:    #b82020;
    --c-red-bg: #fff0f0;
    --c-wf-head:       rgba(20,16,12,0.88);
    --c-wf-zone:       rgba(150,142,132,0.55);
    --c-wf-zone-edge:  rgba(90,84,76,0.65);
    --c-wf-zone-label: rgba(30,26,20,0.85);
    --c-green-br: #8ac098;  --c-green-tx: #1a7030;  --c-green-bg: #eaf6ec;
    --c-red-br:   #d89a9a;  --c-red-tx:   #b82020;
    --c-blue-br:  #93b6e0;  --c-blue-tx:  #1a5cb0;  --c-blue-bg:  #e8f0fb;
    --c-warn-br:  #e0b483;  --c-warn-tx:  #9a5000;  --c-warn-bg:  #fdf1e3;
  }
  :global(*, *::before, *::after) { box-sizing: border-box; margin: 0; padding: 0; }
  :global(body) {
    background: var(--c-bg);
    color: var(--c-tx2);
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
    overflow: hidden;
    user-select: none;
  }
  :global(::-webkit-scrollbar) { width: 4px; }
  :global(::-webkit-scrollbar-track) { background: transparent; }
  :global(::-webkit-scrollbar-thumb) { background: var(--c-tx7); border-radius: 2px; }

  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
    background: var(--c-bg);
    position: relative;
  }

  .player-bar {
    flex-shrink: 0;
    border-bottom: 1px solid var(--c-br2);
  }

  /* ── Main content area ─────────────────────────────────────────────────── */
  .main {
    flex: 1;
    display: flex;
    overflow: hidden;
    min-height: 0;
  }

  /* ── Left / Right panes ────────────────────────────────────────────────── */
  .pane-left {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-width: 0;
    border-right: 1px solid var(--c-br1);
  }

  .pane-right {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-width: 0;
  }

  /* ── Horizontal splitter handle ────────────────────────────────────────── */
  .h-handle {
    width: 4px;
    flex-shrink: 0;
    background: var(--c-bg);
    cursor: col-resize;
    transition: background 0.15s;
    z-index: 10;
  }
  .h-handle:hover { background: #e0780044; }

  /* ── Queue pane ────────────────────────────────────────────────────────── */
  .queue-pane {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-height: 0;
  }

  /* ── Vertical resize handle (also the download header) ────────────────── */
  .v-handle {
    height: 28px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    padding: 0 10px;
    background: var(--c-bg3);
    border-top: 1px solid var(--c-br2);
    cursor: row-resize;
    transition: background 0.15s;
    gap: 8px;
    user-select: none;
  }
  .v-handle:hover { background: var(--c-hover); }

  .dl-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.2px;
    color: var(--c-tx6);
    flex: 1;
    pointer-events: none;
  }

  .collapse-btn {
    background: none;
    border: none;
    color: var(--c-tx6);
    font-size: 13px;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
    transition: color 0.15s;
  }
  .collapse-btn:hover { color: var(--c-accent); }

  /* ── Downloads pane ─────────────────────────────────────────────────────── */
  .dl-pane {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-height: 0;
    border-top: 1px solid var(--c-br1);
  }

  /* ── Collapsed downloads strip ──────────────────────────────────────────── */
  .dl-collapsed {
    height: 28px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    padding: 0 10px;
    background: var(--c-bg3);
    border-top: 1px solid var(--c-br2);
    cursor: pointer;
    gap: 8px;
    transition: background 0.15s;
  }
  .dl-collapsed:hover { background: var(--c-hover); }

  /* ── Connection dot ─────────────────────────────────────────────────────── */
  .conn-dot {
    position: fixed;
    bottom: 6px;
    right: 8px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    transition: background 0.3s;
    z-index: 100;
  }
  .conn-dot.on  { background: var(--c-green); }
  .conn-dot.off { background: var(--c-red); }
</style>
