"""Phase 3: JSON viewer of a save (the working copy, so pending changes are visible).

* Obfuscated keys are shown with their readable names (libNOM.map's mapping_mbincompiler.json).
* Item IDs get the item's name (binary IDs of stored modules included).
* Each node can carry notes: what it is, risks of editing it, and computed dependencies
  (other parts of the save that point to it).
Nothing here writes to the save. The expert mode (phase 5, core/jsonedit.py) changes single values through
the same staged, verified path as the rest of the editor.
"""
from __future__ import annotations

import fnmatch
import json
import re
from functools import lru_cache
from pathlib import Path

from . import gamedata

DATA = Path(__file__).resolve().parent.parent / 'data'
PAGE = 200                   # children per request
PREVIEW = 160                # characters of a string value in the tree
SEARCH_LIMIT = 200


@lru_cache(maxsize=1)
def readable() -> dict[str, str]:
    """obfuscated key -> readable name."""
    m = json.loads((DATA / 'mapping_mbincompiler.json').read_text(encoding='utf-8'))['Mapping']
    return {e['Key']: e['Value'] for e in m}


@lru_cache(maxsize=1)
def obfuscated() -> dict[str, str]:
    return {v: k for k, v in readable().items()}


def rname(key) -> str:
    return readable().get(key, key) if isinstance(key, str) else f'[{key}]'


def kind(v) -> str:
    if isinstance(v, dict):
        return 'object'
    if isinstance(v, list):
        return 'array'
    if isinstance(v, bool):
        return 'bool'
    if v is None:
        return 'null'
    if isinstance(v, (int, float)):
        return 'number'
    return 'string'


def show_str(v: str) -> str:
    """Text for a string value; bytes that are not valid text (binary IDs) as \\xNN."""
    return ''.join(f'\\x{ord(ch) - 0xDC00:02X}' if 0xDC80 <= ord(ch) <= 0xDCFF else ch for ch in v)


def resolve(j, path: list):
    node = j
    for p in path:
        if isinstance(node, dict):
            if not isinstance(p, str) or p not in node:
                raise KeyError(p)
            node = node[p]
        elif isinstance(node, list):
            if not isinstance(p, int) or not 0 <= p < len(node):
                raise KeyError(p)
            node = node[p]
        else:
            raise KeyError(p)
    return node


def rpath(path: list) -> str:
    out = ''
    for p in path:
        out += f'[{p}]' if isinstance(p, int) else ('.' if out else '') + rname(p)
    return out or '(raíz)'


def _hint(key, v, lang: str) -> str:
    """Short human hint for a scalar: the item name of an ID, mostly."""
    if isinstance(v, str) and (v.startswith('^') or rname(key) in ('Id', 'ProductId', 'TechId', 'Product')):
        n = gamedata.name(v, lang)
        if n:
            return n
        if gamedata.is_binary(v):
            return '✦'
    return ''


def _entry(key, v, lang: str) -> dict:
    k = kind(v)
    e = {'k': key, 'name': rname(key), 'type': k}
    if k == 'object' or k == 'array':
        e['len'] = len(v)
    elif k == 'string':
        s = show_str(v)
        e['value'] = s[:PREVIEW] + ('…' if len(s) > PREVIEW else '')
        e['hint'] = _hint(key, v, lang)
    else:
        e['value'] = v
        if isinstance(key, str) and rname(key) in ('Seed', 'UniverseAddress') and isinstance(v, int):
            e['hint'] = f'0x{v & 0xFFFFFFFFFFFFFFFF:X}'
    return e


def node(j, path: list, offset: int = 0, limit: int = PAGE, lang: str = 'es') -> dict:
    v = resolve(j, path)
    out = {'path': path, 'rpath': rpath(path), 'type': kind(v), 'notes': notes(j, path)}
    if isinstance(v, dict):
        items = list(v.items())
        out['total'] = len(items)
        out['children'] = [_entry(k2, v2, lang) for k2, v2 in items[offset:offset + limit]]
    elif isinstance(v, list):
        out['total'] = len(v)
        out['children'] = [_entry(i, v2, lang) for i, v2 in enumerate(v[offset:offset + limit], offset)]
    else:
        out['value'] = show_str(v) if isinstance(v, str) else v
        out['hint'] = _hint(path[-1] if path else '', v, lang)
        out['binary'] = isinstance(v, str) and gamedata.is_binary(v)        # not editable in expert mode
        out['float'] = isinstance(v, float)
    out['offset'] = offset
    return out


def search(j, q: str, lang: str = 'es', limit: int = SEARCH_LIMIT) -> dict:
    """Paths whose key name, original key, value or item name contains q (case-insensitive)."""
    q = (q or '').strip().casefold()
    if len(q) < 2:
        return {'q': q, 'results': [], 'more': False}
    res, more = [], False
    stack = [([], j)]
    while stack:
        path, v = stack.pop()
        if len(res) >= limit:
            more = True
            break
        if path:
            key = path[-1]
            hit = isinstance(key, str) and (q in rname(key).casefold() or q == key.casefold())
            if not hit and not isinstance(v, (dict, list)):
                txt = show_str(v) if isinstance(v, str) else json.dumps(v)
                hit = q in txt.casefold() or q in _hint(key, v, lang).casefold()
            if hit:
                e = _entry(key, v, lang)
                e.update(path=path, rpath=rpath(path))
                res.append(e)
        if isinstance(v, dict):
            stack.extend((path + [k2], v2) for k2, v2 in reversed(list(v.items())))
        elif isinstance(v, list):
            stack.extend((path + [i], v2) for i, v2 in reversed(list(enumerate(v))))
    return {'q': q, 'results': res, 'more': more}


