<script>
  import { untrack } from 'svelte'
  import { setLoudness, normalizeProgress, appSettings, send } from '../stores/ws.js'
  import { analyze } from '../lib/setloudness.js'

  // Lautheit eines DJ-Sets (Playlist, z. B. aus rekordbox eingelesen): jeder
  // Titel in Set-Reihenfolge mit seiner Abweichung vom Rest des Sets oder von
  // einem festen Zielwert. Ausreisser sind angehakt und lassen sich dauerhaft
  // angleichen (wie Rechtsklick auf einen Ordner → Lautstaerke angleichen).
  const st = $derived($setLoudness)
  let mode = $state('set')            // 'set' = gegen den Rest des Sets, 'fixed' = fester Wert
  let target = $state(-10)
  let tol = $state(1.5)
  let pick = $state({})               // Pfad -> angehakt?
  let touched = $state({})            // von Hand geaendert: Vorauswahl nicht mehr ueberschreiben
  let confirm = $state(false)

  $effect(() => { if (st?.path) untrack(() => { pick = {}; touched = {}; confirm = false; target = $appSettings.targetLUFS ?? -10 }) })

  const res = $derived(analyze(st?.tracks ?? [], { mode, target, tol }))
  // Vorauswahl: alles, was zu leise oder zu laut ist — eigene Haken bleiben
  $effect(() => {
    const rows = res.rows
    untrack(() => {
      const p = {}
      for (const r of rows) p[r.path] = r.path in touched ? !!pick[r.path] : (r.state === 'quiet' || r.state === 'loud')
      pick = p
    })
  })
  const chosen = $derived(res.rows.filter(r => pick[r.path] && r.state !== 'unknown'))
  const running = $derived(!!$normalizeProgress)
  const scale = $derived(Math.max(6, ...res.rows.map(r => Math.abs(r.delta ?? 0))))

  function toggle(r) { touched[r.path] = true; pick[r.path] = !pick[r.path] }
  function hide() {
    if (st?.measuring) send({ type: 'playlist_loudness_cancel', path: st.path })
    setLoudness.set(null)
  }
  function run() {
    // Was hoechstens 0,5 dB abweicht, bleibt unberuehrt; Originale in den Papierkorb
    send({ type: 'normalize_files', paths: chosen.map(r => r.path), target_lufs: res.ref, target_tp: -1.5, skip_tol: 0.5, trash: true })
    touched = {}
    confirm = false
  }
  const fmt = (v) => v === null || v === undefined ? '–' : v.toFixed(1).replace('.', ',')
  const sign = (v) => v === null ? '' : (v > 0 ? '+' : v < 0 ? '−' : '±') + Math.abs(v).toFixed(1).replace('.', ',')
  const label = { quiet: 'zu leise', loud: 'zu laut', ok: 'passt', unknown: 'wird gemessen…' }
  function onkey(e) { if (e.key === 'Escape' && st) { if (confirm) confirm = false; else hide() } }
</script>

<svelte:window onkeydown={onkey} />

