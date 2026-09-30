<script>
  // Laufwerksbuchstabe geaendert (M: -> X:): die Titel fehlen, die Eintraege
  // mit BPM, Tonart, Wiedergaben usw. sind aber noch da. Das Backend findet,
  // wohin sie gewandert sind (relocate.detect) — hier ein Klick zum Umstellen.
  import { relocateSuggest, relocateState, send } from '../stores/ws.js'

  const items = $derived($relocateSuggest ?? [])
  const cur = $derived(items[0] ?? null)
  const st = $derived($relocateState)

  function apply() {
    relocateState.set({ busy: true })
    send({ type: 'relocate_apply', from: cur.from, to: cur.to })
  }
  function later() { relocateSuggest.set(items.slice(1)); relocateState.set(null) }
</script>

{#if cur || st?.done}
<div class="dlg-overlay" role="presentation">
  <div class="dlg panel" role="dialog" aria-modal="true" aria-label="Laufwerk gewechselt">
    <div class="dlg-title"><i class="ti ti-folder-search" aria-hidden="true"></i> Laufwerk gewechselt?</div>
    {#if st?.done}
      <p class="txt ok"><i class="ti ti-check"></i> {st.tracks} Titel umgestellt{st.merged ? `, ${st.merged} doppelte zusammengeführt` : ''}{st.playlists ? `, ${st.playlists} Playlist${st.playlists > 1 ? 's' : ''} angepasst` : ''}. BPM, Tonart und Wiedergaben sind geblieben.</p>
      <div class="dlg-actions">
        <button class="btn btn-primary" onclick={() => relocateState.set(null)}>Fertig</button>
      </div>
    {:else}
      <p class="txt"><b>{cur.count} Titel</b> liegen nicht mehr auf <b>{cur.from}</b> — auf <b>{cur.to}</b> sind sie da{cur.found < 1 ? ` (Stichprobe: ${Math.round(cur.found * 100)} % gefunden)` : ''}.</p>
      <p class="sub">Umstellen ändert nur die Pfade in SynthiMIX: Bibliothek, Warteschlange, Playlists, Verlauf, Musik- und Download-Ordner. BPM, Tonart, Wiedergaben und Analysen bleiben. Die Musikdateien werden nicht angefasst.</p>
      <div class="dlg-actions">
        <button class="btn" onclick={later} disabled={st?.busy}>Später</button>
        <button class="btn btn-primary" onclick={apply} disabled={st?.busy}>
          {#if st?.busy}<i class="ti ti-refresh spin"></i> Stelle um…{:else}Auf {cur.to} umstellen{/if}
        </button>
      </div>
    {/if}
  </div>
</div>
{/if}

<style>
  .panel { width: 480px; max-width: calc(100vw - 32px); }
  .dlg-title { display: flex; align-items: center; gap: 8px; }
  .txt { font-size: var(--fs-body); color: var(--c-tx2); line-height: 1.5; margin: 0; }
  .txt b { color: var(--c-tx1); }
  .txt.ok { color: var(--c-green-tx); }
  .sub { font-size: var(--fs-sm); color: var(--c-tx3); line-height: 1.5; margin: 0; }
  .spin { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
