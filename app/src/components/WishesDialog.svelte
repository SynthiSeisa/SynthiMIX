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

<div class="overlay" onclick={onclose} role="presentation">
  <div class="panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">

    <div class="hdr">
      <span class="title">MUSIKWÜNSCHE</span>
      <span class="cnt">{sorted.length}</span>
      <button class="close-btn" onclick={onclose} title="Schließen">✕</button>
    </div>

    <div class="body">
      {#if !sorted.length}
        <div class="empty">
          Noch keine Wünsche.<br>
          Gäste erreichen die Seite über den QR-Code in den Einstellungen unter Remote.
        </div>
      {:else}
        {#each sorted as w (w.id)}
          <div class="w-row">
            <div class="w-info">
              <div class="w-title" title={w.title}>
                {w.title}
                {#if (w.count ?? 1) > 1}<span class="w-cnt">{w.count}×</span>{/if}
              </div>
              <div class="w-meta">
                <span class="w-state s-{w.status}">{LABEL[w.status] ?? w.status}</span>
                {#if w.error}<span class="w-err" title={w.error}>{w.error}</span>{/if}
                <span class="w-time">{uhr(w.created_at)}</span>
              </div>
            </div>

            <div class="w-btns">
              {#if w.status === 'bereit'}
                <button class="w-b next" onclick={() => send({ type: 'wish_accept', id: w.id, as_next: true })}
                        title="Direkt als nächsten Titel einreihen">▶ Nächster</button>
                <button class="w-b ok" onclick={() => send({ type: 'wish_accept', id: w.id })}
                        title="Ans Ende der Warteschlange">+ Queue</button>
              {/if}
              <button class="w-b no" onclick={() => send({ type: 'wish_reject', id: w.id })}
                      title="Ablehnen — die heruntergeladene Datei wird gelöscht">✕</button>
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
    background: var(--c-bg3); border: 1px solid var(--c-br2); border-radius: 6px;
    width: 560px; max-height: 78vh; display: flex; flex-direction: column;
    box-shadow: 0 20px 60px rgba(0,0,0,.9);
  }

  .hdr {
    display: flex; align-items: center; gap: 8px;
    padding: 11px 16px; border-bottom: 1px solid var(--c-br1); flex-shrink: 0;
  }
  .title { font-size: 10px; font-weight: 700; letter-spacing: 1.8px; color: var(--c-tx6); }
  .cnt   { font-size: 10px; color: var(--c-accent); }
  .close-btn {
    margin-left: auto; background: none; border: none; color: var(--c-tx7);
    font-size: 12px; cursor: pointer; padding: 4px 8px; border-radius: 3px;
  }
  .close-btn:hover { color: var(--c-red); }

  .body { flex: 1; overflow-y: auto; }
  .empty {
    padding: 34px 24px; text-align: center;
    font-size: 12px; color: var(--c-tx6); line-height: 1.7;
  }

  .w-row {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 16px; border-bottom: 1px solid var(--c-br1);
  }
  .w-info  { flex: 1; min-width: 0; }
  .w-title {
    font-size: 12px; color: var(--c-tx2);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .w-cnt   { color: var(--c-accent); font-size: 11px; margin-left: 6px; }
  .w-meta  { display: flex; align-items: center; gap: 8px; margin-top: 3px; }
  .w-state { font-size: 10px; }
  .s-bereit     { color: var(--c-green-tx); }
  .s-laedt,
  .s-analysiert { color: var(--c-tx5); }
  .s-neu        { color: var(--c-tx6); }
  .s-fehler     { color: var(--c-red-tx); }
  .w-err {
    font-size: 10px; color: var(--c-red-tx);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px;
  }
  .w-time { font-size: 10px; color: var(--c-tx7); margin-left: auto; }

  .w-btns { display: flex; gap: 6px; flex-shrink: 0; }
  .w-b {
    background: none; border: 1px solid var(--c-br2); border-radius: 3px;
    color: var(--c-tx5); font-size: 10px; padding: 4px 9px; cursor: pointer;
    font-family: inherit; white-space: nowrap;
  }
  .w-b.ok:hover   { color: var(--c-green-tx); border-color: var(--c-green-br); }
  .w-b.next:hover { color: var(--c-accent);   border-color: var(--c-accent); }
  .w-b.no:hover   { color: var(--c-red-tx);   border-color: var(--c-red-br); }
</style>
