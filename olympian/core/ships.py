"""Phase 2a: per-ship actions (each one is planned for a single ship, from its own card).

Class changes are intentionally NOT offered: an in-game class upgrade also re-rolls the base stats
and adds supercharged slots at positions the game derives internally; a save editor can only change
the letter, which leaves an inconsistent ship. (Removed in v0.2.3 at the user's request.)

Every action:
  * edits only the bytes of that ship (and, for duplicate/delete, the matching entry of
    ShipUsesLegacyColours) - the rest of the save stays byte-identical;
  * is verified independently: the expected result is computed on the parsed JSON and compared
    with what the byte edit produced; anything else changed -> RuntimeError, nothing is staged.

Protections (agreed with the user): corvettes (their grid and links come from their modules and
their base) and living ships (organic inventory/tech) only get the safe actions.
"""
from __future__ import annotations

import copy
import re

from . import gamedata, jspan
from .ops import FileChange, Plan, _common_checks, load_slot, manual_only, msg, ship_type
from .savefile import SaveFile, SaveFolder, file_names

# Only the general (cargo) grid is expanded: the technology grid is left to the game, which places
# supercharged slots there itself (an artificially bigger tech grid could leave them misplaced).
S_MAX = {'Inventory': (10, 12)}   # S-class cargo maximum since 4.05
S_TECH = (10, 6)                  # S-class tech maximum; only grown when the ship already holds all its
                                  # supercharged cells (the game placed them, so a bigger grid cannot move them)
GRIDS = ('Inventory', 'Inventory_TechOnly')                       # Inventory_Cargo is legacy (unused)
EMPTY_SHIP = (b'{"NKm":"","NTx":{"93M":"","@EL":[false,"0x0"],"<d2":{"bnT":[]},"QlJ":""},'
              b'";l5":{":No":[],"hl?":[],"B@N":{"1o6":"C"},"WA4":{"rri":"Default"},"@bB":[],"MMm":[],'
              b'"=Tb":1,"N9>":1,"iF:":false,"NKm":"","F2P":1,"2c0":0},'
              b'"gan":{":No":[],"hl?":[],"B@N":{"1o6":"C"},"WA4":{"rri":"Default"},"@bB":[],"MMm":[],'
              b'"=Tb":1,"N9>":1,"iF:":false,"NKm":"","F2P":1,"2c0":0},'
              b'"PMT":{":No":[],"hl?":[],"B@N":{"1o6":"C"},"WA4":{"rri":"Default"},"@bB":[],"MMm":[],'
              b'"=Tb":1,"N9>":1,"iF:":false,"NKm":"","F2P":1,"2c0":0},'
              b'"pMa":{":No":10,"@EL":[true,"0x1"],"9;o":1},"YTa":0,"wMC":[0.0,0.0,0.0,-1.0],'
              b'"l?l":[0.0,0.0,0.0,-1.0],"FA8":[]}')

# which actions each kind of ship allows
SC_MAX = {'C': 1, 'B': 2, 'A': 3, 'S': 4}   # supercharged slots per class (patch 4.05)

ALLOWED = {
    'regular':  {'rename', 'repair', 'unlock', 'expand', 'recharge', 'duplicate', 'delete', 'seed', 'copy_tech', 'move'},
    'living':   {'rename', 'repair', 'unlock', 'recharge', 'duplicate', 'delete', 'seed', 'move'},
    'corvette': {'rename', 'repair', 'recharge', 'move'},
}


def kind_of(t: str) -> str:
    return 'corvette' if t == 'corvette' else 'living' if t == 'living' else 'regular'


def family(item_id: str) -> str:
    """'^UP_HYP4#02507' -> 'UP_HYP'; '^SHIPGUN_ROBO' -> 'SHIPGUN_ROBO' (one module of each kind)."""
    i = item_id.lstrip('^').split('#')[0]
    return re.sub(r'(\d|X)$', '', i) if i.startswith('UP_') else i


def sc_shape(cells) -> dict:
    """Name a supercharged layout the way the community does: 2x2, L, T, line, Z, 2+2, 3+1..."""
    cells = {tuple(c) for c in cells}
    if not cells:
        return {'code': 'none', 'parts': []}
    comps, seen = [], set()
    for c in sorted(cells):
        if c in seen:
            continue
        stack, comp = [c], []
        seen.add(c)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in cells and n not in seen:
                    seen.add(n)
                    stack.append(n)
        comps.append(comp)
    comps.sort(key=len, reverse=True)

    def name(comp):
        n = len(comp)
        xs, ys = {x for x, _ in comp}, {y for _, y in comp}
        w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        if n == 1:
            return 'single'
        if n == 2:
            return 'pair'
        if n == 3:
            return 'line3' if 1 in (w, h) else 'L3'
        if n == 4:
            if (w, h) == (2, 2):
                return 'square'
            if 1 in (w, h):
                return 'line4'
            # 3x2 box: T has a cell whose 3 neighbours are all in the piece; L has an end of the long side
            deg = [sum(((x + dx, y + dy) in comp) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))) for x, y in comp]
            if 3 in deg:
                return 'T'
            long_rows = [r for r in (ys if w == 3 else xs)
                         if sum(1 for x, y in comp if (y if w == 3 else x) == r) == 3]
            return 'L' if long_rows else 'Z'
        return 'big'
    names = [name(c) for c in comps]
    if len(comps) == 1:
        return {'code': names[0], 'parts': [len(comps[0])]}
    return {'code': 'split', 'parts': [len(c) for c in comps], 'names': names}


