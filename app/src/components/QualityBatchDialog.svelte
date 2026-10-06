<script>
  import { untrack } from 'svelte'
  import { qualityBatch, qualityBatchOpen, qualityReplace, send } from '../stores/ws.js'

  // Viele Titel auf einmal ersetzen: Das Backend sucht je Titel automatisch
  // die beste Version (Song-Version zuerst, gleiche Fassung, kleinste
  // Laengenabweichung). Sichere Treffer sind angehakt, unsichere (kein
  // Kuenstler bekannt) nicht. Dann nacheinander ersetzen — wie beim
  // Einzel-Ersetzen: gleicher Name und Pfad, Tags bleiben, alte Datei in den
  // Papierkorb.
  // Gestartet wird ueber startQualityBatch() (ws.js). Das Fenster laesst sich
  // jederzeit schliessen: Suche und Ersetzen laufen im Hintergrund weiter.
  const CUE_TOL = 2
  const st = $derived($qualityBatch)
  // Musikvideos ersetzen: Song-Fassung statt Mitschnitt, Name ohne "(Official Video)"
  const video = $derived(st.mode === 'video')
  const paths = $derived(st.paths ?? [])
  const items = $derived(paths.map(p => st.items?.[p]).filter(Boolean))
  const found = $derived(items.filter(it => it.candidate))
  let pick = $state({})         // Pfad -> angehakt?

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
    send({ type: 'quality_batch_replace', items: chosen.map(it => ({ path: it.path, url: it.candidate.url, rename: video })) })
  }
  function setAll(on) {
    const p = { ...pick }
    for (const it of found) p[it.path] = on
    pick = p
  }
  // Ausblenden: laeuft weiter. Verwerfen: Vorschlaege weg, Anzeige verschwindet.
  function hide() { qualityBatchOpen.set(false) }
  function discard() {
    if (running) send({ type: 'quality_batch_cancel' })
    qualityBatchOpen.set(false)
    qualityBatch.set({})
    pick = {}
  }
  const open = $derived($qualityBatchOpen && paths.length > 0)
  function onkey(e) { if (e.key === 'Escape' && open) hide() }
</script>

<svelte:window onkeydown={onkey} />

