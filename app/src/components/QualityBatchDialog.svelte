<script>
  import { untrack } from 'svelte'
  import { qualityBatch, qualityReplace, send } from '../stores/ws.js'

  // Viele Titel auf einmal ersetzen: Das Backend sucht je Titel automatisch
  // die beste Version (Song-Version zuerst, gleiche Fassung, kleinste
  // Laengenabweichung). Sichere Treffer sind angehakt, unsichere (kein
  // Kuenstler bekannt) nicht. Dann nacheinander ersetzen — wie beim
  // Einzel-Ersetzen: gleicher Name und Pfad, Tags bleiben, alte Datei in den
  // Papierkorb.
  let { paths, onclose } = $props()

  const CUE_TOL = 2
  const st = $derived($qualityBatch)
  const items = $derived(paths.map(p => st.items?.[p]).filter(Boolean))
  const found = $derived(items.filter(it => it.candidate))
  let pick = $state({})         // Pfad -> angehakt?

  $effect(() => {
    untrack(() => {
      qualityBatch.set({ phase: 'search', done: 0, total: paths.length, items: {} })
      qualityReplace.update(m => { const n = { ...m }; for (const p of paths) delete n[p]; return n })
      send({ type: 'quality_batch', paths })
    })
  })
  // Neue Treffer: sichere vorauswaehlen, eigene Haken behalten
  $effect(() => {
    const list = found
    untrack(() => {
      const p = { ...pick }
      for (const it of list) if (!(it.path in p)) p[it.path] = !!it.sure
      pick = p
    })
  })

  const chosen = $derived(found.filter(it => pick[it.path]))
  const running = $derived(st.phase === 'search' || st.phase === 'replace')
  const nLonger = $derived(chosen.filter(it => Math.abs(delta(it) ?? 0) > CUE_TOL).length)

  function delta(it) {
    const a = it.candidate?.duration, b = it.duration
    return a && b ? Math.round(a - b) : null
  }
  function fmt(sec) {
    if (!sec) return ''
    return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
  }
  function replaceAll() {
    qualityBatch.update(s => ({ ...s, phase: 'replace', done: 0, total: chosen.length }))
    send({ type: 'quality_batch_replace', items: chosen.map(it => ({ path: it.path, url: it.candidate.url })) })
  }
  function setAll(on) {
    const p = { ...pick }
    for (const it of found) p[it.path] = on
    pick = p
  }
  function onkey(e) { if (e.key === 'Escape' && st.phase !== 'replace') onclose() }
</script>

<svelte:window onkeydown={onkey} />

