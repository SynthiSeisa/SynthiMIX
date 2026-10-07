// Lautheit eines DJ-Sets beurteilen: welcher Titel ist gegenueber dem Rest
// (oder einem festen Zielwert) zu leise oder zu laut?

/** Mittlere Lautheit des Sets: der Median — ein einzelner Ausreisser zieht ihn nicht mit. */
export function setReference(tracks) {
  const v = tracks.map(t => t.lufs).filter(x => typeof x === 'number' && x > -90).sort((a, b) => a - b)
  if (!v.length) return null
  const m = v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2
  return Math.round(m * 2) / 2            // auf halbe dB: ein Zielwert, den man auch einstellen wuerde
}

/**
 * Jeden Titel einordnen. mode 'set': gegen den Median des Sets, 'fixed':
 * gegen target. tol: so viele dB Abweichung gelten noch als passend.
 * Liefert { ref, rows: [{...track, delta, state}], quiet, loud, ok, unknown }.
 */
export function analyze(tracks, { mode = 'set', target = -10, tol = 1.5 } = {}) {
  const ref = mode === 'fixed' ? target : setReference(tracks)
  const count = { quiet: 0, loud: 0, ok: 0, unknown: 0 }
  const rows = tracks.map(t => {
    const known = typeof t.lufs === 'number' && t.lufs > -90 && ref !== null
    const delta = known ? Math.round((t.lufs - ref) * 10) / 10 : null
    const state = !known ? 'unknown' : delta < -tol ? 'quiet' : delta > tol ? 'loud' : 'ok'
    count[state]++
    return { ...t, delta, state }
  })
  return { ref, rows, ...count }
}
