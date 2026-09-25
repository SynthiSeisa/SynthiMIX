<script>
  import { get } from 'svelte/store'
  import { untrack, onMount } from 'svelte'
  import { keyCompat } from '../lib/keys.js'
  import KeyChip from './KeyChip.svelte'
  import { library, playerState, nowPlaying, queue, waveform, waveformNext, settings, playMode, send, autoMixEnabled, appSettings, introSkipPaths, skipNextCrossfade, livePositionMs } from '../stores/ws.js'
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
  let cfRafActive  = $state(false)
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
      if (el && !cfActive && !cfRafActive) el.volume = volume / 100
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
    waitForNext = false

    if (wasPlaying && cf > 0 && !forceImmediate) {
      cfCancelled = false
      cfRafActive = true
      a.src = url; a.dataset.path = track.path; a.volume = 0; a.load(); a.play().catch(() => {})
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
        else { cfRaf = null; cfRafActive = false; _silenceAndStop(c) }
      }
      cfRaf = requestAnimationFrame(rafTick)
    } else {
      // Silence the alt deck immediately in case a crossfade was mid-flight
      const oldAlt = untrack(alt)
      if (oldAlt) _silenceAndStop(oldAlt)
      const el = untrack(cur)
      if (el) {
        el.src = url; el.dataset.path = track.path; el.volume = v; el.load(); setElLufs(el, track.lufs ?? -99)
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
    return _computeNext(get(queue), get(playerState).current_idx, get(playMode), shufflePickPath)
  }

  // ── Auto-crossfade ─────────────────────────────────────────────────────────
  function _checkCrossfade(pos) {
    if (cfActive || cfS <= 0 || durMs <= 0) return

    const trigFrac = _outroTrigger()
    let triggerMs  = trigFrac >= 0 ? trigFrac * durMs : durMs - cfS * 1000

    // Beat-align: snap crossfade trigger to nearest beat (±1 beat tolerance)
    const bpm = $nowPlaying?.bpm
    if (bpm > 30 && bpm < 300 && get(appSettings).beatAlignCf) {
      const beatMs  = 60000 / bpm
      const snapped = Math.round(triggerMs / beatMs) * beatMs
      // Only apply if snap is within ±1 beat from base trigger
      if (Math.abs(snapped - triggerMs) <= beatMs) {
        // Don't go so late that there's no room for the crossfade
        triggerMs = Math.min(snapped, durMs - cfS * 1000 - 200)
      }
    }

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
    inactive.dataset.path = nextTrk.path
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
      cfCancelled = true; cfActive = false; cfNextIdx = -1
      const c = cur(); if (c) c.volume = volume / 100
      _silenceAndStop(el)
      return
    }
    if (el !== cur()) return

    if (cfRaf !== null) {
      // Manueller Mix in die kaputte Datei: der alte Titel spielt noch und
      // bekommt die volle Lautstaerke zurueck, dann weiter zum naechsten.
      cancelAnimationFrame(cfRaf); cfRaf = null; cfRafActive = false
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
    const el = cur(); if (el) el.volume = volume / 100
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

  let centerH = $state(110)
  let deckH   = $state(0)
</script>

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
        <button class="btn btn-icon" onclick={playPrev} title="Zurück" aria-label="Zurück"><i class="ti ti-player-track-prev"></i></button>
        <button class="play" onclick={() => send({ type: $playerState.playing ? 'pause' : 'resume' })}
                title={$playerState.playing ? 'Pause' : 'Abspielen'} aria-label={$playerState.playing ? 'Pause' : 'Abspielen'}>
          <i class="ti {$playerState.playing ? 'ti-player-pause-filled' : 'ti-player-play-filled'}"></i>
        </button>
        <button class="btn btn-icon" onclick={() => { const n = _nextIdx(); if (n >= 0) send({ type: 'play_at', index: n }) }}
                title="Weiter" aria-label="Weiter"><i class="ti ti-player-track-next"></i></button>
      </div>
    </div>

    <div class="center" bind:clientHeight={centerH}>
      <div class="track-info">
        <span class="title">{$nowPlaying?.title ?? '—'}</span>
        <span class="meta">
          {#if $nowPlaying?.artist}<span class="artist">{$nowPlaying.artist}</span>{/if}
          {#if curKey}<KeyChip key={curKey.key} src={curKey.key_src} />{/if}
          {#if $nowPlaying?.bpm}<span class="m-num">{$nowPlaying.bpm} BPM</span>
          {:else if $nowPlaying}<span class="bpm-pending">BPM wird gemessen…</span>{/if}
          <!-- Normalisierung wird in den Einstellungen geschaltet; hier nur der Stand -->
          {#if $nowPlaying?.lufs && $nowPlaying.lufs > -90}
            {#if $appSettings.normalizeVolume}
              <span class="m-num norm-on" title="Lautstärke-Angleichung an: {$nowPlaying.lufs.toFixed(1)} LUFS wird auf {$appSettings.targetLUFS} LUFS gebracht (Einstellungen → Wiedergabe)"><b>≋</b> {$nowPlaying.lufs.toFixed(1)} → {$appSettings.targetLUFS} LUFS</span>
            {:else}
              <span class="m-num" title="Lautstärke-Angleichung aus (Einstellungen → Wiedergabe)">{$nowPlaying.lufs.toFixed(1)} LUFS</span>
            {/if}
          {:else if $appSettings.normalizeVolume && $nowPlaying}
            <span class="lufs-warn" title="Keine Lautstärkemessung — Normalisierung nicht aktiv für diesen Track">kein LUFS-Wert</span>
          {/if}
          {#if $nowPlaying?.play_count}<span class="m-num" title="So oft gespielt">×{$nowPlaying.play_count}</span>{/if}
          {#if playerNotice}<span class="lufs-warn" role="status"><i class="ti ti-alert-triangle"></i> {playerNotice}</span>{/if}
          <span class="time"><b>{fmt(posMs)}</b> / {fmt(durMs)}</span>
        </span>
      </div>

      <Waveform data={$waveform} position={pos} onclick={seek}
                outroStart={outroBarStart} outroEnd={outroBarEnd}
                loading={$nowPlaying !== null && $waveform.length === 0} />

      {#if nextTrack}
        <div class="next-bar">
          <span class="eyebrow next-label">Nächster</span>
          <span class="next-title">{nextTrack.title}{nextTrack.artist ? ' · ' + nextTrack.artist : ''}</span>
          {#if nextKey}
            <KeyChip key={nextKey.key} src={nextKey.key_src} compat={uebergang.level === 'unknown' ? null : uebergang} hint="Übergang: " />
          {/if}
          <span class="next-dur">{fmt(nextTrack.duration_sec * 1000)}</span>
        </div>
        <Waveform data={$waveformNext} position={0} introStart={nextIntroStart} introEnd={nextIntroEnd} height={20} />
      {/if}
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
  .player { display: flex; flex-direction: column; padding: var(--sp-3) var(--sp-4) var(--sp-2); background: var(--c-bg); flex-shrink: 0; }
  .player-row { display: flex; align-items: flex-start; gap: var(--sp-4); }

  .deck { display: flex; flex-direction: column; align-items: center; gap: 6px; flex-shrink: 0; }
  .art {
    width: 88px; height: 88px; border-radius: var(--r-m); flex-shrink: 0;
    background: var(--c-bg5); border: 1px solid var(--c-br1);
    display: flex; align-items: center; justify-content: center; overflow: hidden;
  }
  .art img { width: 100%; height: 100%; object-fit: cover; }
  .art-ph { font-size: 32px; color: var(--c-tx6); }

  .center { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .track-info { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .title {
    font-size: var(--fs-xl); font-weight: 700; color: var(--c-tx1); line-height: 1.2;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .meta { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; font-size: var(--fs-body); color: var(--c-tx3); }
  .artist { color: var(--c-tx2); font-weight: 600; }
  .m-num { font-variant-numeric: tabular-nums; }
  .bpm-pending { color: var(--c-tx5); font-style: italic; }
  .lufs-warn { color: var(--c-warn-tx); display: inline-flex; align-items: center; gap: 4px; }

  .next-bar { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
  .next-label { flex-shrink: 0; }
  .next-title { flex: 1; min-width: 0; font-size: var(--fs-body); color: var(--c-tx2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .next-dur { flex-shrink: 0; font-size: var(--fs-sm); color: var(--c-tx3); font-variant-numeric: tabular-nums; }

  .controls { display: flex; align-items: center; gap: var(--sp-1); }
  .norm-on b { color: var(--c-accent-tx); font-weight: 700; }
  .play {
    width: 48px; height: 48px; border-radius: 50%; flex-shrink: 0;
    border: none; background: var(--c-accent); color: var(--c-on-accent);
    font-size: 22px; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background .12s, transform .08s;
  }
  .play:hover { background: var(--c-accent2); }
  .play:active { transform: scale(.96); }
  :global([data-density="comfortable"]) .play { width: 56px; height: 56px; font-size: 26px; }
  .time { margin-left: auto; font-size: var(--fs-body); color: var(--c-tx3); font-variant-numeric: tabular-nums; white-space: nowrap; }
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
