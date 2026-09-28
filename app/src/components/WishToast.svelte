<script>
  // Hinweis-Leiste fuer neue Musikwuensche: unten rechts, bleibt stehen, bis
  // man reagiert (annehmen, ansehen, ablehnen oder wegklicken). Vorher gab es
  // nur den kleinen Zaehler am Symbol oben — ein Wunsch ging leicht unter.
  import { wishes, wishesOpen, wishesLoaded, send } from '../stores/ws.js'

  let seen = null                 // bekannte Wuensche: ID -> Anzahl (beim ersten Laden alle)
  let shown = $state([])          // IDs mit offener Leiste, neueste zuletzt

  $effect(() => {
    const list = $wishes
    // Erst ab der ersten echten Liste vom Backend zaehlen: was beim Start
    // schon offen ist, steht im Zaehler oben, loest aber keine Leiste aus
    if (!$wishesLoaded) return
    const ids = new Set(list.map(w => w.id))
    const counts = new Map(list.map(w => [w.id, w.count ?? 1]))
    if (seen === null) { seen = counts; return }
    // Neu — oder noch einmal gewuenscht (Zaehler gestiegen)
    const fresh = list.filter(w => !seen.has(w.id) || (w.count ?? 1) > seen.get(w.id)).map(w => w.id)
    seen = counts
    // Erledigte Wuensche (angenommen/abgelehnt) verschwinden aus der Leiste
    const keep = shown.filter(id => ids.has(id))
    if (fresh.length || keep.length !== shown.length) shown = [...keep.filter(id => !fresh.includes(id)), ...fresh]
  })

  const current = $derived(shown.length ? $wishes.find(w => w.id === shown.at(-1)) : null)
  const more    = $derived(Math.max(0, shown.length - 1))

  const STATUS = { neu: 'wird gleich geladen', laedt: 'wird geladen…', analysiert: 'wird analysiert…', bereit: 'bereit', fehler: 'konnte nicht geladen werden' }

  function dismiss() { shown = shown.slice(0, -1) }
  function accept(next) { send({ type: 'wish_accept', id: current.id, as_next: next }); dismiss() }
  function openAll() { wishesOpen.set(true); shown = [] }
</script>

{#if current}
  <div class="wish-toast" role="alert" aria-live="assertive">
    <div class="wt-ico" aria-hidden="true"><i class="ti ti-music"></i></div>
    <div class="wt-body">
      <div class="wt-head">
        {(current.count ?? 1) > 1 ? 'Wieder gewünscht' : 'Neuer Musikwunsch'}
      </div>
      <div class="wt-title" title={current.title}>{current.title}</div>
      <div class="wt-state s-{current.status}">
        {#if (current.count ?? 1) > 1}<b class="wt-cnt">{current.count}× gewünscht</b>&nbsp;·&nbsp;{/if}{STATUS[current.status] ?? current.status}{#if more}&nbsp;·&nbsp;<button class="wt-link" onclick={openAll}>+{more} weitere</button>{/if}
      </div>
      <div class="wt-btns">
        {#if current.status === 'bereit'}
          <button class="btn btn-sm btn-primary" onclick={() => accept(true)}><i class="ti ti-player-track-next"></i> Als Nächster</button>
          <button class="btn btn-sm" onclick={() => accept(false)}><i class="ti ti-playlist-add"></i> In Queue</button>
        {/if}
        <button class="btn btn-sm btn-ghost" onclick={openAll}>Ansehen</button>
      </div>
    </div>
    <button class="btn btn-icon btn-sm wt-x" onclick={dismiss} title="Hinweis schließen (der Wunsch bleibt in der Liste)" aria-label="Hinweis schließen"><i class="ti ti-x"></i></button>
  </div>
{/if}

<style>
  .wish-toast {
    position: fixed; right: 16px; bottom: 16px; z-index: 900;
    width: 340px; max-width: calc(100vw - 32px);
    display: flex; gap: 12px; align-items: flex-start;
    padding: 12px 10px 12px 12px;
    background: var(--c-bg5); border: 1px solid var(--c-br2);
    border-left: 4px solid var(--c-accent); border-radius: var(--r-m);
    box-shadow: 0 10px 32px rgba(0, 0, 0, .45);
    animation: wt-in .28s cubic-bezier(.2, .8, .2, 1);
  }
  @keyframes wt-in { from { transform: translateY(24px); opacity: 0 } to { transform: none; opacity: 1 } }
  .wt-ico {
    width: 38px; height: 38px; flex-shrink: 0; border-radius: 50%;
    background: var(--c-accent); color: var(--c-on-accent);
    display: flex; align-items: center; justify-content: center; font-size: 19px;
    animation: wt-pulse 1.6s ease-in-out 3;
  }
  @keyframes wt-pulse { 0%, 100% { box-shadow: 0 0 0 0 transparent } 50% { box-shadow: 0 0 0 7px color-mix(in srgb, var(--c-accent) 30%, transparent) } }
  .wt-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
  .wt-head { font-size: var(--fs-sm); font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--c-accent-tx); display: flex; gap: 8px; align-items: center; }
  .wt-cnt { font-weight: 700; color: var(--c-tx1); }
  .wt-title { font-size: var(--fs-lg, 15px); font-weight: 700; color: var(--c-tx1); line-height: 1.3; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .wt-state { font-size: var(--fs-sm); color: var(--c-tx3); }
  .wt-state.s-bereit { color: var(--c-green-tx); }
  .wt-state.s-fehler { color: var(--c-warn-tx); }
  .wt-link { border: none; background: none; padding: 0; color: var(--c-accent-tx); cursor: pointer; font: inherit; text-decoration: underline; }
  .wt-btns { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .wt-x { flex-shrink: 0; margin-top: -4px; }
</style>