def export(j, path: list, names: bool) -> str:
    """The node as JSON text, with original keys (a valid save fragment) or readable ones."""
    v = resolve(j, path)
    if names:
        def ren(o):
            if isinstance(o, dict):
                return {rname(k): ren(x) for k, x in o.items()}
            if isinstance(o, list):
                return [ren(x) for x in o]
            return show_str(o) if isinstance(o, str) else o
        v = ren(v)
        return json.dumps(v, ensure_ascii=False, indent=2)
    return json.dumps(v, ensure_ascii=True, indent=2)     # original keys: binary bytes stay escaped (\udcXX)


# ------------------------------------------------------------------- notes & dependencies -----
# (pattern on the readable path, level, code). Patterns use fnmatch on the readable path with [n] -> #.
RULES = [
    ('*PlayerStateData.UniverseAddress', 'warn', 'jv_address'),
    ('*PlayerStateData.PreviousUniverseAddress', 'info', 'jv_prev_address'),
    ('*CommonStateData.SaveName', 'info', 'jv_savename'),
    ('*PlayerStateData.SaveName', 'warn', 'jv_savename_psd'),
    ('*PlayerStateData.ShipOwnership', 'warn', 'jv_ships'),
    ('*PlayerStateData.ShipOwnership#', 'info', 'jv_ship'),
    ('*PlayerStateData.PrimaryShip', 'warn', 'jv_primary'),
    ('*CorvetteEditAssociatedShipIndex', 'warn', 'jv_corvette_idx'),
    ('*.Resource.Seed', 'info', 'jv_seed'),
    ('*.BaseStatValues', 'info', 'jv_stats'),
    ('*.Slots', 'info', 'jv_slots'),
    ('*.ValidSlotIndices', 'warn', 'jv_valid'),
    ('*.SpecialSlots', 'info', 'jv_special'),
    ('*.StackSizeGroup', 'info', 'jv_stackgroup'),
    ('*.Slots#.MaxAmount', 'info', 'jv_maxamount'),
    ('*PlayerStateData.Units', 'warn', 'jv_currency'),
    ('*PlayerStateData.Nanites', 'warn', 'jv_currency'),
    ('*PlayerStateData.Specials', 'warn', 'jv_currency'),
    ('*PlayerStateData.PersistentPlayerBases', 'warn', 'jv_bases'),
    ('*PlayerStateData.PersistentPlayerBases#.UserData', 'warn', 'jv_base_userdata'),
    ('*PlayerStateData.MissionProgress', 'info', 'jv_missions'),
    ('*PlayerStateData.DifficultyState', 'warn', 'jv_difficulty'),
    ('*PlayerStateData.KnownProducts', 'info', 'jv_known'),
    ('*PlayerStateData.VehicleOwnership', 'info', 'jv_vehicles'),
    ('*PlayerStateData.Multitools', 'info', 'jv_multitools'),
    ('*PlayerStateData.CurrentFreighter', 'info', 'jv_freighter'),
    ('*PlayerStateData.FreighterUniverseAddress', 'warn', 'jv_freighter_address'),
    ('*PlayerStateData.TeleportEndpoints', 'info', 'jv_teleport'),
    ('*PlayerStateData.SeasonData', 'warn', 'jv_season'),
    ('*PlayerStateData.SettlementStatesV2', 'info', 'jv_settlements'),
    ('*PlayerStateData.SettlementStatesV2#.Stats', 'warn', 'jv_settlement_stats'),
    ('Version', 'warn', 'jv_version'),
    ('Platform', 'warn', 'jv_version'),
]


def _norm(rp: str) -> str:
    return re.sub(r'\[\d+\]', '#', rp)


def notes(j, path: list) -> list[dict]:
    rp = _norm(rpath(path))
    out = [{'level': lv, 'code': code} for pat, lv, code in RULES if fnmatch.fnmatchcase(rp, pat)]
    out += _refs(j, path)
    return out


def _psd_path(j):
    o = obfuscated()
    for p in ([o['BaseContext'], o['PlayerStateData']],):
        try:
            resolve(j, p)
            return p
        except KeyError:
            pass
    return None


def _refs(j, path: list) -> list[dict]:
    """Computed dependencies: who in the save points to this node."""
    o = obfuscated()
    psdp = _psd_path(j)
    if not psdp:
        return []
    psd = resolve(j, psdp)
    out = []
    ships_p = psdp + [o['ShipOwnership']]
    if len(path) == len(ships_p) + 1 and path[:-1] == ships_p and isinstance(path[-1], int):
        i = path[-1]
        if psd.get(o['PrimaryShip']) == i:
            out.append({'level': 'info', 'code': 'jv_ref_primary', 'p': {'n': i + 1}})
        for si, s in enumerate(psd.get(o['ShipOwnership']) or []):
            if isinstance(s, dict) and s.get(o['CorvetteEditAssociatedShipIndex']) == i and si != i:
                out.append({'level': 'info', 'code': 'jv_ref_corvette', 'p': {'n': si + 1}})
        for bi, b in enumerate(psd.get(o['PersistentPlayerBases']) or []):
            bt = (b.get(o['BaseType']) or {}).get(o['PersistentBaseTypes']) if isinstance(b, dict) else None
            if bt == 'PlayerShipBase' and b.get(o['UserData']) == i:      # the corvette's own base (same rule as reorder)
                out.append({'level': 'info', 'code': 'jv_ref_base', 'p': {'n': bi + 1}})
    if path == psdp + [o['PrimaryShip']]:
        i = psd.get(o['PrimaryShip'])
        ships = psd.get(o['ShipOwnership']) or []
        if isinstance(i, int) and 0 <= i < len(ships):
            out.append({'level': 'info', 'code': 'jv_points_ship', 'p': {'n': i + 1, 'name': ships[i].get(o['Name'], '')}})
    return out
