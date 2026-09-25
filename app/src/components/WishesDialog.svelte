<script>
  import { wishes, send } from '../stores/ws.js'

  let { onclose } = $props()

  // Reihenfolge: was bereit ist zuerst, danach das meistgewünschte.
  const sorted = $derived.by(() => {
    const rang = { bereit: 0, analysiert: 1, laedt: 2, neu: 3, fehler: 4 }
    return [...$wishes].sort((a, b) =>
      (rang[a.status] ?? 9) - (rang[b.status] ?? 9) ||
      (b.count ?? 1) - (a.count ?? 1) ||
      (a.created_at ?? 0) - (b.created_at ?? 0))
  })

  const LABEL = {
    neu:        'wartet',
    laedt:      'lädt…',
    analysiert: 'analysiert…',
    bereit:     'bereit',
    fehler:     'Fehler',
  }

  function uhr(ts) {
    if (!ts) return ''
    const d = new Date(ts * 1000)
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }

  function onkey(e) { if (e.key === 'Escape') onclose() }
</script>

<svelte:window onkeydown={onkey} />

<div class="dlg-overlay" onclick={onclose} role="presentation">
  <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Musikwünsche">

    <div class="hdr">
      <span class="dlg-title">Musikwünsche</span>
      <span class="cnt">{sorted.length}</span>
      <button class="btn btn-icon btn-sm close-btn" onclick={onclose} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
    </div>

    <div class="body">
      {#if !sorted.length}
        <div class="empty">
          Noch keine Wünsche.<br>
          Gäste erreichen die Seite über den QR-Code unter Einstellungen → Remote.
        </div>
      {:else}
        {#each sorted as w (w.id)}
          <div class="w-row">
            <div class="w-info">
              <div class="w-title" title={w.title}>
                {w.title}
                {#if (w.count ?? 1) > 1}<span class="w-cnt">{w.count}× gewünscht</span>{/if}
              </div>
              <div class="w-meta">
                <span class="w-state s-{w.status}">
                  <i class="ti {w.status === 'bereit' ? 'ti-check' : w.status === 'fehler' ? 'ti-alert-triangle' : 'ti-download'}"></i>
                  {LABEL[w.status] ?? w.status}
                </span>
                {#if w.error}<span class="w-err" title={w.error}>{w.error}</span>{/if}
                <span class="w-time">{uhr(w.created_at)}</span>
              </div>
            </div>

            <div class="w-btns">
              {#if w.status === 'bereit'}
                <button class="btn btn-sm btn-primary" onclick={() => send({ type: 'wish_accept', id: w.id, as_next: true })}
                        title="Direkt als nächsten Titel einreihen"><i class="ti ti-player-track-next"></i> Nächster</button>
                <button class="btn btn-sm" onclick={() => send({ type: 'wish_accept', id: w.id })}
                        title="Ans Ende der Warteschlange"><i class="ti ti-playlist-add"></i> Queue</button>
              {/if}
              <button class="btn btn-icon btn-sm btn-danger" onclick={() => send({ type: 'wish_reject', id: w.id })}
                      title="Ablehnen — nur eine eigens dafür geladene Datei wandert in den Papierkorb"
                      aria-label="Wunsch ablehnen"><i class="ti ti-x"></i></button>
            </div>
          </div>
        {/each}
      {/if}
    </div>

  </div>
</div>

<style>
  .panel { width: 600px; max-height: 78vh; padding: 0; gap: 0; }
  .hdr { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); flex-shrink: 0; }
  .cnt {
    font-size: var(--fs-cap); font-weight: 700; color: var(--c-on-accent); background: var(--c-accent);
    border-radius: 10px; padding: 1px 7px; font-variant-numeric: tabular-nums;
  }
  .close-btn { margin-left: auto; }
  .body { flex: 1; overflow-y: auto; }
  .empty { padding: 40px var(--sp-5); text-align: center; font-size: var(--fs-body); color: var(--c-tx3); line-height: 1.6; }

  .w-row { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); }
  .w-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .w-title { font-size: var(--fs-lg); font-weight: 600; color: var(--c-tx1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .w-cnt { margin-left: var(--sp-2); font-size: var(--fs-sm); font-weight: 600; color: var(--c-accent-tx); }
  .w-meta { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-sm); }
  .w-state { display: inline-flex; align-items: center; gap: 4px; font-weight: 600; }
  .s-bereit { color: var(--c-green-tx); }
  .s-laedt, .s-analysiert, .s-neu { color: var(--c-tx3); }
  .s-fehler { color: var(--c-red-tx); }
  .w-err { color: var(--c-red-tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 220px; }
  .w-time { margin-left: auto; color: var(--c-tx4); font-variant-numeric: tabular-nums; }
  .w-btns { display: flex; gap: var(--sp-1); flex-shrink: 0; }
</style>