def _damaged(it: dict, k) -> bool:
    """What the game shows as broken (red frame and '!'): damaged or not fully installed technology, and the
    'damaged component' placeholders of a crashed or salvaged ship."""
    return _broken_item(it, k) or (it[k.s('Type')][k.s('InventoryType')] == 'Technology'
                                   and (it.get(k.s('DamageFactor'), 0) > 0 or not it.get(k.s('FullyInstalled'), True)))


def _broken_item(it: dict, k) -> bool:
    """Placeholder that fills a damaged cell (^SHIPSLOT_DMG1..n); repairing the cell in the game removes it."""
    return str(it.get(k.s('Id')) or '').startswith('^SHIPSLOT_DMG')


def _special_type(c: dict, k) -> str:
    return ((c.get(k.s('Type')) or {}).get(k.s('InventorySpecialSlotType'))) or ''


def _supercharged(inv: dict, k) -> list:
    """SpecialSlots that are supercharged cells. The same list also marks damaged cells (BlockedByBrokenTech)."""
    return [c for c in inv[k.s('SpecialSlots')] if _special_type(c, k) == 'TechBonus']


def _sc_fixed(ship: dict, k) -> bool:
    """S-class tech grid that already holds its 4 supercharged cells, all inside the grid."""
    t = ship[k.s('Inventory_TechOnly')]
    sc = _supercharged(t, k)
    return (t[k.s('Class')][k.s('InventoryClass')] == 'S' and len(sc) >= SC_MAX['S']
            and all(c[k.s('Index')][k.s('Y')] < t[k.s('Height')] and c[k.s('Index')][k.s('X')] < t[k.s('Width')] for c in sc))


def _drop_ranges(b: bytes, s: int, drop: set) -> list:
    """Byte ranges that remove the array items at the given positions, separators included, without
    touching the bytes of the items that stay (other edits may point inside them)."""
    items = jspan.arr_items(b, s)
    kept = [i for i in range(len(items)) if i not in drop]
    if not kept:
        return [(items[0][0], items[-1][1])] if items else []
    last = kept[-1]
    out = [(items[i][0], items[i + 1][0]) for i in sorted(drop) if i < last]
    tail = [i for i in drop if i > last]
    if tail:
        out.append((items[last][1], items[max(tail)][1]))
    return out


def _clear_broken_cells(se, ships, ship, exp, k) -> int:
    """Repair damaged cells the way the game leaves them: placeholder item gone, BlockedByBrokenTech gone."""
    n = 0
    for g in GRIDS:
        inv = ships[ship][k.s(g)]
        dead = {i for i, it in enumerate(inv[k.s('Slots')]) if _broken_item(it, k)}
        block = {i for i, c in enumerate(inv[k.s('SpecialSlots')]) if _special_type(c, k) == 'BlockedByBrokenTech'}
        cells = {(it[k.s('Index')][k.s('X')], it[k.s('Index')][k.s('Y')]) for i, it in enumerate(inv[k.s('Slots')]) if i in dead}
        cells |= {(c[k.s('Index')][k.s('X')], c[k.s('Index')][k.s('Y')]) for i, c in enumerate(inv[k.s('SpecialSlots')]) if i in block}
        for key, drop in (('Slots', dead), ('SpecialSlots', block)):
            if drop:
                for a, z in _drop_ranges(se.b, se.span([k(g), k(key)])[0], drop):
                    se.replace((a, z), b'')
                e = exp[k.s(g)]
                e[k.s(key)] = [x for i, x in enumerate(e[k.s(key)]) if i not in drop]
        n += len(cells)
    return n


def _rechargeable(it: dict, k) -> bool:
    """Technology that the game says is chargeable and isn't full. Without game data, only items with a
    real partial charge (0 < Amount < Max): -1 means 'no charge' and 0 is ambiguous in the save."""
    if it[k.s('Type')][k.s('InventoryType')] != 'Technology':
        return False
    amount, mx = it[k.s('Amount')], it[k.s('MaxAmount')]
    known = gamedata.chargeable(it[k.s('Id')])
    if known is True:
        return mx > 0 and 0 <= amount < mx
    if known is False:
        return False
    return 0 < amount < mx


def _group(item_id, t):
    from .inventories import item_group          # late import: inventories imports ops, which imports this module
    return item_group(item_id, t)


