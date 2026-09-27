<script>
  import { theme, density } from '../lib/prefs.js'
  import { appSettings, settings, send, watchedFolders, downloadDir, playlistFolderEnabled,
           lastfmApiKey, servicesTest } from '../stores/ws.js'
  import KeyChip from './KeyChip.svelte'
  import BlendSketch from './BlendSketch.svelte'

  // Einrichtung in sechs kurzen Schritten: Aussehen, Bibliothek, Downloads,
  // Uebergaenge, Anzeige, Dienste. Jede Aenderung wirkt sofort und nutzt
  // dieselben Nachrichten wie die Einstellungen — der Assistent ist nur ein
  // gefuehrter Weg dorthin. Laesst sich jederzeit ueberspringen.
  let { onclose } = $props()

  const STEPS = ['Aussehen', 'Bibliothek', 'Downloads', 'Übergänge', 'Anzeige', 'Dienste']
  let step = $state(0)
  let lfmKey = $state($lastfmApiKey ?? '')

  async function addFolder() {
    const folder = await window.electron?.pickFolder?.()
    if (folder) send({ type: 'scan_library', folder })
  }
  async function pickDownload() {
    const p = await window.electron?.pickFolder?.()
    if (p) { downloadDir.set(p); send({ type: 'set_download_folder', path: p }) }
  }
  function setPlaylistFolder(on) {
    playlistFolderEnabled.set(on)
    send({ type: 'set_playlist_folder', enabled: on })
  }
  function setApp(k, v) { appSettings.update(s => ({ ...s, [k]: v })) }
  function saveLfm() {
    lastfmApiKey.set(lfmKey.trim())
    send({ type: 'set_services', lastfm_api_key: lfmKey.trim() })
  }
  function finish() {
    if (lfmKey.trim() !== ($lastfmApiKey ?? '')) saveLfm()
    setApp('setupDone', true)
    onclose()
  }
  function onkey(e) { if (e.key === 'Escape') finish() }
</script>

<svelte:window onkeydown={onkey} />

