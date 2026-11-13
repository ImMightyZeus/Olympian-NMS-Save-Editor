"""Phase 4: missions (PlayerStateData.MissionProgress).

What the save keeps for each mission: its ID, `Progress`, a seed and participants. What `Progress` means,
from the game's own mission tables and the community's long-standing practice:
  -1          the mission was never started (or was reset): this is how a mission is reset;
  0..n        position in the mission's tree of stages (n = stages in the whole tree; branches make it a
              position, not a percentage);
  2147483647  a special mark the game writes on procedural / repeatable missions (settlements, sentinel
              crash sites, deliveries...). Its exact meaning is not documented, so it is shown as is.

Actions (staged like the rest of the editor, verified byte by byte):
  * reset     Progress -> -1 for the chosen entries (optionally the next steps of the same series, e.g.
              ACT2_STEP8 -> also ACT2_STEP9, 10...), as the community does to replay a mission;
  * delete    remove entries (for stuck or duplicated ones); the mission the game has active is refused;
  * stage     EXPERIMENTAL: set Progress to a given stage. Each stage normally also writes other data
              (items, markers, other missions) that this does not write, so it can leave things half done.
"""
from __future__ import annotations

import copy
import re

from . import gamedata, jspan
from .ops import FileChange, Plan, _common_checks, load_slot, msg
from .savefile import SaveFile, SaveFolder, file_names

MARK = 2147483647
SERIES = re.compile(r'^(.*?)(\d+)$')

# mission table -> group shown to the user
GROUPS = {'core': 'story', 'atlaspath': 'atlas', 'tutorial': 'tutorial', 'basecomputer': 'base',
          'npcbuilders': 'base_npc', 'npc': 'base_npc', 'water': 'water', 'seasonal': 'expedition',
          'seasonalbespoke': 'expedition', 'recurring': 'nexus', 'community': 'nexus', 'wiki': 'catalogue',
          'spacepoi': 'procedural', 'spaceoutpost': 'procedural', 'planetproc': 'procedural',
          'sentinelsettlement': 'settlement', 'fleet': 'fleet', 'pirate': 'pirate', 'corvette': 'corvette',
          'multiplayer': 'multiplayer', 'statstories': 'stats', 'swarm': 'swarm', 'startedonuse': 'items',
          'deprecated': 'old', 'debug': 'old', 'disablingconditions': 'old', 'mission': 'other'}


def _defs() -> dict:
    d = gamedata.load()
    return (d or {}).get('missions') or {}


def info(mid: str) -> dict | None:
    return _defs().get((mid or '').lstrip('^'))


def _text(key: str, lang: str) -> str:
    if not key:
        return ''
    d = gamedata.load() or {}
    t = (d.get('texts') or {}).get(lang, {}).get(key) or (d.get('texts') or {}).get('en', {}).get(key) or ''
    return re.sub(r'%[A-Z_]+%', '…', t)        # placeholders filled by the game at run time


def _clean(t: str) -> str:
    t = re.sub(r'%[A-Z_]+%', '…', t)                       # values the game fills in at run time
    t = re.sub(r'\s*\(?\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b\)?', '', t)   # controller/key placeholders (QUICK_MENU)
    return re.sub(r'[ \t]+', ' ', t).strip()


def objective(key: str, lang: str) -> dict | None:
    """{'main': first line, 'more': the tips under it} of an objective text."""
    t = _text(key, lang) if key else ''
    if not t:
        return None
    lines = [_clean(x) for x in t.split('\n')]
    lines = [x for x in lines if x]
    return {'main': lines[0], 'more': lines[1:4]} if lines else None


def steps(mid: str, progress: int, lang: str = 'es') -> dict:
    """What to do now and next, from the game's objective texts of the mission's stages (the save's Progress is
    the position in the stage tree, in the same order the game data lists the stages)."""
    d = info(mid) or {}
    h = d.get('h') or []
    if not h or progress == MARK or (d.get('n') is not None and progress >= d['n']):
        return {'now': None, 'next': None}
    if progress == -1:
        return {'now': None, 'next': objective(h[0][1], lang)}
    cur = [k for i, k in h if i <= progress]
    nxt = [k for i, k in h if i > progress]
    return {'now': objective(cur[-1], lang) if cur else None, 'next': objective(nxt[0], lang) if nxt else None}


