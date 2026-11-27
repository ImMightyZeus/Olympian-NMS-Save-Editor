"""Auto-sort a technology grid: supercharged cells for the chosen priorities, then the best adjacency.

Only moves pieces inside the same grid (nothing is created or removed). The score uses the game's own rules
(techstats): supercharge, adjacency and how each stat adds up.
"""
from __future__ import annotations

import math

from . import techstats as T

PRIORITIES = {
    'hyperdrive': ['Ship_Hyperdrive'],
    'shield': ['Ship_Armour_Shield'],
    'pulse': ['Ship_PulseDrive', 'Ship_Maneuverability'],
    'damage': None,                       # the weapon that gains most (recommended to the user)
}
WEAPON_PREFIX = 'Ship_Weapons'
W_PRIORITY = 1000.0


def families(grid: T.Grid) -> dict[str, list]:
    out: dict[str, list] = {}
    for xy, (base, stats) in grid._info.items():
        if base and stats:
            out.setdefault(base, []).append(xy)
    return out


def _stats_of(grid: T.Grid, base: str) -> list[str]:
    seen = []
    for b, stats in grid._info.values():
        if b == base:
            for s, _v in stats:
                if s != base and s not in seen:          # the root stat (e.g. Ship_Hyperdrive) is only a marker
                    seen.append(s)
    return seen


def family_score(grid: T.Grid, base: str, stats: list[str]) -> float:
    """Log of every stat of a family, so each one counts by its relative change; for stats where less is
    better (multipliers below 1) the sign is reversed."""
    sc = 0.0
    for s in stats:
        t = float(grid.total(s, base))
        if t <= 0:
            continue
        k = T.kind(s)
        sc += math.log(t) if (k == 0 or t >= 1) else -math.log(t)
    return sc


def weapon_dps(grid: T.Grid, base: str) -> float:
    """Rough damage per second of one weapon: damage x fire rate (only to rank weapons)."""
    dmg = [s for s in _stats_of(grid, base) if s.endswith('_Damage')]
    rate = [s for s in _stats_of(grid, base) if s.endswith('_Rate')]
    d = float(grid.total(dmg[0], base)) if dmg else 0.0
    r = float(grid.total(rate[0], base)) if rate else 1.0
    return d * r


class Problem:
    def __init__(self, grid: T.Grid, valid: set, fixed: set, weights: dict[str, float]):
        self.grid, self.valid, self.fixed, self.weights = grid, set(valid), set(fixed), weights
        self.stats = {b: _stats_of(grid, b) for b in weights}

    def score(self, g: T.Grid) -> float:
        # a tiny cost per moved piece: pieces that gain nothing stay where they were
        moved = sum(1 for xy, i in g.cells.items() if self.grid.cells.get(xy) != i)
        return sum(w * family_score(g, b, self.stats[b]) for b, w in self.weights.items() if w) - 1e-4 * moved

    def solve(self, passes: int = 12) -> tuple[T.Grid, float]:
        """Swap pairs of cells while the score improves. Each trial swaps in place and rescores only the
        families of the two pieces (adjacency only counts pieces of the same family), then undoes the swap."""
        g = self.grid.moved(dict(self.grid.cells))
        cells, info = g.cells, g._info
        orig = self.grid.cells
        fs = {b: family_score(g, b, self.stats[b]) for b, w in self.weights.items() if w}
        moved = 0
        best = sum(w * fs[b] for b, w in self.weights.items() if w) - 1e-4 * moved
        movable = sorted(c for c in self.valid if c not in self.fixed)

        def put(xy, iid, inf):
            if iid is None:
                cells.pop(xy, None); info.pop(xy, None)
            else:
                cells[xy], info[xy] = iid, inf

        for _ in range(passes):
            improved = False
            for i, a in enumerate(movable):
                for b in movable[i + 1:]:
                    ia, ib = cells.get(a), cells.get(b)
                    if ia == ib:
                        continue
                    na, nb = info.get(a), info.get(b)
                    fams = {x[0] for x in (na, nb) if x and x[0] in fs}
                    # moved pieces: occupied cells that hold something other than at the start
                    dm = sum((new is not None and orig.get(xy) != new) - (old is not None and orig.get(xy) != old)
                             for xy, old, new in ((a, ia, ib), (b, ib, ia)))
                    put(a, ib, nb); put(b, ia, na)
                    new_fs = {f: family_score(g, f, self.stats[f]) for f in fams}
                    # same sum, in the same order, as score(): the search takes the same path as a full rescore
                    sc = sum(w * new_fs.get(f, fs[f]) for f, w in self.weights.items() if w) - 1e-4 * (moved + dm)
                    if sc > best + 1e-9:
                        fs.update(new_fs); best, moved, improved = sc, moved + dm, True
                    else:
                        put(a, ia, na); put(b, ib, nb)
            if not improved:
                break
        return g, best


