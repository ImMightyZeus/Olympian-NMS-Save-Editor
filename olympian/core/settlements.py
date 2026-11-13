"""Phase 5: the player's settlements (PlayerStateData.SettlementStatesV2) and an advisor.

Agreed limits (with the user): Olympian does NOT edit the settlement's stats and does NOT finish constructions
or upgrades. It only:
  * renames a settlement;
  * removes perks the settlement already has (it never adds any);
  * dismisses the pending decision (the game then brings the next one after its usual wait - resolving it in
    the game is what gives its rewards, the advisor says so).
Production is shown but not collected: the save keeps no accumulated amount (every slot reads 0 in real saves)
- the game works it out from its own timers when the player talks to the overseer, so collecting it here
would be a guess.

The game's own data behind what is shown (tools/build_gamedata.py): the perks table (texts, positive or
negative, which stat each one moves and how much) and GcSettlementGlobals (limits of each stat, the game's
good/bad thresholds, pending-decision texts, building names, the debt texts the game shows).

Which settlements are the player's: their Owner UID/LID is one of the player's own discovery-owner IDs
(CommonStateData.UsedDiscoveryOwnersV2). SettlementStatesV2 also keeps settlements of other players the
player visited; those are never shown or touched.
"""
from __future__ import annotations

import re
import time

from . import gamedata, missions
from .ops import FileChange, Plan, _common_checks, load_slot, msg
from .pathedit import PathEdit
from .savefile import SaveFile, SaveFolder, file_names

NAME_MAX = 40
RACES = {'Traders': 'gek', 'Warriors': 'vykeen', 'Explorers': 'korvax', 'Builders': 'autophage'}
# known mission routes that start at a settlement, in the order the game offers them
ROUTE = [('SETTLEMENT_PRE', ''), ('SETTLE_CLAIM', ''), ('SETTLE_TUT', ''), ('SETTLE_MGR', ''),
         ('SENTINELS_1', 'drone_mech'), ('SENTINELS_2', 'drone_mech'), ('SENTINELS_3', 'drone_mech'),
         ('SENTINELS_4', 'drone_mech'), ('SENTINELS_5', 'drone_mech'),
         ('ROBOT_INTRO', 'autophage'), ('SETTLE_BUI_SE', 'autophage')]


IMG_WORDS = {'SLASH': '—', 'UNITSMALL': 'u', 'UNITS': 'u'}     # names of inline images left in game texts


def _clean(t: str) -> str:
    t = re.sub(r'%[A-Z_]+%', '…', t or '')
    t = re.sub(r'\b(' + '|'.join(IMG_WORDS) + r')\b', lambda m: IMG_WORDS[m.group(1)], t)
    return re.sub(r'\s+', ' ', t).strip(' :')


def _named(t: str) -> str:
    """'' for a text that is only placeholders (procedural names the game makes up at run time)."""
    return t if re.sub(r'[…\s]', '', t) else ''


def owner_ids(f: SaveFile) -> set[str]:
    k = f.keys
    out = set()
    for o in ((f.json or {}).get(k.s('CommonStateData')) or {}).get(k.s('UsedDiscoveryOwnersV2')) or []:
        for key in ('UID', 'LID'):
            v = o.get(k.s(key)) if isinstance(o, dict) else None
            if v:
                out.add(str(v))
    return out


def owned(f: SaveFile) -> list[tuple[int, dict]]:
    """(index in SettlementStatesV2, entry) of the player's own settlements."""
    k = f.keys
    ids = owner_ids(f)
    out = []
    for i, s in enumerate(f.psd().get(k.s('SettlementStatesV2')) or []):
        o = s.get(k.s('Owner')) or {}
        if ids and (o.get(k.s('UID')) in ids or (o.get(k.s('LID')) or None) in ids):
            out.append((i, s))
    return out


def _uid(s: dict, k) -> str:
    return f"{s.get(k.s('UniqueId')) or ''}:{s.get(k.s('SeedValue')) or ''}"


