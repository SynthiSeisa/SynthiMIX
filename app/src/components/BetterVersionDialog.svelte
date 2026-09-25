<script>
  import { searchResults, send } from '../stores/ws.js'

  let { track, onclose } = $props()

  let query        = $state('')
  $effect.pre(() => { if (track?.title) query = track.title })
  let searching    = $state(false)
  let pickedUrl    = $state(null)
  let removeOld    = $state(true)
  let fmt          = $state('mp3-best')
  const formats    = ['mp3-best', 'flac', 'wav', 'm4a', 'opus']

  function doSearch() {
    if (!query.trim()) return
    searching = true
    searchResults.set(null)
    send({ type: 'search', query: query.trim() })
  }

  // Clear searching flag when results arrive
  $effect(() => {
    if ($searchResults) searching = false
  })

  function pick(result) {
    pickedUrl = result.url
    send({ type: 'download_add', url: result.url, format: fmt })
    if (removeOld && track?.path)
      send({ type: 'library_remove', path: track.path })
    onclose()
  }

  function fmt_dur(sec) {
    if (!sec) return ''
    return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
  }
</script>

<div class="dlg-overlay" onclick={onclose} role="presentation">
  <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Bessere Version suchen">

    <div class="hdr">
      <span class="dlg-title">Bessere Version suchen</span>
      <button class="btn btn-icon btn-sm close-btn" onclick={onclose} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
    </div>

    <div class="current">
      <span class="eyebrow">Aktuell</span>
      <span class="cur-title" title={track?.title}>{track?.title}</span>
      {#if track?.bitrate_kbps}
        <span class="cur-meta">{track.bitrate_kbps} kbps</span>
      {/if}
    </div>

    <div class="search-row">
      <input class="field q-input" type="text" bind:value={query}
        placeholder="Suchbegriff…" aria-label="Suchbegriff"
        onkeydown={(e) => e.key === 'Enter' && doSearch()} />
      <button class="btn btn-primary" onclick={doSearch} disabled={searching}>
        <i class="ti ti-search"></i> {searching ? 'Sucht…' : 'Suchen'}
      </button>
    </div>

    <div class="opts-row">
      <label class="opt-label">
        Format
        <select bind:value={fmt} class="field field-sm fmt-sel">
          {#each formats as f}
            <option value={f}>{f}</option>
          {/each}
        </select>
      </label>
      <label class="dlg-check">
        <input type="checkbox" bind:checked={removeOld} />
        Alte Version aus der Bibliothek entfernen
      </label>
    </div>

    <div class="results">
      {#if !$searchResults && !searching}
        <div class="hint">Suche starten, um Ergebnisse zu laden</div>
      {:else if searching}
        <div class="hint">Suche läuft…</div>
      {:else if !$searchResults?.results?.length}
        <div class="hint">Keine Ergebnisse</div>
      {:else}
        {#each $searchResults.results as r}
          <div class="result-row" onclick={() => pick(r)} role="button" tabindex="0"
               onkeydown={(e) => e.key === 'Enter' && pick(r)}>
            <div class="r-info">
              <span class="r-title" title={r.title}>{r.title}</span>
              <span class="r-meta">{r.uploader}</span>
            </div>
            <div class="r-right">
              {#if r.abr}<span class="r-abr">{Math.round(r.abr)} kbps</span>{/if}
              <span class="r-dur">{fmt_dur(r.duration)}</span>
              <span class="btn btn-sm dl-btn"><i class="ti ti-download"></i> Laden</span>
            </div>
          </div>
        {/each}
      {/if}
    </div>

  </div>
</div>

<style>
  .panel { width: 620px; max-height: 80vh; padding: 0; gap: 0; }
  .hdr { display: flex; align-items: center; padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); flex-shrink: 0; }
  .close-btn { margin-left: auto; }
  .current { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); flex-shrink: 0; }
  .cur-title { flex: 1; min-width: 0; font-size: var(--fs-body); font-weight: 600; color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cur-meta { flex-shrink: 0; font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; }
  .search-row { display: flex; gap: var(--sp-2); padding: var(--sp-3) var(--sp-5) var(--sp-2); flex-shrink: 0; }
  .q-input { flex: 1; }
  .opts-row { display: flex; align-items: center; gap: var(--sp-5); flex-wrap: wrap; padding: var(--sp-2) var(--sp-5) var(--sp-3); border-bottom: 1px solid var(--c-br1); flex-shrink: 0; }
  .opt-label { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-body); color: var(--c-tx2); }
  .fmt-sel { width: auto; }
  .results { flex: 1; overflow-y: auto; }
  .hint { padding: 32px var(--sp-5); text-align: center; font-size: var(--fs-body); color: var(--c-tx3); }
  .result-row { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-2) var(--sp-5); border-bottom: 1px solid var(--c-br1); cursor: pointer; }
  .result-row:hover { background: var(--c-hover); }
  .result-row:hover .dl-btn, .result-row:focus-visible .dl-btn { opacity: 1; }
  .r-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .r-title { font-size: var(--fs-body); color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .r-meta { font-size: var(--fs-sm); color: var(--c-tx4); }
  .r-right { display: flex; align-items: center; gap: var(--sp-3); flex-shrink: 0; }
  .r-abr { font-size: var(--fs-sm); font-weight: 600; color: var(--c-green-tx); font-variant-numeric: tabular-nums; }
  .r-dur { font-size: var(--fs-sm); color: var(--c-tx3); min-width: 36px; text-align: right; font-variant-numeric: tabular-nums; }
  .dl-btn { opacity: 0; transition: opacity .1s; }
</style>
