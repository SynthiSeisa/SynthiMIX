<script>
  import { untrack } from 'svelte'
  import { titleState, send, lastfmApiKey, openSettings } from '../stores/ws.js'

  // Titel aufraeumen: Video-Zusaetze, Kanal-/Label-Angaben und Genre-Klammern
  // entfernen, "Kuenstler - Titel" auf Titel- und Kuenstler-Tag verteilen.
  // Optional Schreibweise von Last.fm. Geschrieben werden nur diese beiden
  // Tags, nie der Dateiname — rekordbox findet die Dateien weiter.
  // mode "fingerprint": dieselbe Liste, die Vorschlaege kommen aber von
  // AcoustID (am Klang erkannt) statt aus den Regeln.
  let { paths = null, mode = 'clean', onclose } = $props()
  const fp = mode === 'fingerprint'

  const PAGE = 250
  const st = $derived($titleState)
  const sugg = $derived(st.suggestions)
  let pick = $state({})          // Pfad -> { on, title, artist }
  let editing = $state(null)
  let filter = $state('')
  let onlyUnsure = $state(false)
  let shown = $state(PAGE)

  function run(online) {
    titleState.update(s => ({ ...s, busy: true, progress: null, applied: null }))
    send({ type: 'title_suggest', online, paths })
  }
  $effect(() => {
    untrack(() => {
      titleState.set({ busy: true })
      send(fp ? { type: 'fingerprint_suggest', paths } : { type: 'title_suggest', online: false, paths })
    })
  })
  $effect(() => {
    const s = sugg
    if (!s) return
    untrack(() => {
      const p = {}
      for (const it of s.items) p[it.path] = pick[it.path] ?? { on: it.sure, title: it.title, artist: it.artist }
      pick = p
      shown = PAGE
    })
  })

  const list = $derived.by(() => {
    if (!sugg) return []
    const q = filter.trim().toLowerCase()
    return sugg.items.filter(it => (!onlyUnsure || !it.sure) &&
      (!q || it.old_title.toLowerCase().includes(q) || (it.old_artist ?? '').toLowerCase().includes(q)))
  })
  const chosen = $derived(Object.entries(pick).filter(([, v]) => v.on && v.title?.trim()))
  const nUnsure = $derived(sugg ? sugg.items.filter(it => !it.sure).length : 0)

  function setAll(on) {
    const p = { ...pick }
    for (const it of list) p[it.path] = { ...p[it.path], on }
    pick = p
  }
  function apply() {
    const items = chosen.map(([path, v]) => ({ path, title: v.title.trim(), artist: (v.artist ?? '').trim() }))
    titleState.update(s => ({ ...s, applying: { done: 0, total: items.length } }))
    send({ type: 'title_apply', items })
  }
  function onkey(e) { if (e.key === 'Escape' && !st.applying) onclose() }
  // ~1,3 s je Titel, 3 gleichzeitig
  // ~3 Titel je Sekunde (Grenze von AcoustID), schon erkannte kommen sofort aus dem Speicher
  const fpEta = $derived(st.progress?.total ? Math.ceil((st.progress.total - st.progress.done) * 0.35 / 60) : 0)
  function fixSettings() { openSettings(sugg.fix_tab); onclose() }
  // Geschlossen, waehrend noch gesucht wird: nicht im Hintergrund weiterfragen
  $effect(() => () => { if (untrack(() => $titleState.busy)) send({ type: 'title_cancel' }) })
</script>

<svelte:window onkeydown={onkey} />