def summary(ship: dict, k) -> dict:
    """Per-ship numbers for the card."""
    out = {}
    for g in GRIDS:
        inv = ship[k.s(g)]
        items = inv[k.s('Slots')]
        out[g] = {'w': inv[k.s('Width')], 'h': inv[k.s('Height')], 'valid': len(inv[k.s('ValidSlotIndices')]),
                  'used': len(items), 'cls': inv[k.s('Class')][k.s('InventoryClass')],
                  'damaged': sum(1 for x in items if _damaged(x, k)),
                  'low': sum(1 for x in items if _rechargeable(x, k)),
                  'super': len(_supercharged(inv, k)),
                  'sc': [[c[k.s('Index')][k.s('X')], c[k.s('Index')][k.s('Y')]] for c in _supercharged(inv, k)],
                  'broken': len({(c[k.s('Index')][k.s('X')], c[k.s('Index')][k.s('Y')]) for c in inv[k.s('SpecialSlots')]
                                 if _special_type(c, k) == 'BlockedByBrokenTech'}
                                | {(x[k.s('Index')][k.s('X')], x[k.s('Index')][k.s('Y')]) for x in items if _broken_item(x, k)}),
                  'cells': [[x[k.s('Index')][k.s('X')], x[k.s('Index')][k.s('Y')]] for x in items],
                  'valid_cells': [[c[k.s('X')], c[k.s('Y')]] for c in inv[k.s('ValidSlotIndices')]]}
    t = ship_type(ship[k.s('Resource')][k.s('Filename')])
    out['kind'] = kind_of(t)
    out['allowed'] = sorted(ALLOWED[out['kind']])
    out['seed'] = (ship[k.s('Resource')].get(k.s('Seed')) or [None, None])[1]
    out['class'] = out['Inventory']['cls']
    stats = {x.get(k.s('BaseStatID')): x.get(k.s('Value')) for x in ship[k.s('Inventory')].get(k.s('BaseStatValues'), [])}
    out['stats'] = {n: round(float(stats[f'^SHIP_{n.upper()}']), 1) for n in ('damage', 'shield', 'hyperdrive', 'agile')
                    if f'^SHIP_{n.upper()}' in stats}
    out['sc_shape'] = sc_shape(out['Inventory_TechOnly']['sc'])
    out['sc_max'] = SC_MAX.get(out['Inventory_TechOnly']['cls'], 4)
    out['sc_fixed'] = _sc_fixed(ship, k)
    out['modules'] = [x[k.s('Id')] for x in ship[k.s('Inventory_TechOnly')][k.s('Slots')]]
    out['cargo_items'] = [[x[k.s('Index')][k.s('X')], x[k.s('Index')][k.s('Y')], x[k.s('Id')],
                           x[k.s('Amount')], x[k.s('MaxAmount')], x[k.s('Type')][k.s('InventoryType')],
                           gamedata.resolve(x[k.s('Id')]), _group(x[k.s('Id')], x[k.s('Type')][k.s('InventoryType')]),
                           _damaged(x, k)]
                          for x in ship[k.s('Inventory')][k.s('Slots')]]
    out['tech_items'] = [[x[k.s('Index')][k.s('X')], x[k.s('Index')][k.s('Y')], x[k.s('Id')],
                          x[k.s('Amount')], x[k.s('MaxAmount')], bool(gamedata.chargeable(x[k.s('Id')])),
                          gamedata.resolve(x[k.s('Id')]), _damaged(x, k), _rechargeable(x, k),
                          None if _damaged(x, k) else gamedata.adj_group(x[k.s('Id')])]
                         for x in ship[k.s('Inventory_TechOnly')][k.s('Slots')]]
    return out


# ------------------------------------------------------------------------------- helpers -------
def _cell(x: int, y: int, k) -> bytes:
    return b'{"' + k('X') + b'":%d,"' % x + k('Y') + b'":%d}' % y


def _grid_bytes(w: int, h: int, keep: dict, k) -> bytes:
    return b'[' + b','.join(keep.get((x, y), _cell(x, y, k)) for y in range(h) for x in range(w)) + b']'


def _valid_map(b: bytes, s: int, k) -> dict:
    out = {}
    for a, z in jspan.arr_items(b, s):
        c = jspan.loads(b[a:z])
        out[(c[k.s('X')], c[k.s('Y')])] = b[a:z]
    return out


def _new_inv(o_inv: dict, k, grid_w=None, grid_h=None):
    """Expected (parsed) inventory after unlocking the grid (optionally resized)."""
    w = grid_w or o_inv[k.s('Width')]
    h = grid_h or o_inv[k.s('Height')]
    o_inv[k.s('Width')], o_inv[k.s('Height')] = w, h
    o_inv[k.s('ValidSlotIndices')] = [{k.s('X'): x, k.s('Y'): y} for y in range(h) for x in range(w)]


class _LazyShips:
    """Expected result, copying only the ships an action actually touches."""

    def __init__(self, ships):
        self.src, self.copies = ships, {}

    def __getitem__(self, i):
        if i not in self.copies:
            self.copies[i] = copy.deepcopy(self.src[i])
        return self.copies[i]

    def __setitem__(self, i, value):
        self.copies[i] = value