def stat_state(i: int, v: int) -> dict:
    """Position of a stat between the game's limits and the game's own good/bad thresholds for it.
    Debt is special: any debt stops the settlement's production (game text UI_SETTLEMENT_PROD_DEBT)."""
    st = gamedata.settlement().get('stats') or {}
    lo, hi = st['min'][i], st['max'][i]
    norm = max(0.0, min(1.0, (v - lo) / (hi - lo))) if hi > lo else 0.0
    q = norm if st['good_high'][i] else 1 - norm
    if st['names'][i] == 'Debt':
        state = 'bad' if v > 0 else 'good'
    else:
        state = 'bad' if q <= st['bad'][i] else 'good' if q >= st['good'][i] else 'mid'
    return {'key': st['names'][i], 'v': v, 'min': lo, 'max': hi, 'norm': round(norm, 4), 'state': state,
            'good_high': st['good_high'][i]}


def perk_info(pid: str, lang: str) -> dict:
    sd = gamedata.settlement()
    base = (pid or '').lstrip('^').split('#')[0]
    p = (sd.get('perks') or {}).get(base)
    if not p:
        return {'id': pid, 'base': base, 'known': False, 'name': base, 'desc': '', 'neg': None, 'kind': 'unknown', 'fx': []}
    kind = 'starter' if p['start'] else 'blessing' if p['bless'] else 'job' if p['job'] else 'proc' if p['proc'] else 'extra'
    fx = []
    for stat, strength in p['fx']:
        rng = ((sd.get('strength') or {}).get(stat) or {}).get(strength) or [None, None]
        fx.append([stat, strength, rng[0], rng[1]])
    return {'id': pid, 'base': base, 'known': True, 'name': _named(_clean(gamedata.text(p['n'], lang))),
            'desc': _named(_clean(gamedata.text(p['d'], lang))), 'neg': p['neg'], 'kind': kind, 'fx': fx,
            'generated': '%' in gamedata.text(p['n'], 'en')}


def _pending(s: dict, k, lang: str) -> dict | None:
    jt = (s.get(k.s('PendingJudgementType')) or {}).get(k.s('SettlementJudgementType')) or 'None'
    if jt == 'None':
        return None
    J = gamedata.settlement().get('judgement') or {}
    types = J.get('types') or []
    i = types.index(jt) if jt in types else -1
    pick = lambda f: _clean(gamedata.text(J[f][i], lang)) if i >= 0 and J.get(f) and J[f][i] else ''
    return {'type': jt, 'title': pick('title'), 'sub': pick('sub'), 'text': pick('text'), 'objective': pick('objective'),
            'custom': (s.get(k.s('PendingCustomJudgementID')) or '^').lstrip('^')}


def _building(s: dict, k, name: str, lang: str) -> dict | None:
    cls = (s.get(k.s('NextBuildingUpgradeClass')) or {}).get(k.s('BuildingClass')) or 'None'
    if cls == 'None':
        return None
    key = (gamedata.settlement().get('buildings') or {}).get(cls) or ''
    t = gamedata.text(key, lang).replace('%SETTLEMENT%', name).strip() if key else ''
    return {'cls': cls, 'name': re.sub(r'\s+', ' ', t), 'index': s.get(k.s('NextBuildingUpgradeIndex'))}


def perk_label(p: dict) -> str:
    return p['name'] or p['desc'] or p['base']


def advice(st: dict, lang: str) -> list[dict]:
    """What the advisor says for one settlement: facts from the save, measured against the game's own data."""
    out = []
    S = {x['key']: x for x in st['stats']}
    debt, prod, cost = S['Debt']['v'], S['Production']['v'], S['Upkeep']['v']
    if debt > 0:
        out.append({'level': 'warn', 'code': 'adv_debt', 'p': {'debt': debt, 'prod': prod, 'cost': cost,
                    'game': _clean(gamedata.text('UI_SETTLEMENT_PROD_DEBT', lang)),
                    'tip': _clean(gamedata.text('UI_SETTLEMENT_PROD_DEBT_TIP', lang))}})
        out.append({'level': 'info', 'code': 'adv_debt_compare' if prod > cost else 'adv_debt_compare_bad',
                    'p': {'prod': prod, 'cost': cost}})
    # what is pulling each weak stat down: the settlement's own negative perks
    neg = {}
    for p in st['perks']:
        for stat, strength, _lo, _hi in p['fx']:
            if strength.startswith('Negative'):
                neg.setdefault(stat, []).append(perk_label(p))
    for x in st['stats']:
        # MaxPopulation reads 0 in current saves (the population itself is kept apart): not judged
        if x['state'] == 'bad' and x['key'] not in ('Debt', 'MaxPopulation'):
            out.append({'level': 'warn', 'code': 'adv_stat_bad', 'p': {'stat': x['key'], 'v': x['v'], 'max': x['max'],
                        'perks': ', '.join(neg.get(x['key'], [])[:4])}})
    negp = [p for p in st['perks'] if p['neg']]
    if negp:
        out.append({'level': 'info', 'code': 'adv_neg_perks', 'p': {'n': len(negp),
                    'names': ', '.join(perk_label(p) for p in negp[:6])}})
    if st['pending']:
        out.append({'level': 'info', 'code': 'adv_pending', 'p': {'title': st['pending']['title'] or st['pending']['type']}})
    if st['building']:
        out.append({'level': 'info', 'code': 'adv_building', 'p': {'name': st['building']['name'] or st['building']['cls']}})
    mp = gamedata.settlement().get('max_perks')
    if mp and len(st['perks']) >= mp:
        out.append({'level': 'info', 'code': 'adv_perks_full', 'p': {'n': len(st['perks']), 'max': mp}})
    if not out:
        out.append({'level': 'ok', 'code': 'adv_all_good', 'p': {}})
    return out


