"""Build olympian/data/gamedata.json from the user's No Man's Sky install (phase 2b).

Pipeline (run on a machine with MBINCompiler):
  1. HGPAKtool reads the game's .pak files (format since 5.50 "Worlds Part II").
  2. MBINCompiler converts the few MBIN tables we need to MXML.
  3. This script keeps only what Olympian uses: item names, categories, charge info and stat bonuses,
     plus the texts of those names in the content languages, each item's StackMultiplier and the
     difficulty table of stack sizes per inventory group (phase 2c), the mission tables (phase 4) and the
     settlement perks and rules (phase 5).

Usage:  python tools/build_gamedata.py <PCBANKS dir> <MBINCompiler path> <out json>
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from hgpaktool.api import HGPAKFile

TABLES = {
    'technology': 'metadata/reality/tables/nms_reality_gctechnologytable.mbin',
    'procedural': 'metadata/reality/tables/nms_reality_gcproceduraltechnologytable.mbin',
    'product': 'metadata/reality/tables/nms_reality_gcproducttable.mbin',
    'substance': 'metadata/reality/tables/nms_reality_gcsubstancetable.mbin',
    'difficulty': 'metadata/gamestate/difficultyconfig.mbin',
    'baseparts': 'metadata/reality/tables/nms_basepartproducts.mbin',          # building parts (B_*...)
    'modular': 'metadata/reality/tables/nms_modularcustomisationproducts.mbin',  # ship / corvette parts
    'procproducts': 'metadata/reality/tables/proceduralproducttable.mbin',      # PROC_LOOT#seed... (generic name)
    'settleperks': 'metadata/reality/tables/settlementperkstable.mbin',          # phase 5: settlement perks
    'settleglobals': 'gcsettlementglobals.mbin',                                 # phase 5: settlement rules
    'reality': 'metadata/reality/defaultreality.mbin',                           # tech stats: rarity curves
    'gameplay': 'gcgameplayglobals.global.mbin',                                 # tech stats: adjacency, supercharge
    'inventory': 'metadata/reality/tables/inventorytable.mbin',                  # tech stats: class bonuses
    'player': 'gcplayerglobals.global.mbin',                                     # tech stats: X/Sentinel high grade
    'spaceship': 'gcspaceshipglobals.global.mbin',                               # ship screen: manoeuvrability, damage
    'projectiles': 'metadata/projectiles/projectiletable.mbin',                  # ship screen: weapon range, laser
    'journey': 'metadata/reality/journey.mbin',                                  # summary: medals and milestones
    'leveled': 'metadata/gamestate/stats/leveledstatstable.mbin',                # summary: levels of each stat
}
PROC_QUALITY = ['Normal', 'Rare', 'Epic', 'Legendary', 'Illegal', 'Sentinel', 'Robot', 'SeaTrash']
WEIGHTING = ['NoWeighting', 'MaxIsUncommon', 'MaxIsRare', 'MaxIsSuperRare', 'MinIsUncommon', 'MinIsRare',
             'MinIsSuperRare']
CURVE_IDS = {'Linear': 0, 'SmoothInOut': 1, 'FastInSlowOut': 2, 'BellSquared': 3, 'Squared': 4, 'Cubed': 5,
             'Logarithmic': 6, 'SlowIn': 7, 'SlowOut': 8, 'ReallySlowOut': 9, 'SmootherStep': 10,
             'SmoothFastInSlowOut': 11, 'SmoothSlowInFastOut': 12, 'EaseInSine': 13, 'EaseOutSine': 14,
             'EaseInOutSine': 15, 'EaseInQuad': 16, 'EaseOutQuad': 17, 'EaseInOutQuad': 18, 'EaseInQuart': 19,
             'EaseOutQuart': 20, 'EaseInOutQuart': 21, 'EaseInQuint': 22, 'EaseOutQuint': 23, 'EaseInOutQuint': 24,
             'EaseInExpo': 25, 'EaseOutExpo': 26, 'EaseInOutExpo': 27}
ADJ_KEYS = {'same_add': 'BonusSameTypeElementsAdd', 'same_mul': 'BonusSameTypeElementsMultiply',
            'child_add': 'BonusChildTypeElementsAdd', 'child_mul': 'BonusChildTypeElementsMultiply',
            'supercharge': 'SpecialTechSlotBonus', 'max_group': 'MaxNumSameGroupTech'}
# phase 5: texts shown in the settlements view and the advisor, besides the tables' own keys
SETTLE_TEXTS = ['UI_SETTLEMENT_PROD_DEBT', 'UI_SETTLEMENT_PROD_DEBT_TIP', 'UI_SETTLEMENT_PROD_DEBT_TIP2',
                'UI_SETTLEMENT_DEBT_INC', 'UI_SETTLEMENT_DEBT_DEC']
VEHICLE_TEXTS = ['VEHICLE_BUGGY_TITLE_L', 'VEHICLE_BIKE_TITLE_L', 'VEHICLE_TRUCK_TITLE_L', 'VEHICLE_WHEELEDBIKE_TITLE_L',
                 '', 'VEHICLE_SUBMARINE_TITLE_L', 'VEHICLE_MECH_TITLE_L']     # VehicleOwnership order (GcVehicleType)
STAT_NAMES = ['MaxPopulation', 'Happiness', 'Production', 'Upkeep', 'Sentinels', 'Debt', 'Alert', 'BugAttack']
JUDGEMENT_TYPES = ['None', 'StrangerVisit', 'Policy', 'NewBuilding', 'BuildingChoice', 'Conflict', 'Request',
                   'BlessingPerkRelated', 'JobPerkRelated', 'ProcPerkRelated', 'UpgradeBuilding', 'UpgradeBuildingChoice']
# content language -> (localisation file suffix, column in the table)
LANGS = {'es': ('latinamericanspanish', 'LatinAmericanSpanish'),
         'es-ES': ('spanish', 'Spanish'),
         'en': ('english', 'English'),
         'ru': ('russian', 'Russian')}
TAG = re.compile(r'<[^>]*>')
QUALITY = {'Normal': 'C', 'Rare': 'B', 'Epic': 'A', 'Legendary': 'S', 'Illegal': 'X', 'Sentinel': 'Ø'}


def extract(pcbanks: Path, dest: Path) -> dict[str, Path]:
    wanted = set(TABLES.values())
    out = {}
    for pak in sorted(pcbanks.glob('NMSARC.*.pak')):
        if pak.stat().st_size > 400_000_000:          # textures/audio: never contain tables
            continue
        with HGPAKFile(pak) as p:
            names = [n for n in p.files if n in wanted or n.startswith(MISSION_TABLES) or
                     (n.startswith('language/nms_') and n.endswith(tuple(f'_{s}.mbin' for s, _ in LANGS.values())))]
            for name, data in (p.extract(names) if names else []):
                f = dest / name.replace('/', '__')
                f.write_bytes(data)
                out[name] = f
    return out


MISSION_TABLES = 'metadata/simulation/missions/tables/'     # phase 4: every mission table
TEXT_KEY = re.compile(r'[A-Z][A-Z0-9_]{3,}')


def stage_texts(stage) -> list[str]:
    obj = [x.get('value') for x in stage.iter('Property') if x.get('name') == 'ObjectiveID' and TEXT_KEY.fullmatch(x.get('value') or '')]
    msg = [x.get('value') for x in stage.iter('Property') if x.get('name') in ('Message', 'OSDMessage')
           and TEXT_KEY.fullmatch(x.get('value') or '')]
    seen, out = set(), []
    for k in obj + msg:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out[:6]


def missions(paths: list[Path]) -> dict:
    """{mission id: {t: title key, s: subtitle key, cls, crit, tab: table, n: stages in the whole tree}}."""
    def text(el, name):
        c = props(el).get(name)
        fmt = (val(c, 'Format') or '') if c is not None else ''
        return fmt.replace('%d', '1')            # numbered texts: the first one (…_TITLE_1)
    out = {}
    for path in sorted(paths):
        tab = path.name.split('__')[-1][:-5].replace('missiontable', '').replace('table', '') or 'mission'
        for m in ET.parse(path).getroot().iter('Property'):
            if m.get('name') != 'Missions' or m.get('value') != 'GcGenericMissionSequence':
                continue
            mid = val(m, 'MissionID')
            if not mid or mid in out:
                continue
            stages = [x for x in m.iter('Property') if x.get('value') == 'GcGenericMissionStage']
            out[mid] = {'t': text(m, 'MissionTitles'), 's': text(m, 'MissionSubtitles'),
                        'cls': val(m, 'MissionClass') or '', 'crit': val(m, 'MissionIsCritical') == 'true',
                        'tab': tab, 'n': len(stages),
                        # per stage (same order the save's Progress counts): candidate text keys of its
                        # objective - ObjectiveID first, then on-screen messages; resolved after the texts load
                        '_c': [stage_texts(x) for x in stages]}
    return out


def props(el) -> dict:
    return {c.get('name'): c for c in el}


def val(el, name, default=None):
    c = props(el).get(name)
    return default if c is None else c.get('value', default)


def nested(el, name, inner):
    c = props(el).get(name)
    return None if c is None else val(c, inner)


def charge_by(el) -> list | None:
    """Items that recharge a chargeable technology (ChargeBy), e.g. sodium for the starship shield."""
    if val(el, 'Chargeable') != 'true':
        return None
    c = props(el).get('ChargeBy')
    return [x.get('value') for x in c if x.get('value')] if c is not None else []


def trade(el) -> dict:
    """Rarity (Common/Uncommon/Rare) and base value in units, for sorting inventories."""
    return {'rarity': nested(el, 'Rarity', 'Rarity') or '', 'value': float(val(el, 'BaseValue', 0) or 0)}


def stack_sizes(xml_path: Path) -> dict:
    """InventoryStackLimitsOptionData: {level: {'cap': {...}, 'Substance': {group: n}, 'Product': {group: n}}}."""
    root = ET.parse(xml_path).getroot()
    data = next(c for c in root.iter('Property') if c.get('name') == 'InventoryStackLimitsOptionData')
    out = {}
    for lvl in data:
        p = props(lvl)
        out[lvl.get('name')] = {
            'cap': {'Substance': int(val(lvl, 'SubstanceStackLimit')), 'Product': int(val(lvl, 'ProductStackLimit'))},
            'Substance': {g.get('name'): int(g.get('value')) for g in p['MaxSubstanceStackSizes']},
            'Product': {g.get('name'): int(g.get('value')) for g in p['MaxProductStackSizes']}}
    return out


def settlement(perks_xml: Path, globals_xml: Path) -> dict:
    """What the settlements view needs from the game: perks (texts, kind, stat effects), the size of each effect,
    stat limits and the game's good/bad thresholds, pending-decision texts and building names."""
    perks = {}
    for e in table_entries(perks_xml):
        pid = val(e, 'ID')
        if not pid:
            continue
        sc = props(e).get('StatChanges')
        perks[pid] = {'n': val(e, 'Name') or '', 'd': val(e, 'Description') or '',
                      'neg': val(e, 'IsNegative') == 'true', 'start': val(e, 'IsStarter') == 'true',
                      'proc': val(e, 'IsProc') == 'true', 'job': val(e, 'IsJob') == 'true',
                      'bless': val(e, 'IsBlessing') == 'true',
                      'fx': [[nested(x, 'Stat', 'SettlementStatType'), nested(x, 'Strength', 'SettlementStatStrength')]
                             for x in (sc if sc is not None else [])]}
    root = ET.parse(globals_xml).getroot()
    g = {c.get('name'): c for c in root}

    def arr(name, conv=str):
        return [conv(c.get('value')) for c in g[name]]
    strength = {}
    for st in g['PerkStatStrengthValues']:
        ranges = props(st)['PerkStatStrengthValues']
        strength[st.get('name')] = {r.get('name'): [int(val(r, 'AmountMin')), int(val(r, 'AmountMax'))] for r in ranges}
    return {'perks': perks, 'strength': strength,
            'stats': {'names': STAT_NAMES, 'min': arr('StatsMinValues', int), 'max': arr('StatsMaxValues', int),
                      'bad': arr('NormalisedStatBadThresholds', float), 'good': arr('NormalisedStatGoodThresholds', float),
                      'good_high': arr('StatIsGoodWhenPositive', lambda v: v == 'true')},
            'judgement': {'types': JUDGEMENT_TYPES, 'title': arr('JudgementUpdateTitles'),
                          'sub': arr('JudgementUpdateSubtitles'), 'text': arr('JudgementUpdateMainText'),
                          'objective': arr('JudgementMissionObjectives'),
                          'wait': [int(g['JudgementWaitTimeMin'].get('value')), int(g['JudgementWaitTimeMax'].get('value'))]},
            # building names ("%SETTLEMENT% Trade Centre"), indexed by GcBuildingClassification
            'buildings': {c.get('name'): c.get('value') for c in g['BuildingUpgradePageNames'] if c.get('value')},
            'max_perks': int(g['MaxPerksCount'].get('value')),
            'max_population': int(g['MaxNPCPopulation'].get('value')),
            'debt_per_day': int(g['DailyDebtPaymentModifier'].get('value')),
            # seconds per construction stage / class upgrade; 0 = BuildingUpgradeTimeInSeconds (game code 0x140445e80)
            'build_times': {c.get('name'): int(c.get('value')) for c in g['SettlementBuildingTimes']
                            if int(c.get('value') or 0) > 0},
            'build_time_default': int(g['BuildingUpgradeTimeInSeconds'].get('value')),
            'texts': SETTLE_TEXTS, 'vehicles': VEHICLE_TEXTS}


