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

<div class="overlay" onclick={onclose} role="dialog">
  <div class="panel" onclick={(e) => e.stopPropagation()}>

    <div class="hdr">
      <span class="title">BESSERE VERSION SUCHEN</span>
      <button class="close-btn" onclick={onclose}>✕</button>
    </div>

    <div class="current">
      <span class="cur-label">Aktuell:</span>
      <span class="cur-title" title={track?.title}>{track?.title}</span>
      {#if track?.bitrate_kbps}
        <span class="cur-meta">{track.bitrate_kbps} kbps</span>
      {/if}
    </div>

    <div class="search-row">
      <input class="q-input" type="text" bind:value={query}
        placeholder="Suchbegriff…"
        onkeydown={(e) => e.key === 'Enter' && doSearch()} />
      <button class="search-btn" onclick={doSearch} disabled={searching}>
        {searching ? '…' : '⌕ Suchen'}
      </button>
    </div>

    <div class="opts-row">
      <label class="opt-label">
        Format:
        <select bind:value={fmt} class="fmt-sel">
          {#each formats as f}
            <option value={f}>{f}</option>
          {/each}
        </select>
      </label>
      <label class="opt-label chk">
        <input type="checkbox" bind:checked={removeOld} />
        Alte Version aus Bibliothek entfernen
      </label>
    </div>

    <div class="results">
      {#if !$searchResults && !searching}
        <div class="hint">Suche starten um Ergebnisse zu laden</div>
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
              {#if r.abr}<span class="r-abr">{Math.round(r.abr)}k</span>{/if}
              <span class="r-dur">{fmt_dur(r.duration)}</span>
              <button class="dl-btn">⬇ Laden</button>
            </div>
          </div>
        {/each}
      {/if}
    </div>

  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 2000;
    background: rgba(0,0,0,.75); backdrop-filter: blur(3px);
    display: flex; align-items: center; justify-content: center;
  }
  .panel {
    background: var(--c-bg3); border: 1px solid var(--c-br2); border-radius: 6px;
    width: 580px; max-height: 78vh; display: flex; flex-direction: column;
    box-shadow: 0 20px 60px rgba(0,0,0,.9);
  }
  .hdr {
    display: flex; align-items: center; padding: 11px 16px;
    border-bottom: 1px solid var(--c-br1); flex-shrink: 0;
  }
  .title {
    font-size: 10px; font-weight: 700; letter-spacing: 1.8px; color: var(--c-tx6);
  }
  .close-btn {
    margin-left: auto; background: none; border: none; color: var(--c-tx7);
    font-size: 12px; cursor: pointer; padding: 4px 8px; border-radius: 3px;
  }
  .close-btn:hover { color: var(--c-red-tx); }

  .current {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 16px; border-bottom: 1px solid var(--c-br1); flex-shrink: 0;
  }
  .cur-label { font-size: 10px; color: var(--c-tx7); flex-shrink: 0; }
  .cur-title { font-size: 12px; color: var(--c-tx4); flex: 1; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; }
  .cur-meta  { font-size: 10px; color: var(--c-tx7); flex-shrink: 0; }

  .search-row {
    display: flex; gap: 8px; padding: 10px 16px; flex-shrink: 0;
    border-bottom: 1px solid var(--c-br1);
  }
  .q-input {
    flex: 1; background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: 3px;
    color: var(--c-tx2); font-size: 12px; padding: 5px 10px; outline: none;
  }
  .q-input:focus { border-color: var(--c-accent); }
  .search-btn {
    background: var(--c-accent); border: none; border-radius: 3px;
    color: #fff; font-size: 11px; padding: 5px 14px; cursor: pointer;
    transition: background .1s;
  }
  .search-btn:hover:not(:disabled) { background: var(--c-accent2); }
  .search-btn:disabled { background: var(--c-br1); color: var(--c-tx7); cursor: default; }

  .opts-row {
    display: flex; align-items: center; gap: 20px;
    padding: 6px 16px 8px; border-bottom: 1px solid var(--c-br1); flex-shrink: 0;
  }
  .opt-label { display: flex; align-items: center; gap: 6px;
               font-size: 11px; color: var(--c-tx5); cursor: pointer; }
  .opt-label.chk input { accent-color: var(--c-accent); }
  .fmt-sel {
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: 3px;
    color: var(--c-tx4); font-size: 11px; padding: 2px 6px;
  }

  .results { flex: 1; overflow-y: auto; padding: 4px 0; }

  .hint { padding: 30px 20px; text-align: center; font-size: 12px; color: var(--c-tx7); }

  .result-row {
    display: flex; align-items: center; gap: 10px;
    padding: 7px 16px; border-bottom: 1px solid var(--c-br1);
    cursor: pointer; transition: background .08s;
  }
  .result-row:hover { background: var(--c-hover); }
  .result-row:hover .dl-btn { opacity: 1; }

  .r-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .r-title { font-size: 12px; color: var(--c-tx3); overflow: hidden;
             text-overflow: ellipsis; white-space: nowrap; }
  .r-meta  { font-size: 10px; color: var(--c-tx6); }

  .r-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .r-abr   { font-size: 10px; color: var(--c-green-tx); }
  .r-dur   { font-size: 10px; color: var(--c-tx6); min-width: 36px; text-align: right; }

  .dl-btn {
    background: var(--c-green-bg); border: 1px solid var(--c-green-br); border-radius: 3px;
    color: var(--c-green-tx); font-size: 10px; padding: 3px 9px; cursor: pointer;
    opacity: 0; transition: opacity .1s, border-color .1s, color .1s;
  }
  .dl-btn:hover { border-color: var(--c-green-tx); color: var(--c-green-tx); }
</style>
