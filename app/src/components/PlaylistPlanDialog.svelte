<script>
  import { playlistPlans, planChecks, send } from '../stores/ws.js'

  // Vor dem Laden einer Playlist: wie viel ist schon in der Sammlung, und was
  // soll passieren? Vorher wurde still alles (oder nur Neues) geladen — und
  // im Ordner lagen dann nur 8 von 140 Titeln.
  // Die Pruefung selbst laeuft im Hintergrund (Anzeige unten rechts) —
  // frueher sperrte ein Fenster die ganze App, bis sie fertig war.
  const plan = $derived($playlistPlans[0] ?? null)
  let follow = $state(false)
  $effect(() => { if (plan) follow = false })

  const liste = $derived(plan ? plan.total - plan.dupes : 0)

  function next() { playlistPlans.update(l => l.slice(1)) }
  function pick(mode) {
    send({ type: 'download_add', url: plan.url, format: plan.format,
           plan_id: plan.plan_id, plist_mode: mode, follow })
    next()
  }
  function cancel() { next() }
  function abort(url) {
    send({ type: 'playlist_plan_abort', url })
    planChecks.update(l => l.filter(c => c.url !== url))
  }
  function checkText(c) {
    if (c.phase === 'list') return c.done ? `Playlist wird eingelesen… ${c.done} Titel` : 'Playlist wird eingelesen…'
    if (!c.total) return 'Abgleich mit deiner Sammlung…'
    return `Song-Versionen suchen… ${c.done}/${c.total}`
  }
  function onkey(e) { if (e.key === 'Escape' && plan) cancel() }
</script>

<svelte:window onkeydown={onkey} />

