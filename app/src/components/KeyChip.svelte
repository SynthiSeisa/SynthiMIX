<script>
  // Tonart als farbiger Chip — ein Baustein fuer Bibliothek, Queue und Player.
  // Farbe je Camelot-Nummer, Schreibweise nach Einstellung (Noten / Camelot).
  // Mit compat wird der Uebergang vom vorherigen Titel als Zeichen davor
  // gesetzt: ● gleich, ✓ passt, ⚠ passt nicht — nicht nur als Farbe.
  // (Kein ✕: direkt neben dem Chip sah das aus wie ein Loeschknopf.)
  import { appSettings } from '../stores/ws.js'
  import { keyLabel, keyClass, keyTitle } from '../lib/keys.js'

  let { key = '', src = '', compat = null, hint = '' } = $props()

  const SYM = { same: '●', good: '✓', clash: '⚠' }
  const label = $derived(keyLabel(key, $appSettings.keyNotation))
  const title = $derived(keyTitle(key, src) + (compat?.label ? ' · ' + (hint || '') + compat.label : ''))
</script>

{#if key}
  {#if compat && SYM[compat.level]}
    <span class="cmp cmp-{compat.level}" {title}>
      <span aria-hidden="true">{SYM[compat.level]}</span>
      <span class="kc {keyClass(key)}" class:est={src === 'analyse'}>{label}</span>
    </span>
  {:else}
    <span class="kc {keyClass(key)}" class:est={src === 'analyse'} {title}>{label}</span>
  {/if}
{/if}
