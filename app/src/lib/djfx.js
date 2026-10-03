// Uebergaenge wie am Mischpult: Kanal-Fader, 3-Band-EQ (Bass/Mitten/Hoehen),
// ein Filter und Effekt-Wege (Echo, Hall) je Deck. Hier nur die Verlaeufe —
// welche Regler wann wo stehen; die Audio-Kette steckt im Player.
//
// Zwei Zeitachsen:
// - Blend, Filter, langer EQ-Mix: t = Fortschritt des Uebergangs 0…1
// - Echo, Hall, Loop-Roll, Backspin, Double Drop: rel = Takte relativ zur
//   Grenze bzw. zum Drop im laufenden Titel (negativ = davor)
// Harte Momente (Schnitt auf der Eins, Drop, Bass-Tausch) setzt der Player
// sample-genau auf der Audio-Uhr; die Verlaeufe hier lassen diese Regler dann
// in Ruhe (siehe EVENT_PARAMS).
//
// Getestet in tests/djfx.test.mjs; gemessen an echten Titeln (Ausgang
// mitgeschnitten): ohne Loecher und Spruenge in der Lautheit, ohne Klicks,
// nie zwei Bassdrums zugleich.

export const KILL_DB = -30

/** 0…1: wie weit x zwischen a und b liegt */
export function ramp(x, a, b) { return b === a ? (x >= b ? 1 : 0) : Math.max(0, Math.min(1, (x - a) / (b - a))) }
/** gleiche Leistung: rein (0→1) und raus (1→0) */
export const up = (u) => Math.sin(Math.max(0, Math.min(1, u)) * Math.PI / 2)
export const down = (u) => Math.cos(Math.max(0, Math.min(1, u)) * Math.PI / 2)
/** Frequenz-Fahrt auf logarithmischer Skala */
export const sweep = (f0, f1, u) => f0 * Math.pow(f1 / f0, Math.max(0, Math.min(1, u)))

/** Arten, die auf einer Takt-Grenze schneiden (neuer Titel ab der Eins) */
export const CUT_TYPES = ['echo', 'hall', 'roll', 'backspin']
/** Vorlauf (Takte, neuer Titel laeuft stumm mit und rastet ein) und Ausklang nach der Grenze.
 *  6 Takte (8 s bei 175 BPM): Messung der hoerbaren Stelle (~4 s, lib/audiblepos.js)
 *  plus Nachziehen — mit 2 setzte der neue nach dem Schnitt oft neben dem Takt ein */
export const LEAD_BARS = 6
export const TAIL_BARS = { echo: 2, hall: 2.5, roll: 1, backspin: 1 }
/** Loop-Roll: so viele Takte vor der Grenze beginnt er */
export const ROLL_BARS = 2
export const EQMIX_BARS = 32

/** Spin-Laenge (Schlaege) beim Backspin: ein ganzer Takt (1,4 s bei 175 BPM) —
 *  mit 1-2 Schlaegen war er nur ein kurzes Zucken. Langsame Titel: halber Takt. */
export const spinBeats = (bpm) => (bpm >= 110 ? 4 : 2)
/** Hoechstes Tempo des Spins (Vielfaches, rueckwaerts) */
export const SPIN_TOP = 5

/**
 * Reglerstellungen. o/n: altes/neues Deck. Fader 0…1, EQ in dB (0 = neutral),
 * hp/lp: Filter in Hz (hp: Hochpass, lp: Tiefpass; fehlt = offen),
 * echo/hall: Sendepegel des alten Decks 0…1, echoHp: Tiefensperre im Echo.
 * s: { t, rel, pre, post } — siehe oben.
 */
