<script>
  import { qualityCandidates, qualityReplace, send, library } from '../stores/ws.js'

  // Bessere Version einsetzen, ohne dass sich Name oder Ordner aendern: das
  // Backend laedt die neue Version im Format der alten Datei, uebernimmt alle
  // Tags (Tonart, Kommentar, Cover …), legt die alte in den Papierkorb und die
  // neue unter exakt demselben Pfad ab. So finden rekordbox, Playlisten und
  // Warteschlange den Titel weiter.
  let { track, onclose } = $props()

  const CUTOFF_UPSCALED = 17.0
  const CUE_TOL = 2          // s — mehr Abweichung verschiebt Cues in rekordbox

  // Aktueller Stand aus der Bibliothek (nach dem Ersetzen neue Werte)
  const cur = $derived($library.find(t => t.path === track?.path) ?? track)
  const ext = $derived((track?.path?.split('.').pop() ?? '').toLowerCase())
  const folder = $derived(track?.path?.replace(/[\\/][^\\/]*$/, '') ?? '')

  let query = $state('')
  let picked = $state(null)
  let loading = $state(false)

  const cand = $derived($qualityCandidates?.path === track?.path ? $qualityCandidates : null)
  const results = $derived(cand?.results ?? [])
  const status = $derived($qualityReplace[track?.path] ?? null)
  const busy = $derived(status && (status.state === 'download' || status.state === 'tags'))

  function search(q) {
    loading = true
    picked = null
    qualityCandidates.set(null)
    send({ type: 'quality_candidates', path: track.path, query: q ?? query })
  }
  // Beim Oeffnen gleich mit dem bereinigten Titel suchen; das Backend meldet den Begriff zurueck
  $effect(() => {
    if (!track?.path) return
    qualityReplace.update(m => { const n = { ...m }; delete n[track.path]; return n })
    search('')
  })
  $effect(() => {
    if (cand) {
      if (!query) query = cand.query
      if (cand.final) loading = false
    }
  })

  function replace() {
    if (!picked) return
    send({ type: 'quality_replace', path: track.path, url: picked.url })
    qualityReplace.update(m => ({ ...m, [track.path]: { state: 'download', text: 'Lade die neue Version…' } }))
  }

  function fmt(sec) {
    if (!sec) return ''
    return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
  }
  function delta(r) {
    if (!r?.duration || !cur?.duration_sec) return null
    return Math.round(r.duration - cur.duration_sec)
  }
  const pickedDelta = $derived(delta(picked))

  function onkey(e) { if (e.key === 'Escape' && !busy) onclose() }
</script>

<svelte:window onkeydown={onkey} />

