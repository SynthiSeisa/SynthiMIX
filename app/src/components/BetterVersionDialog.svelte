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
    background: #080e1a; border: 1px solid #1a2838; border-radius: 6px;
    width: 580px; max-height: 78vh; display: flex; flex-direction: column;
    box-shadow: 0 20px 60px rgba(0,0,0,.9);
  }
  .hdr {
    display: flex; align-items: center; padding: 11px 16px;
    border-bottom: 1px solid #0e1828; flex-shrink: 0;
  }
  .title {
    font-size: 10px; font-weight: 700; letter-spacing: 1.8px; color: #4a6080;
  }
  .close-btn {
    margin-left: auto; background: none; border: none; color: #3a5070;
    font-size: 12px; cursor: pointer; padding: 4px 8px; border-radius: 3px;
  }
  .close-btn:hover { color: #e06060; }

  .current {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 16px; border-bottom: 1px solid #080d18; flex-shrink: 0;
  }
  .cur-label { font-size: 10px; color: #3a5068; flex-shrink: 0; }
  .cur-title { font-size: 12px; color: #7a9ab8; flex: 1; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; }
  .cur-meta  { font-size: 10px; color: #3a5068; flex-shrink: 0; }

  .search-row {
    display: flex; gap: 8px; padding: 10px 16px; flex-shrink: 0;
    border-bottom: 1px solid #080d18;
  }
  .q-input {
    flex: 1; background: #0c1220; border: 1px solid #1a2838; border-radius: 3px;
    color: #c8d8f0; font-size: 12px; padding: 5px 10px; outline: none;
  }
  .q-input:focus { border-color: #e07800; }
  .search-btn {
    background: #e07800; border: none; border-radius: 3px;
    color: #fff; font-size: 11px; padding: 5px 14px; cursor: pointer;
    transition: background .1s;
  }
  .search-btn:hover:not(:disabled) { background: #f08a00; }
  .search-btn:disabled { background: #3a3820; color: #6a6040; cursor: default; }

  .opts-row {
    display: flex; align-items: center; gap: 20px;
    padding: 6px 16px 8px; border-bottom: 1px solid #080d18; flex-shrink: 0;
  }
  .opt-label { display: flex; align-items: center; gap: 6px;
               font-size: 11px; color: #5a7090; cursor: pointer; }
  .opt-label.chk input { accent-color: #e07800; }
  .fmt-sel {
    background: #0c1220; border: 1px solid #1a2838; border-radius: 3px;
    color: #8aaac8; font-size: 11px; padding: 2px 6px;
  }

  .results { flex: 1; overflow-y: auto; padding: 4px 0; }

  .hint { padding: 30px 20px; text-align: center; font-size: 12px; color: #3a5068; }

  .result-row {
    display: flex; align-items: center; gap: 10px;
    padding: 7px 16px; border-bottom: 1px solid #060b12;
    cursor: pointer; transition: background .08s;
  }
  .result-row:hover { background: #0c1628; }
  .result-row:hover .dl-btn { opacity: 1; }

  .r-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .r-title { font-size: 12px; color: #9ab8d8; overflow: hidden;
             text-overflow: ellipsis; white-space: nowrap; }
  .r-meta  { font-size: 10px; color: #4a6080; }

  .r-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .r-abr   { font-size: 10px; color: #5a8060; }
  .r-dur   { font-size: 10px; color: #4a6080; min-width: 36px; text-align: right; }

  .dl-btn {
    background: #0c1a0c; border: 1px solid #2a5a2a; border-radius: 3px;
    color: #4a8a4a; font-size: 10px; padding: 3px 9px; cursor: pointer;
    opacity: 0; transition: opacity .1s, border-color .1s, color .1s;
  }
  .dl-btn:hover { border-color: #4aaa4a; color: #6acc6a; }
</style>
