<script>
  import { dupeChoices, revealPath, send } from '../stores/ws.js'

  // Der Link fuehrt zu einem Song, den es in der Bibliothek schon gibt (auch mit
  // kleinen Abweichungen im Namen). Trotzdem laden, oder zur Datei springen.
  // Mehrere Links nacheinander stehen in einer Warteliste.
  const choice = $derived($dupeChoices[0] ?? null)

  function fmt(sec) {
    if (!sec) return ''
    return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
  }
  function delta(m) {
    const a = choice?.video?.duration, b = m.duration_sec
    if (!a || !b) return null
    return Math.round(a - b)
  }

  function next() { dupeChoices.update(l => l.slice(1)) }

  function downloadAnyway() {
    send({ type: 'download_add', url: choice.url, format: choice.format, dupe_checked: true })
    next()
  }
  function reveal(m) {
    revealPath.set(m.path)
    next()
  }
  function onkey(e) { if (choice && e.key === 'Escape') next() }

  const label = $derived(choice
    ? ((choice.video.artist && !choice.video.title.includes(' - ') ? choice.video.artist + ' – ' : '') + choice.video.title)
    : '')
</script>

<svelte:window onkeydown={onkey} />

{#if choice}
  <div class="dlg-overlay" onclick={next} role="presentation">
    <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Schon in der Bibliothek">

      <div class="hdr">
        <span class="dlg-title">Schon in der Bibliothek</span>
        {#if $dupeChoices.length > 1}<span class="count">noch {$dupeChoices.length - 1}</span>{/if}
        <button class="btn btn-icon btn-sm close-btn" onclick={next} title="Nichts laden" aria-label="Nichts laden"><i class="ti ti-x"></i></button>
      </div>

      <div class="new">
        <span class="eyebrow">Zum Laden</span>
        <span class="new-title" title={label}>{label}</span>
        <span class="new-meta">{fmt(choice.video.duration)}{choice.video.uploader ? ' · ' + choice.video.uploader : ''}</span>
      </div>

      <div class="dlg-hint">
        {choice.matches.length === 1 ? 'Diese Datei ist' : `Diese ${choice.matches.length} Dateien sind`} wahrscheinlich derselbe Song:
      </div>

      <div class="list">
        {#each choice.matches as m}
          {@const d = delta(m)}
          <div class="match">
            <i class="ti ti-music m-ico" aria-hidden="true"></i>
            <span class="m-text">
              <span class="m-title" title={m.path}>{m.title}</span>
              <span class="m-meta">
                {fmt(m.duration_sec)}{#if d !== null && d !== 0}<span class="m-delta"> ({d > 0 ? 'neu ' + d + ' s länger' : 'neu ' + (-d) + ' s kürzer'})</span>{/if}
                {#if m.bitrate_kbps} · {m.bitrate_kbps} kbps{/if}
                {#if m.folder} · {m.folder}{/if}
              </span>
            </span>
            <button class="btn btn-sm" onclick={() => reveal(m)} title="In der Bibliothek anzeigen und markieren">
              <i class="ti ti-search"></i> Zeigen
            </button>
          </div>
        {/each}
      </div>

      <div class="dlg-actions">
        <button class="btn" onclick={next}>Nichts laden</button>
        <button class="btn btn-primary" onclick={downloadAnyway}><i class="ti ti-download"></i> Trotzdem laden</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .panel { width: 540px; }
  .hdr { display: flex; align-items: center; gap: var(--sp-2); }
  .count { font-size: var(--fs-sm); color: var(--c-tx3); }
  .close-btn { margin-left: auto; }
  .new { display: grid; grid-template-columns: auto 1fr; gap: 2px var(--sp-3); align-items: baseline;
         padding: var(--sp-3); border-radius: var(--r-m); background: var(--c-bg5); border: 1px solid var(--c-br2); }
  .new-title { font-size: var(--fs-body); font-weight: 600; color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .new-meta { grid-column: 2; font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; }
  .list { display: flex; flex-direction: column; gap: var(--sp-2); max-height: 260px; overflow-y: auto; }
  .match { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-2) var(--sp-3);
           border-radius: var(--r-m); border: 1px solid var(--c-br3); background: var(--c-act-bg); }
  .m-ico { font-size: 18px; color: var(--c-accent-tx); flex-shrink: 0; }
  .m-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .m-title { font-size: var(--fs-body); font-weight: 600; color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .m-meta { font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .m-delta { color: var(--c-warn-tx); }
</style>
