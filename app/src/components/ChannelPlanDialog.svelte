<script>
  import { channelPlan, send, openSettings } from '../stores/ws.js'

  // Kanal verfolgen: alle Playlists des Kanals zur Auswahl. Die gewaehlten
  // werden einmal komplett geladen (Vorhandenes wird uebersprungen), als
  // Playlist angelegt und danach wie jede verfolgte Playlist geprueft.
  const plan = $derived($channelPlan)
  let pick = $state(new Set())
  let autoNew = $state(true)
  let filter = $state('')

  $effect(() => {
    const p = plan
    if (!p || p.error) return
    const ex = new Set(p.excluded ?? [])
    // Neu: alles an. Schon verfolgt: bisherige Auswahl, Neues an
    pick = new Set(p.playlists.filter(x => p.existing ? (x.followed || !ex.has(x.url)) : true).map(x => x.url))
    autoNew = p.auto_new ?? true
    filter = ''
  })

  const list = $derived.by(() => {
    if (!plan?.playlists) return []
    const q = filter.trim().toLowerCase()
    return q ? plan.playlists.filter(x => x.title.toLowerCase().includes(q)) : plan.playlists
  })
  const neuCount = $derived(plan?.playlists ? plan.playlists.filter(x => pick.has(x.url) && !x.followed).length : 0)

  function toggle(url, on) {
    const s = new Set(pick)
    if (on) s.add(url); else s.delete(url)
    pick = s
  }
  function setAll(on) {
    const s = new Set(pick)
    for (const x of list) { if (on) s.add(x.url); else s.delete(x.url) }
    pick = s
  }
  function confirm() {
    send({ type: 'channel_follow', plan_id: plan.plan_id, selected: [...pick], auto_new: autoNew })
    channelPlan.set(null)
    openSettings('download')
  }
  function close() { channelPlan.set(null) }
  function onkey(e) { if (e.key === 'Escape' && plan) close() }
</script>

<svelte:window onkeydown={onkey} />

{#if plan}
  <div class="dlg-overlay" onclick={close} role="presentation">
    <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Kanal verfolgen">
      <div class="hdr">
        <i class="ti ti-broadcast hdr-ico" aria-hidden="true"></i>
        <span class="dlg-title">{plan.title || 'Kanal'}</span>
        {#if plan.playlists}<span class="cnt">{plan.playlists.length} Playlists</span>{/if}
        <button class="btn btn-icon btn-sm close-btn" onclick={close} title="Abbrechen" aria-label="Abbrechen"><i class="ti ti-x"></i></button>
      </div>

      {#if plan.error}
        <div class="err"><i class="ti ti-alert-triangle"></i> {plan.error}</div>
        <div class="dlg-actions foot"><button class="btn" onclick={close}>Schließen</button></div>
      {:else}
        <p class="intro">
          Die gewählten Playlists werden einmal komplett geladen — was schon in deiner Sammlung ist, wird übersprungen —
          und je als Playlist angelegt (auch als .m3u8), abgelegt unter „Downloads/{plan.title}/…“. Danach prüft SynthiMIX
          sie beim Start und auf Knopfdruck auf neue Titel.
        </p>
        <div class="bar">
          <input class="field field-sm" type="search" placeholder="Filtern…" bind:value={filter} aria-label="Playlists filtern" />
          <span class="spacer"></span>
          <button class="btn btn-ghost btn-sm" onclick={() => setAll(true)}>Alle</button>
          <button class="btn btn-ghost btn-sm" onclick={() => setAll(false)}>Keine</button>
        </div>
        <div class="list">
          {#each list as p (p.url)}
            <label class="it" class:off={!pick.has(p.url)}>
              <input type="checkbox" checked={pick.has(p.url)} onchange={(e) => toggle(p.url, e.currentTarget.checked)} />
              {#if p.thumb}<img class="thumb" src={p.thumb} alt="" loading="lazy" onerror={(e) => e.currentTarget.remove()} />{:else}<span class="thumb ph"><i class="ti ti-playlist"></i></span>{/if}
              <span class="t" title={p.title}>{p.title}</span>
              {#if p.followed}<span class="tag"><i class="ti ti-bookmark-filled"></i> verfolgt</span>{/if}
            </label>
          {/each}
        </div>
        <div class="dlg-actions foot">
          <label class="auto" title="Legt der Kanal später eine neue Playlist an, wird sie automatisch verfolgt und geladen. Aus: sie erscheint nur als Vorschlag in den Einstellungen.">
            <input type="checkbox" bind:checked={autoNew} /> Neue Playlists automatisch verfolgen
          </label>
          <button class="btn" onclick={close}>Abbrechen</button>
          <button class="btn btn-primary" onclick={confirm} disabled={!pick.size && !plan.existing}>
            <i class="ti ti-bookmark"></i> {plan.existing ? 'Übernehmen' : 'Verfolgen'} ({pick.size}){#if neuCount && plan.existing} · {neuCount} neu{/if}
          </button>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .panel { width: min(640px, 95vw); max-height: min(86vh, 760px); display: flex; flex-direction: column; }
  .hdr { display: flex; align-items: center; gap: var(--sp-2); }
  .hdr-ico { font-size: 18px; color: var(--c-accent-tx); }
  .cnt { font-size: var(--fs-sm); color: var(--c-tx4); }
  .close-btn { margin-left: auto; }
  .intro { margin: 0; font-size: var(--fs-sm); color: var(--c-tx3); line-height: 1.5; }
  .bar { display: flex; align-items: center; gap: var(--sp-2); }
  .bar input { width: 220px; }
  .spacer { flex: 1; }
  .list { flex: 1; min-height: 120px; overflow-y: auto; border: 1px solid var(--c-br1); border-radius: var(--r-m); }
  .it { display: flex; align-items: center; gap: var(--sp-2); padding: 5px var(--sp-3); border-bottom: 1px solid var(--c-br1); cursor: pointer; font-size: var(--fs-body); }
  .it:hover { background: var(--c-hover); }
  .it.off .t, .it.off .thumb { opacity: .45; }
  .thumb { width: 48px; height: 27px; object-fit: cover; border-radius: 3px; flex-shrink: 0; background: var(--c-bg2); }
  .thumb.ph { display: inline-flex; align-items: center; justify-content: center; color: var(--c-tx4); }
  .t { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--c-tx1); }
  .tag { font-size: var(--fs-cap); color: var(--c-accent-tx); display: inline-flex; align-items: center; gap: 3px; flex-shrink: 0; }
  .foot { align-items: center; }
  .auto { flex: 1; display: inline-flex; align-items: center; gap: 8px; font-size: var(--fs-body); color: var(--c-tx2); cursor: pointer; }
  .err { color: var(--c-warn-tx); font-size: var(--fs-body); padding: var(--sp-4) 0; display: flex; gap: 8px; align-items: center; }
</style>