<div class="dlg-overlay" role="presentation">
  <div class="dlg panel" role="dialog" aria-modal="true" aria-label="SynthiMIX einrichten">
    <div class="hdr">
      <span class="dlg-title">SynthiMIX einrichten</span>
      <span class="step-n">Schritt {step + 1} von {STEPS.length}</span>
      <button class="btn btn-ghost btn-sm skip" onclick={finish} title="Später in den Einstellungen anpassen">Überspringen</button>
    </div>

    <ol class="steps" aria-label="Schritte">
      {#each STEPS as s, i}
        <li class:done={i < step} class:cur={i === step}>
          <button onclick={() => step = i} aria-current={i === step ? 'step' : undefined}>
            <span class="dot">{i < step ? '✓' : i + 1}</span>{s}
          </button>
        </li>
      {/each}
    </ol>

    <div class="body">
      {#if step === 0}
        <h3>Wie soll SynthiMIX aussehen?</h3>
        <div class="field-row">
          <span class="lbl">Farben</span>
          <div class="btn-group">
            <button class="btn" class:is-active={$theme === 'dark'} onclick={() => theme.set('dark')}><i class="ti ti-moon"></i> Dunkel</button>
            <button class="btn" class:is-active={$theme === 'light'} onclick={() => theme.set('light')}><i class="ti ti-sun"></i> Hell</button>
          </div>
        </div>
        <p class="hint">Hell ist für draußen bei Sonne gedacht.</p>
        <div class="field-row">
          <span class="lbl">Dichte</span>
          <div class="btn-group">
            <button class="btn" class:is-active={$density === 'compact'} onclick={() => density.set('compact')}>Kompakt</button>
            <button class="btn" class:is-active={$density === 'comfortable'} onclick={() => density.set('comfortable')}>Komfortabel</button>
          </div>
        </div>
        <p class="hint">Kompakt zeigt viele Titel auf einmal — gut zum Aufräumen zuhause. Komfortabel hat größere Zeilen und Knöpfe — gut live am Laptop.</p>

      {:else if step === 1}
        <h3>Wo liegt deine Musik?</h3>
        <p class="hint">Ordner, die SynthiMIX einliest und beobachtet. Neue Dateien darin kommen automatisch in die Bibliothek. Unterordner werden mit eingelesen.</p>
        <div class="list">
          {#each $watchedFolders as wf (wf.path)}
            <div class="li"><i class="ti ti-folder"></i><span class="li-t" title={wf.path}>{wf.path}</span><span class="li-n">{wf.tracks ?? 0} Titel</span></div>
          {:else}
            <div class="li muted">Noch kein Ordner.</div>
          {/each}
        </div>
        <button class="btn btn-primary" onclick={addFolder}><i class="ti ti-folder-plus"></i> Musikordner hinzufügen</button>

      {:else if step === 2}
        <h3>Wohin sollen Downloads?</h3>
        <div class="field-row">
          <span class="lbl">Ordner</span>
          <span class="path" title={$downloadDir}>{$downloadDir || '—'}</span>
          <button class="btn btn-sm" onclick={pickDownload}><i class="ti ti-folder"></i> Ändern</button>
        </div>
        <div class="field-row">
          <span class="lbl">Playlisten</span>
          <div class="btn-group">
            <button class="btn btn-sm" class:is-active={$playlistFolderEnabled} onclick={() => setPlaylistFolder(true)}>Eigener Unterordner</button>
            <button class="btn btn-sm" class:is-active={!$playlistFolderEnabled} onclick={() => setPlaylistFolder(false)}>Alles in einen Ordner</button>
          </div>
        </div>
        <p class="hint">Gut zu wissen: YouTube und Spotify liefern höchstens etwa 160 kbps. MP3 ist überall abspielbar; FLAC oder WAV daraus klingen nicht besser.</p>

      {:else if step === 3}
        <h3>Wie sollen Titel ineinander übergehen?</h3>
        <div class="field-row">
          <span class="lbl">Überblendzeit</span>
          <input type="range" min="0" max="15" step="1" value={$settings.crossfade_s}
                 oninput={(e) => settings.update(s => ({ ...s, crossfade_s: +e.target.value }))}
                 onchange={(e) => send({ type: 'set_crossfade', seconds: +e.target.value })} />
          <span class="val">{$settings.crossfade_s === 0 ? 'aus' : $settings.crossfade_s + ' s'}</span>
        </div>
        <div class="field-row">
          <span class="lbl">Kurve</span>
          <div class="btn-group">
            {#each [['cosine', 'Kosinus'], ['linear', 'Linear'], ['scurve', 'S-Kurve']] as [v, l]}
              <button class="btn btn-sm" class:is-active={$appSettings.cfCurve === v} onclick={() => setApp('cfCurve', v)}>{l}</button>
            {/each}
          </div>
        </div>
        <BlendSketch kind="curve" value={$appSettings.cfCurve ?? 'cosine'} />
        <div class="field-row">
          <span class="lbl">Intelligent</span>
          <button class="tog {$appSettings.smartFade ? 'on' : ''}" aria-pressed={$appSettings.smartFade} aria-label="Intelligenter Fade"
                  onclick={() => setApp('smartFade', !$appSettings.smartFade)}></button>
          <span class="hint inline">Stille Intros überspringen, am Ende früher mischen</span>
        </div>
        <div class="field-row">
          <span class="lbl">Lautstärke angleichen</span>
          <button class="tog {$appSettings.normalizeVolume ? 'on' : ''}" aria-pressed={$appSettings.normalizeVolume} aria-label="Lautstärke angleichen"
                  onclick={() => { const v = !$appSettings.normalizeVolume; setApp('normalizeVolume', v); send({ type: 'set_normalize_volume', value: v }) }}></button>
          <span class="hint inline">Alle Titel etwa gleich laut ({$appSettings.targetLUFS} LUFS)</span>
        </div>

      {:else if step === 4}
        <h3>Was möchtest du sehen?</h3>
        <div class="field-row">
          <span class="lbl">Tonart als</span>
          <div class="btn-group">
            <button class="btn btn-sm" class:is-active={($appSettings.keyNotation ?? 'musical') === 'musical'} onclick={() => setApp('keyNotation', 'musical')}>Noten</button>
            <button class="btn btn-sm" class:is-active={$appSettings.keyNotation === 'camelot'} onclick={() => setApp('keyNotation', 'camelot')}>Camelot</button>
          </div>
          <span class="chips"><KeyChip key="Am" src="tag" /><KeyChip key="F♯m" src="tag" /></span>
        </div>
        <div class="field-row top">
          <span class="lbl">Warteschlange</span>
          <div class="checks">
            {#each [['qShowEta', 'Startzeit'], ['qShowKey', 'Tonart'], ['qShowBpm', 'BPM'], ['qShowPlays', 'Wiedergaben (×N)']] as [k, l]}
              <label><input type="checkbox" checked={!!$appSettings[k]} onchange={(e) => setApp(k, e.currentTarget.checked)} /> {l}</label>
            {/each}
          </div>
        </div>
        <p class="hint">Später änderbar: Einstellungen → Darstellung und im ⋯-Menü der Warteschlange.</p>

      {:else if step === 5}
        <h3>Zusätzliche Dienste (optional)</h3>
        <p class="hint">Für den Radio-Modus und Genre-/Titel-Vorschläge: kostenloser <b>Last.fm-API-Key</b> (last.fm → API account). Alles andere unter Einstellungen → Dienste.</p>
        <div class="field-row">
          <span class="lbl">Last.fm-Key</span>
          <input class="field" type="text" placeholder="32-stelliger Key" bind:value={lfmKey} onchange={saveLfm} />
          <button class="btn btn-sm" onclick={() => { saveLfm(); servicesTest.set(null); send({ type: 'test_services' }) }}>Testen</button>
        </div>
        {#if $servicesTest?.lastfm}
          <p class="hint {$servicesTest.lastfm.ok ? 'ok' : 'bad'}">{$servicesTest.lastfm.ok ? '✓' : '✗'} {$servicesTest.lastfm.text}</p>
        {/if}
      {/if}
    </div>

    <div class="dlg-actions foot">
      {#if step > 0}<button class="btn" onclick={() => step--}>Zurück</button>{/if}
      <span class="spacer"></span>
      {#if step < STEPS.length - 1}
        <button class="btn btn-primary" onclick={() => step++}>Weiter</button>
      {:else}
        <button class="btn btn-primary" onclick={finish}><i class="ti ti-check"></i> Fertig</button>
      {/if}
    </div>
  </div>
</div>

<style>
  .panel { width: min(640px, 94vw); padding: 0; gap: 0; }
  .hdr { display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-3) var(--sp-3) var(--sp-3) var(--sp-5); border-bottom: 1px solid var(--c-br1); }
  .step-n { font-size: var(--fs-sm); color: var(--c-tx4); }
  .skip { margin-left: auto; }
  .steps { display: flex; gap: 2px; list-style: none; margin: 0; padding: var(--sp-2) var(--sp-4); border-bottom: 1px solid var(--c-br1); overflow-x: auto; }
  .steps button { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border: none; background: none; border-radius: var(--r-s);
                  font: inherit; font-size: var(--fs-sm); color: var(--c-tx4); cursor: pointer; white-space: nowrap; }
  .steps button:hover { background: var(--c-hover); }
  .dot { width: 18px; height: 18px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
         font-size: var(--fs-cap); font-weight: 700; border: 1px solid var(--c-br3); }
  .steps .cur button { color: var(--c-tx1); font-weight: 600; }
  .steps .cur .dot { background: var(--c-accent); border-color: var(--c-accent); color: var(--c-on-accent); }
  .steps .done .dot { color: var(--c-green-tx); border-color: var(--c-green-br); }
  .body { padding: var(--sp-4) var(--sp-5); display: flex; flex-direction: column; gap: var(--sp-3); min-height: 280px; }
  h3 { margin: 0; font-size: var(--fs-h); color: var(--c-tx1); }
  .hint { margin: 0; font-size: var(--fs-sm); color: var(--c-tx3); line-height: 1.5; }
  .hint.inline { flex: 1; }
  .hint.ok { color: var(--c-green-tx); }
  .hint.bad { color: var(--c-red-tx); }
  .field-row { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
  .field-row.top { align-items: flex-start; }
  .lbl { width: 150px; flex-shrink: 0; font-size: var(--fs-body); color: var(--c-tx2); }
  .field-row input[type=range] { flex: 1; }
  .field-row .field { flex: 1; min-width: 180px; }
  .val { width: 44px; font-size: var(--fs-sm); color: var(--c-tx1); font-variant-numeric: tabular-nums; }
  .path { flex: 1; min-width: 0; font-size: var(--fs-sm); color: var(--c-tx2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .list { border: 1px solid var(--c-br1); border-radius: var(--r-m); max-height: 160px; overflow-y: auto; }
  .li { display: flex; align-items: center; gap: var(--sp-2); padding: 6px var(--sp-3); border-bottom: 1px solid var(--c-br1); font-size: var(--fs-sm); }
  .li:last-child { border-bottom: none; }
  .li.muted { color: var(--c-tx4); }
  .li-t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-tx1); }
  .li-n { color: var(--c-tx4); font-variant-numeric: tabular-nums; }
  .chips { display: inline-flex; gap: 4px; }
  .checks { display: flex; flex-wrap: wrap; gap: var(--sp-2) var(--sp-4); font-size: var(--fs-body); color: var(--c-tx2); }
  .checks label { display: inline-flex; align-items: center; gap: 6px; }
  .foot { padding: var(--sp-3) var(--sp-5) var(--sp-4); border-top: 1px solid var(--c-br1); margin: 0; }
  /* Schalter wie in den Einstellungen */
  .tog {
    position: relative; width: 44px; height: 24px; flex-shrink: 0; cursor: pointer;
    border-radius: 12px; border: 1px solid var(--c-br3); background: var(--c-bg2);
    transition: background .15s, border-color .15s;
  }
  .tog::after {
    content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%;
    background: var(--c-tx4); transition: transform .15s, background .15s;
  }
  .tog.on { background: var(--c-accent); border-color: var(--c-accent); }
  .tog.on::after { transform: translateX(20px); background: var(--c-on-accent); }
  .spacer { flex: 1; }
</style>
