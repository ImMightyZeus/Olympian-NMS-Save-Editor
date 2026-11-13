"""Phase-1 operations. Every operation is planned first (preview) and applied later.

Messages are returned as {code, p} so the interface can show them in the user's language.
Levels: 'info' (what will change), 'ok', 'warn' (review), 'danger' (may break things), 'block' (cannot apply).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import jspan, safety
from .coords import Address, galaxies, galaxy_name
from .savefile import SaveFile, SaveFolder, files_of_slot, file_names, verify_pair


def msg(level: str, code: str, **p) -> dict:
    return {'level': level, 'code': code, 'p': p}


@dataclass
class FileChange:  # noqa: D101
    target_index: int            # file index that will be written
    source: SaveFile             # loaded file the bytes come from (for meta template / mtime guard)
    new_raw: bytes
    name: str | None = None      # new save name for the manifest (None = keep)


@dataclass
class Plan:
    action: str
    folder: Path
    changes: list[FileChange] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: float = field(default_factory=time.time)
    restore_point: Path | None = None   # for restore operations (file copy, no JSON edit)
    summary: dict | None = None         # one-line description for the pending-changes list
    staged: bool = False                # True = goes to the working copy, written only on Save
    dest_slot: int | None = None        # slot written by copy / restore (for the history)

    @property
    def blocked(self) -> bool:
        return any(m['level'] == 'block' for m in self.messages)

    def public(self) -> dict:
        seen, msgs = set(), []
        for m in self.messages:  # both files of a slot usually yield the same message
            k = json.dumps(m, sort_keys=True, ensure_ascii=False)
            if k not in seen:
                seen.add(k)
                msgs.append(m)
        return {'id': self.id, 'action': self.action, 'blocked': self.blocked, 'messages': msgs, 'staged': self.staged,
                'files': sorted({file_names(c.target_index)[0] for c in self.changes})}


# ---------------------------------------------------------------- helpers ------------------
def load_slot(folder: SaveFolder, slot: int) -> list[SaveFile]:
    out = []
    for fi in files_of_slot(slot):
        f = folder.file(fi)
        if f.exists():
            out.append(f.load())
    if not out:
        raise FileNotFoundError(f'slot {slot}')
    return out


def _set_name_in_json(raw: bytes, k, name: str) -> bytes:
    """The game reads the save name from CommonStateData.SaveName (top of the file) and writes the
    manifest name from it on every in-game save. Older Olympian versions wrote PlayerStateData.SaveName
    instead (a key the game ignores); that stray copy is removed here."""
    csd = [k('CommonStateData'), k('SaveName')]
    ed = jspan.Editor(raw)
    if not ed.has(csd):                      # very old saves without CommonStateData
        if ed.has(k.psd + [k('SaveName')]):
            ed.set(k.psd + [k('SaveName')], name)
            return ed.apply()
        s, _ = ed.index.get(k.psd)
        s = jspan.ws(raw, s)
        ins = b'"' + k('SaveName') + b'":' + jspan.dumps_value(name) + b','
        new = raw[:s + 1] + ins + raw[s + 1:]
        jspan.carry_index(raw, new, [(s + 1, s + 1, ins)])
        return new
    ed.set(csd, name)
    stray = _stray_psd_name(raw, k, ed.index)
    if stray:
        ed.replace(stray, b'')
    return ed.apply()


def _stray_psd_name(raw: bytes, k, idx) -> tuple[int, int] | None:
    """Span of '"SaveName":"…",' inside PlayerStateData (only when CommonStateData exists)."""
    if not idx.has(k.psd + [k('SaveName')]):
        return None
    ps, _ = idx.get(k.psd)
    vs, ve = idx.get(k.psd + [k('SaveName')])
    ks = raw.rfind(b'"' + k('SaveName') + b'"', ps, vs)
    j = jspan.ws(raw, ve)
    if raw[j:j + 1] == b',':
        return ks, jspan.ws(raw, j + 1)
    c = raw.rfind(b',', ps, ks)              # last key of the object: drop the comma before it
    return (c, ve) if c != -1 else (ks, ve)


def save_name(f: SaveFile) -> tuple[str, bool]:
    """(name, persisted). persisted=False when the manifest shows a name the game will lose."""
    k = f.keys
    j = f.json
    csd = j.get(k.s('CommonStateData'))
    if isinstance(csd, dict):
        n = csd.get(k.s('SaveName'), '') or ''
        if n:
            return n, True
        return f.meta.name or '', not f.meta.name
    return f.meta.name or f.psd().get(k.s('SaveName'), '') or '', True


def _float(v: float) -> str:
    return repr(float(v))


def describe_slot(folder: SaveFolder, slot: int, files_in: list[SaveFile] | None = None,
                  names: dict | None = None) -> dict:
    files = []
    for f in (files_in if files_in is not None else load_slot(folder, slot)):
        k = f.keys
        psd = f.psd()
        ua = psd[k.s('UniverseAddress')]
        ga = ua[k.s('GalacticAddress')]
        ships = psd.get(k.s('ShipOwnership'), [])
        spawn = f.json[k.s('BaseContext')].get(k.s('SpawnStateData'), {})
        files.append({
            'file': file_names(f.file_index)[0], 'file_index': f.file_index,
            'name': (names or {}).get(f.file_index) or save_name(f)[0],
            'name_ok': bool((names or {}).get(f.file_index)) or save_name(f)[1],
            'freighter': _freighter(psd, k),
            'summary': f.meta.summary,
            'timestamp': f.meta.timestamp, 'mtime': f.mtime_hg, 'playtime': f.meta.playtime,
            'galaxy': ua[k.s('RealityIndex')], 'galaxy_name': galaxy_name(ua[k.s('RealityIndex')]),
            'address': {'x': ga[k.s('VoxelX')], 'y': ga[k.s('VoxelY')], 'z': ga[k.s('VoxelZ')],
                        'system': ga[k.s('SolarSystemIndex')], 'planet': ga[k.s('PlanetIndex')]},
            'state': spawn.get(k.s('LastKnownPlayerState')),
            'primary_ship': psd.get(k.s('PrimaryShip')),
            'ships': [{'index': i, 'name': s.get(k.s('Name'), ''),
                       'model': s[k.s('Resource')][k.s('Filename')].split('/')[-1].split('.')[0],
                       'type': ship_type(s[k.s('Resource')][k.s('Filename')]),
                       'info': _ship_info(s, k) if s[k.s('Resource')][k.s('Filename')] else None,
                       'seed': (s[k.s('Resource')].get(k.s('Seed')) or [None, None])[1]}
                      for i, s in enumerate(ships)],
        })
    newest = max(files, key=lambda x: x['mtime'])
    for x in files:
        x['newest'] = x is newest
    return {'slot': slot, 'files': files}


def _game_check(plan: Plan):
    running = safety.game_running()
    if running:
        plan.messages.append(msg('block', 'game_running'))
    elif running is None:
        plan.messages.append(msg('warn', 'game_unknown'))


def _common_checks(plan: Plan, files: list[SaveFile], check_game: bool = True):
    if check_game:
        _game_check(plan)
    if len(files) == 2:
        names = [[s.get(f.keys.s('Name')) for s in f.psd().get(f.keys.s('ShipOwnership'), [])] for f in files]
        if names[0] != names[1]:
            plan.messages.append(msg('info', 'files_differ'))


# Ship family from the model path. Seen in real saves: SENTINELSHIP (Interceptor), BIOSHIP (Living Ship),
# BIGGS (Corvette), S-CLASS_PROC (Exotic). The others follow the game's folder names; unknown -> 'ship'.
_SHIP_TYPES = [('BIOSHIP', 'living'), ('SENTINELSHIP', 'interceptor'), ('BIGGS', 'corvette'),
               ('S-CLASS', 'exotic'), ('SAILSHIP', 'solar'), ('DROPSHIP', 'hauler'), ('SCIENTIFIC', 'explorer'),
               ('SHUTTLE', 'shuttle'), ('FIGHTER', 'fighter'), ('ROYAL', 'exotic')]


_FREIGHTERS = [('PIRATEFREIGHTER', 'dreadnought'), ('CAPITALFREIGHTER', 'capital'), ('FREIGHTER', 'freighter')]


def _freighter(psd, k):
    try:
        res = psd.get(k.s('CurrentFreighter')) or {}
        fn = res.get(k.s('Filename'), '')
        if not fn:
            return None
        inv = psd.get(k.s('FreighterInventory')) or {}
        tech = psd.get(k.s('FreighterInventory_TechOnly')) or {}
        ua = psd.get(k.s('FreighterUniverseAddress')) or {}
        return {'name': psd.get(k.s('PlayerFreighterName'), ''),
                'type': next((t for key, t in _FREIGHTERS if key in fn.upper()), 'freighter'),
                'model': fn.split('/')[-1].split('.')[0],
                'cls': (inv.get(k.s('Class')) or {}).get(k.s('InventoryClass'), ''),
                'cells': len(inv.get(k.s('ValidSlotIndices'), [])),
                'tech_cells': len(tech.get(k.s('ValidSlotIndices'), [])),
                'galaxy': ua.get(k.s('RealityIndex'))}
    except Exception:  # noqa: BLE001
        return None


def _ship_info(ship, k):
    from .ships import summary   # local import: ships.py builds on this module
    try:
        return summary(ship, k)
    except Exception:  # noqa: BLE001 - never break the slot view over one odd ship
        return None


def ship_type(filename: str) -> str:
    if not filename:
        return 'empty'
    up = filename.upper()
    return next((t for key, t in _SHIP_TYPES if key in up), 'ship')


# ---------------------------------------------------------------- operations ---------------
def plan_set_primary(folder: SaveFolder, slot: int, ship_index: int, files: list[SaveFile] | None = None) -> Plan:
    plan = Plan('set_primary', folder.path, staged=files is not None)
    files = files if files is not None else load_slot(folder, slot)
    _common_checks(plan, files, check_game=not plan.staged)
    for f in files:
        k = f.keys
        ships = f.psd()[k.s('ShipOwnership')]
        if not (0 <= ship_index < len(ships)) or not ships[ship_index][k.s('Resource')][k.s('Filename')]:
            plan.messages.append(msg('block', 'ship_empty', file=file_names(f.file_index)[0], n=ship_index + 1))
            continue
        old = f.psd()[k.s('PrimaryShip')]
        ed = jspan.Editor(f.raw)
        ed.set(k.psd + [k('PrimaryShip')], ship_index)
        plan.changes.append(FileChange(f.file_index, f, ed.apply()))
        plan.messages.append(msg('info', 'primary_change', file=file_names(f.file_index)[0],
                                 old=old + 1, old_name=ships[old].get(k.s('Name'), '') if 0 <= old < len(ships) else '',
                                 new=ship_index + 1, new_name=ships[ship_index].get(k.s('Name'), '')))
    plan.messages.append(msg('ok', 'primary_safe'))
    sh = files[0].psd()[files[0].keys.s('ShipOwnership')]
    if 0 <= ship_index < len(sh):
        plan.summary = msg('info', 'chg_primary', n=ship_index + 1, name=sh[ship_index].get(files[0].keys.s('Name'), ''))
    return plan


SPAWN_LIFT = 20000.0


def plan_travel(folder: SaveFolder, slot: int, addr: Address, spawn: str = 'ship',
                files: list[SaveFile] | None = None) -> Plan:
    plan = Plan('travel', folder.path, staged=files is not None)
    errs = addr.validate()
    if errs:
        plan.messages.append(msg('block', 'address_invalid', fields=errs))
        return plan
    files = files if files is not None else load_slot(folder, slot)
    _common_checks(plan, files, check_game=not plan.staged)
    g = galaxies()[addr.galaxy]
    plan.summary = msg('info', 'chg_travel', to_gal=g['name'], to_n=g['n'], to_sys=addr.system, glyphs=addr.to_glyphs())
    for f in files:
        k = f.keys
        psd = f.psd()
        ua = psd[k.s('UniverseAddress')]
        old_gal = ua[k.s('RealityIndex')]
        old_ga = ua[k.s('GalacticAddress')]
        ed = jspan.Editor(f.raw)
        for which in ('UniverseAddress', 'PreviousUniverseAddress'):
            base = k.psd + [k(which)]
            ed.set(base + [k('RealityIndex')], addr.galaxy)
            gab = base + [k('GalacticAddress')]
            ed.set(gab + [k('VoxelX')], addr.x)
            ed.set(gab + [k('VoxelY')], addr.y)
            ed.set(gab + [k('VoxelZ')], addr.z)
            ed.set(gab + [k('SolarSystemIndex')], addr.system)
            ed.set(gab + [k('PlanetIndex')], addr.planet)
        spawn_obj = f.json[k.s('BaseContext')].get(k.s('SpawnStateData'), {})
        state = spawn_obj.get(k.s('LastKnownPlayerState'))
        if spawn == 'ship':
            pos = spawn_obj.get(k.s('PlayerPositionInSystem')) or [0.0, 0.0, 0.0, 1.0]
            newpos = [pos[0], pos[1] + SPAWN_LIFT, pos[2], pos[3] if len(pos) > 3 else 1.0]
            pb = ('[' + ','.join(_float(v) for v in newpos) + ']').encode()
            ed.set(k.spawn + [k('LastKnownPlayerState')], 'InShip')
            for key in ('PlayerPositionInSystem', 'PlayerDeathRespawnPositionInSystem', 'ShipPositionInSystem'):
                if ed.has(k.spawn + [k(key)]):
                    ed.set(k.spawn + [k(key)], pb)
            if ed.has(k.spawn + [k('ShipHovering')]):
                ed.set(k.spawn + [k('ShipHovering')], False)
        elif state in ('InStation', 'OnFoot') or (state != 'InShip'):
            plan.messages.append(msg('danger', 'spawn_keep_risky', state=state or '?'))
        plan.changes.append(FileChange(f.file_index, f, ed.apply()))
        plan.messages.append(msg('info', 'travel_from_to', file=file_names(f.file_index)[0],
                                 from_gal=galaxy_name(old_gal), from_sys=old_ga[k.s('SolarSystemIndex')],
                                 to_gal=g['name'], to_n=g['n'], to_sys=addr.system, glyphs=addr.to_glyphs()))
    plan.messages.append(msg('info', 'travel_prev_too'))
    if spawn == 'ship':
        plan.messages.append(msg('info', 'spawn_ship'))
    if addr.galaxy == 255:
        plan.messages.append(msg('danger', 'galaxy_256'))
    plan.messages.append(msg('warn', 'system_may_not_exist', sys=addr.system))
    if addr.planet:
        plan.messages.append(msg('warn', 'planet_nonzero'))
    plan.messages.append(msg('info', 'bases_stay'))
    return plan


def plan_copy_slot(folder: SaveFolder, src_file_index: int, dest_slot: int, name: str) -> Plan:
    plan = Plan('copy_slot', folder.path)
    src = folder.file(src_file_index)
    if not src.exists():
        plan.messages.append(msg('block', 'source_missing'))
        return plan
    src.load()
    if src.slot == dest_slot:
        plan.messages.append(msg('block', 'same_slot'))
        return plan
    _common_checks(plan, [src])
    name = (name or '').strip()[:60]
    k = src.keys
    new_raw = _set_name_in_json(src.raw, k, name) if name else src.raw
    existing = [fi for fi in files_of_slot(dest_slot) if folder.file(fi).exists()]
    if existing:
        plan.messages.append(msg('danger', 'dest_not_empty', slot=dest_slot))
    for fi in files_of_slot(dest_slot):
        plan.changes.append(FileChange(fi, src, new_raw, name=name or None))
    plan.messages.append(msg('info', 'copy_what', src=file_names(src_file_index)[0], slot=dest_slot, name=name or '-'))
    plan.summary = msg('info', 'chg_copy', src_slot=src.slot, name=name or '-')
    plan.dest_slot = dest_slot
    plan.messages.append(msg('info', 'copy_reencrypt'))
    return plan


def plan_rename(folder: SaveFolder, slot: int, name: str, files: list[SaveFile] | None = None) -> Plan:
    plan = Plan('rename', folder.path, staged=files is not None)
    files = files if files is not None else load_slot(folder, slot)
    _common_checks(plan, files, check_game=not plan.staged)
    name = (name or '').strip()[:60]
    if not name:
        plan.messages.append(msg('block', 'name_empty'))
        return plan
    for f in files:
        plan.changes.append(FileChange(f.file_index, f, _set_name_in_json(f.raw, f.keys, name), name=name))
    plan.messages.append(msg('info', 'rename_what', slot=slot, name=name))
    plan.summary = msg('info', 'chg_rename', name=name)
    return plan


def plan_restore(folder: SaveFolder, point_id: str) -> Plan:
    plan = Plan('restore', folder.path)
    d = safety.point_dir(folder.path, point_id)
    info = json.loads(safety.info_path(d).read_text(encoding='utf-8'))
    running = safety.game_running()
    if running:
        plan.messages.append(msg('block', 'game_running'))
    plan.restore_point = d
    plan.messages.append(msg('info', 'restore_what', files=', '.join(info.get('files', [])), when=info.get('created')))
    plan.messages.append(msg('ok', 'restore_backup_first'))
    return plan


def plan_reorder(folder: SaveFolder, slot: int, order: list[int], files: list[SaveFile] | None = None) -> Plan:
    """order = old ship indices in their new positions (a permutation of every ship index)."""
    plan = Plan('reorder', folder.path, staged=files is not None)
    files = files if files is not None else load_slot(folder, slot)
    _common_checks(plan, files, check_game=not plan.staged)
    for f in files:
        k = f.keys
        psd = f.psd()
        ships = psd[k.s('ShipOwnership')]
        if sorted(order) != list(range(len(ships))):
            plan.messages.append(msg('block', 'order_invalid'))
            return plan
        o2n = {old: new for new, old in enumerate(order)}
        b = f.raw
        P = k.psd
        idx = jspan.Index(b)
        edits = []

        def permute(key):
            if not idx.has(P + [k(key)]):
                return
            s0, e0 = idx.get(P + [k(key)])
            items = [b[a:z] for a, z in jspan.arr_items(b, s0)]
            if len(items) != len(order):
                raise ValueError('order_invalid')
            edits.append((s0, e0, b'[' + b','.join(items[i] for i in order) + b']'))

        permute('ShipOwnership')
        permute('ShipUsesLegacyColours')
        s0, e0 = idx.get(P + [k('PrimaryShip')])
        old_primary = int(b[s0:e0])
        edits.append((s0, e0, str(o2n.get(old_primary, old_primary)).encode()))
        if idx.has(P + [k('CorvetteEditAssociatedShipIndex')]):
            s0, e0 = idx.get(P + [k('CorvetteEditAssociatedShipIndex')])
            old = int(b[s0:e0])
            edits.append((s0, e0, str(o2n.get(old, old)).encode()))
        moved_bases = 0
        if idx.has(P + [k('PersistentPlayerBases')]):
            bs, _ = idx.get(P + [k('PersistentPlayerBases')])
            for a, z in jspan.arr_items(b, bs):
                base = jspan.loads(b[a:z])
                if base.get(k.s('BaseType'), {}).get(k.s('PersistentBaseTypes')) == 'PlayerShipBase':
                    us, ue = jspan.get(b, [k('UserData')], a)
                    old = int(b[us:ue])
                    edits.append((us, ue, str(o2n.get(old, old)).encode()))
                    moved_bases += 1
        edits.sort(key=lambda t: t[0], reverse=True)
        for x, y in zip(edits, edits[1:]):
            if y[1] > x[0]:
                raise RuntimeError('reorder_overlap')
        out = bytearray(b)
        for s0, e0, r in edits:
            out[s0:e0] = r
        new_raw = bytes(out)
        jspan.carry_index(b, new_raw, edits)
        # independent check: parse both and compare with the expected permutation
        new = jspan.loads(new_raw)[k.s('BaseContext')][k.s('PlayerStateData')]
        if [x.get(k.s('Name')) for x in new[k.s('ShipOwnership')]] != [ships[i].get(k.s('Name')) for i in order] \
                or new[k.s('PrimaryShip')] != o2n.get(old_primary, old_primary):
            raise RuntimeError('reorder_verify')
        plan.changes.append(FileChange(f.file_index, f, new_raw))
        plan.messages.append(msg('info', 'reorder_what', file=file_names(f.file_index)[0],
                                 names=[ships[i].get(k.s('Name')) or ship_type(ships[i][k.s('Resource')][k.s('Filename')])
                                        for i in order if ships[i][k.s('Resource')][k.s('Filename')]]))
        if moved_bases:
            plan.messages.append(msg('ok', 'reorder_corvettes', n=moved_bases))
    plan.messages.append(msg('ok', 'reorder_safe'))
    plan.summary = msg('info', 'chg_reorder')
    return plan


def point_files(point: Path) -> list[SaveFile]:
    out = []
    for fi in range(1, 31):
        hg, mf = file_names(fi)
        if (point / hg).is_file() and (point / mf).is_file():
            out.append(SaveFile(point, fi))
    return out


def plan_restore_to(folder: SaveFolder, point_id: str, dest_slot: int) -> Plan:
    """Restore a backup into any slot (re-encrypting the manifest for the destination)."""
    plan = Plan('restore_to', folder.path)
    d = safety.point_dir(folder.path, point_id)
    srcs = [f.load() for f in point_files(d)]
    if not srcs:
        plan.messages.append(msg('block', 'point_empty'))
        return plan
    if not 1 <= dest_slot <= 15:
        plan.messages.append(msg('block', 'slot_invalid'))
        return plan
    _game_check(plan)
    srcs.sort(key=lambda f: f.file_index)
    targets = files_of_slot(dest_slot)
    existing = [fi for fi in targets if folder.file(fi).exists()]
    if existing:
        try:
            cur = folder.file(existing[0])
            from .meta import Meta
            cur_name = Meta.load(cur.mf_path.read_bytes(), cur.meta_index).name
        except Exception:  # noqa: BLE001
            cur_name = ''
        plan.messages.append(msg('danger', 'restore_overwrites', slot=dest_slot, name=cur_name or '-'))
        plan.messages.append(msg('ok', 'restore_backup_first'))
    src_name = srcs[0].meta.name
    for i, fi in enumerate(targets):
        src = srcs[min(i, len(srcs) - 1)]
        plan.changes.append(FileChange(fi, src, src.raw))
    plan.messages.append(msg('info', 'restore_to_what', name=src_name or '-', from_slot=srcs[0].slot, slot=dest_slot))
    plan.summary = msg('info', 'chg_restore', name=src_name or '-', from_slot=srcs[0].slot)
    plan.dest_slot = dest_slot
    if srcs[0].slot != dest_slot:
        plan.messages.append(msg('info', 'copy_reencrypt'))
    return plan


def slot_changelog(folder: Path, slot: int, limit: int = 10) -> list[dict]:
    """Last changes saved in a slot, newest first. Each change points to the automatic backup taken
    right before the save that contained it ("go back to before this change")."""
    out = []
    for p in safety.list_points(folder):
        if p.get('kind') != 'auto' or p.get('slot') != slot or not p.get('changes'):
            continue
        for i, c in enumerate(p['changes']):
            out.append({'time': p.get('created'), 'change': c, 'point': p['id'], 'session_size': len(p['changes']),
                        'order': i})
    out.sort(key=lambda e: (e['time'] or 0, e['order']), reverse=True)
    return out[:limit]


def plan_revert(folder: SaveFolder, slot: int, point_id: str, files: list[SaveFile]) -> Plan:
    """Stage the slot's files as they were in an automatic backup (written only on Save)."""
    plan = Plan('revert', folder.path, staged=True)
    d = safety.point_dir(folder.path, point_id)
    info = json.loads(safety.info_path(d).read_text(encoding='utf-8'))
    if info.get('slot') != slot:
        plan.messages.append(msg('block', 'revert_other_slot'))
        return plan
    by_index = {f.file_index: f for f in files}
    srcs = [f.load() for f in point_files(d) if f.file_index in by_index]
    if not srcs:
        plan.messages.append(msg('block', 'point_empty'))
        return plan
    when = info.get('created')
    undone = []
    for p in safety.list_points(folder.path):
        if p.get('kind') == 'auto' and p.get('slot') == slot and (p.get('created') or 0) >= (when or 0):
            undone.append(p)
    undone.sort(key=lambda p: p.get('created') or 0)
    lost = [c for p in undone for c in p.get('changes', [])]
    cur = by_index[srcs[0].file_index]
    hours = max(0, (cur.meta.playtime or 0) - (srcs[0].meta.playtime or 0)) // 3600
    plan.messages.append(msg('danger', 'revert_what' if hours else 'revert_what_lt1', n=len(lost), hours=hours))
    for c in lost:
        plan.messages.append(msg('info', 'revert_item', **{'code_': c.get('code'), **(c.get('p') or {})}))
    plan.messages.append(msg('warn', 'revert_also_game'))
    plan.messages.append(msg('ok', 'revert_not_account'))
    plan.messages.append(msg('warn', 'revert_no_dupes'))
    for src in srcs:
        plan.changes.append(FileChange(src.file_index, by_index[src.file_index], src.raw))
    plan.summary = msg('info', 'chg_revert', when=when)
    return plan


