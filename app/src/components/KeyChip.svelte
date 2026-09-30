<script>
  // Tonart als farbiger Chip — ein Baustein fuer Bibliothek, Queue und Player.
  // Farbe je Camelot-Nummer, Schreibweise nach Einstellung (Noten / Camelot).
  // Mit compat wird der Uebergang vom vorherigen Titel als Zeichen davor
  // gesetzt: ● gleich, ✓ passt, ⚠ passt nicht — nicht nur als Farbe.
  // (Kein ✕: direkt neben dem Chip sah das aus wie ein Loeschknopf.)
  // ring (Warteschlange): kompakt am Chip — roter Rand = passt nicht, dahinter
  // ein schlanker Pfeil nach oben = Energie-Schub (boost). Der Platz fuer den
  // Pfeil ist immer da, damit die Chips untereinander stehen.
  import { appSettings } from '../stores/ws.js'
  import { keyLabel, keyClass, keyTitle } from '../lib/keys.js'

  let { key = '', src = '', compat = null, hint = '', ring = false, boost = false } = $props()

  const SYM = { same: '●', good: '✓', clash: '⚠' }
  const label = $derived(keyLabel(key, $appSettings.keyNotation))
  const title = $derived(keyTitle(key, src) + (compat?.label ? ' · ' + (hint || '') + compat.label : '')
                         + (boost ? ' · Energie-Schub: Tonart oder Tempo gehen hier bewusst nach oben' : ''))
</script>

{#if key}
  {#if ring}
    <span class="ring-wrap" {title}>
      <!-- Beim Energie-Schub ist der Tonart-Sprung gewollt: dann kein roter Rand -->
      <span class="kc {keyClass(key)}" class:est={src === 'analyse'} class:r-clash={!boost && compat?.level === 'clash'}>{label}</span>
      <span class="boost-slot" aria-hidden="true">
        {#if boost}
          <svg width="7" height="11" viewBox="0 0 7 11"><path d="M3.5 10.5V1M1.3 3.2 3.5 1 5.7 3.2" /></svg>
        {/if}
      </span>
    </span>
  {:else if compat && SYM[compat.level]}
    <span class="cmp cmp-{compat.level}" {title}>
      <span aria-hidden="true">{SYM[compat.level]}</span>
      <span class="kc {keyClass(key)}" class:est={src === 'analyse'}>{label}</span>
    </span>
  {:else}
    <span class="kc {keyClass(key)}" class:est={src === 'analyse'} {title}>{label}</span>
  {/if}
{/if}

<style>
  .ring-wrap { display: inline-flex; align-items: center; gap: 2px; }
  .r-clash { box-shadow: inset 0 0 0 1.5px var(--c-red-tx); }
  .boost-slot { width: 8px; display: inline-flex; align-items: center; color: var(--c-accent-tx); }
  .boost-slot path { fill: none; stroke: currentColor; stroke-width: 1.1; stroke-linecap: round; stroke-linejoin: round; }
</style>
