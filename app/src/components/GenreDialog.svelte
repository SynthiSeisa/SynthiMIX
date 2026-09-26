<script>
  import { untrack } from 'svelte'
  import { genreState, send, lastfmApiKey, spotifyClientId, openSettings } from '../stores/ws.js'

  // Genres vereinheitlichen und ergaenzen. Ablauf:
  // 1. Beim Oeffnen offline rechnen: Schreibweisen vereinheitlichen, Ordner-Regeln.
  // 2. Ordner-Regeln pruefen (Vorschlag aus dem Ordnernamen), dann online ergaenzen
  //    (Last.fm-Tags des Titels, Spotify-Kuenstlergenres, Last.fm-Kuenstler).
  // 3. Vorschlagsliste pruefen — sichere sind angehakt — und uebernehmen. Das
  //    Backend schreibt nur das Genre-Feld der Dateien.
  let { onclose } = $props()

  const st = $derived($genreState)
  const sugg = $derived(st.suggestions)
  const genres = $derived(sugg?.genres ?? [])

  // Eigene Aenderungen an den Ordner-Regeln; sonst gilt gespeicherte Regel bzw.
  // der Vorschlag aus dem Ordnernamen. Bewusst kein bind:value — die Auswahlliste
  // wuerde beim ersten Zeichnen "" zurueckschreiben und den Vorschlag ueberdecken.
  let userRules = $state({})      // Ordner -> Genre ('' = keine Regel)
  function ruleOf(f) { return userRules[f.folder] ?? f.rule ?? f.auto ?? '' }
  let pick = $state({})           // Pfad -> { on, genre }
  let open = $state({})           // Genre-Gruppe aufgeklappt?
  let editing = $state(null)      // Pfad, dessen Genre gerade per Auswahlliste geaendert wird
  let showRules = $state(true)

  function run(online) {
    genreState.update(s => ({ ...s, busy: true, progress: null, applied: null }))
    const folder_rules = Object.fromEntries((sugg?.folders ?? []).map(f => [f.folder, ruleOf(f)]))   // '' = bewusst keine Regel
    send({ type: 'genre_suggest', online, folder_rules })
  }
  // Beim Oeffnen einmal offline rechnen
  $effect(() => {
    untrack(() => {
      genreState.set({ busy: true, progress: null, suggestions: null, applied: null, applying: null })
      send({ type: 'genre_suggest', online: false })
    })
  })

  // Neue Vorschlaege: Ordner-Regeln und Auswahl uebernehmen, eigene Aenderungen behalten
  $effect(() => {
    const s = sugg
    if (!s) return
    untrack(() => {
      const p = {}
      for (const it of s.items) p[it.path] = pick[it.path] ?? { on: it.sure, genre: it.genre }
      pick = p
    })
  })

  const groups = $derived.by(() => {
    if (!sugg) return []
    const m = new Map()
    for (const it of sugg.items) {
      const g = pick[it.path]?.genre ?? it.genre
      if (!m.has(g)) m.set(g, [])
      m.get(g).push(it)
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length)
  })
  const chosen = $derived(Object.entries(pick).filter(([, v]) => v.on))

  function toggleGroup(items, on) {
    const p = { ...pick }
    for (const it of items) p[it.path] = { ...p[it.path], on }
    pick = p
  }
  function setGenre(path, genre) {
    pick = { ...pick, [path]: { on: true, genre } }
    editing = null
  }
  function apply() {
    const items = chosen.map(([path, v]) => ({ path, genre: v.genre }))
    genreState.update(s => ({ ...s, applying: { done: 0, total: items.length } }))
    send({ type: 'genre_apply', items })
  }
  function onkey(e) { if (e.key === 'Escape' && !st.applying) onclose() }

  const SOURCE_TIP = {
    'Vereinheitlicht': 'Vorhandenes Genre, nur anders geschrieben',
    'Ordner': 'Aus deiner Ordner-Regel',
    'Last.fm': 'Last.fm-Tags dieses Titels',
    'Spotify (Künstler)': 'Spotify-Genres des Künstlers — gilt für alle seine Titel',
    'Last.fm (Künstler)': 'Last.fm-Tags des Künstlers — gilt für alle seine Titel',
  }
</script>

<svelte:window onkeydown={onkey} />

