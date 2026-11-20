"""What the Summary view shows besides the slot details: currencies, reputation, the active ship,
freighter, exocraft, corvettes and the multi-tool in hand. Read only."""
from __future__ import annotations

from . import inventories
from .ops import ship_type
from .savefile import SaveFile

# player stats of the game that hold standing (GLOBAL_STATS group), in the order the game lists them
REPUTATION = ('TRA_STANDING', 'EXP_STANDING', 'WAR_STANDING', 'BUI_STANDING',
              'TGUILD_STAND', 'EGUILD_STAND', 'WGUILD_STAND', 'PIRATE_STAND')


def _u32(v) -> int | None:
    """Units are a 32-bit unsigned value that the save stores as signed."""
    return v & 0xFFFFFFFF if isinstance(v, int) else None


def _global_stats(psd: dict, k) -> dict:
    for g in psd.get(k.s('Stats')) or []:
        if isinstance(g, dict) and g.get(k.s('GroupId')) == '^GLOBAL_STATS':
            out = {}
            for st in g.get(k.s('Stats')) or []:
                v = (st.get(k.s('Value')) or {}).get(k.s('IntValue'))
                out[(st.get(k.s('Id')) or '').lstrip('^')] = v if isinstance(v, int) else 0
            return out
    return {}


# body base of the player character (CharacterCustomisationData[0]); seen in saves: ASTRONAUT_BASE, BUILDERS_BASE
RACES = {'ASTRONAUT': 'anomaly', 'BUILDERS': 'autophage', 'GEK': 'gek', 'KORVAX': 'korvax',
         'VYKEEN': 'vykeen', 'VYK': 'vykeen', 'FOURTH': 'traveller'}


def race(psd: dict, k) -> dict | None:
    """The look the player chose (the Appearance Modifier race), from the first descriptor of the character."""
    cc = psd.get(k.s('CharacterCustomisationData')) or []
    groups = (((cc[0] if cc else {}) or {}).get(k.s('CustomData')) or {}).get(k.s('DescriptorGroups')) or []
    base = next((g.lstrip('^') for g in groups if isinstance(g, str) and g.endswith('_BASE')), None)
    if not base:
        return None
    key = base[:-len('_BASE')]
    return {'id': RACES.get(key), 'raw': base}


def describe(files: list[SaveFile], lang: str = 'es') -> dict:
    f = files[0]
    k = f.keys
    psd = f.psd()
    stats = _global_stats(psd, k)
    ships = psd.get(k.s('ShipOwnership')) or []

    def ship(i):
        s = ships[i]
        fn = s[k.s('Resource')][k.s('Filename')]
        cls = ((s.get(k.s('Inventory')) or {}).get(k.s('Class')) or {}).get(k.s('InventoryClass'))
        return {'index': i, 'name': s.get(k.s('Name')) or '', 'type': ship_type(fn), 'class': cls}

    prim = psd.get(k.s('PrimaryShip'))
    primary = ship(prim) if isinstance(prim, int) and 0 <= prim < len(ships) and ships[prim][k.s('Resource')][k.s('Filename')] else None
    corvettes = [ship(i) for i, s in enumerate(ships)
                 if s[k.s('Resource')][k.s('Filename')] and ship_type(s[k.s('Resource')][k.s('Filename')]) == 'corvette']

    veh = None
    vi = psd.get(k.s('PrimaryVehicle'))
    vo = psd.get(k.s('VehicleOwnership')) or []
    if isinstance(vi, int) and 0 <= vi < len(vo) and vi not in inventories.VEHICLE_SKIP:
        inv = (vo[vi] or {}).get(k.s('Inventory')) or {}
        if inv.get(k.s('ValidSlotIndices')):                        # an exocraft the player has built
            veh = {'index': vi, 'name': vo[vi].get(k.s('Name')) or '', 'type': inventories.vehicle_name(vi, lang)}

    mt = None
    mi = psd.get(k.s('ActiveMultioolIndex'))
    mts = psd.get(k.s('Multitools')) or []
    if isinstance(mi, int) and 0 <= mi < len(mts) and isinstance(mts[mi], dict):
        st = mts[mi].get(k.s('Store')) or {}
        mt = {'index': mi, 'name': mts[mi].get(k.s('Name')) or '',
              'class': ((st.get(k.s('Class')) or {}).get(k.s('InventoryClass')))}

    return {'race': race(psd, k), 'units': _u32(psd.get(k.s('Units'))), 'nanites': psd.get(k.s('Nanites')),
            'quicksilver': psd.get(k.s('Specials')),
            'reputation': [{'id': r, 'value': stats[r]} for r in REPUTATION if r in stats],
            'primary_ship': primary, 'corvettes': corvettes, 'vehicle': veh, 'multitool': mt}
