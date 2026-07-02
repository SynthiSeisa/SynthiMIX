<script>
  let { groups = [], onclose, onremove } = $props()

  // Local copy so user actions (keep/skip) can remove groups without affecting the parent
  let remaining = $state([])
  $effect(() => { remaining = [...groups] })

  // Checkbox-Auswahl für Mehrfachlöschung (nur "Kopie"-Tracks, nie die "Beste" Version)
  let checked = $state(new Set())
  function toggleCheck(path) {
    const s = new Set(checked)
    s.has(path) ? s.delete(path) : s.add(path)
    checked = s
  }
  const checkedCount = $derived(checked.size)
  function deleteChecked() {
    if (checked.size === 0) return
    if (!confirm(`${checked.size} ausgewählte Kopien dauerhaft von der Festplatte löschen?`)) return
    for (const p of checked) onremove(p)
    const del = checked
    remaining = remaining.map(g => ({
      best: g.best,
      others: g.others.filter(t => !del.has(t.path)),
    })).filter(g => g.others.length > 0)
    checked = new Set()
  }

  // Sortier-/Filtermodus: nach Ordner gruppieren statt nach Song
  let groupByFolder = $state(false)
  let folderFilter = $state('')

  const byFolderView = $derived.by(() => {
    if (!groupByFolder) return []
    const map = new Map() // dir → { dir, name, items: [{group, track}] }
    for (const g of remaining) {
      for (const t of g.others) {
        const dir = trackDir(t)
        if (!map.has(dir)) map.set(dir, { dir, name: dir.split('/').pop() || dir, items: [] })
        map.get(dir).items.push({ group: g, track: t })
      }
    }
    let list = [...map.values()].sort((a, b) => b.items.length - a.items.length)
    if (folderFilter.trim()) {
      const q = folderFilter.toLowerCase()
      list = list.filter(f => f.dir.toLowerCase().includes(q))
    }
    return list
  })

  function fmt(sec) {
    if (!sec) return ''
    return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
  }

  // Full directory path of a track
  function trackDir(track) {
    return track.path.replace(/\\/g, '/').split('/').slice(0, -1).join('/')
  }

  // Folders that appear only as "others" (duplicate copies, not best versions)
  const folderGroups = $derived.by(() => {
    const bestDirs = new Set(remaining.map(g => trackDir(g.best)))
    const map = new Map() // dir → { name, tracks[] }
    for (const g of remaining) {
      for (const t of g.others) {
        const dir = trackDir(t)
        if (!map.has(dir)) {
          const name = dir.split('/').pop() || dir
          map.set(dir, { dir, name, tracks: [] })
        }
        map.get(dir).tracks.push(t)
      }
    }
    // Only show folders that have NO "best" tracks (safe to nuke entirely)
    return [...map.values()]
      .filter(f => !bestDirs.has(f.dir))
      .sort((a, b) => b.tracks.length - a.tracks.length)
  })

  function keepOnly(group, trackToKeep) {
    const allInGroup = [group.best, ...group.others]
    allInGroup.forEach(t => { if (t.path !== trackToKeep.path) onremove(t.path) })
    remaining = remaining.filter(g => g.best.path !== group.best.path)
  }

  function removeTrack(group, track) {
    onremove(track.path)
    const newAll = [group.best, ...group.others].filter(t => t.path !== track.path)
    if (newAll.length < 2) {
      remaining = remaining.filter(g => g.best.path !== group.best.path)
    } else {
      remaining = remaining.map(g => g.best.path === group.best.path
        ? { best: newAll[0], others: newAll.slice(1) }
        : g)
    }
  }

  function skipGroup(group) {
    remaining = remaining.filter(g => g.best.path !== group.best.path)
  }

  function deleteFolderDupes(folderEntry) {
    folderEntry.tracks.forEach(t => onremove(t.path))
    const deletedPaths = new Set(folderEntry.tracks.map(t => t.path))
    remaining = remaining.map(g => {
      const newOthers = g.others.filter(t => !deletedPaths.has(t.path))
      return { best: g.best, others: newOthers }
    }).filter(g => g.others.length > 0)
  }

  function removeAllLower() {
    const count = groups.reduce((n, g) => n + g.others.length, 0)
    if (!confirm(`${count} niedrigwertige Kopien dauerhaft von der Festplatte löschen?`)) return
    for (const g of remaining) g.others.forEach(t => onremove(t.path))
    onclose()
  }