class ShipEdit:
    """Byte edits for one ship + the parsed expectation they must produce."""

    def __init__(self, f: SaveFile, index: int):
        self.f, self.k, self.b = f, f.keys, f.raw
        k = self.k
        self.idx = jspan.index_for(self.b)
        self.ships_span = self.idx.get(k.psd + [k('ShipOwnership')])
        self.items = jspan.arr_items(self.b, self.ships_span[0])
        self.ships = f.psd()[k.s('ShipOwnership')]
        self.expected = _LazyShips(self.ships)
        self.index = index
        self.edits: list[tuple[int, int, bytes]] = []
        self.legacy_edit = None
        self.expected_legacy = None

    def patched_json(self):
        """The old parsed document with only the touched ships (and legacy list) replaced."""
        k = self.k
        j = self.f.json
        if j is None:
            return None
        j2 = dict(j)
        bc = dict(j2[k.s('BaseContext')])
        psd = dict(bc[k.s('PlayerStateData')])
        ships = list(psd[k.s('ShipOwnership')])
        for i, v in self.expected.copies.items():
            ships[i] = v
        psd[k.s('ShipOwnership')] = ships
        if self.expected_legacy is not None:
            psd[k.s('ShipUsesLegacyColours')] = self.expected_legacy
        bc[k.s('PlayerStateData')] = psd
        j2[k.s('BaseContext')] = bc
        return j2

    @property
    def start(self) -> int:
        return self.items[self.index][0]

    def span(self, path, at=None):
        return jspan.get(self.b, path, self.start if at is None else at)

    def replace(self, span, new: bytes):
        self.edits.append((span[0], span[1], new))

    def result(self) -> bytes:
        k = self.k
        edits = list(self.edits)
        if self.legacy_edit:
            edits.append(self.legacy_edit)
        ed = sorted(edits, key=lambda t: t[0], reverse=True)
        for x, y in zip(ed, ed[1:]):
            if y[1] > x[0]:
                raise RuntimeError('ship_overlap')
        out = bytearray(self.b)
        for s0, e0, r in ed:
            out[s0:e0] = r
        new = bytes(out)
        # ---- independent verification ----
        s0, e0 = self.ships_span
        lo = min(s0, *(e[0] for e in ed)) if ed else s0
        hi_old = max(e0, *(e[1] for e in ed)) if ed else e0
        delta = len(new) - len(self.b)
        if new[:lo] != self.b[:lo] or new[hi_old + delta:] != self.b[hi_old:]:
            raise RuntimeError('ship_verify_outside')
        # reuse the shifted index of the old document instead of re-walking PlayerStateData
        jspan.carry_index(self.b, new, ed)
        nidx = jspan.index_for(new)
        # parse back only the touched ships (byte-identical elsewhere was checked above)
        ns, _ = nidx.get(k.psd + [k('ShipOwnership')])
        new_items = jspan.arr_items(new, ns)
        if len(new_items) != len(self.items):
            raise RuntimeError('ship_verify')
        for i, want in self.expected.copies.items():
            a, z = new_items[i]
            if jspan.loads(new[a:z]) != want:
                raise RuntimeError('ship_verify')
        for i in range(len(self.items)):                 # untouched ships: same bytes, just shifted
            if i not in self.expected.copies:
                a0, z0 = self.items[i]
                a1, z1 = new_items[i]
                if self.b[a0:z0] != new[a1:z1]:
                    raise RuntimeError('ship_verify_outside')
        if self.expected_legacy is not None:
            ls, le = nidx.get(k.psd + [k('ShipUsesLegacyColours')])
            if jspan.loads(new[ls:le]) != self.expected_legacy:
                raise RuntimeError('ship_verify')
        return new


def _legacy(se: ShipEdit, dst: int, value_from: int | None):
    """Keep ShipUsesLegacyColours in line with the ship list (copy or reset one entry)."""
    k = se.k
    if not se.idx.has(k.psd + [k('ShipUsesLegacyColours')]):
        return None
    s, _ = se.idx.get(k.psd + [k('ShipUsesLegacyColours')])
    items = jspan.arr_items(se.b, s)
    if dst >= len(items):
        return None
    val = se.b[items[value_from][0]:items[value_from][1]] if value_from is not None else b'false'
    se.legacy_edit = (items[dst][0], items[dst][1], val)
    exp = list(se.f.psd()[k.s('ShipUsesLegacyColours')])
    exp[dst] = jspan.loads(val)
    se.expected_legacy = exp
    return val


def _cells_txt(cells) -> str:
    """(col, row) shown 1-based, the way players count cells in the game."""
    return ' '.join(f'{x + 1}·{y + 1}' for x, y in sorted(cells, key=lambda c: (c[1], c[0]))) or '-'


