// Loop-Roll als AudioWorklet: nimmt das Signal eines Decks laufend auf (6 s)
// und spielt ab einem geplanten Zeitpunkt einen immer kuerzeren Loop ab —
// sample-genau, ohne im Titel zu springen. Plan per port.postMessage:
// { start, end, steps: [{ at, len }] } in Frames der AudioContext-Uhr.
const SRC = `
class Roller extends AudioWorkletProcessor {
  constructor() {
    super()
    this.cap = Math.ceil(sampleRate * 6)
    this.buf = [new Float32Array(this.cap), new Float32Array(this.cap)]
    this.plan = null
    this.port.onmessage = (e) => { this.plan = e.data }
  }
  process(inputs, outputs) {
    const inp = inputs[0], out = outputs[0]
    const n = out[0] ? out[0].length : 128
    const p = this.plan
    for (let i = 0; i < n; i++) {
      const f = currentFrame + i
      const wi = f % this.cap
      for (let c = 0; c < 2; c++) {
        const ch = inp.length ? inp[Math.min(c, inp.length - 1)] : null
        this.buf[c][wi] = ch ? ch[i] : 0
      }
      let src = wi
      if (p && f >= p.start && f < p.end) {
        let len = p.steps[0].len
        for (const s of p.steps) if (f >= s.at) len = s.len
        src = (p.start + ((f - p.start) % len)) % this.cap
      }
      for (let c = 0; c < out.length; c++) out[c][i] = this.buf[Math.min(c, 1)][src]
    }
    if (p && currentFrame > p.end + sampleRate) this.plan = null
    return true
  }
}
registerProcessor('synthimix-roller', Roller)
`

/** Worklet laden; true, wenn Loop-Roll verfuegbar ist. */
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
