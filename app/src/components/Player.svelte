<script>
  import { get } from 'svelte/store'
  import { untrack, onMount } from 'svelte'
  import { keyCompat } from '../lib/keys.js'
  import KeyChip from './KeyChip.svelte'
  import { library, playerState, nowPlaying, queue, waveform, waveformNext, settings, playMode, send, autoMixEnabled, appSettings, introSkipPaths, skipNextCrossfade, livePositionMs, beatGrids, waveformThird, mixNowRequest, requestWaveform } from '../stores/ws.js'
  import { createSync, glideRate, barAlignedStart, snapToPhrase, swapEligible, bassSwapPlan, meetRate, barLen, tempoMatch } from '../lib/beatsync.js'
  import Waveform from './Waveform.svelte'
  import { detectDrops, doubleDropPlan, chooseTransition, rollSteps, DJ_LABEL } from '../lib/djmode.js'
  import { loadRoller, makeRoller } from '../lib/roller.js'

  let elA = $state(null)
  let elB = $state(null)
  let which = $state('A')

  let posMs  = $state(0)
  let durMs  = $state(0)
  let volume = $state(80)
  let cfS    = $state(8)                // Uebergangslaenge in Sekunden (Einstellung, Rueckfall ohne BPM)

  let cfActive     = false
  let cfTimer      = null
  let cfNextIdx    = $state(-1)
  let cfRaf        = null
  let cfRafActive  = $state(false)
  let cfCancelled  = false   // cancels pending canplay.play() on pause
  let loadedUrl    = ''

  // ── Beat-Sync und Anzeige des Uebergangs ──────────────────────────────────
  let cfSync       = null            // Regelkreis waehrend des Uebergangs (lib/beatsync.js)
  let _stopGlide   = () => {}        // Tempo nach dem Uebergang langsam zurueck auf 100 %
  let blendP       = $state(0)       // Fortschritt des automatischen Uebergangs, 0…1
  let blendNextPos = $state(0)       // Position im naechsten Titel waehrend des Uebergangs
  let cfStartAt = 0, cfLenMs = 0     // fuer die Animation
  // Laenge des Uebergangs in Sekunden fuer den laufenden Titel: in Takten
  // (Standard) nach dessen Tempo, ohne bekannte BPM die eingestellten Sekunden.
  const cfEff = $derived.by(() => {
    const cfg = $appSettings
    if ((cfg.cfUnit ?? 'bars') !== 'bars') return cfS
    const bars = cfg.cfBars ?? 16
    if (bars <= 0) return 0
    void $beatGrids
    const g = gridOf($nowPlaying?.path)
    const bpm = g?.bpm || $nowPlaying?.bpm
    if (!(bpm > 30 && bpm < 300)) return cfS > 0 ? cfS : 8
    return Math.min(40, bars * barLen(g ?? { bpm }))
  })
  // Dasselbe fuer einen anderen Titel, wenn er einmal laeuft (Vorschau der Zonen)
  function _cfSecFor(path, bpm) {
    const cfg = get(appSettings)
    if ((cfg.cfUnit ?? 'bars') !== 'bars') return cfS
    const bars = cfg.cfBars ?? 16
    if (bars <= 0) return 0
    const g = gridOf(path)
    const b = g?.bpm || bpm
    if (!(b > 30 && b < 300)) return cfS > 0 ? cfS : 8
    return Math.min(40, bars * barLen(g ?? { bpm: b }))
  }
  function gridOf(path) {
    const g = path ? get(beatGrids)[path] : null
    return g && g.bpm > 0 ? g : null
  }
  function startSync(oldEl, newEl, oldPath, newPath) {
    const cfg = get(appSettings)
    if (!cfg.beatAlignCf && !cfg.tempoMatch) return null
    return createSync(oldEl, newEl, gridOf(oldPath), gridOf(newPath),
                      { tempo: cfg.tempoMatch !== false, phase: !!cfg.beatAlignCf, maxDiff: maxTempoDiff(), meet: true })
  }
  function maxTempoDiff() { return (get(appSettings).maxTempoDiff ?? 8) / 100 }
  // Uebergang abgebrochen: laufender Titel zurueck aufs Original-Tempo
  function cancelSync() {
    if (cfSync) cfSync.abort()
    cfSync = null
  }
  function endSync(el) {
    if (!cfSync) return
    cfSync.settle(); cfSync = null
    _stopGlide(); _stopGlide = glideRate(el, 20000)
  }
  // Animation: der naechste Titel gleitet waehrend des Uebergangs nach oben
  function _blendLoop() {
    if (!cfActive || cfLenMs <= 0) { blendP = 0; return }
    blendP = Math.min(1, (performance.now() - cfStartAt) / cfLenMs)
    requestAnimationFrame(_blendLoop)
  }

  // ── Mix-Zonen per Ziehen verschieben (gilt nur fuer diesen Uebergang) ─────
  let outroOverride = $state(null)   // { path, frac }: Beginn der MIX-Zone im laufenden Titel
  let introOverride = $state(null)   // { path, frac }: Einstieg im naechsten Titel
  let nextOutroOverride = $state(null) // { path, frac }: spaeteres Ausmischen des naechsten Titels (wird zu outroOverride, sobald er laeuft)
  let zoneDragging  = false
  let _cfDoneAt     = 0            // Ende des letzten Uebergangs (performance.now)

  let audioCtx = null
  let gainA = null, gainB = null
  // Angleichen beim Uebergang: der neue Titel steigt so laut ein, wie der
  // alte gerade klingt, und gleitet bis zum Ende des Uebergangs auf seinen
  // normalen Pegel (hinter der Normalisierung, eigener Knoten je Deck)
  let matchA = null, matchB = null
  function matchOf(el) { return el === elA ? matchA : el === elB ? matchB : null }
  function _setMatch(el, db, tc = 0.05) {
    const m = matchOf(el)
    if (!m || !audioCtx) return
    m.gain.setTargetAtTime(Math.pow(10, db / 20), audioCtx.currentTime, tc)
  }
  // DJ-Modus: Filter (Biquad), Loop-Roll (AudioWorklet) und Echo je Deck.
  // Kette: Quelle → Bass-EQ → Filter → Roll → Normalisierung → Angleich → Limiter,
  // Echo zweigt hinter der Normalisierung ab (klingt nach, wenn das Deck stumm ist).
  let fxA = null, fxB = null, rollA = null, rollB = null, sendA = null, sendB = null
  let echoDelay = null, echoFb = null
  let rollerOk = $state(false)
  function fxOf(el)   { return el === elA ? fxA : el === elB ? fxB : null }
  function rollOf(el) { return el === elA ? rollA : el === elB ? rollB : null }
  function sendOf(el) { return el === elA ? sendA : el === elB ? sendB : null }
  function _resetFx(el) {
    const f = fxOf(el), sd = sendOf(el)
    if (!audioCtx) return
    const now = audioCtx.currentTime
    if (f) { f.frequency.cancelScheduledValues(now); f.type = 'lowpass'; f.frequency.setValueAtTime(22000, now) }
    if (sd) { sd.gain.cancelScheduledValues(now); sd.gain.setValueAtTime(0, now) }
    rollOf(el)?.port.postMessage(null)
  }
  function _resetMatch() {
    for (const m of [matchA, matchB]) if (m && audioCtx) { m.gain.cancelScheduledValues(audioCtx.currentTime); m.gain.setTargetAtTime(1, audioCtx.currentTime, 0.05) }
  }
  // Bass je Deck (Low-Shelf vor der Lautstaerke): beim Uebergang laeuft der
  // neue Titel erst ohne Bass mit, in der Mitte wird der Bass weich getauscht
  // — so wummern nie zwei Bassdrums uebereinander.
  let eqA = null, eqB = null
  const BASS_KILL_DB = -30
  let bassSwapOn = $state(false)     // Anzeige waehrend des Uebergangs
  function eqOf(el) { return el === elA ? eqA : el === elB ? eqB : null }
  function resetBass(el, sec = 0.04) {
    const eq = eqOf(el)
    if (!eq || !audioCtx) return
    const now = audioCtx.currentTime
    eq.gain.cancelScheduledValues(now)
    eq.gain.setValueAtTime(eq.gain.value, now)
    eq.gain.linearRampToValueAtTime(0, now + sec)
  }
  function resetAllBass() { resetBass(elA); resetBass(elB); bassSwapOn = false; _resetMatch(); _resetFx(elA); _resetFx(elB) }
  /** Bass-Tausch fuer einen Uebergang einplanen; false = normaler Blend. */
  function planBassSwap(oldEl, newEl, gOld, gNew, fadeSec) {
    const cfg = get(appSettings)
    if (!audioCtx || !eqA || cfg.bassSwap === false || !oldEl || !newEl) return false
    if (!swapEligible(oldEl, gOld, gNew, cfg)) return false
    // Treffen in der Mitte: der alte Titel gleitet in den ersten 40 % auf sein Ziel-Tempo
    const r1 = cfg.tempoMatch !== false ? meetRate(oldEl, gOld, gNew, maxTempoDiff()) : null
    const plan = bassSwapPlan(oldEl.currentTime, oldEl.playbackRate || 1, gOld, fadeSec,
                              r1 ? { r1, sec: 0.4 * fadeSec } : null)
    if (!plan) return false
    const eo = eqOf(oldEl), en = eqOf(newEl), now = audioCtx.currentTime
    const t0 = now + plan.delay, t1 = t0 + plan.len
    for (const eq of [eo, en]) eq.gain.cancelScheduledValues(now)
    eo.gain.setValueAtTime(0, now); eo.gain.setValueAtTime(0, t0); eo.gain.linearRampToValueAtTime(BASS_KILL_DB, t1)
    en.gain.setValueAtTime(BASS_KILL_DB, now); en.gain.setValueAtTime(BASS_KILL_DB, t0); en.gain.linearRampToValueAtTime(0, t1)
    bassSwapOn = true
    return true
  }
  let lufsA = $state(-99)
  let lufsB = $state(-99)

  function cur() { return which === 'A' ? elA : elB }
  function alt() { return which === 'A' ? elB : elA }

  let _volDragging = false
  $effect(() => {
    cfS = $settings.crossfade_s
    if (!_volDragging) {
      volume = $settings.volume
      const el = cur()
      // Waehrend der Pause-Blende (spielt noch, aber "playing" ist schon aus)
      // nichts anfassen — sonst ersetzt die Rampe das Ausblenden und der Titel
      // pausiert nie.
      const playingNow = untrack(() => $playerState.playing)
      if (el && !cfActive && !cfRafActive && !el.paused && playingNow) rampVolume(el, volume / 100, untrack(() => $appSettings.volumeFadeMs ?? 200))
      else if (el && !cfActive && !cfRafActive && el.paused) el.volume = volume / 100
    }
  })

  // ── Sanfte Lautstaerke-Aenderungen ─────────────────────────────────────────
  // setInterval statt requestAnimationFrame, damit es auch minimiert laeuft.
  // Je Element hoechstens eine Rampe; eine neue ersetzt die alte.
  const _ramps = new Map()   // el -> { iv, done }
  function stopRamp(el) {
    const r = _ramps.get(el)
    if (r) { clearInterval(r.iv); _ramps.delete(el) }
  }
  function rampVolume(el, to, ms, done) {
    if (!el) return
    stopRamp(el)
    to = Math.max(0, Math.min(1, to))
    if (!ms || ms <= 0 || Math.abs(el.volume - to) < 0.005) { el.volume = to; done?.(); return }
    const from = el.volume, t0 = performance.now()
    const iv = setInterval(() => {
      const t = Math.min(1, (performance.now() - t0) / ms)
      el.volume = from + (to - from) * t
      if (t >= 1) { stopRamp(el); done?.() }
    }, 15)
    _ramps.set(el, { iv })
  }

  $effect(() => {
    if (!elA || !elB || audioCtx) return
    try {
      audioCtx = new AudioContext()
      // Limiter vor dem Ausgang: bei lautem Normalisierungsziel (bis -5 LUFS)
      // werden leise Titel stark angehoben — Spitzen sollen nicht verzerren.
      // Bei normalen Pegeln greift er nicht ein.
      const limiter = audioCtx.createDynamicsCompressor()
      limiter.threshold.value = -1.5; limiter.knee.value = 0; limiter.ratio.value = 20
      limiter.attack.value = 0.002;   limiter.release.value = 0.15
      limiter.connect(audioCtx.destination)
      matchA = audioCtx.createGain(); matchA.connect(limiter)
      matchB = audioCtx.createGain(); matchB.connect(limiter)
      gainA = audioCtx.createGain(); gainA.connect(matchA)
      gainB = audioCtx.createGain(); gainB.connect(matchB)
      const shelf = () => { const f = audioCtx.createBiquadFilter(); f.type = 'lowshelf'; f.frequency.value = 200; f.gain.value = 0; return f }
      const neutral = () => { const f = audioCtx.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = 22000; f.Q.value = 0.7; return f }
      fxA = neutral(); fxA.connect(gainA)
      fxB = neutral(); fxB.connect(gainB)
      eqA = shelf(); eqA.connect(fxA)
      eqB = shelf(); eqB.connect(fxB)
      // Echo: eine Verzoegerung mit Rueckkopplung fuer beide Decks
      echoDelay = audioCtx.createDelay(4); echoFb = audioCtx.createGain(); echoFb.gain.value = 0.5
      const echoHp = audioCtx.createBiquadFilter(); echoHp.type = 'highpass'; echoHp.frequency.value = 250
      echoDelay.connect(echoFb); echoFb.connect(echoDelay); echoDelay.connect(echoHp); echoHp.connect(limiter)
      sendA = audioCtx.createGain(); sendA.gain.value = 0; gainA.connect(sendA); sendA.connect(echoDelay)
      sendB = audioCtx.createGain(); sendB.gain.value = 0; gainB.connect(sendB); sendB.connect(echoDelay)
      // Loop-Roll: Worklet laedt nebenher und wird dann zwischen Filter und Pegel gehaengt
      const ctx = audioCtx
      loadRoller(ctx).then(ok => {
        if (!ok || ctx !== audioCtx) return
        try {
          rollA = makeRoller(ctx); rollB = makeRoller(ctx)
          fxA.disconnect(); fxA.connect(rollA); rollA.connect(gainA)
          fxB.disconnect(); fxB.connect(rollB); rollB.connect(gainB)
          rollerOk = true
        } catch (e) { console.warn('Loop-Roll:', e) }
      })
      audioCtx.createMediaElementSource(elA).connect(eqA)
      audioCtx.createMediaElementSource(elB).connect(eqB)
      untrack(() => applySink())
    } catch (e) { console.warn('AudioContext:', e) }
  })

  $effect(() => {
    if ($playerState.playing && audioCtx?.state === 'suspended') audioCtx.resume().catch(() => {})
  })

  // Ausgabegeraet: der ganze Audio-Graph geht ueber ein Geraet. Fehlt es
  // (Box aus, Interface abgesteckt), laeuft es ueber den Windows-Standard
  // weiter; kommt es zurueck, wird wieder umgeschaltet.
  let _sinkNow = ''
  async function applySink(notify = false) {
    if (!audioCtx?.setSinkId) return
    const cfg = get(appSettings)
    let want = cfg.outputDevice || ''
    if (want) {
      try {
        const devs = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audiooutput')
        const hit = devs.find(d => d.deviceId === want) || devs.find(d => cfg.outputDeviceLabel && d.label === cfg.outputDeviceLabel)
        if (!hit) {
          if (_sinkNow && notify) _notice(`Ausgabegerät „${cfg.outputDeviceLabel || 'gewählt'}" nicht gefunden — spiele über den Windows-Standard`)
          want = ''
        } else want = hit.deviceId
      } catch { want = '' }
    }
    if (want === _sinkNow) return
    try { await audioCtx.setSinkId(want); _sinkNow = want }
    catch (e) { console.warn('setSinkId:', e); try { await audioCtx.setSinkId(''); _sinkNow = '' } catch {} }
  }
  $effect(() => {
    void $appSettings.outputDevice
    if (audioCtx) untrack(() => applySink(true))
  })
  onMount(() => {
    const onChange = () => applySink(true)
    navigator.mediaDevices?.addEventListener?.('devicechange', onChange)
    return () => navigator.mediaDevices?.removeEventListener?.('devicechange', onChange)
  })

  // Browsers create an AudioContext in the "suspended" state until the page
  // receives a user gesture. Resume it on the very first interaction so audio
  // routed through the MediaElementSource graph is actually audible.
  onMount(() => {
    const resume = () => { if (audioCtx?.state === 'suspended') audioCtx.resume().catch(() => {}) }
    window.addEventListener('pointerdown', resume, true)
    window.addEventListener('keydown', resume, true)
    return () => {
      window.removeEventListener('pointerdown', resume, true)
      window.removeEventListener('keydown', resume, true)
    }
  })

  // Single source of truth for gain: smooth ramp when element is playing,
  // immediate set when paused (e.g. pre-loading during crossfade).
  $effect(() => {
    if (!gainA || !gainB || !audioCtx) return
    if (lufsA > -90) {
      const t = normFactor(lufsA)
      if (elA && !elA.paused && Math.abs(gainA.gain.value - t) > 0.05) {
        gainA.gain.cancelScheduledValues(audioCtx.currentTime)
        gainA.gain.setValueAtTime(gainA.gain.value, audioCtx.currentTime)
        gainA.gain.linearRampToValueAtTime(t, audioCtx.currentTime + 0.4)
      } else { gainA.gain.value = t }
    } else {
      // No LUFS data → reset to neutral so previous track's gain doesn't carry over
      gainA.gain.cancelScheduledValues(audioCtx.currentTime)
      gainA.gain.value = 1.0
    }
    if (lufsB > -90) {
      const t = normFactor(lufsB)
      if (elB && !elB.paused && Math.abs(gainB.gain.value - t) > 0.05) {
        gainB.gain.cancelScheduledValues(audioCtx.currentTime)
        gainB.gain.setValueAtTime(gainB.gain.value, audioCtx.currentTime)
        gainB.gain.linearRampToValueAtTime(t, audioCtx.currentTime + 0.4)
      } else { gainB.gain.value = t }
    } else {
      gainB.gain.cancelScheduledValues(audioCtx.currentTime)
      gainB.gain.value = 1.0
    }
  })

  $effect(() => {
    const np = $nowPlaying
    const lufs = lufsOf(np)
    if (!lufs || lufs <= -90) return
    // untrack: don't re-run when 'which' flips — during _finishCrossfade the deck
    // swaps before the backend sends now_playing, so cur() would point to the new
    // element while nowPlaying.lufs still holds the old track's LUFS → wrong gain spike.
    untrack(() => {
      const el = cur()
      if (el) setElLufs(el, lufs)
    })
  })

  // During crossfade: apply LUFS to alt() reactively when enrichment arrives.
  $effect(() => {
    const idx  = cfNextIdx
    if (idx < 0) return
    const lufs = lufsOf($queue[idx])
    if (!lufs || lufs <= -90) return
    const a = alt()
    if (a) setElLufs(a, lufs)
  })

  function _fade(t, vFrom, vTo, curve) {
    curve ??= get(appSettings).cfCurve ?? 'cosine'
    if (curve === 'linear') return [Math.max(0, vFrom * (1 - t)), Math.max(0, vTo * t)]
    if (curve === 'scurve') {
      const s = t * t * (3 - 2 * t)
      return [Math.max(0, vFrom * (1 - s)), Math.max(0, vTo * s)]
    }
    return [Math.max(0, vFrom * Math.cos(t * Math.PI / 2)), Math.max(0, vTo * Math.sin(t * Math.PI / 2))]
  }

  function fmt(ms) {
    if (!ms || ms < 0) return '0:00'
    const s = Math.floor(ms / 1000)
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2,'0')}`
  }

  // Lautheit des Hauptteils (Bibliothek) vor dem Wert ueber den ganzen Titel
  const mainByPath = $derived(new Map($library.filter(t => t.lufs_main != null).map(t => [t.path, t.lufs_main])))
  function lufsOf(t) {
    if (!t) return -99
    const m = untrack(() => mainByPath).get(t.path)
    return m ?? t.lufs ?? -99
  }

  // Pegel einer Stelle der Waveform (Energie-Mittel, dB) und des Hauptteils
  function _wfDb(wf, a, b) {
    const n = wf.length, i0 = Math.max(0, Math.floor(a * n)), i1 = Math.min(n, Math.ceil(b * n))
    if (i1 - i0 < 3) return null
    let e = 0
    for (let i = i0; i < i1; i++) e += wf[i] * wf[i]
    return 10 * Math.log10(e / (i1 - i0) + 1e-9)
  }
  function _wfMainDb(wf) {
    const v = [...wf].sort((x, y) => x - y).slice(Math.floor(wf.length / 2))
    let e = 0
    for (const x of v) e += x * x
    return 10 * Math.log10(e / Math.max(1, v.length) + 1e-9)
  }
  /** Wie viel dB der neue Titel beim Einstieg angehoben/abgesenkt wird (±4). */
  function _blendMatchDb(wfO, wfI, o0, oLen, i0, iLen) {
    if (!wfO?.length || !wfI?.length || !(oLen > 0) || !(iLen > 0)) return 0
    const lo = _wfDb(wfO, o0, o0 + oLen), li = _wfDb(wfI, i0, i0 + iLen)
    if (lo == null || li == null) return 0
    const d = (lo - _wfMainDb(wfO)) - (li - _wfMainDb(wfI))
    return Math.max(-4, Math.min(4, Math.round(d * 10) / 10))
  }

  function normFactor(lufs) {
    const target = $appSettings.targetLUFS ?? -14
    if (!$appSettings.normalizeVolume || !lufs || lufs <= -90) return 1.0
    const db = Math.max(-20, Math.min(12, target - lufs))
    return Math.pow(10, db / 20)
  }

  function getGainNode(el) { return el === elA ? gainA : gainB }

  function setElLufs(el, lufs) {
    if (el === elA) lufsA = lufs
    else lufsB = lufs
    // Gain is applied smoothly by the $effect above
  }

  // ── Smart Fade ─────────────────────────────────────────────────────────────
  // Strategy: detect the natural intro/outro boundary ONCE with a fixed,
  // track-relative threshold (a fraction of the track's own body loudness).
  // Then the sliders only scale HOW MUCH of that detected zone to use.
  // This way the slider always moves the grey zone predictably — no more
  // "only works at one extreme". Waveform data is normalized so max bar = 1.0.

  // INTRO_SKIP: multiplier on detected quiet intro (0=skip nothing, 0.95=just before drop)
  const INTRO_SKIP  = [0.0, 0.45, 0.68, 0.84, 0.95]
  // INTRO_FRACS: minimum skip when no quiet intro detected (Level 1=0:00, Level 5≈36s)
  const INTRO_FRACS = [0.0, 0.02, 0.06, 0.12, 0.20]
  // OUTRO_EARLY: crossfade-lengths to start blending BEFORE the detected silence
  const OUTRO_EARLY = [0.0, 0.5, 1.0, 1.5, 2.2]

  function _bodyAvg(data) {
    const n = data.length
    const a = Math.floor(n * 0.15), b = Math.floor(n * 0.70)
    let sum = 0
    for (let i = a; i < b; i++) sum += data[i]
    return sum / Math.max(1, b - a)
  }

  // Where the sustained main section begins (end of quiet intro). 0 = no intro.
  function _detectIntroLen(data) {
    if (!data || data.length < 50) return 0
    const n       = data.length
    const body    = _bodyAvg(data)
    if (body < 0.03) return 0
    const thresh  = body * 0.60        // sustained ≥ 60% of body = real music
    const sustain = Math.max(12, Math.floor(n * 0.05))
    const maxI    = Math.floor(n * 0.45)
    for (let i = 0; i < maxI; i++) {
      let avg = 0
      const e = Math.min(n, i + sustain)
      for (let j = i; j < e; j++) avg += data[j]
      avg /= (e - i)
      if (avg >= thresh) return i / n
    }
    return 0
  }

  // Where the last sustained-loud section ends (start of outro/silence). -1 = none.
  function _detectOutroStart(data) {
    if (!data || data.length < 100) return -1
    const n       = data.length
    const body    = _bodyAvg(data)
    if (body < 0.03) return -1
    const thresh  = body * 0.50
    const sustain = Math.max(12, Math.floor(n * 0.04))
    const minI    = Math.floor(n * 0.45)
    for (let i = n - sustain; i >= minI; i--) {
      let avg = 0
      for (let j = i; j < i + sustain; j++) avg += data[j]
      avg /= sustain
      if (avg >= thresh) return Math.min(0.99, (i + sustain) / n)
    }
    return -1
  }

  // Cache — detection recalculated only when waveform data changes
  let _sfWf = null, _sfSilence = -1
  let _sfNWf = null, _sfIntroLen = 0

  function _outroSilence() {
    const wf = get(waveform), cfg = get(appSettings)
    if (!cfg.smartFade || !wf?.length) return -1
    if (wf !== _sfWf) { _sfWf = wf; _sfSilence = _detectOutroStart(wf) }
    return _sfSilence
  }

  function _nextIntroLen() {
    const wf = get(waveformNext)
    if (wf !== _sfNWf) { _sfNWf = wf; _sfIntroLen = _detectIntroLen(wf) }
    return _sfIntroLen
  }

  // Fraction of the next track to skip on entry (manual override or scaled detection)
  // Returns where track 2 should START playing (fraction to seek to).
  // Bar START = this value. Bar END = this + cfEff/duration.
  function _getNextIntroStart() {
    const cfg = get(appSettings)
    const nt  = get(queue)[_nextIdx()]
    if (introOverride && nt?.path === introOverride.path) return introOverride.frac
    // Per-track one-shot skip (queue context menu "Intro überspringen") — always max
    if (nt?.path && get(introSkipPaths).has(nt.path)) {
      introSkipPaths.update(s => { const n = new Set(s); n.delete(nt.path); return n })
      const detected = _nextIntroLen() * INTRO_SKIP[4]
      return Math.min(0.45, Math.max(detected, INTRO_FRACS[4]))
    }
    if ((cfg.introSkipSec ?? 0) > 0) {
      const dur = nt?.duration_sec ?? 0
      if (dur > 0) return Math.min(cfg.introSkipSec / dur, 0.45)
    }
    if (!cfg.smartFade) return 0
    const a = Math.max(0, Math.min(4, (cfg.introAggressiveness ?? cfg.fadeAggressiveness ?? 3) - 1))
    const detected = _nextIntroLen() * INTRO_SKIP[a]
    return Math.min(0.45, Math.max(detected, INTRO_FRACS[a]))
  }

  // Crossfade trigger as a fraction of the current track — single source of truth
  function _outroTrigger() {
    if (durMs <= 0 || cfEff <= 0) return -1
    const cfg    = get(appSettings)
    const cfFrac = Math.min(0.9, (cfEff * 1000) / durMs)
    const playingPath = cur()?.dataset.path || get(nowPlaying)?.path
    if (outroOverride && outroOverride.path === playingPath)
      return Math.max(0.02, Math.min(outroOverride.frac, 1 - cfFrac))
    const sil    = _outroSilence()
    let trig
    if (sil >= 0) {
      const a = Math.max(0, Math.min(4, (cfg.outroAggressiveness ?? cfg.fadeAggressiveness ?? 3) - 1))
      trig = sil - OUTRO_EARLY[a] * cfFrac
    } else {
      trig = 1 - cfFrac
    }
    // MIX zone must always be at least one crossfade wide, even for hard-ending tracks
    trig = Math.min(trig, 1 - cfFrac)
    return Math.max(0.2, trig)
  }

  // Mix-Punkt einrasten: auf einen Phrasenanfang (16/8 Takte, hoechstens
  // 8 Takte verschoben), bei selbst gezogener Zone nur auf die naechste Eins,
  // ohne Takt-Information wie bisher auf den naechsten Schlag.
  function _snapTriggerMs(triggerMs) {
    const playingPath = cur()?.dataset.path || get(nowPlaying)?.path
    const dragged = !!(outroOverride && outroOverride.path === playingPath)
    return _snapTrigger(triggerMs, durMs, cfEff, playingPath, dragged, get(nowPlaying)?.bpm)
  }
  function _snapTrigger(triggerMs, dMs, cf, path, dragged, bpm) {
    const cfg = get(appSettings)
    if (!cfg.beatAlignCf || dMs <= 0) return triggerMs
    const latest = dMs - cf * 1000 - 200
    const gC = gridOf(path)
    if (gC && gC.conf >= 0.4 && gC.phrase != null) {
      const units = dragged || cfg.phraseAlign === false ? [1] : [16, 8, 1]
      return snapToPhrase(triggerMs / 1000, gC, 0, latest / 1000, dragged ? 1 : 8, units) * 1000
    }
    if (gC && gC.conf >= 0.3) {
      const beatMs = 60000 / gC.bpm, offMs = gC.off * 1000
      const snapped = offMs + Math.round((triggerMs - offMs) / beatMs) * beatMs
      if (Math.abs(snapped - triggerMs) <= beatMs) return Math.min(snapped, latest)
    } else if (bpm > 30 && bpm < 300) {
      const beatMs  = 60000 / bpm
      const snapped = Math.round(triggerMs / beatMs) * beatMs
      // Only apply if snap is within ±1 beat from base trigger
      if (Math.abs(snapped - triggerMs) <= beatMs) return Math.min(snapped, latest)
    }
    return triggerMs
  }
  // Einstieg im naechsten Titel: auf dessen Phrasenanfang (nicht bei selbst gezogener Zone)
  function _snapIntroSec(sec, g, dur, dragged) {
    const cfg = get(appSettings)
    if (!cfg.beatAlignCf || cfg.phraseAlign === false || dragged || !g || g.conf < 0.4 || !(dur > 0)) return sec
    return snapToPhrase(sec, g, 0, dur * 0.6)
  }

  // Farbband ueber den Waveforms: Intro bis zum Einsatz, Outro ab dem Ende
  // des lauten Teils (dieselbe Erkennung wie der Intelligente Fade)
  const curIntroMark  = $derived(_detectIntroLen($waveform))
  const curOutroMark  = $derived(_detectOutroStart($waveform))
  const nextIntroMark = $derived(_detectIntroLen($waveformNext))
  const nextOutroMark = $derived(_detectOutroStart($waveformNext))
  const WF_BAND = 12

  $effect(() => { void $waveform;     _sfWf  = null })
  $effect(() => { void $waveformNext; _sfNWf = null })

  // ── Derived grey-bar zones for the waveform ────────────────────────────────
  const _cfFrac = $derived(durMs > 0 && cfEff > 0 ? Math.min(0.9, (cfEff * 1000) / durMs) : 0)

  // Outro grey "mix" bar on the current track: [trigger, trigger + crossfade]
  // Die Zone steht immer da (auch ohne Smart Fade), damit man sie ziehen kann
  const _baseOutroStart = $derived.by(() => {
    if (durMs <= 0 || cfEff <= 0) return -1
    void $waveform; void $appSettings.outroAggressiveness; void $appSettings.smartFade
    void outroOverride; void $nowPlaying?.path; void $beatGrids
    void $appSettings.phraseAlign; void $appSettings.beatAlignCf
    const trig = _outroTrigger()
    return trig < 0 ? trig : Math.max(0, _snapTriggerMs(trig * durMs) / durMs)
  })
  // Mit DJ-Modus zeigt die Zone, wo der kreative Uebergang wirklich liegt
  const outroBarStart = $derived(djPlan?.zoneOut ? djPlan.zoneOut[0] : _baseOutroStart)
  const outroBarEnd = $derived(djPlan?.zoneOut ? djPlan.zoneOut[1]
    : _baseOutroStart >= 0 ? Math.min(1, _baseOutroStart + _cfFrac) : -1)

  // ── Welcher Titel kommt als naechstes ─────────────────────────────────────
  // Eine Stelle fuer Anzeige, Vorbereitung und tatsaechliches Abspielen.
  // Vorher zeigte "NAECHSTER" bei Zufallswiedergabe ci+1, gespielt wurde aber
  // ein anderer, bei jedem Aufruf neu ausgewuerfelter Titel.
  let badPaths   = new Set()          // Dateien, die sich nicht abspielen liessen
  let badVersion = $state(0)          // macht Aenderungen an badPaths sichtbar
  let shufflePickPath = $state('')
  let _shuffleFor = null

  function _pickShuffle(q, ci) {
    const cand  = [...q.keys()].filter(i => i !== ci && !badPaths.has(q[i]?.path))
    const fresh = cand.filter(i => !q[i].played)
    const pool  = fresh.length ? fresh : cand
    return pool.length ? q[pool[Math.floor(Math.random() * pool.length)]].path : ''
  }

  $effect(() => {
    const q = $queue, ci = $playerState.current_idx, on = $playMode.shuffle
    void badVersion
    untrack(() => {
      if (!on) { shufflePickPath = ''; _shuffleFor = null; return }
      const curPath = q[ci]?.path ?? ''
      const pickOk = shufflePickPath && !badPaths.has(shufflePickPath) &&
                     q.some((t, i) => i !== ci && t.path === shufflePickPath)
      if (curPath !== _shuffleFor || !pickOk) {
        _shuffleFor = curPath
        shufflePickPath = _pickShuffle(q, ci)
      }
    })
  })

  function _computeNext(q, ci, pm, pick) {
    if (!q.length) return -1
    if (pm.repeat === 1 && ci >= 0 && ci < q.length) return ci
    if (pm.shuffle) return pick ? q.findIndex((t, i) => i !== ci && t.path === pick) : -1
    let nxt = ci + 1
    while (nxt < q.length && badPaths.has(q[nxt]?.path)) nxt++
    if (nxt < q.length) return nxt
    if (pm.repeat === 2) {
      const first = q.findIndex(t => !badPaths.has(t.path))
      return first
    }
    return -1
  }

  const nextTrackIdx = $derived.by(() => {
    void badVersion
    return _computeNext($queue, $playerState.current_idx, $playMode, shufflePickPath)
  })
  // Tonart aus der Bibliothek, nicht aus dem Queue-Eintrag (der traegt sie nicht)
  const keyByPath = $derived(new Map($library.filter(t => t.key).map(t => [t.path, t])))
  const curKey    = $derived(keyByPath.get($nowPlaying?.path) ?? null)
  const nextKey   = $derived(keyByPath.get(nextTrack?.path) ?? null)
  const uebergang = $derived(keyCompat(curKey?.key, nextKey?.key))
  // Tempo des naechsten Titels, und ob es sich angleichen laesst (auch halb/doppelt)
  const bpmByPath = $derived(new Map($library.filter(t => t.bpm).map(t => [t.path, t.bpm])))
  const nextBpm   = $derived(nextTrack ? (bpmByPath.get(nextTrack.path) || nextTrack.bpm || 0) : 0)
  const nextTempo = $derived.by(() => {
    const cb = $nowPlaying?.bpm || bpmByPath.get($nowPlaying?.path) || 0
    if (!(nextBpm > 30) || !(cb > 30)) return null
    const lim = $appSettings.maxTempoDiff ?? 8
    const m = tempoMatch(cb, nextBpm, lim / 100)
    return m ? { ok: true, pct: Math.round(Math.abs(m.rate - 1) * 1000) / 10 } : { ok: false, lim }
  })

  const nextTrack    = $derived(
    nextTrackIdx >= 0 && nextTrackIdx < $queue.length ? $queue[nextTrackIdx] : null
  )
  // Der Titel danach gleitet beim Uebergang in die Zeile "Naechster" nach
  // (bei Zufallswiedergabe steht er noch nicht fest)
  const thirdTrack = $derived.by(() => {
    if ($playMode.shuffle || nextTrackIdx < 0) return null
    const i = _computeNext($queue, nextTrackIdx, $playMode, '')
    return i >= 0 && i !== nextTrackIdx && i !== $playerState.current_idx ? $queue[i] : null
  })
  // Deck 2 grey bar:
  //   START = where track 2 begins playing (seeks to this position)
  //   END   = start + cfEff (bar is always exactly one crossfade wide)
  //   Level 1 (soft) → bar at 0:00; Level 5 (aggressive) → bar before the drop
  const _baseIntroStart = $derived.by(() => {
    const sf = $appSettings.smartFade
    const ia = $appSettings.introAggressiveness ?? $appSettings.fadeAggressiveness ?? 3
    const wf = $waveformNext
    const nt = nextTrack
    if (nt && introOverride && introOverride.path === nt.path) return introOverride.frac
    if (!nt || cfEff <= 0) return 0
    void sf; void ia; void $beatGrids; void $appSettings.phraseAlign; void $appSettings.beatAlignCf
    return _introFracFor(nt, wf)
  })
  // Wo ein Titel einsteigt, wenn in ihn gemischt wird (ohne gezogene Zone)
  function _introFracFor(t, wf) {
    const cfg = get(appSettings)
    const dur = t.duration_sec || 0
    let frac = 0
    if (cfg.smartFade) {
      const a   = Math.max(0, Math.min(4, (cfg.introAggressiveness ?? cfg.fadeAggressiveness ?? 3) - 1))
      const det = wf?.length > 0 ? _detectIntroLen(wf) * INTRO_SKIP[a] : 0
      frac = Math.min(0.45, Math.max(det, INTRO_FRACS[a]))
    }
    return dur > 0 ? _snapIntroSec(frac * dur, gridOf(t.path), dur, false) / dur : frac
  }
  // Wo ein Titel ausgemischt wird, wenn er einmal laeuft (wie outroBarStart,
  // ohne gezogene Zone) — fuer den naechsten und den Titel danach
  function _outroZoneFor(t, wf, ovFrac = null) {
    const dur = t?.duration_sec || 0
    const bpm = bpmByPath.get(t?.path) || t?.bpm
    const cf  = _cfSecFor(t?.path, bpm)
    if (!(dur > 0) || cf <= 0) return null
    const cfg    = get(appSettings)
    const cfFrac = Math.min(0.9, cf / dur)
    const sil    = cfg.smartFade && wf?.length ? _detectOutroStart(wf) : -1
    const a      = Math.max(0, Math.min(4, (cfg.outroAggressiveness ?? cfg.fadeAggressiveness ?? 3) - 1))
    let trig = sil >= 0 ? sil - OUTRO_EARLY[a] * cfFrac : 1 - cfFrac
    trig = Math.max(0.2, Math.min(trig, 1 - cfFrac))
    if (ovFrac != null) trig = Math.max(0.02, Math.min(ovFrac, 1 - cfFrac))   // selbst gezogen
    const st = Math.max(0, _snapTrigger(trig * dur * 1000, dur * 1000, cf, t.path, ovFrac != null, bpm) / (dur * 1000))
    return [st, Math.min(1, st + cfFrac)]
  }
  const nextIntroStart = $derived(djPlan?.zoneIn ? djPlan.zoneIn[0] : _baseIntroStart)
  const nextIntroEnd = $derived.by(() => {
    if (djPlan?.zoneIn) return djPlan.zoneIn[1]
    const nt = nextTrack
    if (!nt || cfEff <= 0) return -1
    const dur = nt.duration_sec || 180
    return Math.min(0.95, _baseIntroStart + cfEff / dur)
  })

  // ── DJ-Modus: Plan fuer den Uebergang zum naechsten Titel ─────────────────
  const DJ_LEAD = 2                                  // Takte Vorlauf bei Echo/Roll
  const DJ_TAIL = { echo: 2, roll: 0.5 }             // Takte nach der Grenze
  let djOverride = $state({})                        // "lauf|naechst" -> Art (von Hand)
  let _djLastType = $state('blend')
  let djMenu = $state(null)                          // {x, y} des offenen Menues
  function toggleDjMenu(e) {
    e.stopPropagation()
    if (djMenu) { djMenu = null; return }
    const r = e.currentTarget.getBoundingClientRect()
    djMenu = { x: Math.max(8, r.right - 170), y: r.bottom + 4 }
  }
  const libByPath = $derived(new Map($library.map(t => [t.path, t])))
  const curDrops = $derived.by(() => {
    void $beatGrids
    const p = $nowPlaying?.path, g = gridOf(p)
    return p && g && durMs > 0 ? detectDrops($waveform, g, durMs / 1000, libByPath.get(p)?.mik_cues) : []
  })
  const nextDrops = $derived.by(() => {
    void $beatGrids
    const t = nextTrack, g = gridOf(t?.path)
    return t && g && t.duration_sec > 0 ? detectDrops($waveformNext, g, t.duration_sec, libByPath.get(t.path)?.mik_cues) : []
  })
  const djPlan = $derived.by(() => {
    const cfg = $appSettings
    const np = $nowPlaying, nt = nextTrack
    if (!np?.path || !nt?.path || durMs <= 0 || cfEff <= 0) return null
    void $beatGrids
    const gC = gridOf(np.path), gN = gridOf(nt.path)
    const key = np.path + '|' + nt.path
    const maxD = (cfg.maxTempoDiff ?? 8) / 100
    const eligible = !!(cfg.beatAlignCf && gC && gN && gC.conf >= 0.4 && gN.conf >= 0.4 && tempoMatch(gC.bpm, gN.bpm, maxD))
    const bucket = Math.floor(posMs / 4000)          // nicht bei jedem Bild neu rechnen
    const nDur = nt.duration_sec || 0
    const dd = eligible && nDur > 0
      ? doubleDropPlan({ drops: curDrops, g: gC, dur: durMs / 1000 }, { drops: nextDrops, g: gN, dur: nDur }, maxD, bucket * 4 - 2)      // Start darf nur schon vorbei sein, nicht kurz bevorstehen
      : null
    const dragged = outroOverride?.path === np.path || introOverride?.path === nt.path
    const ddOk = !!dd && !dragged
    const ov = djOverride[key]
    let type
    if (ov) type = ov
    else if (!cfg.djMode) type = 'blend'
    else type = chooseTransition({ amount: cfg.djAmount ?? 0.4, types: cfg.djTypes ?? {} },
      { seed: key, eligible, keyClash: uebergang.level === 'clash', ddPossible: ddOk, lastType: untrack(() => _djLastType) })
    if (type !== 'blend' && !eligible) type = 'blend'
    if (type === 'doubledrop' && !ddOk) type = 'blend'
    if (type === 'roll' && !rollerOk) type = 'echo'
    const plan = { type, auto: !ov, eligible, ddOk, dd, key }
    const barC = gC ? barLen(gC) : 0, barN = gN ? barLen(gN) : 0
    const dC = durMs / 1000
    if (type === 'doubledrop') {
      plan.zoneOut = [dd.trig / dC, Math.min(1, (dd.trig + dd.len * barC) / dC)]
      plan.zoneIn = [dd.intro / nDur, Math.min(0.99, (dd.intro + dd.len * barN) / nDur)]
    } else if ((type === 'echo' || type === 'roll') && _baseOutroStart >= 0 && nDur > 0) {
      const B = _baseOutroStart * dC, tail = DJ_TAIL[type]
      const I = Math.max(_baseIntroStart * nDur, DJ_LEAD * barN)
      plan.B = B; plan.I = I
      plan.zoneOut = [Math.max(0, (B - DJ_LEAD * barC) / dC), Math.min(1, (B + tail * barC) / dC)]
      plan.zoneIn = [(I - DJ_LEAD * barN) / nDur, Math.min(0.99, (I + tail * barN) / nDur)]
    }
    return plan
  })
  function pickDj(type) {
    const key = djPlan?.key
    if (!key) return
    const o = { ...djOverride }
    if (type === 'auto') delete o[key]; else o[key] = type
    djOverride = o
    djMenu = null
  }

  // Mix-Zonen im Farbband: der naechste Titel zeigt auch schon, wo er
  // ausgemischt wird, der Titel danach (rueckt beim Uebergang nach) beides —
  // so sieht jede Zeile nach dem Hochgleiten gleich aus wie davor
  const nextOutroZone = $derived.by(() => {
    void $appSettings; void $beatGrids; void cfS; void bpmByPath
    if (!nextTrack) return null
    const ov = nextOutroOverride?.path === nextTrack.path ? nextOutroOverride.frac : null
    return _outroZoneFor(nextTrack, $waveformNext, ov)
  })
  const thirdWf = $derived($waveformThird.path && $waveformThird.path === thirdTrack?.path ? $waveformThird.data : [])
  const thirdIntroMark = $derived(_detectIntroLen(thirdWf))
  const thirdOutroMark = $derived(_detectOutroStart(thirdWf))
  const thirdIntroStart = $derived.by(() => {
    void $appSettings; void $beatGrids
    return thirdTrack ? _introFracFor(thirdTrack, thirdWf) : 0
  })
  const thirdIntroEnd = $derived.by(() => {
    if (!thirdTrack || !nextTrack) return -1
    void $appSettings; void $beatGrids; void cfS
    const cf = _cfSecFor(nextTrack.path, nextBpm)     // dann laeuft der naechste Titel
    return cf > 0 ? Math.min(0.95, thirdIntroStart + cf / (thirdTrack.duration_sec || 180)) : -1
  })
  const thirdOutroZone = $derived.by(() => {
    void $appSettings; void $beatGrids; void cfS; void bpmByPath
    return thirdTrack ? _outroZoneFor(thirdTrack, thirdWf) : null
  })
  // Tonart und Tempo des Titels danach (im Verhaeltnis zum naechsten)
  const thirdKey   = $derived(keyByPath.get(thirdTrack?.path) ?? null)
  const thirdCompat = $derived(keyCompat(nextKey?.key, thirdKey?.key))
  const thirdBpm   = $derived(thirdTrack ? (bpmByPath.get(thirdTrack.path) || thirdTrack.bpm || 0) : 0)
  const thirdTempo = $derived.by(() => {
    if (!(thirdBpm > 30) || !(nextBpm > 30)) return null
    const lim = $appSettings.maxTempoDiff ?? 8
    const m = tempoMatch(nextBpm, thirdBpm, lim / 100)
    return m ? { ok: true, pct: Math.round(Math.abs(m.rate - 1) * 1000) / 10 } : { ok: false, lim }
  })
  // Waveform des Titels danach schon vorher holen, damit er beim Nachruecken fertig ist
  $effect(() => {
    const p = thirdTrack?.path
    if (p && untrack(() => get(waveformThird).path) !== p) requestWaveform('waveform_third', p)
  })

  // Taktraster fuer laufenden und naechsten Titel holen (einmal je Pfad)
  const _gridAsked = new Set()
  $effect(() => {
    if (!$appSettings.beatAlignCf && $appSettings.tempoMatch === false) return
    for (const p of [$nowPlaying?.path, nextTrack?.path, thirdTrack?.path]) {
      if (p && !_gridAsked.has(p)) { _gridAsked.add(p); send({ type: 'get_beatgrid', path: p }) }
    }
  })

  // Verschobene Zonen gelten nur fuer den einen Uebergang
  let _ovFor = ''
  $effect(() => {
    const p = $nowPlaying?.path ?? ''
    if (p === _ovFor) return
    _ovFor = p
    untrack(() => {
      // Beim naechsten Titel gezogenes Ausmischen gilt jetzt fuer ihn als laufenden
      if (nextOutroOverride?.path === p) outroOverride = nextOutroOverride
      else if (outroOverride?.path !== p) outroOverride = null
      nextOutroOverride = null
      if (introOverride?.path === p) introOverride = null
    })
  })
  // "Jetzt mischen" (Fernbedienung): Mix-Zone an die aktuelle Stelle legen —
  // der Uebergang startet dann auf der naechsten Eins, im Takt und mit
  // Bass-Tausch, wie ein automatischer. Eine halbe Takt-Laenge Vorlauf, damit
  // die Eins davor (schon vorbei) nicht gewaehlt wird.
  let _mixReqSeen = 0
  $effect(() => {
    const at = $mixNowRequest
    if (!at || at === _mixReqSeen) return
    _mixReqSeen = at
    untrack(() => {
      const el = cur(), np = el?.dataset.path || get(nowPlaying)?.path
      if (!el || !np || cfActive || cfRafActive || durMs <= 0 || !get(playerState).playing) return
      const g = gridOf(np)
      const lead = g ? barLen(g) * 500 : 250
      _cfDoneAt = 0
      outroOverride = { path: np, frac: Math.min(0.999, (el.currentTime * 1000 + lead + 150) / durMs) }
      _checkCrossfade(el.currentTime * 1000)
    })
  })

  function dragOutro(frac, done) {
    const np = cur()?.dataset.path || get(nowPlaying)?.path
    zoneDragging = !done
    if (!np || cfActive || cfRafActive || durMs <= 0) return
    const cfFrac = Math.min(0.9, (cfEff * 1000) / durMs)
    outroOverride = { path: np, frac: Math.max(0.02, Math.min(frac, 1 - cfFrac)) }
  }
  function dragIntro(frac, done) {
    const nt = nextTrack
    zoneDragging = !done
    if (!nt?.path || cfActive || cfRafActive) return
    const dur = nt.duration_sec || 180
    introOverride = { path: nt.path, frac: Math.max(0, Math.min(frac, 0.95 - cfEff / dur)) }
  }

  function dragNextOutro(frac, done) {
    const nt = nextTrack
    zoneDragging = !done
    if (!nt?.path || !(nt.duration_sec > 0) || cfActive || cfRafActive) return
    const cf = _cfSecFor(nt.path, nextBpm)
    const cfFrac = Math.min(0.9, cf / nt.duration_sec)
    nextOutroOverride = { path: nt.path, frac: Math.max(0.02, Math.min(frac, 1 - cfFrac)) }
  }

  let _lastNextPath = ''
  $effect(() => {
    const nt = nextTrack
    if (nt?.path && nt.path !== _lastNextPath) {
      _lastNextPath = nt.path
      requestWaveform('waveform_next', nt.path)
      // Pre-enrich next track so its LUFS is ready before crossfade starts
      if (!nt.lufs || nt.lufs <= -90)
        send({ type: 'enrich_track', path: nt.path })
    } else if (!nt) { _lastNextPath = '' }
  })

  // ── Load new track ─────────────────────────────────────────────────────────
  $effect(() => {
    const track = $nowPlaying
    if (!track?.path) return

    const url = 'file:///' + track.path.replace(/\\/g, '/')
    if (url === loadedUrl) return

    if (cfTimer) { clearInterval(cfTimer); cfTimer = null }
    if (cfRaf)   { clearTimeout(cfRaf); cfRaf = null }
    cfCancelled = true
    cfActive = false; cfNextIdx = -1
    untrack(cancelSync); _stopGlide(); blendP = 0
    untrack(resetAllBass)

    const c  = untrack(cur)
    const a  = untrack(alt)
    const v  = untrack(() => volume) / 100
    const cf = untrack(() => cfEff)
    const wasPlaying = c && c.src && c.readyState >= 2 && !c.paused

    // Consume the one-shot flag: user-initiated plays skip the crossfade so
    // the old track stops immediately instead of fading out over cfEff seconds.
    const forceImmediate = get(skipNextCrossfade)
    if (forceImmediate) skipNextCrossfade.set(false)

    loadedUrl = url
    waitForNext = false

    if (wasPlaying && cf > 0 && !forceImmediate) {
      cfCancelled = false
      cfRafActive = true
      a.playbackRate = 1
      a.src = url; a.dataset.path = track.path; a.volume = 0; a.load(); a.play().catch(() => {})
      setElLufs(a, lufsOf(track))
      which = untrack(() => which) === 'A' ? 'B' : 'A'

      // LUFS-Angleichung für manuellen Crossfade (Mix Now / load-effect-Weg):
      // setTimeout(0) läuft nach dem Svelte-Microtask für setElLufs,
      // sodass unser Gain-Override den $effect überschreibt.
      const _mixNextLufs = lufsOf(track)
      const _mixCurLufs  = c === elA ? lufsA : lufsB
      const _mixSettings = get(appSettings)
      if (_mixNextLufs > -90 && _mixCurLufs > -90 && !_mixSettings.normalizeVolume) {
        const db = Math.max(-20, Math.min(12, _mixCurLufs - _mixNextLufs))
        const matchG = Math.pow(10, db / 20)
        const gnAlt  = getGainNode(a)
        setTimeout(() => {
          if (gnAlt && audioCtx && cfRaf !== null) {
            gnAlt.gain.cancelScheduledValues(audioCtx.currentTime)
            gnAlt.gain.setValueAtTime(matchG, audioCtx.currentTime)
          }
        }, 0)
      }

      stopRamp(c); stopRamp(a)      // der Uebergang steuert jetzt die Lautstaerke
      const fadeMs = cf * 1000
      const t0     = performance.now()
      const vOld   = c.volume

      let syncTried = false
      const oldPath = c.dataset.path
      untrack(() => planBassSwap(c, a, gridOf(oldPath), gridOf(track.path), fadeMs / 1000))
      function rafTick() {
        const t = Math.min(1, (performance.now() - t0) / fadeMs)
        const [fv, tv] = _fade(t, vOld, v)
        c.volume = fv; a.volume = tv
        // Beat-Sync, sobald der neue Titel wirklich laeuft
        if (!syncTried && !a.paused && a.currentTime > 0.05) {
          syncTried = true
          cfSync = startSync(c, a, oldPath, track.path)
        }
        cfSync?.tick(t < 0.3, t)
        // setTimeout statt requestAnimationFrame: rAF steht still, wenn das
        // Fenster minimiert oder verdeckt ist — der Uebergang blieb dann haengen
        // und beide Titel spielten weiter
        if (t < 1) cfRaf = setTimeout(rafTick, 16)
        else { cfRaf = null; cfRafActive = false; endSync(a); resetBass(a, 0.08); bassSwapOn = false; _silenceAndStop(c); _cfDoneAt = performance.now() }
      }
      cfRaf = setTimeout(rafTick, 16)
    } else {
      // Silence the alt deck immediately in case a crossfade was mid-flight
      const oldAlt = untrack(alt)
      if (oldAlt) _silenceAndStop(oldAlt)
      const el = untrack(cur)
      if (el) {
        el.playbackRate = 1
        el.src = url; el.dataset.path = track.path; el.volume = v; el.load(); setElLufs(el, lufsOf(track))
        if (untrack(() => $playerState.playing)) el.play().catch(() => {})
      }
    }

    posMs = 0; durMs = 0
  })

  // ── Deck-1 waveform: always request when the now-playing track changes ──────
  // Independent of loadedUrl so it self-heals after a crossfade (where the load
  // effect early-returns because loadedUrl already matches). _wfPath is primed
  // by _finishCrossfade to reuse the pre-fetched next-waveform without a flicker.
  let _wfPath = ''
  $effect(() => {
    const p = $nowPlaying?.path
    if (p && p !== _wfPath) {
      _wfPath = p
      waveform.set([])
      requestWaveform('waveform', p)
    }
  })

  // ── Play / pause sync ──────────────────────────────────────────────────────
  $effect(() => {
    const playing = $playerState.playing
    const el = cur()
    if (!el) return
    if (playing) {
      const fadeMs = untrack(() => $appSettings.pauseFadeMs ?? 500)
      const target = untrack(() => volume) / 100
      if (_ramps.has(el)) {
        // Wieder an, bevor das Ausblenden fertig war: zurueck auf volle Lautstaerke
        rampVolume(el, target, fadeMs)
      } else if (el.paused) {
        if (fadeMs > 0 && el.src && el.currentTime > 0.2) { el.volume = 0; el.play().catch(() => {}); rampVolume(el, target, fadeMs) }
        else { el.volume = target; el.play().catch(() => {}) }
      }
      const iv = setInterval(() => {
        const e = untrack(cur); if (!e) return
        posMs = (e.currentTime ?? 0) * 1000
        send({ type: 'position_update',
               position_ms: Math.floor(posMs),
               duration_ms: Math.floor(durMs) })
      }, 500)
      return () => clearInterval(iv)
    } else {
      // Mark all pending canplay listeners as cancelled
      cfCancelled = true
      // Laufende Uebergaenge abbrechen und das zweite Deck sofort stoppen —
      // egal ob es gerade ein- oder ausblendet. Frueher lief nach einem Stopp
      // mitten im Mix gelegentlich der andere Titel weiter.
      if (cfTimer) { clearInterval(cfTimer); cfTimer = null }
      if (cfRaf)   { clearTimeout(cfRaf); cfRaf = null }
      cfRafActive = false
      if (cfActive) { cfActive = false; cfNextIdx = -1 }
      cancelSync(); blendP = 0; blendNextPos = 0
      untrack(resetAllBass)
      const a = alt()
      if (a) {
        stopRamp(a)
        try { a.pause() } catch {}
        a.removeAttribute('src')
        try { a.load() } catch {}  // flush queued canplay/loadeddata events
      }
      // Den laufenden Titel kurz ausblenden statt hart abzuschneiden
      const fadeMs = untrack(() => $appSettings.pauseFadeMs ?? 500)
      if (!el.paused && fadeMs > 0) rampVolume(el, 0, fadeMs, () => { try { el.pause() } catch {} })
      else { stopRamp(el); el.pause() }
    }
  })

  // ── Seek command from backend ──────────────────────────────────────────────
  let _knownPos = 0
  $effect(() => {
    const p = $playerState.position_ms
    if (p !== _knownPos) {
      _knownPos = p
      const el = untrack(cur)
      if (!el) return
      // queue_remove causes backend to echo our own position back via push_player().
      // Any seek (even accurate) causes a brief audio glitch — skip if within 1.5s.
      if (Math.abs(p - posMs) < 1500) return
      el.currentTime = p / 1000; posMs = p
    }
  })

  function _nextIdx() {
    return _computeNext(get(queue), get(playerState).current_idx, get(playMode), shufflePickPath)
  }

  // ── Auto-crossfade ─────────────────────────────────────────────────────────
  function _checkCrossfade(pos) {
    if (cfActive || cfEff <= 0 || durMs <= 0) return
    // Nicht waehrend der Pause-Blende: der alte Titel laeuft dort noch ein
    // paar hundert Millisekunden — frueher startete genau dann ein neuer
    // Uebergang, und der naechste Titel spielte nach dem Pausieren weiter.
    if (!get(playerState).playing || zoneDragging) return
    // Nach einem Uebergang den neuen Titel erst ein paar Sekunden laufen
    // lassen — nie zwei Uebergaenge direkt hintereinander (Ueberspringen)
    if (performance.now() - _cfDoneAt < 8000) return

    const trigFrac = _outroTrigger()
    // Einrasten auf Phrase / Eins / Schlag (siehe _snapTriggerMs)
    let triggerMs = _snapTriggerMs(trigFrac >= 0 ? trigFrac * durMs : durMs - cfEff * 1000)

    // DJ-Modus: Double Drop beginnt 16 Takte vor dem Drop, Echo/Roll 2 Takte
    // vor der Grenze. Liegt der Start schon hinter uns: normaler Uebergang.
    const q0 = get(queue), n0 = _nextIdx()
    const plan = djPlan
    let dj = plan && plan.key === (cur()?.dataset.path || get(nowPlaying)?.path) + '|' + q0[n0]?.path ? plan.type : 'blend'
    const gCur = gridOf(cur()?.dataset.path || get(nowPlaying)?.path)
    const barC = gCur ? barLen(gCur) : 0
    const djB = triggerMs / 1000                      // Grenze fuer Echo/Roll
    if (dj === 'doubledrop' && plan.dd) {
      if (pos > plan.dd.trig * 1000 + 1500) dj = 'blend'
      else triggerMs = plan.dd.trig * 1000
    } else if ((dj === 'echo' || dj === 'roll') && barC > 0) {
      const t0 = (djB - DJ_LEAD * barC) * 1000
      if (t0 < pos - 1500) dj = 'blend'
      else triggerMs = t0
    } else if (dj !== 'filter') dj = 'blend'

    if (pos < triggerMs - 100 || pos >= durMs - 100) return

    const q       = get(queue)
    const nextIdx = _nextIdx()
    if (nextIdx < 0 || nextIdx >= q.length) return
    const nextTrk = q[nextIdx]
    if (!nextTrk?.path) return
    _djLastType = dj

    cfActive     = true
    cfNextIdx    = nextIdx
    cfCancelled  = false
    const nextUrl  = 'file:///' + nextTrk.path.replace(/\\/g, '/')
    const inactive = alt()
    if (!inactive) { cfActive = false; return }

    setElLufs(inactive, lufsOf(nextTrk))

    // LUFS-Angleichung: eingehendes Deck auf denselben Pegel wie aktives Deck bringen.
    // Wenn normalizeVolume aktiv ist, erledigt das bereits der Gain-$effect.
    // Ohne Normalisierung gleichen wir manuell an – Gain wird im canplay-Handler gesetzt,
    // da der $effect (ausgelöst durch setElLufs oben) zuvor als Microtask abläuft.
    const _nextLufs = lufsOf(nextTrk)
    const _curLufs  = which === 'A' ? lufsA : lufsB
    const _cfSettings = get(appSettings)
    let _cfMatchGain = null
    if (_nextLufs > -90 && _curLufs > -90 && !_cfSettings.normalizeVolume) {
      const db = Math.max(-20, Math.min(12, _curLufs - _nextLufs))
      _cfMatchGain = Math.pow(10, db / 20)
    }

    const _nd = nextTrk.duration_sec || 0
    const introFrac = dj === 'doubledrop' && _nd > 0 ? plan.dd.intro / _nd
      : (dj === 'echo' || dj === 'roll') && plan.zoneIn ? plan.zoneIn[0]
      : _getNextIntroStart()
    // DJ-Einstiege liegen schon auf dem Takt des Drops/der Phrase: nicht verschieben
    const introDragged = !!(introOverride && introOverride.path === nextTrk.path) || (dj !== 'blend' && dj !== 'filter')

    inactive.playbackRate = 1
    inactive.src = nextUrl
    inactive.dataset.path = nextTrk.path
    inactive.load()
    const curPath = cur()?.dataset.path
    const beginSync = () => { if (!cfCancelled && cfActive) cfSync = startSync(cur(), inactive, curPath, nextTrk.path) }

    inactive.addEventListener('canplay', function onCanPlay() {
      if (cfCancelled) return
      // Gain jetzt setzen: $effect aus setElLufs ist zu diesem Zeitpunkt bereits abgelaufen
      if (_cfMatchGain !== null) {
        const gn = getGainNode(inactive)
        if (gn && audioCtx) {
          gn.gain.cancelScheduledValues(audioCtx.currentTime)
          gn.gain.setValueAtTime(_cfMatchGain, audioCtx.currentTime)
        }
      }
      inactive.volume = 0
      // Use inactive.duration (reliable at canplay) instead of metadata duration_sec
      // which is often 0 for newly added tracks.
      const elDur = inactive.duration
      let skipSec = (introFrac > 0.01 && elDur > 0 && isFinite(elDur))
        ? introFrac * elDur : 0
      // Auf einem Phrasenanfang des naechsten Titels einsteigen, und zwar an
      // derselben Stelle im Takt, an der der laufende gerade steht (Eins auf Eins)
      if (get(appSettings).beatAlignCf) {
        skipSec = _snapIntroSec(skipSec, gridOf(nextTrk.path), elDur, introDragged)
        skipSec = barAlignedStart(cur(), skipSec, gridOf(curPath), gridOf(nextTrk.path), 0.12, maxTempoDiff())
      }

      if (skipSec > 0.05) {
        // Option A: currentTime → wait for seeked → play().
        // Seek from 0 to skipSec always fires seeked.
        inactive.currentTime = skipSec
        let seekDone = false
        const onSeeked = () => {
          if (seekDone) return
          seekDone = true
          if (!cfCancelled) inactive.play().then(beginSync).catch(() => {})
        }
        inactive.addEventListener('seeked', onSeeked, { once: true })
        const fbTimer = setTimeout(() => { if (!seekDone) { seekDone = true; if (!cfCancelled) inactive.play().then(beginSync).catch(() => {}) } }, 1500)
        inactive.addEventListener('seeked', () => clearTimeout(fbTimer), { once: true })
      } else {
        inactive.play().then(beginSync).catch(() => {})
      }
    }, { once: true })

    const remaining = durMs - pos
    const rateC = cur()?.playbackRate || 1
    let cfMs = Math.min(cfEff * 1000, remaining)
    if (dj === 'doubledrop') cfMs = Math.min(remaining, plan.dd.len * barC * 1000 / rateC)
    else if (dj === 'echo' || dj === 'roll') cfMs = Math.min(remaining, (DJ_LEAD + DJ_TAIL[dj]) * barC * 1000 / rateC)
    // Geplante Ereignisse auf der Zeitachse des laufenden Titels: werden erst
    // kurz vorher auf die Audio-Uhr gelegt (das Tempo gleitet waehrend des
    // Uebergangs, eine Zeit von jetzt aus waere am Drop schon daneben)
    const djEvents = []
    let djEnd = Infinity, djCut = false
    if (dj === 'doubledrop') {
      // Neuer Titel ohne Bass bis zum Drop, dort hart tauschen
      const eo = eqOf(cur()), en = eqOf(inactive), now = audioCtx?.currentTime ?? 0
      if (eo && en) {
        eo.gain.cancelScheduledValues(now); en.gain.cancelScheduledValues(now)
        eo.gain.setValueAtTime(0, now); en.gain.setValueAtTime(BASS_KILL_DB, now)
        bassSwapOn = true
        djEvents.push({ at: plan.dd.drop1, fn: (tA) => {
          eo.gain.setValueAtTime(0, tA); eo.gain.linearRampToValueAtTime(BASS_KILL_DB, tA + 0.03)
          en.gain.setValueAtTime(BASS_KILL_DB, tA); en.gain.linearRampToValueAtTime(0, tA + 0.03)
        } })
      }
      djEnd = plan.dd.drop1 + plan.dd.post * barC
    } else if (dj === 'echo' || dj === 'roll') {
      resetAllBass()
      const mi = matchOf(inactive), mo = matchOf(cur()), now = audioCtx?.currentTime ?? 0
      if (mi) { mi.gain.cancelScheduledValues(now); mi.gain.setValueAtTime(0, now) }
      const beat = barC / 4
      const oldEl = cur()
      djEvents.push({ at: djB - (dj === 'roll' ? barC : beat), fn: (tA, rate) => {
        const beatR = beat / rate, tB = tA + (dj === 'roll' ? 4 : 1) * beatR
        if (dj === 'echo') {
          const sd = sendOf(oldEl)
          echoDelay.delayTime.setValueAtTime(Math.min(3.9, beatR), tA)
          echoFb.gain.setValueAtTime(0.5, tA)
          if (sd) { sd.gain.setValueAtTime(1, tA); sd.gain.setValueAtTime(0, tB) }
        } else {
          const sr = audioCtx.sampleRate, fr = (x) => Math.round(x * sr)
          rollOf(oldEl)?.port.postMessage({ start: fr(tA), end: fr(tB),
            steps: rollSteps(4).map(st => ({ at: fr(tA + st.at * beatR), len: Math.max(64, fr(st.len * beatR)) })) })
        }
        // Auf der Grenze: alter Titel aus, neuer Titel voll da
        if (mo) mo.gain.setTargetAtTime(0, tB, 0.004)
        if (mi) mi.gain.setTargetAtTime(1, tB - 0.002, 0.003)
        djCut = true
      } })
      djEnd = djB + DJ_TAIL[dj] * barC
    } else {
      planBassSwap(cur(), inactive, gridOf(curPath), gridOf(nextTrk.path), cfMs / 1000)
    }
    if (dj === 'filter') {
      const fi = fxOf(inactive), now = audioCtx?.currentTime ?? 0
      if (fi) { fi.type = 'lowpass'; fi.frequency.cancelScheduledValues(now); fi.frequency.setValueAtTime(200, now) }
    }
    let elapsed = 0
    const cfCurveSnap = get(appSettings).cfCurve ?? 'cosine'

    stopRamp(cur()); stopRamp(inactive)
    const _nDur = nextTrk.duration_sec || 0
    const matchDb = dj === 'doubledrop' || dj === 'echo' || dj === 'roll' ? 0
      : get(appSettings).cfLoudMatch === false || durMs <= 0 || !(_nDur > 0) ? 0
      : _blendMatchDb(get(waveform), get(waveformNext), pos / durMs, cfMs / durMs, introFrac, cfMs / 1000 / _nDur)
    if (thirdTrack?.path && get(waveformThird).path !== thirdTrack.path)
      requestWaveform('waveform_third', thirdTrack.path)
    cfStartAt = performance.now(); cfLenMs = cfMs; blendNextPos = introFrac
    requestAnimationFrame(_blendLoop)
    cfTimer = setInterval(() => {
      elapsed += 50
      const t      = Math.min(1, elapsed / cfMs)
      const active = cur()
      const inact  = alt()
      const v      = volume / 100
      let [fv, tv] = _fade(t, v, v, cfCurveSnap)
      // Faellige Ereignisse (Drop, Echo, Roll) auf die Audio-Uhr legen
      if (active && audioCtx) for (const e of djEvents) {
        if (e.done) continue
        const rate = active.playbackRate || 1
        const dt = (e.at - active.currentTime) / rate
        if (dt < 0.35) { e.done = true; e.fn(audioCtx.currentTime + Math.max(0.005, dt), rate) }
      }
      let done = t >= 1
      if (dj === 'doubledrop' && active) {
        const dd = plan.dd, ct = active.currentTime
        if (ct < dd.drop1) {
          // neuer Titel kommt leise dazu (Anlauf), alter bleibt voll
          const u = Math.max(0, (ct - dd.trig) / (dd.drop1 - dd.trig))
          fv = v; tv = v * 0.85 * Math.sqrt(Math.min(1, u / 0.5))
        } else {
          // Drop: beide zusammen, je ~2 dB leiser (zwei volle Titel addieren
          // sich), nur der Bass des neuen — dann blendet der alte aus und der
          // neue kommt wieder auf voll
          const w = Math.min(1, (ct - dd.drop1) / (dd.post * barC))
          const DD = 0.79                               // ≈ −2 dB
          fv = w < 0.5 ? v * DD : v * DD * Math.max(0, 1 - (w - 0.5) / 0.5)
          tv = v * (w < 0.5 ? DD : DD + (1 - DD) * (w - 0.5) / 0.5)
        }
        done = ct >= djEnd || elapsed >= cfMs * 1.15
      } else if (dj === 'echo' || dj === 'roll') {
        fv = v; tv = v                                   // geschaltet wird ueber den Angleich-Knoten
        done = (active && active.currentTime >= djEnd) || elapsed >= cfMs * 1.2
      } else if (dj === 'filter' && audioCtx) {
        const now = audioCtx.currentTime, fo = fxOf(active), fi = fxOf(inact)
        if (fo) { fo.type = 'highpass'; fo.frequency.setTargetAtTime(20 * Math.pow(1200 / 20, Math.max(0, (t - 0.2) / 0.8)), now, 0.05) }
        if (fi) { fi.type = 'lowpass'; fi.frequency.setTargetAtTime(200 * Math.pow(20000 / 200, Math.min(1, t / 0.7)), now, 0.05) }
      }
      if (active) active.volume = fv
      if (inact)  inact.volume  = tv
      // Angleichen: erste 40 % halten, dann auf 0 dB zurueck
      if (matchDb && inact) _setMatch(inact, t < 0.4 ? matchDb : matchDb * (1 - (t - 0.4) / 0.6))
      // Springen nur, solange der neue Titel noch leise ist
      cfSync?.tick(dj === 'doubledrop' ? t < 0.08 : (dj === 'echo' || dj === 'roll') ? !djCut : t < 0.3, t)
      if (inact && inact.duration > 0) blendNextPos = inact.currentTime / inact.duration
      if (done) {
        clearInterval(cfTimer); cfTimer = null
        _finishCrossfade(nextIdx, nextUrl, nextTrk.path)
      }
    }, 50)
  }

  function _silenceAndStop(el) {
    const gn = getGainNode(el)
    if (gn && audioCtx) {
      gn.gain.cancelScheduledValues(audioCtx.currentTime)
      gn.gain.setValueAtTime(gn.gain.value, audioCtx.currentTime)
      gn.gain.setTargetAtTime(0, audioCtx.currentTime, 0.015)
    }
    el.volume = 0
    setTimeout(() => {
      try { el.pause() } catch {}
      el.playbackRate = 1
      el.removeAttribute('src')
      if (gn) { gn.gain.cancelScheduledValues(audioCtx?.currentTime ?? 0); gn.gain.value = 1 }
      const eq = eqOf(el)
      if (eq) { eq.gain.cancelScheduledValues(audioCtx?.currentTime ?? 0); eq.gain.value = 0 }
      _resetFx(el)
    }, 80)
  }

  function _finishCrossfade(nextIdx, nextUrl, nextPath) {
    cfActive = false
    cfNextIdx = -1  // reset before which-flip so cfNextIdx-effect doesn't misfire on alt()
    if (cfTimer) { clearInterval(cfTimer); cfTimer = null }

    const oldEl = cur()
    const newEl = alt()
    loadedUrl = nextUrl
    if (newEl) { endSync(newEl); resetBass(newEl, 0.08) }
    bassSwapOn = false
    _resetMatch()
    if (newEl) _resetFx(newEl)
    if (oldEl) { const sd = sendOf(oldEl); if (sd && audioCtx) { sd.gain.cancelScheduledValues(audioCtx.currentTime); sd.gain.setValueAtTime(0, audioCtx.currentTime) } }
    blendP = 0; blendNextPos = 0
    _cfDoneAt = performance.now()
    outroOverride = nextOutroOverride?.path === nextPath ? nextOutroOverride : null
    introOverride = null
    which = which === 'A' ? 'B' : 'A'

    if (oldEl) _silenceAndStop(oldEl)
    if (newEl)  newEl.volume = volume / 100

    posMs = newEl ? (newEl.currentTime ?? 0) * 1000 : 0
    durMs = newEl ? (newEl.duration    ?? 0) * 1000 : 0
    _knownPos = 0

    // Immediately show next track's waveform — don't wait for WS round-trip.
    const wfNext = get(waveformNext)
    waveform.set(wfNext)
    const w3 = get(waveformThird)
    waveformNext.set(w3.path && w3.path === thirdTrack?.path ? w3.data : [])
    if (wfNext.length > 0) {
      // Prime _wfPath so the deck-1 waveform effect reuses this without a flicker
      _wfPath = nextPath
    } else {
      // No pre-fetched waveform — force a fresh request via the effect
      _wfPath = ''
    }

    send({ type: 'play_at', index: nextIdx })
  }

  // ── Audio element events ───────────────────────────────────────────────────
  function onLoadedMetadata(e) {
    if (e.target !== cur()) return
    durMs = (e.target.duration ?? 0) * 1000
    send({ type: 'position_update', position_ms: 0, duration_ms: Math.floor(durMs) })
    if ($playerState.playing) e.target.play().catch(() => {})
  }

  function onTimeUpdate(e) {
    if (e.target !== cur()) return
    const pos = e.target.currentTime * 1000
    posMs = pos
    livePositionMs.set(pos)
    _checkCrossfade(pos)
  }

  function onEnded(e) {
    if (e.target !== cur()) return
    if (cfActive) {
      if (cfTimer) { clearInterval(cfTimer); cfTimer = null }
      const q   = get(queue)
      const nxt = q[cfNextIdx]
      if (nxt?.path) {
        _finishCrossfade(cfNextIdx,
          'file:///' + nxt.path.replace(/\\/g, '/'),
          nxt.path)
      }
      return
    }
    const nxt = _nextIdx()
    if (nxt >= 0) {
      send({ type: 'play_at', index: nxt })
    } else {
      _queueRanOut()
    }
    posMs = 0
  }

  // ── Ende der Warteschlange ─────────────────────────────────────────────────
  // Ist der letzte Titel zu Ende, bevor Auto-Mix oder Radio etwas angehaengt
  // haben, wurde der neue Titel frueher zwar eingereiht, aber nie gestartet —
  // es blieb still, bis jemand auf Play drueckte.
  let waitForNext = $state(false)
  function _queueRanOut() {
    waitForNext = true
    if (get(autoMixEnabled)) {
      const t = get(nowPlaying)
      if (t?.title) send({ type: 'automix_trigger', title: t.title })
    }
  }
  $effect(() => {
    const idx = nextTrackIdx
    if (!waitForNext || idx < 0 || !$playerState.playing) return
    untrack(() => {
      waitForNext = false
      skipNextCrossfade.set(true)     // der alte Titel ist schon zu Ende
      send({ type: 'play_at', index: idx })
    })
  })

  // ── Nicht abspielbare Dateien ─────────────────────────────────────────────
  // Ohne das hier blendete der Crossfade in eine fehlende oder kaputte Datei
  // ueber — danach war es still, denn ein fehlerhaftes <audio> meldet nie
  // "ended". Jetzt wird die Datei uebersprungen und der naechste Titel genommen.
  let playerNotice = $state('')
  let _noticeTimer = null
  function _notice(text) {
    playerNotice = text
    clearTimeout(_noticeTimer)
    _noticeTimer = setTimeout(() => playerNotice = '', 10000)
  }

  function onMediaError(e) {
    const el = e.target
    const path = el.dataset.path
    if (!el.getAttribute('src') || !path) return        // absichtlich geleert
    badPaths.add(path); badVersion++
    const name = path.split(/[\\/]/).pop()
    _notice(`„${name}" lässt sich nicht abspielen — übersprungen`)
    console.warn('[player] nicht abspielbar:', path, el.error)

    if (el === alt() && cfActive) {
      // Automatischer Crossfade in die kaputte Datei: abbrechen, der aktuelle
      // Titel laeuft weiter; der naechste Versuch nimmt den Titel danach.
      if (cfTimer) { clearInterval(cfTimer); cfTimer = null }
      cfCancelled = true; cfActive = false; cfNextIdx = -1; cancelSync()
      resetAllBass()
      const c = cur(); if (c) c.volume = volume / 100
      _silenceAndStop(el)
      return
    }
    if (el !== cur()) return

    if (cfRaf !== null) {
      // Manueller Mix in die kaputte Datei: der alte Titel spielt noch und
      // bekommt die volle Lautstaerke zurueck, dann weiter zum naechsten.
      clearTimeout(cfRaf); cfRaf = null; cfRafActive = false
      const old = alt()
      which = which === 'A' ? 'B' : 'A'
      if (old) { old.volume = volume / 100; loadedUrl = old.getAttribute('src') ?? '' }
      _silenceAndStop(el)
      const nxt = _nextIdx()
      if (nxt >= 0) send({ type: 'play_at', index: nxt })
      return
    }

    const nxt = _nextIdx()
    if (nxt >= 0) {
      skipNextCrossfade.set(true)
      send({ type: 'play_at', index: nxt })
    } else {
      _queueRanOut()
    }
  }

  function playPrev() {
    const el = cur()
    if (el && el.currentTime > 3) {
      el.currentTime = 0; posMs = 0
      send({ type: 'seek', position_ms: 0 })
    } else {
      send({ type: 'play_prev' })
    }
  }

  function seek(e) {
    const rect  = e.currentTarget.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    const ms    = ratio * durMs
    const el    = cur()
    if (el && durMs > 0) { el.currentTime = ms / 1000; posMs = ms; _knownPos = Math.floor(ms) }
    send({ type: 'seek', position_ms: Math.floor(ms) })
  }

  function setVolume(v) {
    volume = +v
    const el = cur()
    const playingNow = get(playerState).playing
    if (el && !cfActive && !cfRafActive && playingNow) rampVolume(el, volume / 100, get(appSettings).volumeFadeMs ?? 200)
    else if (el && (playingNow || el.paused)) el.volume = volume / 100
    send({ type: 'set_volume', value: volume })
  }


  // Mausrad am Fader: 2er-Schritte, genauer als Ziehen am kurzen Regler
  function onVolWheel(e) {
    e.preventDefault()
    const v = Math.max(0, Math.min(100, volume + (e.deltaY < 0 ? 2 : -2)))
    if (v !== volume) setVolume(v)
  }

  function setCrossfade(v) {
    cfS = +v
    send({ type: 'set_crossfade', seconds: cfS })
  }

  const pos = $derived(durMs > 0 ? posMs / durMs : 0)

  // Nur beim Entwickeln: Einblick fuer automatische Tests (fehlt im fertigen Build)
  if (import.meta.env.DEV) {
    window.__player = {
      get elA() { return elA }, get elB() { return elB }, get which() { return which },
      get eqA() { return eqA?.gain.value }, get eqB() { return eqB?.gain.value },
      get bassSwapOn() { return bassSwapOn }, get cfActive() { return cfActive }, get cfS() { return cfS }, get cfEff() { return cfEff },
      get durMs() { return durMs }, get triggerMs() { return outroBarStart >= 0 ? outroBarStart * durMs : -1 },
      get nextIntroStart() { return nextIntroStart }, get nextOutroZone() { return nextOutroZone },
      get match() { return [matchA?.gain.value, matchB?.gain.value] },
      get djPlan() { return djPlan }, get drops() { return [curDrops, nextDrops] }, get rollerOk() { return rollerOk },
      pickDj: (t) => pickDj(t), get fx() { return [fxA?.type, fxA?.frequency.value, fxB?.type, fxB?.frequency.value] }, lufsOf: (t) => lufsOf(t), grid: (p) => gridOf(p),
      get audioTime() { return audioCtx?.currentTime ?? 0 }, get sinkId() { return audioCtx?.sinkId ?? null },
    }
  }

  let centerH = $state(110)
  let deckH   = $state(0)
  let deck1H   = $state(44)
  let nextBarH = $state(18)
  let deck2H   = $state(48)
  const WF1_H = 60, WF2_H = 28
