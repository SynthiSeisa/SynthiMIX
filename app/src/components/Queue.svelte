<script>
  import { get } from 'svelte/store'
  import { queue, playerState, library, playlists, playMode, send, automixStatus, introSkipPaths, settings, appSettings, skipNextCrossfade, autoRemovePlayed, livePositionMs, selectionOwner } from '../stores/ws.js'

  // ── Shuffle / Repeat ────────────────────────────────────────────────────────
  const shuffle = $derived($playMode.shuffle)
  const repeat  = $derived($playMode.repeat)
  const repeatLabel = $derived(repeat === 1 ? '↺¹' : repeat === 2 ? '↺∞' : '↺')

  function toggleShuffle() {
    const v = !get(playMode).shuffle
    playMode.update(pm => ({ ...pm, shuffle: v }))
    send({ type: 'set_shuffle', value: v })
  }
  function cycleRepeat() {
    const v = (get(playMode).repeat + 1) % 3
    playMode.update(pm => ({ ...pm, repeat: v }))
    send({ type: 'set_repeat', value: v })
  }

  // ── Drag state ─────────────────────────────────────────────────────────────
  let dragFrom    = $state(null)   // queue-row drag: source index
  let dragOver    = $state(null)   // insertion line shown above this index
  let isDragOver  = $state(false)  // library-drop highlight

  // ── Queue ops ──────────────────────────────────────────────────────────────
  function play(idx)    { skipNextCrossfade.set(true); send({ type: 'play_at', index: idx }) }
  function mixNow(idx) { /* kein skipNextCrossfade → sofortiger Crossfade */ send({ type: 'play_at', index: idx }) }
  function remove(idx) { send({ type: 'queue_remove', index: idx }) }

  function skipIntroFor(path) {
    if (!path) return
    introSkipPaths.update(s => { const n = new Set(s); n.add(path); return n })
  }

  function clearQueue() {
    if ($queue.length) { dlgClearPending = true } else send({ type: 'queue_clear' })
  }

  function shuffleQueue()         { send({ type: 'queue_shuffle' }) }
  function shuffleUnplayed()      { send({ type: 'queue_shuffle_unplayed' }); showMenu = false }
  function shuffleSelected()      { send({ type: 'queue_shuffle_selected', indices: [...qSelected] }); showMenu = false }
  function markAllUnplayed()      { send({ type: 'queue_mark_unplayed' }); showMenu = false }
  function removePlayed()         { send({ type: 'queue_remove_played' }); showMenu = false }
  function removeQueueDuplicates(){ send({ type: 'queue_remove_duplicates' }); showMenu = false }
  function toggleAutoRemove()     { send({ type: 'set_auto_remove_played', enabled: !$autoRemovePlayed }); showMenu = false }

  function savePlaylist() {
    plNameValue = ''
    plSaveOnlySelected = qSelected.size > 0
    plClearAfterSave = false
    plNameDialog = true
  }
  function confirmSavePlaylist() {
    if (plNameValue.trim()) {
      const paths = plSaveOnlySelected && qSelected.size > 0
        ? [...qSelected].sort((a, b) => a - b).map(i => $queue[i]?.path).filter(Boolean)
        : null
      send({ type: 'save_playlist', name: plNameValue.trim(), paths, clear_after: plClearAfterSave })
    }
    plNameDialog = false; showMenu = false
  }

  function loadPlaylist(path) {
    send({ type: 'load_playlist', path })
    showMenu = false
  }

  function deletePlaylist(path) { dlgDeletePath = path }

  // ── Custom dialogs ─────────────────────────────────────────────────────────
  let plNameDialog      = $state(false)
  let plNameValue       = $state('')
  let plSaveOnlySelected = $state(false)
  let plClearAfterSave  = $state(false)
  let dlgClearPending   = $state(false)
  let dlgDeletePath     = $state(null)
  function focus(el) { setTimeout(() => el?.focus(), 50) }

  // ── Menu dropdown ──────────────────────────────────────────────────────────
  let showMenu  = $state(false)
  let showPlaylists = $state(false)

  function toggleMenu() { showMenu = !showMenu; showPlaylists = false }

  // ── Drag: library → queue ──────────────────────────────────────────────────
  function onContainerDragOver(e) {
    // Reordering an existing queue row (dragFrom set) — always allow the drop
    if (dragFrom !== null) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      return
    }
    if (!e.dataTransfer.types.some(t =>
        t === 'text/plain' || t === 'application/x-ytdl-track' || t === 'application/x-ytdl-multi')) return
    e.preventDefault()
    isDragOver = true
    e.dataTransfer.dropEffect = 'copy'
  }

  function onContainerDragLeave(e) {
    if (!e.relatedTarget || !e.currentTarget.contains(e.relatedTarget)) {
      isDragOver = false; dragOver = null
    }
  }

  function onContainerDrop(e) {
    e.preventDefault()
    isDragOver = false; dragOver = null
    if (dragFrom !== null) {
      // Dropped a reordered row onto empty queue area → move to end
      const dest = dragOver ?? $queue.length
      if (dragFrom !== dest && dragFrom + 1 !== dest) {
        send({ type: 'queue_move', from: dragFrom, to: dest })
      }
      dragFrom = null
      return
    }
    _handleLibraryDrop(e, $queue.length)
  }

  // ── Drag: reorder within queue ─────────────────────────────────────────────
  function onRowDragStart(e, i) {
    dragFrom = i
    e.dataTransfer.setData('application/x-queue-move', String(i))
    e.dataTransfer.effectAllowed = 'move'
  }

  let _scrollRaf = null
  function _autoScroll(e) {
    const list = e.currentTarget?.closest?.('.queue-list') ?? document.querySelector('.queue-list')
    if (!list) return
    const rect = list.getBoundingClientRect()
    const zone = 60
    const speed = 8
    if (_scrollRaf) cancelAnimationFrame(_scrollRaf)
    if (e.clientY < rect.top + zone) {
      const tick = () => { list.scrollTop -= speed; _scrollRaf = requestAnimationFrame(tick) }
      _scrollRaf = requestAnimationFrame(tick)
    } else if (e.clientY > rect.bottom - zone) {
      const tick = () => { list.scrollTop += speed; _scrollRaf = requestAnimationFrame(tick) }
      _scrollRaf = requestAnimationFrame(tick)
    }
  }

  function onRowDragOver(e, i) {
    e.preventDefault()
    if (dragFrom !== null) {
      dragOver = i
      e.dataTransfer.dropEffect = 'move'
      _autoScroll(e)
    } else {
      isDragOver = true
      e.dataTransfer.dropEffect = 'copy'
    }
  }

  function onRowDrop(e, i) {
    e.preventDefault()
    isDragOver = false
    if (dragFrom !== null && dragFrom !== i && dragFrom + 1 !== i) {
      send({ type: 'queue_move', from: dragFrom, to: i })
    } else if (dragFrom === null) {
      _handleLibraryDrop(e, i)
    }
    dragFrom = null; dragOver = null
  }

  function onDragEnd() {
    dragFrom = null; dragOver = null; isDragOver = false
    if (_scrollRaf) { cancelAnimationFrame(_scrollRaf); _scrollRaf = null }
  }

  function _handleLibraryDrop(e, insertAt) {
    const atEnd = insertAt >= $queue.length

    // Multi-track drop (Ctrl+A then drag)
    const multiRaw = e.dataTransfer.getData('application/x-ytdl-multi')
    if (multiRaw) {
      try {
        const tracks = JSON.parse(multiRaw)
        tracks.forEach((t, offset) => {
          if (atEnd) {
            send({ type: 'queue_add', path: t.path, title: t.title,
                   duration_sec: t.duration_sec, lufs: t.lufs,
                   bpm: t.bpm, bitrate_kbps: t.bitrate_kbps })
          } else {
            send({ type: 'queue_insert_at', index: insertAt + offset, path: t.path,
                   title: t.title, duration_sec: t.duration_sec, lufs: t.lufs,
                   bpm: t.bpm, bitrate_kbps: t.bitrate_kbps })
          }
        })
        return
      } catch {}
    }

    const type  = atEnd ? 'queue_add' : 'queue_insert_at'
    const idx   = atEnd ? undefined : insertAt

    const rich = e.dataTransfer.getData('application/x-ytdl-track')
    if (rich) {
      try {
        const t = JSON.parse(rich)
        send({ type, index: idx, path: t.path, title: t.title,
               duration_sec: t.duration_sec, lufs: t.lufs,
               bpm: t.bpm, bitrate_kbps: t.bitrate_kbps })
        return
      } catch {}
    }
    const path = e.dataTransfer.getData('text/plain')
    if (!path) return
    const lib = get(library)
    const t = lib.find(t => t.path === path)
    if (t) {
      send({ type, index: idx, path: t.path, title: t.title,
             duration_sec: t.duration_sec, lufs: t.lufs,
             bpm: t.bpm, bitrate_kbps: t.bitrate_kbps })
    } else {
      const name = path.replace(/\\/g, '/').split('/').pop().replace(/\.[^.]+$/, '')
      send({ type, index: idx, path, title: name })
    }
  }

  // ── Row selection (Ctrl+A / Delete) ───────────────────────────────────────
  let qSelected = $state(new Set())
  let _qHover   = $state(false)   // mouse over queue panel — scopes Ctrl+A

  // Selection is mutually exclusive between Library and Queue panels
  $effect(() => {
    if ($selectionOwner === 'library' && qSelected.size > 0) qSelected = new Set()
  })

  function qRowClick(e, i) {
    selectionOwner.set('queue')
    if (e.ctrlKey || e.metaKey) {
      const s = new Set(qSelected)
      if (s.has(i)) s.delete(i); else s.add(i)
      qSelected = s
    } else if (e.shiftKey && qSelected.size > 0) {
      const last = [...qSelected].at(-1)
      const lo = Math.min(last, i), hi = Math.max(last, i)
      const s = new Set(qSelected)
      for (let k = lo; k <= hi; k++) s.add(k)
      qSelected = s
    } else if (!qSelected.has(i)) {
      // Plain click on unselected: select only this row (preserves multi-select for drag)
      qSelected = new Set([i])
    }
  }

  function removeSelected() {
    if (qSelected.size === 0) return
    // Remove from highest index to lowest to preserve indices
    const idxs = [...qSelected].sort((a, b) => b - a)
    idxs.forEach(i => send({ type: 'queue_remove', index: i }))
    qSelected = new Set()
  }

  let qListEl = $state(null)

  function scrollToCurrent() {
    qListEl?.querySelector('.row.active')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }

  $effect(() => {
    const _ = $playerState.current_idx
    setTimeout(scrollToCurrent, 80)
  })

  function qKeydown(e) {
    if (e.target?.tagName === 'INPUT') return
    if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
      if (!_qHover) return
      e.preventDefault()
      selectionOwner.set('queue')
      qSelected = new Set($queue.map((_, i) => i))
    }
    if (e.key === 'Delete' || e.key === 'Backspace') {
      if (qSelected.size > 0) { e.preventDefault(); removeSelected() }
    }
    if (e.key === 'Escape') { qSelected = new Set(); closeRowCtx() }
  }

  // ── Row context menu ───────────────────────────────────────────────────────
  let qCtxMenu = $state(null)   // { x, y, idx }
  function openRowCtx(e, i) {
    e.preventDefault()
    e.stopPropagation()
    qCtxMenu = { x: e.clientX, y: e.clientY, idx: i }
  }
  function closeRowCtx() { qCtxMenu = null }

  // ── Format ─────────────────────────────────────────────────────────────────
  function fmt(sec) {
    if (!sec) return ''
    return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
  }

  const totalDurStr = $derived.by(() => {
    const total = $queue.reduce((s, t) => s + (t.duration_sec ?? 0), 0)
    if (total <= 0) return ''
    const h = Math.floor(total / 3600)
    const m = Math.floor((total % 3600) / 60)
    return h > 0 ? `${h}h ${m}m` : `${m} min`
  })

  // Sekunden bis Track i startet — live-Position + Intro/Outro-Schätzung
  const etaSecs = $derived.by(() => {
    const result = new Array($queue.length).fill(null)
    const idx = $playerState.current_idx
    if (idx < 0) return result
    const cfS    = $settings.crossfade_s ?? 0
    // Live-Position aus Player (wird jede Sekunde aktualisiert, nicht nur bei Backend-Events)
    const posS   = $livePositionMs / 1000
    const curDur = $playerState.duration_ms > 0
      ? $playerState.duration_ms / 1000
      : ($queue[idx]?.duration_sec ?? 0)
    let acc = Math.max(0, curDur - posS)
    acc -= Math.min(cfS, acc)

    // Intro-Skip-Schätzung für Folgetracks (SmartFade + Aggressivität)
    const smartFade = $appSettings.smartFade
    const ia = Math.max(0, Math.min(4, ($appSettings.introAggressiveness ?? 3) - 1))
    const INTRO_SKIP_FRAC = [0.0, 0.05, 0.10, 0.15, 0.20]  // grobe Schätzung ohne Waveform
    const introSkipFrac = (smartFade && cfS > 0) ? INTRO_SKIP_FRAC[ia] : 0

    for (let j = idx + 1; j < $queue.length; j++) {
      result[j] = acc
      const dur = $queue[j]?.duration_sec ?? 0
      const effective = Math.max(0, dur * (1 - introSkipFrac) - Math.min(cfS, dur))
      acc += effective
    }
    return result
  })

  const remainingStr = $derived.by(() => {
    const idx = $playerState.current_idx
    if (idx < 0 || $queue.length === 0) return ''
    const last  = $queue.length - 1
    const secs  = etaSecs[last]
    if (secs === null) return ''
    const grand = secs + ($queue[last]?.duration_sec ?? 0)
    const h = Math.floor(grand / 3600)
    const m = Math.floor((grand % 3600) / 60)
    const s = Math.floor(grand % 60)
    return h > 0 ? `−${h}h ${m}m` : m > 0 ? `−${m}m ${String(s).padStart(2,'0')}s` : `−${s}s`
  })

  function trackEta(i) {
    const secs = etaSecs[i]
    if (secs === null) return null
    const then = new Date(Date.now() + secs * 1000)
    return `~${String(then.getHours()).padStart(2,'0')}:${String(then.getMinutes()).padStart(2,'0')}`
  }

  function qualityClass(track) {
    const br   = track.bitrate_kbps ?? 0
    const lufs = track.lufs ?? -99
    const hasLufs = lufs > -90
    if (br > 0 && br < 128) return 'q-bad'       // low bitrate → red
    if (hasLufs && lufs > -9)  return 'q-loud'   // too loud (clipping risk) → orange
    if (hasLufs && lufs < -18) return 'q-quiet'  // too quiet → orange
    if (hasLufs) return 'q-good'                  // normal range → green
    return ''
  }

  function qualityTitle(track) {
    const br   = track.bitrate_kbps ?? 0
    const lufs = track.lufs ?? -99
    if (br > 0 && br < 128) return `Niedrige Bitrate (${br} kbps)`
    if (lufs > -90) {
      if (lufs > -9)  return `Zu laut (${lufs.toFixed(1)} LUFS)`
      if (lufs < -18) return `Zu leise (${lufs.toFixed(1)} LUFS)`
      return `Gut (${lufs.toFixed(1)} LUFS)`
    }
    return ''
  }
