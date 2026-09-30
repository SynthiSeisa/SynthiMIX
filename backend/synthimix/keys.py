"""Tonarten (Camelot) und harmonisches Sortieren der Warteschlange."""
import random
import re

# ── Tonart ───────────────────────────────────────────────────────────────────
# Gespeichert wird immer in musikalischer Schreibweise mit ♯ ("F♯m", "C").
# Eingelesen wird alles, was in Tags vorkommt: rekordbox ("F♯m"), Mixed In Key
# (Camelot "4A"), Traktor (Open Key "1m"), Varianten mit #, b und "minor".
_KEY_NAMES = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B']
_KEY_FLATS = {'CB': 11, 'DB': 1, 'EB': 3, 'FB': 4, 'GB': 6, 'AB': 8, 'BB': 10}
# Camelot-Nummer -> Tonhoehenklasse (0 = C). A = Moll, B = Dur.
_CAMELOT_MINOR = {1: 8, 2: 3, 3: 10, 4: 5, 5: 0, 6: 7, 7: 2, 8: 9, 9: 4, 10: 11, 11: 6, 12: 1}
_CAMELOT_MAJOR = {1: 11, 2: 6, 3: 1, 4: 8, 5: 3, 6: 10, 7: 5, 8: 0, 9: 7, 10: 2, 11: 9, 12: 4}

