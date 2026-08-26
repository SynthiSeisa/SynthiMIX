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
  <div class="overlay" onclick={cancel} role="presentation">
    <div class="panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">

      <div class="hdr">
        <span class="title">LINK GEHÖRT ZU EINER PLAYLIST</span>
        <button class="close-btn" onclick={cancel} title="Abbrechen">✕</button>
      </div>

      {#if choice.pending}
        <div class="loading">Playlist wird geprüft…</div>
      {:else}
        <div class="body">
          <button class="opt" onclick={() => pick('single')}>
            <span class="opt-lbl">Nur dieser Titel</span>
            <span class="opt-sub" title={choice.track_title}>
              {choice.track_title || 'Einzelner Titel'}
            </span>
          </button>

          <button class="opt" onclick={() => pick('all')}>
            <span class="opt-lbl">Ganze Playlist</span>
            <span class="opt-sub" title={choice.playlist_title}>
              {choice.playlist_title || 'Playlist'} · {choice.count} Titel
            </span>
          </button>
        </div>

        <div class="foot">
          <span class="hint">Die Playlist landet in einem eigenen Unterordner.</span>
          <button class="cancel-btn" onclick={cancel}>Abbrechen</button>
        </div>
      {/if}

    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 2000;
    background: rgba(0,0,0,.75); backdrop-filter: blur(3px);
    display: flex; align-items: center; justify-content: center;
  }
  .panel {
    background: var(--c-bg3); border: 1px solid var(--c-br2); border-radius: 6px;
    width: 460px; display: flex; flex-direction: column;
    box-shadow: 0 20px 60px rgba(0,0,0,.9);
  }

  .hdr {
    display: flex; align-items: center; padding: 11px 16px;
    border-bottom: 1px solid var(--c-br1); flex-shrink: 0;
  }
  .title {
    font-size: 10px; font-weight: 700; letter-spacing: 1.8px; color: var(--c-tx6);
  }
  .close-btn {
    margin-left: auto; background: none; border: none; color: var(--c-tx7);
    font-size: 12px; cursor: pointer; padding: 4px 8px; border-radius: 3px;
  }
  .close-btn:hover { color: var(--c-red); }

  .loading {
    padding: 34px 20px; text-align: center;
    font-size: 12px; color: var(--c-tx6);
  }

  .body { display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; }

  .opt {
    display: flex; flex-direction: column; align-items: flex-start; gap: 3px;
    background: var(--c-bg5); border: 1px solid var(--c-br1); border-radius: 4px;
    padding: 10px 13px; cursor: pointer; text-align: left;
    font-family: inherit; transition: border-color .1s, background .1s;
  }
  .opt:hover { border-color: var(--c-accent); background: var(--c-hover); }

  .opt-lbl { font-size: 13px; color: var(--c-tx2); }
  .opt-sub {
    font-size: 11px; color: var(--c-tx5); max-width: 100%;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }

  .foot {
    display: flex; align-items: center; gap: 12px;
    padding: 9px 16px; border-top: 1px solid var(--c-br1);
  }
  .hint { font-size: 10px; color: var(--c-tx7); flex: 1; }
  .cancel-btn {
    background: none; border: 1px solid var(--c-br2); border-radius: 3px;
    color: var(--c-tx5); font-size: 11px; padding: 4px 12px; cursor: pointer;
    font-family: inherit;
  }
  .cancel-btn:hover { color: var(--c-tx3); border-color: var(--c-br3); }
</style>