def route(files: list[SaveFile], lang: str) -> list[dict]:
    """Known mission routes that start at a settlement, with the player's progress in each."""
    have = {}
    for m in missions.describe(files[:1], lang)['missions']:
        have.setdefault(m['id'], m)
    out = []
    for mid, reward in ROUTE:
        d = missions.info(mid) or {}
        m = have.get(mid)
        p = m['progress'] if m else None
        if m is None:
            state = 'absent'
        elif p == -1:
            state = 'inactive'
        elif p == missions.MARK or (d.get('n') is not None and p >= d['n']):
            state = 'end'
        else:
            state = 'stage'
        title = _clean(gamedata.text(d.get('t', ''), lang)) or mid
        out.append({'id': mid, 'title': title, 'sub': _clean(gamedata.text(d.get('s', ''), lang)), 'state': state,
                    'progress': p, 'n': d.get('n'), 'reward': reward,
                    'steps': missions.steps(mid, p, lang) if state == 'stage' else None,
                    'current': bool(m and m['current'])})
    return out


def describe(files: list[SaveFile], lang: str = 'es') -> dict:
    f = files[0]
    k = f.keys
    sd = gamedata.settlement()
    if not sd:
        return {'available': False, 'settlements': [], 'route': []}
    hist = {h.get(k.s('SeedValue')): h for h in f.psd().get(k.s('SettlementHistory')) or [] if isinstance(h, dict)}
    out = []
    for i, s in owned(f):
        name = s.get(k.s('Name')) or ''
        stats = [stat_state(n, v) for n, v in enumerate((s.get(k.s('Stats')) or [])[:8])]
        perks = [{**perk_info(pid, lang), 'i': j} for j, pid in enumerate(s.get(k.s('Perks')) or [])]
        prod = []
        for p in s.get(k.s('ProductionState')) or []:
            eid = (p.get(k.s('ElementId')) or '^').lstrip('^')
            if eid:
                prod.append({'id': eid, 'cap': p.get(k.s('ProductionAccumulationCap')), 'amount': p.get(k.s('Amount'))})
        h = hist.get(s.get(k.s('SeedValue'))) or {}
        st = {'uid': _uid(s, k), 'index': i, 'name': name,
              'race': RACES.get((s.get(k.s('Race')) or {}).get(k.s('AlienRace')), 'unknown'),
              'population': s.get(k.s('Population')), 'stats': stats, 'perks': perks,
              'pending': _pending(s, k, lang), 'last_judgement': s.get(k.s('LastJudgementTime')) or 0,
              'production': prod, 'building': _building(s, k, name, lang),
              'claimed': h.get(k.s('PlayerClaimedTime')) or 0, 'judgements_settled': h.get(k.s('JudgementsSettled'))}
        st['advice'] = advice(st, lang)
        out.append(st)
    return {'available': True, 'settlements': out, 'route': route(files, lang), 'max_perks': sd.get('max_perks'),
            'owner_found': bool(owner_ids(f)), 'max_population': sd.get('max_population')}


# ------------------------------------------------------------------------------- actions -------
def _find(f: SaveFile, uid: str) -> int | None:
    k = f.keys
    for i, s in owned(f):
        if _uid(s, k) == uid:
            return i
    return None


