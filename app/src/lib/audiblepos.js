// Hoerbare Stelle eines <audio>-Elements. Chromium springt in MP3s ungenau:
// nach currentTime = 65.854 klingt die Stelle 65.50 oder 66.23 (gemessen
// 10/2026 an echten Titeln: -355…+378 ms, danach gleichbleibend). currentTime
// meldet trotzdem die gewuenschte Zeit — der Beat-Sync hielt die Titel fuer
// synchron, und die Schlaege lagen hoerbar daneben (Double Drop!).
//
// Deshalb wird nach jedem Start/Sprung gemessen: ein Worklet nimmt 3 s die
// Huellkurven (je 1 ms) von Hoehen und Bass des Decks auf, das Backend liefert
// dieselbe Stelle exakt dekodiert, die Kreuzkorrelation ergibt den Versatz.
// posOf(el) = currentTime + Versatz ist die Stelle, die man hoert.

const SRC = `
class Meter extends AudioWorkletProcessor {
  constructor() {
    super()
    this.on = false
    this.reset()
    this.port.onmessage = (e) => { this.on = !!e.data.on; this.reset() }
  }
  reset() { this.b = -1; this.sh = 0; this.sl = 0; this.n = 0; this.b0 = -1; this.hi = []; this.lo = [] }
  flush() {
    if (this.n > 0) { this.hi.push(this.sh / this.n); this.lo.push(this.sl / this.n) }
    if (this.hi.length >= 64) {
      this.port.postMessage({ b0: this.b0, hi: Float32Array.from(this.hi), lo: Float32Array.from(this.lo) })
      this.b0 += this.hi.length; this.hi = []; this.lo = []
    }
  }
  process(inputs) {
    if (!this.on) return true
    const hi = inputs[0], lo = inputs[1]
    const n = (hi[0] || lo[0] || []).length || 128
    for (let i = 0; i < n; i++) {
      const b = Math.floor((currentFrame + i) * 1000 / sampleRate)
      if (b !== this.b) {
        if (this.b >= 0) this.flush(); else this.b0 = b
        this.b = b; this.sh = 0; this.sl = 0; this.n = 0
      }
      let h = 0, l = 0
      for (let c = 0; c < hi.length; c++) h += hi[c][i]
      for (let c = 0; c < lo.length; c++) l += lo[c][i]
      h /= Math.max(1, hi.length); l /= Math.max(1, lo.length)
      this.sh += h * h; this.sl += l * l; this.n++
    }
    return true
  }
}
registerProcessor('synthimix-meter', Meter)
`

const WIN_MS = 3000          // so lange zuhoeren
const SKIP_MS = 300          // Anlauf nach dem Start nicht mitnehmen
const LAG_MS = 1500          // so weit kann ein Sprung danebenliegen (gemessen bis 640 ms)
const SMOOTH = 81            // gleitendes Mittel (ms) fuer die Hochpass-Huelle
const MIN_SCORE = 0.3, MIN_RATIO = 1.08
const GIVE_UP_MS = 9000      // laenger nicht auf eine Messung warten (stilles Intro …)
// Gleichbleibender Anteil, der auch ohne Sprung gemessen wird (Puffer des
// Players, Messkette) — gilt fuer jedes Deck gleich, wird abgezogen
export const BIAS = 0

/** Huelle fuer den Vergleich: log-Energie minus gleitendes Mittel. */
export function feature(e) {
  const n = e.length, x = new Float32Array(n), y = new Float32Array(n)
  for (let i = 0; i < n; i++) x[i] = Math.log(1e-10 + e[i])
  const h = SMOOTH >> 1
  let s = 0, c = 0
  for (let i = 0; i < Math.min(n, h); i++) { s += x[i]; c++ }
  for (let i = 0; i < n; i++) {
    if (i + h < n) { s += x[i + h]; c++ }
    if (i - h - 1 >= 0) { s -= x[i - h - 1]; c-- }
    y[i] = x[i] - s / c
  }
  return y
}

/**
 * Versatz suchen: meas (media-Zeitraster ab m0, 1 ms) gegen ref (ab r0, 1 ms),
 * je Band. Liefert {lag (s), score, ratio} — lag > 0: es klingt eine spaetere
 * Stelle als gemeldet.
 */
