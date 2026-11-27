"""Phase 2c: storage inventories (exosuit, freighter, storage containers, corvette storage, ship cargo).

What can be done here (agreed with the user):
  * move an item to another cell, in the same inventory or in another one;
  * drop it on a stack of the same item -> they merge up to the stack limit, the rest stays where it was;
  * drop it on a different item -> the two swap places (both must fit where they go);
  * "sort by" one inventory (name, rarity or value; ascending or descending): equal stacks are merged
    first; technology stays where it is;
  * "move here all the ..." (storage containers, corvette storage, freighter, exocraft): gathers every item of one group (gases, minerals,
    crafting parts, upgrade modules by class...) from the other inventories into that container;
  * rename a storage container.
Moving, sorting and gathering never create or delete anything (verified on every action). On the user's request,
the item menu can also split, fill to the max, duplicate or delete a stack: those set an exact expected change of the
totals (totals_delta) and the verification checks exactly that.

Stack limits are the game's own rule, checked against every item of a real save:
    MaxAmount = min(cap, size[difficulty level][Substance|Product][StackSizeGroup] x StackMultiplier)
  - difficulty level: PlayerStateData.DifficultyState.Settings.InventoryStackLimits (High/Normal/Low)
  - StackSizeGroup: stored in each inventory (Personal, Ship, Freighter, Chest, BaseCapsule...)
  - sizes and caps: metadata/gamestate/difficultyconfig.mbin; StackMultiplier: product/substance tables.
Items that are not in those tables (building parts, procedural items) keep their own ratio, derived from
the MaxAmount the game gave them; when it cannot be derived the move to another inventory is refused.

Technology never leaves its inventory (it is installed there), and the technology grids are not shown.

Every action rewrites only the Slots arrays of the inventories it touches. The expected result is
computed on the parsed JSON and compared with what the byte edit produced; any difference anywhere
else -> RuntimeError and nothing is staged.
"""
from __future__ import annotations
import json

import copy
import secrets
from functools import lru_cache

from . import gamedata, jspan
from .ops import FileChange, Plan, _common_checks, load_slot, msg, ship_type
from .savefile import SaveFile, SaveFolder, file_names

CHESTS = [f'Chest{i}Inventory' for i in range(1, 11)]
# (container id, key in PlayerStateData, kind) - fixed inventories, in the order shown
FIXED = [('suit', 'Inventory', 'suit'), ('freighter', 'FreighterInventory', 'freighter')] + \
        [(f'chest{i}', key, 'chest') for i, key in enumerate(CHESTS, 1)] + \
        [('corvette', 'CorvetteStorageInventory', 'corvette')]
TYPE_ORDER = {'Substance': 0, 'Product': 1}
# technology grids besides the ships' (shown in Inventarios): (container id, key in PlayerStateData, kind)
TECH_FIXED = [('suittech', 'Inventory_TechOnly', 'suittech'), ('freightertech', 'FreighterInventory_TechOnly', 'freightertech')]
TECH_KINDS = {'shiptech', 'suittech', 'freightertech', 'mttech'}
VEHICLE_SKIP = {4}                       # VehicleOwnership[4] is the Hovercraft slot, not an exocraft of the game

# "Move here all the ..." groups, from the game's own categories (substance / product tables) and, for
# upgrade modules, their class (C+B / A / S, X and special: sentinel, robot...).
GROUPS = ['gases', 'minerals', 'fuel', 'metal', 'catalyst', 'stellar', 'precious', 'flora', 'special',
          'components', 'trade', 'consumables', 'curiosities', 'building', 'salvage', 'treasure', 'creatures',
          'custom', 'tech_items', 'mods_cb', 'mods_a', 'mods_sx', 'other']
_SUB_GROUP = {'Earth': 'minerals', 'Fuel': 'fuel', 'Metal': 'metal', 'Catalyst': 'catalyst', 'Stellar': 'stellar',
              'Exotic': 'precious', 'Flora': 'flora', 'Special': 'special'}
_PROD_GROUP = {'Component': 'components', 'Tradeable': 'trade', 'Consumable': 'consumables',
               'Curiosity': 'curiosities', 'BuildingPart': 'building', 'SpaceSalvage': 'salvage',
               'Procedural': 'treasure', 'CreatureEgg': 'creatures', 'Fish': 'creatures', 'ExhibitBone': 'creatures',
               'CustomisationPart': 'custom'}
_RARITY = {'Common': 1, 'Uncommon': 2, 'Rare': 3}
_CLASS = {'C': 1, 'B': 2, 'A': 3, 'S': 4, 'X': 5, 'Ø': 5}
SORT_KEYS = ('name', 'rarity', 'value')
NAME_MAX = 40


def item_group(item_id: str, t: str) -> str | None:
    """Group of a stored item for "move here all the ..." (None = technology installed there, or unknown)."""
    if t not in TYPE_ORDER:
        return None
    it = gamedata.item(item_id)
    if not it:
        return None
    rid = gamedata.resolve(item_id) or ''
    if it['k'] == 'proc':
        c = it.get('cls') or ''
        return 'mods_cb' if c in ('C', 'B') else 'mods_a' if c == 'A' else 'mods_sx'
    if it['k'] == 'tech':
        return 'tech_items'
    if it['k'] == 'substance':
        if rid.startswith('GAS') and not rid.startswith('GASGIANT'):
            return 'gases'
        return _SUB_GROUP.get(it.get('cat'), 'other')
    return _PROD_GROUP.get(it.get('cat'), 'other')


def rank(item_id: str) -> int:
    """Rarity on one scale: common 1 .. rare 3; upgrade modules by class C 1 .. S 4, X / special 5."""
    it = gamedata.item(item_id) or {}
    if it.get('k') == 'proc':
        return _CLASS.get(it.get('cls') or '', 5)
    return _RARITY.get(it.get('rarity') or '', 0)


def value(item_id: str) -> float:
    return float((gamedata.item(item_id) or {}).get('value') or 0)


# ------------------------------------------------------------------------------- reading -------
def level_of(psd: dict, k) -> str | None:
    """Difficulty level of stack sizes ('High' / 'Normal' / 'Low'), None when the save has none."""
    try:
        v = psd[k.s('DifficultyState')][k.s('Settings')][k.s('InventoryStackLimits')]
        return v[k.s('InventoryStackLimitsDifficulty')]
    except (KeyError, TypeError):
        return None


def group_of(inv: dict, k) -> str:
    try:
        return inv[k.s('StackSizeGroup')][k.s('InventoryStackSizeGroup')]
    except (KeyError, TypeError):
        return 'Default'


def _type(it: dict, k) -> str:
    return it[k.s('Type')][k.s('InventoryType')]


def _xy(it: dict, k) -> tuple[int, int]:
    return it[k.s('Index')][k.s('X')], it[k.s('Index')][k.s('Y')]


