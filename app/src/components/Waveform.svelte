<script>
  // Oben ein Farbband: Intro (bis introMark), Mix (die Zonen), Outro (ab
  // outroMark). bandLabels=false: nur die Streifen (z. B. waehrend die untere
  // Waveform beim Uebergang hochwaechst und sich streckt)
  let { data = [], position = 0, onclick, introStart = 0, introEnd = -1, outroStart = -1, outroEnd = -1, height = 36, loading = false,
        dragZone = null, onzonedrag = null, zoneTitle = '',
        bandLabels = true, introMark = -1, outroMark = -1 } = $props()
  const BAND_H = 12

  let canvas = $state(null)

  // ── MIX-Zone ziehen ─────────────────────────────────────────────────────
  // dragZone: 'outro' (laufender Titel), 'intro' oder 'both' (naechster Titel:
  // Einstieg und spaeteres Ausmischen). onzonedrag(beginn, fertig, art) meldet
  // den neuen Beginn der Zone (0…1). zoneTitle: Text oder { intro, outro }.
  let drag = null                  // { grab, kind } Abstand Zeiger → Zonenbeginn
  let hoverZone = $state(false)    // false oder 'intro' / 'outro'
  let dragging  = $state(false)
  let suppressClick = false
  function zones() {
    const z = []
    if ((dragZone === 'outro' || dragZone === 'both') && outroStart >= 0 && outroEnd > outroStart) z.push({ kind: 'outro', r: [outroStart, outroEnd] })
    if ((dragZone === 'intro' || dragZone === 'both') && introEnd > 0 && introEnd > introStart) z.push({ kind: 'intro', r: [introStart, introEnd] })
    return z
  }
  function fracAt(e, el) {
    const r = el.getBoundingClientRect()
    return Math.max(0, Math.min(1, (e.clientX - r.left) / Math.max(1, r.width)))
  }
  function inZone(e, el) {
    if (!onzonedrag) return null
    const f = fracAt(e, el), pad = 6 / Math.max(1, el.getBoundingClientRect().width)
    const hit = zones().find(z => f >= z.r[0] - pad && f <= z.r[1] + pad)
    return hit ? { f, z: hit.r, kind: hit.kind } : null
  }
  const hoverTitle = $derived(!hoverZone ? '' : typeof zoneTitle === 'string' ? zoneTitle : (zoneTitle?.[hoverZone] ?? ''))
  function onDown(e) {
    const hit = inZone(e, e.currentTarget)
    if (!hit || e.button !== 0) return
    e.preventDefault()
    drag = { grab: hit.f - hit.z[0], kind: hit.kind }
    dragging = true
    suppressClick = true            // Klick in die Zone springt nicht dorthin
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  function onMove(e) {
    if (drag) { onzonedrag(Math.max(0, fracAt(e, e.currentTarget) - drag.grab), false, drag.kind); return }
    hoverZone = inZone(e, e.currentTarget)?.kind ?? false
  }
  function onUp(e) {
    if (!drag) return
    onzonedrag(Math.max(0, fracAt(e, e.currentTarget) - drag.grab), true, drag.kind)
    drag = null; dragging = false
  }
  function onClickWrap(e) {
    if (suppressClick) { suppressClick = false; return }
    onclick?.(e)
  }

  // Die Farben stecken in CSS-Variablen, die das Canvas nicht von selbst
  // mitbekommt — beim Theme-Wechsel muss neu gezeichnet werden, sonst bleibt
  // z.B. der Playhead in der Farbe des alten Themes stehen.
  $effect(() => {
    const obs = new MutationObserver(() => { if (canvas) draw() })
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => obs.disconnect()
  })

  // draw() liest alle Props selbst: der Effekt laeuft bei jeder Aenderung einmal
  $effect(() => { if (canvas) draw() })

  // Mix-Zone in der Waveform: leicht getoent, orange Kanten (zum Farbband)
  function mixZone(ctx, x0, x1, w, top, h, col) {
    const a = Math.round(Math.max(0, x0) * w), b = Math.round(Math.min(1, x1) * w)
    if (b <= a) return
    ctx.save()
    ctx.globalAlpha = 0.12; ctx.fillStyle = col; ctx.fillRect(a, top, b - a, h)
    ctx.globalAlpha = 0.75; ctx.strokeStyle = col; ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(a + 0.5, top); ctx.lineTo(a + 0.5, top + h); ctx.moveTo(b - 0.5, top); ctx.lineTo(b - 0.5, top + h); ctx.stroke()
    ctx.restore()
  }

  function drawBand(ctx, w, theme) {
    const segs = []
    const inMix  = introEnd > 0 && introEnd > introStart ? [introStart, introEnd] : null
    const outMix = outroStart >= 0 && outroEnd > outroStart ? [outroStart, outroEnd] : null
    // Intro und Outro schliessen ohne Luecke an die Mix-Zonen an (die liegt darueber)
    if (introMark > 0.005)
      segs.push({ r: [0, inMix ? Math.max(introMark, inMix[0]) : introMark], col: theme.intro, name: 'INTRO', align: 'left' })
    if (outroMark > 0 && outroMark < 0.995)
      segs.push({ r: [outMix ? Math.min(outroMark, outMix[1]) : outroMark, 1], col: theme.outro, name: 'OUTRO', align: 'right' })
    for (const m of [inMix, outMix]) if (m) segs.push({ r: m, col: theme.mix, name: 'MIX', align: 'left', prio: true })
    const lineY = BAND_H - 4
    for (const sg of segs) {
      const a = Math.round(sg.r[0] * w), b = Math.round(sg.r[1] * w)
      ctx.fillStyle = sg.col
      ctx.fillRect(a, lineY, Math.max(2, b - a), 3)
    }
    if (!bandLabels) return
    // Beschriftung: MIX zuerst, die anderen nur, wenn Platz ist
    ctx.font = `700 9px 'Segoe UI', system-ui, sans-serif`
    ctx.textBaseline = 'middle'
    const taken = []
    const order = [...segs.filter(sg => sg.prio), ...segs.filter(sg => !sg.prio)]
    for (const sg of order) {
      const a = sg.r[0] * w, b = sg.r[1] * w
      const tw = ctx.measureText(sg.name).width
      let x0 = sg.align === 'right' ? b - 1 - tw : a + 1
      x0 = Math.max(0, Math.min(w - tw, x0))
      const box = [x0 - 3, x0 + tw + 3]
      if (taken.some(t => box[0] < t[1] && box[1] > t[0])) continue
      taken.push(box)
      ctx.fillStyle = sg.col
      ctx.textAlign = 'left'
      ctx.fillText(sg.name, x0, 4.5)
    }
  }

  function draw() {
    const ctx = canvas.getContext('2d')
    const w = canvas.width  = canvas.offsetWidth
    const H = canvas.height = canvas.offsetHeight
    if (w === 0 || H === 0) return
    const top = BAND_H
    const h = H - top
    const mid = top + h / 2
    ctx.clearRect(0, 0, w, H)

    const px = position * w

    // ── Base waveform bars ────────────────────────────────────────────────
    const cs = getComputedStyle(document.documentElement)
    const v = (name, fallback) => cs.getPropertyValue(name).trim() || fallback
    const cPlayed   = v('--c-accent', '#e07800')
    const cUnplayed = v('--c-br2',    '#1a2838')
    const theme = {
      head:  v('--c-wf-head', 'rgba(255,255,255,0.90)'),
      intro: v('--c-wf-intro', '#4f8fe8'),
      outro: v('--c-wf-outro', '#4f8fe8'),
      mix:   v('--c-wf-mix',   '#ff9a33'),
    }
    if (data.length > 0) {
      const bw = w / data.length
      data.forEach((amp, i) => {
        const x  = i * bw
        const bh = Math.max(1, amp * mid * 0.85)
        ctx.fillStyle = x < px ? cPlayed : cUnplayed
        ctx.fillRect(Math.floor(x), mid - bh, Math.max(1, Math.floor(bw)), bh * 2)
      })
    } else {
      ctx.fillStyle = cPlayed
      ctx.fillRect(0, mid - 1, px, 2)
      ctx.fillStyle = cUnplayed
      ctx.fillRect(px, mid - 1, w - px, 2)
    }

    // Farbband oben, Mix-Zonen in der Waveform nur leicht getoent
    drawBand(ctx, w, theme)
    if (introEnd > 0 && introEnd <= 1) mixZone(ctx, introStart, introEnd, w, top, h, theme.mix)
    if (outroStart >= 0 && outroEnd > outroStart) mixZone(ctx, outroStart, outroEnd, w, top, h, theme.mix)

    // ── Playhead ──────────────────────────────────────────────────────────
    ctx.save()
    ctx.strokeStyle = theme.head
    ctx.lineWidth = 1.5
    ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, top + h); ctx.stroke()
    ctx.restore()
  }