export function bestLag(meas, m0, ref, r0, maxLag = LAG_MS) {
  const off = Math.round((m0 - r0) * 1000)          // Index in ref fuer lag 0
  const n = meas[0].length
  const scores = new Float32Array(2 * maxLag + 1).fill(-1)
  const norm = (a, i0, len) => { let s = 0; for (let i = i0; i < i0 + len; i++) s += a[i] * a[i]; return Math.sqrt(s) || 1 }
  const mn = meas.map(a => norm(a, 0, n))
  for (let L = -maxLag; L <= maxLag; L++) {
    const j0 = off + L
    if (j0 < 0 || j0 + n > ref[0].length) continue
    let tot = 0
    for (let b = 0; b < meas.length; b++) {
      const a = meas[b], r = ref[b]
      let s = 0
      for (let i = 0; i < n; i++) s += a[i] * r[j0 + i]
      tot += s / (mn[b] * norm(r, j0, n))
    }
    scores[L + maxLag] = tot / meas.length
  }
  let bi = 0
  for (let i = 1; i < scores.length; i++) if (scores[i] > scores[bi]) bi = i
  let second = -1
  for (let i = 0; i < scores.length; i++) if (Math.abs(i - bi) > 30 && scores[i] > second) second = scores[i]
  // fein: Parabel durch die Nachbarn
  let fine = 0
  if (bi > 0 && bi < scores.length - 1) {
    const a = scores[bi - 1], b = scores[bi], c = scores[bi + 1], d = a - 2 * b + c
    if (d < 0) fine = 0.5 * (a - c) / d
  }
  const best = scores[bi]
  return { lag: (bi - maxLag + fine) / 1000, score: best, ratio: second > 0 ? best / second : Infinity }
}

/** Gemessene Bins (Audio-Uhr, 1 ms) auf das gemeldete Medienzeit-Raster legen. */
function toMedia(bins, map, m0, n) {
  const out = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    const g = map.toGraph(m0 + i / 1000)                  // Audio-Uhr (s)
    const k = g * 1000 - bins.b0
    const k0 = Math.floor(k), f = k - k0
    const v0 = bins.v[Math.max(0, Math.min(bins.v.length - 1, k0))]
    const v1 = bins.v[Math.max(0, Math.min(bins.v.length - 1, k0 + 1))]
    out[i] = v0 + (v1 - v0) * f
  }
  return out
}

/**
 * Audio-Uhr -> gemeldete Medienzeit aus den mitgeschriebenen Paaren. Geglaettet
 * und stueckweise (das Tempo kann sich waehrend der Messung aendern: Treffen
 * in der Mitte, Nachziehen). Liefert {toMedia(g), toGraph(m)} oder null.
 */
export function pairMap(pairs, k = 9) {
  if (pairs.length < 2 * k) return null
  const G = [], M = []
  for (let i = 0; i + k <= pairs.length; i++) {
    let g = 0, m = 0
    for (let j = i; j < i + k; j++) { g += pairs[j][0]; m += pairs[j][1] }
    G.push(g / k); M.push(m / k)
  }
  for (let i = 1; i < M.length; i++) if (!(M[i] > M[i - 1] - 1e-4) || !(G[i] > G[i - 1])) return null
  const interp = (xs, ys, x) => {
    let i = 1
    if (x <= xs[0]) i = 1
    else if (x >= xs[xs.length - 1]) i = xs.length - 1
    else { let lo = 0, hi = xs.length - 1; while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] <= x) lo = mid; else hi = mid } i = hi }
    const x0 = xs[i - 1], x1 = xs[i], y0 = ys[i - 1], y1 = ys[i]
    return x1 > x0 ? y0 + (y1 - y0) * (x - x0) / (x1 - x0) : y0
  }
  return { toMedia: (g) => interp(G, M, g), toGraph: (m) => interp(M, G, m) }
}

/**
 * Messung fuer die Decks einrichten. decks: [{el, src}] (src: MediaElementSource),
 * requestRef(path, start, dur) -> Promise<{start, hi: Float32Array, lo: Float32Array}>.
 * Liefert { posOf(el), offsetOf(el), state() }.
 */