{#if st}
  <div class="dlg-overlay" onclick={hide} role="presentation">
    <div class="dlg panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Lautstärke im Set prüfen">
      <div class="hdr">
        <span class="dlg-title">Lautstärke im Set „{st.name}“</span>
        <button class="btn btn-icon btn-sm close-btn" onclick={hide} title="Schließen" aria-label="Schließen"><i class="ti ti-x"></i></button>
      </div>

      <div class="opts">
        <label class="radio-opt"><input type="radio" bind:group={mode} value="set" /> Gegen den Rest des Sets</label>
        <label class="radio-opt"><input type="radio" bind:group={mode} value="fixed" /> Fester Wert</label>
        {#if mode === 'fixed'}
          <input class="rng" type="range" min="-18" max="-5" step="0.5" bind:value={target} aria-label="Zielwert in LUFS" />
        {/if}
        <span class="ref">Bezug: <b>{res.ref === null ? '–' : fmt(res.ref)} LUFS</b></span>
        <span class="spacer"></span>
        <label class="tol">Passt bis
          <select bind:value={tol} aria-label="Erlaubte Abweichung">
            <option value={1}>± 1 dB</option>
            <option value={1.5}>± 1,5 dB</option>
            <option value={2}>± 2 dB</option>
            <option value={3}>± 3 dB</option>
          </select>
        </label>
      </div>

      <div class="status" role="status">
        {#if st.error}
          <span class="bad">{st.error}</span>
        {:else if running}
          <i class="ti ti-refresh spin" aria-hidden="true"></i>
          <span>Angleichen: {$normalizeProgress.done}/{$normalizeProgress.total}{$normalizeProgress.current ? ` — ${$normalizeProgress.current}` : ''}</span>
          <span class="spacer"></span>
          <button class="btn btn-sm" onclick={() => send({ type: 'normalize_cancel' })}>Abbrechen</button>
        {:else if st.loading}
          <i class="ti ti-refresh spin" aria-hidden="true"></i><span>Playlist wird gelesen…</span>
        {:else}
          <span><b>{res.rows.length}</b> Titel</span>
          <span class="s-ok"><b>{res.ok}</b> passen</span>
          <span class="s-quiet"><b>{res.quiet}</b> zu leise</span>
          <span class="s-loud"><b>{res.loud}</b> zu laut</span>
          {#if st.measuring}<span><i class="ti ti-refresh spin" aria-hidden="true"></i> {st.measuring} werden noch gemessen…</span>{/if}
        {/if}
      </div>

      <div class="list">
        {#each res.rows as r, i (r.path + '|' + i)}
          <label class="it {r.state}">
            <input type="checkbox" checked={!!pick[r.path]} disabled={r.state === 'unknown' || running} onchange={() => toggle(r)} />
            <span class="nr">{i + 1}</span>
            <span class="name" title={r.path}>{r.artist && !r.title.toLowerCase().startsWith(r.artist.toLowerCase()) ? `${r.artist} – ` : ''}{r.title}</span>
            <span class="lufs">{fmt(r.lufs)}</span>
            <span class="bar" aria-hidden="true">
              {#if r.delta !== null}
                <span class="fill" style="{r.delta < 0 ? 'right' : 'left'}: 50%; width: {Math.min(50, Math.abs(r.delta) / scale * 50)}%"></span>
              {/if}
              <span class="mid"></span>
            </span>
            <span class="delta">{sign(r.delta)}</span>
            <span class="chip">{label[r.state]}</span>
          </label>
        {/each}
        {#if !st.loading && !res.rows.length && !st.error}
          <div class="empty">Die Playlist ist leer oder ihre Titel sind gerade nicht erreichbar.</div>
        {/if}
      </div>

      <div class="foot">
        {#if confirm}
          <div class="dlg-hint warn">
            <b>{chosen.length} Datei{chosen.length === 1 ? '' : 'en'} werden neu geschrieben</b> und auf {fmt(res.ref)} LUFS gebracht. Format, Bitrate, Länge, Tags und Cue-Punkte bleiben; die Originale kommen in den Papierkorb. Bei MP3 kostet das Neu-Schreiben etwas Qualität — und in rekordbox bitte an zwei, drei Titeln prüfen, ob Beatgrid und Cues danach noch genau sitzen, bevor du ein ganzes Set angleichst.
          </div>
          <div class="dlg-actions">
            <button class="btn" onclick={() => confirm = false}>Zurück</button>
            <button class="btn btn-primary" onclick={run}>Jetzt angleichen</button>
          </div>
        {:else}
          <div class="dlg-hint">Angehakt ist, was mehr als {String(tol).replace('.', ',')} dB vom Bezugswert abweicht. Die Messung gilt für den ganzen Titel (LUFS) — ein Titel mit langem ruhigem Intro wirkt in der Liste leiser, als er im Drop ist.</div>
          <div class="dlg-actions">
            <button class="btn" onclick={hide}>Schließen</button>
            <button class="btn btn-primary" disabled={!chosen.length || running || res.ref === null} onclick={() => confirm = true}>
              {chosen.length} Titel angleichen…
            </button>
          </div>
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .panel { width: min(820px, 94vw); height: min(84vh, 720px); padding: 0; gap: 0; display: flex; flex-direction: column; }
  .hdr { display: flex; align-items: center; padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); }
  .close-btn { margin-left: auto; }
  .opts { display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2) var(--sp-4); padding: var(--sp-2) var(--sp-5);
          border-bottom: 1px solid var(--c-br1); font-size: var(--fs-sm); color: var(--c-tx2); }
  .opts label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
  .rng { width: 130px; }
  .ref b { color: var(--c-tx1); font-variant-numeric: tabular-nums; }
  .tol select { font: inherit; color: inherit; background: var(--c-bg2); border: 1px solid var(--c-br2); border-radius: var(--r-s); padding: 2px 4px; }
  .spacer { flex: 1; }
  .status { display: flex; align-items: center; gap: var(--sp-4); padding: var(--sp-2) var(--sp-5); border-bottom: 1px solid var(--c-br1);
            font-size: var(--fs-sm); color: var(--c-tx3); min-height: 40px; }
  .status b { color: var(--c-tx1); font-variant-numeric: tabular-nums; }
  .s-ok b { color: var(--c-green-tx); }
  .s-quiet b { color: var(--c-accent-tx); }
  .s-loud b { color: var(--c-warn-tx); }
  .bad { color: var(--c-red-tx); }
  .list { flex: 1; min-height: 0; overflow-y: auto; }
  .it { display: flex; align-items: center; gap: var(--sp-2); padding: 5px var(--sp-5); border-bottom: 1px solid var(--c-br1);
        font-size: var(--fs-sm); cursor: pointer; }
  .it.unknown { cursor: default; opacity: .7; }
  .nr { width: 26px; text-align: right; color: var(--c-tx4); font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-tx1); }
  .lufs, .delta { width: 44px; text-align: right; font-variant-numeric: tabular-nums; flex-shrink: 0; color: var(--c-tx3); }
  .delta { font-weight: 600; }
  .bar { position: relative; width: 120px; height: 8px; flex-shrink: 0; border-radius: 4px; background: var(--c-bg2); overflow: hidden; }
  .bar .mid { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: var(--c-br3); }
  .bar .fill { position: absolute; top: 0; bottom: 0; background: var(--c-green-tx); }
  .chip { width: 96px; flex-shrink: 0; text-align: center; padding: 0 5px; border-radius: var(--r-s); font-size: var(--fs-cap); font-weight: 700; line-height: 16px;
          color: var(--c-green-tx); background: var(--c-green-bg); border: 1px solid var(--c-green-br); }
  .it.quiet .fill { background: var(--c-accent); }
  .it.quiet .delta { color: var(--c-accent-tx); }
  .it.quiet .chip { color: var(--c-accent-tx); background: var(--c-act-bg); border-color: var(--c-accent); }
  .it.loud .fill { background: var(--c-warn-tx); }
  .it.loud .delta { color: var(--c-warn-tx); }
  .it.loud .chip { color: var(--c-warn-tx); background: var(--c-warn-bg); border-color: var(--c-warn-br); }
  .it.unknown .chip { color: var(--c-tx4); background: none; border-color: var(--c-br2); font-weight: 400; }
  .empty { padding: var(--sp-5); color: var(--c-tx4); font-size: var(--fs-sm); }
  .foot { display: flex; flex-direction: column; gap: var(--sp-3); padding: var(--sp-3) var(--sp-5) var(--sp-4); border-top: 1px solid var(--c-br1); }
  .foot .dlg-actions { margin: 0; }
  .warn { color: var(--c-tx2); }
  .spin { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