# ------------------------------------------------------------------------------- planner -------
def plan_ship(folder: SaveFolder, slot: int, ship: int, action: str, params: dict | None = None,
              files: list[SaveFile] | None = None) -> Plan:
    params = params or {}
    plan = Plan('ship_' + action, folder.path, staged=files is not None)
    files = files if files is not None else load_slot(folder, slot)
    _common_checks(plan, files, check_game=not plan.staged)

    def ship_id(f):
        k = f.keys
        sh = f.psd()[k.s('ShipOwnership')]
        return (sh[ship][k.s('Resource')][k.s('Filename')], sh[ship].get(k.s('Name')) or '') if 0 <= ship < len(sh) else None
    kept = manual_only(plan, files, lambda f: ship_id(f) != ship_id(files[0]))
    if kept is not None:
        files = kept
    k0 = files[0].keys
    ships0 = files[0].psd()[k0.s('ShipOwnership')]
    if not (0 <= ship < len(ships0)) or not ships0[ship][k0.s('Resource')][k0.s('Filename')]:
        plan.messages.append(msg('block', 'ship_empty', file=file_names(files[0].file_index)[0], n=ship + 1))
        return plan
    t = ship_type(ships0[ship][k0.s('Resource')][k0.s('Filename')])
    kind = kind_of(t)
    name = ships0[ship].get(k0.s('Name')) or ''
    label = {'n': ship + 1, 'name': name or '-'}
    if action not in ALLOWED[kind]:
        plan.messages.append(msg('block', 'ship_protected', kind=kind, action=action, **label))
        return plan

    for f in files:
        k = f.keys
        ships = f.psd()[k.s('ShipOwnership')]
        if ship >= len(ships) or ships[ship][k.s('Resource')][k.s('Filename')] != ships0[ship][k0.s('Resource')][k0.s('Filename')] \
                or (ships[ship].get(k.s('Name')) or '') != name:
            plan.messages.append(msg('block', 'ship_files_differ', file=file_names(f.file_index)[0], **label))
            return plan
        se = ShipEdit(f, ship)
        exp = se.expected[ship]
        fname = file_names(f.file_index)[0]

        if action == 'rename':
            new = str(params.get('name', '')).strip()[:60]
            se.replace(se.span([k('Name')]), jspan.dumps_value(new))
            exp[k.s('Name')] = new
            plan.messages.append(msg('info', 'ship_rename_what', new=new or '-', **label))

        elif action == 'repair':                     # damaged technology, as the game's own repair leaves it
            fixed = 0
            for g in GRIDS:
                inv = ships[ship][k.s(g)]
                sl, _ = se.span([k(g), k('Slots')])
                for j, (a, _z) in enumerate(jspan.arr_items(se.b, sl)):
                    it = inv[k.s('Slots')][j]
                    if not _damaged(it, k) or _broken_item(it, k):     # placeholders go in _clear_broken_cells
                        continue
                    if it.get(k.s('DamageFactor'), 0) > 0:
                        se.replace(jspan.get(se.b, [k('DamageFactor')], a), b'0.0')
                        exp[k.s(g)][k.s('Slots')][j][k.s('DamageFactor')] = 0.0
                    if not it.get(k.s('FullyInstalled'), True):
                        se.replace(jspan.get(se.b, [k('FullyInstalled')], a), b'true')
                        exp[k.s(g)][k.s('Slots')][j][k.s('FullyInstalled')] = True
                    fixed += 1
            fixed += _clear_broken_cells(se, ships, ship, exp, k)
            if fixed:
                plan.messages.append(msg('info', 'ship_repair_what', file=fname, fixed=fixed, **label))
            else:
                plan.messages.append(msg('ok', 'ship_repair_nothing'))

        elif action == 'unlock':                     # cells inside the current grid that are still closed
            if kind == 'corvette':
                plan.messages.append(msg('info', 'ship_corvette_grid'))
                return plan
            opened = 0
            for g in GRIDS:
                inv = ships[ship][k.s(g)]
                w, h = inv[k.s('Width')], inv[k.s('Height')]
                vs = se.span([k(g), k('ValidSlotIndices')])
                if len(inv[k.s('ValidSlotIndices')]) < w * h:
                    opened += w * h - len(inv[k.s('ValidSlotIndices')])
                    se.replace(vs, _grid_bytes(w, h, _valid_map(se.b, vs[0], k), k))
                    _new_inv(exp[k.s(g)], k)
            opened += _clear_broken_cells(se, ships, ship, exp, k)
            if opened:
                plan.messages.append(msg('info', 'ship_unlock_what', file=fname, opened=opened, **label))
            else:
                plan.messages.append(msg('ok', 'ship_unlock_nothing'))

        elif action == 'expand':
            cls = ships[ship][k.s('Inventory')][k.s('Class')][k.s('InventoryClass')]
            if cls != 'S':
                plan.messages.append(msg('block', 'ship_expand_class', cls=cls, **label))
                return plan
            grids = dict(S_MAX)
            if _sc_fixed(ships[ship], k):
                grids['Inventory_TechOnly'] = S_TECH
            for g, (W, H) in grids.items():
                inv = ships[ship][k.s(g)]
                if (inv[k.s('Width')], inv[k.s('Height')]) == (W, H) and len(inv[k.s('ValidSlotIndices')]) == W * H:
                    continue
                w, h = inv[k.s('Width')], inv[k.s('Height')]
                if w > W or h > H:
                    plan.messages.append(msg('block', 'ship_grid_bigger', **label))
                    return plan
                vs = se.span([k(g), k('ValidSlotIndices')])
                se.replace(se.span([k(g), k('Width')]), str(W).encode())
                se.replace(se.span([k(g), k('Height')]), str(H).encode())
                se.replace(vs, _grid_bytes(W, H, _valid_map(se.b, vs[0], k), k))
                _new_inv(exp[k.s(g)], k, W, H)
                plan.messages.append(msg('info', 'ship_expand_what', file=fname, grid=g,
                                         old=len(inv[k.s('ValidSlotIndices')]), new=W * H, **label))

        elif action == 'recharge':
            n, what = 0, []
            for g in GRIDS:
                inv = ships[ship][k.s(g)]
                sl, _ = se.span([k(g), k('Slots')])
                for j, (a, _z) in enumerate(jspan.arr_items(se.b, sl)):
                    it = inv[k.s('Slots')][j]
                    if _rechargeable(it, k):
                        se.replace(jspan.get(se.b, [k('Amount')], a), str(it[k.s('MaxAmount')]).encode())
                        exp[k.s(g)][k.s('Slots')][j][k.s('Amount')] = it[k.s('MaxAmount')]
                        n += 1
                        ix = it[k.s('Index')]
                        what.append([it[k.s('Id')], f"{ix[k.s('X')] + 1}·{ix[k.s('Y')] + 1}",
                                     max(0, it[k.s('Amount')]), it[k.s('MaxAmount')]])
            if n:
                plan.messages.append(msg('info', 'ship_recharge_what', file=fname, n_items=n, **label))
                plan.messages.append(msg('info', 'ship_recharge_list', charges=what))
            else:
                plan.messages.append(msg('ok', 'ship_recharge_nothing'))

        elif action == 'seed':
            raw = str(params.get('seed', '')).strip()
            if not re.fullmatch(r'0x[0-9A-Fa-f]{1,16}', raw):
                plan.messages.append(msg('block', 'ship_seed_invalid'))
                return plan
            new = '0x' + raw[2:].upper()
            se.replace(se.span([k('Resource'), k('Seed')]), b'[true,' + jspan.dumps_value(new) + b']')
            old = exp[k.s('Resource')][k.s('Seed')][1]
            exp[k.s('Resource')][k.s('Seed')] = [True, new]
            plan.messages.append(msg('info', 'ship_seed_what', file=fname, old=old, new=new, **label))
            plan.meta['seed'] = {'kind': 'ship', 'index': ship, 'model': ships0[ship][k0.s('Resource')][k0.s('Filename')],
                                 'name': name, 'old': old, 'new': new}
            plan.messages.append(msg('warn', 'ship_seed_look'))

        elif action == 'duplicate':
            free = next((i for i, s in enumerate(ships) if not s[k.s('Resource')][k.s('Filename')]), None)
            if free is None:
                plan.messages.append(msg('block', 'ship_no_free'))
                return plan
            src = jspan.arr_items(se.b, se.ships_span[0])[ship]
            dst = se.items[free]
            se.edits.append((dst[0], dst[1], se.b[src[0]:src[1]]))
            se.expected[free] = copy.deepcopy(ships[ship])
            _legacy(se, free, ship)
            plan.messages.append(msg('info', 'ship_duplicate_what', file=fname, slot=free + 1, **label))

        elif action == 'delete':
            p = f.psd()
            refs = []
            if p.get(k.s('PrimaryShip')) == ship:
                refs.append('primary')
            if p.get(k.s('CorvetteEditAssociatedShipIndex')) == ship:
                refs.append('corvette_edit')
            for base in p.get(k.s('PersistentPlayerBases'), []):
                if base.get(k.s('BaseType'), {}).get(k.s('PersistentBaseTypes')) == 'PlayerShipBase' \
                        and base.get(k.s('UserData')) == ship:
                    refs.append('corvette_base')
            if refs:
                for r in dict.fromkeys(refs):
                    plan.messages.append(msg('block', 'ship_in_use_' + r, **label))
                return plan
            if sum(1 for s in ships if s[k.s('Resource')][k.s('Filename')]) <= 1:
                plan.messages.append(msg('block', 'ship_last'))
                return plan
            if not k.obfuscated:
                plan.messages.append(msg('block', 'ship_old_format'))
                return plan
            se.edits.append((se.items[ship][0], se.items[ship][1], EMPTY_SHIP))
            se.expected[ship] = jspan.loads(EMPTY_SHIP)
            _legacy(se, ship, None)
            plan.messages.append(msg('danger', 'ship_delete_what', file=fname, **label))
            plan.messages.append(msg('ok', 'ship_delete_others'))

        elif action == 'copy_tech':
            src_i = int(params.get('from', -1))
            if not (0 <= src_i < len(ships)) or src_i == ship or not ships[src_i][k.s('Resource')][k.s('Filename')]:
                plan.messages.append(msg('block', 'ship_copy_src'))
                return plan
            if kind_of(ship_type(ships[src_i][k.s('Resource')][k.s('Filename')])) != 'regular':
                plan.messages.append(msg('block', 'ship_copy_src_kind'))
                return plan
            g = 'Inventory_TechOnly'
            src_inv, dst_inv = ships[src_i][k.s(g)], ships[ship][k.s(g)]
            W, H = dst_inv[k.s('Width')], dst_inv[k.s('Height')]
            valid = {(c[k.s('X')], c[k.s('Y')]) for c in dst_inv[k.s('ValidSlotIndices')]}
            src_items = src_inv[k.s('Slots')]
            src_cells = {(x[k.s('Index')][k.s('X')], x[k.s('Index')][k.s('Y')]) for x in src_items}
            if not src_cells <= valid:
                plan.messages.append(msg('block', 'ship_copy_grid', need=len(src_cells), have=len(valid), **label))
                return plan
            src_fams = {family(x[k.s('Id')]) for x in src_items}
            sa = jspan.arr_items(se.b, se.ships_span[0])[src_i][0]
            ss, _ = jspan.get(se.b, [k(g), k('Slots')], sa)
            src_bytes = [se.b[a:z] for a, z in jspan.arr_items(se.b, ss)]
            free = [c for c in sorted(valid, key=lambda c: (c[1], c[0])) if c not in src_cells]
            ts, te = se.span([k(g), k('Slots')])
            keep_b, keep_o, dropped = [], [], []
            for j, (a, z) in enumerate(jspan.arr_items(se.b, ts)):
                it = dst_inv[k.s('Slots')][j]
                if family(it[k.s('Id')]) in src_fams:
                    dropped.append(it[k.s('Id')])
                    continue
                if not free:
                    plan.messages.append(msg('block', 'ship_copy_full', **label))
                    return plan
                cx, cy = free.pop(0)
                el = se.b[a:z]
                cs, ce = jspan.get(el, [k('Index')])
                keep_b.append(el[:cs] + _cell(cx, cy, k) + el[ce:])
                o = copy.deepcopy(it)
                o[k.s('Index')] = {k.s('X'): cx, k.s('Y'): cy}
                keep_o.append(o)
            se.replace((ts, te), b'[' + b','.join(src_bytes + keep_b) + b']')
            exp[k.s(g)][k.s('Slots')] = copy.deepcopy(src_items) + keep_o
            plan.messages.append(msg('info', 'ship_copy_what', file=fname, n_items=len(src_items),
                                     src=ships[src_i].get(k.s('Name')) or str(src_i + 1),
                                     kept=len(keep_o), replaced=len(dropped), **label))
            plan.messages.append(msg('warn', 'ship_copy_super'))
        elif action == 'move':
            g = {'tech': 'Inventory_TechOnly', 'cargo': 'Inventory'}.get(params.get('grid'))
            try:
                a = tuple(int(v) for v in params.get('from'))
                b = tuple(int(v) for v in params.get('to'))
            except (TypeError, ValueError):
                g = None
            if not g or a == b:
                plan.messages.append(msg('block', 'ship_move_invalid'))
                return plan
            inv = ships[ship][k.s(g)]
            valid = {(c[k.s('X')], c[k.s('Y')]) for c in inv[k.s('ValidSlotIndices')]}
            at = {(x[k.s('Index')][k.s('X')], x[k.s('Index')][k.s('Y')]): j for j, x in enumerate(inv[k.s('Slots')])}
            if a not in at:
                plan.messages.append(msg('block', 'ship_move_empty'))
                return plan
            if b not in valid:
                plan.messages.append(msg('block', 'ship_move_locked'))
                return plan
            sl, _ = se.span([k(g), k('Slots')])
            spans = jspan.arr_items(se.b, sl)
            moves = [(at[a], b)] + ([(at[b], a)] if b in at else [])
            for j, (x, y) in moves:
                se.replace(jspan.get(se.b, [k('Index')], spans[j][0]), _cell(x, y, k))
                exp[k.s(g)][k.s('Slots')][j][k.s('Index')] = {k.s('X'): x, k.s('Y'): y}
            ids = [inv[k.s('Slots')][j][k.s('Id')] for j, _ in moves]
            plan.messages.append(msg('info', 'ship_move_what' if len(moves) == 1 else 'ship_swap_what', file=fname,
                                     item=ids[0], other=ids[-1], frm=f'{a[0] + 1}·{a[1] + 1}', to=f'{b[0] + 1}·{b[1] + 1}',
                                     grid=params.get('grid'), **label))
        else:
            raise LookupError(action)

        fc = FileChange(f.file_index, f, se.result())
        fc.json = se.patched_json()          # lets the session skip re-parsing 5 MB for the next action
        plan.changes.append(fc)
    plan.summary = msg('info', 'chg_ship_' + action, **label)
    return plan


