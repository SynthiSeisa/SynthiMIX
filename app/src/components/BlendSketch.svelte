<script>
  // Kleine Skizzen zu den Blend-Einstellungen. Zeigen schematisch, was eine
  // Einstellung bewirkt — die Kurven entsprechen _fade() im Player.
  //   kind: 'curve' (value = 'cosine'|'linear'|'scurve')
  //         'intro' (value = Stufe 1–5), 'outro' (value = Stufe 1–5)
  //         'beat'  (value = an/aus)
  let { kind, value, dimmed = false } = $props()

  const W = 260, H = 76, PAD = 6

  function fade(t, curve) {
    if (curve === 'linear') return [1 - t, t]
    if (curve === 'scurve') { const s = t * t * (3 - 2 * t); return [1 - s, s] }
    return [Math.cos(t * Math.PI / 2), Math.sin(t * Math.PI / 2)]
  }
  function curvePath(curve, which) {
    const pts = []
    for (let i = 0; i <= 40; i++) {
      const t = i / 40
      const v = fade(t, curve)[which]
      pts.push(`${PAD + t * (W - 2 * PAD)},${H - PAD - v * (H - 2 * PAD - 12)}`)
    }
    return 'M' + pts.join(' L')
  }

  // Schematische Wellenform: leises Intro, lauter Hauptteil, leises Outro
  const N = 64
  function bars(introEnd, outroStart) {
    const out = []
    for (let i = 0; i < N; i++) {
      const x = i / N
      const noise = 0.75 + 0.25 * Math.abs(Math.sin(i * 12.9898) * 43758.5453 % 1)
      let a = x < introEnd ? 0.18 + 0.25 * (x / introEnd) : x > outroStart ? 0.15 + 0.5 * (1 - (x - outroStart) / (1 - outroStart)) : 0.9
      out.push(a * noise)
    }
    return out
  }
  const INTRO_SKIP = [0.3, 0.5, 0.7, 0.85, 1.0]     // Anteil des erkannten Intros
  const OUTRO_EARLY = [0, 0.5, 1, 1.5, 2]          // x Blendzeit vor der Stille

  const introBars = bars(0.3, 0.92)
  const outroBars = bars(0.05, 0.72)
  const bw = (W - 2 * PAD) / N
</script>

