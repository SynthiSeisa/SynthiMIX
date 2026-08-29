<script>
  let { data = [], position = 0, onclick, introStart = 0, introEnd = -1, outroStart = -1, outroEnd = -1, height = 36, loading = false } = $props()

  let canvas = $state(null)

  // Die Farben stecken in CSS-Variablen, die das Canvas nicht von selbst
  // mitbekommt — beim Theme-Wechsel muss neu gezeichnet werden, sonst bleibt
  // z.B. der Playhead in der Farbe des alten Themes stehen.
  $effect(() => {
    const obs = new MutationObserver(() => { if (canvas) draw() })
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => obs.disconnect()
  })

  $effect(() => { if (!canvas) return; draw() })
  $effect(() => { void data; void position; void introStart; void introEnd; void outroStart; void outroEnd; if (canvas) draw() })

  // Solid grey overlay marking a mix zone (Virtual-DJ style), with optional label
  function greyZone(ctx, x0, x1, w, h, label, theme) {
    const a = Math.round(Math.max(0, x0) * w)
    const b = Math.round(Math.min(1, x1) * w)
    if (b <= a) return
    ctx.fillStyle = theme.zone
    ctx.fillRect(a, 0, b - a, h)
    ctx.strokeStyle = theme.edge
    ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(a + 0.5, 0); ctx.lineTo(a + 0.5, h); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(b - 0.5, 0); ctx.lineTo(b - 0.5, h); ctx.stroke()
    if (label && (b - a) > 26 && h >= 16) {
      ctx.fillStyle = theme.label
      ctx.font = `bold ${Math.max(7, Math.floor(h * 0.28))}px monospace`
      ctx.textBaseline = 'middle'
      ctx.fillText(label, a + 4, h / 2)
    }
  }

  function draw() {
    const ctx = canvas.getContext('2d')
    const w = canvas.width  = canvas.offsetWidth
    const h = canvas.height = canvas.offsetHeight
    if (w === 0 || h === 0) return
    const mid = h / 2
    ctx.clearRect(0, 0, w, h)

    const px = position * w

    // ── Base waveform bars ────────────────────────────────────────────────
    const cs = getComputedStyle(document.documentElement)
    const v = (name, fallback) => cs.getPropertyValue(name).trim() || fallback
    const cPlayed   = v('--c-accent', '#e07800')
    const cUnplayed = v('--c-br2',    '#1a2838')
    const theme = {
      head:  v('--c-wf-head',       'rgba(255,255,255,0.90)'),
      zone:  v('--c-wf-zone',       'rgba(86,96,112,0.62)'),
      edge:  v('--c-wf-zone-edge',  'rgba(150,166,190,0.55)'),
      label: v('--c-wf-zone-label', 'rgba(196,208,226,0.82)'),
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

    // ── Intro grey bar: [introStart, introEnd] — crossfade entry zone ──
    if (introEnd > 0 && introEnd <= 1) greyZone(ctx, introStart, introEnd, w, h, 'INTRO', theme)

    // ── Outro grey bar: [outroStart, outroEnd] — the crossfade mix zone ──
    if (outroStart >= 0 && outroEnd > outroStart)
      greyZone(ctx, outroStart, outroEnd, w, h, 'MIX', theme)

    // ── Playhead ──────────────────────────────────────────────────────────
    ctx.save()
    ctx.strokeStyle = theme.head
    ctx.lineWidth = 1.5
    ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, h); ctx.stroke()
    ctx.restore()
  }
</script>

<div class="waveform-wrap" style="height:{height}px"
  role="slider" aria-valuenow={Math.round(position * 100)}
  onclick={onclick} tabindex={onclick ? 0 : -1}>
  <canvas bind:this={canvas}></canvas>
  {#if loading && data.length === 0}
    <div class="wf-loading"></div>
  {/if}
</div>

<style>
  .waveform-wrap { width: 100%; cursor: pointer; border-radius: 2px; overflow: hidden; position: relative; }
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