def plan(grid: T.Grid, valid: set, priorities: list[str], base_values: dict | None = None) -> dict:
    """Best arrangement for 1 or 2 priorities. Returns the new cells, the moves and before/after values."""
    prios = [p for p in priorities if p in PRIORITIES][:2]
    fams = families(grid)
    fixed = {xy for xy in grid.cells if xy in grid.damaged}
    weapons = [b for b in fams if b.startswith(WEAPON_PREFIX)]

    def weights_for(targets: set) -> dict:
        return {b: (W_PRIORITY if b in targets else 1.0) for b in fams}

    targets = set()
    for p in prios:
        if p != 'damage':
            targets |= {b for b in fams if any(b == x or b.startswith(x) for x in PRIORITIES[p])}
    recommend = None
    if 'damage' in prios and weapons:
        # try each weapon as the damage priority and keep the one with the highest damage per second
        best = None
        for wb in weapons:
            g, _sc = Problem(grid, valid, fixed, weights_for(targets | {wb})).solve()
            dps = weapon_dps(g, wb)
            if best is None or dps > best[0]:
                best = (dps, wb, g)
        recommend = best[1]
        new = best[2]
    else:
        new, _sc = Problem(grid, valid, fixed, weights_for(targets)).solve()

    moves = []
    free = {}
    for xy, iid in grid.cells.items():
        if new.cells.get(xy) != iid:
            free.setdefault(iid, []).append(xy)
    for xy, iid in sorted(new.cells.items()):
        if grid.cells.get(xy) != iid:
            moves.append([list(free[iid].pop(0)), list(xy)])

    def values(g: T.Grid) -> dict:
        out = T.ship_display(g, base_values or {})
        for wb in weapons:
            out['dps:' + wb] = round(weapon_dps(g, wb), 2)
        for b in fams:
            out['score:' + b] = round(family_score(g, b, _stats_of(g, b)), 6)
        return out

    names = {}
    for b in fams:
        ids = [grid.cells[xy] for xy in fams[b]]
        names[b] = next((i for i in ids if '#' not in i and not i.startswith('UT_')), ids[0])
    return {'cells': {f'{x},{y}': i for (x, y), i in new.cells.items()}, 'moves': moves, 'names': names,
            'before': values(grid), 'after': values(new), 'recommend': recommend, 'priorities': prios}


def from_save(f, ship: int) -> tuple[T.Grid, set, dict, str]:
    """Technology grid of one ship of a loaded save: (grid, valid cells, class bonuses, container id)."""
    from . import inventories
    k = f.keys
    cid = f'shiptech{ship}'
    c = next((c for c in inventories.containers(f, tech=True) if c['id'] == cid), None)
    if c is None:
        raise LookupError(cid)
    cells, damaged = {}, set()
    for it in c['inv'].get(k.s('Slots')) or []:
        xy = (it[k.s('Index')][k.s('X')], it[k.s('Index')][k.s('Y')])
        cells[xy] = str(it.get(k.s('Id')) or '').lstrip('^')
        if float(it.get(k.s('DamageFactor')) or 0) > 0 or str(cells[xy]).startswith('SHIPSLOT_DMG'):
            damaged.add(xy)
    s = f.psd()[k.s('ShipOwnership')][ship]
    bsv = {}
    for b in (s.get(k.s('Inventory')) or {}).get(k.s('BaseStatValues')) or []:
        if isinstance(b, dict):
            bsv[str(b.get(k.s('BaseStatID')))] = float(b.get(k.s('Value')) or 0)
    return T.Grid(cells, c['special'], damaged), set(c['valid']), bsv, cid