def max_in(it: dict, group: str, src_group: str, level: str | None) -> int | None:
    """Stack limit of an item if it were stored in an inventory of `group` (None = cannot tell)."""
    k_type = it['_t']
    cur = it['_max']
    if group == src_group:
        return cur                                     # same kind of inventory: nothing changes
    if k_type not in TYPE_ORDER:
        return None
    sizes = gamedata.stack_sizes(level) if level else None
    if not sizes or group not in sizes[k_type] or src_group not in sizes[k_type]:
        return 1 if cur <= 1 else None
    cap = sizes['cap'][k_type]
    m = gamedata.stack_mult(it['_id'])
    if m > 0:
        return min(cap, sizes[k_type][group] * m)
    if cur <= 1:
        return 1
    base = sizes[k_type][src_group]
    if base > 0 and cur < cap and cur % base == 0:     # e.g. building parts: 50 in the exosuit = 10 x 5
        return min(cap, sizes[k_type][group] * (cur // base))
    return None


def containers(f: SaveFile, tech: bool = False) -> list[dict]:
    """[{id, key path, kind, name, group, w, h, valid, special, inv}] for one file. With tech=True also every
    technology grid (ships, exosuit, freighter, multi-tools)."""
    k = f.keys
    psd = f.psd()
    out = []
    for cid, key, kind in FIXED + (TECH_FIXED if tech else []):
        inv = psd.get(k.s(key))
        if not isinstance(inv, dict) or not inv.get(k.s('ValidSlotIndices')):
            continue
        out.append({'id': cid, 'path': [k(key)], 'kind': kind, 'name': inv.get(k.s('Name')) or '', 'inv': inv})
    for i, v in enumerate(psd.get(k.s('VehicleOwnership')) or []):          # exocraft cargo
        inv = v.get(k.s('Inventory')) if isinstance(v, dict) else None
        if i in VEHICLE_SKIP or not isinstance(inv, dict) or not inv.get(k.s('ValidSlotIndices')):
            continue
        out.append({'id': f'vehicle{i}', 'path': [k('VehicleOwnership'), i, k('Inventory')], 'kind': 'vehicle',
                    'vehicle': i, 'name': v.get(k.s('Name')) or '', 'inv': inv})
    if tech:
        # multi-tools: each one keeps its grid in Multitools[i].Store; the one in hand is also copied in
        # WeaponInventory, so edits of that one are written to both (and only when both are equal)
        weapon = psd.get(k.s('WeaponInventory'))
        mts = psd.get(k.s('Multitools')) or []
        same = [i for i, m in enumerate(mts) if isinstance(m, dict) and m.get(k.s('Store')) == weapon]
        for i, m in enumerate(mts):
            inv = m.get(k.s('Store')) if isinstance(m, dict) else None
            if not isinstance(inv, dict) or not inv.get(k.s('ValidSlotIndices')):
                continue
            c = {'id': f'mt{i}', 'path': [k('Multitools'), i, k('Store')], 'kind': 'mttech', 'mt': i,
                 'name': m.get(k.s('Name')) or '', 'inv': inv}
            if len(same) == 1 and same[0] == i:
                c['mirror'] = [k('WeaponInventory')]
                c['active'] = True
            elif len(same) != 1:
                c['blocked'] = 'inv_mt_unclear'        # cannot tell which copy the game reads
            out.append(c)
    for i, s in enumerate(psd.get(k.s('ShipOwnership')) or []):
        fn = s[k.s('Resource')][k.s('Filename')]
        inv = s.get(k.s('Inventory'))
        if not fn or not isinstance(inv, dict) or not inv.get(k.s('ValidSlotIndices')):
            continue
        out.append({'id': f'ship{i}', 'path': [k('ShipOwnership'), i, k('Inventory')], 'kind': 'ship', 'ship': i,
                    'ship_type': ship_type(fn), 'name': s.get(k.s('Name')) or '', 'inv': inv})
        ti = s.get(k.s('Inventory_TechOnly'))
        if tech and isinstance(ti, dict) and ti.get(k.s('ValidSlotIndices')):
            out.append({'id': f'shiptech{i}', 'path': [k('ShipOwnership'), i, k('Inventory_TechOnly')],
                        'kind': 'shiptech', 'ship': i, 'ship_type': ship_type(fn), 'name': s.get(k.s('Name')) or '',
                        'inv': ti})
    for c in out:
        inv = c['inv']
        c['group'] = group_of(inv, k)
        c['w'], c['h'] = inv[k.s('Width')], inv[k.s('Height')]
        c['valid'] = {(v[k.s('X')], v[k.s('Y')]) for v in inv[k.s('ValidSlotIndices')]}
        c['special'] = {(v[k.s('Index')][k.s('X')], v[k.s('Index')][k.s('Y')])
                        for v in inv.get(k.s('SpecialSlots')) or [] if isinstance(v, dict) and k.s('Index') in v
                        and ((v.get(k.s('Type')) or {}).get(k.s('InventorySpecialSlotType')) or 'TechBonus') == 'TechBonus'}
    return out


def vehicle_name(i: int, lang: str) -> str:
    keys = (gamedata.settlement() or {}).get('vehicles') or []
    return gamedata.text(keys[i], lang) if i < len(keys) and keys[i] else ''


def _tech_values(items: list, special: set) -> dict:
    """Exact values of the procedural upgrades in a grid, as the game's tooltip shows them."""
    from . import techstats
    procs = [it for it in items if it[5] == 'Technology'
             and (gamedata.item(techstats.split_id(str(it[2]).lstrip('^'))[0]) or {}).get('k') == 'proc']
    if not procs or not techstats.rules():
        return {}
    try:
        grid = techstats.Grid({(it[0], it[1]): str(it[2]).lstrip('^') for it in items if it[5] == 'Technology'},
                              set(special), {(it[0], it[1]) for it in items if it[8]})
        vals = {f'{it[0]},{it[1]}': techstats.tooltip(grid, (it[0], it[1])) for it in procs}
    except Exception:                       # never break the view over the extra values
        return {}
    return {c: v for c, v in vals.items() if v}


def describe(files: list[SaveFile], lang: str = 'es') -> dict:
    """What the Inventarios view shows (from the first file of the slot)."""
    f = files[0]
    k = f.keys
    level = level_of(f.psd(), k)
    out = []
    for c in containers(f, tech=True):
        if c['kind'] == 'shiptech':                      # ships' technology: in the Naves view
            continue
        items = []
        for it in c['inv'][k.s('Slots')]:
            x, y = _xy(it, k)
            iid, t = it[k.s('Id')], _type(it, k)
            items.append([x, y, iid, it[k.s('Amount')], it[k.s('MaxAmount')], t,
                          gamedata.resolve(iid), item_group(iid, t),
                          t == 'Technology' and (it.get(k.s('DamageFactor'), 0) > 0
                                                 or not it.get(k.s('FullyInstalled'), True))])
        out.append({'id': c['id'], 'kind': c['kind'], 'name': c['name'], 'group': c['group'],
                    'ship': c.get('ship'), 'ship_type': c.get('ship_type'), 'vehicle': c.get('vehicle'),
                    'vname': vehicle_name(c['vehicle'], lang) if 'vehicle' in c else '', 'mt': c.get('mt'),
                    'active': bool(c.get('active')), 'blocked': c.get('blocked'),
                    'w': c['w'], 'h': c['h'], 'valid': sorted(c['valid'], key=lambda v: (v[1], v[0])),
                    'items': items, 'vals': _tech_values(items, c.get('special') or set())})
    meta = {}
    for c in out:
        for it in c['items']:
            if it[6] and it[6] not in meta:
                meta[it[6]] = [value(it[2]), rank(it[2])]
    return {'level': level, 'containers': out, 'meta': meta}


# ------------------------------------------------------------------------------- editing -------
def _cell(x: int, y: int, k) -> bytes:
    return b'{"' + k('X') + b'":%d,"' % x + k('Y') + b'":%d}' % y


def _set(ib: bytes, key: bytes, val: bytes) -> bytes:
    s, e = jspan.get(ib, [key], 0)
    return ib[:s] + val + ib[e:]


class _Box:
    """One inventory being edited: its items as (bytes, parsed) pairs, in the order they are stored."""

    def __init__(self, edit: 'InvEdit', c: dict):
        k = edit.k
        self.c, self.k = c, k
        self.span = edit.idx.get(k.psd + c['path'] + [k('Slots')])
        spans = jspan.arr_items(edit.b, self.span[0])
        parsed = c['inv'][k.s('Slots')]
        if len(spans) != len(parsed):
            raise RuntimeError('inv_verify')
        self.items = [[edit.b[a:z], copy.deepcopy(p)] for (a, z), p in zip(spans, parsed)]
        self.orig_cells = {_xy(p, k) for p in parsed}     # an item may stay where the game put it
        self.touched = False

    def at(self, xy) -> int | None:
        for j, (_b, p) in enumerate(self.items):
            if _xy(p, self.k) == tuple(xy):
                return j
        return None

    def info(self, j: int) -> dict:
        p = self.items[j][1]
        k = self.k
        return {'_id': p[k.s('Id')], '_t': _type(p, k), '_max': p[k.s('MaxAmount')], '_amount': p[k.s('Amount')]}

    # every change keeps bytes and parsed value in step
    def set_xy(self, j: int, xy):
        k = self.k
        self.items[j][0] = _set(self.items[j][0], k('Index'), _cell(xy[0], xy[1], k))
        self.items[j][1][k.s('Index')] = {k.s('X'): xy[0], k.s('Y'): xy[1]}
        self.touched = True

    def set_num(self, j: int, name: str, v: int):
        k = self.k
        self.items[j][0] = _set(self.items[j][0], k(name), b'%d' % v)
        self.items[j][1][k.s(name)] = v
        self.touched = True

    def take(self, j: int) -> list:
        self.touched = True
        return self.items.pop(j)

    def put(self, entry: list):
        self.touched = True
        self.items.append(entry)

    def bytes(self) -> bytes:
        return b'[' + b','.join(b for b, _p in self.items) + b']'

    def parsed(self) -> list:
        return [p for _b, p in self.items]


def _deep_set(root: dict, path: list, value):
    """Set root[path] = value copying every container along the path (the original parse is shared)."""
    keys = [p.decode() if isinstance(p, bytes) else p for p in path]
    node = root
    for p in keys[:-1]:
        child = copy.copy(node[p])
        node[p] = child
        node = child
    node[keys[-1]] = value


class InvEdit:
    def __init__(self, f: SaveFile):
        self.f, self.k, self.b = f, f.keys, f.raw
        self.idx = jspan.index_for(self.b)
        self.level = level_of(f.psd(), self.k)
        self.cont = {c['id']: c for c in containers(f, tech=True)}
        self.boxes: dict[str, _Box] = {}
        self.names: dict[str, tuple] = {}        # cid -> (span, new bytes, path, new name)
        self.totals_before: dict = {}
        self.totals_delta: dict = {}             # what an action creates (+) or deletes (-) on purpose

    def box(self, cid: str) -> _Box:
        if cid not in self.boxes:
            self.boxes[cid] = _Box(self, self.cont[cid])
        return self.boxes[cid]

    def set_name(self, cid: str, name: str):
        c = self.cont[cid]
        path = c['path'] + [self.k('Name')]
        self.names[cid] = (self.idx.get(self.k.psd + path), jspan.dumps_value(name), path, name)

    def totals(self, boxes) -> dict:
        t: dict = {}
        for bx in boxes:
            for _b, p in bx.items:
                key = (p[self.k.s('Id')], _type(p, self.k))
                t[key] = t.get(key, 0) + p[self.k.s('Amount')]
        return t

    def patched_json(self):
        k = self.k
        j = self.f.json
        if j is None:
            return None
        j2 = dict(j)
        bc = dict(j2[k.s('BaseContext')])
        psd = dict(bc[k.s('PlayerStateData')])
        for cid, (_sp, _nb, _path, name) in self.names.items():       # renamed containers (fixed ones only)
            _deep_set(psd, self.cont[cid]['path'] + [k('Name')], name)
        for bx in self.boxes.values():
            if not bx.touched:
                continue
            _deep_set(psd, bx.c['path'] + [k('Slots')], bx.parsed())
            if bx.c.get('mirror'):
                _deep_set(psd, bx.c['mirror'] + [k('Slots')], bx.parsed())
        bc[k.s('PlayerStateData')] = psd
        j2[k.s('BaseContext')] = bc
        return j2

    def result(self) -> bytes:
        k = self.k
        touched = [bx for bx in self.boxes.values() if bx.touched]
        ed = [(bx.span[0], bx.span[1], bx.bytes()) for bx in touched] + \
             [(sp[0], sp[1], nb) for sp, nb, _p, _n in self.names.values()]
        for bx in touched:                       # the multi-tool in hand: the same grid in WeaponInventory
            if bx.c.get('mirror'):
                ms = self.idx.get(k.psd + bx.c['mirror'] + [k('Slots')])
                ed.append((ms[0], ms[1], bx.bytes()))
        ed.sort(key=lambda t: t[0], reverse=True)
        for x, y in zip(ed, ed[1:]):
            if y[1] > x[0]:
                raise RuntimeError('inv_overlap')
        out = bytearray(self.b)
        for s0, e0, r in ed:
            out[s0:e0] = r
        new = bytes(out)
        # ---- independent verification ----
        # 1) outside the rewritten arrays the document is byte-identical
        asc = sorted(ed, key=lambda t: t[0])
        pos_old, pos_new = 0, 0
        for s0, e0, r in asc:
            n = s0 - pos_old
            if new[pos_new:pos_new + n] != self.b[pos_old:s0]:
                raise RuntimeError('inv_verify_outside')
            pos_new += n + len(r)
            pos_old = e0
        if new[pos_new:] != self.b[pos_old:]:
            raise RuntimeError('inv_verify_outside')
        # 2) every rewritten array parses back to exactly the expected items
        jspan.carry_index(self.b, new, ed)
        nidx = jspan.index_for(new)
        for bx in touched:
            s, e = nidx.get(k.psd + bx.c['path'] + [k('Slots')])
            if jspan.loads(new[s:e]) != bx.parsed():
                raise RuntimeError('inv_verify')
            if bx.c.get('mirror'):
                s, e = nidx.get(k.psd + bx.c['mirror'] + [k('Slots')])
                if jspan.loads(new[s:e]) != bx.parsed():
                    raise RuntimeError('inv_verify')
            # and each item sits on a valid cell, one item per cell
            cells = [_xy(p, k) for p in bx.parsed()]
            if len(set(cells)) != len(cells) or any(c not in bx.c['valid'] and c not in bx.orig_cells for c in cells):
                raise RuntimeError('inv_verify_cells')
        for sp, nb, path, name in self.names.values():
            s, e = nidx.get(k.psd + path)
            if jspan.loads(new[s:e]) != name:
                raise RuntimeError('inv_verify')
        # 3) nothing was created or lost
        if touched:                                  # every box the action read
            want = dict(self.totals_before)
            for key, d in self.totals_delta.items():
                want[key] = want.get(key, 0) + d
            want = {key: v for key, v in want.items() if v}
            got = {key: v for key, v in self.totals(self.boxes.values()).items() if v}   # empty charges count as 0
            if got != want:
                raise RuntimeError('inv_verify_totals')
        return new


# ------------------------------------------------------------------------------- actions -------
def _label(c: dict) -> dict:
    n = c['ship'] if 'ship' in c else c['mt'] if 'mt' in c else c.get('vehicle') or 0
    out = {'inv': c['id'], 'kind': c['kind'], 'name': c['name'], 'n': n + 1}
    if 'vehicle' in c:
        out['vehicle'] = c['vehicle']
    return out


def _fits_amount(info: dict, mx: int | None) -> bool:
    return mx is not None and info['_amount'] <= mx


def _move(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    src, dst = str(params.get('src')), str(params.get('dst'))
    try:
        a = tuple(int(v) for v in params.get('from'))
        b = tuple(int(v) for v in params.get('to'))
    except (TypeError, ValueError):
        a = b = None
    if src not in ie.cont or dst not in ie.cont or not a or not b or (src == dst and a == b):
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    sb, db = ie.box(src), ie.box(dst)
    ie.totals_before = ie.totals({sb, db})
    j = sb.at(a)
    if j is None:
        plan.messages.append(msg('block', 'inv_move_empty'))
        return False
    if b not in db.c['valid']:
        plan.messages.append(msg('block', 'inv_move_locked'))
        return False
    it = sb.info(j)
    t = db.at(b)
    other = db.info(t) if t is not None else None
    cross = src != dst
    lab = {'item': gamedata.resolve(it['_id']) or it['_id'], 'frm': f'{a[0] + 1}·{a[1] + 1}', 'to': f'{b[0] + 1}·{b[1] + 1}',
           'src': _label(sb.c), 'dst': _label(db.c)}
    if cross and (it['_t'] not in TYPE_ORDER or (other and other['_t'] not in TYPE_ORDER)):
        plan.messages.append(msg('block', 'inv_tech_cross', **lab))
        return False

    # same item on the target cell -> merge up to the target's limit
    if other and other['_id'] == it['_id'] and other['_t'] == it['_t'] and it['_t'] in TYPE_ORDER:
        room = other['_max'] - other['_amount']
        if room <= 0:
            plan.messages.append(msg('block', 'inv_stack_full', mx=other['_max'], **lab))
            return False
        n = min(room, it['_amount'])
        db.set_num(t, 'Amount', other['_amount'] + n)
        if n == it['_amount']:
            sb.take(j)
            plan.messages.append(msg('info', 'inv_merge_what', n=n, **lab))
        else:
            sb.set_num(j, 'Amount', it['_amount'] - n)
            plan.messages.append(msg('info', 'inv_merge_part', n=n, left=it['_amount'] - n, mx=other['_max'], **lab))
        return True

    if not cross:
        sb.set_xy(j, b)
        if t is not None:
            sb.set_xy(t, a)
        plan.messages.append(msg('info', 'inv_swap_what' if t is not None else 'inv_move_what',
                                 other=(gamedata.resolve(other['_id']) or other['_id']) if other else '', **lab))
        return True

    mx = max_in(it, db.c['group'], sb.c['group'], ie.level)
    if mx is None:
        plan.messages.append(msg('block', 'inv_limit_unknown', **lab))
        return False
    if t is None:
        if it['_amount'] <= mx:                       # the whole stack goes
            entry = sb.take(j)
            db.put(entry)
            jj = len(db.items) - 1
            db.set_xy(jj, b)
            if mx != it['_max']:
                db.set_num(jj, 'MaxAmount', mx)
            plan.messages.append(msg('info', 'inv_move_what', mx=mx, **lab))
        else:                                         # only what fits; the rest stays
            entry = [sb.items[j][0], copy.deepcopy(sb.items[j][1])]
            sb.set_num(j, 'Amount', it['_amount'] - mx)
            db.put(entry)
            jj = len(db.items) - 1
            db.set_xy(jj, b)
            db.set_num(jj, 'Amount', mx)
            db.set_num(jj, 'MaxAmount', mx)
            plan.messages.append(msg('info', 'inv_move_part', n=mx, left=it['_amount'] - mx, mx=mx, **lab))
        return True

    # a different item on the target cell -> swap, both must fit where they go
    mx_back = max_in(other, sb.c['group'], db.c['group'], ie.level)
    if mx_back is None or not _fits_amount(it, mx) or not _fits_amount(other, mx_back):
        plan.messages.append(msg('block', 'inv_swap_over', other=gamedata.resolve(other['_id']) or other['_id'], mx=mx, mx2=mx_back or 0, **lab))
        return False
    e1, e2 = sb.items[j], db.items[t]
    sb.items[j], db.items[t] = e2, e1
    sb.touched = db.touched = True
    sb.set_xy(j, a)
    db.set_xy(t, b)
    if mx != it['_max']:
        db.set_num(t, 'MaxAmount', mx)
    if mx_back != other['_max']:
        sb.set_num(j, 'MaxAmount', mx_back)
    plan.messages.append(msg('info', 'inv_swap_what', other=gamedata.resolve(other['_id']) or other['_id'], **lab))
    return True


def _fold(v: str) -> str:
    import unicodedata
    return ''.join(ch for ch in unicodedata.normalize('NFD', (v or '').casefold()) if not unicodedata.combining(ch))


def _merge_stacks(bx, k, loose) -> tuple[list, int]:
    """Merge equal stacks (same id, type and limit), keeping the first stack of each. -> (stacks, merged)."""
    merged, firsts = [], {}
    for e in loose:
        p = e[1]
        firsts.setdefault((p[k.s('Id')], _type(p, k), p[k.s('MaxAmount')]), []).append(e)
    n = 0
    for key, group in firsts.items():
        total, mx = sum(p[k.s('Amount')] for _b, p in group), key[2]
        need = max(1, -(-total // mx)) if mx > 0 else len(group)
        if mx <= 0 or need >= len(group):
            merged.extend(group)
            continue
        n += len(group) - need
        for i, e in enumerate(group[:need]):
            amt = min(mx, total - i * mx)
            if e[1][k.s('Amount')] != amt:
                e[0] = _set(e[0], k('Amount'), b'%d' % amt)
                e[1][k.s('Amount')] = amt
            merged.append(e)
    return merged, n


def _free_cells(bx, k, taken) -> list:
    return [c for c in sorted(bx.c['valid'], key=lambda v: (v[1], v[0])) if c not in taken and c not in bx.c['special']]


def _sort(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    k = ie.k
    cid, key = str(params.get('inv')), str(params.get('key') or 'name')
    desc = str(params.get('dir') or 'asc') == 'desc'
    lang = str(params.get('lang') or 'es')
    if cid not in ie.cont or key not in SORT_KEYS:
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    bx = ie.box(cid)
    ie.totals_before = ie.totals({bx})
    fixed = [e for e in bx.items if _type(e[1], k) not in TYPE_ORDER or _xy(e[1], k) in bx.c['special']]
    loose = [e for e in bx.items if e not in fixed]
    before = [(_xy(p, k), p[k.s('Id')], p[k.s('Amount')]) for _b, p in loose]
    merged, n = _merge_stacks(bx, k, loose)

    def name_of(p):
        iid = p[k.s('Id')]
        return _fold(gamedata.name(iid, lang) or gamedata.resolve(iid) or '\uffff')

    known = [e for e in merged if gamedata.item(e[1][k.s('Id')]) is not None]
    unknown = [e for e in merged if e not in known]            # undecodable items always go last
    if key == 'name':
        known.sort(key=lambda e: (name_of(e[1]), -e[1][k.s('Amount')]), reverse=desc)
    else:
        main = rank if key == 'rarity' else value
        known.sort(key=lambda e: ((-1 if desc else 1) * main(e[1][k.s('Id')]), name_of(e[1]), -e[1][k.s('Amount')]))
    merged = known + unknown
    shield = shield_chargers(ie, bx.c)
    if shield:            # ship cargo: what recharges the shield goes first, whatever the order (for a surprise fight)
        merged = [e for e in merged if e[1][k.s('Id')].lstrip('^') in shield] + \
                 [e for e in merged if e[1][k.s('Id')].lstrip('^') not in shield]
    taken = {_xy(p, k) for _b, p in fixed}
    free = _free_cells(bx, k, taken)
    if len(merged) > len(free):            # cannot happen: merging never adds stacks
        raise RuntimeError('inv_verify')
    for e, xy in zip(merged, free):
        if _xy(e[1], k) != xy:
            e[0] = _set(e[0], k('Index'), _cell(xy[0], xy[1], k))
            e[1][k.s('Index')] = {k.s('X'): xy[0], k.s('Y'): xy[1]}
    bx.items = fixed + merged
    after = [(_xy(p, k), p[k.s('Id')], p[k.s('Amount')]) for _b, p in merged]
    if sorted(before) == sorted(after):
        plan.messages.append(msg('block', 'inv_nothing', inv=_label(bx.c)))
        return False
    bx.touched = True
    plan.messages.append(msg('info', 'inv_sort_what', merged=n, items=len(merged), key=key, dir='desc' if desc else 'asc',
                             inv=_label(bx.c)))
    return True


def shield_chargers(ie: 'InvEdit', c: dict) -> set:
    """Items that recharge the shield installed on a ship (the game's ChargeBy of its shield technology:
    sodium, sodium nitrate, Starshield Battery; living ships and sentinel ships use their own). Empty for other inventories."""
    if c.get('kind') != 'ship':
        return set()
    k = ie.k
    ship = ie.f.psd()[k.s('ShipOwnership')][c['ship']]
    out: set = set()
    for g in ('Inventory_TechOnly', 'Inventory'):        # older ships keep their technology in the cargo grid
        for x in (ship.get(k.s(g)) or {}).get(k.s('Slots')) or []:
            it = gamedata.item(str(x.get(k.s('Id')) or '').lstrip('^')) or {}
            if it.get('k') == 'tech' and it.get('stat') == 'Ship_Armour_Shield':
                out.update(it.get('charge_by') or ())
    return out


GATHER_TARGETS = ('chest', 'corvette', 'freighter', 'vehicle')   # cargo inventories; not exosuit or ships
SCOPES = {'chests': ('chest',), 'storage': ('chest', 'freighter', 'corvette'),
          'all': ('chest', 'freighter', 'corvette', 'suit', 'ship', 'vehicle')}


def gather_sources(ie_or_cont, cid: str, scope: str) -> list[str]:
    cont = ie_or_cont.cont if hasattr(ie_or_cont, 'cont') else ie_or_cont
    kinds = SCOPES.get(scope, SCOPES['storage'])       # storage first; exosuit and ships last
    return [x for kind in kinds for x, c in cont.items() if x != cid and c['kind'] == kind]


def _transfer(ie: InvEdit, tb: _Box, sb: _Box, entry: list, free: list) -> tuple[str, int]:
    """Move one stack of sb into tb: top up equal stacks, then free cells (with tb's stack limit).
    -> ('all' | 'part' | 'none' | 'unknown', units moved). `free` is consumed."""
    k = ie.k
    p = entry[1]
    j = sb.items.index(entry)
    info = sb.info(j)
    mx = max_in(info, tb.c['group'], sb.c['group'], ie.level)
    if mx is None:
        return 'unknown', 0
    amount = info['_amount']
    for t, (_b, tp) in enumerate(tb.items):            # top up equal stacks first
        if amount <= 0:
            break
        if tp[k.s('Id')] == info['_id'] and _type(tp, k) == info['_t'] and tp[k.s('Amount')] < tp[k.s('MaxAmount')]:
            n = min(amount, tp[k.s('MaxAmount')] - tp[k.s('Amount')])
            tb.set_num(t, 'Amount', tp[k.s('Amount')] + n)
            amount -= n
    while amount > 0 and free:                          # then new stacks in free cells
        n = min(amount, mx)
        tb.put([entry[0], copy.deepcopy(p)])
        jj = len(tb.items) - 1
        tb.set_xy(jj, free.pop(0))
        tb.set_num(jj, 'Amount', n)
        if mx != info['_max']:
            tb.set_num(jj, 'MaxAmount', mx)
        amount -= n
    j = sb.items.index(entry)
    if amount == info['_amount']:
        return 'none', 0
    if amount == 0:
        sb.take(j)
        return 'all', info['_amount']
    sb.set_num(j, 'Amount', amount)
    return 'part', info['_amount'] - amount


def _gather(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    k = ie.k
    cid, grp, scope = str(params.get('inv')), str(params.get('group')), str(params.get('from') or 'storage')
    ids = params.get('ids')                     # optional: only these items (real base IDs) of the group
    if ids is not None:
        ids = {str(x) for x in ids} if isinstance(ids, list) and len(ids) <= 2000 else set()
    if (cid not in ie.cont or ie.cont[cid]['kind'] not in GATHER_TARGETS or scope not in SCOPES
            or not (grp in GROUPS or (grp == 'any' and ids)) or ids == set()):
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    tb = ie.box(cid)
    srcs = [ie.box(x) for x in gather_sources(ie, cid, scope)]
    ie.totals_before = ie.totals({tb, *srcs})
    taken = {_xy(p, k) for _b, p in tb.items}
    free = _free_cells(tb, k, taken)
    moved = units = left = unknown = 0
    for sb in srcs:
        for entry in list(sb.items):
            p = entry[1]
            g = item_group(p[k.s('Id')], _type(p, k))
            if g is None or (grp != 'any' and g != grp) or (ids is not None and gamedata.resolve(p[k.s('Id')]) not in ids):
                continue
            res, n = _transfer(ie, tb, sb, entry, free)
            units += n
            moved += res == 'all'
            left += res in ('part', 'none')
            unknown += res == 'unknown'
    if not units:
        plan.messages.append(msg('block', 'inv_gather_full' if left else 'inv_gather_none', group=grp,
                                 inv=_label(tb.c)))
        return False
    plan.messages.append(msg('info', 'inv_gather_what', group=grp, n=moved, units=units, inv=_label(tb.c)))
    if left:
        plan.messages.append(msg('warn', 'inv_gather_left', n=left, inv=_label(tb.c)))
    if unknown:
        plan.messages.append(msg('warn', 'inv_gather_unknown', n=unknown))
    return True


def _gather_id(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    """Right click on an item -> "move here all": every stack with exactly that ID (procedural items
    included, seed and all) from all the other inventories, into the clicked one; the game's stack limits."""
    k = ie.k
    cid = str(params.get('inv'))
    try:
        cell = tuple(int(v) for v in params.get('cell'))
    except (TypeError, ValueError):
        cell = None
    if cid not in ie.cont or ie.cont[cid]['kind'] not in SCOPES['all'] or not cell or len(cell) != 2:
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    tb = ie.box(cid)
    here = next((p for _b, p in tb.items if _xy(p, k) == cell), None)
    if here is None or _type(here, k) not in TYPE_ORDER:
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    iid = here[k.s('Id')]
    srcs = [ie.box(x) for x in gather_sources(ie, cid, 'all')]
    ie.totals_before = ie.totals({tb, *srcs})
    taken = {_xy(p, k) for _b, p in tb.items}
    free = _free_cells(tb, k, taken)
    moved = units = left = unknown = 0
    for sb in srcs:
        for entry in list(sb.items):
            if entry[1][k.s('Id')] != iid or _type(entry[1], k) not in TYPE_ORDER:
                continue
            res, n = _transfer(ie, tb, sb, entry, free)
            units += n
            moved += res == 'all'
            left += res in ('part', 'none')
            unknown += res == 'unknown'
    item = str(iid).lstrip('^')
    if not units:
        plan.messages.append(msg('block', 'inv_gather_item_full' if left else 'inv_gather_item_none', item=item,
                                 inv=_label(tb.c)))
        return False
    plan.messages.append(msg('info', 'inv_gather_item_what', item=item, n=moved, units=units, inv=_label(tb.c)))
    if left:
        plan.messages.append(msg('warn', 'inv_gather_left', n=left, inv=_label(tb.c)))
    if unknown:
        plan.messages.append(msg('warn', 'inv_gather_unknown', n=unknown))
    return True


def _rename(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    cid = str(params.get('inv'))
    name = ' '.join(str(params.get('name') or '').split())[:NAME_MAX]
    if cid not in ie.cont or ie.cont[cid]['kind'] != 'chest' or any(ord(ch) < 32 for ch in name):
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    name = name or 'BLD_STORAGE_NAME'                   # empty: back to the game's default name
    if name == (ie.cont[cid]['name'] or 'BLD_STORAGE_NAME'):
        plan.messages.append(msg('block', 'inv_rename_same'))
        return False
    ie.set_name(cid, name)
    if name == 'BLD_STORAGE_NAME':
        plan.messages.append(msg('info', 'inv_rename_default', inv=_label(ie.cont[cid])))
    else:
        plan.messages.append(msg('info', 'inv_rename_what', inv=_label(ie.cont[cid]), new=name))
    return True


def _cells_param(v) -> list[tuple[int, int]] | None:
    try:
        out = [tuple(int(a) for a in c) for c in v]
    except (TypeError, ValueError):
        return None
    return out if out and all(len(c) == 2 for c in out) and len(out) <= 200 and len(set(out)) == len(out) else None


def _move_many(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    """Selected cells of one inventory -> another inventory (equal stacks first, then free cells)."""
    k = ie.k
    src, dst, cells = str(params.get('src')), str(params.get('dst')), _cells_param(params.get('cells'))
    if src not in ie.cont or dst not in ie.cont or src == dst or not cells:
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    sb, tb = ie.box(src), ie.box(dst)
    ie.totals_before = ie.totals({sb, tb})
    entries = [sb.items[j] for j in (sb.at(c) for c in cells) if j is not None]
    if len(entries) != len(cells):
        plan.messages.append(msg('block', 'inv_move_empty'))
        return False
    free = _free_cells(tb, k, {_xy(p, k) for _b, p in tb.items})
    moved = units = left = unknown = tech = 0
    for entry in entries:
        if _type(entry[1], k) not in TYPE_ORDER:
            tech += 1
            continue
        res, n = _transfer(ie, tb, sb, entry, free)
        units += n
        moved += res == 'all'
        left += res in ('part', 'none')
        unknown += res == 'unknown'
    if not units:
        plan.messages.append(msg('block', 'inv_many_none', dst=_label(tb.c)))
        return False
    plan.messages.append(msg('info', 'inv_many_what', n=moved, units=units, src=_label(sb.c), dst=_label(tb.c)))
    if left:
        plan.messages.append(msg('warn', 'inv_gather_left', n=left, inv=_label(tb.c)))
    if unknown:
        plan.messages.append(msg('warn', 'inv_gather_unknown', n=unknown))
    if tech:
        plan.messages.append(msg('warn', 'inv_tech_cross'))
    return True


def _item(ie: InvEdit, plan: Plan, params: dict, tech: bool = False):
    """(box, index, info, cell) of the one item an item action works on, or None (message added).
    tech=True: installed technology is accepted too (uninstalling it from a technology grid)."""
    cid = str(params.get('inv'))
    cells = _cells_param([params.get('cell')])
    if cid not in ie.cont or not cells:
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return None
    bx = ie.box(cid)
    ie.totals_before = ie.totals({bx})
    j = bx.at(cells[0])
    if j is None:
        plan.messages.append(msg('block', 'inv_move_empty'))
        return None
    info = bx.info(j)
    if info['_t'] not in TYPE_ORDER and not (tech and bx.c['kind'] in TECH_KINDS):
        plan.messages.append(msg('block', 'inv_item_tech'))
        return None
    return bx, j, info, cells[0]


def _lab_item(bx, info, cell) -> dict:
    return {'item': gamedata.resolve(info['_id']) or info['_id'], 'inv': _label(bx.c), 'at': f'{cell[0] + 1}·{cell[1] + 1}'}


def _first_free(bx, k):
    free = _free_cells(bx, k, {_xy(p, k) for _b, p in bx.items})
    return free[0] if free else None


def _split(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    got = _item(ie, plan, params)
    if not got:
        return False
    bx, j, info, cell = got
    try:
        n = int(params.get('n'))
    except (TypeError, ValueError):
        n = 0
    if info['_amount'] < 2 or not 1 <= n < info['_amount']:
        plan.messages.append(msg('block', 'inv_split_bad', mx=info['_amount'] - 1, **_lab_item(bx, info, cell)))
        return False
    to = _first_free(bx, ie.k)
    if to is None:
        plan.messages.append(msg('block', 'inv_no_room', **_lab_item(bx, info, cell)))
        return False
    entry = bx.items[j]
    bx.set_num(j, 'Amount', info['_amount'] - n)
    bx.put([entry[0], copy.deepcopy(entry[1])])
    jj = len(bx.items) - 1
    bx.set_xy(jj, to)
    bx.set_num(jj, 'Amount', n)
    plan.messages.append(msg('info', 'inv_split_what', n=n, left=info['_amount'] - n,
                             to=f'{to[0] + 1}·{to[1] + 1}', **_lab_item(bx, info, cell)))
    return True


def _fill(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    got = _item(ie, plan, params)
    if not got:
        return False
    bx, j, info, cell = got
    add = info['_max'] - info['_amount']
    if add <= 0:
        plan.messages.append(msg('block', 'inv_stack_full', mx=info['_max'], dst=_label(bx.c), **_lab_item(bx, info, cell)))
        return False
    bx.set_num(j, 'Amount', info['_max'])
    ie.totals_delta = {(info['_id'], info['_t']): add}
    plan.messages.append(msg('warn', 'inv_fill_what', n=add, mx=info['_max'], **_lab_item(bx, info, cell)))
    return True


def _dup(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    got = _item(ie, plan, params)
    if not got:
        return False
    bx, j, info, cell = got
    to = _first_free(bx, ie.k)
    if to is None:
        plan.messages.append(msg('block', 'inv_no_room', **_lab_item(bx, info, cell)))
        return False
    entry = bx.items[j]
    bx.put([entry[0], copy.deepcopy(entry[1])])
    bx.set_xy(len(bx.items) - 1, to)
    ie.totals_delta = {(info['_id'], info['_t']): info['_amount']}
    plan.messages.append(msg('warn', 'inv_dup_what', n=info['_amount'], to=f'{to[0] + 1}·{to[1] + 1}',
                             **_lab_item(bx, info, cell)))
    return True


def _delete(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    got = _item(ie, plan, params, tech=True)
    if not got:
        return False
    bx, j, info, cell = got
    if info['_t'] not in TYPE_ORDER:
        it = gamedata.item(info['_id']) or {}
        if it.get('core'):                           # the game does not let these be dismantled either
            plan.messages.append(msg('block', 'inv_delete_core', **_lab_item(bx, info, cell)))
            return False
        if bx.c['kind'] == 'shiptech' and _ship_kind(bx.c) is None:   # corvette tech comes from its parts
            plan.messages.append(msg('block', 'inv_delete_corvette', **_lab_item(bx, info, cell)))
            return False
        bx.take(j)
        ie.totals_delta = {(info['_id'], info['_t']): -info['_amount']}
        plan.messages.append(msg('warn', 'inv_uninstall_what', **_lab_item(bx, info, cell)))
        return True
    bx.take(j)
    ie.totals_delta = {(info['_id'], info['_t']): -info['_amount']}
    plan.messages.append(msg('warn', 'inv_delete_what', n=info['_amount'], **_lab_item(bx, info, cell)))
    return True


def _delete_many(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    """Selected cells of one or more inventories -> deleted (the user confirms the preview first). Like one by one:
    technology the game will not dismantle and a corvette's technology stay where they are."""
    sel = params.get('sel')
    if not isinstance(sel, dict) or not sel or any(str(c) not in ie.cont for c in sel):
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    boxes = [(ie.box(str(c)), _cells_param(cells)) for c, cells in sel.items()]
    ie.totals_before = ie.totals({bx for bx, _ in boxes})
    delta, done, kept, where = {}, 0, [], []
    for bx, cells in boxes:
        n0 = done
        for cell in cells:
            j = bx.at(cell)
            if j is None:
                continue
            info = bx.info(j)
            if info['_t'] not in TYPE_ORDER and ((gamedata.item(info['_id']) or {}).get('core')
                                                 or (bx.c['kind'] == 'shiptech' and _ship_kind(bx.c) is None)):
                kept.append(gamedata.resolve(info['_id']) or info['_id'])
                continue
            bx.take(j)
            key = (info['_id'], info['_t'])
            delta[key] = delta.get(key, 0) - info['_amount']
            done += 1
        if done > n0:
            where.append(_label(bx.c))
    if not done:
        plan.messages.append(msg('block', 'inv_delmany_none'))
        return False
    ie.totals_delta = delta
    plan.messages.append(msg('danger', 'inv_delmany_what', count=done, invs=where))
    if kept:
        plan.messages.append(msg('warn', 'inv_delmany_kept', count=len(kept), ids=kept))
    return True


def _repair(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    """One damaged technology back to working order, as the game's repair leaves it
    (DamageFactor 0, fully installed); nothing else of the item changes."""
    got = _item(ie, plan, params, tech=True)
    if not got:
        return False
    bx, j, info, cell = got
    k, p = ie.k, bx.items[j][1]
    dmg = p.get(k.s('DamageFactor'), 0) > 0
    part = not p.get(k.s('FullyInstalled'), True)
    if info['_t'] != 'Technology' or not (dmg or part):
        plan.messages.append(msg('block', 'inv_repair_none', **_lab_item(bx, info, cell)))
        return False
    if dmg:
        bx.items[j][0] = _set(bx.items[j][0], k('DamageFactor'), b'0.0')
        p[k.s('DamageFactor')] = 0.0
    if part:
        bx.items[j][0] = _set(bx.items[j][0], k('FullyInstalled'), b'true')
        p[k.s('FullyInstalled')] = True
    bx.touched = True
    plan.messages.append(msg('info', 'inv_repair_what', **_lab_item(bx, info, cell)))
    return True


# ------------------------------------------------------------------- adding new items -------
# Which items an inventory accepts, following the game's own rules:
#   storage / cargo / exosuit general: substances and products only (no installed technology);
#   ship technology grids: technology of that kind of ship and its upgrade modules -
#     regular ships: Ship / AllShips / AllShipsExceptAlien tech and UP_ modules (sentinel ships also RobotShip);
#     living ships: AlienShip / AllShips tech and UA_ modules (regular modules do not fit a living ship);
#     corvettes: their technology is built with corvette parts, so it is not added here.
SHIP_TECH = {'regular': {'Ship', 'AllShips', 'AllShipsExceptAlien'}, 'living': {'AlienShip', 'AllShips'}}
SHIP_MODS = {'regular': ('UP_',), 'living': ('UA_',)}
MODS_PER_FAMILY = 3                                 # the game takes up to three modules of one kind


def _ship_kind(c: dict) -> str | None:
    t = c.get('ship_type')
    return None if t == 'corvette' else 'living' if t == 'living' else 'regular'


def _family(rid: str) -> str:
    return rid.rstrip('01234X')


# exosuit / freighter / multi-tool grids: (technology categories of the table, stat prefix of their modules)
GRID_TECH = {'suittech': ({'Suit'}, 'Suit_'), 'freightertech': ({'Freighter'}, 'Freighter_'),
             'mttech': ({'Weapon'}, 'Weapon_')}


@lru_cache(maxsize=1)
def _templates() -> frozenset:
    """Technology entries that only serve as templates of upgrade modules (T_SHIPJUMP...): not real items."""
    d = gamedata.load() or {}
    return frozenset(v.get('template') for v in (d.get('items') or {}).values() if v.get('k') == 'proc' and v.get('template'))


def accepts(c: dict, rid: str) -> str | None:
    """None if inventory c accepts item rid (a real base id), else the reason code."""
    it = gamedata.item(rid)
    if not it or (it['k'] == 'tech' and rid in _templates()):
        return 'inv_add_unknown'
    kind = it['k']
    if c['kind'] not in TECH_KINDS:
        return None if kind in ('substance', 'product') else 'inv_add_not_cargo'
    if c['kind'] in GRID_TECH:
        cats, prefix = GRID_TECH[c['kind']]
        if kind == 'tech':
            return None if it.get('cat') in cats else 'inv_add_wrong_grid'
        if kind == 'proc':
            stats = it.get('stats') or []
            return None if stats and stats[0].startswith(prefix) else 'inv_add_wrong_grid'
        return 'inv_add_not_tech'
    sk = _ship_kind(c)
    if sk is None:
        return 'inv_add_corvette'
    if kind == 'tech':
        cats = SHIP_TECH[sk] | ({'RobotShip'} if c.get('ship_type') == 'interceptor' else set())
        return None if it.get('cat') in cats else 'inv_add_wrong_ship'
    if kind == 'proc':
        stats = it.get('stats') or []
        ok = rid.startswith(SHIP_MODS[sk]) and stats and stats[0].startswith('Ship_')
        return None if ok else 'inv_add_wrong_ship'
    return 'inv_add_not_tech'


def tech_conflict(here: list[str], rid: str, roles: bool = True) -> tuple[str, str] | None:
    """Game rules for a technology grid: one of each technology, three modules per kind; on ships also one
    technology per role (e.g. one hyperdrive)."""
    it = gamedata.item(rid) or {}
    if it.get('k') == 'tech':
        if rid in here:
            return 'inv_add_dup_tech', rid
        for h in (here if roles else []):
            o = gamedata.item(h) or {}
            if it.get('stat') and o.get('k') == 'tech' and o.get('stat') == it['stat']:
                return 'inv_add_same_role', h
    if it.get('k') == 'proc' and sum(_family(h) == _family(rid) for h in here) >= MODS_PER_FAMILY:
        return 'inv_add_mods_full', ''
    return None


def addable(files: list[SaveFile], cid: str) -> dict:
    """Items that can be added to inventory cid: {'items': [[rid, kind, max]], 'level': ...}."""
    f = files[0]
    c = {x['id']: x for x in containers(f, tech=True)}.get(cid)
    if not c:
        raise LookupError(cid)
    k = f.keys
    level = level_of(f.psd(), k)
    here = [gamedata.resolve(p[k.s('Id')]) or '' for p in c['inv'][k.s('Slots')]]
    out = []
    for rid, it in gamedata.load()['items'].items():
        if accepts(c, rid) is None and not (c['kind'] in TECH_KINDS and tech_conflict(here, rid, c['kind'] == 'shiptech')):
            out.append([rid, it['k'], _new_max(c, rid, level)])
    return {'items': out, 'level': level, 'kind': c['kind']}


def _new_max(c: dict, rid: str, level: str | None) -> int | None:
    it = gamedata.item(rid) or {}
    if c['kind'] in TECH_KINDS:
        return int(it.get('charge_amount') or 0) if it.get('charge') else 100
    t = 'Substance' if it.get('k') == 'substance' else 'Product'
    sizes = gamedata.stack_sizes(level) if level else None
    m = int(it.get('stack') or 0)
    if m <= 0:
        return 1
    if not sizes or c['group'] not in sizes[t]:
        return None
    return min(sizes['cap'][t], sizes[t][c['group']] * m)


def _template(ie: InvEdit) -> bytes | None:
    """Bytes of an existing slot item of this save, used as the shape of a new one (same fields as the game)."""
    for cid in sorted(ie.boxes, key=lambda x: x not in ie.boxes) + [x for x in ie.cont if x not in ie.boxes]:
        for b, p in ie.box(cid).items:
            if {ie.k.s('Type'), ie.k.s('Id'), ie.k.s('Amount'), ie.k.s('MaxAmount'), ie.k.s('Index')} <= set(p):
                return b
    return None


def _set_path(ib: bytes, path: list, val: bytes) -> bytes:
    s, e = jspan.get(ib, path, 0)
    return ib[:s] + val + ib[e:]


def _add(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    k = ie.k
    cid, rid = str(params.get('inv')), str(params.get('id') or '').lstrip('^')
    cells = _cells_param([params.get('cell')])
    if cid not in ie.cont or not cells or not rid:
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    c, cell = ie.cont[cid], cells[0]
    why = accepts(c, rid)
    lab = {'item': rid, 'inv': _label(c), 'at': f'{cell[0] + 1}·{cell[1] + 1}'}
    if why:
        plan.messages.append(msg('block', why, **lab))
        return False
    bx = ie.box(cid)
    if cell not in c['valid']:
        plan.messages.append(msg('block', 'inv_move_locked'))
        return False
    if bx.at(cell) is not None:
        plan.messages.append(msg('block', 'inv_add_taken', **lab))
        return False
    it = gamedata.item(rid)
    mx = _new_max(c, rid, ie.level)
    if mx is None:
        plan.messages.append(msg('block', 'inv_limit_unknown', dst=_label(c), **lab))
        return False
    if c['kind'] in TECH_KINDS:
        here = [gamedata.resolve(p[k.s('Id')]) or '' for _b, p in bx.items]
        bad = tech_conflict(here, rid, c['kind'] == 'shiptech')          # e.g. a hyperdrive when the ship already has its own (Crimson Core)
        if bad:
            plan.messages.append(msg('block', bad[0], other=bad[1], n=MODS_PER_FAMILY, **lab))
            return False
        amount = mx if it.get('charge') else -1
        t = 'Technology'
        iid = '^' + rid + ('#%05d' % secrets.randbelow(100000) if it['k'] == 'proc' else '')
    else:
        t = 'Substance' if it['k'] == 'substance' else 'Product'
        try:
            amount = int(params.get('n') or mx)
        except (TypeError, ValueError):
            amount = 0
        if not 1 <= amount <= mx:
            plan.messages.append(msg('block', 'inv_add_amount', mx=mx, **lab))
            return False
        iid = '^' + rid
    tpl = _template(ie)
    if tpl is None:
        plan.messages.append(msg('block', 'inv_add_no_template'))
        return False
    ie.totals_before = ie.totals(ie.boxes.values())     # every box read (the template search opens some)
    ib = _set_path(tpl, [k('Type'), k('InventoryType')], jspan.dumps_value(t))
    ib = _set_path(ib, [k('Id')], jspan.dumps_value(iid))
    ib = _set_path(ib, [k('Amount')], b'%d' % amount)
    ib = _set_path(ib, [k('MaxAmount')], b'%d' % mx)
    ib = _set_path(ib, [k('Index')], _cell(cell[0], cell[1], k))
    p = jspan.loads(ib)
    if k.s('DamageFactor') in p:
        ib = _set_path(ib, [k('DamageFactor')], b'0.0')
    if k.s('FullyInstalled') in p:
        ib = _set_path(ib, [k('FullyInstalled')], b'true')
    if k.s('AddedAutomatically') in p:
        ib = _set_path(ib, [k('AddedAutomatically')], b'false')
    p = jspan.loads(ib)
    bx.put([ib, p])
    ie.totals_delta = {(iid, t): amount}
    plan.messages.append(msg('warn', 'inv_add_what', n=amount if amount > 0 else 1, tech=t == 'Technology', **lab))
    return True


def _arrange(ie: InvEdit, plan: Plan, params: dict, fname: str) -> bool:
    """Several pieces of one grid to new cells at once (auto-sort). Nothing is created or removed: every
    source cell must hold a piece, every target must be a usable cell, and no two pieces end on one cell."""
    cid = str(params.get('inv'))
    moves = params.get('moves') or []
    if cid not in ie.cont or not isinstance(moves, list) or not moves or len(moves) > 200:
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    try:
        pairs = [(tuple(int(a) for a in m[0]), tuple(int(a) for a in m[1])) for m in moves]
    except (TypeError, ValueError, IndexError):
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    bx = ie.box(cid)
    ie.totals_before = ie.totals({bx})
    srcs = [a for a, _b in pairs]
    if len(set(srcs)) != len(srcs) or len({b for _a, b in pairs}) != len(pairs):
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    idx = [bx.at(a) for a in srcs]
    if any(j is None for j in idx):
        plan.messages.append(msg('block', 'inv_move_empty'))
        return False
    if any(b not in bx.c['valid'] for _a, b in pairs):
        plan.messages.append(msg('block', 'inv_move_locked'))
        return False
    stay = {_xy(p, ie.k) for j, (_b, p) in enumerate(bx.items) if j not in idx}
    if any(b in stay for _a, b in pairs):
        plan.messages.append(msg('block', 'inv_move_invalid'))
        return False
    for j, (_a, b) in zip(idx, pairs):
        bx.set_xy(j, b)
    plan.messages.append(msg('info', 'inv_arrange_what', n=len(pairs), inv=_label(bx.c)))
    return True


ACTIONS = {'arrange': _arrange, 'move': _move, 'sort': _sort, 'gather': _gather, 'gather_id': _gather_id, 'rename': _rename, 'move_many': _move_many,
           'split': _split, 'fill': _fill, 'dup': _dup, 'delete': _delete, 'delete_many': _delete_many, 'add': _add,
           'repair': _repair}


def plan_inventory(folder: SaveFolder, slot: int, action: str, params: dict | None = None,
                   files: list[SaveFile] | None = None) -> Plan:
    params = params or {}
    plan = Plan('inv_' + action, folder.path, staged=files is not None)
    files = files if files is not None else load_slot(folder, slot)
    _common_checks(plan, files, check_game=not plan.staged)
    if action not in ACTIONS:
        raise LookupError(action)
    byid = {c['id']: c for c in containers(files[0], tech=True)}
    ids = [str(params.get(x)) for x in ('src', 'dst', 'inv') if params.get(x) is not None]
    if action == 'delete_many' and isinstance(params.get('sel'), dict):
        ids += [str(c) for c in params['sel'] if str(c) in byid]
    if action == 'gather' and ids and ids[0] in byid:
        ids += gather_sources(byid, ids[0], str(params.get('from') or 'storage'))
    elif action == 'gather_id' and ids and ids[0] in byid:
        ids += gather_sources(byid, ids[0], 'all')
    # the edit is described by cells, so it goes to every file that holds the same inventories; when the
    # autosave (odd file) differs from the manual save (even file: ship exit, save point, beacon), only the
    # manual save is edited and the autosave is left as it was
    def same(a, b):
        norm = lambda v: sorted(json.dumps(x, sort_keys=True) for x in v) if isinstance(v, list) else v
        return isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys() and \
            all(norm(a[x]) == norm(b[x]) for x in a)
    for f in files[1:]:
        other = {c['id']: c['inv'] for c in containers(f, tech=True)}
        if not all(same((byid.get(i) or {}).get('inv'), other.get(i)) for i in ids):
            manual = [x for x in files if x.file_index % 2 == 0]
            auto = [x for x in files if x.file_index % 2 == 1]
            if not manual or not auto:
                plan.messages.append(msg('block', 'inv_files_differ', file=file_names(f.file_index)[0]))
                return plan
            files = manual
            byid = {c['id']: c for c in containers(files[0], tech=True)}
            plan.messages.append(msg('warn', 'inv_manual_only', file=file_names(manual[0].file_index)[0],
                                     auto=file_names(auto[0].file_index)[0]))
            break
    for i in ids:
        if (byid.get(i) or {}).get('blocked'):
            plan.messages.append(msg('block', byid[i]['blocked']))
            return plan
    for f in files:
        ie = InvEdit(f)
        if not ACTIONS[action](ie, plan, params, file_names(f.file_index)[0]):
            return plan
        fc = FileChange(f.file_index, f, ie.result())
        fc.json = ie.patched_json()
        plan.changes.append(fc)
    if action == 'move':
        plan.summary = msg('info', 'chg_inv_move', src=_label(byid[ids[0]]), dst=_label(byid[ids[1]]))
    elif action == 'sort':
        plan.summary = msg('info', 'chg_inv_sort', inv=_label(byid[ids[0]]), key=str(params.get('key') or 'name'))
    elif action == 'gather':
        plan.summary = msg('info', 'chg_inv_gather', inv=_label(byid[ids[0]]), group=str(params.get('group')))
    elif action == 'gather_id':
        last = next(m for m in reversed(plan.messages) if m['code'] == 'inv_gather_item_what')
        plan.summary = msg('info', 'chg_inv_gather_item', inv=_label(byid[ids[0]]), item=last['p']['item'],
                           n=last['p']['units'])
    elif action == 'arrange':
        plan.summary = msg('info', 'chg_inv_arrange', inv=_label(byid[ids[0]]), n=len(params.get('moves') or []))
    elif action == 'move_many':
        plan.summary = msg('info', 'chg_inv_move_many', src=_label(byid[ids[0]]), dst=_label(byid[ids[1]]),
                           n=len(params.get('cells') or []))
    elif action == 'delete_many':
        last = next(m for m in reversed(plan.messages) if m['code'] == 'inv_delmany_what')
        plan.summary = msg('info', 'chg_inv_delete_many', count=last['p']['count'], n_inv=len(last['p']['invs']))
    elif action in ('split', 'fill', 'dup', 'delete', 'add', 'repair'):
        last = next(m for m in reversed(plan.messages) if m['code'].startswith('inv_') and m['code'].endswith('_what'))
        code = 'chg_inv_uninstall' if last['code'] == 'inv_uninstall_what' else 'chg_inv_' + action
        plan.summary = msg('info', code, inv=_label(byid[ids[0]]), item=last['p']['item'], n=last['p'].get('n', 0))
    else:
        nm = ' '.join(str(params.get('name') or '').split())[:NAME_MAX]
        plan.summary = msg('info', 'chg_inv_rename', inv=_label(byid[ids[0]]), new=nm)
    return plan