def plan_settlement(folder: SaveFolder, slot: int, action: str, params: dict | None = None,
                    files: list[SaveFile] | None = None) -> Plan:
    params = params or {}
    plan = Plan('set_' + action, folder.path, staged=files is not None)
    files = files if files is not None else load_slot(folder, slot)
    _common_checks(plan, files, check_game=not plan.staged)
    if action not in ('rename', 'perk_remove', 'skip_decision'):
        raise LookupError(action)
    lang = str(params.get('lang') or 'es')
    uid = str(params.get('uid') or '')
    first = next((s for s in describe(files[:1], lang)['settlements'] if s['uid'] == uid), None)
    if first is None:
        plan.messages.append(msg('block', 'set_not_found'))
        return plan
    label = first['name'] or '—'
    if action == 'rename':
        name = ' '.join(str(params.get('name') or '').split())
        if not name or len(name) > NAME_MAX or any(ord(c) < 32 for c in name):
            plan.messages.append(msg('block', 'set_name_invalid', max=NAME_MAX))
            return plan
        if name == first['name']:
            plan.messages.append(msg('block', 'set_nothing'))
            return plan
    if action == 'perk_remove':
        try:
            pick = sorted({int(x) for x in params.get('perks') or []})
            ids = [str(x) for x in params.get('ids') or []]
        except (TypeError, ValueError):
            pick, ids = [], []
        cur = [p['id'] for p in first['perks']]
        if not pick or any(not 0 <= x < len(cur) for x in pick) or ids != [cur[x] for x in pick]:
            plan.messages.append(msg('block', 'set_perk_pick'))
            return plan
        removed = [first['perks'][x] for x in pick]
    if action == 'skip_decision' and not first['pending']:
        plan.messages.append(msg('block', 'set_no_decision'))
        return plan

    now = int(time.time())
    for f in files:
        k = f.keys
        fname = file_names(f.file_index)[0]
        i = _find(f, uid)
        if i is None:
            plan.messages.append(msg('info', 'set_not_in_file', file=fname))
            continue
        base = k.psd + [k('SettlementStatesV2'), i]
        pe = PathEdit(f)
        if action == 'rename':
            pe.set(base + [k('Name')], name)
        elif action == 'perk_remove':
            cur = pe.get(base + [k('Perks')])
            want = [first['perks'][x]['id'] for x in pick]
            if [cur[x] if x < len(cur) else None for x in pick] != want:
                plan.messages.append(msg('block', 'set_files_differ', file=fname))
                return plan
            pe.set(base + [k('Perks')], [p for n, p in enumerate(cur) if n not in set(pick)])
        else:
            pe.set(base + [k('PendingJudgementType'), k('SettlementJudgementType')], 'None')
            pe.set(base + [k('PendingCustomJudgementID')], '^')
            pe.set(base + [k('LastJudgementTime')], now)
        fc = FileChange(f.file_index, f, pe.result())
        fc.json = pe.patched_json()
        plan.changes.append(fc)
    if not plan.changes:
        plan.messages.append(msg('block', 'set_not_found'))
        return plan
    if action == 'rename':
        plan.messages.append(msg('info', 'set_rename_what', old=label, new=name))
        plan.summary = msg('info', 'chg_set_rename', old=label, new=name)
    elif action == 'perk_remove':
        names = ', '.join(perk_label(p) for p in removed)
        plan.messages.append(msg('warn', 'set_perk_remove_what', n=len(removed), names=names, name=label))
        if any(p['neg'] is False for p in removed):
            plan.messages.append(msg('warn', 'set_perk_remove_positive'))
        plan.messages.append(msg('info', 'set_perk_remove_note'))
        plan.summary = msg('info', 'chg_set_perk_remove', n=len(removed), names=names, name=label)
    else:
        title = first['pending']['title'] or first['pending']['type']
        plan.messages.append(msg('warn', 'set_skip_what', title=title, name=label))
        plan.messages.append(msg('info', 'set_skip_note', lo=round(gamedata.settlement()['judgement']['wait'][0] / 60),
                                 hi=round(gamedata.settlement()['judgement']['wait'][1] / 60)))
        plan.summary = msg('info', 'chg_set_skip', title=title, name=label)
    return plan
