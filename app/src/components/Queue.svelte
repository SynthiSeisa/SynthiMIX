<script>
  import { get } from 'svelte/store'
  import { keyCompat } from '../lib/keys.js'
  import KeyChip from './KeyChip.svelte'
  import { queue, playerState, library, playlists, playMode, send, automixStatus, introSkipPaths, settings, appSettings, skipNextCrossfade, autoRemovePlayed, livePositionMs, selectionOwner, radioEnabled, radioStatus, lastfmApiKey } from '../stores/ws.js'

  // Tonart kommt aus der Bibliothek: Queue-Eintraege entstehen an vielen Stellen
  // und tragen sie nicht selbst mit.
  const keyByPath = $derived(new Map($library.filter(t => t.key).map(t => [t.path, t])))
  function keyOf(track) { return keyByPath.get(track?.path) ?? null }
  // BPM ebenso: die Bibliothek misst nach, der Queue-Eintrag behaelt den alten Stand
  const bpmByPath = $derived(new Map($library.filter(t => t.bpm).map(t => [t.path, t.bpm])))
  function bpmOf(track) { return bpmByPath.get(track?.path) ?? track?.bpm ?? null }

  // Spalten, umschaltbar im •••-Menue (Titel und Dauer immer)
  const QCOLS = [
    ['qShowEta',   'Startzeit'],
    ['qShowKey',   'Tonart'],
    ['qShowBpm',   'BPM'],
    ['qShowPlays', 'Wiedergaben (×N)'],
  ]
  const showKey   = $derived($appSettings.qShowKey ?? false)
  const showBpm   = $derived($appSettings.qShowBpm ?? false)
  const showEta   = $derived($appSettings.qShowEta ?? true)
  const showPlays = $derived($appSettings.qShowPlays ?? false)

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
  function shuffleSelected()      { send({ type: 'queue_shuffle_selected', indices: [...qSelected] }); showMenu = false; qSelected = new Set() }
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

  // Fest positioniert: die Warteschlange schneidet ein absolut gesetztes Menue ab
  let menuBtn = $state(null)
  let menuPos = $state('')
  function toggleMenu() {
    if (!showMenu && menuBtn) {
      const r = menuBtn.getBoundingClientRect()
      menuPos = `right:${window.innerWidth - r.right}px;top:${r.bottom + 4}px;max-height:${window.innerHeight - r.bottom - 16}px`
    }
    showMenu = !showMenu; showPlaylists = false
  }

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

  const remainingSecs = $derived.by(() => {
    const idx = $playerState.current_idx
    if (idx < 0 || $queue.length === 0) return null
    const last  = $queue.length - 1
    const secs  = etaSecs[last]
    if (secs === null) return null
    return secs + ($queue[last]?.duration_sec ?? 0)
  })
  const remainingStr = $derived.by(() => {
    const grand = remainingSecs
    if (grand === null) return ''
    const h = Math.floor(grand / 3600)
    const m = Math.floor((grand % 3600) / 60)
    const s = Math.floor(grand % 60)
    return h > 0 ? `−${h} h ${m} min` : m > 0 ? `−${m} min ${String(s).padStart(2,'0')} s` : `−${s} s`
  })
  // Uhrzeit, zu der die Warteschlange durchgelaufen ist
  const endClock = $derived.by(() => {
    if (remainingSecs === null) return ''
    const d = new Date(Date.now() + remainingSecs * 1000)
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  })
  // Der naechste Titel (bei Zufallswiedergabe entscheidet der Player, dann ohne Markierung)
  const nextIdx = $derived(!shuffle && $playerState.current_idx >= 0 && $playerState.current_idx + 1 < $queue.length
                           ? $playerState.current_idx + 1 : -1)

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
    // Markierung nur bei Klicks ausserhalb der Warteschlange verwerfen. Vorher
    // reichte schon der Klick aufs •••-Menue — "Nur markierte mischen" und
    // "Nur markierte speichern" waren dadurch nie erreichbar.
    if (!e.target.closest('.queue-outer') && !e.target.closest('.meta-overlay') &&
        !e.target.closest('.q-ctx')) qSelected = new Set()
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
    <span class="eyebrow">Warteschlange</span>
    <span class="queue-count">{$queue.length}</span>
    <button class="btn btn-sm" class:is-active={$radioEnabled}
            title={$lastfmApiKey ? ($radioEnabled ? 'Radio-Modus deaktivieren' : 'Radio-Modus: Queue automatisch mit ähnlichen Tracks füllen') : 'Last.fm API-Key in Einstellungen → Dienste eintragen'}
            onclick={() => send({ type: 'set_radio', enabled: !$radioEnabled })}>
      <i class="ti ti-radio"></i> Radio
    </button>

    <div class="header-actions">
      <button class="btn btn-icon btn-sm" class:is-active={shuffle} onclick={toggleShuffle}
              title={shuffle ? 'Zufallswiedergabe an' : 'Zufallswiedergabe aus'} aria-label="Zufallswiedergabe" aria-pressed={shuffle}><i class="ti ti-arrows-shuffle"></i></button>
      <button class="btn btn-icon btn-sm" class:is-active={repeat > 0} onclick={cycleRepeat}
              title={repeat === 1 ? 'Titel wiederholen' : repeat === 2 ? 'Warteschlange wiederholen' : 'Wiederholen aus'} aria-label="Wiederholen"><i class="ti {repeat === 1 ? 'ti-repeat-once' : 'ti-repeat'}"></i></button>
      <div class="menu-wrap">
        <button class="btn btn-icon btn-sm" class:is-active={showMenu} bind:this={menuBtn} onclick={toggleMenu} title="Menü" aria-label="Menü"><i class="ti ti-dots"></i></button>
        {#if showMenu}
          <div class="ctx-menu dropdown" style={menuPos}>
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
                    <button class="dd-pl-del ctx-danger" onclick={() => deletePlaylist(pl.path)} title="Playlist löschen" aria-label="Playlist löschen"><i class="ti ti-trash"></i></button>
                  </div>
                {/each}
              {/if}
            {/if}
            <button onclick={() => { shuffleQueue(); showMenu = false }}>Einmalig mischen</button>
            <button onclick={shuffleUnplayed}>Nur ungespielte mischen</button>
            {#if qSelected.size > 1}
              <button onclick={shuffleSelected}>Nur markierte mischen ({qSelected.size})</button>
            {/if}
            {#if $playerState.current_idx >= 0}
              <button onclick={() => { scrollToCurrent(); showMenu = false }}>Zum laufenden Titel springen</button>
            {/if}
            <div class="ctx-sep"></div>
            <button onclick={markAllUnplayed}>Alle als ungespielt markieren</button>
            <button onclick={removePlayed}>Gespielte entfernen</button>
            <button onclick={removeQueueDuplicates}>Duplikate entfernen</button>
            <button onclick={toggleAutoRemove} class:dd-active={$autoRemovePlayed}>
              <i class="ti {$autoRemovePlayed ? 'ti-check' : 'ti-minus'} dd-check"></i> Gespielte automatisch entfernen
            </button>
            <div class="ctx-sep"></div>
            <!-- Spalten: Menue bleibt offen, damit man mehrere umschalten kann -->
            <span class="dd-head">Anzeigen</span>
            {#each QCOLS as [k, label]}
              <button role="menuitemcheckbox" aria-checked={!!$appSettings[k]} class:dd-active={$appSettings[k]}
                      onclick={() => appSettings.update(s => ({ ...s, [k]: !s[k] }))}>
                <i class="ti {$appSettings[k] ? 'ti-check' : 'ti-minus'} dd-check"></i> {label}
              </button>
            {/each}
            <div class="ctx-sep"></div>
            <button onclick={clearQueue} class="ctx-danger">Queue leeren</button>
          </div>
        {/if}
      </div>
    </div>
  </div>

  <!-- Restzeit und Ende: eigene Zeile, damit beides gross lesbar bleibt -->
  {#if remainingStr || totalDurStr || $automixStatus || $radioStatus}
    <div class="queue-sub">
      {#if remainingStr}
        <span class="queue-dur" title="Restzeit ab jetzt">{remainingStr}</span>
        <span class="queue-end">Ende ~{endClock}</span>
      {:else if totalDurStr}
        <span class="queue-dur">{totalDurStr}</span>
      {/if}
      {#if $automixStatus}
        <span class="am-status">{$automixStatus}</span>
      {/if}
      {#if $radioStatus}
        <span class="radio-added" title="Ähnlich zu: {$radioStatus.similar_to}">+ {$radioStatus.title}</span>
      {/if}
    </div>
  {/if}

  <!-- Drop: Einfügelinie oben wenn Cursor über leerem Bereich -->
  {#if isDragOver && dragOver === null}
    <div class="drop-banner"><i class="ti ti-plus"></i> Hier einreihen</div>
  {/if}

  <!-- Track list -->
  <div class="queue-list" bind:this={qListEl}>
    {#if $queue.length === 0}
      <div class="empty">Queue leer · Tracks aus der Bibliothek hierher ziehen</div>
    {:else}
      {#each $queue as track, i}
        {@const active = i === $playerState.current_idx}
        {@const played = track.played && !active}
        {@const isNext = i === nextIdx}
        <div
          class="row {active ? 'active' : played ? 'played' : ''} {isNext ? 'next' : ''} {dragOver === i ? 'drop-before' : ''} {qSelected.has(i) ? 'q-sel' : ''}"
          draggable="true"
          ondragstart={(e) => onRowDragStart(e, i)}
          ondragover={(e) => onRowDragOver(e, i)}
          ondrop={(e) => onRowDrop(e, i)}
          ondragend={onDragEnd}
          onclick={(e) => qRowClick(e, i)}
          ondblclick={() => mixNow(i)}
          oncontextmenu={(e) => openRowCtx(e, i)}>

          <span class="drag-handle" title="Verschieben"><i class="ti ti-grip-vertical"></i></span>
          <!-- Fester Platz für Qualitätspunkt — verhindert Verschiebung wenn kein Punkt -->
          <span class="q-indicator {qualityClass(track)}">
            {#if qualityTitle(track)}
              <span class="q-tip">{qualityTitle(track)}</span>
            {/if}
          </span>
          <span class="idx" onclick={() => play(i)} title="Sofort abspielen">
            {#if active}<i class="ti ti-player-play-filled"></i>{:else}{i + 1}{/if}
          </span>

          <span class="title" title={active ? `Läuft: ${track.title}` : isNext ? `Als Nächstes: ${track.title}` : track.title}>{track.title}</span>

          {#if showPlays && track.play_count > 0}
            <span class="pc" title="So oft gespielt">×{track.play_count}</span>
          {/if}

          {#if showBpm}
            {@const bpm = bpmOf(track)}
            <span class="bpm">{bpm ? Math.round(bpm) : ''}</span>
          {/if}

          {#if showKey && keyOf(track)}
            {@const k = keyOf(track)}
            {@const uebergang = i > 0 ? keyCompat(keyOf($queue[i - 1])?.key, k.key) : null}
            <span class="key"><KeyChip key={k.key} src={k.key_src} compat={uebergang} hint="Übergang vom vorherigen Titel: " /></span>
          {/if}
          <span class="dur">{fmt(track.duration_sec)}</span>
          <!-- Startzeit-Spalte immer ausgeben (auch leer), damit die Dauer buendig bleibt -->
          {#if showEta}
            {#if played && track.played_at}
              {@const d = new Date(track.played_at * 1000)}
              <span class="eta played-at">{d.getHours().toString().padStart(2,'0')}:{d.getMinutes().toString().padStart(2,'0')}</span>
            {:else}
              <span class="eta">{active ? '' : (trackEta(i) ?? '')}</span>
            {/if}
          {/if}

          <button class="btn btn-icon btn-sm remove" onclick={(e) => { e.stopPropagation(); remove(i) }}
                  title="Aus der Warteschlange entfernen" aria-label="Aus der Warteschlange entfernen"><i class="ti ti-x"></i></button>
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
<div class="dlg-overlay" onclick={() => plNameDialog = false} role="dialog">
  <div class="dlg" onclick={(e) => e.stopPropagation()}>
    <div class="dlg-title">Playlist speichern</div>
    <label class="dlg-field">Name
      <input class="field" bind:value={plNameValue} placeholder="Playlist-Name…"
             onkeydown={(e) => { if (e.key === 'Enter') confirmSavePlaylist(); if (e.key === 'Escape') plNameDialog = false }}
             use:focus />
    </label>
    {#if qSelected.size > 0}
      <label class="dlg-check">
        <input type="checkbox" bind:checked={plSaveOnlySelected} />
        Nur markierte Tracks speichern ({qSelected.size})
      </label>
    {/if}
    <label class="dlg-check">
      <input type="checkbox" bind:checked={plClearAfterSave} />
      Queue nach dem Speichern leeren
    </label>
    <div class="dlg-actions">
      <button class="btn" onclick={() => plNameDialog = false}>Abbrechen</button>
      <button class="btn btn-primary" onclick={confirmSavePlaylist}>Speichern</button>
    </div>
  </div>
</div>
{/if}

{#if dlgClearPending}
<div class="dlg-overlay" onclick={() => dlgClearPending = false} role="dialog">
  <div class="dlg" onclick={(e) => e.stopPropagation()}>
    <div class="dlg-title">Queue leeren?</div>
    <div class="dlg-hint">Alle {$queue.length} Tracks werden entfernt.</div>
    <div class="dlg-actions">
      <button class="btn" onclick={() => dlgClearPending = false}>Abbrechen</button>
      <button class="btn btn-danger"
              onclick={() => { send({ type: 'queue_clear' }); dlgClearPending = false; showMenu = false }}>Leeren</button>
    </div>
  </div>
</div>
{/if}

{#if dlgDeletePath}
<div class="dlg-overlay" onclick={() => dlgDeletePath = null} role="dialog">
  <div class="dlg" onclick={(e) => e.stopPropagation()}>
    <div class="dlg-title">Playlist löschen?</div>
    <div class="dlg-hint">Die Playlist-Datei wandert in den Papierkorb. Die Titel selbst bleiben unberührt.</div>
    <div class="dlg-actions">
      <button class="btn" onclick={() => dlgDeletePath = null}>Abbrechen</button>
      <button class="btn btn-danger"
              onclick={() => { send({ type: 'delete_playlist', path: dlgDeletePath }); dlgDeletePath = null; showMenu = false }}>In den Papierkorb</button>
    </div>
  </div>
</div>
{/if}

{#if qCtxMenu}
  <div class="ctx-menu q-ctx"
       style="left:{Math.min(qCtxMenu.x, window.innerWidth - 200)}px;top:{Math.min(qCtxMenu.y, window.innerHeight - 160)}px">
    <button onclick={() => { play(qCtxMenu.idx); closeRowCtx() }}>Abspielen</button>
    <button class="ctx-mix" onclick={() => { mixNow(qCtxMenu.idx); closeRowCtx() }}>Jetzt mischen</button>
    <button onclick={() => { skipIntroFor($queue[qCtxMenu.idx]?.path); play(qCtxMenu.idx); closeRowCtx() }}>Intro überspringen</button>
    <div class="ctx-sep"></div>
    {#if qSelected.size > 1 && qSelected.has(qCtxMenu.idx)}
      <button class="ctx-danger" onclick={() => { removeSelected(); closeRowCtx() }}>{qSelected.size} löschen</button>
    {:else}
      <button class="ctx-danger" onclick={() => { remove(qCtxMenu.idx); closeRowCtx() }}>Löschen</button>
    {/if}
  </div>
{/if}

<style>
  .queue-outer { flex: 1; display: flex; flex-direction: column; overflow: hidden; position: relative; }
  .queue-outer.drag-over { box-shadow: inset 0 0 0 2px var(--c-accent); }

  /* ── Kopf: Restzeit und Ende gross, Werkzeuge rechts ─────────────────── */
  .queue-header {
    display: flex; align-items: center; gap: var(--sp-2);
    height: 40px; padding: 0 var(--sp-2) 0 var(--sp-3); flex-shrink: 0;
    background: var(--c-bg2); border-bottom: 1px solid var(--c-br1);
  }
  .queue-count {
    font-size: var(--fs-cap); font-weight: 700; color: var(--c-tx2); font-variant-numeric: tabular-nums;
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: 10px; padding: 1px 7px;
  }
  .queue-sub {
    display: flex; align-items: baseline; gap: var(--sp-3); flex-shrink: 0;
    padding: 6px var(--sp-3); border-bottom: 1px solid var(--c-br1); background: var(--c-bg2);
  }
  .queue-dur { font-size: var(--fs-h); font-weight: 700; color: var(--c-tx1); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .queue-end { font-size: var(--fs-body); color: var(--c-tx3); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .am-status, .radio-added {
    font-size: var(--fs-sm); flex-shrink: 1; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .am-status { color: var(--c-accent-tx); }
  .radio-added { color: var(--c-green-tx); animation: fadeIn .3s ease; }
  @keyframes fadeIn { from { opacity: 0 } to { opacity: 1 } }
  .header-actions { display: flex; align-items: center; gap: 2px; margin-left: auto; }

  /* Menue: Aussehen vom Kontextmenue, aber am Knopf verankert */
  .menu-wrap { position: relative; }
  .dropdown { position: fixed; z-index: 500; min-width: 250px; overflow-y: auto; }
  .dropdown .dd-active { color: var(--c-accent-tx); }
  .dd-check { width: 14px; font-size: 14px; }
  .dd-head { display: block; padding: var(--sp-1) var(--sp-3) 2px; font-size: var(--fs-cap); font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--c-tx4); }
  .dd-empty { display: block; padding: var(--sp-2) var(--sp-3); font-size: var(--fs-sm); color: var(--c-tx5); }
  .dd-pl-row { display: flex; align-items: center; }
  .dd-pl-row .dd-pl-name { flex: 1; padding-left: var(--sp-5); }
  .dd-pl-row .dd-pl-del { width: var(--btn-h); justify-content: center; padding: 0; }

  .drop-banner {
    position: absolute; bottom: 0; left: 0; right: 0; z-index: 10; height: 36px;
    display: flex; align-items: center; justify-content: center; gap: 6px;
    font-size: var(--fs-body); font-weight: 600; color: var(--c-accent-tx);
    background: var(--c-act-bg); border-top: 2px solid var(--c-accent);
    pointer-events: none;
  }

  /* ── Liste ───────────────────────────────────────────────────────────── */
  .queue-list { flex: 1; overflow-y: auto; }
  .empty { padding: 40px var(--sp-4); text-align: center; color: var(--c-tx4); font-size: var(--fs-body); }

  .row {
    display: flex; align-items: center; gap: 6px;
    min-height: var(--q-row-h); padding: 0 var(--sp-1) 0 2px;
    border-bottom: 1px solid var(--c-br1); position: relative; cursor: default;
    font-size: var(--q-fs);
  }
  .row:hover { background: var(--c-hover); }
  .row:hover .remove, .row:hover .drag-handle, .row.q-sel .remove { opacity: 1; }

  /* Gespielt: gedaempfte, aber lesbare Schrift statt halber Deckkraft */
  .row.played .title, .row.played .idx { color: var(--c-tx5); }

  /* Laufend: Flaeche, Kante, groesser — aus zwei Metern erkennbar */
  .row.active {
    min-height: calc(var(--q-row-h) + 12px);
    background: var(--c-act-bg); box-shadow: inset 4px 0 0 var(--c-accent);
  }
  .row.active .title { color: var(--c-tx1); font-weight: 700; font-size: calc(var(--q-fs) + 2px); }
  .row.active .idx { color: var(--c-accent-tx); }
  /* Naechster: gedaempft orange Kante und kraeftigere Schrift */
  .row.next { box-shadow: inset 4px 0 0 color-mix(in srgb, var(--c-accent) 50%, transparent); }
  .row.next .title { color: var(--c-tx1); font-weight: 600; }

  .row.q-sel { background: var(--c-sel); box-shadow: inset 4px 0 0 var(--c-blue); }
  .row.drop-before { box-shadow: inset 0 3px 0 0 var(--c-accent); }
  .row-drop-end { height: 8px; }
  .row-drop-end.drop-before { box-shadow: inset 0 3px 0 0 var(--c-accent); }

  .drag-handle {
    width: 16px; flex-shrink: 0; text-align: center; font-size: 14px; color: var(--c-tx5);
    cursor: grab; opacity: 0; transition: opacity .1s;
  }
  .drag-handle:active { cursor: grabbing; }

  .idx {
    width: 26px; flex-shrink: 0; text-align: right; cursor: pointer;
    font-size: var(--fs-cap); color: var(--c-tx4); font-variant-numeric: tabular-nums;
  }
  .idx:hover { color: var(--c-accent-tx); }

  .title {
    flex: 1; min-width: 0; padding: 0 var(--sp-1);
    color: var(--c-tx2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }

  .pc { flex-shrink: 0; font-size: var(--fs-cap); font-weight: 600; color: var(--c-accent-tx); font-variant-numeric: tabular-nums; }
  .key { flex-shrink: 0; font-size: var(--fs-sm); }
  .bpm { flex-shrink: 0; min-width: 28px; text-align: right; font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; }
  .dur {
    flex-shrink: 0; min-width: 38px; text-align: right;
    font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums;
  }
  .row.active .dur { color: var(--c-tx1); font-weight: 600; }
  .eta { flex-shrink: 0; min-width: 44px; text-align: right; font-size: var(--fs-cap); color: var(--c-tx4); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .played-at { color: var(--c-green-tx); }
  .remove { --h: 24px; opacity: 0; transition: opacity .1s; }
  .remove:hover { color: var(--c-red-tx); background: var(--c-red-bg); }

  /* Qualitaetspunkt mit Erklaerung beim Draufzeigen */
  .q-indicator { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; background: transparent; position: relative; }
  .q-tip {
    display: none; position: absolute; left: 12px; top: 50%; transform: translateY(-50%); z-index: 300;
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: var(--r-s);
    color: var(--c-tx2); font-size: var(--fs-sm); white-space: nowrap; padding: 4px 8px;
    pointer-events: none; box-shadow: 0 6px 18px rgba(0,0,0,.4);
  }
  .q-indicator:hover .q-tip { display: block; }
  .q-bad   { background: var(--c-red-tx); }
  .q-quiet, .q-loud { background: var(--c-warn-tx); }
  .q-good  { background: var(--c-green-tx); }
</style>