{#if $planChecks.length}
  <div class="checks" role="status" aria-live="polite">
    {#each $planChecks as c (c.url)}
      <div class="check">
        <i class="ti ti-refresh spin" aria-hidden="true"></i>
        <div class="check-body">
          <span class="check-head">Playlist wird geprüft</span>
          <span class="check-txt">{checkText(c)}</span>
          {#if c.phase === 'search' && c.total}
            <span class="check-bar"><span style="width: {Math.round(c.done / c.total * 100)}%"></span></span>
          {/if}
        </div>
        <button class="btn btn-icon btn-sm" onclick={() => abort(c.url)} title="Prüfung abbrechen" aria-label="Prüfung abbrechen"><i class="ti ti-x"></i></button>
      </div>
    {/each}
  </div>
{/if}

{#if plan}
  <div class="dlg-overlay" onclick={cancel} role="presentation">
    <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Playlist laden">

      <div class="hdr">
        <span class="dlg-title">{plan.title || 'Playlist'}</span>
        {#if $playlistPlans.length > 1}<span class="more">+{$playlistPlans.length - 1} weitere</span>{/if}
        <button class="btn btn-icon btn-sm close-btn" onclick={cancel} title="Abbrechen" aria-label="Abbrechen"><i class="ti ti-x"></i></button>
      </div>

        <!-- Info-Kaestchen -->
        <div class="facts" role="status">
          <span class="fact"><b>{plan.total}</b> Titel</span>
          <span class="fact have"><b>{plan.have}</b> schon in deiner Sammlung</span>
          <span class="fact new"><b>{plan.new}</b> neu</span>
          {#if plan.dupes}<span class="fact"><b>{plan.dupes}</b> doppelt in der Playlist</span>{/if}
        </div>

        <div class="body">
          <button class="opt rec" onclick={() => pick('playlist')}>
            <i class="ti ti-list opt-ico" aria-hidden="true"></i>
            <span class="opt-text">
              <span class="opt-lbl">Als Playlist anlegen <span class="rec-tag">Empfohlen</span></span>
              <span class="opt-sub">{plan.new ? `Lädt ${plan.new} neue` : 'Nichts zu laden'} · Playlist „{plan.title || 'Playlist'}“ mit allen {liste} Titeln, auch als .m3u8 für rekordbox / Virtual DJ</span>
            </span>
          </button>
          <button class="opt" onclick={() => pick('new')} disabled={!plan.new}>
            <i class="ti ti-download opt-ico" aria-hidden="true"></i>
            <span class="opt-text">
              <span class="opt-lbl">Nur neue laden</span>
              <span class="opt-sub">{plan.new} Titel in den Ordner „{plan.title || 'Playlist'}“, vorhandene bleiben, wo sie sind</span>
            </span>
          </button>
          <button class="opt" onclick={() => pick('folder')}>
            <i class="ti ti-folder opt-ico" aria-hidden="true"></i>
            <span class="opt-text">
              <span class="opt-lbl">Kompletter Ordner</span>
              <span class="opt-sub">Alle {liste} Titel im Ordner „{plan.title || 'Playlist'}“{plan.have ? ` — ${plan.have} vorhandene werden verknüpft (kein Download, kein zusätzlicher Platz)` : ''}{plan.new ? `, ${plan.new} neue geladen` : ''}. Gut zum Kopieren auf einen Stick.</span>
            </span>
          </button>
        </div>

        <div class="dlg-actions foot">
          {#if plan.followed}
            <span class="dlg-hint"><i class="ti ti-bookmark-filled"></i> Wird schon verfolgt</span>
          {:else}
            <label class="follow-chk" title="Beim Start und auf Knopfdruck neue Titel dieser Playlist laden">
              <input type="checkbox" bind:checked={follow} /> Playlist verfolgen (neue Titel automatisch)
            </label>
          {/if}
          <button class="btn" onclick={cancel}>Abbrechen</button>
        </div>

    </div>
  </div>
{/if}

<style>
  .panel { width: 520px; }
  .hdr { display: flex; align-items: center; gap: var(--sp-2); }
  .close-btn { margin-left: auto; }
  .more { font-size: var(--fs-sm); color: var(--c-tx4); }
  .checks {
    position: fixed; right: 16px; bottom: 16px; z-index: 850;
    width: 320px; max-width: calc(100vw - 32px);
    display: flex; flex-direction: column; gap: 8px;
  }
  .check {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 8px 10px 12px;
    background: var(--c-bg5); border: 1px solid var(--c-br2);
    border-left: 4px solid var(--c-accent); border-radius: var(--r-m);
    box-shadow: 0 8px 26px rgba(0, 0, 0, .4);
  }
  .check > .spin { font-size: 18px; color: var(--c-accent-tx); flex-shrink: 0; }
  .check-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
  .check-head { font-size: var(--fs-sm); font-weight: 700; color: var(--c-tx1); }
  .check-txt { font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; }
  .check-bar { height: 3px; border-radius: 2px; background: var(--c-br2); overflow: hidden; margin-top: 2px; }
  .check-bar span { display: block; height: 100%; background: var(--c-accent); transition: width .25s; }
  .spin { animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .facts {
    display: flex; flex-wrap: wrap; gap: 6px 14px; padding: 10px 12px; border-radius: var(--r-m);
    background: var(--c-bg2); border: 1px solid var(--c-br2); font-size: var(--fs-body); color: var(--c-tx3);
  }
  .fact b { color: var(--c-tx1); font-variant-numeric: tabular-nums; }
  .fact.have b { color: var(--c-green-tx); }
  .fact.new b { color: var(--c-accent-tx); }
  .body { display: flex; flex-direction: column; gap: var(--sp-2); }
  .opt {
    display: flex; align-items: center; gap: var(--sp-3); min-height: 56px;
    padding: var(--sp-3) var(--sp-4); border-radius: var(--r-m);
    border: 1px solid var(--c-br3); background: var(--c-bg5);
    cursor: pointer; text-align: left; font-family: inherit;
  }
  .opt:hover:not(:disabled) { border-color: var(--c-accent); background: var(--c-act-bg); }
  .opt:disabled { opacity: .45; cursor: default; }
  .opt.rec { border-color: color-mix(in srgb, var(--c-accent) 55%, var(--c-br3)); }
  .opt-ico { font-size: 20px; color: var(--c-tx3); flex-shrink: 0; }
  .opt:hover:not(:disabled) .opt-ico { color: var(--c-accent-tx); }
  .opt-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .opt-lbl { font-size: var(--fs-lg); font-weight: 600; color: var(--c-tx1); display: flex; align-items: center; gap: 8px; }
  .rec-tag { font-size: var(--fs-cap); font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--c-accent-tx); }
  .opt-sub { font-size: var(--fs-sm); color: var(--c-tx3); line-height: 1.4; }
  .foot { align-items: center; }
  .foot .dlg-hint { flex: 1; display: inline-flex; align-items: center; gap: 6px; color: var(--c-accent-tx); }
  .follow-chk { flex: 1; display: inline-flex; align-items: center; gap: 8px; font-size: var(--fs-body); color: var(--c-tx2); cursor: pointer; }
</style>