</script>

<svelte:window onclick={() => { if (djMenu) djMenu = null }} onkeydown={(e) => { if (e.key === 'Escape' && djMenu) djMenu = null }} />

<!-- Auswahl des Uebergangs: ganz aussen, sonst schneidet der Deck-Bereich (eigene Ebene fuer die Animation) es ab -->
{#if djMenu && djPlan}
  <div class="dj-menu" role="menu" style="left:{djMenu.x}px;top:{djMenu.y}px" onclick={(e) => e.stopPropagation()}>
    <button role="menuitem" class:sel={djPlan.auto} onclick={() => pickDj('auto')}>Automatisch</button>
    {#each ['blend', 'filter', 'echo', 'roll', 'doubledrop'] as t}
      {@const off = t !== 'blend' && (!djPlan.eligible || (t === 'doubledrop' && !djPlan.ddOk) || (t === 'roll' && !rollerOk))}
      <button role="menuitem" class:sel={!djPlan.auto && djPlan.type === t} disabled={off}
              title={off ? (t === 'doubledrop' && djPlan.eligible ? 'Keine passenden Drops erkannt' : 'Tempo oder Takt passen nicht') : ''}
              onclick={() => pickDj(t)}>{DJ_LABEL[t]}</button>
    {/each}
  </div>
{/if}


<audio bind:this={elA} ontimeupdate={onTimeUpdate} onloadedmetadata={onLoadedMetadata} onended={onEnded} onerror={onMediaError}></audio>
<audio bind:this={elB} ontimeupdate={onTimeUpdate} onloadedmetadata={onLoadedMetadata} onended={onEnded} onerror={onMediaError}></audio>

<div class="player">
  <div class="player-row" style="--player-h:{Math.max(centerH, deckH)}px">

    <!-- Cover und Transport-Knoepfe teilen sich eine Spalte: spart die eigene Knopfzeile -->
    <div class="deck" bind:clientHeight={deckH}>
      <div class="art">
        {#if $nowPlaying?.art}
          <img src={$nowPlaying.art} alt="" />
        {:else}
          <i class="ti ti-music art-ph" aria-hidden="true"></i>
        {/if}
      </div>
      <div class="controls">
        <button class="btn btn-icon btn-sm" onclick={playPrev} title="Zurück" aria-label="Zurück"><i class="ti ti-player-track-prev"></i></button>
        <button class="play" onclick={() => send({ type: $playerState.playing ? 'pause' : 'resume' })}
                title={$playerState.playing ? 'Pause' : 'Abspielen'} aria-label={$playerState.playing ? 'Pause' : 'Abspielen'}>
          <i class="ti {$playerState.playing ? 'ti-player-pause-filled' : 'ti-player-play-filled'}"></i>
        </button>
        <button class="btn btn-icon btn-sm" onclick={() => { const n = _nextIdx(); if (n >= 0) send({ type: 'play_at', index: n }) }}
                title="Weiter" aria-label="Weiter"><i class="ti ti-player-track-next"></i></button>
      </div>
    </div>

    <div class="center" bind:clientHeight={centerH}>
      <!-- Eine Zeile: Titel links, Tonart/BPM/Zeit rechtsbuendig. Die Zeile
           darunter faellt weg, die Waveform bekommt den Platz -->
      <div class="track-info">
        <span class="title" title={$nowPlaying?.title ?? ''}>{$nowPlaying?.title ?? '—'}{#if $nowPlaying?.artist && !($nowPlaying.title ?? '').toLowerCase().includes($nowPlaying.artist.toLowerCase())}<span class="artist"> · {$nowPlaying.artist}</span>{/if}</span>
        <span class="meta">
          {#if playerNotice}<span class="lufs-warn" role="status"><i class="ti ti-alert-triangle"></i> {playerNotice}</span>{/if}
          {#if curKey}<KeyChip key={curKey.key} src={curKey.key_src} />{/if}
          {#if $nowPlaying?.bpm}<span class="m-num">{$nowPlaying.bpm} BPM</span>
          {:else if $nowPlaying}<span class="bpm-pending">BPM wird gemessen…</span>{/if}
          <!-- LUFS nur auf Wunsch (Einstellungen → Darstellung) -->
          {#if !$appSettings.playerShowLufs}
          {:else if lufsOf($nowPlaying) > -90}
            {#if $appSettings.normalizeVolume}
              <span class="m-num norm-on" title="Lautstärke-Angleichung an: der Hauptteil ({lufsOf($nowPlaying).toFixed(1)} LUFS) wird auf {$appSettings.targetLUFS} LUFS gebracht (Einstellungen → Wiedergabe)"><b>≋</b> {lufsOf($nowPlaying).toFixed(1)} → {$appSettings.targetLUFS} LUFS</span>
            {:else}
              <span class="m-num" title="Lautstärke-Angleichung aus (Einstellungen → Wiedergabe)">{lufsOf($nowPlaying).toFixed(1)} LUFS</span>
            {/if}
          {:else if $appSettings.normalizeVolume && $nowPlaying}
            <span class="lufs-warn" title="Keine Lautstärkemessung — Normalisierung nicht aktiv für diesen Track">kein LUFS-Wert</span>
          {/if}
          {#if bassSwapOn}<span class="swap-badge" title="Übergang im Takt: Bass wird weich getauscht (Einstellungen → Blend)"><i class="ti ti-arrows-exchange" aria-hidden="true"></i> Bass</span>{/if}
          <span class="time"><b>{fmt(posMs)}</b> / {fmt(durMs)}</span>
        </span>
      </div>

      <!-- Beim Uebergang gleitet der naechste Titel nach oben an die Stelle des
           laufenden; am Ende tauschen die Daten, das Bild bleibt gleich -->
      <div class="decks" style={blendP > 0 ? `height:${deck1H + 6 + deck2H}px` : ''}>
        <div class="deck1" bind:clientHeight={deck1H}
             style={blendP > 0 ? `transform:translateY(${-blendP * 100}%);opacity:${1 - blendP}` : ''}>
          <Waveform data={$waveform} position={pos} onclick={seek} height={WF1_H}
                    outroStart={outroBarStart} outroEnd={outroBarEnd}
                    band introMark={curIntroMark} outroMark={curOutroMark}
                    dragZone="outro" onzonedrag={dragOutro}
                    zoneTitle="MIX-Zone ziehen: Übergang früher oder später starten (nur dieses Mal)"
                    loading={$nowPlaying !== null && $waveform.length === 0} />
        </div>
        {#if nextTrack}
          <div class="deck2" bind:clientHeight={deck2H} style={blendP > 0 ? `transform:translateY(${-blendP * (deck1H + nextBarH + 12)}px)` : ''}>
            <div class="next-bar" bind:clientHeight={nextBarH} style={blendP > 0 ? `opacity:${1 - Math.min(1, blendP * 2)}` : ''}>
              <span class="eyebrow next-label">Nächster</span>
              <span class="next-title" title={nextTrack.title + (nextTrack.artist ? ' · ' + nextTrack.artist : '')}>{nextTrack.title}{nextTrack.artist ? ' · ' + nextTrack.artist : ''}</span>
              {#if nextKey}
                <KeyChip key={nextKey.key} src={nextKey.key_src} compat={uebergang.level === 'unknown' ? null : uebergang} hint="Übergang: " ring boost={!!nextTrack.energy_boost} />
              {/if}
              {#if nextBpm}
                <span class="next-bpm" class:far={nextTempo && !nextTempo.ok}
                      title={!nextTempo ? 'Tempo des nächsten Titels' : nextTempo.ok ? `Tempo wird im Übergang angeglichen (${nextTempo.pct} %)` : `Mehr als ${nextTempo.lim} % Tempo-Unterschied — der Übergang wird nur geblendet (Einstellungen → Blend)`}>{Math.round(nextBpm)} BPM</span>
              {/if}
              {#if $appSettings.djMode && djPlan && blendP === 0}
                <span class="dj-wrap">
                  <button class="dj-chip" class:on={djPlan.type !== 'blend'} onclick={toggleDjMenu}
                          title="Übergang zum nächsten Titel (DJ-Modus) — klicken zum Ändern">
                    {DJ_LABEL[djPlan.type]}{#if !djPlan.auto}<i class="ti ti-pin" aria-hidden="true"></i>{/if}
                  </button>
                </span>
              {/if}
              <span class="next-dur">{fmt(nextTrack.duration_sec * 1000)}</span>
            </div>
            <div class="wf2" style={blendP > 0 ? `transform:scaleY(${1 + blendP * ((WF1_H + WF_BAND) / (WF2_H + WF_BAND) - 1)})` : ''}>
              <Waveform data={$waveformNext} position={blendP > 0 ? blendNextPos : 0} height={WF2_H}
                        introStart={nextIntroStart} introEnd={nextIntroEnd}
                        outroStart={nextOutroZone?.[0] ?? -1} outroEnd={nextOutroZone?.[1] ?? -1}
                        band bandLabels={blendP === 0} introMark={nextIntroMark} outroMark={nextOutroMark}
                        dragZone="both" onzonedrag={(f, done, kind) => kind === 'outro' ? dragNextOutro(f, done) : dragIntro(f, done)}
                        zoneTitle={{ intro: 'MIX-Zone ziehen: an anderer Stelle in den nächsten Titel einsteigen (nur dieses Mal)',
                                     outro: 'MIX-Zone ziehen: wenn dieser Titel läuft, früher oder später ausmischen (nur dieses Mal)' }} />
            </div>
          </div>
          {#if blendP > 0 && thirdTrack}
            <!-- Titel danach rueckt von unten in die Zeile "Naechster" nach -->
            <div class="deck2 deck3" style={`transform:translateY(${-blendP * (deck2H + 6)}px);opacity:${Math.min(1, blendP * 1.6)}`}>
              <div class="next-bar">
                <span class="eyebrow next-label">Nächster</span>
                <span class="next-title">{thirdTrack.title}{thirdTrack.artist ? ' · ' + thirdTrack.artist : ''}</span>
                {#if thirdKey}
                  <KeyChip key={thirdKey.key} src={thirdKey.key_src} compat={thirdCompat.level === 'unknown' ? null : thirdCompat} hint="Übergang: " ring boost={!!thirdTrack.energy_boost} />
                {/if}
                {#if thirdBpm}
                  <span class="next-bpm" class:far={thirdTempo && !thirdTempo.ok}>{Math.round(thirdBpm)} BPM</span>
                {/if}
                <span class="next-dur">{fmt(thirdTrack.duration_sec * 1000)}</span>
              </div>
              <Waveform data={thirdWf} position={0} height={WF2_H}
                        introStart={thirdIntroStart} introEnd={thirdIntroEnd}
                        outroStart={thirdOutroZone?.[0] ?? -1} outroEnd={thirdOutroZone?.[1] ?? -1}
                        band introMark={thirdIntroMark} outroMark={thirdOutroMark} />
            </div>
          {/if}
        {/if}
      </div>
    </div>

    <!-- Schmaler Lautstaerke-Fader; Mausrad fuer feine Schritte -->
    <div class="right" onwheel={onVolWheel}>
      <span class="fdr-val" aria-hidden="true">{volume}</span>
      <div class="fdr-slot">
        <div class="fdr-groove"><div class="fdr-fill" style="height:{volume}%"></div></div>
        <input type="range" class="fdr-input" min="0" max="100" value={volume} aria-label="Lautstärke"
               title="Lautstärke {volume} — Mausrad für feine Schritte"
               onmousedown={() => _volDragging = true}
               onmouseup={() => { _volDragging = false }}
               ontouchstart={() => _volDragging = true}
               ontouchend={() => { _volDragging = false }}
               oninput={(e) => setVolume(e.target.value)} />
      </div>
      <span class="eyebrow">Vol</span>
    </div>
  </div>
</div>

<style>
  .swap-badge { display: inline-flex; align-items: center; gap: 3px; font-size: var(--fs-cap); font-weight: 700;
                letter-spacing: .04em; color: var(--c-accent-tx); padding: 0 6px; border-radius: var(--r-s);
                border: 1px solid color-mix(in srgb, var(--c-accent) 50%, transparent); }
  .player { display: flex; flex-direction: column; padding: var(--sp-3) var(--sp-4) var(--sp-2); background: var(--c-bg); flex-shrink: 0; }
  .player-row { display: flex; align-items: flex-start; gap: var(--sp-4); }

  .deck { display: flex; flex-direction: column; align-items: center; gap: 6px; flex-shrink: 0; }
  /* Grosses Cover, Bedienung klein darunter */
  .art {
    width: 120px; height: 120px; border-radius: var(--r-m); flex-shrink: 0;
    background: var(--c-bg5); border: 1px solid var(--c-br1);
    display: flex; align-items: center; justify-content: center; overflow: hidden;
  }
  .art img { width: 100%; height: 100%; object-fit: cover; }
  .art-ph { font-size: 40px; color: var(--c-tx6); }
  :global([data-density="comfortable"]) .art { width: 140px; height: 140px; }
  .decks { display: flex; flex-direction: column; gap: 6px; overflow: hidden; }
  .deck1, .deck2 { will-change: transform; }
  .deck2 { display: flex; flex-direction: column; gap: 6px; }
  .wf2 { transform-origin: top center; }

  .center { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .track-info { display: flex; align-items: center; gap: var(--sp-4); min-width: 0; }
  .title {
    flex: 1; min-width: 0;
    font-size: var(--fs-xl); font-weight: 700; color: var(--c-tx1); line-height: 1.25;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .meta { flex-shrink: 0; display: flex; align-items: center; gap: var(--sp-3); font-size: var(--fs-body); color: var(--c-tx3); }
  .artist { color: var(--c-tx3); font-weight: 600; }
  .m-num { font-variant-numeric: tabular-nums; }
  .bpm-pending { color: var(--c-tx5); font-style: italic; }
  .lufs-warn { color: var(--c-warn-tx); display: inline-flex; align-items: center; gap: 4px; }

  .next-bar { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
  .next-label { flex-shrink: 0; }
  .next-title { flex: 1; min-width: 0; font-size: var(--fs-body); color: var(--c-tx2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dj-wrap { position: relative; flex-shrink: 0; }
  .dj-chip {
    display: inline-flex; align-items: center; gap: 3px; height: 20px; padding: 0 7px;
    border-radius: 10px; border: 1px solid var(--c-br2); background: none; cursor: pointer;
    font: 600 var(--fs-cap) 'Segoe UI', system-ui, sans-serif; color: var(--c-tx3); white-space: nowrap;
  }
  .dj-chip.on { color: var(--c-accent-tx); border-color: color-mix(in srgb, var(--c-accent) 55%, transparent); }
  .dj-chip:hover { background: var(--c-hover); }
  .dj-chip .ti { font-size: 11px; }
  .dj-menu {
    position: fixed; z-index: 900; width: 170px;
    display: flex; flex-direction: column; padding: 4px;
    background: var(--c-bg5); border: 1px solid var(--c-br2); border-radius: var(--r-m); box-shadow: 0 10px 28px rgba(0,0,0,.45);
  }
  .dj-menu button {
    text-align: left; height: 28px; padding: 0 10px; border: none; background: none; border-radius: var(--r-s);
    font: inherit; font-size: var(--fs-body); color: var(--c-tx2); cursor: pointer;
  }
  .dj-menu button:hover:not(:disabled) { background: var(--c-hover); color: var(--c-tx1); }
  .dj-menu button.sel { color: var(--c-accent-tx); font-weight: 600; }
  .dj-menu button:disabled { color: var(--c-tx5); cursor: default; }
  .next-bpm { flex-shrink: 0; font-size: var(--fs-sm); color: var(--c-tx2); font-variant-numeric: tabular-nums; }
  .next-bpm.far { color: var(--c-warn-tx); }
  .next-dur { flex-shrink: 0; font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; }

  .controls { display: flex; align-items: center; justify-content: center; gap: var(--sp-1); }
  .norm-on b { color: var(--c-accent-tx); font-weight: 700; }
  .play {
    width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0;
    border: none; background: var(--c-accent); color: var(--c-on-accent);
    font-size: 16px; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background .12s, transform .08s;
  }
  .play:hover { background: var(--c-accent2); }
  .play:active { transform: scale(.96); }
  :global([data-density="comfortable"]) .play { width: 40px; height: 40px; font-size: 19px; }
  .time { font-size: var(--fs-body); color: var(--c-tx3); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .time b { color: var(--c-tx1); font-weight: 600; }

  /* ── Lautstaerke-Fader: schmal, Wert oben, Fuellung zeigt den Pegel ─────── */
  .right {
    display: flex; flex-direction: column; align-items: center; gap: 4px; flex-shrink: 0;
    width: 36px; height: var(--player-h, 120px);
  }
  .fdr-val { font-size: var(--fs-body); font-weight: 700; color: var(--c-tx1); font-variant-numeric: tabular-nums; line-height: 1; }
  .fdr-slot { position: relative; flex: 1; min-height: 0; width: 28px; display: flex; justify-content: center; }
  .fdr-groove {
    position: absolute; width: 4px; top: 6px; bottom: 6px; border-radius: 2px; pointer-events: none;
    background: var(--c-br2); display: flex; align-items: flex-end; overflow: hidden;
  }
  .fdr-fill { width: 100%; background: var(--c-accent); border-radius: 2px; }
  .fdr-input {
    -webkit-appearance: none; writing-mode: vertical-lr; direction: rtl;
    width: 28px; height: 100%; background: transparent; cursor: grab;
    position: relative; z-index: 1; padding: 0; margin: 0;
  }
  .fdr-input:active { cursor: grabbing; }
  .fdr-input::-webkit-slider-runnable-track { width: 4px; background: transparent; border-radius: 2px; }
  .fdr-input::-webkit-slider-thumb {
    -webkit-appearance: none; width: 24px; height: 12px; margin-left: -10px;
    background: var(--c-tx1); border: 1px solid var(--c-br3); border-radius: var(--r-s);
    box-shadow: 0 1px 4px rgba(0,0,0,.45), inset 0 -1px 0 var(--c-br3);
  }
  .fdr-input:focus-visible { outline: 2px solid var(--c-focus); outline-offset: 2px; border-radius: var(--r-s); }
  input[type=range] { cursor: pointer; }
</style>