def num(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def table_entries(xml_path: Path):
    root = ET.parse(xml_path).getroot()
    table = next(c for c in root if c.get('name') in ('Table', 'Objects', 'Categories', 'LeveledStatTable'))
    return list(table)


def icon(el):
    c = props(el).get('Icon')
    f = val(c, 'Filename') if c is not None else None
    return f.lower() if f else None


def stat_bonuses(el):
    out = []
    sb = props(el).get('StatBonuses')
    for b in (sb if sb is not None else []):
        out.append([nested(b, 'Stat', 'StatsType'), float(val(b, 'Bonus', 0) or 0)])
    return out


def proc_levels(e) -> dict:
    """What the game needs to roll a procedural upgrade from its seed: quality, how many stats, the rarity
    curves and each stat's range (full precision: the roll is recomputed in float32)."""
    lv = props(e).get('StatLevels')
    q = val(e, 'Quality')
    return {'q': PROC_QUALITY.index(q) if q in PROC_QUALITY else 0,
            'n': [int(val(e, 'NumStatsMin', 0) or 0), int(val(e, 'NumStatsMax', 0) or 0)],
            'wc': WEIGHTING.index(nested(e, 'WeightingCurve', 'WeightingCurve') or 'NoWeighting'),
            'biggs': val(e, 'IsBiggsProcTech') == 'true',
            'lv': [[nested(x, 'Stat', 'StatsType'), float(val(x, 'ValueMin', 0) or 0), float(val(x, 'ValueMax', 0) or 0),
                    WEIGHTING.index(nested(x, 'WeightingCurve', 'WeightingCurve') or 'NoWeighting'),
                    val(x, 'AlwaysChoose') == 'true'] for x in (lv if lv is not None else [])]}


CONTROL_BY_CLASS = {          # GcSpaceshipClasses -> control set the ship screen uses (game code, build 185034)
    'Dropship': 'ControlHeavy', 'Alien': 'ControlHeavy', 'Corvette': 'ControlCorvette',
    'Fighter': 'ControlLight', 'Scientific': 'ControlLight', 'Royal': 'ControlLight', 'Sail': 'ControlLight',
    'Robot': 'ControlHover', 'SwarmDrone': 'ControlHover'}


def journey(journey_xml: Path, leveled_xml: Path) -> dict:
    """The game's journey page: categories, the stat behind each medal and that stat's levels.
    {'categories': [{'name': key, 'medals': [{'stat': id, 'title': key, 'float': bool,
                                              'levels': [[threshold, level name key], ...]}]}]}"""
    levels = {}
    for e in table_entries(leveled_xml):
        sid = val(e, 'StatId')
        lv = props(e).get('StatLevels')
        if not sid or lv is None:
            continue
        rows = []
        for x in lv:
            v = props(x).get('Value')
            iv, fv = num(val(v, 'IntValue')) if v is not None else 0, num(val(v, 'FloatValue')) if v is not None else 0
            rows.append([fv if fv else iv, val(x, 'LevelName') or ''])
        levels[sid] = rows
    cats = []
    for c in table_entries(journey_xml):
        medals = []
        for m in (props(c).get('Medals') if props(c).get('Medals') is not None else []):
            sid = val(m, 'LevelledStatID')
            if not sid or val(m, 'OverallJourneyDummy') == 'true' or sid not in levels:
                continue
            medals.append({'stat': sid, 'title': val(m, 'MedalTitle') or '',
                           'float': nested(m, 'StatType', 'StatType') == 'Float', 'levels': levels[sid]})
        if medals:
            cats.append({'name': val(c, 'NameIDLower') or val(c, 'NameIDUpper') or '', 'medals': medals})
    return {'categories': cats}


def journey_keys(j: dict) -> set:
    return {k for c in j['categories'] for k in [c['name']] + [m['title'] for m in c['medals']] +
            [n for m in c['medals'] for _v, n in m['levels']] if k}


def ship_screen(reality: Path, spaceship: Path, projectiles: Path, player: Path) -> dict:
    """Data behind the ship screen's Manoeuvrability and Damage Potential."""
    sg = ET.parse(spaceship).getroot()
    top = {c.get('name'): c for c in sg}
    num = lambda e, k: float(val(e, k))

    def planet(name):
        pe = next(c for c in top[name] if c.get('name') == 'PlanetEngine')
        return [num(pe, 'TurnStrength'), num(pe, 'BoostingTurnDamp')]
    controls = {n: planet(n) for n in ('Control', 'ControlLight', 'ControlHeavy', 'ControlCorvette', 'ControlHover')}
    bonus = {c: [num(top['ControlBonus' + c], k) for k in ('TurnStrengthMax', 'TurnStrengthMin',
                                                           'BoostingTurnDampMax', 'BoostingTurnDampMin')]
             for c in 'CBAS'}
    weapons = []
    sw = next(c for c in ET.parse(reality).getroot() if c.get('name') == 'ShipWeapons')
    pt = {}
    for e in ET.parse(projectiles).getroot().iter('Property'):
        if e.get('value') == 'GcProjectileData' and val(e, 'Id'):
            pt[val(e, 'Id')] = [num(e, 'DefaultSpeed'), num(e, 'Life')]
        if e.get('value') == 'GcLaserBeamData' and val(e, 'Id') == 'PLAYER_SHIP':
            laser = [num(e, 'DefaultDamage'), num(e, 'HitRate')]
    for w in sw:
        proj = val(w, 'Projectile') or ''
        weapons.append({'name': w.get('name'), 'stat': nested(w, 'Stat', 'StatsType'),
                        'projectile': val(w, 'IsProjectile') == 'true', 'cool': num(w, 'OverheatCoolTime'),
                        'speed_life': pt.get(proj)})
    pl = {c.get('name'): c.get('value') for c in ET.parse(player).getroot()}
    g = {c.get('name'): c.get('value') for c in sg}
    return {'controls': controls, 'control_by_class': CONTROL_BY_CLASS, 'class_bonus': bonus, 'weapons': weapons,
            'laser': laser, 'laser_downtime': float(g['LaserOverheatDownTime']),
            'ref_range': float(g['WeaponDamagePotentialReferenceRange']),
            'default_range': float(pl['AimDistanceShip'])}


def techstats(reality: Path, gameplay: Path, inventory: Path, player: Path) -> dict:
    """Rules for the real value of each stat: rarity curves (procedural rolls), adjacency and supercharge
    bonuses, and the per-ship/tool class bonuses (BaseStatValues) with the stats each one changes."""
    root = ET.parse(reality).getroot()
    wcs = next(c for c in root.iter('Property') if c.get('name') == 'WeightingCurves')
    curves = [CURVE_IDS[val(c, 'Curve')] for c in wcs]
    groot = ET.parse(gameplay).getroot()
    g = {c.get('name'): c.get('value') for c in groot}
    adj = {k: float(g[v]) for k, v in ADJ_KEYS.items()}
    adj['max_group'] = int(adj['max_group'])
    adj['multiplier_bonus'] = g.get('UseMultiplierBonus') == 'true'
    base = {}
    for e in ET.parse(inventory).getroot().iter('Property'):
        if e.get('value') == 'GcInventoryBaseStat' and val(e, 'BaseStatID'):
            sb = props(e).get('StatBonus')
            base[val(e, 'BaseStatID')] = [[nested(b, 'StatType', 'StatsType'), val(b, 'LessIsBetter') == 'true']
                                          for b in (sb if sb is not None else [])]
    pl = {c.get('name'): c.get('value') for c in ET.parse(player).getroot()}
    # Illegal/Sentinel/Robot upgrades: chance (%) of the "high grade" roll (near-best values)
    return {'curves': curves, 'adj': adj, 'base_stats': base, 'high_grade': int(pl.get('ChanceHighGradeIllegal') or 0)}


def build(pcbanks: Path, mbinc: str, out_json: Path, timings: dict | None = None):
    """timings, when given, receives the seconds spent in each step (extract, convert, parse)."""
    clock = [time.perf_counter()]

    def lap(name):
        now = time.perf_counter()
        if timings is not None:
            timings[name] = now - clock[0]
        clock[0] = now

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        files = extract(pcbanks, td)
        lap('extract')
        mbins = [str(p) for p in files.values()]
        r = subprocess.run([mbinc, 'convert', '-y', '-q', *mbins], capture_output=True, text=True)
        lap('convert')
        if r.returncode != 0:
            raise SystemExit(f'MBINCompiler failed: {r.stderr[-500:]}')
        mx = {k: Path(str(v)[:-5] + '.MXML') for k, v in files.items()}
        missing = [k for k, p in mx.items() if not p.exists()]
        if missing:
            raise SystemExit(f'MBINCompiler did not convert: {missing}')
        stacks = stack_sizes(mx[TABLES['difficulty']])

        items: dict[str, dict] = {}
        for e in table_entries(mx[TABLES['technology']]):
            iid = val(e, 'ID')
            if not iid:
                continue
            items[iid] = {'k': 'tech', 'name': val(e, 'NameLower') or val(e, 'Name'),
                          'cat': nested(e, 'Category', 'TechnologyCategory'),
                          'charge': val(e, 'Chargeable') == 'true', 'charge_amount': int(val(e, 'ChargeAmount', 0) or 0),
                          'charge_by': charge_by(e),
                          'upgrade': val(e, 'Upgrade') == 'true', 'core': val(e, 'Core') == 'true',
                          'stat': nested(e, 'BaseStat', 'StatsType'), 'bonus': stat_bonuses(e), 'icon': icon(e),
                          'sub': val(e, 'Subtitle') or None, 'desc': val(e, 'Description') or None}
        for e in table_entries(mx[TABLES['procedural']]):
            iid = val(e, 'ID')
            if not iid:
                continue
            items[iid] = {'k': 'proc', 'name': val(e, 'NameLower') or val(e, 'Name'), 'sub': val(e, 'Subtitle'),
                          'template': val(e, 'Template') or '',
                          'cat': nested(e, 'Category', 'ProceduralTechnologyCategory'),
                          'cls': QUALITY.get(val(e, 'Quality'), ''), 'charge': False, 'upgrade': True,
                          'icon': icon(e), 'stats': [nested(x, 'Stat', 'StatsType') for x in (props(e).get('StatLevels') if props(e).get('StatLevels') is not None else [])],
                          'desc': val(e, 'Description') or None,
                          # tooltip: each stat with the range the game rolls in (the exact roll comes from the seed)
                          'ranges': [[nested(x, 'Stat', 'StatsType'), num(val(x, 'ValueMin')), num(val(x, 'ValueMax'))]
                                     for x in (props(e).get('StatLevels') if props(e).get('StatLevels') is not None else [])],
                          **proc_levels(e)}
        for e in [e for t in ('product', 'baseparts', 'modular') for e in table_entries(mx[TABLES[t]])]:
            iid = val(e, 'ID')
            if iid:
                items.setdefault(iid, {'k': 'product', 'name': val(e, 'NameLower') or val(e, 'Name'),
                                       'cat': nested(e, 'Type', 'ProductCategory'), 'icon': icon(e),
                                       'stack': int(val(e, 'StackMultiplier', 0) or 0), **trade(e)})
        # procedural products: the real name is generated from the seed; we keep the type (subtitle) and icon
        for e in ET.parse(mx[TABLES['procproducts']]).getroot().iter('Property'):
            if e.get('name') == 'Product' and e.get('value') == 'GcProductData' and val(e, 'ID'):
                items.setdefault(val(e, 'ID'), {'k': 'product', 'name': val(e, 'Subtitle') or val(e, 'NameLower'),
                                                'cat': 'Procedural', 'icon': icon(e),
                                                'stack': int(val(e, 'StackMultiplier', 0) or 0), **trade(e)})
        for e in table_entries(mx[TABLES['substance']]):
            iid = val(e, 'ID')
            if iid:
                items.setdefault(iid, {'k': 'substance', 'name': val(e, 'NameLower') or val(e, 'Name'),
                                       'cat': nested(e, 'Category', 'SubstanceCategory'), 'icon': icon(e),
                                       'stack': int(val(e, 'StackMultiplier', 0) or 0), **trade(e)})

        mis = missions([p for n, p in mx.items() if n.startswith(MISSION_TABLES)])
        st = settlement(mx[TABLES['settleperks']], mx[TABLES['settleglobals']])
        st_keys = {p[f] for p in st['perks'].values() for f in ('n', 'd') if p.get(f)}
        st_keys |= {k for f in ('title', 'sub', 'text', 'objective') for k in st['judgement'][f] if k}
        st_keys |= {k for k in st['buildings'].values() if k} | {k for k in SETTLE_TEXTS + VEHICLE_TEXTS if k}
        jr = journey(mx[TABLES['journey']], mx[TABLES['leveled']])
        st_keys |= journey_keys(jr)                      # kept like the settlement texts
        needed = {it[f] for it in items.values() for f in ('name', 'sub', 'desc') if it.get(f)}
        needed |= st_keys
        needed |= {m[f] for m in mis.values() for f in ('t', 's') if m.get(f)}
        needed |= {k for m in mis.values() for c in m['_c'] for k in c}
        stats = {r[0] for it in items.values() for f in ('bonus', 'ranges', 'lv') for r in it.get(f) or []}
        needed |= {s for s in stats if isinstance(s, str)}       # the game's own stat labels (key = stat name)
        texts: dict[str, dict[str, str]] = {lang: {} for lang in LANGS}
        needed_uc = {n.upper(): n for n in needed}
        needed_uc.update({s.upper()[:31]: s for s in stats if len(s) > 31})   # the game cuts label ids at 31
        for name, path in mx.items():
            if not name.startswith('language/'):
                continue
            for lang, (suffix, column) in LANGS.items():
                if not name.endswith(f'_{suffix}.mbin'):
                    continue
                for e in table_entries(path):
                    key = needed_uc.get((val(e, 'Id') or '').upper())   # tables sometimes differ in case (1x1 / 1X1)
                    if key:
                        t = TAG.sub('', val(e, column) or '').strip()
                        if t:
                            texts[lang][key] = t

    # objective of each stage: the first candidate key with a text; kept only where it changes ([index, key])
    for m in mis.values():
        hints, last = [], None
        for i, cands in enumerate(m.pop('_c')):
            key = next((c for c in cands if c in texts['en'] or c in texts['es']), None)
            if key and key != last:
                hints.append([i, key])
                last = key
        m['h'] = hints
    used = {m[f] for m in mis.values() for f in ('t', 's') if m.get(f)} | {k for m in mis.values() for _i, k in m['h']}
    item_keys = {it[f] for it in items.values() for f in ('name', 'sub', 'desc') if it.get(f)}
    texts = {lang: {k: v for k, v in tx.items() if k in used or k in item_keys or k in st_keys} for lang, tx in texts.items()}

    sig = pcbanks / 'BankSignatures.bin'
    out = {'built': time.time(),
           'game': {'signature': hashlib.sha256(sig.read_bytes()).hexdigest()[:16] if sig.exists() else None,
                    },
           'compiler': subprocess.run([mbinc, 'version'], capture_output=True, text=True).stdout.strip()[-40:],
           'source': 'No Man\'s Sky game files, read with HGPAKtool (MIT) and MBINCompiler (LGPL-3.0)',
           'stack_sizes': stacks,
           'items': items, 'missions': mis, 'settlement': st, 'texts': texts, 'journey': jr,
           'techstats': {**techstats(mx[TABLES['reality']], mx[TABLES['gameplay']], mx[TABLES['inventory']],
                                     mx[TABLES['player']]),
                         'ship': ship_screen(mx[TABLES['reality']], mx[TABLES['spaceship']], mx[TABLES['projectiles']],
                                             mx[TABLES['player']])}}
    out_json.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    lap('parse')
    exe = pcbanks.parent / 'Binaries' / 'NMS.exe'             # stat parents and kinds live in the executable
    if exe.exists():
        meta = out_json.parent / 'statsmeta.json'
        try:
            import build_statsmeta                             # needs pefile (pip install pefile)
            sm = build_statsmeta.build(exe)
            old = json.loads(meta.read_text(encoding='utf-8')) if meta.exists() else {}
            meta.write_text(json.dumps({'game_build': old.get('game_build', ''), 'note': old.get('note', ''), **sm},
                                       ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        except (ImportError, LookupError) as e:               # keep the bundled table; say so
            print('statsmeta:', e, '- kept the bundled table')
        lap('statsmeta')
    return out


if __name__ == '__main__':
    d = build(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
    print(len(d['items']), 'items;', len(d['missions']), 'missions;', len(d['settlement']['perks']), 'perks;', list(d['stack_sizes']), 'stack levels;', {k: len(v) for k, v in d['texts'].items()}, 'texts')
