<script>
  // Kleine Skizzen zu den Blend-Einstellungen. Zeigen schematisch, was eine
  // Einstellung bewirkt — die Kurven entsprechen _fade() im Player, Farbband
  // und Mix-Zone sehen aus wie im Player (Waveform.svelte: drawBand, mixZone).
  //   kind: 'curve' (value = 'cosine'|'linear'|'scurve')
  //         'intro' (value = Stufe 1–5), 'outro' (value = Stufe 1–5)
  //         'beat'  (value = an/aus)
  let { kind, value, dimmed = false } = $props()

  const W = 260, PAD = 6
  const BAND = 14                       // Farbband oben: Beschriftung + Linie
  const H = $derived(kind === 'curve' ? 76 : 76 + BAND)
  const X = (f) => PAD + f * (W - 2 * PAD)

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
  // Wellenform unter dem Band
  const WF_TOP = BAND + 2, WF_H = 76 - 18
  const barY = (a) => WF_TOP + WF_H / 2 - a * (WF_H - 6) / 2
  const barH = (a) => a * (WF_H - 6)

  // Farbband wie im Player: INTRO/OUTRO blau, MIX orange (liegt darueber und
  // wird zuerst beschriftet; die anderen nur, wenn Platz ist)
  const LBL_W = { INTRO: 25, OUTRO: 30, MIX: 17 }
  function band(segs) {
    const order = [...segs.filter(s => s.name === 'MIX'), ...segs.filter(s => s.name !== 'MIX')]
    const taken = []
    const labels = []
    for (const s of order) {
      const tw = LBL_W[s.name]
      let x0 = s.align === 'right' ? X(s.r[1]) - 1 - tw : X(s.r[0]) + 1
      x0 = Math.max(PAD, Math.min(W - PAD - tw, x0))
      const box = [x0 - 3, x0 + tw + 3]
      if (taken.some(t => box[0] < t[1] && box[1] > t[0])) continue
      taken.push(box)
      labels.push({ ...s, x: x0 })
    }
    return { segs: [...segs.filter(s => s.name !== 'MIX'), ...segs.filter(s => s.name === 'MIX')], labels }
  }
</script>

{#snippet bandSvg(b)}
  {#each b.segs as s}
    <rect class="band {s.cls}" x={X(s.r[0])} y={BAND - 5} width={Math.max(2, X(s.r[1]) - X(s.r[0]))} height="3" />
  {/each}
  {#each b.labels as s}
    <text class="blbl {s.cls}" x={s.x} y="7">{s.name}</text>
  {/each}
{/snippet}

{#snippet mixZone(x0, x1)}
  <rect class="mz" x={X(x0)} y={WF_TOP} width={X(x1) - X(x0)} height={WF_H} />
  <line class="mz-edge" x1={X(x0) + 0.5} y1={WF_TOP} x2={X(x0) + 0.5} y2={WF_TOP + WF_H} />
  <line class="mz-edge" x1={X(x1) - 0.5} y1={WF_TOP} x2={X(x1) - 0.5} y2={WF_TOP + WF_H} />
{/snippet}

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
    {@const intro = 0.3}
    {@const skip = intro * INTRO_SKIP[(value ?? 3) - 1]}
    {@const mixEnd = Math.min(1, skip + 0.16)}
    {@render bandSvg(band([{ r: [0, Math.max(intro, skip)], cls: 'intro', name: 'INTRO', align: 'left' },
                           { r: [skip, mixEnd], cls: 'mix', name: 'MIX', align: 'left' }]))}
    {#each introBars as a, i}
      <rect class={i / N < skip ? 'bar off' : 'bar b'} x={PAD + i * bw + 0.5} y={barY(a)} width={bw - 1} height={barH(a)} />
    {/each}
    {@render mixZone(skip, mixEnd)}
    <text class="lbl" x={Math.max(X(skip) + 4, skip > 0.05 ? PAD + 66 : 0)} y={H - 3}>Start von Titel B</text>
    <text class="lbl muted" x={PAD + 2} y={H - 3}>{skip > 0.05 ? 'übersprungen' : ''}</text>

  {:else if kind === 'outro'}
    {@const silence = 0.72}
    {@const blend = 0.1}
    {@const start = Math.max(0.3, silence - OUTRO_EARLY[(value ?? 3) - 1] * blend)}
    {@const mixEnd = Math.min(1, start + blend * 1.2)}
    {@render bandSvg(band([{ r: [Math.min(silence, mixEnd), 1], cls: 'outro', name: 'OUTRO', align: 'right' },
                           { r: [start, mixEnd], cls: 'mix', name: 'MIX', align: 'left' }]))}
    {#each outroBars as a, i}
      <rect class="bar a" x={PAD + i * bw + 0.5} y={barY(a)} width={bw - 1} height={barH(a)} />
    {/each}
    {@render mixZone(start, mixEnd)}
    <line class="mark dashed" x1={X(silence)} y1={WF_TOP} x2={X(silence)} y2={WF_TOP + WF_H} />
    <text class="lbl" x={X(start) - 2} y={H - 3} text-anchor="end">Mix beginnt</text>
    <text class="lbl muted" x={X(silence) + 3} y={H - 3}>Stille</text>

  {:else if kind === 'beat'}
    {@const want = 0.47}
    {@const snapped = value ? 0.5 : want}
    {@render bandSvg(band([{ r: [snapped, Math.min(1, snapped + 0.3)], cls: 'mix', name: 'MIX', align: 'left' }]))}
    {#each Array(17) as _, i}
      <line class={i % 4 === 0 ? 'grid bar-line' : 'grid'} x1={X(i / 16)} y1={i % 4 === 0 ? WF_TOP : WF_TOP + 12} x2={X(i / 16)} y2={H - 16} />
    {/each}
    {@render mixZone(snapped, Math.min(1, snapped + 0.3))}
    {#if value}
      <line class="mark dashed faint" x1={X(want)} y1={WF_TOP} x2={X(want)} y2={H - 14} />
    {/if}
    <text class="lbl" x={X(snapped) + 4} y={H - 3}>{value ? 'Übergang auf dem Takt' : 'Übergang zur festen Zeit'}</text>
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
  /* Farbband und Mix-Zone wie im Player */
  .band.intro { fill: var(--c-wf-intro); }
  .band.outro { fill: var(--c-wf-outro); }
  .band.mix   { fill: var(--c-wf-mix); }
  .blbl { font: 700 9px 'Segoe UI', system-ui, sans-serif; }
  .blbl.intro { fill: var(--c-wf-intro); }
  .blbl.outro { fill: var(--c-wf-outro); }
  .blbl.mix   { fill: var(--c-wf-mix); }
  .mz { fill: var(--c-wf-mix); opacity: .12; }
  .mz-edge { stroke: var(--c-wf-mix); stroke-width: 1; opacity: .75; }
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
