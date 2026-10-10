// Titel mit gehaltener Maustaste markieren (wie im Explorer): druecken und nach
// oben/unten ziehen markiert den Bereich dazwischen — auf einem Titel oder in
// der leeren Flaeche unter der Liste. Seitwaerts ziehen bewegt die Titel.

/** Zeile unter dem Mauszeiger, unbegrenzt (negativ = ueber, >= Anzahl = unter der Liste). */
export function rawIndexAt(clientY, top, rowH) {
  return rowH > 0 ? Math.floor((clientY - top) / rowH) : -1
}

/**
 * Pfade von Zeile a bis b (Reihenfolge egal; ausserhalb der Liste wird
 * abgeschnitten), zusaetzlich zu base (Strg: vorhandene Markierung bleibt).
 */
export function rangePaths(paths, a, b, base = null) {
  const s = new Set(base ?? [])
  const lo = Math.max(0, Math.min(a, b)), hi = Math.min(paths.length - 1, Math.max(a, b))
  for (let i = lo; i <= hi; i++) s.add(paths[i])
  return s
}

/**
 * Markieren oder Titel bewegen? Nur ein klar senkrechter Zug markiert. Schraeg
 * (zur Playlist links unten, zur Warteschlange rechts) bewegt die Titel — die
 * Ziele liegen immer seitlich, ein schraeger Zug darf nicht als Markieren enden.
 */
export function isSweepMove(dx, dy) {
  return Math.abs(dy) >= 2 * Math.abs(dx) && Math.abs(dy) > 0
}

/** Wie schnell am Rand mitrollen (Pixel je Schritt): ausserhalb der Liste schneller. */
export function edgeScroll(clientY, top, bottom, zone = 28, max = 26) {
  if (clientY < top + zone) return -Math.min(max, Math.ceil((top + zone - clientY) / 3))
  if (clientY > bottom - zone) return Math.min(max, Math.ceil((clientY - (bottom - zone)) / 3))
  return 0
}