# ------------------------------------------------------------------- copy / move to other saves -------
def _cc_index(ship: int) -> int | None:
    """CharacterCustomisationData entry that holds a custom ship's look (slots 1-6 and 7-12)."""
    if 0 <= ship < 6:
        return 3 + ship
    if 6 <= ship < 12:
        return 17 + ship - 6
    return None


def _cc_used(psd: dict, k, ship: int) -> bool:
    i = _cc_index(ship)
    cc = psd.get(k.s('CharacterCustomisationData')) or []
    if i is None or i >= len(cc) or not isinstance(cc[i], dict):
        return False
    d = cc[i].get(k.s('CustomData')) or {}
    return bool(d.get(k.s('DescriptorGroups')) or d.get(k.s('Colours')))


def plan_ship_transfer(folder: SaveFolder, src_slot: int, ship: int, dest_slots: list[int], move: bool = False) -> Plan:
    """Copy one ship, with everything in its inventories, into a free ship slot of other saves; `move` also
    removes it from the source afterwards. Byte-faithful: the ship's own bytes are written as they are."""
    plan = Plan('ship_move' if move else 'ship_transfer', folder.path)
    src_files = load_slot(folder, src_slot)
    _common_checks(plan, src_files, check_game=True)
    src = max(src_files, key=lambda f: f.mtime_hg)                        # the copy the game loads
    k0 = src.keys
    sps = src.psd()
    ships0 = sps[k0.s('ShipOwnership')]
    if not (0 <= ship < len(ships0)) or not ships0[ship][k0.s('Resource')][k0.s('Filename')]:
        plan.messages.append(msg('block', 'ship_empty', file=file_names(src.file_index)[0], n=ship + 1))
        return plan
    t = ship_type(ships0[ship][k0.s('Resource')][k0.s('Filename')])
    name = ships0[ship].get(k0.s('Name')) or ''
    label = {'n': ship + 1, 'name': name or '-'}
    if t == 'corvette':
        plan.messages.append(msg('block', 'ship_transfer_corvette', **label))
        return plan
    if _cc_used(sps, k0, ship):
        plan.messages.append(msg('block', 'ship_transfer_custom', **label))
        return plan
    dest_slots = sorted({int(d) for d in dest_slots if 1 <= int(d) <= 15 and int(d) != src_slot})
    if not dest_slots:
        plan.messages.append(msg('block', 'ship_transfer_no_dest'))
        return plan
    sidx = jspan.index_for(src.raw)
    sspan = sidx.get(k0.psd + [k0('ShipOwnership')])
    sitem = jspan.arr_items(src.raw, sspan[0])[ship]
    ship_bytes = src.raw[sitem[0]:sitem[1]]
    legacy_val = None
    if sidx.has(k0.psd + [k0('ShipUsesLegacyColours')]):
        ls, _ = sidx.get(k0.psd + [k0('ShipUsesLegacyColours')])
        li = jspan.arr_items(src.raw, ls)
        if ship < len(li):
            legacy_val = src.raw[li[ship][0]:li[ship][1]]
    for d in dest_slots:
        try:
            dfiles = load_slot(folder, d)
        except FileNotFoundError:
            plan.messages.append(msg('block', 'ship_transfer_empty_slot', slot=d))
            return plan
        for f in dfiles:
            k = f.keys
            if k.obfuscated != k0.obfuscated:
                plan.messages.append(msg('block', 'ship_transfer_format', slot=d))
                return plan
            ships = f.psd()[k.s('ShipOwnership')]
            free = next((i for i, s in enumerate(ships) if not s[k.s('Resource')][k.s('Filename')]
                         and not _cc_used(f.psd(), k, i)), None)
            fname = file_names(f.file_index)[0]
            if free is None:
                plan.messages.append(msg('block', 'ship_transfer_full', slot=d, file=fname))
                return plan
            se = ShipEdit(f, free)
            dst = se.items[free]
            se.edits.append((dst[0], dst[1], ship_bytes))
            se.expected[free] = copy.deepcopy(ships0[ship])
            if se.idx.has(k.psd + [k('ShipUsesLegacyColours')]):
                ls, _ = se.idx.get(k.psd + [k('ShipUsesLegacyColours')])
                li = jspan.arr_items(se.b, ls)
                if free < len(li):
                    val = legacy_val if legacy_val is not None else b'false'
                    se.legacy_edit = (li[free][0], li[free][1], val)
                    exp = list(f.psd()[k.s('ShipUsesLegacyColours')])
                    exp[free] = jspan.loads(val)
                    se.expected_legacy = exp
            fc = FileChange(f.file_index, f, se.result())
            fc.json = se.patched_json()
            plan.changes.append(fc)
            plan.messages.append(msg('info', 'ship_transfer_what', slot=d, file=fname, to=free + 1, **label))
    plan.messages.append(msg('ok', 'ship_transfer_loot'))
    if move:
        rm = plan_ship(folder, src_slot, ship, 'delete')
        if rm.blocked:
            plan.messages.extend(rm.messages)
            return plan
        plan.changes.extend(rm.changes)
        plan.messages.append(msg('danger', 'ship_move_remove', slot=src_slot, **label))
    plan.summary = msg('info', 'chg_ship_move' if move else 'chg_ship_transfer', slots=', '.join(map(str, dest_slots)), **label)
    return plan
