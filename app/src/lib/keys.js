// Tonart-Hilfen fuer die Oberflaeche. Das Backend liefert Tonarten bereits
// vereinheitlicht ("F♯m", "C"); hier geht es nur um Camelot-Position und
// Vertraeglichkeit. Muss zu _key_to_camelot / _key_compat in main.py passen.

const NAMES = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B']
// Tonhoehenklasse -> Camelot-Nummer
const MINOR = { 8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7, 9: 8, 4: 9, 11: 10, 6: 11, 1: 12 }
const MAJOR = { 11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7, 0: 8, 7: 9, 2: 10, 9: 11, 4: 12 }

/** "F♯m" -> { num: 11, letter: 'A' }, unbekannt -> null */
export function toCamelot(key) {
  if (!key) return null
  const minor = key.endsWith('m')
  const pc = NAMES.indexOf(minor ? key.slice(0, -1) : key)
  if (pc < 0) return null
  return { num: (minor ? MINOR : MAJOR)[pc], letter: minor ? 'A' : 'B' }
}

/** Sortierwert entlang des Quintenzirkels: benachbarte Tonarten liegen beieinander. */
export function keySortValue(key) {
  const c = toCamelot(key)
  return c ? c.num * 2 + (c.letter === 'B' ? 1 : 0) : 999
}

/**
 * Wie gut passen zwei Tonarten fuer einen Uebergang?
 * level: 'same' | 'good' | 'clash' | 'unknown'
 */
export function keyCompat(a, b) {
  const ca = toCamelot(a), cb = toCamelot(b)
  if (!ca || !cb) return { level: 'unknown', label: '' }
  if (ca.num === cb.num && ca.letter === cb.letter) return { level: 'same', label: 'gleiche Tonart' }
  if (ca.num === cb.num) return { level: 'good', label: 'Paralleltonart — passt' }
  const diff = ((ca.num - cb.num) % 12 + 12) % 12
  if (ca.letter === cb.letter && (diff === 1 || diff === 11)) return { level: 'good', label: 'benachbarte Tonart — passt' }
  return { level: 'clash', label: 'Tonarten passen nicht zusammen' }
}

/** Tooltip-Text fuer eine Tonart samt Herkunft. */
export function keyTitle(key, src) {
  if (!key) return ''
  const c = toCamelot(key)
  const cam = c ? ` · Camelot ${c.num}${c.letter}` : ''
  return src === 'analyse'
    ? `${key}${cam} — geschätzt, kann danebenliegen`
    : `${key}${cam}`
}
