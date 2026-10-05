<script>
  // Klare Meldung, wenn der Dienst (Backend) nicht laeuft oder die Festplatte
  // mit den Daten weg ist. Vorher zeigte nur ein kleiner roter Punkt, dass
  // etwas nicht stimmt — Bibliothek leer, nichts ging, ohne Erklaerung.
  import { connected } from '../stores/ws.js'
  import { noticeFor } from '../lib/backendNotice.js'

  const api = () => (typeof window !== 'undefined' ? window.electron : null)
  let status = $state(null)          // Antwort von backendStatus()
  let downFor = $state(0)            // Sekunden ohne Verbindung
  let details = $state(false)
  let copied = $state(false)
  let busy = $state(false)

  // Sekunden ohne Verbindung zaehlen
  $effect(() => {
    if ($connected) { downFor = 0; details = false; return }
    const t0 = Date.now()
    const iv = setInterval(() => { downFor = Math.round((Date.now() - t0) / 1000) }, 1000)
    return () => clearInterval(iv)
  })
  // Zustand abfragen: ohne Verbindung alle 3 s, sonst alle 15 s (Platte abgezogen?)
  $effect(() => {
    if (!api()?.backendStatus) return
    const poll = () => api().backendStatus().then(s => { status = s }).catch(() => {})
    poll()
    const iv = setInterval(poll, $connected ? 15000 : 3000)
    return () => clearInterval(iv)
  })

  const note = $derived(noticeFor({ connected: $connected, downFor, status }))

  async function retry() {
    busy = true
    try { await api()?.backendRestart?.() } catch {}
    setTimeout(() => { busy = false }, 2500)
  }
  async function copyLog() {
    try { await navigator.clipboard.writeText((status?.log ?? []).join('\n')); copied = true; setTimeout(() => { copied = false }, 2500) } catch {}
  }
</script>

{#if note}
  <div class="bn" class:err={note.level === 'error'} role="alert">
    <i class="ti {note.level === 'error' ? 'ti-alert-triangle' : 'ti-refresh spin'}"></i>
    <div class="bn-body">
      <div class="bn-title">{note.title}</div>
      <div class="bn-text">{note.text}</div>
      {#if details && status?.log?.length}
        <pre class="bn-log">{status.log.join('\n')}</pre>
      {/if}
      {#if note.level === 'error'}
        <div class="bn-acts">
          {#if note.retry && api()?.backendRestart}
            <button class="btn btn-sm btn-primary" onclick={retry} disabled={busy}><i class="ti ti-refresh" class:spin={busy}></i> Erneut versuchen</button>
          {/if}
          {#if status?.log?.length}
            <button class="btn btn-sm" onclick={() => details = !details}>{details ? 'Details ausblenden' : 'Details'}</button>
            {#if details}<button class="btn btn-sm" onclick={copyLog}><i class="ti ti-copy"></i> {copied ? 'Kopiert' : 'Kopieren'}</button>{/if}
          {/if}
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .bn {
    position: fixed; left: 50%; top: 46px; transform: translateX(-50%); z-index: 950;
    width: 560px; max-width: calc(100vw - 32px);
    display: flex; gap: 12px; align-items: flex-start; padding: 12px 14px;
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-left: 4px solid var(--c-accent);
    border-radius: var(--r-m); box-shadow: 0 10px 32px rgba(0, 0, 0, .45);
  }
  .bn.err { border-left-color: var(--c-red-tx, #e5484d); }
  .bn > .ti { font-size: 20px; margin-top: 2px; color: var(--c-accent-tx); flex-shrink: 0; }
  .bn.err > .ti { color: var(--c-red-tx, #e5484d); }
  .bn-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
  .bn-title { font-weight: 700; color: var(--c-tx1); }
  .bn-text { font-size: var(--fs-sm); color: var(--c-tx2); line-height: 1.45; overflow-wrap: anywhere; }
  .bn-acts { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
  .bn-log {
    margin: 4px 0 0; padding: 8px; max-height: 180px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere;
    font: 11px/1.4 ui-monospace, Consolas, monospace; color: var(--c-tx3); background: var(--c-bg3); border-radius: var(--r-s);
  }
</style>
