<script>
  import { untrack } from 'svelte'
  import { downloads, searchResults, send } from '../stores/ws.js'

  let input     = $state('')
  let fmt       = $state('mp3-best')
  let searching = $state(false)
  let expanded         = $state(new Set())
  let manuallyCollapsed = $state(new Set())

  const formats = [
    { id: 'mp3-best', label: 'MP3',  sub: 'Beste Qualität (320k)' },
    { id: 'flac',     label: 'FLAC', sub: 'Verlustfrei'           },
    { id: 'wav',      label: 'WAV',  sub: 'Unkomprimiert'         },
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

<div class="downloads">
  <!-- Input bar -->
  <div class="top">
    <div class="input-row">
      <input class="url-input" type="text"
        placeholder="YouTube-Link / Playlist-URL  oder  Songname suchen — Enter"
        bind:value={input} onkeydown={handleKey} onfocus={onUrlFocus} onpaste={onUrlPaste} />
      <button class="icon-btn submit-btn" onclick={submit} title={isUrl(input.trim()) ? 'Herunterladen' : 'Suchen'}>
        {#if isUrl(input.trim())}
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="14" height="14">
            <line x1="8" y1="2" x2="8" y2="11"/>
            <polyline points="4,7 8,12 12,7"/>
            <line x1="3" y1="14" x2="13" y2="14"/>
          </svg>
        {:else}
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" width="14" height="14">
            <circle cx="6.5" cy="6.5" r="4.5"/>
            <line x1="10" y1="10" x2="13.5" y2="13.5"/>
          </svg>
        {/if}
      </button>
    </div>
    <div class="fmt-row">
      {#each formats as f}
        <button class="fmt-chip {fmt === f.id ? 'active' : ''}" onclick={() => fmt = f.id}>
          <span class="fmt-label">{f.label}</span>
          <span class="fmt-sub">{f.sub}</span>
        </button>
      {/each}
    </div>
  </div>

  <!-- Search results panel -->
  {#if searching && !$searchResults}
    <div class="search-panel loading"><span class="spinner">⟳</span> Suche läuft…</div>
  {:else if $searchResults}
    <div class="search-panel">
      <div class="search-header">
        <span class="search-title">Ergebnisse für <em>„{$searchResults.query}"</em></span>
        <span class="search-fmt">{formats.find(f=>f.id===fmt)?.label}</span>
        <button class="close-btn" onclick={closeSearch}>✕</button>
      </div>
      <div class="search-results">
        {#each $searchResults.results as r}
          <button class="result-row" onclick={() => pickResult(r.url)}>
            <div class="result-info">
              <span class="result-title">{r.title}</span>
              <span class="result-meta">
                <span class="result-artist">{r.uploader}</span>
                {#if r.abr}<span class="result-abr">· {fmtAbr(r.abr)}</span>{/if}
              </span>
            </div>
            <span class="result-dur">{fmtDur(r.duration)}</span>
            <span class="result-dl">↓</span>
          </button>
        {/each}
        {#if $searchResults.results.length === 0}
          <div class="no-results">Keine Ergebnisse gefunden</div>
        {/if}
      </div>
    </div>
  {/if}

  <!-- Download groups -->
  <div class="list-header">
    {#if hasDone}
      <button class="clear-btn" onclick={clearCompleted}>Abgeschlossene leeren</button>
    {/if}
  </div>
  <div class="list">
    {#if groups.length === 0 && !searching && !$searchResults}
      <div class="empty">URL eingeben zum Sofortdownload · Songname eingeben zum Suchen</div>
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

          <!-- ── Summary / progress header — always visible ── -->
          <div class="summary-row" class:active>

            <!-- Expand toggle (only if has track items) -->
            <button class="arrow-btn" class:open disabled={!isPlay}
                    onclick={() => isPlay && toggleGroup(g.session_id)}
                    title={isPlay ? (open ? 'Einklappen' : 'Aufklappen') : ''}>
              {isPlay ? (open ? '▾' : '▸') : '•'}
            </button>

            <!-- Label + progress bar -->
            <div class="summary-center">
              <div class="summary-top">
                <span class="summary-label">
                  {#if s.title && !s.title.startsWith('http')}
                    {trackLabel(s.title)}
                  {:else if active}
                    <em style="color:#3a5070">Lädt…</em>
                  {:else if s.session_label && !s.session_label.startsWith('http')}
                    {trackLabel(s.session_label)}
                  {:else}
                    <em style="color:#3a5070">Abgeschlossen</em>
                  {/if}
                </span>
                {#if tt > 1}
                  <span class="summary-counter" class:active>
                    {tn}<span class="summary-sep">/</span>{tt}
                  </span>
                {:else}
                  <span class="summary-stat" class:active>{s.status_text ?? ''}</span>
                {/if}
              </div>
              {#if tt > 1 || active}
                <div class="summary-bar-wrap">
                  <div class="summary-bar" class:active style="width:{pct}%"></div>
                  {#if tt > 1 && pct > 4}
                    <span class="summary-pct">{pct}%</span>
                  {/if}
                </div>
              {/if}
            </div>

            <!-- Action buttons -->
            <div class="summary-actions">
              {#if active}
                <button class="stop-btn" onclick={() => stopSession(g.session_id)}>⏹ Stopp</button>
              {:else if !active && s.path}
                <!-- Single track done -->
                <button class="act queue" onclick={() => addToQueue(s)}>+ Queue</button>
                <button class="act next" onclick={() => insertNext(s)}>▶ Nächster</button>
              {/if}
              {#if !active}
                <button class="rm-group" onclick={() => removeGroup(g)} title="Entfernen">✕</button>
              {/if}
            </div>
          </div>

          <!-- ── Track rows (dropdown) ── -->
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
                    <span class="track-done">✓</span>
                    <div class="track-acts">
                      <button class="act-sm queue" onclick={(e) => { e.stopPropagation(); addToQueue(dl) }}>+ Q</button>
                      <button class="act-sm next" onclick={(e) => { e.stopPropagation(); insertNext(dl) }}>▶</button>
                    </div>
                  {:else if dl.status === 'error'}
                    <span class="track-err" title={dl.error_msg || dl.status_text || ''}>✗</span>
                    {#if dl.error_msg || dl.status_text}
                      <span class="track-errmsg">{(dl.error_msg || dl.status_text || '').slice(0, 48)}</span>
                    {/if}
                    {#if dl.url}
                      <button class="act-sm retry" onclick={(e) => { e.stopPropagation(); send({ type: 'download_add', url: dl.url, format: dl.fmt || 'mp3-best' }) }}
                        title="Erneut versuchen">↺</button>
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
  .downloads { flex:1; display:flex; flex-direction:column; overflow:hidden; }
  .top { flex-shrink:0; border-bottom:1px solid #0e1828; }

  /* Input */
  .input-row { display:flex; gap:6px; padding:10px 12px 6px; }
  .url-input {
    flex:1; background:#0c1220; border:1px solid #1a2838; border-radius:4px;
    color:#c8d8f0; font-size:12px; padding:6px 12px; outline:none;
    transition:border-color .15s;
  }
  .url-input:focus { border-color:#e07800; }
  .icon-btn {
    background:#0e1828; border:1px solid #1a2838; border-radius:4px;
    color:#4a6080; font-size:13px; width:34px; cursor:pointer;
    transition:border-color .15s, color .15s;
  }
  .icon-btn:hover { border-color:#e07800; color:#e07800; }
  .submit-btn { display:flex; align-items:center; justify-content:center; }

  .fmt-row { display:flex; gap:5px; padding:0 12px 8px; flex-wrap:wrap; }
  .fmt-chip {
    display:flex; align-items:baseline; gap:4px; background:#0c1220;
    border:1px solid #1a2838; border-radius:3px; color:#4a6080;
    padding:3px 9px; cursor:pointer; font-size:10px; white-space:nowrap;
    transition:border-color .12s, color .12s, background .12s;
  }
  .fmt-chip:hover  { border-color:#3a5070; color:#8aa0c0; }
  .fmt-chip.active { border-color:#e07800; color:#e07800; background:#e0780012; }
  .fmt-label { font-weight:700; letter-spacing:.03em; }
  .fmt-sub   { font-size:9px; opacity:.6; }
  .fmt-chip.active .fmt-sub { opacity:.85; }

  /* Search */
  .search-panel {
    flex-shrink:0; border-bottom:1px solid #0e1828; background:#060a12;
    max-height:280px; overflow-y:auto;
  }
  .search-panel.loading {
    display:flex; align-items:center; gap:10px;
    padding:16px; color:#3a5070; font-size:12px;
  }
  .search-header {
    display:flex; align-items:center; gap:8px; padding:8px 14px;
    border-bottom:1px solid #0e1828; position:sticky; top:0;
    background:#060a12; z-index:1;
  }
  .search-title { font-size:11px; color:#4a6080; flex:1; }
  .search-title em { color:#8aaac8; font-style:normal; }
  .search-fmt   { font-size:10px; color:#2a3a54; }
  .close-btn {
    background:none; border:none; color:#2a3a54; font-size:12px;
    cursor:pointer; padding:2px 6px; transition:color .1s;
  }
  .close-btn:hover { color:#c04040; }
  .result-row {
    display:flex; align-items:center; gap:10px; width:100%;
    padding:8px 14px; background:none; border:none; border-bottom:1px solid #080d16;
    text-align:left; cursor:pointer; transition:background .1s;
  }
  .result-row:hover { background:#0c1828; }
  .result-row:hover .result-dl { color:#e07800; }
  .result-info   { flex:1; min-width:0; display:flex; flex-direction:column; gap:2px; }
  .result-title  { font-size:12px; color:#c0d0e8; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .result-meta   { display:flex; gap:6px; align-items:center; }
  .result-artist { font-size:10px; color:#3a5070; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .result-abr    { font-size:10px; color:#2a4060; }
  .result-dur    { font-size:11px; color:#2a3a54; flex-shrink:0; font-variant-numeric:tabular-nums; }
  .result-dl     { font-size:14px; color:#2a3a54; width:18px; text-align:center; flex-shrink:0; transition:color .1s; }
  .no-results    { padding:20px; text-align:center; color:#2a3a54; font-size:12px; }

  /* List header + clear button */
  .list-header { flex-shrink:0; display:flex; justify-content:flex-end; padding:4px 10px; min-height:0; }
  .list-header:empty { display:none; }
  .clear-btn {
    background:none; border:1px solid #1a2838; border-radius:3px;
    color:#2a4060; font-size:10px; padding:3px 10px; cursor:pointer;
    transition: color .1s, border-color .1s;
  }
  .clear-btn:hover { color:#c04040; border-color:#502020; }

  /* Groups */
  .list { flex:1; overflow-y:auto; }
  .empty { padding:40px 20px; text-align:center; color:#2a3a54; font-size:12px; }

  .group { border-bottom:1px solid #0a0e16; }

  /* ── Summary row ── */
  .summary-row {
    display:flex; align-items:center; gap:8px;
    padding:10px 12px; background:#080e18;
    transition:background .1s;
  }
  .summary-row.active { background:#0a1020; }

  .arrow-btn {
    width:18px; flex-shrink:0; background:none; border:none;
    color:#2a4060; font-size:10px; cursor:pointer; padding:0;
    transition:color .1s;
  }
  .arrow-btn:not([disabled]):hover { color:#7aaae0; }
  .arrow-btn[disabled] { cursor:default; }

  .summary-center { flex:1; min-width:0; display:flex; flex-direction:column; gap:6px; }

  .summary-top { display:flex; align-items:baseline; gap:8px; }

  .summary-label {
    flex:1; min-width:0; font-size:12px; font-weight:600;
    color:#8aaac8; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .summary-label.url-placeholder { color:#3a5070; font-style:italic; font-weight:400; }
  .summary-row:not(.active) .summary-label { color:#4a6a5a; }

  .summary-counter {
    flex-shrink:0; font-size:18px; font-weight:700; color:#2a4060;
    font-variant-numeric:tabular-nums; letter-spacing:-.02em;
    line-height:1;
  }
  .summary-counter.active { color:#e07800; }
  .summary-sep { font-size:13px; color:#2a3a54; margin:0 1px; }

  .summary-stat { font-size:10px; color:#3a5070; flex-shrink:0; }
  .summary-stat.active { color:#e07800; }

  .summary-bar-wrap {
    height:4px; background:#0e1828; border-radius:2px;
    overflow:hidden; position:relative;
  }
  .summary-bar {
    height:100%; background:#2a4060; border-radius:2px;
    transition:width .5s ease;
  }
  .summary-bar.active { background:#e07800; }
  .summary-pct {
    position:absolute; right:4px; top:-1px;
    font-size:9px; color:#4a6080; pointer-events:none;
  }

  .summary-actions { display:flex; align-items:center; gap:4px; flex-shrink:0; }

  .stop-btn {
    border:1px solid #702020; border-radius:4px; background:none;
    color:#c04040; font-size:10px; padding:4px 10px; cursor:pointer;
    font-weight:600; transition:all .15s;
  }
  .stop-btn:hover { background:#1a0808; border-color:#c04040; color:#e06060; }

  .act {
    border:1px solid #1a2838; border-radius:4px; background:none;
    font-size:10px; padding:3px 8px; cursor:pointer; white-space:nowrap;
    transition:border-color .12s, color .12s, background .12s;
  }
  .act.queue { color:#4a7aaa; border-color:#1a3050; }
  .act.queue:hover { color:#7aaadd; border-color:#4a7aaa; background:#0a1828; }
  .act.next  { color:#7a8aa4; border-color:#2a3a50; }
  .act.next:hover { color:#b0c0d8; border-color:#5a7090; background:#0e1828; }

  .rm-group {
    background:none; border:1px solid transparent; border-radius:4px;
    color:#1e2e42; font-size:11px; cursor:pointer; padding:3px 6px;
    transition:color .1s, border-color .1s, background .1s;
  }
  .rm-group:hover { color:#c04040; border-color:#502020; background:#1a0808; }

  /* ── Track rows ── */
  .tracks { }

  .track-row {
    display:flex; align-items:center; gap:8px;
    padding:6px 12px 6px 38px;
    border-top:1px solid #080d14;
    cursor:default; transition:background .1s;
  }
  .track-row:hover { background:#0a1018; }
  .track-row.error { background:#0d0505; }

  .track-title {
    flex:1; min-width:0; font-size:11px; color:#5a7a9a;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  .track-row.done  .track-title { color:#4a7a5a; }
  .track-row.error .track-title { color:#7a4a4a; }
  .track-row.active .track-title { color:#8aaac8; }

  .track-bar-wrap { width:80px; flex-shrink:0; height:2px; background:#1a2838; border-radius:1px; overflow:hidden; }
  .track-bar { height:100%; background:#e07800; border-radius:1px; transition:width .3s; }
  .track-pct { font-size:9px; color:#4a6080; width:28px; text-align:right; flex-shrink:0; }

  .track-done   { font-size:11px; color:#2a6a4a; flex-shrink:0; }
  .track-err    { font-size:11px; color:#7a2a2a; flex-shrink:0; }
  .track-errmsg { font-size:10px; color:#5a3030; flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .act-sm.retry { color:#8a4a20; }
  .act-sm.retry:hover { color:#e07800; border-color:#6a3a10; }

  .track-acts { display:flex; gap:3px; flex-shrink:0; }
  .act-sm {
    border:1px solid #1a2838; border-radius:3px; background:none;
    font-size:9px; padding:2px 6px; cursor:pointer;
    transition:border-color .12s, color .12s;
  }
  .act-sm.queue { color:#3a6a9a; }
  .act-sm.queue:hover { color:#7aaadd; border-color:#4a7aaa; }
  .act-sm.next  { color:#5a6a84; }
  .act-sm.next:hover  { color:#a0b0c8; border-color:#5a7090; }

  .spinner { display:inline-block; animation:spin 1s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
</style>