export async function createAudible(ctx, decks, requestRef) {
  const url = URL.createObjectURL(new Blob([SRC], { type: 'application/javascript' }))
  try { await ctx.audioWorklet.addModule(url) } finally { URL.revokeObjectURL(url) }
  const offs = new Map()          // el -> Sekunden (hoerbar - gemeldet)
  const since = new Map()         // el -> seit wann ungemessen (ms); gescheitert = 0
  const info = new Map()          // el -> letzte Messung (Anzeige/Tests)
  const mute = ctx.createGain(); mute.gain.value = 0; mute.connect(ctx.destination)
  for (const { el, src } of decks) {
    const hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 2000
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 10000
    const lb = ctx.createBiquadFilter(); lb.type = 'lowpass'; lb.frequency.value = 200
    const meter = new AudioWorkletNode(ctx, 'synthimix-meter', { numberOfInputs: 2, numberOfOutputs: 1 })
    meter.connect(mute)                   // stumm, aber im Graphen (sonst rechnet er nicht)
    src.connect(hp); hp.connect(lp); lp.connect(meter, 0, 0)
    src.connect(lb); lb.connect(meter, 0, 1)
    let run = 0                    // Messlauf; ein Sprung macht ihn ungueltig
    let need = true, busy = false
    // Wiedergabe-Art bei der Messung: Chromium schaltet bei Tempo != 1 einen
    // Resampler (ohne Tonhoehen-Erhalt) bzw. die Zeitdehnung dazu — das
    // verschiebt das Hoerbare noch einmal um ~15 ms (gemessen 10/2026)
    const modeOf = () => Math.abs(el.playbackRate - 1) < 1e-6 ? 'eins' : el.preservesPitch ? 'dehnen' : 'resample'
    let mMode = '', mRate = 1
    const invalidate = () => { offs.delete(el); since.set(el, performance.now()); need = true; run++; meter.port.postMessage({ on: false }) }
    el.addEventListener('seeking', invalidate)
    el.addEventListener('emptied', invalidate)
    el.addEventListener('loadstart', invalidate)
    const measure = async (tries = 3) => {
      busy = true
      try { await measureOnce(tries) } finally { busy = false }
    }
    const measureOnce = async (tries) => {
      const my = ++run
      need = false
      const path = el.dataset.path
      const hi = [], lo = [], pairs = []
      let b0 = -1
      meter.port.onmessage = (e) => {
        if (my !== run) return
        if (b0 < 0) b0 = e.data.b0
        hi.push(e.data.hi); lo.push(e.data.lo)
      }
      meter.port.postMessage({ on: true })
      const t0 = performance.now()
      // Wiedergabe-Art waehrend des Fensters; wechselt sie, gilt der Wert nur vorlaeufig
      const mode0 = modeOf(), rate0 = el.playbackRate
      let mixed = false
      await new Promise(res => {
        const iv = setInterval(() => {
          if (my !== run || el.paused) { clearInterval(iv); res(); return }
          if (modeOf() !== mode0) mixed = true
          pairs.push([ctx.currentTime, el.currentTime])
          if (performance.now() - t0 > WIN_MS + SKIP_MS + 150) { clearInterval(iv); res() }
        }, 15)
      })
      meter.port.postMessage({ on: false })
      if (my !== run) return
      if (el.paused || pairs.length < 50 || b0 < 0) { need = true; return }
      const cat = (arr) => { const n = arr.reduce((s, a) => s + a.length, 0), o = new Float32Array(n); let k = 0; for (const a of arr) { o.set(a, k); k += a.length } return o }
      const H = cat(hi), Lo = cat(lo)
      const map = pairMap(pairs)
      if (!map) { need = true; return }
      // Fenster: nach dem Anlauf, so lang wie aufgenommen (und mitgeschrieben)
      const gStart = Math.max((b0 + SKIP_MS) / 1000, pairs[5][0]), gEnd = Math.min((b0 + H.length - 5) / 1000, pairs[pairs.length - 5][0])
      const m0 = map.toMedia(gStart), n = Math.floor((map.toMedia(gEnd) - m0) * 1000)
      if (n < 1500) { need = true; return }
      const level = H.reduce((s, v) => s + v, 0) / H.length
      if (!(level > 1e-7)) { if (tries > 1) return measureOnce(tries - 1); need = true; return }
      let ref
      try { ref = await requestRef(path, m0 - LAG_MS / 1000 - 0.05, n / 1000 + 2 * LAG_MS / 1000 + 0.1) } catch { ref = null }
      if (my !== run || !ref || el.dataset.path !== path) { if (my === run) need = true; return }
      const mh = feature(toMedia({ b0, v: H }, map, m0, n)), ml = feature(toMedia({ b0, v: Lo }, map, m0, n))
      const res = bestLag([mh, ml], m0, [feature(ref.hi), feature(ref.lo)], ref.start)
      info.set(el, { ...res, at: m0, path })
      if (res.score >= MIN_SCORE && res.ratio >= MIN_RATIO) { offs.set(el, res.lag - BIAS); mMode = mixed ? '' : mode0; mRate = rate0 }
      else if (tries > 1) return measureOnce(tries - 1)
      else { since.set(el, 0); mMode = modeOf(); mRate = el.playbackRate }   // nicht nochmal versuchen   // nicht eindeutig: bisheriger Wert gilt
    }
    const kick = () => {
      if (busy || el.paused || el.seeking || !el.dataset.path) return
      // neu messen nach Sprung/Start, oder wenn die Wiedergabe-Art gewechselt hat
      // (der alte Wert gilt bis dahin weiter)
      if (need || (offs.has(el) && (modeOf() !== mMode || Math.abs(el.playbackRate - mRate) > 0.03))) measure()
    }
    el.addEventListener('playing', kick)
    el.addEventListener('seeked', () => setTimeout(kick, 50))
    setInterval(kick, 1000)
  }
  return {
    offsetOf: (el) => offs.get(el) ?? 0,
    // Gemessen, gescheitert oder zu lange ohne Ergebnis: dann nicht mehr warten
    known: (el) => offs.has(el) || !since.has(el) || performance.now() - since.get(el) > GIVE_UP_MS,
    posOf: (el) => el.currentTime + (offs.get(el) ?? 0),
    state: () => [...info.entries()].map(([el, i]) => ({ deck: el.id || '', ...i, offset: offs.get(el) ?? null })),
  }
}