def state(progress: int) -> str:
    return 'inactive' if progress == -1 else 'mark' if progress == MARK else 'stage'


def describe(files: list[SaveFile], lang: str = 'es') -> dict:
    f = files[0]
    k = f.keys
    psd = f.psd()
    cur, prev = psd.get(k.s('CurrentMissionID')), psd.get(k.s('PreviousMissionID'))
    out, seen = [], {}
    for i, e in enumerate(psd.get(k.s('MissionProgress')) or []):
        mid = e.get(k.s('Mission')) or ''
        seen[mid] = seen.get(mid, 0) + 1
        d = info(mid) or {}
        p = e.get(k.s('Progress'))
        title, sub = _text(d.get('t', ''), lang), _text(d.get('s', ''), lang)
        if title and not re.search(r'[^\W\d_]', title):      # some titles are only a counter ("16 / 16")
            title, sub = sub or title, ''
        out.append({'i': i, 'id': mid.lstrip('^'), 'progress': p, 'state': state(p), 'n': d.get('n'),
                    'title': title, 'sub': sub,
                    'cls': d.get('cls', ''), 'crit': bool(d.get('crit')), 'group': GROUPS.get(d.get('tab'), 'unknown'),
                    'known': bool(d), 'current': mid == cur, 'previous': mid == prev, 'copy': seen[mid]})
    dup = {m for m, c in seen.items() if c > 1}
    for o in out:
        o['dup'] = ('^' + o['id']) in dup
    return {'missions': out, 'current': (cur or '').lstrip('^'), 'previous': (prev or '').lstrip('^'),
            'version': psd.get(k.s('MissionVersion'))}


def series_after(entries: list[dict], idx: int) -> list[int]:
    """Indices of the next steps of the same series (ACT2_STEP8 -> ACT2_STEP9, 10...)."""
    m = SERIES.match(entries[idx]['id'])
    if not m:
        return []
    pre, n = m.group(1), int(m.group(2))
    out = []
    for e in entries:
        mm = SERIES.match(e['id'])
        if mm and mm.group(1) == pre and int(mm.group(2)) > n:
            out.append(e['i'])
    return out


class MissionEdit:
    def __init__(self, f: SaveFile):
        self.f, self.k, self.b = f, f.keys, f.raw
        self.idx = jspan.index_for(self.b)
        self.span = self.idx.get(self.k.psd + [self.k('MissionProgress')])
        spans = jspan.arr_items(self.b, self.span[0])
        parsed = self.f.psd()[self.k.s('MissionProgress')]
        if len(spans) != len(parsed):
            raise RuntimeError('mis_verify')
        self.items = [[self.b[a:z], copy.deepcopy(p)] for (a, z), p in zip(spans, parsed)]
        self.before = len(self.items)
        self.scalars: dict[str, object] = {}           # PlayerStateData field -> new value (active mission...)

    def set_field(self, name: str, value):
        self.scalars[name] = value

    def set_progress(self, i: int, value: int):
        s, e = jspan.get(self.items[i][0], [self.k('Progress')], 0)
        self.items[i][0] = self.items[i][0][:s] + b'%d' % value + self.items[i][0][e:]
        self.items[i][1][self.k.s('Progress')] = value

    def delete(self, idxs: set[int]):
        self.items = [it for n, it in enumerate(self.items) if n not in idxs]

    def result(self) -> bytes:
        k = self.k
        new_arr = b'[' + b','.join(b for b, _p in self.items) + b']'
        ed = [(self.span[0], self.span[1], new_arr)]
        for name, v in self.scalars.items():
            s, e = self.idx.get(k.psd + [k(name)])
            ed.append((s, e, jspan.dumps_value(v)))
        ed.sort(key=lambda t: t[0])
        for x, y in zip(ed, ed[1:]):
            if x[1] > y[0]:
                raise RuntimeError('mis_overlap')
        out, pos = bytearray(), 0
        for s, e, r in ed:
            out += self.b[pos:s] + r
            pos = e
        out += self.b[pos:]
        new = bytes(out)
        # verification: outside the edited parts nothing changed, and each part parses back as expected
        po = pn = 0
        for s, e, r in ed:
            if new[pn:pn + (s - po)] != self.b[po:s]:
                raise RuntimeError('mis_verify_outside')
            pn += (s - po) + len(r)
            po = e
        if new[pn:] != self.b[po:]:
            raise RuntimeError('mis_verify_outside')
        jspan.carry_index(self.b, new, [(s, e, r) for s, e, r in sorted(ed, key=lambda t: t[0], reverse=True)])
        nidx = jspan.index_for(new)
        s, e = nidx.get(k.psd + [k('MissionProgress')])
        if jspan.loads(new[s:e]) != [p for _b, p in self.items]:
            raise RuntimeError('mis_verify')
        for name, v in self.scalars.items():
            s, e = nidx.get(k.psd + [k(name)])
            if jspan.loads(new[s:e]) != v:
                raise RuntimeError('mis_verify')
        return new

    def patched_json(self):
        k = self.k
        j = self.f.json
        if j is None:
            return None
        j2 = dict(j)
        bc = dict(j2[k.s('BaseContext')])
        psd = dict(bc[k.s('PlayerStateData')])
        psd[k.s('MissionProgress')] = [p for _b, p in self.items]
        for name, v in self.scalars.items():
            psd[k.s(name)] = v
        bc[k.s('PlayerStateData')] = psd
        j2[k.s('BaseContext')] = bc
        return j2


