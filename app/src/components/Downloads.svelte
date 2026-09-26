<script>
  import { untrack } from 'svelte'
  import { downloads, searchResults, send, openSettings, videoCheckPending } from '../stores/ws.js'

  let input     = $state('')
  let fmt       = $state('mp3-best')
  let searching = $state(false)
  let expanded         = $state(new Set())
  let manuallyCollapsed = $state(new Set())
  let fmtOpen   = $state(false)
  let fmtPos    = $state('')      // fest positioniert: der Download-Bereich schneidet sonst ab
  let fmtBtn    = $state(null)

  function toggleFmt() {
    if (!fmtOpen && fmtBtn) {
      const r = fmtBtn.getBoundingClientRect()
      const right = window.innerWidth - r.right
      // nach oben, wenn unten kein Platz ist
      fmtPos = window.innerHeight - r.bottom < 220
        ? `right:${right}px;bottom:${window.innerHeight - r.top + 4}px`
        : `right:${right}px;top:${r.bottom + 4}px`
    }
    fmtOpen = !fmtOpen
  }
  function pickFmt(id) { fmt = id; fmtOpen = false; fmtBtn?.focus() }

  const formats = [
    // yt-dlp --audio-quality 0 = VBR V0, meist um 245 kbps — keine 320k CBR
    { id: 'mp3-best', label: 'MP3',  sub: 'Beste Qualität (VBR)'  },
    // Die Quelle (YouTube/Spotify) liefert hoechstens ~160 kbps — FLAC und WAV
    // verpacken das nur verlustfrei, besser klingt es dadurch nicht.
    { id: 'flac',     label: 'FLAC', sub: 'Groß, nicht besser als MP3' },
    { id: 'wav',      label: 'WAV',  sub: 'Sehr groß, nicht besser'    },
    { id: 'm4a',      label: 'M4A',  sub: 'AAC'                   },
    { id: 'opus',     label: 'Opus', sub: 'Effizient'             },
  ]

  // ── group by session ─────────────────────────────────────────────────────
  // Header item: id === session  (id doubles as session_id)
  // Track items: id !== session
  function buildGroups(dls) {
    const order = []
    const map   = new Map()
    for (const dl of dls) {
      const sid = dl.session ?? dl.id
      if (!map.has(sid)) { order.push(sid); map.set(sid, { session_id: sid, hdr: null, tracks: [] }) }
      const g = map.get(sid)
      if (dl.id === sid) g.hdr = dl      // header = item whose id equals its own session id
      else g.tracks.push(dl)
    }
    const all = order.map(sid => map.get(sid)).filter(g => g.hdr)
    all.sort((a, b) => (b.hdr.status === 'active' ? 1 : 0) - (a.hdr.status === 'active' ? 1 : 0))
    return all
  }

  const groups = $derived(buildGroups($downloads))

  // Auto-expand active sessions — but not those the user manually collapsed.
  $effect(() => {
    const newSids = groups
      .filter(g => g.hdr?.status === 'active' && g.tracks.length > 0
                && !untrack(() => manuallyCollapsed).has(g.session_id))
      .map(g => g.session_id)
    if (newSids.length === 0) return
    const cur = untrack(() => expanded)
    const next = new Set([...cur, ...newSids])
    if (next.size > cur.size) expanded = next
  })

  function toggleGroup(sid) {
    const s = new Set(expanded)
    if (s.has(sid)) {
      s.delete(sid)
      manuallyCollapsed = new Set([...manuallyCollapsed, sid])
    } else {
      s.add(sid)
      manuallyCollapsed = new Set([...manuallyCollapsed].filter(x => x !== sid))
    }
    expanded = s
  }

  // ── input / search ────────────────────────────────────────────────────────
  function isUrl(v) { return v.startsWith('http://') || v.startsWith('https://') }

  async function onUrlFocus() {
    if (input.trim()) return
    try {
      const text = (await navigator.clipboard.readText()).trim()
      if (isUrl(text) && /youtube|youtu\.be|soundcloud|spotify|tiktok|vimeo|twitch/i.test(text)) {
        input = text
      }
    } catch {}
  }

  function submit() {
    const val = input.trim()
    if (!val) return
    if (isUrl(val)) {
      send({ type: 'download_add', url: val, format: fmt })
      input = ''
    } else {
      searching = true
      searchResults.set(null)
      send({ type: 'search', query: val })
    }
  }

  function handleKey(e) { if (e.key === 'Enter') submit() }

  function onUrlPaste(e) {
    const text = (e.clipboardData?.getData('text') ?? '').trim()
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(l => isUrl(l))
    if (lines.length > 1) {
      e.preventDefault()
      for (const url of lines) send({ type: 'download_add', url, format: fmt })
      input = ''
    }
  }

  function pickResult(url) {
    send({ type: 'download_add', url, format: fmt })
    searchResults.set(null)
    searching = false
    input = ''
  }

  function closeSearch() { searchResults.set(null); searching = false }

  // ── download actions ───────────────────────────────────────────────────────
  function addToQueue(dl) {
    if (dl.path) send({ type: 'queue_add', path: dl.path, title: dl.title })
  }
  function insertNext(dl) {
    if (dl.path) send({ type: 'queue_insert_next', path: dl.path, title: dl.title })
  }
  function stopSession(sid) {
    send({ type: 'download_stop', session_id: sid })
  }
  function removeItem(dl) {
    send({ type: 'download_cancel', id: dl.id })
  }
  function removeGroup(g) {
    if (g.hdr) send({ type: 'download_cancel', id: g.hdr.id })
    g.tracks.forEach(dl => send({ type: 'download_cancel', id: dl.id }))
  }
  function openInExplorer(dl) { window.electron?.openPath(dl.path ?? null) }

  const hasDone = $derived(groups.some(g => g.hdr?.status !== 'active'))

  function clearCompleted() {
    send({ type: 'download_clear_done' })
  }

  // ── helpers ────────────────────────────────────────────────────────────────
  function fmtDur(sec) {
    if (!sec) return ''
    return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2,'0')}`
  }
  function fmtAbr(abr) { return abr ? `${Math.round(abr)} kbps` : '' }

  function trackLabel(s) {
    if (!s) return ''
    const max = 46
    return s.length > max ? s.slice(0, max) + '…' : s
  }

  $effect(() => { if ($searchResults) searching = false })
</script>

<svelte:window
  onclick={(e) => { if (fmtOpen && !e.target.closest('.fmt-wrap')) fmtOpen = false }}
  onkeydown={(e) => { if (fmtOpen && e.key === 'Escape') { fmtOpen = false; fmtBtn?.focus() } }} />

<div class="downloads">
  <!-- Eingabe -->
  <div class="top">
    <div class="input-row">
      <input class="field url-input" type="text"
        placeholder="Link oder Songname"
        aria-label="YouTube-Link, Playlist oder Songname"
        title="YouTube-Link, Playlist oder Songname — Enter"
        bind:value={input} onkeydown={handleKey} onfocus={onUrlFocus} onpaste={onUrlPaste} />
      <div class="fmt-wrap">
        <button class="btn fmt-btn" class:is-active={fmtOpen} bind:this={fmtBtn} onclick={toggleFmt}
                aria-haspopup="menu" aria-expanded={fmtOpen}
                title="Format: {formats.find(f => f.id === fmt)?.sub}">
          {formats.find(f => f.id === fmt)?.label}<i class="ti ti-chevron-down"></i>
        </button>
        {#if fmtOpen}
          <div class="ctx-menu fmt-menu" style={fmtPos} role="menu" aria-label="Format">
            {#each formats as f}
              <button role="menuitemradio" aria-checked={fmt === f.id} class:sel={fmt === f.id} onclick={() => pickFmt(f.id)}>
                {#if fmt === f.id}<i class="ti ti-check fmt-check" aria-hidden="true"></i>{:else}<span class="fmt-check" aria-hidden="true"></span>{/if}
                <span class="fmt-label">{f.label}</span>
                <span class="fmt-sub">{f.sub}</span>
              </button>
            {/each}
          </div>
        {/if}
      </div>
      <button class="btn btn-primary" onclick={submit}>
        {#if isUrl(input.trim())}<i class="ti ti-download"></i> Laden{:else}<i class="ti ti-search"></i> Suchen{/if}
      </button>
    </div>
  </div>

  {#if $videoCheckPending > 0}
    <div class="check-line" role="status"><i class="ti ti-refresh spinner"></i> Prüfe Link: schon in der Bibliothek? Gibt es eine Song-Version ohne Video-Intro?</div>
  {/if}

  <!-- Suchergebnisse -->
  {#if searching && !$searchResults}
    <div class="search-panel loading"><i class="ti ti-refresh spinner"></i> Suche läuft…</div>
  {:else if $searchResults}
    <div class="search-panel">
      <div class="search-header">
        <span class="search-title">Ergebnisse für <em>„{$searchResults.query}"</em></span>
        <span class="search-fmt">als {formats.find(f=>f.id===fmt)?.label}</span>
        <button class="btn btn-icon btn-sm" onclick={closeSearch} title="Schließen" aria-label="Suche schließen"><i class="ti ti-x"></i></button>
      </div>
      <div class="search-results">
        {#each $searchResults.results as r}
          <button class="result-row" onclick={() => pickResult(r.url)}>
            {#if r.thumbnail}
              <img class="result-thumb" src={r.thumbnail} alt=""
                   loading="lazy" onerror={(e) => e.currentTarget.style.display='none'} />
            {:else}
              <div class="result-thumb result-thumb-ph"></div>
            {/if}
            <div class="result-info">
              <span class="result-title">{#if r.kind === 'song'}<span class="song-chip" title="Studio-Version von YouTube Music: ohne Video-Intro und Pausen">Song</span>{/if}{r.title}</span>
              <span class="result-meta">
                <span class="result-artist">{r.uploader || (r.kind === 'song' && !$searchResults.final ? 'Künstler wird geladen…' : '')}</span>
                {#if r.abr}<span class="result-abr">· {fmtAbr(r.abr)}</span>{/if}
              </span>
            </div>
            <span class="result-dur">{fmtDur(r.duration)}</span>
            <i class="ti ti-download result-dl" aria-hidden="true"></i>
          </button>
        {/each}
        {#if $searchResults.results.length === 0}
          <div class="no-results">Keine Ergebnisse gefunden</div>
        {/if}
      </div>
    </div>
  {/if}

  <!-- Downloads -->
  <div class="list-header">
    {#if hasDone}
      <button class="btn btn-ghost btn-sm" onclick={clearCompleted}><i class="ti ti-trash"></i> Abgeschlossene leeren</button>
    {/if}
  </div>
  <div class="list">
    {#if groups.length === 0 && !searching && !$searchResults}
      <div class="empty">Link einfügen zum Herunterladen · Songname eingeben zum Suchen</div>
    {:else}
      {#each groups as g (g.session_id)}
        {@const s      = g.hdr}
        {@const isPlay = g.tracks.length > 0}
        {@const open   = expanded.has(g.session_id)}
        {@const active = s.status === 'active'}
        {@const tn     = s.track_n ?? 0}
        {@const tt     = s.track_total ?? 0}
        {@const pct    = s.progress ?? 0}

        <div class="group" class:active class:playlist={isPlay}>
          <div class="summary-row" class:active>
            {#if isPlay}
              <button class="btn btn-icon btn-sm" onclick={() => toggleGroup(g.session_id)}
                      title={open ? 'Einklappen' : 'Aufklappen'} aria-label={open ? 'Einklappen' : 'Aufklappen'} aria-expanded={open}>
                <i class="ti {open ? 'ti-chevron-down' : 'ti-chevron-right'}"></i>
              </button>
            {:else}
              <span class="status-ico {active ? 'is-active' : s.status}" aria-hidden="true">
                <i class="ti {active ? 'ti-download' : s.status === 'error' ? 'ti-alert-triangle' : 'ti-check'}"></i>
              </span>
            {/if}

            <div class="summary-center">
              <div class="summary-top">
                <span class="summary-label">
                  {#if s.title && !s.title.startsWith('http')}
                    {trackLabel(s.title)}
                  {:else if active}
                    <em class="muted">Lädt…</em>
                  {:else if s.session_label && !s.session_label.startsWith('http')}
                    {trackLabel(s.session_label)}
                  {:else}
                    <em class="muted">Abgeschlossen</em>
                  {/if}
                </span>
                {#if tt > 1}
                  <span class="summary-counter" class:active>
                    {tn}<span class="summary-sep">/</span>{tt}
                  </span>
                {:else}
                  <span class="summary-stat" class:active class:err={s.status === 'error'}>{s.status_text ?? ''}</span>
                {/if}
              </div>
              {#if tt > 1 || active}
                <div class="summary-bar-wrap">
                  <div class="summary-bar" class:active style="width:{pct}%"></div>
                </div>
              {/if}
            </div>

            <div class="summary-actions">
              {#if active}
                <button class="btn btn-sm btn-danger" onclick={() => stopSession(g.session_id)}><i class="ti ti-player-stop"></i> Stopp</button>
              {:else if !active && s.path}
                <button class="btn btn-sm" onclick={() => insertNext(s)} title="Als nächsten Titel einreihen"><i class="ti ti-player-track-next"></i> Nächster</button>
                <button class="btn btn-sm" onclick={() => addToQueue(s)} title="Ans Ende der Queue"><i class="ti ti-playlist-add"></i> Queue</button>
                <button class="btn btn-icon btn-sm" onclick={() => openInExplorer(s)}
                        title="Im Explorer zeigen" aria-label="Im Explorer zeigen"><i class="ti ti-folder"></i></button>
              {/if}
              {#if !active}
                <button class="btn btn-icon btn-sm" onclick={() => removeGroup(g)} title="Aus der Liste entfernen" aria-label="Aus der Liste entfernen"><i class="ti ti-x"></i></button>
              {/if}
            </div>
          </div>

          {#if isPlay && open}
            <div class="tracks">
              {#each g.tracks as dl (dl.id)}
                <div class="track-row {dl.status}"
                     ondblclick={() => openInExplorer(dl)} role="listitem"
                     title="Doppelklick: Im Explorer öffnen">
                  <span class="track-title">{dl.title}</span>

                  {#if dl.status === 'active'}
                    <div class="track-bar-wrap">
                      <div class="track-bar" style="width:{dl.progress ?? 0}%"></div>
                    </div>
                    <span class="track-pct">{dl.status_text ?? ''}</span>
                  {:else if dl.status === 'done'}
                    <i class="ti ti-check track-done" aria-label="Fertig"></i>
                    <div class="track-acts">
                      <button class="btn btn-icon btn-sm" onclick={(e) => { e.stopPropagation(); insertNext(dl) }} title="Als nächsten einreihen" aria-label="Als nächsten einreihen"><i class="ti ti-player-track-next"></i></button>
                      <button class="btn btn-icon btn-sm" onclick={(e) => { e.stopPropagation(); addToQueue(dl) }} title="Ans Ende der Queue" aria-label="Ans Ende der Queue"><i class="ti ti-playlist-add"></i></button>
                      <button class="btn btn-icon btn-sm" onclick={(e) => { e.stopPropagation(); openInExplorer(dl) }}
                              title="Im Explorer zeigen" aria-label="Im Explorer zeigen"><i class="ti ti-folder"></i></button>
                    </div>
                  {:else if dl.status === 'error'}
                    <i class="ti ti-alert-triangle track-err" title={dl.error_msg || dl.status_text || ''}></i>
                    {#if dl.error_msg || dl.status_text}
                      <span class="track-errmsg">{(dl.error_msg || dl.status_text || '').slice(0, 60)}</span>
                    {/if}
                    {#if dl.fix_tab}
                      <button class="btn btn-sm" onclick={(e) => { e.stopPropagation(); openSettings(dl.fix_tab) }}
                        title="Einstellungen öffnen">Einrichten</button>
                    {/if}
                    {#if dl.url}
                      <button class="btn btn-icon btn-sm" onclick={(e) => { e.stopPropagation(); send({ type: 'download_add', url: dl.url, format: dl.fmt || 'mp3-best' }) }}
                        title="Erneut versuchen" aria-label="Erneut versuchen"><i class="ti ti-refresh"></i></button>
                    {/if}
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/each}
    {/if}
  </div>
</div>

<style>
  .downloads { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .top { flex-shrink: 0; border-bottom: 1px solid var(--c-br1); padding: var(--sp-3); display: flex; flex-direction: column; gap: var(--sp-2); }
  .input-row { display: flex; gap: var(--sp-2); }
  .url-input { flex: 1; }
  /* Format: nur das gewaehlte sichtbar, Rest im Menue */
  .fmt-wrap { position: relative; flex-shrink: 0; }
  .fmt-btn { gap: 4px; font-weight: 700; min-width: 76px; justify-content: space-between; }
  .fmt-btn .ti { font-size: 14px; color: var(--c-tx4); }
  .fmt-menu { position: fixed; z-index: 500; min-width: 230px; }
  .fmt-menu button { display: flex; align-items: center; gap: var(--sp-2); }
  .fmt-check { width: 14px; font-size: 14px; color: var(--c-accent-tx); }
  .fmt-label { font-weight: 700; min-width: 38px; }
  .fmt-sub { font-weight: 400; color: var(--c-tx4); }
  .fmt-menu .sel .fmt-label { color: var(--c-accent-tx); }

  .check-line { display: flex; align-items: center; gap: var(--sp-2); flex-shrink: 0; padding: var(--sp-2) var(--sp-3); font-size: var(--fs-sm); color: var(--c-tx3); border-bottom: 1px solid var(--c-br1); }

  /* Suche */
  .search-panel { flex-shrink: 0; max-height: 300px; overflow-y: auto; background: var(--c-bg2); border-bottom: 1px solid var(--c-br1); }
  .search-panel.loading { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-4); color: var(--c-tx3); font-size: var(--fs-body); }
  .search-header {
    display: flex; align-items: center; gap: var(--sp-2); padding: 6px var(--sp-2) 6px var(--sp-3);
    position: sticky; top: 0; z-index: 1; background: var(--c-bg2); border-bottom: 1px solid var(--c-br1);
  }
  .search-title { flex: 1; font-size: var(--fs-sm); color: var(--c-tx3); }
  .search-title em { color: var(--c-tx1); font-style: normal; font-weight: 600; }
  .search-fmt { font-size: var(--fs-sm); color: var(--c-tx4); }
  .result-row {
    display: flex; align-items: center; gap: var(--sp-3); width: 100%;
    padding: var(--sp-2) var(--sp-3); background: none; border: none; border-bottom: 1px solid var(--c-br1);
    text-align: left; cursor: pointer; font-family: inherit;
  }
  .result-row:hover { background: var(--c-hover); }
  .result-row:hover .result-dl { color: var(--c-accent-tx); }
  .result-thumb { width: 72px; height: 40px; object-fit: cover; border-radius: var(--r-s); flex-shrink: 0; background: var(--c-bg5); }
  .result-thumb-ph { background: var(--c-bg5); }
  .result-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .song-chip {
    display: inline-block; margin-right: 6px; padding: 0 5px; border-radius: var(--r-s); vertical-align: 1px;
    font-size: var(--fs-cap); font-weight: 700; letter-spacing: .04em; line-height: 16px;
    color: var(--c-green-tx); background: var(--c-green-bg); border: 1px solid var(--c-green-br);
  }
  .result-title { font-size: var(--fs-body); color: var(--c-tx1); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .result-meta { display: flex; gap: 6px; align-items: center; min-width: 0; }
  .result-artist, .result-abr { font-size: var(--fs-sm); color: var(--c-tx4); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .result-dur { font-size: var(--fs-sm); color: var(--c-tx3); flex-shrink: 0; font-variant-numeric: tabular-nums; }
  .result-dl { font-size: 16px; color: var(--c-tx4); flex-shrink: 0; }
  .no-results { padding: var(--sp-5); text-align: center; color: var(--c-tx4); font-size: var(--fs-body); }

  .list-header { flex-shrink: 0; display: flex; justify-content: flex-end; padding: var(--sp-1) var(--sp-2) 0; }
  .list-header:empty { display: none; }
  .list { flex: 1; overflow-y: auto; }
  .empty { padding: 40px 20px; text-align: center; color: var(--c-tx4); font-size: var(--fs-body); }

  .group { border-bottom: 1px solid var(--c-br1); }
  .summary-row { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-2) var(--sp-2) var(--sp-2) var(--sp-3); }
  .summary-row.active { background: var(--c-act-bg); }
  .status-ico {
    width: 28px; height: 28px; flex-shrink: 0; border-radius: 50%;
    display: flex; align-items: center; justify-content: center; font-size: 15px;
    color: var(--c-green-tx); background: var(--c-green-bg);
  }
  .status-ico.is-active { color: var(--c-accent-tx); background: var(--c-bg5); }
  .status-ico.error { color: var(--c-red-tx); background: var(--c-red-bg); }
  .summary-center { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .summary-top { display: flex; align-items: baseline; gap: var(--sp-2); }
  .summary-label { flex: 1; min-width: 0; font-size: var(--fs-body); font-weight: 600; color: var(--c-tx1); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .muted { color: var(--c-tx4); font-weight: 400; }
  .summary-counter { flex-shrink: 0; font-size: var(--fs-h); font-weight: 700; color: var(--c-tx3); font-variant-numeric: tabular-nums; line-height: 1; }
  .summary-counter.active { color: var(--c-accent-tx); }
  .summary-sep { font-size: var(--fs-body); color: var(--c-tx4); margin: 0 2px; }
  .summary-stat { flex-shrink: 0; font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; }
  .summary-stat.active { color: var(--c-accent-tx); }
  .summary-stat.err { color: var(--c-red-tx); }
  .summary-bar-wrap { height: 4px; background: var(--c-br1); border-radius: 2px; overflow: hidden; }
  .summary-bar { height: 100%; background: var(--c-tx6); border-radius: 2px; transition: width .5s ease; }
  .summary-bar.active { background: var(--c-accent); }
  .summary-actions { display: flex; align-items: center; gap: var(--sp-1); flex-shrink: 0; }

  .track-row { display: flex; align-items: center; gap: var(--sp-2); min-height: 32px; padding: 2px var(--sp-2) 2px 48px; border-top: 1px solid var(--c-br1); }
  .track-row:hover { background: var(--c-hover); }
  .track-row.error { background: var(--c-red-bg); }
  .track-title { flex: 1; min-width: 0; font-size: var(--fs-sm); color: var(--c-tx2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .track-row.error .track-title { color: var(--c-red-tx); }
  .track-bar-wrap { width: 80px; flex-shrink: 0; height: 4px; background: var(--c-br2); border-radius: 2px; overflow: hidden; }
  .track-bar { height: 100%; background: var(--c-accent); transition: width .3s; }
  .track-pct { font-size: var(--fs-cap); color: var(--c-tx3); min-width: 36px; text-align: right; flex-shrink: 0; font-variant-numeric: tabular-nums; }
  .track-done { font-size: 15px; color: var(--c-green-tx); flex-shrink: 0; }
  .track-err { font-size: 15px; color: var(--c-red-tx); flex-shrink: 0; }
  .track-errmsg { flex: 1; min-width: 0; font-size: var(--fs-sm); color: var(--c-red-tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .track-acts { display: flex; gap: 2px; flex-shrink: 0; }

  .spinner { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
