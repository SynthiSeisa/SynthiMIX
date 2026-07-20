<script>
  import { get } from 'svelte/store'
  import { untrack, onMount } from 'svelte'
  import { playerState, nowPlaying, queue, waveform, waveformNext, settings, playMode, send, autoMixEnabled, appSettings, introSkipPaths, skipNextCrossfade, livePositionMs } from '../stores/ws.js'
  import Waveform from './Waveform.svelte'

  let elA = $state(null)
  let elB = $state(null)
  let which = $state('A')

  let posMs  = $state(0)
  let durMs  = $state(0)
  let volume = $state(80)
  let cfS    = $state(8)

  let cfActive     = false
  let cfTimer      = null
  let cfNextIdx    = $state(-1)
  let cfRaf        = null
  let cfCancelled  = false   // cancels pending canplay.play() on pause
  let loadedUrl    = ''

  let audioCtx = null
  let gainA = null, gainB = null
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
      if (el && !cfActive) el.volume = volume / 100
    }
  })

  $effect(() => {
    if (!elA || !elB || audioCtx) return
    try {
      audioCtx = new AudioContext()
      gainA = audioCtx.createGain(); gainA.connect(audioCtx.destination)
      gainB = audioCtx.createGain(); gainB.connect(audioCtx.destination)
      audioCtx.createMediaElementSource(elA).connect(gainA)
      audioCtx.createMediaElementSource(elB).connect(gainB)
    } catch (e) { console.warn('AudioContext:', e) }
  })

  $effect(() => {
    if ($playerState.playing && audioCtx?.state === 'suspended') audioCtx.resume().catch(() => {})
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
    }
    if (lufsB > -90) {
      const t = normFactor(lufsB)
      if (elB && !elB.paused && Math.abs(gainB.gain.value - t) > 0.05) {
        gainB.gain.cancelScheduledValues(audioCtx.currentTime)
        gainB.gain.setValueAtTime(gainB.gain.value, audioCtx.currentTime)
        gainB.gain.linearRampToValueAtTime(t, audioCtx.currentTime + 0.4)
      } else { gainB.gain.value = t }
    }
  })

  $effect(() => {
    const lufs = $nowPlaying?.lufs
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
    const lufs = $queue[idx]?.lufs
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

  function normFactor(lufs) {
    const target = $appSettings.targetLUFS ?? -14
    if (!$appSettings.normalizeVolume || !lufs || lufs <= -90) return 1.0
    const db = Math.max(-20, Math.min(12, target - lufs))
    const factor = Math.pow(10, db / 20)
    // Cap so that gain × el.volume (= volume/100) never exceeds 1.0 (prevents clipping)
    const v = volume / 100
    return v > 0 ? Math.min(factor, 1.0 / v) : factor
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
  // Bar START = this value. Bar END = this + cfS/duration.
  function _getNextIntroStart() {
    const cfg = get(appSettings)
    const nt  = get(queue)[_nextIdx()]
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
    if (durMs <= 0 || cfS <= 0) return -1
    const cfg    = get(appSettings)
    const cfFrac = Math.min(0.9, (cfS * 1000) / durMs)
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

  $effect(() => { void $waveform;     _sfWf  = null })
  $effect(() => { void $waveformNext; _sfNWf = null })

  // ── Derived grey-bar zones for the waveform ────────────────────────────────
  const _cfFrac = $derived(durMs > 0 && cfS > 0 ? Math.min(0.9, (cfS * 1000) / durMs) : 0)

  // Outro grey "mix" bar on the current track: [trigger, trigger + crossfade]
  const outroBarStart = $derived.by(() => {
    if (!$appSettings.smartFade || durMs <= 0 || cfS <= 0) return -1
    void $waveform; void $appSettings.outroAggressiveness
    return _outroTrigger()
  })
  const outroBarEnd = $derived(
    outroBarStart >= 0 ? Math.min(1, outroBarStart + _cfFrac) : -1
  )

  const nextTrackIdx = $derived.by(() => {
    const ci  = $playerState.current_idx
    const n   = $queue.length
    const rep = $playMode.repeat
    if (!n) return -1
    const nxt = ci + 1
    if (nxt < n) return nxt
    if (rep === 2) return 0
    return -1
  })
  const nextTrack    = $derived(
    nextTrackIdx >= 0 && nextTrackIdx < $queue.length ? $queue[nextTrackIdx] : null
  )
  // Deck 2 grey bar:
  //   START = where track 2 begins playing (seeks to this position)
  //   END   = start + cfS (bar is always exactly one crossfade wide)
  //   Level 1 (soft) → bar at 0:00; Level 5 (aggressive) → bar before the drop
  const nextIntroStart = $derived.by(() => {
    const sf = $appSettings.smartFade
    const ia = $appSettings.introAggressiveness ?? $appSettings.fadeAggressiveness ?? 3
    const wf = $waveformNext
    const nt = nextTrack
    if (!nt || cfS <= 0 || !sf) return 0
    const a   = Math.max(0, Math.min(4, ia - 1))
    const det = wf?.length > 0 ? _detectIntroLen(wf) * INTRO_SKIP[a] : 0
    return Math.min(0.45, Math.max(det, INTRO_FRACS[a]))
  })
  const nextIntroEnd = $derived.by(() => {
    const nt = nextTrack
    if (!nt || cfS <= 0) return -1
    const dur = nt.duration_sec || 180
    return Math.min(0.95, nextIntroStart + cfS / dur)
  })

  let _lastNextPath = ''
  $effect(() => {
    const nt = nextTrack
    if (nt?.path && nt.path !== _lastNextPath) {
      _lastNextPath = nt.path
      send({ type: 'get_waveform_next', path: nt.path })
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
    if (cfRaf)   { cancelAnimationFrame(cfRaf); cfRaf = null }
    cfCancelled = true
    cfActive = false; cfNextIdx = -1

    const c  = untrack(cur)
    const a  = untrack(alt)
    const v  = untrack(() => volume) / 100
    const cf = untrack(() => cfS)
    const wasPlaying = c && c.src && c.readyState >= 2 && !c.paused

    // Consume the one-shot flag: user-initiated plays skip the crossfade so
    // the old track stops immediately instead of fading out over cfS seconds.
    const forceImmediate = get(skipNextCrossfade)
    if (forceImmediate) skipNextCrossfade.set(false)

    loadedUrl = url

    if (wasPlaying && cf > 0 && !forceImmediate) {
      cfCancelled = false
      a.src = url; a.volume = 0; a.load(); a.play().catch(() => {})
      setElLufs(a, track.lufs ?? -99)
      which = untrack(() => which) === 'A' ? 'B' : 'A'

      // LUFS-Angleichung für manuellen Crossfade (Mix Now / load-effect-Weg):
      // setTimeout(0) läuft nach dem Svelte-Microtask für setElLufs,
      // sodass unser Gain-Override den $effect überschreibt.
      const _mixNextLufs = track.lufs ?? -99
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

      const fadeMs = cf * 1000
      const t0     = performance.now()
      const vOld   = c.volume

      function rafTick() {
        const t = Math.min(1, (performance.now() - t0) / fadeMs)
        const [fv, tv] = _fade(t, vOld, v)
        c.volume = fv; a.volume = tv
        if (t < 1) cfRaf = requestAnimationFrame(rafTick)
        else { cfRaf = null; _silenceAndStop(c) }
      }
      cfRaf = requestAnimationFrame(rafTick)
    } else {
      // Silence the alt deck immediately in case a crossfade was mid-flight
      const oldAlt = untrack(alt)
      if (oldAlt) _silenceAndStop(oldAlt)
      const el = untrack(cur)
      if (el) {
        el.src = url; el.volume = v; el.load(); setElLufs(el, track.lufs ?? -99)
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
      send({ type: 'get_waveform', path: p })
    }
  })

  // ── Play / pause sync ──────────────────────────────────────────────────────
  $effect(() => {
    const playing = $playerState.playing
    const el = cur()
    if (!el) return
    if (playing) {
      if (el.paused) el.play().catch(() => {})
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
      el.pause()
      // Stop crossfade timer and kill the crossfading-in element unconditionally
      if (cfTimer) { clearInterval(cfTimer); cfTimer = null }
      if (cfRaf)   { cancelAnimationFrame(cfRaf); cfRaf = null }
      if (cfActive) { cfActive = false; cfNextIdx = -1 }
      const a = alt()
      if (a) {
        try { a.pause() } catch {}
        a.removeAttribute('src')
        try { a.load() } catch {}  // flush queued canplay/loadeddata events
      }
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
    const pm = get(playMode)
    const q  = get(queue)
    const ci = get(playerState).current_idx
    if (!q.length) return -1
    if (pm.repeat === 1) return ci
    if (pm.shuffle) {
      const pool = [...q.keys()].filter(i => i !== ci)
      return pool.length
        ? pool[Math.floor(Math.random() * pool.length)]
        : (pm.repeat === 2 ? ci : -1)
    }
    const nxt = ci + 1
    return nxt < q.length ? nxt : (pm.repeat === 2 ? 0 : -1)
  }

  // ── Auto-crossfade ─────────────────────────────────────────────────────────
  function _checkCrossfade(pos) {
    if (cfActive || cfS <= 0 || durMs <= 0) return

    const trigFrac  = _outroTrigger()
    const triggerMs = trigFrac >= 0 ? trigFrac * durMs : durMs - cfS * 1000

    if (pos < triggerMs - 100 || pos >= durMs - 100) return

    const q       = get(queue)
    const nextIdx = _nextIdx()
    if (nextIdx < 0 || nextIdx >= q.length) return
    const nextTrk = q[nextIdx]
    if (!nextTrk?.path) return

    cfActive     = true
    cfNextIdx    = nextIdx
    cfCancelled  = false
    const nextUrl  = 'file:///' + nextTrk.path.replace(/\\/g, '/')
    const inactive = alt()
    if (!inactive) { cfActive = false; return }

    setElLufs(inactive, nextTrk.lufs ?? -99)

    // LUFS-Angleichung: eingehendes Deck auf denselben Pegel wie aktives Deck bringen.
    // Wenn normalizeVolume aktiv ist, erledigt das bereits der Gain-$effect.
    // Ohne Normalisierung gleichen wir manuell an – Gain wird im canplay-Handler gesetzt,
    // da der $effect (ausgelöst durch setElLufs oben) zuvor als Microtask abläuft.
    const _nextLufs = nextTrk.lufs ?? -99
    const _curLufs  = which === 'A' ? lufsA : lufsB
    const _cfSettings = get(appSettings)
    let _cfMatchGain = null
    if (_nextLufs > -90 && _curLufs > -90 && !_cfSettings.normalizeVolume) {
      const db = Math.max(-20, Math.min(12, _curLufs - _nextLufs))
      _cfMatchGain = Math.pow(10, db / 20)
    }

    const introFrac = _getNextIntroStart()

    inactive.src = nextUrl
    inactive.load()

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
      const skipSec = (introFrac > 0.01 && elDur > 0 && isFinite(elDur))
        ? introFrac * elDur : 0

      if (skipSec > 0.5) {
        // Option A: currentTime → wait for seeked → play().
        // Seek from 0 to skipSec always fires seeked.
        inactive.currentTime = skipSec
        let seekDone = false
        const onSeeked = () => {
          if (seekDone) return
          seekDone = true
          if (!cfCancelled) inactive.play().catch(() => {})
        }
        inactive.addEventListener('seeked', onSeeked, { once: true })
        const fbTimer = setTimeout(() => { if (!seekDone) { seekDone = true; if (!cfCancelled) inactive.play().catch(() => {}) } }, 1500)
        inactive.addEventListener('seeked', () => clearTimeout(fbTimer), { once: true })
      } else {
        inactive.play().catch(() => {})
      }
    }, { once: true })

    const remaining = durMs - pos
    const cfMs = Math.min(cfS * 1000, remaining)
    let elapsed = 0
    const cfCurveSnap = get(appSettings).cfCurve ?? 'cosine'

    cfTimer = setInterval(() => {
      elapsed += 50
      const t      = Math.min(1, elapsed / cfMs)
      const active = cur()
      const inact  = alt()
      const v      = volume / 100
      const [fv, tv] = _fade(t, v, v, cfCurveSnap)
      if (active) active.volume = fv
      if (inact)  inact.volume  = tv
      if (t >= 1) {
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
      el.removeAttribute('src')
      if (gn) { gn.gain.cancelScheduledValues(audioCtx?.currentTime ?? 0); gn.gain.value = 1 }
    }, 80)
  }

  function _finishCrossfade(nextIdx, nextUrl, nextPath) {
    cfActive = false
    cfNextIdx = -1  // reset before which-flip so cfNextIdx-effect doesn't misfire on alt()
    if (cfTimer) { clearInterval(cfTimer); cfTimer = null }

    const oldEl = cur()
    const newEl = alt()
    loadedUrl = nextUrl
    which = which === 'A' ? 'B' : 'A'

    if (oldEl) _silenceAndStop(oldEl)
    if (newEl)  newEl.volume = volume / 100

    posMs = newEl ? (newEl.currentTime ?? 0) * 1000 : 0
    durMs = newEl ? (newEl.duration    ?? 0) * 1000 : 0
    _knownPos = 0

    // Immediately show next track's waveform — don't wait for WS round-trip.
    const wfNext = get(waveformNext)
    waveform.set(wfNext)
    waveformNext.set([])
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
    } else if (get(autoMixEnabled)) {
      const t = get(nowPlaying)
      if (t?.title) send({ type: 'automix_trigger', title: t.title })
    }
    posMs = 0
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
    const el = cur(); if (el) el.volume = volume / 100
    send({ type: 'set_volume', value: volume })
  }


  function setCrossfade(v) {
    cfS = +v
    send({ type: 'set_crossfade', seconds: cfS })
  }

  const pos = $derived(durMs > 0 ? posMs / durMs : 0)

  let centerH = $state(110)
</script>

<audio bind:this={elA} ontimeupdate={onTimeUpdate} onloadedmetadata={onLoadedMetadata} onended={onEnded}></audio>
<audio bind:this={elB} ontimeupdate={onTimeUpdate} onloadedmetadata={onLoadedMetadata} onended={onEnded}></audio>

<div class="player">
  <div class="player-row" style="--player-h:{centerH}px">

    <div class="art">
      {#if $nowPlaying?.art}
        <img src={$nowPlaying.art} alt="" />
      {:else}
        <span class="art-ph">♫</span>
      {/if}
    </div>

    <div class="center" bind:clientHeight={centerH}>
      <div class="track-info">
        <span class="title">{$nowPlaying?.title ?? '—'}</span>
        <span class="meta">
          {#if $nowPlaying?.lufs && $nowPlaying.lufs > -90}
            <span>{$nowPlaying.lufs.toFixed(1)} LUFS</span>
          {:else if $appSettings.normalizeVolume && $nowPlaying}
            <span class="lufs-warn" title="Keine Lautstärkemessung — Normalisierung nicht aktiv für diesen Track">⚠ kein LUFS</span>
          {/if}
          {#if $nowPlaying?.bpm}<span>{$nowPlaying.bpm} BPM</span>
          {:else if $nowPlaying}<span class="bpm-pending">· BPM</span>{/if}
          {#if $nowPlaying?.play_count}<span>×{$nowPlaying.play_count}</span>{/if}
        </span>
      </div>

      <Waveform data={$waveform} position={pos} onclick={seek}
                outroStart={outroBarStart} outroEnd={outroBarEnd}
                loading={$nowPlaying !== null && $waveform.length === 0} />

      {#if nextTrack}
        <div class="next-bar">
          <span class="next-arrow">↓</span>
          <span class="next-label">NÄCHSTER</span>
          <span class="next-title">{nextTrack.title}</span>
          <span class="next-dur">{fmt(nextTrack.duration_sec * 1000)}</span>
        </div>
        <Waveform data={$waveformNext} position={0} introStart={nextIntroStart} introEnd={nextIntroEnd} height={20} />
      {/if}

      <div class="controls">
        <button class="ctrl" onclick={playPrev} title="Zurück">⏮</button>
        <button class="play" onclick={() => send({ type: $playerState.playing ? 'pause' : 'resume' })}>
          {$playerState.playing ? '⏸' : '▶'}
        </button>
        <button class="ctrl" onclick={() => { const n = _nextIdx(); if (n >= 0) send({ type: 'play_at', index: n }) }} title="Weiter">⏭</button>
        <span class="time">{fmt(posMs)}</span>
        <span class="time-sep">/</span>
        <span class="time">{fmt(durMs)}</span>

        <button class="ctrl {$appSettings.normalizeVolume ? 'active' : ''}"
                onclick={() => appSettings.update(s => ({...s, normalizeVolume: !s.normalizeVolume}))}
                title={$appSettings.normalizeVolume ? 'Normalisierung ein' : 'Normalisierung aus'}>≋</button>
        {#if $appSettings.normalizeVolume}
          <input class="lufs-sl" type="range" min="-23" max="-8" step="1"
                 value={$appSettings.targetLUFS}
                 oninput={(e) => appSettings.update(s => ({...s, targetLUFS: +e.target.value}))}
                 title="Ziel-LUFS" />
          <span class="lufs-val">{$appSettings.targetLUFS}L</span>
        {/if}
      </div>
    </div>

    <!-- Vertical volume fader -->
    <div class="right">
      <!-- Fader -->
      <div class="mixer-fader">
        <div class="fdr-marks">
          <span>∞</span><span>75</span><span>50</span><span>25</span><span>0</span>
        </div>
        <div class="fdr-slot">
          <div class="fdr-groove"></div>
          <input type="range" class="fdr-input" min="0" max="100" value={volume}
                 onmousedown={() => _volDragging = true}
                 onmouseup={() => { _volDragging = false }}
                 ontouchstart={() => _volDragging = true}
                 ontouchend={() => { _volDragging = false }}
                 oninput={(e) => setVolume(e.target.value)} />
        </div>
        <div class="fdr-info">
          <span class="fdr-val">{volume}</span>
          <span class="fdr-lbl">VOL</span>
        </div>
      </div>
    </div>
  </div>
</div>

<style>
  .player {
    display: flex; flex-direction: column;
    padding: 8px 16px 6px;
    background: var(--c-bg); flex-shrink: 0;
  }
  .player-row { display: flex; align-items: flex-start; gap: 16px; padding-bottom: 2px; }

  .art {
    width: 72px; height: 72px; border-radius: 4px;
    background: var(--c-bg3); flex-shrink: 0; align-self: flex-start; margin-top: 2px;
    display: flex; align-items: center; justify-content: center; overflow: hidden;
  }
  .art img { width: 100%; height: 100%; object-fit: cover; }
  .art-ph  { font-size: 24px; color: var(--c-tx7); }

  .center { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; padding-top: 2px; }
  .track-info { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
  .title {
    font-size: 14px; font-weight: 600; color: var(--c-tx1);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .meta { display: flex; gap: 10px; font-size: 11px; color: var(--c-tx5); flex-shrink: 0; }
  .meta span::before { content: '· '; }
  .bpm-pending { color: var(--c-tx7) !important; font-style: italic; }
  .lufs-warn { color: #c08030 !important; font-style: italic; }
  .controls { display: flex; align-items: center; gap: 6px; }
  .ctrl {
    background: none; border: none; color: var(--c-tx4); font-size: 14px;
    cursor: pointer; width: 28px; height: 28px; border-radius: 4px;
    transition: color .15s, background .15s; flex-shrink: 0;
  }
  .ctrl:hover  { color: var(--c-tx2); background: var(--c-br2); }
  .ctrl.active { color: var(--c-accent); background: var(--c-act-bg); }
  .play {
    width: 38px; height: 38px; border-radius: 50%;
    border: 2px solid var(--c-accent); background: none; color: var(--c-accent);
    font-size: 15px; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background .15s; flex-shrink: 0;
  }
  .play:hover { background: #e0780020; }
  .time     { font-size: 11px; color: var(--c-tx5); font-variant-numeric: tabular-nums; }
  .time-sep { color: var(--c-tx6); font-size: 11px; }

  .lufs-sl  { width: 56px; accent-color: var(--c-accent); height: 2px; cursor: pointer; flex-shrink: 0; }
  .lufs-val { font-size: 10px; color: var(--c-accent); min-width: 26px; flex-shrink: 0; }

  .right {
    display: flex; align-items: stretch; gap: 8px; flex-shrink: 0;
    padding: 2px 14px 2px 0;
    /* Match the center column height without stretching to full window */
    height: var(--player-h, 110px);
  }

  /* Mixer fader */
  .mixer-fader { display: flex; gap: 5px; align-items: stretch; }

  .fdr-marks {
    display: flex; flex-direction: column; justify-content: space-between;
    align-items: flex-end; flex: 1;
  }
  .fdr-marks span { font-size: 7px; color: var(--c-tx7); line-height: 1; }

  .fdr-slot {
    position: relative; width: 34px; flex: 1;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  .fdr-groove {
    position: absolute;
    width: 3px; top: 5px; bottom: 5px;
    background: linear-gradient(to bottom, #2a5070 0%, #0a1420 35%);
    border-radius: 2px;
    box-shadow: inset 0 1px 4px rgba(0,0,0,.9), 0 0 4px rgba(0,80,120,.3);
    pointer-events: none;
  }

  .fdr-input {
    -webkit-appearance: none;
    writing-mode: vertical-lr;
    direction: rtl;
    width: 34px; height: 100%;
    background: transparent;
    cursor: grab;
    position: relative; z-index: 1;
    padding: 0; margin: 0;
  }
  .fdr-input:active { cursor: grabbing; }
  .fdr-input::-webkit-slider-runnable-track {
    width: 3px;
    background: transparent;
    border-radius: 2px;
  }
  .fdr-input::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 32px; height: 10px;
    background: linear-gradient(to right,
      #c0d0e4 0%, #8aa0be 30%,
      #6080a0 47%, #101828 50%,
      #6080a0 53%, #8aa0be 70%,
      #c0d0e4 100%);
    border: 1px solid var(--c-tx4);
    border-radius: 2px;
    box-shadow: 0 2px 6px rgba(0,0,0,.8), inset 0 1px 0 rgba(255,255,255,.1);
    margin-left: -15px;
  }

  .fdr-info { display: flex; flex-direction: column; align-items: center; gap: 1px; }
  .fdr-val  { font-size: 10px; color: var(--c-tx5); font-variant-numeric: tabular-nums; }
  .fdr-lbl  { font-size: 8px; color: var(--c-tx7); text-transform: uppercase; letter-spacing: .1em; }

  input[type=range] { cursor: pointer; }

  .next-bar {
    display: flex; flex-direction: row; align-items: center;
    gap: 6px; margin-top: 1px;
  }
  .next-arrow { font-size: 10px; color: var(--c-accent); flex-shrink: 0; }
  .next-label { font-size: 9px; font-weight: 700; letter-spacing: .08em; color: var(--c-tx5); text-transform: uppercase; flex-shrink: 0; }
  .next-title { flex: 1; font-size: 11px; color: var(--c-tx4); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .next-dur   { font-size: 10px; color: var(--c-tx5); font-variant-numeric: tabular-nums; flex-shrink: 0; }
</style>