def neutral_for(f: SaveFile, avoid: set[str]) -> dict | None:
    """A mission to leave active while the reset one restarts (what the community recommends: switch to a
    neutral mission, then back). Order: the previous mission if still running, an Atlas loop mission,
    any running Atlas mission, any other running mission. None if there is none."""
    k = f.keys
    psd = f.psd()
    entries = []
    for e in psd.get(k.s('MissionProgress')) or []:
        mid = (e.get(k.s('Mission')) or '').lstrip('^')
        p = e.get(k.s('Progress'))
        d = info(mid) or {}
        if mid in avoid or p == -1 or p == MARK or not d:
            continue
        if d.get('n') is not None and p >= d['n']:
            continue                                   # at the end of its tree: nothing left to follow
        entries.append((mid, e.get(k.s('Seed')) or 0, d))
    prev = (psd.get(k.s('PreviousMissionID')) or '').lstrip('^')
    order = [lambda m, d: m == prev,
             lambda m, d: m.startswith('ATLAS_LOOP'),
             lambda m, d: GROUPS.get(d.get('tab')) == 'atlas',
             lambda m, d: d.get('cls') in ('Primary', 'Atlas', 'Secondary')]
    for test in order:
        for mid, seed, d in entries:
            if test(mid, d):
                return {'id': mid, 'seed': seed}
    return None


def _keys(f: SaveFile) -> list[tuple[str, int]]:
    """(mission id, copy number) of every entry of a file - how a mission is identified across the files
    of a slot, which may hold different lists (an autosave and a manual save)."""
    k = f.keys
    seen, out = {}, []
    for e in f.psd().get(k.s('MissionProgress')) or []:
        mid = (e.get(k.s('Mission')) or '').lstrip('^')
        seen[mid] = seen.get(mid, 0) + 1
        out.append((mid, seen[mid]))
    return out


def _pick(params) -> list[tuple[str, int]] | None:
    try:
        sel = [(str(a), int(b)) for a, b in params.get('sel') or []]
    except (TypeError, ValueError):
        return None
    return sel if sel and len(sel) <= 2000 and len(set(sel)) == len(sel) else None