def point_details(folder: Path, info: dict) -> dict:
    """Name / location / playtime of a backup, read from its manifest only (fast)."""
    from .meta import Meta
    try:
        d = safety.point_dir(folder, info['id'])
        files = point_files(d)
        if files:
            f = files[0]
            m = Meta.load(f.mf_path.read_bytes(), f.meta_index)
            return {'save_name': m.name, 'summary': m.summary, 'playtime': m.playtime,
                    'saved_at': m.timestamp, 'from_slot': f.slot}
    except Exception:  # noqa: BLE001
        pass
    return {}


# ---------------------------------------------------------------- apply --------------------
def apply(plan: Plan) -> dict:
    if plan.blocked:
        raise PermissionError('blocked')
    if safety.game_running():
        raise PermissionError('game_running')
    folder = plan.folder
    report = {'action': plan.action, 'written': [], 'backup': None, 'verified': []}

    if plan.restore_point is not None:
        info = json.loads(safety.info_path(plan.restore_point).read_text(encoding='utf-8'))
        names = info.get('files', [])
        report['backup'] = str(safety.backup(folder, names, 'before_restore'))
        for n in names:
            safety.atomic_write(folder / n, (plan.restore_point / n).read_bytes())
            report['written'].append(n)
        return report

    for c in plan.changes:  # nothing changed on disk since the preview?
        if c.source.changed_on_disk():
            raise RuntimeError('changed_on_disk')
    names = sorted({n for c in plan.changes for n in file_names(c.target_index)})
    report['backup'] = str(safety.backup(folder, names, plan.action))
    built = []
    for c in plan.changes:
        hg, mf = c.source.build(c.new_raw, meta_index=c.target_index + 1, name=c.name)
        problems = verify_pair(hg, mf, c.target_index + 1, c.new_raw)
        if problems:
            raise RuntimeError('verify_failed: ' + '; '.join(problems))
        built.append((c, hg, mf))
    for c, hg, mf in built:
        hg_name, mf_name = file_names(c.target_index)
        safety.atomic_write(folder / hg_name, hg)
        safety.atomic_write(folder / mf_name, mf)
        report['written'] += [hg_name, mf_name]
        again = SaveFile(folder, c.target_index).load()  # re-read from disk, exactly like the game
        ok = again.raw == c.new_raw and again.meta.size_decompressed == len(c.new_raw)
        report['verified'].append({'file': hg_name, 'ok': ok})
        if not ok:
            raise RuntimeError('verify_after_write_failed')
    return report
