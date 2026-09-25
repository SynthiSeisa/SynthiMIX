<script>
  let { groups = [], onclose, onremove, playlistMode = false } = $props()

  // Was der Nutzer hier entschieden hat, liegt in zwei Mengen neben den
  // Gruppen. Vorher war es eine Kopie, die bei jeder Aenderung der Bibliothek
  // (etwa waehrend der Hintergrundanalyse) neu gefuellt wurde — uebersprungene
  // Gruppen tauchten dann wieder auf.
  let removed = $state(new Set())   // entfernte Pfade (Backend kommt gleich nach)
  let skipped = $state(new Set())   // Pfade uebersprungener Gruppen
  const remaining = $derived(groups
    .map(g => {
      const all = [g.best, ...g.others].filter(t => !removed.has(t.path))
      return all.length >= 2 ? { best: all[0], others: all.slice(1) } : null
    })
    .filter(g => g && ![g.best, ...g.others].some(t => skipped.has(t.path))))

  function markRemoved(paths) { removed = new Set([...removed, ...paths]) }

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
    const msg = playlistMode
      ? `${checked.size} ausgewählte Kopien aus der Playlist entfernen?`
      : `${checked.size} ausgewählte Kopien in den Papierkorb verschieben?`
    if (!confirm(msg)) return
    for (const p of checked) onremove(p)
    markRemoved(checked)
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
    const weg = allInGroup.filter(t => t.path !== trackToKeep.path).map(t => t.path)
    weg.forEach(p => onremove(p))
    markRemoved(weg)
  }

  function removeTrack(group, track) {
    onremove(track.path)
    markRemoved([track.path])
  }

  function skipGroup(group) {
    skipped = new Set([...skipped, group.best.path, ...group.others.map(t => t.path)])
  }

  function deleteFolderDupes(folderEntry) {
    const msg = playlistMode
      ? `${folderEntry.tracks.length} Kopien aus „${folderEntry.name}" aus der Playlist entfernen?`
      : `${folderEntry.tracks.length} Kopien aus „${folderEntry.name}" in den Papierkorb verschieben?`
    if (!confirm(msg)) return
    folderEntry.tracks.forEach(t => onremove(t.path))
    markRemoved(folderEntry.tracks.map(t => t.path))
  }

  function removeAllLower() {
    // Nur was noch angezeigt wird — uebersprungene Gruppen bleiben unberuehrt
    const count = remaining.reduce((n, g) => n + g.others.length, 0)
    const msg = playlistMode
      ? `${count} niedrigwertige Kopien aus der Playlist entfernen?`
      : `${count} niedrigwertige Kopien in den Papierkorb verschieben?`
    if (!confirm(msg)) return
    for (const g of remaining) g.others.forEach(t => onremove(t.path))
    onclose()
  }
</script>