<div class="dlg-overlay" onclick={() => !busy && onclose()} role="presentation">
  <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Bessere Version suchen">

    <div class="hdr">
      <span class="dlg-title">Bessere Version suchen</span>
      <button class="btn btn-icon btn-sm close-btn" onclick={onclose} disabled={busy} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
    </div>

    <div class="current">
      <span class="eyebrow">Aktuell</span>
      <span class="cur-title" title={cur?.path}>{cur?.title}</span>
      <span class="cur-meta">
        {fmt(cur?.duration_sec)}
        {#if cur?.bitrate_kbps} · {cur.bitrate_kbps} kbps{/if}
        {#if cur?.cutoff_khz > 5}
          · <span class:bad={cur.cutoff_khz < CUTOFF_UPSCALED}
                  title="Obere Grenzfrequenz. Unter 17 kHz klingt eine Datei wie höchstens 128 kbps.">Höhen bis {cur.cutoff_khz} kHz</span>
        {/if}
      </span>
      <span class="cur-path" title={cur?.path}>{folder}</span>
    </div>

    <div class="search-row">
      <input class="field q-input" type="text" bind:value={query}
        placeholder="Suchbegriff…" aria-label="Suchbegriff"
        onkeydown={(e) => e.key === 'Enter' && search()} />
      <button class="btn" onclick={() => search()} disabled={loading || busy}>
        <i class="ti ti-search"></i> Suchen
      </button>
    </div>

    <div class="results" role="radiogroup" aria-label="Kandidaten">
      {#if !cand}
        <div class="hint"><i class="ti ti-refresh spin"></i> Suche auf YouTube Music und YouTube…</div>
      {:else if !results.length}
        <div class="hint">Nichts gefunden. Suchbegriff anpassen, z. B. nur „Künstler Titel“.</div>
      {:else}
        {#each results as r}
          {@const d = delta(r)}
          <button class="result-row" class:sel={picked?.url === r.url} role="radio" aria-checked={picked?.url === r.url}
                  disabled={busy} onclick={() => picked = r}>
            <i class="ti {picked?.url === r.url ? 'ti-check' : 'ti-minus'} r-radio" aria-hidden="true"></i>
            <span class="r-info">
              <span class="r-title" title={r.title}>
                {#if r.kind === 'song'}<span class="song-chip" title="Studio-Version von YouTube Music">Song</span>{/if}{r.title}
              </span>
              <span class="r-meta">{r.uploader || (r.kind === 'song' && !cand.final ? 'Künstler wird geladen…' : '')}</span>
            </span>
            <span class="r-right">
              {#if r.abr}<span class="r-abr">{Math.round(r.abr)} kbps</span>{/if}
              <span class="r-dur">{fmt(r.duration)}</span>
              {#if d !== null}
                <span class="r-delta" class:ok={Math.abs(d) <= CUE_TOL} title="Längenunterschied zur jetzigen Datei">
                  {d === 0 ? '±0 s' : (d > 0 ? '+' : '−') + Math.abs(d) + ' s'}
                </span>
              {/if}
            </span>
          </button>
        {/each}
        {#if !cand.final}<div class="hint small"><i class="ti ti-refresh spin"></i> Länge und Künstler werden geladen…</div>{/if}
      {/if}
    </div>

    <div class="foot">
      {#if status?.state === 'done'}
        <div class="notice ok"><i class="ti ti-check"></i> {status.text}</div>
      {:else if status?.state === 'error'}
        <div class="notice error"><i class="ti ti-alert-triangle"></i> {status.text}</div>
      {:else if busy}
        <div class="notice info"><i class="ti ti-refresh spin"></i> {status.text}</div>
      {:else if picked && pickedDelta !== null && Math.abs(pickedDelta) > CUE_TOL}
        <div class="notice warn"><i class="ti ti-alert-triangle"></i>
          Die neue Version ist {Math.abs(pickedDelta)} s {pickedDelta > 0 ? 'länger' : 'kürzer'}. Cues und Beatgrid in rekordbox danach neu setzen (Titel neu analysieren).</div>
      {:else}
        <div class="dlg-hint">Gleicher Name und Ordner, gleiches Format (.{ext}). Tonart, Kommentar, Cover und alle anderen Tags bleiben. Die alte Datei kommt in den Papierkorb.</div>
      {/if}
      <div class="dlg-actions">
        {#if status?.state === 'done'}
          <button class="btn btn-primary" onclick={onclose}>Fertig</button>
        {:else}
          <button class="btn" onclick={onclose} disabled={busy}>Abbrechen</button>
          <button class="btn btn-primary" onclick={replace} disabled={!picked || busy}>
            <i class="ti ti-refresh"></i> Ersetzen
          </button>
        {/if}
      </div>
    </div>

  </div>
</div>

<style>
  .panel { width: 640px; max-height: 84vh; padding: 0; gap: 0; }
  .hdr { display: flex; align-items: center; padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); flex-shrink: 0; }
  .close-btn { margin-left: auto; }
  .current { display: grid; grid-template-columns: auto 1fr; gap: 2px var(--sp-3); align-items: baseline; padding: var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); flex-shrink: 0; }
  .cur-title { font-size: var(--fs-body); font-weight: 600; color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cur-meta, .cur-path { grid-column: 2; font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; }
  .cur-path { color: var(--c-tx4); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bad { color: var(--c-red-tx); font-weight: 600; }
  .search-row { display: flex; gap: var(--sp-2); padding: var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); flex-shrink: 0; }
  .q-input { flex: 1; }
  .results { flex: 1; overflow-y: auto; min-height: 160px; }
  .hint { display: flex; align-items: center; justify-content: center; gap: var(--sp-2); padding: 32px var(--sp-5); text-align: center; font-size: var(--fs-body); color: var(--c-tx3); }
  .hint.small { padding: var(--sp-2); font-size: var(--fs-sm); }
  .spin { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .result-row {
    display: flex; align-items: center; gap: var(--sp-3); width: 100%; text-align: left;
    padding: var(--sp-2) var(--sp-5); border: none; border-bottom: 1px solid var(--c-br1);
    background: none; cursor: pointer; font-family: inherit;
  }
  .result-row:hover { background: var(--c-hover); }
  .result-row.sel { background: var(--c-act-bg); box-shadow: inset 3px 0 0 var(--c-accent); }
  .r-radio { width: 16px; font-size: 14px; color: var(--c-tx5); flex-shrink: 0; }
  .result-row.sel .r-radio { color: var(--c-accent-tx); }
  .r-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .r-title { font-size: var(--fs-body); color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .r-meta { font-size: var(--fs-sm); color: var(--c-tx4); }
  .r-right { display: flex; align-items: center; gap: var(--sp-3); flex-shrink: 0; font-variant-numeric: tabular-nums; }
  .r-abr { font-size: var(--fs-sm); color: var(--c-tx3); }
  .r-dur { font-size: var(--fs-sm); color: var(--c-tx2); min-width: 36px; text-align: right; }
  .r-delta { font-size: var(--fs-sm); font-weight: 600; min-width: 48px; text-align: right; color: var(--c-warn-tx); }
  .r-delta.ok { color: var(--c-green-tx); }
  .song-chip {
    display: inline-block; margin-right: 6px; padding: 0 5px; border-radius: var(--r-s); vertical-align: 1px;
    font-size: var(--fs-cap); font-weight: 700; line-height: 16px;
    color: var(--c-green-tx); background: var(--c-green-bg); border: 1px solid var(--c-green-br);
  }
  .foot { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-3) var(--sp-5) var(--sp-4); border-top: 1px solid var(--c-br1); flex-shrink: 0; }
  .foot .dlg-actions { margin: 0; }
  .notice.warn { border-color: var(--c-warn-br); background: var(--c-warn-bg); color: var(--c-warn-tx); }
</style>