def _parse_key(raw) -> str | None:
    """Beliebige Tonart-Angabe -> "F♯m" / "C", oder None wenn nicht lesbar."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    txt = str(raw).strip().replace('♯', '#').replace('♭', 'b')
    if not txt:
        return None
    m = re.fullmatch(r'0?(1[0-2]|[1-9])\s*([ABab])', txt)                  # Camelot
    if m:
        minor = m.group(2).upper() == 'A'
        pc = (_CAMELOT_MINOR if minor else _CAMELOT_MAJOR)[int(m.group(1))]
        return _KEY_NAMES[pc] + ('m' if minor else '')
    m = re.fullmatch(r'(1[0-2]|[1-9])\s*([dmDM])', txt)                     # Open Key
    if m:
        minor = m.group(2).lower() == 'm'
        camelot = (int(m.group(1)) + 6) % 12 + 1
        pc = (_CAMELOT_MINOR if minor else _CAMELOT_MAJOR)[camelot]
        return _KEY_NAMES[pc] + ('m' if minor else '')
    m = re.fullmatch(r'([A-Ga-g])\s*(#|b)?\s*(m|min|minor|moll|maj|major|dur)?', txt, re.I)
    if not m:
        return None
    note, acc = m.group(1).upper(), m.group(2) or ''
    if acc == 'b':
        pc = _KEY_FLATS.get(note + 'B')
        if pc is None:
            return None
    else:
        pc = _KEY_NAMES.index(note)
        if acc == '#':
            pc = (pc + 1) % 12
    suffix = (m.group(3) or '').lower()
    minor = suffix in ('m', 'min', 'minor', 'moll')
    return _KEY_NAMES[pc] + ('m' if minor else '')

def _key_to_camelot(key) -> tuple[int, str] | None:
    k = _parse_key(key)
    if not k:
        return None
    minor = k.endswith('m')
    pc = _KEY_NAMES.index(k[:-1] if minor else k)
    table = _CAMELOT_MINOR if minor else _CAMELOT_MAJOR
    num = next(n for n, v in table.items() if v == pc)
    return num, 'A' if minor else 'B'

def _key_compat(a, b) -> int:
    """3 = gleiche Tonart, 2 = passt (Parallel- oder Nachbartonart), 0 = passt nicht,
    -1 = mindestens eine Tonart unbekannt."""
    ca, cb = _key_to_camelot(a), _key_to_camelot(b)
    if not ca or not cb:
        return -1
    if ca == cb:
        return 3
    if ca[0] == cb[0]:
        return 2
    if ca[1] == cb[1] and (ca[0] - cb[0]) % 12 in (1, 11):
        return 2
    return 0

# ── Harmonisch sortieren (Warteschlange) ──────────────────────────────────────
def _energy(t: dict) -> float:
    """Energie fuer die Sortierung: der Energie-Level von Mixed In Key (1-10),
    sonst grob aus Tempo und Lautheit (schneller und lauter = mehr Energie).
    Beide Skalen liegen etwa im selben Bereich (-3 … +4)."""
    if t.get("energy"):
        return (int(t["energy"]) - 5) * 0.8
    bpm = float(t.get("bpm_f") or t.get("bpm") or 0) or 120.0
    lufs = float(t.get("lufs", -99) or -99)
    if lufs <= -90:
        lufs = -9.0
    return (bpm - 120.0) / 20.0 + (lufs + 9.0) / 3.0

def _tempo_gap(a: float, b: float) -> float:
    """Relativer Tempo-Abstand, halbes/doppeltes Tempo zaehlt als gleich."""
    if not a or not b:
        return -1.0
    return min(abs(a * m / b - 1.0) for m in (0.5, 1.0, 2.0))

def _key_step(a, b) -> str:
    """Art des Tonart-Schritts von a nach b im Camelot-Rad."""
    ca, cb = _key_to_camelot(a), _key_to_camelot(b)
    if not ca or not cb:
        return "unknown"
    if ca == cb:
        return "same"
    d = (cb[0] - ca[0]) % 12
    if ca[0] == cb[0]:
        return "relative"
    if ca[1] == cb[1]:
        if d == 1:
            return "up1"
        if d == 11:
            return "down1"
        if d == 2:
            return "up2"          # Energie-Schub: ein Ganzton hoeher
        if d == 7:
            return "up7"          # Energie-Schub: ein Halbton hoeher
    return "clash"

_KEY_SCORE = {"same": 3.0, "relative": 2.6, "up1": 2.7, "down1": 2.5,
              "up2": 1.2, "up7": 0.8, "unknown": 1.2, "clash": 0.0}
_BOOST_KEY_SCORE = {"up2": 3.6, "up7": 3.3}


def _pre(t: dict) -> tuple:
    """Einmal je Titel: Tonart im Camelot-Rad, Tempo, Energie."""
    return (_key_to_camelot(t.get("key")), float(t.get("bpm_f") or t.get("bpm") or 0), _energy(t))


def _camelot_step(ca, cb) -> str:
    """Wie _key_step, aber auf schon umgerechneten Camelot-Werten."""
    if not ca or not cb:
        return "unknown"
    if ca == cb:
        return "same"
    if ca[0] == cb[0]:
        return "relative"
    if ca[1] == cb[1]:
        return {1: "up1", 11: "down1", 2: "up2", 7: "up7"}.get((cb[0] - ca[0]) % 12, "clash")
    return "clash"


def _trans_score(pa: tuple, pc: tuple, boost_due: bool) -> tuple[float, str, float]:
    """Wie gut der Titel c (pc = _pre(c)) auf a folgt: Tonart, Tempo, Energie.
    Ist ein Energie-Schub faellig, zaehlen +2/+7 im Camelot-Rad und mehr Energie.
    Liefert (Wertung, Tonart-Schritt, Energie-Unterschied)."""
    (ka, ba, ea), (kc, bc, ec) = pa, pc
    step = _camelot_step(ka, kc)
    sc = (_BOOST_KEY_SCORE.get(step) if boost_due and step in _BOOST_KEY_SCORE
          else _KEY_SCORE[step])
    gap = _tempo_gap(ba, bc)
    sc -= 0.3 if gap < 0 else min(3.0, gap * 25)
    de = ec - ea
    if boost_due:
        sc += max(0.0, min(2.0, de))
    else:
        sc -= abs(de - 0.15) * 0.5
    return sc, step, de

def _harmonic_order(start: dict | None, tracks: list[dict], rng=None) -> tuple[list[dict], set[int]]:
    """Reihenfolge fuer einen harmonischen Mix mit gelegentlichen Energie-Schueben.

    Gierig vom laufenden Titel aus: jeweils der Titel, dessen Tonart am besten
    passt (Camelot gleich, +-1, Paralleltonart), dessen Tempo nah liegt und der
    die Energie haelt oder leicht hebt. Alle 4-6 Titel ist ein Schub faellig:
    dann zaehlen +2 / +7 im Camelot-Rad und deutlich mehr Energie besonders —
    so wie DJs zwischendurch "hochschalten".
    Liefert die neue Reihenfolge und die Positionen der Schuebe.
    """
    rng = rng or random.Random(len(tracks))
    rest = list(tracks)
    out: list[dict] = []
    boosts: set[int] = set()
    prev = start
    since = 0
    due_at = rng.randint(4, 6)
    # Je Titel einmal vorrechnen: Tonart im Camelot-Rad, Tempo, Energie
    pre: dict[int, tuple] = {id(x): _pre(x) for x in ([start] if start else []) + list(tracks)}

    def score(a, c, boost_due):
        return _trans_score(pre[id(a)], pre[id(c)], boost_due)

    while rest:
        boost_due = since >= due_at
        best, best_s, best_step, best_de = None, -1e9, "", 0.0
        # Ein Schritt voraus: nicht in eine Tonart laufen, aus der nichts mehr
        # passt (bei sehr langen Listen zu teuer, dann ohne)
        look = 1 < len(rest) <= 150
        for c in rest:
            if prev is None:
                sc, step, de = -abs(pre[id(c)][2]), "start", 0.0
            else:
                sc, step, de = score(prev, c, boost_due)
            if look:
                sc += 0.5 * max(score(c, d, False)[0] for d in rest if d is not c)
            if sc > best_s:
                best, best_s, best_step, best_de = c, sc, step, de
        rest.remove(best)
        out.append(best)
        since += 1
        if boost_due and (best_step in _BOOST_KEY_SCORE or best_de >= 0.6):
            boosts.add(len(out) - 1)
            since = 0
            due_at = rng.randint(4, 6)
        prev = best
    return out, boosts
