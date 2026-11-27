"""Real values of technology: procedural upgrade rolls, supercharged slots, adjacency and class bonuses.

Own implementation of the rules the game applies (worked out from the game files and checked against the
values the game shows; see the project notes). All arithmetic is float32, like the game.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import math
import struct

from . import gamedata

_PACK = struct.Struct('<f')


def _r(x) -> float:
    """Round to the nearest float32 (the game's precision)."""
    return _PACK.unpack(_PACK.pack(x))[0]


class F(float):
    """float32 value: every arithmetic result is rounded to float32, like the game (no numpy needed)."""
    def __new__(cls, v=0.0):
        return float.__new__(cls, _r(float(v)))

    def __add__(self, o): return F(float(self) + float(o))
    __radd__ = __add__
    def __sub__(self, o): return F(float(self) - float(o))
    def __rsub__(self, o): return F(float(o) - float(self))
    def __mul__(self, o): return F(float(self) * float(o))
    __rmul__ = __mul__
    def __truediv__(self, o): return F(float(self) / float(o))
    def __rtruediv__(self, o): return F(float(o) / float(self))
    def __neg__(self): return F(-float(self))
A = 0x5A76F899
M32 = 0xFFFFFFFF
FREIGHTER = 'Freighter'
HIGH_GRADE_Q = (4, 5, 6)                 # Illegal, Sentinel, Robot


@lru_cache(maxsize=1)
def _meta() -> dict:
    p = Path(__file__).resolve().parent.parent / 'data' / 'statsmeta.json'
    return json.loads(p.read_text(encoding='utf-8'))['stats']


def parent(stat: str) -> str | None:
    return (_meta().get(stat) or [None, 0])[0]


def kind(stat: str) -> int:
    """0 additive (summed), 1 multiplier (product), 2 max, 3 multiplier variant."""
    return (_meta().get(stat) or [None, 0])[1]


def rules() -> dict:
    return (gamedata.load() or {}).get('techstats') or {}


# ---- procedural upgrades -------------------------------------------------------------------------------

def _hash(s: int) -> int:
    s &= M32
    h = (((s ^ 0x3D0000) >> 16) ^ s) * 9 & M32
    h = ((h >> 4) ^ h) * 0x1B873593 & M32
    return (h >> 15) ^ h


class _Rng:
    def __init__(self, h: int):
        self.lo = (h + (h == 0)) & M32
        self.hi = (((h >> 16) | (h << 16)) & M32) ^ h

    def step(self) -> int:
        x = self.lo * A + self.hi
        self.lo, self.hi = x & M32, x >> 32
        return self.lo

    def unit(self):
        return F(self.step() * 2.3283064370807974e-10)


def curve(t, k: int):
    t, one = F(t), F(1)
    if k == 0:
        return t
    if k == 16:
        return t * t
    if k == 17:
        return -((t - F(2)) * t)
    if k == 19:
        return t * (t * t) * t
    if k == 20:
        u = t - one
        return u * u * u * (one - t) + one
    if k == 25:
        return t if t == 0 else F(math.pow(2.0, (t - one) * F(10)))
    if k == 26:
        return t if t == 1 else one - F(math.pow(2.0, t * F(-10)))
    raise ValueError(f'curve {k}')


def _round(x) -> int:
    x = F(x)
    return int(F(x + (F(0.5) if x >= 0 else F(-0.5))))


def split_id(iid: str) -> tuple[str, int | None]:
    if gamedata.is_binary(iid):                       # compact binary IDs (items kept in storage)
        b = gamedata.resolve(iid) or ''
        s = iid.split('#', 1)[1] if '#' in iid else ''
        try:
            return b, int(s) if s else None
        except ValueError:
            return b, None
    iid = iid.lstrip('^')
    if '#' in iid:
        b, s = iid.split('#', 1)
        try:
            return b, int(s)
        except ValueError:
            return b, None
    return iid, None


def high_grade(iid: str) -> bool | None:
    """Illegal/Sentinel/Robot upgrades: True when the seed gives the high-grade roll at the base chance, False when
    it does not (the game can still give it with an extra in-game bonus), None for other upgrades."""
    base, seed = split_id(iid)
    it = gamedata.item(base)
    if not it or it.get('k') != 'proc' or seed is None or it.get('q') not in HIGH_GRADE_Q:
        return None
    r = _Rng(_hash(seed))
    if not it.get('biggs') and not _freighter(it):
        r.step(); r.step()
    return (r.step() * 100) >> 32 < int(rules().get('high_grade') or 0)


def _freighter(it: dict) -> bool:
    t = gamedata.item(it.get('template') or '')
    return bool(t and t.get('cat') == FREIGHTER)


def roll(iid: str, high: bool | None = None) -> list[tuple[str, float]] | None:
    """Stats of a procedural upgrade 'ID#seed' as the game rolls them. high: force the high-grade roll on/off for
    Illegal/Sentinel/Robot upgrades (default: what the seed gives at the base chance)."""
    base, seed = split_id(iid)
    it = gamedata.item(base)
    if not it or it.get('k') != 'proc' or seed is None or 'lv' not in it:
        return None
    curves = rules().get('curves') or [0] * 7
    r = _Rng(_hash(seed))
    if not it.get('biggs') and not _freighter(it):
        r.step(); r.step()
    perfect = False
    if it.get('q') in HIGH_GRADE_Q:
        hit = (r.step() * 100) >> 32 < int(rules().get('high_grade') or 0)
        perfect = hit if high is None else high
    v = curve(r.unit(), curves[it['wc']])          # always drawn, then replaced by the high-grade roll
    if perfect:
        v = F(1)
    nmin, nmax = it['n']
    n = _round((F(nmax) - F(nmin)) * v + F(nmin))
    always = [s for s in it['lv'] if s[4]]
    rest = [s for s in it['lv'] if not s[4]]
    for i in range(len(rest) - 1, 0, -1):
        j = (r.step() * (i + 1)) >> 32
        rest[i], rest[j] = rest[j], rest[i]
    chosen = (always + rest)[:min(n + len(always), len(always) + len(rest), 4)]
    out = []
    for stat, vmin, vmax, wc, _a in chosen:
        v = curve(r.unit(), curves[wc])
        if perfect:
            t = r.unit()
            v = t * F(0.14) + F(0.01) if wc > 4 else t * F(0.14999998) + F(0.85)
        out.append((stat, float((F(vmax) - F(vmin)) * v + F(vmin))))
    return out


def item_stats(iid: str, high: bool | None = None) -> tuple[str | None, list[tuple[str, float]]]:
    """(base stat, [(stat, value)]) of an installed technology."""
    base, seed = split_id(iid)
    it = gamedata.item(base)
    if not it:
        return None, []
    if it.get('k') == 'proc':
        t = gamedata.item(it.get('template') or '') or {}
        return t.get('stat'), (roll(iid, high) or []) if seed is not None else []
    if it.get('k') == 'tech':
        return it.get('stat'), [(s, float(v)) for s, v in it.get('bonus') or []]
    return None, []


# ---- grid totals ---------------------------------------------------------------------------------------

def _child(a: list, b: list) -> bool:
    """a has a stat whose parent stat is in b's list."""
    sb = {s for s, _v in b}
    return any(parent(s) in sb for s, _v in a)


def _same(a: list, b: list) -> bool:
    if _child(a, b) or _child(b, a):
        return False
    pa = {parent(s) for s, _v in a}
    return any(parent(s) in pa for s, _v in b)


def supercharged(v, k: int, f):
    v = F(v)
    if k in (0, 2):
        return v * f
    if k == 1:
        return v / f if v < 1 else v * f
    if k == 3:
        return v / f if v < 1 else v + (v - F(1)) * f
    return v


class Grid:
    """Technology of one inventory: {(x, y): item id}, supercharged cells and damaged cells."""

    def __init__(self, cells: dict, special: set | None = None, damaged: set | None = None, high: dict | None = None):
        self.cells = dict(cells)
        self.special = set(special or ())
        self.damaged = set(damaged or ())
        self._info = {xy: item_stats(i, (high or {}).get(i)) for xy, i in self.cells.items()}

    def moved(self, cells: dict) -> 'Grid':
        g = Grid.__new__(Grid)
        g.cells, g.special, g.damaged = dict(cells), self.special, set(self.damaged)   # damaged pieces never move
        by_id = {}
        for xy, i in self.cells.items():
            by_id.setdefault(i, self._info[xy])
        g._info = {xy: by_id[i] for xy, i in cells.items()}
        return g

    def neighbours(self, xy) -> tuple[int, int]:
        """(same, child) adjacent technologies of the piece at xy."""
        base, stats = self._info[xy]
        same = child = 0
        if xy in self.damaged or not base:
            return 0, 0
        x, y = xy
        for q in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            o = self._info.get(q)
            if not o or q in self.damaged or o[0] != base:
                continue
            if _child(stats, o[1]):
                child += 1
            elif _same(stats, o[1]):
                same += 1
        return same, child

    def piece(self, xy, stat: str, adjacency: bool = True):
        """Value of one stat of the piece at xy: supercharged and (optionally) with its adjacency bonus."""
        base, stats = self._info[xy]
        r = rules().get('adj') or {}
        k = kind(stat)
        out = None
        for s, v in stats:
            if s != stat:
                continue
            v = F(v)
            if xy in self.special:
                v = supercharged(v, k, F(r.get('supercharge', 1.25)))
            if adjacency:
                same, child = self.neighbours(xy)
                if k == 0 and v > 0:
                    v = v * F(r.get('same_add', 0.06)) * F(same) + v + v * F(r.get('child_add', 0.05)) * F(child)
                elif k in (1, 3) and v > 1:
                    v = v * F(r.get('same_mul', 0.04)) * F(same) + v + v * F(r.get('child_mul', 0.03)) * F(child)
            out = v
        return out

    def is_upgrade(self, xy) -> bool:
        it = gamedata.item(split_id(self.cells[xy])[0]) or {}
        return it.get('k') == 'proc' or bool(it.get('upgrade'))

    def total(self, stat: str, base: str | None = None, base_adjacency: bool = True):
        """Sum (or product / max) of one stat over the grid; base limits it to one technology family.
        base_adjacency=False: the base technology (not an upgrade) gets no adjacency bonus, as in the ship
        screen's Damage Potential (checked in game with four ships)."""
        k = kind(stat)
        tot = F(1) if k in (1, 3) else F(0)
        for xy, (b, _s) in self._info.items():
            if base is not None and b != base or xy in self.damaged:
                continue
            v = self.piece(xy, stat, base_adjacency or self.is_upgrade(xy))
            if v is None:
                continue
            tot = tot + v if k == 0 else tot * v if k in (1, 3) else max(tot, v)
        return tot


def with_class(total, stat: str, base_values: dict) -> float:
    """Apply the class bonuses stored in the ship / tool (BaseStatValues: {'SHIP_DAMAGE': 79.6, ...})."""
    t = F(total)
    bs = rules().get('base_stats') or {}
    for bid, val in (base_values or {}).items():
        for s, less in bs.get(bid.lstrip('^'), []):
            if s == stat:
                d = F(val) * t * F(0.01)
                t = t - d if less else t + d
    return float(t)


def ship_display(grid: Grid, base_values: dict) -> dict:
    """Values the ship screen shows (the ones checked against the game)."""
    out = {}
    jd = grid.total('Ship_Hyperdrive_JumpDistance')
    if jd:
        out['hyperdrive'] = with_class(jd, 'Ship_Hyperdrive_JumpDistance', base_values) + 1.0
    sh = grid.total('Ship_Armour_Shield_Strength')
    if sh > 0:
        s = F((base_values or {}).get('^SHIP_SHIELD', (base_values or {}).get('SHIP_SHIELD', 0))) / F(85) + F(1.65)
        out['shield'] = float((s * (F(2) - F(0.65) / F(sh))) * F(100))
    return out


# ---- how the game shows a value in the technology tooltip ----------------------------------------------

def _index() -> dict:
    return {s: i for i, s in enumerate(_meta())}


def _bits(base: int, mask: int, n: int) -> set:
    return {base + i for i in range(n) if mask >> i & 1}


_ENABLED = _bits(0x37, 0x18e0000400025a21, 0x3d) | _bits(0, 0x2c00000080200, 0x32) | _bits(0x97, 0x218000000040c09, 0x3a)
_LIGHTYEARS = {0x95, 0xab}
_COUNT = {0xa4, 0xa5, 0x70}
_ONE_DECIMAL = {0x10, 0x18, 0x19}
_FLAT = set(range(0x52, 0x57)) | {0x58}
_NEGATED = {3, 0x15, 0x69, 0x7b, 0x7e, 199} | _bits(0x9c, 0x44000200001, 0x2b)


@lru_cache(maxsize=None)
def reference(family: str, stat: str) -> float | None:
    """Value the game compares an additive stat against: the same stat in the first base technology of the family."""
    for iid, it in (gamedata.load() or {}).get('items', {}).items():
        if it.get('k') == 'tech' and not it.get('upgrade') and it.get('stat') == family:
            for s, v in it.get('bonus') or []:
                if s == stat and v > 0:
                    return float(v)
    return None


def _thousands(n: int) -> str:
    return f'{n:,}'


def display(stat: str, v: float, family: str | None) -> tuple[str, str] | None:
    """(kind, text) as the tooltip shows it: kind 'pct' (+29 %), 'ly' (250), 'num' or 'on'. None: not shown."""
    i = _index().get(stat)
    if i is None:
        return None
    v = F(v)
    if i in _ENABLED:
        return ('on', '')
    if i in _LIGHTYEARS:
        return ('ly', _thousands(int(round(float(v)))))
    if i in _COUNT:
        return ('num', '+' + _thousands(int(round(float(v)))))
    if i in _ONE_DECIMAL:
        return ('num', ('+' if v > 0 else '') + f'{float(v):.1f}')
    if i in _FLAT:
        return ('num', '+' + _thousands(int(round(float(v)))))
    k = kind(stat)
    if k in (0, 2):
        ref = reference(family or '', stat)
        if not ref:
            return None
        if k == 2:
            return ('num', _thousands(int(round(float(v)))))
        p = max(1, int(F(v / F(ref)) * F(100)))
        return ('pct', '+' + _thousands(p))
    p = (v - F(1)) * F(100)
    if 0 < p < 1:
        p = F(1)
    elif -1 < p < 0:
        p = F(-1)
    n = math.floor(p) if p < 0 else math.ceil(p)
    if i in _NEGATED:
        n = -n
    return ('pct', ('+' if n > 0 else '') + _thousands(n))


def tooltip(grid: Grid, xy) -> list[list]:
    """[[stat, kind, text]] of the piece at xy, with supercharge and without adjacency, like the game's tooltip."""
    base, stats = grid._info.get(xy, (None, []))
    it = gamedata.item(split_id(grid.cells.get(xy, ''))[0]) or {}
    fixed = {r[0] for r in it.get('ranges') or [] if r[1] == r[2] == 1}     # placeholders the game does not list
    out = []
    seen = set()
    for s, _v in stats:
        if s in seen or s in fixed:
            continue
        seen.add(s)
        v = grid.piece(xy, s, adjacency=False)
        d = display(s, v, base) if v is not None else None
        if d:
            out.append([s, d[0], d[1]])
    return out


def family_totals(grid: Grid, base_values: dict | None = None) -> list[dict]:
    """Sum of the bonuses of each technology family: everything installed together (upgrades, adjacency,
    supercharged slots) over what the base technology gives alone, and the same with the class bonus.
    [{'family', 'stats': [[stat, kind, text, text_with_class]]}], in grid order."""
    fams: dict[str, dict] = {}
    for xy in sorted(grid.cells, key=lambda p: (p[1], p[0])):
        fam, stats = grid._info[xy]
        if not fam or xy in grid.damaged:
            continue
        it = gamedata.item(split_id(grid.cells[xy])[0]) or {}
        fixed = {r[0] for r in it.get('ranges') or [] if r[1] == r[2] == 1}
        f = fams.setdefault(fam, {'stats': [], 'base': {}, 'tech': None})
        if f['tech'] is None or not grid.is_upgrade(xy) and grid.is_upgrade(f['tech_xy']):
            f['tech'], f['tech_xy'] = grid.cells[xy], xy
        for s, v in stats:
            if s in fixed:
                continue
            if s not in f['stats']:
                f['stats'].append(s)
            if not grid.is_upgrade(xy):
                k = kind(s)
                prev = f['base'].get(s)
                f['base'][s] = F(v) if prev is None else (prev + F(v) if k == 0 else prev * F(v) if k in (1, 3)
                                                          else max(prev, F(v)))
    out = []
    for fam, f in fams.items():
        rows = []
        for s in sorted(f['stats'], key=lambda s: _index().get(s, 999)):
            i, k = _index().get(s), kind(s)
            if s == fam or k in (1, 3) and f['base'].get(s) == 0:
                continue                                        # the family's own flag; a base of 0 has no ratio
            tot = grid.total(s, fam)
            fin = F(with_class(tot, s, base_values or {}))
            if s == 'Ship_Hyperdrive_JumpDistance':
                fin = fin + F(1)                                # as the ship screen shows it
            base = f['base'].get(s)

            def shown(t):
                if i in _LIGHTYEARS or i in _COUNT or i in _FLAT or i in _ONE_DECIMAL or k == 2:
                    return display(s, t, fam)
                if k == 0:
                    return display(s, t - (base or F(0)), fam) if t - (base or F(0)) > 0 else None
                return display(s, t / base if base else t, fam)
            a, b = shown(tot), shown(fin)
            if a and a[0] != 'on' and a[1] not in ('+0', '0'):
                rows.append([s, a[0], a[1], b[1] if b and b[1] != a[1] else None])
        if rows:
            out.append({'family': fam, 'tech': f['tech'], 'stats': rows})
    return out


def bonus_totals(grid: Grid) -> list[list]:
    """[[family, [[stat, kind, text], ...], base tech id], ...]: what all the installed technology adds up to, per family and
    stat, with adjacency and supercharged slots (class bonus not included), written like the tooltip: the sum of
    the upgrades plus what the base technology gains from its slot and neighbours. Only the stats the installed
    upgrades work on."""
    fams: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for xy, (fam, stats) in grid._info.items():
        if not fam or xy in grid.damaged:
            continue
        if not grid.is_upgrade(xy):
            names.setdefault(fam, split_id(grid.cells[xy])[0])
        lst = fams.setdefault(fam, [])
        if not grid.is_upgrade(xy):              # only what the upgrades work on (not size, splash radius...)
            continue
        for st, _v in stats:
            if st not in lst and st != fam:
                lst.append(st)
    out = []
    for fam, stats in fams.items():
        rows = []
        for st in stats:
            k = kind(st)
            base = [F(v) for xy, (f, ss) in grid._info.items() if f == fam and xy not in grid.damaged
                    and not grid.is_upgrade(xy) for s2, v in ss if s2 == st]
            tot = grid.total(st, fam)
            if k == 0:
                v = tot - sum(base, F(0))
                if not v > 0:
                    continue
            elif k in (1, 3):
                b = F(1)
                for x in base:
                    b = b * x
                if not b:
                    continue
                v = tot / b
                if abs(float(v) - 1) < 1e-6:
                    continue
            else:
                continue
            d = display(st, v, fam)
            if d and d[0] != 'on':
                rows.append([st, d[0], d[1]])
        if rows:
            if fam not in names:                       # no base piece installed: name the family by its base tech
                names[fam] = next((iid for iid, it in ((gamedata.load() or {}).get('items') or {}).items()
                                   if it.get('k') == 'tech' and not it.get('upgrade') and it.get('stat') == fam), None)
            out.append([fam, rows, names.get(fam)])
    return out


# ---- ship screen: Manoeuvrability and Damage Potential (game code 0x1406bd840 / 0x1417effa0) -------------

def _seed_units(seed: int | None) -> tuple:
    """The two numbers the ship screen draws from the ship's seed (MWC, seeded without hashing)."""
    if seed is None:
        lo, hi = 1, 0
    else:
        s_lo, s_hi = seed & M32, (seed >> 32) & M32
        lo, hi = (s_lo + (s_lo == 0)) & M32, (((s_lo >> 16) | (s_lo << 16)) & M32) ^ s_hi ^ s_lo
    x = lo * A + hi
    u1 = F((x & M32) * 2.3283064370807974e-10)
    u2 = F((((x & M32) * A + (x >> 32)) & M32) * 2.3283064370807974e-10)
    return u1, u2


def _final(grid: Grid, stat: str, base_values: dict, base: str | None = None, base_adjacency: bool = True):
    return F(with_class(grid.total(stat, base, base_adjacency), stat, base_values))


def _clamp01(v):
    return F(min(F(1), max(F(0), F(v))))


def manoeuvrability(grid: Grid, base_values: dict, ship_class: str, inv_class: str, seed: int | None):
    sh = rules().get('ship')
    if not sh:
        return None
    turn, damp = sh['controls'][sh['control_by_class'].get(ship_class, 'Control')]
    ts_max, ts_min, bd_max, bd_min = sh['class_bonus'].get(inv_class, sh['class_bonus']['C'])
    base = F({'C': 5, 'B': 10, 'A': 15, 'S': 20}.get(inv_class, 5))
    u1, u2 = _seed_units(seed)
    t = (u2 * (F(ts_max) - F(ts_min)) + F(ts_min) + F(turn)) * F(15) + \
        (u1 * (F(bd_max) - F(bd_min)) + F(bd_min) + F(damp)) * F(35)
    m = _final(grid, 'Ship_Maneuverability', base_values)
    b = _final(grid, 'Ship_BoostManeuverability', base_values)
    return float(((m - F(1)) * F(50) + t + (b - F(1)) * F(9)) * F(10) + base)


NORMS = (1500.0, 4000.0, 18000.0, 10000.0, 8500.0, 5000.0, 2000.0)


def weapon_dps(grid: Grid, base_values: dict, w: dict):
    """Damage per second the game credits one ship weapon with (0 when it is not installed)."""
    sh = rules()['ship']
    fam = w['stat']
    if not w['projectile']:                                 # beam: phase beam (and the unused missile slot)
        d = _final(grid, 'Ship_Weapons_Lasers_Damage', base_values, fam, False)
        dmg = F(max(1, int(d)) * sh['laser'][0])
        heat = _final(grid, 'Ship_Weapons_Lasers_HeatTime', base_values, fam, False)
        if heat + F(sh['laser_downtime']) == 0:
            return F(0)
        v = heat / (heat + F(sh['laser_downtime'])) * (F(1) / F(sh['laser'][1])) * dmg
        # The game counts the phase beam even when it is not installed (damage at least 1, heat time 1).
        if fam != 'Ship_Weapons_Lasers' and not grid.total('Ship_Weapons_Lasers_Damage', fam) > 0:
            return F(0)
        return v + v
    dmg = _final(grid, 'Ship_Weapons_Guns_Damage', base_values, fam, False)
    rate = _final(grid, 'Ship_Weapons_Guns_Rate', base_values, fam, False)
    shots = _final(grid, 'Ship_Weapons_Guns_BulletsPerShot', base_values, fam, False)
    heat = _final(grid, 'Ship_Weapons_Guns_HeatTime', base_values, fam, False)
    rng = _final(grid, 'Ship_Weapons_Guns_Range', base_values, fam, False)
    if not rng > 0:
        rng = F(F(w['speed_life'][0]) * F(w['speed_life'][1])) if w.get('speed_life') else F(sh['default_range'])
    reach = _clamp01(rng / F(sh['ref_range']))
    v = F(int(shots)) * rate * dmg
    if w['name'] == 'Rocket':
        return v * reach / F(w['cool'])
    if heat + F(w['cool']) == 0:
        return F(0)
    return heat / (F(w['cool']) + heat) * v * reach


def damage_potential(grid: Grid, base_values: dict, seed: int | None):
    sh = rules().get('ship')
    if not sh:
        return None
    u1, _u2 = _seed_units(seed)
    sd = F((base_values or {}).get('^SHIP_DAMAGE', (base_values or {}).get('SHIP_DAMAGE', 0)))
    tot = F(0)
    for w, norm in zip(sh['weapons'], NORMS):
        tot = tot + _clamp01(weapon_dps(grid, base_values, w) / F(norm)) * F(50)
    return float(tot + sd / F(60) * F(215) + u1 * F(4) + F(33))