<div class="dlg-overlay" onclick={() => st.phase !== 'replace' && onclose()} role="presentation">
  <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Bessere Versionen suchen">

    <div class="hdr">
      <span class="dlg-title">Bessere Versionen für {paths.length} Titel</span>
      <button class="btn btn-icon btn-sm close-btn" onclick={onclose} disabled={st.phase === 'replace'} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
    </div>

    <div class="status">
      {#if st.phase === 'search'}
        <i class="ti ti-refresh spin"></i> Suche {st.done ?? 0} / {st.total} …
        <progress max={st.total} value={st.done ?? 0}></progress>
        <button class="btn btn-ghost btn-sm" onclick={() => send({ type: 'quality_batch_cancel' })}>Suche abbrechen</button>
      {:else if st.phase === 'replace'}
        <i class="ti ti-refresh spin"></i> Ersetze {(st.done ?? 0) + 1} / {st.total}: <span class="cur">{st.current}</span>
        <progress max={st.total} value={st.done ?? 0}></progress>
        <button class="btn btn-sm" onclick={() => send({ type: 'quality_batch_cancel' })}><i class="ti ti-player-stop"></i> Abbrechen</button>
      {:else if st.phase === 'finished'}
        <span class="ok"><i class="ti ti-check"></i> {st.result?.ok ?? 0} ersetzt{st.result?.cancelled ? ', dann abgebrochen' : ''}.</span>
        {#if st.result?.failed_count}<span class="bad">{st.result.failed_count} fehlgeschlagen.</span>{/if}
      {:else}
        {found.length} von {items.length} mit Vorschlag · {chosen.length} ausgewählt
        <span class="spacer"></span>
        <button class="btn btn-ghost btn-sm" onclick={() => setAll(true)}>Alle</button>
        <button class="btn btn-ghost btn-sm" onclick={() => setAll(false)}>Keine</button>
      {/if}
    </div>

    <div class="list">
      {#each items as it (it.path)}
        {@const d = delta(it)}
        {@const rs = $qualityReplace[it.path]}
        <div class="it" class:none={!it.candidate}>
          {#if it.candidate}
            <input type="checkbox" checked={!!pick[it.path]} disabled={running || st.phase === 'finished'}
                   onchange={(e) => pick = { ...pick, [it.path]: e.currentTarget.checked }} aria-label="Ersetzen: {it.title}" />
          {:else}
            <span class="cb-ph"></span>
          {/if}
          <span class="old" title={it.path}>{it.title}<span class="dur">{fmt(it.duration)}</span></span>
          <i class="ti ti-chevron-right arrow" aria-hidden="true"></i>
          {#if it.candidate}
            <span class="new" title={it.candidate.url}>
              {#if it.candidate.kind === 'song'}<span class="song-chip">Song</span>{/if}
              <span class="n-title">{it.candidate.title}</span>
              <span class="n-meta">{it.candidate.uploader}{it.candidate.uploader ? ' · ' : ''}{fmt(it.candidate.duration)}</span>
              {#if d !== null}<span class="delta" class:ok={Math.abs(d) <= CUE_TOL}>{d === 0 ? '±0 s' : (d > 0 ? '+' : '−') + Math.abs(d) + ' s'}</span>{/if}
              {#if !it.sure}<span class="unsure" title="Kein Künstler bekannt — gleichnamige Songs möglich, bitte prüfen">unsicher</span>{/if}
            </span>
          {:else}
            <span class="new muted">nichts Passendes gefunden</span>
          {/if}
          <span class="st">
            {#if rs?.state === 'done'}<i class="ti ti-check ok" title={rs.text}></i>
            {:else if rs?.state === 'error'}<i class="ti ti-alert-triangle bad" title={rs.text}></i>
            {:else if rs}<i class="ti ti-refresh spin" title={rs.text}></i>{/if}
          </span>
        </div>
      {/each}
      {#if st.phase === 'search' && items.length < paths.length}
        <div class="it muted"><span class="cb-ph"></span>… noch {paths.length - items.length} Titel</div>
      {/if}
    </div>

    <div class="foot">
      {#if st.phase === 'finished' && st.result?.failed?.length}
        <div class="notice error">
          {#each st.result.failed.slice(0, 4) as f}<div>{f.title}: {f.text}</div>{/each}
        </div>
      {:else if nLonger && st.phase === 'review'}
        <div class="notice"><i class="ti ti-alert-triangle"></i>
          {nLonger} der ausgewählten Titel {nLonger === 1 ? 'ist' : 'sind'} mehr als {CUE_TOL} s anders lang — Cues und Beatgrid in rekordbox danach neu setzen.</div>
      {:else}
        <div class="dlg-hint">Gleicher Name und Ordner, gleiches Format, alle Tags bleiben. Alte Dateien kommen in den Papierkorb.</div>
      {/if}
      <div class="dlg-actions">
        <button class="btn" onclick={onclose} disabled={st.phase === 'replace'}>{st.phase === 'finished' ? 'Fertig' : 'Abbrechen'}</button>
        {#if st.phase !== 'finished'}
          <button class="btn btn-primary" onclick={replaceAll} disabled={!chosen.length || running}>
            <i class="ti ti-refresh"></i> {chosen.length} ersetzen
          </button>
        {/if}
      </div>
    </div>
  </div>
</div>

<style>
  .panel { width: min(900px, 94vw); height: min(84vh, 720px); padding: 0; gap: 0; display: flex; flex-direction: column; }
  .hdr { display: flex; align-items: center; padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); }
  .close-btn { margin-left: auto; }
  .status { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-2) var(--sp-5); border-bottom: 1px solid var(--c-br1);
            font-size: var(--fs-sm); color: var(--c-tx3); min-height: 40px; }
  .status progress { width: 160px; }
  .status .cur { color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 280px; }
  .spacer { flex: 1; }
  .list { flex: 1; min-height: 0; overflow-y: auto; }
  .it { display: flex; align-items: center; gap: var(--sp-2); padding: 5px var(--sp-5); border-bottom: 1px solid var(--c-br1); font-size: var(--fs-sm); }
  .it.none { opacity: .7; }
  .cb-ph { width: 13px; flex-shrink: 0; }
  .old { flex: 1 1 0; min-width: 0; display: flex; gap: 6px; color: var(--c-tx2); overflow: hidden; white-space: nowrap; }
  .old { text-overflow: ellipsis; }
  .dur, .n-meta { color: var(--c-tx4); font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .arrow { color: var(--c-tx5); flex-shrink: 0; }
  .new { flex: 1.2 1 0; min-width: 0; display: flex; align-items: center; gap: 6px; overflow: hidden; white-space: nowrap; }
  .n-title { color: var(--c-tx1); font-weight: 600; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  .muted { color: var(--c-tx4); }
  .delta { font-weight: 600; color: var(--c-warn-tx); font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .delta.ok { color: var(--c-green-tx); }
  .unsure { flex-shrink: 0; font-size: var(--fs-cap); font-weight: 700; padding: 0 5px; border-radius: var(--r-s);
            color: var(--c-warn-tx); border: 1px solid var(--c-warn-br); background: var(--c-warn-bg); }
  .song-chip { flex-shrink: 0; padding: 0 5px; border-radius: var(--r-s); font-size: var(--fs-cap); font-weight: 700; line-height: 16px;
               color: var(--c-green-tx); background: var(--c-green-bg); border: 1px solid var(--c-green-br); }
  .st { width: 18px; flex-shrink: 0; text-align: center; }
  .ok { color: var(--c-green-tx); }
  .bad { color: var(--c-red-tx); }
  .spin { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .foot { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-3) var(--sp-5) var(--sp-4); border-top: 1px solid var(--c-br1); }
  .foot .dlg-actions { margin: 0; }
</style>