{#if !$qualityBatchOpen && paths.length && (running || st.phase === 'review' || st.phase === 'finished')}
  <!-- Klein unten rechts, solange das Fenster zu ist -->
  <div class="bg" role="status" aria-live="polite">
    {#if running}<i class="ti ti-refresh spin" aria-hidden="true"></i>{:else}<i class="ti ti-check done" aria-hidden="true"></i>{/if}
    <button class="bg-body" onclick={() => qualityBatchOpen.set(true)} title="Fenster öffnen">
      <span class="bg-head">{st.phase === 'replace' ? 'Bessere Versionen werden eingesetzt' : 'Bessere Versionen'}</span>
      <span class="bg-txt">
        {#if st.phase === 'search'}Suche {st.done ?? 0} / {st.total} …
        {:else if st.phase === 'replace'}Ersetze {(st.done ?? 0) + 1} / {st.total}
        {:else if st.phase === 'review'}{found.length} Vorschläge — ansehen
        {:else}{st.result?.ok ?? 0} ersetzt — ansehen{/if}
      </span>
      {#if running}<span class="bg-bar"><span style="width: {Math.round((st.done ?? 0) / Math.max(1, st.total) * 100)}%"></span></span>{/if}
    </button>
    {#if running}
      <button class="btn btn-icon btn-sm" onclick={() => send({ type: 'quality_batch_cancel' })} title="Abbrechen" aria-label="Abbrechen"><i class="ti ti-x"></i></button>
    {:else}
      <button class="btn btn-icon btn-sm" onclick={discard} title="Verwerfen" aria-label="Verwerfen"><i class="ti ti-x"></i></button>
    {/if}
  </div>
{/if}

{#if open}
<div class="dlg-overlay" onclick={hide} role="presentation">
  <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label={video ? 'Musikvideos ersetzen' : 'Bessere Versionen suchen'}>

    <div class="hdr">
      <span class="dlg-title">{video ? `${paths.length} Musikvideos — Song-Fassung laden` : `Bessere Versionen für ${paths.length} Titel`}</span>
      <button class="btn btn-icon btn-sm close-btn" onclick={hide} title={running ? 'Im Hintergrund weiterlaufen lassen' : 'Schließen'} aria-label="Schließen"><i class="ti ti-x"></i></button>
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
        <span class="ok"><i class="ti ti-check"></i> {st.result?.ok ?? 0} ersetzt{video && st.result?.renamed != null ? `, ${st.result.renamed} umbenannt` : ''}{st.result?.cancelled ? ', dann abgebrochen' : ''}.</span>
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
          <span class="old" title={it.path}>{it.title}<span class="dur">{fmt(it.duration)}</span>
            {#if video && it.candidate && it.new_name}<span class="newname" title="So heißt die Datei danach">→ {it.new_name}</span>{/if}</span>
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
            <span class="new muted">{video ? 'keine Song-Fassung gefunden — bleibt, wie es ist' : 'nichts Passendes gefunden'}</span>
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
      {:else if video}
        <div class="dlg-hint">Die Song-Fassung ersetzt den Mitschnitt des Videos (oft länger — Videos sind häufig gekürzt). Datei und Titel verlieren den Zusatz „(Official Video)“; Bibliothek, Warteschlange und eigene Playlists ziehen mit, Tags und Cover bleiben. Alte Dateien kommen in den Papierkorb. Andere Programme (rekordbox, FL Studio) finden umbenannte Dateien nicht mehr unter dem alten Namen.</div>
      {:else if nLonger && st.phase === 'review'}
        <div class="notice"><i class="ti ti-alert-triangle"></i>
          {nLonger} der ausgewählten Titel {nLonger === 1 ? 'ist' : 'sind'} mehr als {CUE_TOL} s anders lang — Cues und Beatgrid in rekordbox danach neu setzen.</div>
      {:else}
        <div class="dlg-hint">Gleicher Name und Ordner, gleiches Format, alle Tags bleiben. Alte Dateien kommen in den Papierkorb.</div>
      {/if}
      <div class="dlg-actions">
        {#if running}
          <button class="btn" onclick={hide}>Im Hintergrund weiter</button>
        {:else}
          <button class="btn" onclick={discard}>{st.phase === 'finished' ? 'Fertig' : 'Verwerfen'}</button>
        {/if}
        {#if st.phase !== 'finished'}
          <button class="btn btn-primary" onclick={replaceAll} disabled={!chosen.length || running}>
            <i class="ti ti-refresh"></i> {chosen.length} ersetzen
          </button>
        {/if}
      </div>
    </div>
  </div>
</div>
{/if}

<style>
  .newname { display: block; font-size: var(--fs-sm); color: var(--c-accent-tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bg {
    position: fixed; right: 16px; bottom: 16px; z-index: 840;
    width: 320px; max-width: calc(100vw - 32px);
    display: flex; align-items: center; gap: 10px; padding: 10px 8px 10px 12px;
    background: var(--c-bg5); border: 1px solid var(--c-br2);
    border-left: 4px solid var(--c-accent); border-radius: var(--r-m);
    box-shadow: 0 8px 26px rgba(0, 0, 0, .4);
  }
  .bg > .spin, .bg > .done { font-size: 18px; color: var(--c-accent-tx); flex-shrink: 0; }
  .bg > .done { color: var(--c-green-tx); }
  .bg-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; text-align: left;
             background: none; border: none; padding: 0; cursor: pointer; font: inherit; color: inherit; }
  .bg-head { font-size: var(--fs-sm); font-weight: 700; color: var(--c-tx1); }
  .bg-txt { font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; }
  .bg-bar { height: 3px; border-radius: 2px; background: var(--c-br2); overflow: hidden; margin-top: 2px; }
  .bg-bar span { display: block; height: 100%; background: var(--c-accent); transition: width .25s; }
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