<svg class="sketch" class:dimmed viewBox="0 0 {W} {H}" width={W} height={H} role="img"
     aria-label={kind === 'curve' ? 'Lautstärkeverlauf während des Übergangs' : kind === 'intro' ? 'Welcher Teil des Intros übersprungen wird' : kind === 'outro' ? 'Wo der Übergang am Ende beginnt' : 'Übergang auf der Taktgrenze'}>
  {#if kind === 'curve'}
    <line class="axis" x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} />
    <path class="a" d={curvePath(value, 0)} />
    <path class="b" d={curvePath(value, 1)} />
    <text class="lbl la" x={PAD + 2} y="10">Titel A</text>
    <text class="lbl lb" x={W - PAD - 2} y="10" text-anchor="end">Titel B</text>
    <text class="lbl" x={W / 2} y={H - PAD - 2} text-anchor="middle">Übergang</text>

  {:else if kind === 'intro'}
    {@const skip = 0.3 * INTRO_SKIP[(value ?? 3) - 1]}
    <rect class="zone" x={PAD} y="4" width={skip * (W - 2 * PAD)} height={H - 18} />
    {#each introBars as a, i}
      <rect class={i / N < skip ? 'bar off' : 'bar b'} x={PAD + i * bw + 0.5} y={(H - 14) / 2 - a * (H - 22) / 2 + 2} width={bw - 1} height={a * (H - 22)} />
    {/each}
    <line class="mark" x1={PAD + skip * (W - 2 * PAD)} y1="2" x2={PAD + skip * (W - 2 * PAD)} y2={H - 12} />
    <text class="lbl" x={PAD + skip * (W - 2 * PAD) + 4} y={H - 2}>Start von Titel B</text>
    <text class="lbl muted" x={PAD + 2} y={H - 2}>{skip > 0.05 ? 'übersprungen' : ''}</text>

  {:else if kind === 'outro'}
    {@const silence = 0.72}
    {@const blend = 0.1}
    {@const start = Math.max(0.3, silence - OUTRO_EARLY[(value ?? 3) - 1] * blend)}
    <rect class="zone mix" x={PAD + start * (W - 2 * PAD)} y="4" width={(Math.min(1, start + blend * 1.2) - start) * (W - 2 * PAD)} height={H - 18} />
    {#each outroBars as a, i}
      <rect class="bar a" x={PAD + i * bw + 0.5} y={(H - 14) / 2 - a * (H - 22) / 2 + 2} width={bw - 1} height={a * (H - 22)} />
    {/each}
    <line class="mark dashed" x1={PAD + silence * (W - 2 * PAD)} y1="2" x2={PAD + silence * (W - 2 * PAD)} y2={H - 12} />
    <text class="lbl" x={PAD + start * (W - 2 * PAD) - 2} y={H - 2} text-anchor="end">Mix beginnt</text>
    <text class="lbl muted" x={PAD + silence * (W - 2 * PAD) + 3} y="11">Stille</text>

  {:else if kind === 'beat'}
    {@const want = 0.47}
    {@const snapped = value ? 0.5 : want}
    {#each Array(17) as _, i}
      <line class={i % 4 === 0 ? 'grid bar-line' : 'grid'} x1={PAD + i / 16 * (W - 2 * PAD)} y1={i % 4 === 0 ? 8 : 20} x2={PAD + i / 16 * (W - 2 * PAD)} y2={H - 16} />
    {/each}
    {#if value}
      <line class="mark dashed faint" x1={PAD + want * (W - 2 * PAD)} y1="6" x2={PAD + want * (W - 2 * PAD)} y2={H - 14} />
    {/if}
    <line class="mark" x1={PAD + snapped * (W - 2 * PAD)} y1="4" x2={PAD + snapped * (W - 2 * PAD)} y2={H - 14} />
    <text class="lbl" x={PAD + snapped * (W - 2 * PAD) + 4} y={H - 3}>{value ? 'Übergang auf dem Takt' : 'Übergang zur festen Zeit'}</text>
  {/if}
</svg>

<style>
  .sketch { display: block; max-width: 100%; height: auto; margin-top: var(--sp-2);
            background: var(--c-bg2); border: 1px solid var(--c-br1); border-radius: var(--r-m); }
  .sketch.dimmed { opacity: .45; }
  .axis { stroke: var(--c-br3); stroke-width: 1; }
  path { fill: none; stroke-width: 2.5; stroke-linecap: round; }
  path.a { stroke: var(--c-accent); }
  path.b { stroke: var(--c-blue); }
  .bar.a { fill: var(--c-accent); opacity: .8; }
  .bar.b { fill: var(--c-blue); opacity: .85; }
  .bar.off { fill: var(--c-tx5); opacity: .35; }
  .zone { fill: var(--c-hover); }
  .zone.mix { fill: var(--c-act-bg); stroke: var(--c-accent); stroke-width: 1; stroke-dasharray: 3 2; }
  .mark { stroke: var(--c-tx1); stroke-width: 1.5; }
  .mark.dashed { stroke-dasharray: 3 3; stroke: var(--c-tx3); }
  .mark.faint { opacity: .6; }
  .grid { stroke: var(--c-br2); stroke-width: 1; }
  .grid.bar-line { stroke: var(--c-tx4); }
  .lbl { font: 600 9.5px 'Segoe UI', system-ui, sans-serif; fill: var(--c-tx3); }
  .lbl.la { fill: var(--c-accent-tx); }
  .lbl.lb { fill: var(--c-blue); }
  .lbl.muted { fill: var(--c-tx4); font-weight: 400; }
</style>