</script>

<div class="overlay" onclick={onclose} role="dialog">
  <div class="panel" onclick={(e) => e.stopPropagation()}>

    <div class="hdr">
      <span class="title">DUPLIKATE PRÜFEN</span>
      <span class="subtitle">{remaining.length} Gruppen</span>
      <button class="mode-toggle {groupByFolder ? 'active' : ''}"
              onclick={() => groupByFolder = !groupByFolder}
              title="Nach Ordner gruppieren statt nach Song">
        📁 Nach Ordner
      </button>
      <button class="remove-all-btn" onclick={removeAllLower}>✕ Alle Kopien löschen</button>
      <button class="close-btn" onclick={onclose}>✕</button>
    </div>

    {#if folderGroups.length > 0}
      <div class="folder-bar">
        <span class="folder-bar-label">Ordner komplett löschen:</span>
        {#each folderGroups as f}
          <button class="folder-del-btn" onclick={() => deleteFolderDupes(f)}
                  title="Alle {f.tracks.length} Kopien aus '{f.dir}' von Festplatte löschen">
            📁 {f.name} <span class="folder-count">×{f.tracks.length}</span>
          </button>
        {/each}
      </div>
    {/if}

    {#if groupByFolder}
      <div class="filter-bar">
        <input class="folder-filter" placeholder="Ordner filtern…" bind:value={folderFilter} />
        {#if checkedCount > 0}
          <button class="del-checked-btn" onclick={deleteChecked}>
            🗑 {checkedCount} markierte löschen
          </button>
        {/if}
      </div>
    {/if}

    <div class="body">
      {#if remaining.length === 0}
        <div class="done">✓ Alle Duplikate bereinigt</div>
      {:else if groupByFolder}
        {#if byFolderView.length === 0}
          <div class="done">Keine Ordner gefunden</div>
        {/if}
        {#each byFolderView as folder}
          <div class="group">
            <div class="group-hdr">
              <span class="group-title" title={folder.dir}>{folder.name}</span>
              <span class="folder-item-count">{folder.items.length} Kopien</span>
            </div>
            {#each folder.items as { group, track }}
              <div class="track-row worse checkable">
                <input type="checkbox" class="track-check"
                       checked={checked.has(track.path)}
                       onchange={() => toggleCheck(track.path)} />
                <div class="track-badge">✕ Kopie</div>
                <div class="track-info">
                  <div class="track-title">{track.title}</div>
                  <div class="track-meta">
                    <span class="meta-tag q">{track.bitrate_kbps || '?'} kbps</span>
                    {#if track.lufs && track.lufs > -90}
                      <span class="meta-tag l">{track.lufs?.toFixed(1)} LUFS</span>
                    {/if}
                    <span class="meta-tag d">{fmt(track.duration_sec)}</span>
                    <span class="meta-path" title={track.path}>{trackDir(track)}</span>
                  </div>
                </div>
                <div class="track-actions">
                  <button class="keep-btn" onclick={() => keepOnly(group, track)}
                          title="Diese behalten, alle anderen in der Gruppe löschen">Behalten</button>
                  <button class="del-btn" onclick={() => removeTrack(group, track)}
                          title="Diese Datei dauerhaft von der Festplatte löschen">🗑 Von Platte löschen</button>
                </div>
              </div>
            {/each}
          </div>
        {/each}
      {:else}
        {#each remaining as group}
          {@const allTracks = [group.best, ...group.others]}
          <div class="group">
            <div class="group-hdr">
              <span class="group-title">{group.best.title?.replace(/^.+?\s+[-–—]\s+/, '') || group.best.title}</span>
              <button class="skip-btn" onclick={() => skipGroup(group)}>Überspringen</button>
            </div>

            {#each allTracks as track, i}
              <div class="track-row {i === 0 ? 'best' : 'worse'}">
                <div class="track-badge">{i === 0 ? '★ Beste' : '✕ Kopie'}</div>
                <div class="track-info">
                  <div class="track-title">{track.title}</div>
                  <div class="track-meta">
                    <span class="meta-tag q">{track.bitrate_kbps || '?'} kbps</span>
                    {#if track.lufs && track.lufs > -90}
                      <span class="meta-tag l">{track.lufs?.toFixed(1)} LUFS</span>
                    {/if}
                    <span class="meta-tag d">{fmt(track.duration_sec)}</span>
                    <span class="meta-path" title={track.path}>{trackDir(track)}</span>
                  </div>
                </div>
                <div class="track-actions">
                  <button class="keep-btn" onclick={() => keepOnly(group, track)}
                          title="Diese behalten, alle anderen in der Gruppe löschen">Behalten</button>
                  <button class="del-btn" onclick={() => removeTrack(group, track)}
                          title="Diese Datei dauerhaft von der Festplatte löschen">🗑 Von Platte löschen</button>
                </div>
              </div>
            {/each}
          </div>
        {/each}
      {/if}
    </div>

  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 1100;
    background: rgba(0,0,0,.75); backdrop-filter: blur(3px);
    display: flex; align-items: center; justify-content: center;
  }
  .panel {
    background: #06090f; border: 1px solid #1a2838; border-radius: 6px;
    width: 720px; max-height: 82vh;
    display: flex; flex-direction: column;
    box-shadow: 0 20px 60px rgba(0,0,0,.9);
  }
  .hdr {
    display: flex; align-items: center; gap: 10px; padding: 12px 16px;
    border-bottom: 1px solid #0e1828; flex-shrink: 0;
  }
  .title {
    font-size: 10px; font-weight: 700; letter-spacing: 2px; color: #4a6080;
  }
  .subtitle {
    font-size: 11px; color: #3a5068;
  }
  .mode-toggle {
    margin-left: auto; padding: 3px 12px;
    background: #0a1018; border: 1px solid #1a2838; border-radius: 3px;
    color: #5a7090; font-size: 10px; cursor: pointer;
    transition: border-color .1s, color .1s, background .1s;
  }
  .mode-toggle:hover { border-color: #3a5878; color: #8aaac8; }
  .mode-toggle.active { background: #0e1a2c; border-color: #2a5888; color: #6aa0e0; }
  .remove-all-btn {
    padding: 3px 12px;
    background: #180808; border: 1px solid #3a1010; border-radius: 3px;
    color: #a04040; font-size: 10px; cursor: pointer;
    transition: border-color .1s, color .1s;
  }
  .remove-all-btn:hover { border-color: #c04040; color: #e06060; }

  .filter-bar {
    display: flex; align-items: center; gap: 8px;
    padding: 7px 16px; background: #07101a; border-bottom: 1px solid #0e1828;
    flex-shrink: 0;
  }
  .folder-filter {
    flex: 1; background: #060c18; border: 1px solid #1a2838;
    border-radius: 3px; color: #7a9ab8; font-size: 11px;
    padding: 4px 8px; outline: none;
  }
  .del-checked-btn {
    padding: 4px 12px; background: #180808; border: 1px solid #3a1010; border-radius: 3px;
    color: #c04040; font-size: 10px; cursor: pointer; white-space: nowrap;
    transition: border-color .1s, color .1s;
  }
  .del-checked-btn:hover { border-color: #e06060; color: #ff7070; }

  .folder-item-count {
    font-size: 10px; color: #3a5068; flex-shrink: 0;
  }
  .track-row.checkable { padding-left: 12px; }
  .track-check {
    flex-shrink: 0; width: 14px; height: 14px; cursor: pointer; accent-color: #3a6ab0;
  }
  .close-btn {
    background: none; border: none; color: #3a5070; font-size: 13px;
    cursor: pointer; padding: 4px 6px; border-radius: 3px;
    transition: color .1s, background .1s;
  }
  .close-btn:hover { color: #e06060; background: #1a0808; }

  /* Folder bulk-delete bar */
  .folder-bar {
    display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
    padding: 7px 16px; background: #07101a; border-bottom: 1px solid #0e1828;
    flex-shrink: 0;
  }
  .folder-bar-label {
    font-size: 9px; font-weight: 700; letter-spacing: .08em;
    color: #3a5068; text-transform: uppercase; flex-shrink: 0; margin-right: 2px;
  }
  .folder-del-btn {
    padding: 3px 10px; background: #100b08; border: 1px solid #3a2010;
    border-radius: 3px; color: #8a5030; font-size: 10px; cursor: pointer;
    transition: border-color .1s, color .1s; white-space: nowrap;
  }
  .folder-del-btn:hover { border-color: #c06020; color: #e08040; }
  .folder-count {
    font-size: 9px; color: #604020; margin-left: 2px;
  }

  .body { overflow-y: auto; padding: 8px 0; }

  .done {
    padding: 40px; text-align: center; font-size: 14px; color: #3a9a50;
  }

  .group {
    border-bottom: 1px solid #080d18; padding: 8px 0;
  }
  .group-hdr {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 16px 6px; min-width: 0;
  }
  .group-title {
    font-size: 12px; font-weight: 600; color: #8aaac8;
    flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .skip-btn {
    background: none; border: 1px solid #1a2838; border-radius: 3px;
    color: #3a5068; font-size: 10px; padding: 2px 8px; cursor: pointer;
    flex-shrink: 0; transition: border-color .1s, color .1s;
  }
  .skip-btn:hover { border-color: #3a5878; color: #5a8090; }

  .track-row {
    display: flex; align-items: center; gap: 8px;
    padding: 5px 16px; min-height: 38px;
    transition: background 0.06s;
  }
  .track-row:hover { background: #080e18; }
  .track-row.best  { border-left: 3px solid #2a6a30; }
  .track-row.worse { border-left: 3px solid #602020; }

  .track-badge {
    font-size: 9px; font-weight: 700; letter-spacing: .05em;
    width: 52px; flex-shrink: 0; text-align: center;
  }
  .track-row.best  .track-badge { color: #3a9a40; }
  .track-row.worse .track-badge { color: #8a3030; }

  .track-info { flex: 1; min-width: 0; }
  .track-title {
    font-size: 12px; color: #a0b8cc;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .track-meta {
    display: flex; align-items: center; gap: 6px; margin-top: 2px;
  }
  .meta-tag {
    font-size: 9px; padding: 1px 5px; border-radius: 2px;
    font-variant-numeric: tabular-nums;
  }
  .meta-tag.q { background: #0e1a10; color: #3a8a40; }
  .meta-tag.l { background: #0e1a28; color: #3a6080; }
  .meta-tag.d { background: #0e1020; color: #4a5870; }
  .meta-path {
    font-size: 10px; color: #2e4058;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px;
  }

  .track-actions { display: flex; gap: 6px; flex-shrink: 0; }
  .keep-btn {
    background: #061408; border: 1px solid #1a4020; border-radius: 3px;
    color: #3a8040; font-size: 10px; padding: 3px 10px; cursor: pointer;
    transition: border-color .1s, color .1s;
  }
  .keep-btn:hover { border-color: #3a9040; color: #5ab060; }
  .del-btn {
    background: #140606; border: 1px solid #3a1010; border-radius: 3px;
    color: #8a3030; font-size: 10px; padding: 3px 10px; cursor: pointer;
    transition: border-color .1s, color .1s;
  }
  .del-btn:hover { border-color: #9a3030; color: #c04040; }
</style>