<div class="dlg-overlay" onclick={() => !st.applying && !st.busy && onclose()} role="presentation">
  <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label={fp ? 'Per Fingerprint erkennen' : 'Titel aufräumen'}>

    <div class="hdr">
      <span class="dlg-title">{fp ? 'Per Fingerprint erkennen' : 'Titel aufräumen'}{paths?.length ? ` · ${paths.length} ${paths.length === 1 ? 'Titel' : 'ausgewählt'}` : ''}</span>
      <button class="btn btn-icon btn-sm close-btn" onclick={onclose} disabled={!!st.applying} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
    </div>

    <div class="body">
      {#if fp}
      <p class="intro">
        Erkennt jeden Titel am Klang (AcoustID) — auch Dateien ohne oder mit falschen Tags. Vorausgewählt sind nur
        sichere Treffer (ab 85 %); Remixe, die AcoustID dem Original zuordnet, und reine Text-Treffer sind orange markiert und nicht vorausgewählt.
        Geschrieben werden nur Titel und Künstler — Dateinamen bleiben.
      </p>
      {:else}
      <p class="intro">
        Entfernt „(Official Video)“, „HQ“, „| Kanalname“, „[Label]“, Genre-Klammern wie „(Rock)“ und Anführungszeichen,
        und verteilt „Künstler - Titel“ auf Titel- und Künstler-Feld. Remix, Edit, VIP, feat. und Acapella bleiben.
        Geschrieben werden nur diese zwei Tags — Dateinamen bleiben, rekordbox findet alles weiter („Tag neu laden“).
      </p>
      {/if}

      <div class="bar">
        <input class="field field-sm" type="search" placeholder="Filtern…" bind:value={filter} aria-label="Filtern" />
        <label class="chk"><input type="checkbox" bind:checked={onlyUnsure} /> nur unsichere ({nUnsure})</label>
        <span class="spacer"></span>
        <button class="btn btn-ghost btn-sm" onclick={() => setAll(true)} disabled={!list.length}>Alle</button>
        <button class="btn btn-ghost btn-sm" onclick={() => setAll(false)} disabled={!list.length}>Keine</button>
        {#if !fp}
        <button class="btn btn-sm" onclick={() => run(true)} disabled={st.busy || !!st.applying || !$lastfmApiKey}
                title={$lastfmApiKey ? 'Schreibweise von Künstler und Titel bei Last.fm prüfen' : 'Last.fm-Key unter Einstellungen → Dienste eintragen'}>
          <i class="ti ti-wand"></i> Schreibweise online prüfen
        </button>
        {/if}
      </div>

      {#if st.busy}
        <div class="busy"><i class="ti ti-refresh spin"></i>
          {#if st.progress?.total}{fp ? 'Erkenne am Klang' : 'Frage Last.fm'}: {st.progress.done} / {st.progress.total}{#if fp && fpEta > 1} · noch ca. {fpEta} min{/if}
            <button class="btn btn-ghost btn-sm" onclick={() => send({ type: 'title_cancel' })}>Abbrechen</button>
          {:else}Rechne…{/if}
        </div>
      {:else if sugg?.error}
        <div class="busy err"><i class="ti ti-alert-triangle"></i> {sugg.error}
          {#if sugg.fix_tab}<button class="btn btn-sm" onclick={fixSettings}>Einstellungen öffnen</button>{/if}
        </div>
      {:else if sugg && !sugg.items.length}
        <div class="busy">{fp ? `Keine Änderungen: ${sugg.same ?? 0} passen schon, ${sugg.nomatch ?? 0} nicht erkannt.` : 'Alles sauber — keine Vorschläge.'}</div>
      {:else if sugg}
        <div class="list">
          <div class="it head"><span class="cb"></span><span class="c-old">Bisher</span><span class="c-new">Neu: Künstler - Titel</span><span class="c-src"></span></div>
          {#each list.slice(0, shown) as it (it.path)}
            {@const v = pick[it.path]}
            <div class="it" class:off={!v?.on}>
              <input class="cb" type="checkbox" checked={v?.on} onchange={(e) => pick = { ...pick, [it.path]: { ...v, on: e.currentTarget.checked } }}
                     aria-label="Übernehmen: {it.old_title}" />
              <span class="c-old" title={it.path}>
                <span class="o-t">{it.old_title}</span>
                {#if it.old_artist}<span class="o-a">{it.old_artist}</span>{/if}
              </span>
              {#if editing === it.path}
                <span class="c-new edit">
                  <input class="field field-sm" value={v.artist} placeholder="Künstler" aria-label="Künstler"
                         oninput={(e) => pick = { ...pick, [it.path]: { ...pick[it.path], artist: e.currentTarget.value, on: true } }} />
                  <input class="field field-sm" value={v.title} placeholder="Titel" aria-label="Titel"
                         oninput={(e) => pick = { ...pick, [it.path]: { ...pick[it.path], title: e.currentTarget.value, on: true } }}
                         onkeydown={(e) => e.key === 'Enter' && (editing = null)} />
                  <button class="btn btn-sm" onclick={() => editing = null}>OK</button>
                </span>
              {:else}
                <button class="c-new" onclick={() => editing = it.path} title="Klicken zum Bearbeiten">
                  {#if v?.artist}<span class="n-a">{v.artist}</span><span class="dash">-</span>{/if}<span class="n-t">{v?.title}</span>
                </button>
              {/if}
              <span class="c-src">
                {#if fp}<span class="src" class:low={!it.sure} title={it.why || 'Übereinstimmung laut AcoustID'}>{it.source}</span>
                {:else if !it.sure}<span class="unsure" title="Künstler sieht nach Titel aus — evtl. „Titel - Künstler“ vertauscht">prüfen</span>
                {:else if it.source !== 'Regeln'}<span class="src">{it.source}</span>{/if}
              </span>
            </div>
          {/each}
          {#if list.length > shown}
            <button class="btn btn-ghost btn-sm more" onclick={() => shown += PAGE}>Weitere {Math.min(PAGE, list.length - shown)} von {list.length - shown} anzeigen</button>
          {/if}
        </div>
      {/if}
    </div>

    <div class="foot">
      {#if st.applied}
        <div class="notice ok"><i class="ti ti-check"></i> {st.applied.ok} Titel geändert{st.applied.cancelled ? ', dann abgebrochen' : ''}.
          {#if st.applied.skipped}{st.applied.skipped} übersprungen (läuft gerade).{/if}
          {#if st.applied.failed_count}{st.applied.failed_count} ließen sich nicht schreiben.{/if}</div>
      {:else if st.applying}
        <div class="notice info apply-row"><i class="ti ti-refresh spin"></i> Schreibe Titel: {st.applying.done} / {st.applying.total}
          <button class="btn btn-sm" onclick={() => send({ type: 'title_cancel' })}><i class="ti ti-player-stop"></i> Abbrechen</button></div>
      {:else if fp}
        <div class="dlg-hint">{#if sugg && !sugg.error}{sugg.checked ?? 0} geprüft · {sugg.same ?? 0} passen schon · {sugg.nomatch ?? 0} nicht erkannt{sugg.cached ? ` · ${sugg.cached} aus dem Speicher` : ''}{sugg.cancelled ? ' · abgebrochen' : ''}.{' '}{/if}Orange = unsicher (Maus drauf zeigt warum). Klick auf einen neuen Titel zum Bearbeiten.</div>
      {:else}
        <div class="dlg-hint">„prüfen“ = Reihenfolge vermutlich vertauscht, nicht vorausgewählt. Klick auf einen neuen Titel zum Bearbeiten.</div>
      {/if}
      <div class="dlg-actions">
        <button class="btn" onclick={onclose} disabled={!!st.applying}>{st.applied ? 'Fertig' : 'Abbrechen'}</button>
        {#if !st.applied}
          <button class="btn btn-primary" onclick={apply} disabled={!chosen.length || st.busy || !!st.applying}>
            <i class="ti ti-check"></i> Übernehmen ({chosen.length})
          </button>
        {/if}
      </div>
    </div>
  </div>
</div>

<style>
  .panel { width: min(960px, 95vw); height: min(86vh, 780px); padding: 0; gap: 0; display: flex; flex-direction: column; }
  .hdr { display: flex; align-items: center; padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); }
  .close-btn { margin-left: auto; }
  .body { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-3) var(--sp-5); }
  .intro { margin: 0; font-size: var(--fs-body); color: var(--c-tx2); line-height: 1.5; }
  .bar { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
  .bar input[type=search] { width: 200px; }
  .chk { display: inline-flex; align-items: center; gap: 4px; font-size: var(--fs-sm); color: var(--c-tx2); }
  .spacer { flex: 1; }
  .busy { display: flex; align-items: center; justify-content: center; gap: var(--sp-2); padding: var(--sp-5); color: var(--c-tx3); font-size: var(--fs-body); }
  .spin { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .list { flex: 1; min-height: 0; overflow-y: auto; border: 1px solid var(--c-br1); border-radius: var(--r-m); }
  .it { display: flex; align-items: center; gap: var(--sp-2); padding: 4px var(--sp-3); border-bottom: 1px solid var(--c-br1); font-size: var(--fs-sm); min-height: 32px; }
  .it.head { position: sticky; top: 0; z-index: 1; background: var(--c-bg2); font-size: var(--fs-cap); font-weight: 700;
             letter-spacing: .06em; text-transform: uppercase; color: var(--c-tx4); min-height: 28px; }
  .it.off .c-new { opacity: .5; }
  .cb { width: 14px; flex-shrink: 0; }
  .c-old { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; }
  .o-t { color: var(--c-tx3); text-decoration: line-through; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .o-a { color: var(--c-tx5); font-size: var(--fs-cap); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .c-new { flex: 1.3 1 0; min-width: 0; display: flex; align-items: center; gap: 6px; background: none; border: none; padding: 2px 4px;
           border-radius: var(--r-s); cursor: text; font-family: inherit; font-size: inherit; text-align: left; }
  button.c-new:hover { background: var(--c-hover); }
  .c-new.edit { cursor: default; }
  .c-new.edit input { flex: 1; min-width: 0; }
  .n-a { color: var(--c-tx2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 45%; }
  .dash { color: var(--c-tx5); }
  .n-t { color: var(--c-tx1); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  .c-src { width: 76px; flex-shrink: 0; text-align: right; }
  .unsure { font-size: var(--fs-cap); font-weight: 700; padding: 0 5px; border-radius: var(--r-s);
            color: var(--c-warn-tx); border: 1px solid var(--c-warn-br); background: var(--c-warn-bg); }
  .src { font-size: var(--fs-cap); color: var(--c-green-tx); font-variant-numeric: tabular-nums; }
  .src.low { color: var(--c-warn-tx); }
  .busy.err { color: var(--c-warn-tx); flex-wrap: wrap; text-align: center; }
  .more { margin: var(--sp-2) auto; display: flex; }
  .foot { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-3) var(--sp-5) var(--sp-4); border-top: 1px solid var(--c-br1); }
  .foot .dlg-actions { margin: 0; }
  .apply-row { display: flex; align-items: center; gap: var(--sp-2); }
  .apply-row .btn { margin-left: auto; }
</style>