</script>

<div class="waveform-wrap" class:zone-hover={!!hoverZone} class:zone-drag={dragging} style="height:{height + BAND_H}px"
  role="slider" aria-valuenow={Math.round(position * 100)}
  title={hoverTitle}
  onpointerdown={onDown} onpointermove={onMove} onpointerup={onUp} onpointercancel={onUp}
  onpointerleave={() => { if (!drag) hoverZone = false }}
  onclick={onClickWrap} tabindex={onclick ? 0 : -1}>
  <canvas bind:this={canvas}></canvas>
  {#if loading && data.length === 0}
    <div class="wf-loading"></div>
  {/if}
</div>

<style>
  .waveform-wrap { width: 100%; cursor: pointer; border-radius: 2px; overflow: hidden; position: relative; touch-action: none; }
  .waveform-wrap.zone-hover { cursor: grab; }
  .waveform-wrap.zone-drag  { cursor: grabbing; }
  canvas { width: 100%; height: 100%; display: block; }
  .wf-loading {
    position: absolute; inset: 0;
    background: linear-gradient(90deg, transparent 0%, var(--c-br2) 50%, transparent 100%);
    background-size: 200% 100%;
    animation: wf-shimmer 1.4s ease-in-out infinite;
    border-radius: 2px;
    pointer-events: none;
  }
  @keyframes wf-shimmer {
    0%   { background-position: 200% 0; }
    100% { background-position: -200% 0; }
  }
</style>
