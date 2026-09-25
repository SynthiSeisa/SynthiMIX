<script>
  import { videoChoices, send } from '../stores/ws.js'

  // Ein einzelner Link fuehrt zu einem Musikvideo, und YouTube Music hat die
  // Studio-Version. Videos haben oft Intro, Pausen oder Geraeusche — deshalb
  // wird gefragt statt still das Video zu laden. Mehrere Links nacheinander
  // stehen in einer Warteliste und kommen einzeln dran.
  const choice = $derived($videoChoices[0] ?? null)

  function fmt(sec) {
    if (!sec) return ''
    return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
  }

  function next() { videoChoices.update(l => l.slice(1)) }

  function pick(which) {
    const url = which === 'song' ? choice.song.url : choice.url
    send({ type: 'download_add', url, format: choice.format, video_choice: which })
    next()
  }

  function onkey(e) {
    if (choice && e.key === 'Escape') next()
  }

  const diff = $derived(choice ? Math.round((choice.video.duration ?? 0) - (choice.song.duration ?? 0)) : 0)
</script>

<svelte:window onkeydown={onkey} />

{#if choice}
  <div class="dlg-overlay" onclick={next} role="presentation">
    <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Link führt zu einem Musikvideo">

      <div class="hdr">
        <span class="dlg-title">Das ist ein Musikvideo</span>
        {#if $videoChoices.length > 1}<span class="count">noch {$videoChoices.length - 1}</span>{/if}
        <button class="btn btn-icon btn-sm close-btn" onclick={next} title="Nichts laden" aria-label="Nichts laden"><i class="ti ti-x"></i></button>
      </div>

      <div class="dlg-hint">
        Musikvideos haben oft ein Intro, Pausen oder Geräusche.
        {#if diff >= 5}Dieses ist {diff} s länger als der Song.{/if}
        Auf YouTube Music gibt es die Studio-Version.
      </div>

      <div class="body">
        <button class="opt opt-main" onclick={() => pick('song')}>
          <i class="ti ti-music opt-ico" aria-hidden="true"></i>
          <span class="opt-text">
            <span class="opt-lbl">Song-Version laden</span>
            <span class="opt-sub" title={choice.song.title}>{choice.song.uploader ? choice.song.uploader + ' – ' : ''}{choice.song.title} · {fmt(choice.song.duration)}</span>
          </span>
          <span class="rec">Empfohlen</span>
        </button>
        <button class="opt" onclick={() => pick('video')}>
          <i class="ti ti-player-play opt-ico" aria-hidden="true"></i>
          <span class="opt-text">
            <span class="opt-lbl">Trotzdem das Video laden</span>
            <span class="opt-sub" title={choice.video.title}>{choice.video.title} · {fmt(choice.video.duration)}</span>
          </span>
        </button>
      </div>

      <div class="dlg-actions">
        <button class="btn" onclick={next}>Nichts laden</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .panel { width: 500px; }
  .hdr { display: flex; align-items: center; gap: var(--sp-2); }
  .count { font-size: var(--fs-sm); color: var(--c-tx3); }
  .close-btn { margin-left: auto; }
  .body { display: flex; flex-direction: column; gap: var(--sp-2); }
  .opt {
    display: flex; align-items: center; gap: var(--sp-3); min-height: 56px;
    padding: var(--sp-3) var(--sp-4); border-radius: var(--r-m);
    border: 1px solid var(--c-br3); background: var(--c-bg5);
    cursor: pointer; text-align: left; font-family: inherit;
  }
  .opt:hover { border-color: var(--c-accent); background: var(--c-act-bg); }
  .opt-main { border-color: var(--c-accent); }
  .opt-ico { font-size: 20px; color: var(--c-tx3); flex-shrink: 0; }
  .opt:hover .opt-ico, .opt-main .opt-ico { color: var(--c-accent-tx); }
  .opt-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
  .opt-lbl { font-size: var(--fs-lg); font-weight: 600; color: var(--c-tx1); }
  .opt-sub { font-size: var(--fs-sm); color: var(--c-tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rec { flex-shrink: 0; font-size: var(--fs-cap); font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--c-accent-tx); }
</style>