<div class="dlg-overlay" onclick={() => !st.applying && onclose()} role="presentation">
  <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Genres ergänzen">

    <div class="hdr">
      <span class="dlg-title">Genres ergänzen</span>
      <button class="btn btn-icon btn-sm close-btn" onclick={onclose} disabled={!!st.applying} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
    </div>

    <div class="body">
      <p class="intro">
        Vereinheitlicht Schreibweisen („Drum and Bass“ → „Drum & Bass“), entfernt YouTube-Kategorien wie „Music“ und
        schlägt fehlende Genres vor: erst aus deinen Ordnern, dann online.
        Übernommen wird nur, was angehakt ist — ins Genre-Feld der Datei (in rekordbox danach „Tag neu laden“) und in SynthiMIX.
      </p>

      <div class="sources">
        <span class="src" class:ok={$lastfmApiKey}><i class="ti {$lastfmApiKey ? 'ti-check' : 'ti-minus'}"></i> Last.fm</span>
        <span class="src" class:ok={$spotifyClientId}><i class="ti {$spotifyClientId ? 'ti-check' : 'ti-minus'}"></i> Spotify</span>
        {#if !$lastfmApiKey || !$spotifyClientId}
          <button class="btn btn-ghost btn-sm" onclick={() => openSettings(!$lastfmApiKey ? 'services' : 'download')}>Zugang einrichten</button>
        {/if}
      </div>

      {#if sugg}
        <!-- Ordner-Regeln -->
        <div class="sec">
          <button class="sec-hdr" onclick={() => showRules = !showRules} aria-expanded={showRules}>
            <i class="ti ti-chevron-right chev" class:open={showRules} aria-hidden="true"></i>
            <span class="eyebrow">Ordner-Regeln</span>
            <span class="sec-sub">Alle Titel ohne Genre in diesem Ordner bekommen das gewählte Genre</span>
          </button>
          {#if showRules}
            <div class="rules">
              {#each sugg.folders as f}
                <label class="rule" title={f.folder}>
                  <span class="r-name">{f.name}</span>
                  <span class="r-count">{f.count}</span>
                  <select class="field field-sm" class:set={ruleOf(f)}
                          onchange={(e) => userRules = { ...userRules, [f.folder]: e.currentTarget.value }}>
                    <option value="" selected={ruleOf(f) === ''}>— keine Regel —</option>
                    {#each genres as g}<option value={g} selected={ruleOf(f) === g}>{g}</option>{/each}
                  </select>
                </label>
              {/each}
            </div>
          {/if}
        </div>
      {/if}

      <!-- Vorschlaege -->
      <div class="sec grow">
        <div class="sec-row">
          <span class="eyebrow">Vorschläge</span>
          {#if sugg}<span class="sec-sub">{sugg.items.length} Titel · {sugg.missing} ohne Vorschlag</span>{/if}
          <span class="spacer"></span>
          <button class="btn btn-sm" onclick={() => run(false)} disabled={st.busy || !!st.applying}
                  title="Mit den Ordner-Regeln neu rechnen (ohne Internet)"><i class="ti ti-refresh"></i> Neu rechnen</button>
          <button class="btn btn-sm btn-primary" onclick={() => run(true)} disabled={st.busy || !!st.applying || (!$lastfmApiKey && !$spotifyClientId)}
                  title="Titel ohne Regel bei Last.fm und Spotify nachschlagen"><i class="ti ti-wand"></i> Online ergänzen</button>
        </div>

        {#if st.busy}
          <div class="busy">
            <i class="ti ti-refresh spin"></i>
            {#if st.progress?.total}
              Frage Last.fm/Spotify: {st.progress.done} / {st.progress.total}
              <progress max={st.progress.total} value={st.progress.done}></progress>
              <button class="btn btn-ghost btn-sm" onclick={() => send({ type: 'genre_cancel' })}>Abbrechen</button>
            {:else}
              Rechne…
            {/if}
          </div>
        {:else if sugg && !sugg.items.length}
          <div class="busy">Keine Vorschläge. {sugg.missing ? 'Ordner-Regeln setzen oder online ergänzen.' : ''}</div>
        {:else if sugg}
          <div class="list">
            {#each groups as [g, items]}
              {@const nOn = items.filter(it => pick[it.path]?.on).length}
              <div class="grp">
                <div class="grp-hdr">
                  <input type="checkbox" checked={nOn === items.length} indeterminate={nOn > 0 && nOn < items.length}
                         onchange={(e) => toggleGroup(items, e.currentTarget.checked)} aria-label="Alle in {g}" />
                  <button class="grp-btn" onclick={() => open = { ...open, [g]: !open[g] }} aria-expanded={!!open[g]}>
                    <i class="ti ti-chevron-right chev" class:open={open[g]} aria-hidden="true"></i>
                    <b>{g}</b> <span class="sec-sub">{items.length} Titel · {nOn} ausgewählt</span>
                  </button>
                </div>
                {#if open[g]}
                  {#each items as it (it.path)}
                    <div class="it">
                      <input type="checkbox" checked={pick[it.path]?.on}
                             onchange={(e) => pick = { ...pick, [it.path]: { ...pick[it.path], on: e.currentTarget.checked } }}
                             aria-label="Übernehmen: {it.title}" />
                      <span class="it-title" title={it.path}>{it.title}</span>
                      {#if it.current}<span class="it-cur" title="Bisher">{it.current}</span>{/if}
                      <span class="it-src" class:unsure={!it.sure} title={SOURCE_TIP[it.source] ?? ''}>{it.source}</span>
                      {#if editing === it.path}
                        <select class="field field-sm it-sel" value={pick[it.path]?.genre}
                                onchange={(e) => setGenre(it.path, e.currentTarget.value)} onblur={() => editing = null}>
                          {#each genres as gg}<option value={gg}>{gg}</option>{/each}
                        </select>
                      {:else}
                        <button class="btn btn-ghost btn-sm it-edit" onclick={() => editing = it.path} title="Genre ändern">ändern</button>
                      {/if}
                    </div>
                  {/each}
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      </div>
    </div>

    <div class="foot">
      {#if st.applied}
        <div class="notice ok"><i class="ti ti-check"></i>
          {st.applied.ok} Genres gesetzt.{#if st.applied.skipped} {st.applied.skipped} übersprungen (läuft gerade).{/if}{#if st.applied.failed_count} {st.applied.failed_count} ließen sich nicht schreiben.{/if}
        </div>
      {:else if st.applying}
        <div class="notice info"><i class="ti ti-refresh spin"></i> Schreibe Genres: {st.applying.done} / {st.applying.total}</div>
      {:else}
        <div class="dlg-hint">Orange Quelle = unsicher (nur Künstler), nicht vorausgewählt. Ein Klick auf „ändern“ setzt ein anderes Genre.</div>
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
  .panel { width: min(860px, 94vw); height: min(86vh, 760px); padding: 0; gap: 0; display: flex; flex-direction: column; }
  .hdr { display: flex; align-items: center; padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); }
  .close-btn { margin-left: auto; }
  .body { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-3) var(--sp-5); overflow: hidden; }
  .intro { margin: 0; font-size: var(--fs-body); color: var(--c-tx2); line-height: 1.5; }
  .sources { display: flex; align-items: center; gap: var(--sp-3); font-size: var(--fs-sm); }
  .src { display: inline-flex; align-items: center; gap: 4px; color: var(--c-tx4); }
  .src.ok { color: var(--c-green-tx); }
  .sec { display: flex; flex-direction: column; gap: var(--sp-2); min-height: 0; }
  .sec.grow { flex: 1; }
  .sec-hdr, .grp-btn { display: flex; align-items: center; gap: var(--sp-2); background: none; border: none; padding: 0; cursor: pointer; font-family: inherit; color: var(--c-tx1); text-align: left; }
  .sec-row { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
  .sec-sub { font-size: var(--fs-sm); color: var(--c-tx3); font-weight: 400; }
  .spacer { flex: 1; }
  .chev { font-size: 14px; color: var(--c-tx4); transition: transform .12s; }
  .chev.open { transform: rotate(90deg); }
  .rules { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 4px var(--sp-3); max-height: 170px; overflow-y: auto; padding-right: 4px; }
  .rule { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-sm); min-width: 0; }
  .r-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-tx2); }
  .r-count { color: var(--c-tx4); font-variant-numeric: tabular-nums; }
  .rule select { width: 132px; flex-shrink: 0; color: var(--c-tx4); }
  .rule select.set { color: var(--c-tx1); font-weight: 600; }
  .busy { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-5) 0; justify-content: center; font-size: var(--fs-body); color: var(--c-tx3); }
  .busy progress { width: 160px; }
  .spin { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .list { flex: 1; min-height: 0; overflow-y: auto; border: 1px solid var(--c-br1); border-radius: var(--r-m); }
  .grp { border-bottom: 1px solid var(--c-br1); }
  .grp-hdr { display: flex; align-items: center; gap: var(--sp-2); padding: 6px var(--sp-3); background: var(--c-bg2); position: sticky; top: 0; z-index: 1; }
  .it { display: flex; align-items: center; gap: var(--sp-2); padding: 3px var(--sp-3) 3px 34px; font-size: var(--fs-sm); border-top: 1px solid var(--c-br1); min-height: 30px; }
  .it-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-tx1); }
  .it-cur { color: var(--c-tx4); text-decoration: line-through; white-space: nowrap; }
  .it-src { color: var(--c-green-tx); white-space: nowrap; }
  .it-src.unsure { color: var(--c-warn-tx); }
  .it-sel { width: 150px; }
  .it-edit { --h: 24px; }
  .foot { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-3) var(--sp-5) var(--sp-4); border-top: 1px solid var(--c-br1); }
  .foot .dlg-actions { margin: 0; }
</style>