export function mixState(type, s) {
  const { t = 0, rel = 0 } = s
  switch (type) {
    case 'blend': {
      // Neuer kommt in der ersten Haelfte, Hoehen erst gedaempft; der alte
      // gibt zuerst Hoehen, dann Mitten, dann Lautstaerke ab. Bass: Tausch in
      // der Mitte (Player). Beide nie gleichzeitig voll — sonst +3 dB.
      return {
        fo: down(ramp(t, 0.5, 1)), fn: 0.9 * up(ramp(t, 0, 0.45)) + 0.1 * ramp(t, 0.45, 0.7),
        hiN: -9 * (1 - ramp(t, 0, 0.5)), hiO: -9 * ramp(t, 0.5, 0.95), midO: -6 * ramp(t, 0.65, 1), midN: -3 * (1 - ramp(t, 0.2, 0.55)),
      }
    }
    case 'eqmix': {
      // Langer Mix ueber 32 Takte in vier Phrasen: neu leise und duenn dazu,
      // neu aufmachen, Bass-Tausch auf Takt 16 (Player), alt abgeben.
      const b = t * EQMIX_BARS
      return {
        fn: 0.75 * up(ramp(b, 0, 8)) + 0.25 * ramp(b, 8, 16),
        hiN: -12 + 6 * ramp(b, 0, 8) + 6 * ramp(b, 8, 16), midN: -6 * (1 - ramp(b, 8, 16)),
        hiO: -12 * ramp(b, 16, 24), midO: -4 * ramp(b, 16, 24) - 8 * ramp(b, 24, 32),
        fo: b < 24 ? 1 - 0.1 * ramp(b, 16, 24) : 0.9 * down(ramp(b, 24, 32)),
      }
    }
    case 'filter': {
      // Neu: Tiefpass oeffnet sich; alt: Hochpass nimmt ihm den Boden.
      return {
        fn: up(ramp(t, 0, 0.35)), fo: down(ramp(t, 0.6, 1)),
        lpN: sweep(300, 20000, ramp(t, 0, 0.65)), hpO: sweep(20, 1000, ramp(t, 0.3, 1)),
      }
    }
    case 'doubledrop': {
      const pre = s.pre || 16, post = s.post || 16
      if (rel < 0) {
        // Anlauf: neuer Titel duenn (ohne Bass, Hochpass) und leiser unter dem alten
        const u = (rel + pre) / pre
        return { fo: 1, fn: 0.7 * up(ramp(u, 0, 0.5)), hpN: sweep(400, 160, u), hiN: -6 + 3 * u, midN: -2, hiO: -3 * ramp(rel, -4, 0) }
      }
      // Drop: beide zusammen, Bass nur vom neuen. Der alte bleibt fast voll
      // (vorher -1,5 dB, Mitten -3 dB und ab der Haelfte raus: er war kaum zu
      // hoeren) und geht erst im letzten Drittel kurz und zuegig
      const out = ramp(rel, 0.7 * post, post)
      return { fn: 1, hpN: 20, hiN: 0, midN: 0, fo: 0.9 * down(out), midO: -4 * out, hiO: -2 - 8 * ramp(rel, 0.6 * post, post) }
    }
    case 'echo': {
      // Letzter Takt: Echo aufziehen, unten raus; auf der Eins alt weg (Player),
      // das Echo klingt ueber dem neuen aus und wird dabei duenner
      if (rel < 0) return { echo: ramp(rel, -1, 0), hpO: sweep(20, 300, ramp(rel, -1, 0)), echoHp: 200 }
      return { echo: rel < 0.0625 ? 1 : 0, echoHp: sweep(200, 1800, ramp(rel, 0, 2)) }
    }
    case 'hall': {
      // Letzte zwei Schlaege in den Hall schicken; der Hall waescht ueber den Einstieg
      if (rel < 0) return { hall: ramp(rel, -0.5, 0), hpO: sweep(20, 300, ramp(rel, -1, 0)) }
      return { hall: 1 - ramp(rel, 0.125, 0.375) }
    }
    case 'roll': {
      // Loop wird ueber zwei Takte immer kuerzer (Player), der Hochpass baut dabei
      // Spannung auf; am Ende Hall und ein Hauch Echo, damit der Schnitt ausklingt
      // Hall von Anfang an leicht dabei (weicher), zum Schluss deutlich
      if (rel < 0) return { hpO: sweep(20, 1200, ramp(rel, -ROLL_BARS, 0)),
                            hall: 0.3 * ramp(rel, -ROLL_BARS, -ROLL_BARS + 0.5) + 0.5 * ramp(rel, -1, 0),
                            echo: 0.4 * ramp(rel, -0.25, 0), echoHp: 400 }
      return { echo: 0, hall: 0, echoHp: 400 }
    }
    case 'backspin': {
      // Waehrend des Spins (Player) die Hoehen etwas zu — klingt nach Platte —
      // und Hall dazu: der auslaufende Spin klingt nach, statt abzureissen
      // (Hall gleich mit dem Anreissen, sonst klingt der Spin trocken und hart)
      if (rel < 0) return { lpO: sweep(20000, 5000, ramp(rel, -1, 0)), hall: 0.75 * ramp(rel, -1.05, -0.85) }
      return {}
    }
  }
  return {}
}

/**
 * Lautstaerke der Decks bei Schnitt-Uebergaengen (Echo, Hall, Roll, Backspin):
 * vor der Eins nur alt, danach nur neu — gesetzt vom Player auf der Audio-Uhr.
 * Weich genug gegen Klicks, kurz genug fuer einen Schnitt.
 */
export const CUT_FADE_OUT = 0.03          // s
export const CUT_FADE_IN = { echo: 0.04, roll: 0.03, backspin: 0.04, hall: 0.25 }

/** Verlauf der Spin-Geschwindigkeit beim Backspin (u 0…1): kurz anreissen, dann auslaufen */
export function spinSpeed(u, top = SPIN_TOP) {
  if (u <= 0) return 0
  if (u < 0.04) return top * (u / 0.04)
  // wie eine zurueckgerissene Platte: schnell los, dann laeuft sie aus (Tonhoehe faellt)
  return top * Math.exp(-3.3 * (u - 0.04) / 0.96)
}