</script>

<!-- Click-outside to close menus -->
<svelte:window
  onclick={(e) => {
    if (!e.target.closest('.queue-header')) { showMenu = false; showPlaylists = false }
    if (!e.target.closest('.q-ctx')) closeRowCtx()
    if (!e.target.closest('.queue-list')) qSelected = new Set()
  }}
  onkeydown={qKeydown}
/>

<div class="queue-outer"
     ondragover={onContainerDragOver}
     ondragleave={onContainerDragLeave}
     ondrop={onContainerDrop}
     onmouseenter={() => _qHover = true}
     onmouseleave={() => _qHover = false}
     class:drag-over={isDragOver}>

  <!-- Header -->
  <div class="queue-header">
    <span class="queue-title">WARTESCHLANGE</span>
    <span class="queue-count">{$queue.length}</span>
    {#if remainingStr}
      <span class="queue-dur" title="Restzeit ab jetzt">{remainingStr}</span>
    {:else if totalDurStr}
      <span class="queue-dur">{totalDurStr}</span>
    {/if}
    {#if $automixStatus}
      <span class="am-status">{$automixStatus}</span>
    {/if}

    <div class="header-actions">
      {#if qSelected.size > 1}
        <button class="hdr-btn sel-shuffle-btn" onclick={shuffleSelected} title="Nur markierte {qSelected.size} Tracks mischen">⇄ {qSelected.size}</button>
      {/if}
      {#if $playerState.current_idx >= 0}
        <button class="hdr-btn" onclick={scrollToCurrent} title="Zum aktuellen Track scrollen">⦿</button>
      {/if}
      <button class="hdr-btn {shuffle ? 'active' : ''}" onclick={toggleShuffle} title="Zufallswiedergabe">⇄</button>
      <button class="hdr-btn {repeat > 0 ? 'active' : ''}" onclick={cycleRepeat} title="Wiederholen">{repeatLabel}</button>
      <div class="menu-wrap">
        <button class="hdr-btn menu-btn" onclick={toggleMenu} title="Menü">•••</button>
        {#if showMenu}
          <div class="dropdown">
            <button onclick={savePlaylist}>Als Playlist speichern</button>
            <button onclick={() => { showPlaylists = !showPlaylists }}>
              In Playlist öffnen{showPlaylists ? ' ▲' : ' ▶'}
            </button>
            {#if showPlaylists}
              {#if $playlists.length === 0}
                <span class="dd-empty">Keine gespeicherten Playlists</span>
              {:else}
                {#each $playlists as pl}
                  <div class="dd-pl-row">
                    <button class="dd-pl-name" onclick={() => loadPlaylist(pl.path)}>{pl.name}</button>
                    <button class="dd-pl-del" onclick={() => deletePlaylist(pl.path)} title="Löschen">✕</button>
                  </div>
                {/each}
              {/if}
            {/if}
            <button onclick={() => { shuffleQueue(); showMenu = false }}>Einmalig mischen</button>
            <button onclick={shuffleUnplayed}>Nur ungespielte mischen</button>
            {#if qSelected.size > 1}
              <button onclick={shuffleSelected}>Nur markierte mischen ({qSelected.size})</button>
            {/if}
            <div class="dd-sep"></div>
            <button onclick={markAllUnplayed}>Alle als ungespielt markieren</button>
            <button onclick={removePlayed}>Gespielte entfernen</button>
            <button onclick={removeQueueDuplicates}>Duplikate entfernen</button>
            <button onclick={toggleAutoRemove} class:dd-active={$autoRemovePlayed}>
              {$autoRemovePlayed ? '✓ ' : ''}Gespielte auto. entfernen
            </button>
            <div class="dd-sep"></div>
            <button onclick={clearQueue} class="dd-danger">Queue leeren</button>
          </div>
        {/if}
      </div>
    </div>
  </div>

  <!-- Drop hint overlay -->
  {#if isDragOver}
    <div class="drop-hint">Hier ablegen</div>
  {/if}

  <!-- Track list -->
  <div class="queue-list" bind:this={qListEl}>
    {#if $queue.length === 0}
      <div class="empty">Queue leer · Tracks aus der Bibliothek hierher ziehen</div>
    {:else}
      {#each $queue as track, i}
        {@const active = i === $playerState.current_idx}
        {@const played = track.played && !active}
        <div
          class="row {active ? 'active' : played ? 'played' : ''} {dragOver === i ? 'drop-before' : ''} {qSelected.has(i) ? 'q-sel' : ''}"
          draggable="true"
          ondragstart={(e) => onRowDragStart(e, i)}
          ondragover={(e) => onRowDragOver(e, i)}
          ondrop={(e) => onRowDrop(e, i)}
          ondragend={onDragEnd}
          onclick={(e) => qRowClick(e, i)}
          ondblclick={() => mixNow(i)}
          oncontextmenu={(e) => openRowCtx(e, i)}>

          <span class="drag-handle" title="Verschieben">⋮⋮</span>
          <!-- Fester Platz für Qualitätspunkt — verhindert Verschiebung wenn kein Punkt -->
          <span class="q-indicator {qualityClass(track)}">
            {#if qualityTitle(track)}
              <span class="q-tip">{qualityTitle(track)}</span>
            {/if}
          </span>
          <div class="stripe"></div>

          <span class="idx" onclick={() => play(i)}>
            {#if active}<span class="play-dot">▶</span>{:else}{i + 1}{/if}
          </span>

          <span class="title" title={track.title}>{track.title}</span>

          {#if track.play_count > 0}
            <span class="pc">×{track.play_count}</span>
          {/if}

          <span class="dur">{fmt(track.duration_sec)}</span>
          {#if played && track.played_at}
            {@const d = new Date(track.played_at * 1000)}
            <span class="eta played-at">{d.getHours().toString().padStart(2,'0')}:{d.getMinutes().toString().padStart(2,'0')}</span>
          {:else if !active}
            {@const eta = trackEta(i)}
            {#if eta}<span class="eta">{eta}</span>{/if}
          {/if}

          <button class="remove" onclick={(e) => { e.stopPropagation(); remove(i) }}>✕</button>
        </div>
      {/each}
      <!-- drop zone after last item -->
      <div class="row-drop-end {dragOver === $queue.length ? 'drop-before' : ''}"
           ondragover={(e) => { if (dragFrom !== null) { e.preventDefault(); dragOver = $queue.length } }}
           ondrop={(e) => onRowDrop(e, $queue.length)}>
      </div>
    {/if}
  </div>
</div>

{#if plNameDialog}
<div class="meta-overlay" onclick={() => plNameDialog = false} role="dialog">
  <div class="meta-dialog" onclick={(e) => e.stopPropagation()}>
    <div class="meta-title">Playlist speichern</div>
    <label class="meta-label">Name
      <input class="meta-input" bind:value={plNameValue} placeholder="Playlist-Name…"
             onkeydown={(e) => { if (e.key === 'Enter') confirmSavePlaylist(); if (e.key === 'Escape') plNameDialog = false }}
             use:focus />
    </label>
    {#if qSelected.size > 0}
      <label class="meta-check">
        <input type="checkbox" bind:checked={plSaveOnlySelected} />
        Nur markierte Tracks speichern ({qSelected.size})
      </label>
    {/if}
    <label class="meta-check">
      <input type="checkbox" bind:checked={plClearAfterSave} />
      Queue nach dem Speichern leeren
    </label>
    <div class="meta-actions">
      <button class="meta-cancel" onclick={() => plNameDialog = false}>Abbrechen</button>
      <button class="meta-save" onclick={confirmSavePlaylist}>Speichern</button>
    </div>
  </div>
</div>
{/if}

{#if dlgClearPending}
<div class="meta-overlay" onclick={() => dlgClearPending = false} role="dialog">
  <div class="meta-dialog" onclick={(e) => e.stopPropagation()}>
    <div class="meta-title">Queue leeren?</div>
    <div class="meta-hint">Alle {$queue.length} Tracks werden entfernt.</div>
    <div class="meta-actions">
      <button class="meta-cancel" onclick={() => dlgClearPending = false}>Abbrechen</button>
      <button class="meta-save" style="background:#7a1a1a;border-color:#9a2a2a"
              onclick={() => { send({ type: 'queue_clear' }); dlgClearPending = false; showMenu = false }}>Leeren</button>
    </div>
  </div>
</div>
{/if}

{#if dlgDeletePath}
<div class="meta-overlay" onclick={() => dlgDeletePath = null} role="dialog">
  <div class="meta-dialog" onclick={(e) => e.stopPropagation()}>
    <div class="meta-title">Playlist löschen?</div>
    <div class="meta-hint">Diese Aktion kann nicht rückgängig gemacht werden.</div>
    <div class="meta-actions">
      <button class="meta-cancel" onclick={() => dlgDeletePath = null}>Abbrechen</button>
      <button class="meta-save" style="background:#7a1a1a;border-color:#9a2a2a"
              onclick={() => { send({ type: 'delete_playlist', path: dlgDeletePath }); dlgDeletePath = null; showMenu = false }}>Löschen</button>
    </div>
  </div>
</div>
{/if}

{#if qCtxMenu}
  <div class="q-ctx"
       style="left:{Math.min(qCtxMenu.x, window.innerWidth - 200)}px;top:{Math.min(qCtxMenu.y, window.innerHeight - 160)}px">
    <button onclick={() => { play(qCtxMenu.idx); closeRowCtx() }}>Abspielen</button>
    <button class="ctx-mix" onclick={() => { mixNow(qCtxMenu.idx); closeRowCtx() }}>⇌ Mix Now</button>
    <button onclick={() => { skipIntroFor($queue[qCtxMenu.idx]?.path); play(qCtxMenu.idx); closeRowCtx() }}>Intro überspringen</button>
    <div class="q-ctx-sep"></div>
    {#if qSelected.size > 1 && qSelected.has(qCtxMenu.idx)}
      <button class="q-ctx-danger" onclick={() => { removeSelected(); closeRowCtx() }}>{qSelected.size} löschen</button>
    {:else}
      <button class="q-ctx-danger" onclick={() => { remove(qCtxMenu.idx); closeRowCtx() }}>Löschen</button>
    {/if}
  </div>
{/if}

<style>
  .queue-outer {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    position: relative;
  }
  .queue-outer.drag-over { box-shadow: inset 0 0 0 2px var(--c-accent); }

  /* ── Header ─────────────────────────────────────────────────────────────── */
  .queue-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 8px;
    height: 30px;
    flex-shrink: 0;
    border-bottom: 1px solid var(--c-br1);
    background: var(--c-bg2);
  }

  .queue-title {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1.5px;
    color: var(--c-tx5);
    text-transform: uppercase;
  }

  .queue-count {
    font-size: 9px;
    color: var(--c-tx6);
    background: var(--c-bg5);
    border-radius: 8px;
    padding: 1px 5px;
  }
  .queue-dur {
    font-size: 9px;
    color: var(--c-tx7);
    font-variant-numeric: tabular-nums;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 2px;
    margin-left: auto;
  }

  .hdr-btn {
    background: none;
    border: none;
    color: var(--c-tx5);
    font-size: 11px;
    cursor: pointer;
    padding: 3px 6px;
    border-radius: 3px;
    transition: color .12s, background .12s;
    line-height: 1;
  }
  .hdr-btn:hover  { color: var(--c-tx2); background: var(--c-bg5); }
  .hdr-btn.active { color: var(--c-accent); }
  .sel-shuffle-btn { color: var(--c-blue); font-weight: 600; }
  .sel-shuffle-btn:hover { color: var(--c-tx3); background: var(--c-bg5); }
  .menu-btn { letter-spacing: 2px; }

  /* ── Dropdown menu ─────────────────────────────────────────────────────── */
  .menu-wrap { position: relative; }

  .dropdown {
    position: absolute;
    right: 0;
    top: 100%;
    background: var(--c-bg);
    border: 1px solid var(--c-br2);
    border-radius: 4px;
    z-index: 100;
    min-width: 200px;
    padding: 4px 0;
    box-shadow: 0 8px 24px rgba(0,0,0,.6);
  }

  .dropdown button {
    display: flex;
    align-items: center;
    width: 100%;
    padding: 6px 14px;
    background: none;
    border: none;
    color: var(--c-tx3);
    font-size: 12px;
    text-align: left;
    cursor: pointer;
    transition: background .1s, color .1s;
    gap: 8px;
  }
  .dropdown button:hover  { background: var(--c-br1); color: var(--c-tx2); }
  .dropdown .dd-danger { color: var(--c-red); }
  .dropdown .dd-danger:hover { color: var(--c-red) !important; background: var(--c-red-bg); }

  .dd-sep { height: 1px; background: var(--c-br1); margin: 3px 0; }
  .dropdown .dd-active { color: var(--c-accent) !important; }
  .dropdown .dd-active:hover { color: var(--c-accent2) !important; }
  .dd-empty { display: block; padding: 6px 14px; font-size: 11px; color: var(--c-tx7); }

  .dd-pl-row {
    display: flex;
    align-items: center;
    gap: 0;
  }
  .dd-pl-name {
    flex: 1;
    padding: 5px 14px 5px 24px !important;
    font-size: 11px !important;
    color: var(--c-tx5) !important;
  }
  .dd-pl-del {
    width: 28px !important;
    padding: 5px 6px !important;
    font-size: 10px !important;
    color: var(--c-tx7) !important;
    flex-shrink: 0 !important;
  }
  .dd-pl-del:hover { color: var(--c-red) !important; }

  /* ── Drop hint ─────────────────────────────────────────────────────────── */
  .drop-hint {
    position: absolute;
    inset: 30px 0 0 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    color: var(--c-accent);
    background: rgba(6, 9, 15, 0.85);
    z-index: 10;
    pointer-events: none;
    letter-spacing: .06em;
  }

  /* ── Track list ─────────────────────────────────────────────────────────── */
  .queue-list {
    flex: 1;
    overflow-y: auto;
  }

  .empty {
    padding: 32px 16px;
    text-align: center;
    color: var(--c-tx7);
    font-size: 11px;
  }

  /* Compact rows — text height only */
  .row {
    display: flex;
    align-items: center;
    height: 24px;
    border-bottom: 1px solid var(--c-bg2);
    transition: background 0.07s;
    cursor: default;
    position: relative;
  }
  /* Zebra — nur auf neutrale Zeilen (nicht active/sel) */
  .row:nth-child(even):not(.active):not(.q-sel) { background: var(--c-bg4); }
  .row:nth-child(odd):not(.active):not(.q-sel)  { background: var(--c-bg); }
  .row:hover  { background: var(--c-hover); }
  .row:hover .remove  { opacity: 1; }
  .row:hover .drag-handle { opacity: 0.5; }

  .row.active  { background: var(--c-act-bg); }
  .row.played  { opacity: 0.55; }
  .row.q-sel   { background: var(--c-sel); }
  .row.q-sel .stripe { background: #2a5aaa; }

  /* Drop indicator: orange line above the row */
  .row.drop-before  { box-shadow: inset 0 2px 0 0 var(--c-accent); }
  .row-drop-end     { height: 6px; }
  .row-drop-end.drop-before { box-shadow: inset 0 2px 0 0 var(--c-accent); }

  /* Colored left stripe */
  .stripe {
    width: 3px;
    height: 100%;
    background: var(--c-br2);
    flex-shrink: 0;
    transition: background 0.12s;
  }
  .row.active .stripe { background: var(--c-accent); }
  .row.played .stripe { background: var(--c-red-bg); }
  .row:hover  .stripe { background: var(--c-br2); }

  .drag-handle {
    width: 16px;
    font-size: 9px;
    color: var(--c-tx7);
    cursor: grab;
    flex-shrink: 0;
    text-align: center;
    opacity: 0;
    transition: opacity 0.1s;
    user-select: none;
  }
  .drag-handle:active { cursor: grabbing; }

  .idx {
    width: 28px;
    font-size: 10px;
    color: var(--c-tx5);
    text-align: center;
    flex-shrink: 0;
    cursor: pointer;
    transition: color 0.1s;
  }
  .row.active .idx { color: var(--c-accent); }
  .idx:hover { color: var(--c-tx4); }
  .play-dot  { font-size: 8px; }

  .title {
    flex: 1;
    font-size: 12px;
    color: var(--c-tx3);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    padding: 0 4px;
  }
  .row.active .title { color: var(--c-act-tx); font-weight: 600; }
  .row.played .title { color: var(--c-tx5); }

  .pc {
    font-size: 9px;
    color: var(--c-accent);
    flex-shrink: 0;
    padding: 0 4px;
    opacity: 0.7;
  }

  .eta {
    font-size: 9px;
    color: var(--c-tx7);
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
    white-space: nowrap;
    padding-right: 4px;
  }
  .played-at { color: var(--c-green); }
  .dur {
    font-size: 10px;
    color: var(--c-tx5);
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
    padding: 0 2px 0 4px;
    min-width: 32px;
    text-align: right;
  }

  .remove {
    background: none;
    border: none;
    color: var(--c-tx7);
    font-size: 9px;
    cursor: pointer;
    width: 20px;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    opacity: 0.4;
    transition: color 0.1s, opacity 0.1s;
  }
  .remove:hover { color: var(--c-red); opacity: 1; }

  /* ── Row context menu ──────────────────────────────────────────────────── */
  .q-ctx {
    position: fixed;
    z-index: 500;
    background: var(--c-bg);
    border: 1px solid var(--c-br2);
    border-radius: 4px;
    padding: 4px 0;
    min-width: 160px;
    box-shadow: 0 8px 24px rgba(0,0,0,.7);
  }
  .q-ctx button {
    display: block;
    width: 100%;
    padding: 6px 14px;
    background: none;
    border: none;
    color: var(--c-tx3);
    font-size: 12px;
    text-align: left;
    cursor: pointer;
    transition: background .08s, color .08s;
  }
  .q-ctx button:hover { background: var(--c-br1); color: var(--c-tx2); }
  .q-ctx-sep { height: 1px; background: var(--c-br1); margin: 3px 0; }
  .q-ctx-danger { color: var(--c-red) !important; }
  .q-ctx-danger:hover { color: var(--c-red) !important; background: var(--c-red-bg) !important; }
  .ctx-mix { color: var(--c-accent) !important; }
  .ctx-mix:hover { color: var(--c-accent2) !important; background: var(--c-act-bg) !important; }

  /* Fester Platz — immer gerendert, transparent wenn kein Qualitätsstatus */
  .q-indicator {
    width: 5px; height: 5px; border-radius: 50%;
    flex-shrink: 0; margin-right: 2px;
    background: transparent;
    position: relative;
  }
  .q-tip {
    display: none;
    position: absolute;
    left: 10px; top: 50%; transform: translateY(-50%);
    background: var(--c-bg5); border: 1px solid var(--c-br3);
    color: var(--c-tx3); font-size: 10px; font-weight: 400;
    white-space: nowrap; padding: 3px 8px; border-radius: 3px;
    pointer-events: none; z-index: 300;
    box-shadow: 0 4px 12px rgba(0,0,0,.7);
  }
  .q-indicator:hover .q-tip { display: block; }
  .q-bad   { background: var(--c-red); }
  .q-quiet { background: #c07020; }
  .q-loud  { background: #c07020; }
  .q-good  { background: var(--c-green); }

  .am-status {
    font-size: 10px;
    color: var(--c-accent);
    flex-shrink: 0;
    max-width: 140px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* ── Custom dialogs (same design as Library.svelte) ────────────────────── */
  .meta-overlay { position:fixed; inset:0; background:rgba(0,0,0,.6); z-index:9000; display:flex; align-items:center; justify-content:center; }
  .meta-dialog  { background:var(--c-bg5); border:1px solid var(--c-br3); border-radius:6px; padding:20px; min-width:300px; max-width:400px; }
  .meta-title   { font-size:13px; font-weight:700; color:var(--c-tx2); margin-bottom:16px; }
  .meta-hint    { font-size:11px; color:var(--c-tx5); margin-bottom:16px; }
  .meta-label   { display:flex; flex-direction:column; gap:5px; font-size:11px; color:var(--c-tx4); margin-bottom:14px; }
  .meta-input   { background:var(--c-bg2); border:1px solid var(--c-br3); border-radius:4px; color:var(--c-tx2); padding:7px 10px; font-size:12px; outline:none; }
  .meta-input:focus { border-color:var(--c-accent); }
  .meta-check   { display:flex; align-items:center; gap:7px; font-size:11px; color:var(--c-tx4); margin-bottom:10px; cursor:pointer; user-select:none; }
  .meta-check input[type=checkbox] { accent-color:var(--c-accent); width:13px; height:13px; cursor:pointer; }
  .meta-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:4px; }
  .meta-cancel  { background:none; border:1px solid var(--c-br3); border-radius:4px; color:var(--c-tx4); padding:6px 14px; font-size:11px; cursor:pointer; }
  .meta-cancel:hover { border-color:var(--c-tx5); color:var(--c-tx3); }
  .meta-save    { background:#8a4000; border:1px solid #b05000; border-radius:4px; color:#ffd090; padding:6px 14px; font-size:11px; cursor:pointer; }
  .meta-save:hover { background:#a05000; }
</style>