<div class="dlg-overlay" onclick={onclose} role="presentation">
  <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Duplikate prüfen">

    <div class="hdr">
      <span class="dlg-title">Duplikate prüfen</span>
      <span class="subtitle">{remaining.length} Gruppen</span>
      <span class="hdr-space"></span>
      <button class="btn btn-sm" class:is-active={groupByFolder}
              onclick={() => groupByFolder = !groupByFolder}
              title="Nach Ordner gruppieren statt nach Song"><i class="ti ti-folder"></i> Nach Ordner</button>
      <button class="btn btn-sm btn-danger" onclick={removeAllLower}>
        {#if playlistMode}Alle Kopien aus der Playlist{:else}<i class="ti ti-trash"></i> Alle Kopien in den Papierkorb{/if}
      </button>
      <button class="btn btn-icon btn-sm" onclick={onclose} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
    </div>

    {#if folderGroups.length > 0 && !playlistMode}
      <div class="folder-bar">
        <span class="folder-bar-label">Ordner, die nur Kopien enthalten:</span>
        {#each folderGroups as f}
          <button class="btn btn-sm" onclick={() => deleteFolderDupes(f)}
                  title="Alle {f.tracks.length} Kopien aus '{f.dir}' in den Papierkorb verschieben">
            <i class="ti ti-folder"></i> {f.name} <span class="folder-count">×{f.tracks.length}</span>
          </button>
        {/each}
      </div>
    {/if}

    {#if groupByFolder}
      <div class="filter-bar">
        <input class="field field-sm folder-filter" placeholder="Ordner filtern…" bind:value={folderFilter} aria-label="Ordner filtern" />
        {#if checkedCount > 0}
          <button class="btn btn-sm btn-danger" onclick={deleteChecked}>
            <i class="ti ti-trash"></i> {checkedCount} markierte entfernen
          </button>
        {/if}
      </div>
    {/if}

    <div class="body">
      {#if remaining.length === 0}
        <div class="done"><i class="ti ti-check"></i> Alle Duplikate bereinigt</div>
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
              <div class="track-row worse">
                <input type="checkbox" class="track-check" aria-label="Markieren"
                       checked={checked.has(track.path)}
                       onchange={() => toggleCheck(track.path)} />
                <span class="track-badge">Kopie</span>
                <div class="track-info">
                  <div class="track-title">{track.title}</div>
                  <div class="track-meta">
                    <span class="meta-tag">{track.bitrate_kbps || '?'} kbps</span>
                    {#if track.lufs && track.lufs > -90}
                      <span class="meta-tag">{track.lufs?.toFixed(1)} LUFS</span>
                    {/if}
                    <span class="meta-tag">{fmt(track.duration_sec)}</span>
                    <span class="meta-path" title={track.path}>{trackDir(track)}</span>
                  </div>
                </div>
                <div class="track-actions">
                  <button class="btn btn-sm" onclick={() => keepOnly(group, track)}
                          title="Diese behalten, alle anderen in der Gruppe entfernen">Behalten</button>
                  <button class="btn btn-sm btn-danger" onclick={() => removeTrack(group, track)}
                          title={playlistMode ? 'Aus Playlist entfernen' : 'Diese Datei in den Papierkorb verschieben'}>
                    {#if playlistMode}Aus Playlist{:else}<i class="ti ti-trash"></i> Papierkorb{/if}
                  </button>
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
              <button class="btn btn-ghost btn-sm" onclick={() => skipGroup(group)}>Überspringen</button>
            </div>

            {#each allTracks as track, i}
              <div class="track-row {i === 0 ? 'best' : 'worse'}">
                <span class="track-badge">{#if i === 0}<i class="ti ti-star"></i> Beste{:else}Kopie{/if}</span>
                <div class="track-info">
                  <div class="track-title">{track.title}</div>
                  <div class="track-meta">
                    <span class="meta-tag">{track.bitrate_kbps || '?'} kbps</span>
                    {#if track.lufs && track.lufs > -90}
                      <span class="meta-tag">{track.lufs?.toFixed(1)} LUFS</span>
                    {/if}
                    <span class="meta-tag">{fmt(track.duration_sec)}</span>
                    <span class="meta-path" title={track.path}>{trackDir(track)}</span>
                  </div>
                </div>
                <div class="track-actions">
                  <button class="btn btn-sm" onclick={() => keepOnly(group, track)}
                          title="Diese behalten, alle anderen in der Gruppe entfernen">Behalten</button>
                  <button class="btn btn-sm btn-danger" onclick={() => removeTrack(group, track)}
                          title={playlistMode ? 'Aus Playlist entfernen' : 'Diese Datei in den Papierkorb verschieben'}>
                    {#if playlistMode}Aus Playlist{:else}<i class="ti ti-trash"></i> Papierkorb{/if}
                  </button>
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
  .panel { width: 780px; max-width: calc(100vw - 32px); max-height: 84vh; padding: 0; gap: 0; }
  .hdr { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); flex-shrink: 0; }
  .subtitle { font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; }
  .hdr-space { flex: 1; }
  .folder-bar, .filter-bar {
    display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; flex-shrink: 0;
    padding: var(--sp-2) var(--sp-5); border-bottom: 1px solid var(--c-br1); background: var(--c-bg2);
  }
  .folder-bar-label { font-size: var(--fs-sm); color: var(--c-tx3); }
  .folder-count { color: var(--c-tx4); font-weight: 400; }
  .folder-filter { width: 240px; }
  .body { flex: 1; overflow-y: auto; }
  .done { display: flex; align-items: center; justify-content: center; gap: var(--sp-2); padding: 40px; font-size: var(--fs-lg); font-weight: 600; color: var(--c-green-tx); }
  .group { border-bottom: 1px solid var(--c-br2); }
  .group-hdr { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-3) var(--sp-3) var(--sp-1) var(--sp-5); }
  .group-title { flex: 1; min-width: 0; font-size: var(--fs-lg); font-weight: 700; color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .folder-item-count { font-size: var(--fs-sm); color: var(--c-tx3); }
  .track-row { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-2) var(--sp-3) var(--sp-2) var(--sp-5); }
  .track-row:hover { background: var(--c-hover); }
  .track-row.best { background: var(--c-green-bg); }
  .track-check { flex-shrink: 0; }
  .track-badge {
    flex-shrink: 0; min-width: 64px; display: inline-flex; align-items: center; justify-content: center; gap: 4px;
    font-size: var(--fs-cap); font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
    padding: 3px 6px; border-radius: var(--r-s);
  }
  .best .track-badge { color: var(--c-green-tx); border: 1px solid var(--c-green-br); }
  .worse .track-badge { color: var(--c-tx3); border: 1px solid var(--c-br3); }
  .track-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
  .track-title { font-size: var(--fs-body); color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .track-meta { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; font-size: var(--fs-sm); }
  .meta-tag { color: var(--c-tx2); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .meta-path { color: var(--c-tx4); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .track-actions { display: flex; gap: var(--sp-1); flex-shrink: 0; }
</style>
