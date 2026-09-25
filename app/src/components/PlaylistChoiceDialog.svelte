<script>
  import { playlistChoice, send } from '../stores/ws.js'

  // Ein Link wie watch?v=…&list=… meint meist nur den einen Titel. Statt das
  // zu raten, wird hier gefragt — vorher lief still die ganze Playlist durch.
  const choice = $derived($playlistChoice)

  function pick(mode) {
    send({ type: 'download_add', url: choice.url,
           format: choice.format, playlist_choice: mode })
    playlistChoice.set(null)
  }

  function cancel() {
    playlistChoice.set(null)
  }

  function onkey(e) {
    if (e.key === 'Escape') cancel()
  }
</script>

<svelte:window onkeydown={onkey} />

{#if choice}
  <div class="dlg-overlay" onclick={cancel} role="presentation">
    <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Link gehört zu einer Playlist">

      <div class="hdr">
        <span class="dlg-title">Link gehört zu einer Playlist</span>
        <button class="btn btn-icon btn-sm close-btn" onclick={cancel} title="Abbrechen" aria-label="Abbrechen"><i class="ti ti-x"></i></button>
      </div>

      {#if choice.pending}
        <div class="loading"><i class="ti ti-refresh spin"></i> Playlist wird geprüft…</div>
      {:else}
        <div class="body">
          <button class="opt" onclick={() => pick('single')}>
            <i class="ti ti-music opt-ico" aria-hidden="true"></i>
            <span class="opt-text">
              <span class="opt-lbl">Nur dieser Titel</span>
              <span class="opt-sub" title={choice.track_title}>{choice.track_title || 'Einzelner Titel'}</span>
            </span>
          </button>
          <button class="opt" onclick={() => pick('all')}>
            <i class="ti ti-list opt-ico" aria-hidden="true"></i>
            <span class="opt-text">
              <span class="opt-lbl">Ganze Playlist</span>
              <span class="opt-sub" title={choice.playlist_title}>{choice.playlist_title || 'Playlist'} · {choice.count} Titel</span>
            </span>
          </button>
        </div>

        <div class="dlg-actions foot">
          <span class="dlg-hint">Die Playlist landet in einem eigenen Unterordner.</span>
          <button class="btn" onclick={cancel}>Abbrechen</button>
        </div>
      {/if}

    </div>
  </div>
{/if}

<style>
  .panel { width: 480px; }
  .hdr { display: flex; align-items: center; gap: var(--sp-2); }
  .close-btn { margin-left: auto; }
  .loading { display: flex; align-items: center; justify-content: center; gap: var(--sp-2); padding: var(--sp-5); font-size: var(--fs-body); color: var(--c-tx3); }
  .spin { animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .body { display: flex; flex-direction: column; gap: var(--sp-2); }
  .opt {
    display: flex; align-items: center; gap: var(--sp-3); min-height: 56px;
    padding: var(--sp-3) var(--sp-4); border-radius: var(--r-m);
    border: 1px solid var(--c-br3); background: var(--c-bg5);
    cursor: pointer; text-align: left; font-family: inherit;
  }
  .opt:hover { border-color: var(--c-accent); background: var(--c-act-bg); }
  .opt-ico { font-size: 20px; color: var(--c-tx3); flex-shrink: 0; }
  .opt:hover .opt-ico { color: var(--c-accent-tx); }
  .opt-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .opt-lbl { font-size: var(--fs-lg); font-weight: 600; color: var(--c-tx1); }
  .opt-sub { font-size: var(--fs-sm); color: var(--c-tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .foot { align-items: center; }
  .foot .dlg-hint { flex: 1; }
</style>