def plan_missions(folder: SaveFolder, slot: int, action: str, params: dict | None = None,
                  files: list[SaveFile] | None = None) -> Plan:
    params = params or {}
    plan = Plan('mis_' + action, folder.path, staged=files is not None)
    files = files if files is not None else load_slot(folder, slot)
    _common_checks(plan, files, check_game=not plan.staged)
    if action not in ('reset', 'delete', 'stage'):
        raise LookupError(action)
    lang = str(params.get('lang') or 'es')
    sel = _pick(params)
    if sel is None:
        plan.messages.append(msg('block', 'mis_pick'))
        return plan
    first = describe(files[:1], lang)['missions']
    by_key = {(e['id'], e['copy']): e for e in first}
    if any(x not in by_key for x in sel):
        plan.messages.append(msg('block', 'mis_pick'))
        return plan
    if action == 'reset' and params.get('chain'):
        for x in list(sel):
            for j in series_after(first, by_key[x]['i']):
                y = (first[j]['id'], first[j]['copy'])
                if y not in sel:
                    sel.append(y)
    chosen = [by_key[x] for x in sel]
    names = [e['title'] or e['id'] for e in chosen]
    if action == 'stage':
        if len(sel) != 1:
            plan.messages.append(msg('block', 'mis_stage_one'))
            return plan
        e = chosen[0]
        try:
            to = int(params.get('to'))
        except (TypeError, ValueError):
            to = None
        if to is None or e['n'] is None or not 0 <= to <= e['n']:
            plan.messages.append(msg('block', 'mis_stage_range', n=e['n'] or 0))
            return plan
    changed = 0
    switched: list[tuple[str, str]] = []
    for f in files:
        k = f.keys
        cur = (f.psd().get(k.s('CurrentMissionID')) or '').lstrip('^')
        pos = {key: i for i, key in enumerate(_keys(f))}
        here = [pos[x] for x in sel if x in pos]
        fname = file_names(f.file_index)[0]
        if len(here) < len(sel):
            plan.messages.append(msg('info', 'mis_not_in_file', n=len(sel) - len(here), file=fname))
        if action == 'delete' and any(x[0] == cur for x in sel if x in pos):
            plan.messages.append(msg('block', 'mis_delete_current', id=cur, file=fname))
            return plan
        me = MissionEdit(f)
        if action == 'reset':
            for i in here:
                if me.items[i][1][k.s('Progress')] != -1:
                    me.set_progress(i, -1)
                    changed += 1
        elif action == 'delete':
            me.delete(set(here))
            changed += len(here)
        else:
            for i in here:
                if me.items[i][1][k.s('Progress')] != int(params['to']):
                    me.set_progress(i, int(params['to']))
                    changed += 1
        if action == 'reset' and cur and any(x[0] == cur for x in sel if x in pos):
            # the game does not restart the mission it has active: leave a neutral one active instead
            nt = neutral_for(f, {x[0] for x in sel})
            if nt is None:
                plan.messages.append(msg('block', 'mis_no_neutral', id=cur, file=fname))
                return plan
            old_seed = f.psd().get(k.s('CurrentMissionSeed')) or 0
            me.set_field('CurrentMissionID', '^' + nt['id'])
            me.set_field('CurrentMissionSeed', nt['seed'])
            me.set_field('PreviousMissionID', '^' + cur)
            me.set_field('PreviousMissionSeed', old_seed)
            nd = info(nt['id']) or {}
            plan.messages.append(msg('warn', 'mis_switched', file=fname,
                                     neutral=_text(nd.get('t', ''), lang) or nt['id'], neutral_id=nt['id'],
                                     reset=next((e['title'] for e in chosen if e['id'] == cur), '') or cur, reset_id=cur))
            switched.append((nt['id'], cur))
            changed += 1
        fc = FileChange(f.file_index, f, me.result())
        fc.json = me.patched_json()
        plan.changes.append(fc)
    if not changed:
        plan.changes.clear()
        plan.messages.append(msg('block', 'mis_nothing'))
        return plan

    shown = ', '.join(names[:6]) + (f' (+{len(names) - 6})' if len(names) > 6 else '')
    if action == 'reset':
        plan.messages.append(msg('info', 'mis_reset_what', n=len(sel), names=shown))
        plan.messages.append(msg('warn', 'mis_reset_note'))
    elif action == 'delete':
        plan.messages.append(msg('warn', 'mis_delete_what', n=len(sel), names=shown))
        if any(e['crit'] for e in chosen):
            plan.messages.append(msg('warn', 'mis_delete_story'))
    else:
        e = chosen[0]
        plan.messages.append(msg('warn', 'mis_stage_what', name=names[0], old=e['progress'], new=int(params['to']), n=e['n']))
        plan.messages.append(msg('warn', 'mis_stage_risk'))
    plan.summary = msg('info', 'chg_mis_' + action, n=len(sel), names=shown)
    if switched:
        nid, rid = switched[0]
        plan.summary = msg('info', 'chg_mis_reset_switch', n=len(sel), names=shown,
                           neutral=_text((info(nid) or {}).get('t', ''), lang) or nid,
                           reset=_text((info(rid) or {}).get('t', ''), lang) or rid)
    return plan
