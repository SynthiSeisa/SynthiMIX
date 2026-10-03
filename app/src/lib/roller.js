// Loop-Roll und Backspin als AudioWorklet: nimmt das Signal eines Decks laufend
// auf (6 s) und spielt ab einem geplanten Zeitpunkt etwas anderes ab —
// sample-genau, ohne im Titel zu springen. Plan per port.postMessage:
//   Loop-Roll: { start, end, steps: [{ at, len }] } — immer kuerzerer Loop
//   Backspin:  { kind: 'spin', start, end, top } — rueckwaerts, schnell
//              angerissen und auslaufend (Verlauf wie djfx.spinSpeed); danach stumm.
//              Jede Stufe des Rolls spielt immer wieder den Anfang des Rolls
//              (ab start), nur kuerzer — wie die Loop-Taste am Mischpult
// Zeiten in Frames der AudioContext-Uhr.
const SRC = `
class Roller extends AudioWorkletProcessor {
  constructor() {
    super()
    this.cap = Math.ceil(sampleRate * 6)
    this.buf = [new Float32Array(this.cap), new Float32Array(this.cap)]
    this.plan = null
    this.pos = -1
    this.port.onmessage = (e) => { this.plan = e.data; this.pos = -1 }
  }
  speed(u, top) {
    if (u <= 0) return 0
    if (u < 0.04) return top * (u / 0.04)
    return top * Math.exp(-3.3 * (u - 0.04) / 0.96)
  }
  process(inputs, outputs) {
    const inp = inputs[0], out = outputs[0]
    const n = out[0] ? out[0].length : 128
    const p = this.plan
    const cap = this.cap
    for (let i = 0; i < n; i++) {
      const f = currentFrame + i
      const wi = f % cap
      for (let c = 0; c < 2; c++) {
        const ch = inp.length ? inp[Math.min(c, inp.length - 1)] : null
        this.buf[c][wi] = ch ? ch[i] : 0
      }
      if (p && p.kind === 'spin' && f >= p.start) {
        if (f >= p.end) { for (let c = 0; c < out.length; c++) out[c][i] = 0; continue }
        if (this.pos < 0) this.pos = (p.start - 1) % cap
        const u = (f - p.start) / (p.end - p.start)
        this.pos -= this.speed(u, p.top || 5)
        while (this.pos < 0) this.pos += cap
        const i0 = Math.floor(this.pos), fr = this.pos - i0, i1 = (i0 + 1) % cap
        // kurz einblenden gegen Knacken, im letzten Viertel auslaufen lassen
        const env = Math.min(1, u / 0.01, (1 - u) / 0.25)
        for (let c = 0; c < out.length; c++) {
          const b = this.buf[Math.min(c, 1)]
          out[c][i] = env * (b[i0] * (1 - fr) + b[i1] * fr)
        }
        continue
      }
      let src = wi, g = 1
      if (p && !p.kind && f >= p.start && f < p.end) {
        let len = p.steps[0].len, at = p.steps[0].at
        for (const s of p.steps) if (f >= s.at) { len = s.len; at = s.at }
        const k = (f - at) % len
        src = (p.start + k) % cap
        // Naht des Loops kurz ab- und aufblenden: ohne das knackt jede Wiederholung
        const edge = Math.min(96, len / 4)
        g = Math.min(1, (k + 1) / edge, (len - k) / edge)
      }
      for (let c = 0; c < out.length; c++) out[c][i] = g * this.buf[Math.min(c, 1)][src]
    }
    if (p && currentFrame > p.end + sampleRate) { this.plan = null; this.pos = -1 }
    return true
  }
}
registerProcessor('synthimix-roller', Roller)
`

/** Worklet laden; true, wenn Loop-Roll und Backspin verfuegbar sind. */
export async function loadRoller(ctx) {
  if (!ctx?.audioWorklet) return false
  const url = URL.createObjectURL(new Blob([SRC], { type: 'application/javascript' }))
  try { await ctx.audioWorklet.addModule(url); return true }
  catch { return false }
  finally { URL.revokeObjectURL(url) }
}

export function makeRoller(ctx) {
  return new AudioWorkletNode(ctx, 'synthimix-roller', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2] })
}

/**
 * Nachhall fuer den Hall-Ausklang: Stereo-Rauschen mit exponentiellem
 * Abklingen (RT60 ~ sec), Anfang weich, ohne Dateien.
 */
export function makeHallIR(ctx, sec = 3.2) {
  const sr = ctx.sampleRate, n = Math.floor(sr * sec)
  const ir = ctx.createBuffer(2, n, sr)
  for (let c = 0; c < 2; c++) {
    const d = ir.getChannelData(c)
    let seed = 1234567 + c * 7654321
    for (let i = 0; i < n; i++) {
      seed = (seed * 1103515245 + 12345) >>> 0
      const noise = (seed / 4294967296) * 2 - 1
      const t = i / sr
      const env = Math.exp(-6.9 * t / sec) * Math.min(1, t / 0.02)
      d[i] = noise * env
    }
  }
  return ir
}
