"""Tests against copies of real saves (tests/fixture, not published: they are personal). Never touches the originals.
Tests that need them are skipped when tests/fixture is empty."""
import json
import shutil
from pathlib import Path

import pytest

from olympian.core import hgfile, jspan, meta, safety, ops
from olympian.core.coords import Address, galaxies
from olympian.core.keys import OBF, mapping
from olympian.core.savefile import SaveFolder, SaveFile, verify_pair

FIX = Path(__file__).parent / 'fixture'


@pytest.fixture
def folder(tmp_path):
    # tests/fixture holds copies of real saves; they are personal, so they are not published with the repo
    if not (FIX / 'save.hg').is_file():
        pytest.skip('no save fixtures (put copies of a save folder in tests/fixture)')
    d = tmp_path / 'st_test'
    shutil.copytree(FIX, d)
    return SaveFolder(d)


def test_keys_match_mapping():
    m = mapping()
    for name, key in OBF.items():
        assert m.get(key) == name, (name, key, m.get(key))


def test_roundtrip_all_files(folder):
    for fi in folder.file_indices():
        f = folder.file(fi).load()
        assert f.meta.size_decompressed == len(f.raw)
        jspan.loads(f.raw)
        hg, mf = f.build(f.raw)
        assert verify_pair(hg, mf, f.meta_index, f.raw) == []
        assert meta.decrypt(meta.encrypt(bytes(f.meta.raw), f.meta_index), f.meta_index) == bytes(f.meta.raw)


def test_meta_key_is_slot_specific(folder):
    f = folder.file(1).load()
    enc = f.meta.encrypt_for(4)
    with pytest.raises(ValueError):
        meta.Meta.load(enc, 2)
    assert meta.Meta.load(enc, 4).size_decompressed == len(f.raw)


def test_pure_python_lz4_fallback(folder):
    f = folder.file(2).load()
    # literal-only block produced by the fallback must decompress with the real lz4 too
    import lz4.block
    chunk = f.raw[:0x80000]
    lit = bytearray()
    n = len(chunk)
    lit.append(0xF0); rest = n - 15
    while rest >= 255:
        lit.append(255); rest -= 255
    lit.append(rest); lit += chunk
    assert lz4.block.decompress(bytes(lit), uncompressed_size=n) == chunk


def test_glyphs_and_galactic():
    a = Address.from_glyphs('121905417A6B', 0)
    assert (a.planet, a.system, a.y, a.z, a.x) == (1, 0x219, 5, 0x417, -1429)
    assert a.to_glyphs() == '121905417A6B'
    b = Address.from_galactic(a.to_galactic(), 0, planet=1)
    assert (b.x, b.y, b.z, b.system) == (a.x, a.y, a.z, a.system)
    assert Address(0, 0, 0, 0, 0).to_galactic() == '07FF:007F:07FF:0000'
    assert len(galaxies()) == 256 and galaxies()[29]['name'] == 'Sudzerbal'


def test_describe_slot(folder):
    d = ops.describe_slot(folder, 1)
    assert len(d['files']) == 2
    assert d['files'][0]['ships'][0]['name'] == 'Olympian Firefly Class S'


def _semantic(f):
    return jspan.loads(f.raw)


def test_set_primary(folder):
    plan = ops.plan_set_primary(folder, 1, 8)
    assert not plan.blocked
    rep = ops.apply(plan)
    assert all(v['ok'] for v in rep['verified'])
    for fi in (1, 2):
        f = folder.file(fi).load()
        assert f.psd()['aBE'] == 8
    assert Path(rep['backup']).is_dir()


def test_set_primary_empty_slot_blocked(folder):
    plan = ops.plan_set_primary(folder, 1, 10)   # slot 11 is empty in the fixture
    assert plan.blocked


def test_travel_only_expected_changes(folder):
    before = {fi: _semantic(folder.file(fi).load()) for fi in (1, 2)}
    addr = Address(galaxy=29, x=-923, y=7, z=-1491, system=1, planet=0)
    plan = ops.plan_travel(folder, 1, addr, 'ship')
    assert not plan.blocked
    ops.apply(plan)
    for fi in (1, 2):
        after = _semantic(folder.file(fi).load())
        exp = before[fi]
        p = exp['vLc']['6f=']
        for ua in ('yhJ', 'ux@'):
            p[ua]['Iis'] = 29
            ga = p[ua]['oZw']
            ga.update({'dZj': -923, 'IyE': 7, 'uXE': -1491, 'vby': 1, 'jsv': 0})
        sp = exp['vLc']['rnc']
        pos = sp['mEH']
        newpos = [pos[0], pos[1] + ops.SPAWN_LIFT, pos[2], pos[3]]
        sp['jk4'] = 'InShip'
        for key in ('mEH', 'tmM', 'tnP'):
            sp[key] = newpos
        sp['lKr'] = False
        assert after == exp


def test_copy_slot_and_name(folder):
    assert any(m['code'] == 'dest_not_empty' for m in ops.plan_copy_slot(folder, 2, 2, 'x').messages)
    plan = ops.plan_copy_slot(folder, 2, 3, 'Prueba Olympian')       # slot 3 is empty in the fixture
    assert not any(m['code'] == 'dest_not_empty' for m in plan.messages)
    ops.apply(plan)
    src = _semantic(folder.file(2).load())
    src['<h0']['Pk4'] = 'Prueba Olympian'
    src['vLc']['6f='].pop('Pk4', None)
    for fi in (5, 6):
        f = folder.file(fi).load()
        assert f.meta.name == 'Prueba Olympian' and ops.save_name(f) == ('Prueba Olympian', True)
        assert _semantic(f) == src


def test_rename_writes_where_the_game_reads(folder):
    # slot 2 in the fixture carries the stray PlayerStateData.SaveName written by old versions
    assert 'Pk4' in folder.file(3).load().psd()
    ops.apply(ops.plan_rename(folder, 2, 'Uno'))
    ops.apply(ops.plan_rename(folder, 2, 'Dos'))
    for fi in (3, 4):
        f = folder.file(fi).load()
        assert f.meta.name == 'Dos' and f.json['<h0']['Pk4'] == 'Dos'
        assert 'Pk4' not in f.psd()                               # stray copy removed
        assert ops.save_name(f) == ('Dos', True)


def test_name_not_persisted_is_flagged(folder):
    # manifest has a name but the JSON field the game reads is empty -> the game would lose it
    f = folder.file(1).load()
    if f.json['<h0']['Pk4'] == '' and f.meta.name:
        assert ops.save_name(f) == (f.meta.name, False)


def test_same_slot_copy_blocked(folder):
    assert ops.plan_copy_slot(folder, 1, 1, 'x').blocked


def test_restore_point_roundtrip(folder):
    names = ['save.hg', 'mf_save.hg', 'save2.hg', 'mf_save2.hg']
    orig = {n: (folder.path / n).read_bytes() for n in names}
    p = safety.create_point(folder.path, names, 'antes', 1)
    ops.apply(ops.plan_set_primary(folder, 1, 2))
    assert (folder.path / 'save.hg').read_bytes() != orig['save.hg']
    pid = f'{safety.POINTS_DIR}/{p.name}'
    ops.apply(ops.plan_restore(folder, pid))
    for n in names:
        assert (folder.path / n).read_bytes() == orig[n]


def test_point_id_traversal_rejected(folder):
    with pytest.raises(ValueError):
        safety.point_dir(folder.path, '_OlympianRestorePoints/../../etc')


def test_changed_on_disk_guard(folder):
    plan = ops.plan_set_primary(folder, 1, 2)
    import os, time
    p = folder.path / 'save.hg'
    os.utime(p, (time.time() + 5, time.time() + 5))
    with pytest.raises(RuntimeError):
        ops.apply(plan)


# ---------------------------------------------------------------- v0.1.1 ------------------
from olympian.core.session import Session  # noqa: E402


def _psd(folder, fi):
    f = SaveFile(folder.path, fi).load()
    return f, f.psd(), f.keys


def test_reorder_moves_everything_consistently(folder):
    f, p, k = _psd(folder, 1)
    ships = p[k.s('ShipOwnership')]
    n = len(ships)
    filled = [i for i, s in enumerate(ships) if s[k.s('Resource')][k.s('Filename')]]
    empties = [i for i in range(n) if i not in filled]
    order = list(reversed(filled)) + empties
    plan = ops.plan_reorder(folder, 1, order)
    assert not plan.blocked
    ops.apply(plan)
    for fi in (1, 2):
        old_f = SaveFile(folder.path, fi)
        g, q, k2 = _psd(folder, fi)
        old = jspan.loads((folder.path / '_OlympianBackups').glob('*reorder*').__next__().joinpath(
            f'save{"" if fi == 1 else fi}.hg').read_bytes() and hgfile.decompress(
            next((folder.path / '_OlympianBackups').glob('*reorder*')).joinpath(f'save{"" if fi == 1 else fi}.hg').read_bytes()))
        op = old[k2.s('BaseContext')][k2.s('PlayerStateData')]
        o2n = {o: nw for nw, o in enumerate(order)}
        assert q[k2.s('ShipOwnership')] == [op[k2.s('ShipOwnership')][i] for i in order]
        assert q[k2.s('PrimaryShip')] == o2n[op[k2.s('PrimaryShip')]]
        if k2.s('ShipUsesLegacyColours') in op:
            assert q[k2.s('ShipUsesLegacyColours')] == [op[k2.s('ShipUsesLegacyColours')][i] for i in order]
        if k2.s('CorvetteEditAssociatedShipIndex') in op:
            assert q[k2.s('CorvetteEditAssociatedShipIndex')] == o2n.get(op[k2.s('CorvetteEditAssociatedShipIndex')], op[k2.s('CorvetteEditAssociatedShipIndex')])
        bt, pbt = k2.s('BaseType'), k2.s('PersistentBaseTypes')
        olds = [b[k2.s('UserData')] for b in op[k2.s('PersistentPlayerBases')] if b.get(bt, {}).get(pbt) == 'PlayerShipBase']
        news = [b[k2.s('UserData')] for b in q[k2.s('PersistentPlayerBases')] if b.get(bt, {}).get(pbt) == 'PlayerShipBase']
        assert olds and news == [o2n[x] for x in olds]
        # every other top-level PSD key unchanged
        for key in op:
            if key not in (k2.s('ShipOwnership'), k2.s('PrimaryShip'), k2.s('ShipUsesLegacyColours'),
                           k2.s('CorvetteEditAssociatedShipIndex'), k2.s('PersistentPlayerBases')):
                assert q[key] == op[key], key


def test_reorder_rejects_bad_permutation(folder):
    plan = ops.plan_reorder(folder, 1, [0, 0, 1])
    assert plan.blocked


def test_restore_to_other_slot(folder):
    d = safety.create_point(folder.path, ['save3.hg', 'mf_save3.hg', 'save4.hg', 'mf_save4.hg'], 'pre', 2)
    pid = '_OlympianRestorePoints/' + d.name
    plan = ops.plan_restore_to(folder, pid, 4)          # slot 4 is empty
    assert not plan.blocked and not any(m['code'] == 'restore_overwrites' for m in plan.messages)
    ops.apply(plan)
    for src_fi, dst_fi in ((3, 7), (4, 8)):
        a = SaveFile(folder.path, src_fi).load()
        b = SaveFile(folder.path, dst_fi).load()        # decrypts with slot 4's key, or raises
        assert a.raw == b.raw and b.meta.name == a.meta.name
    plan = ops.plan_restore_to(folder, pid, 1)           # occupied -> danger warning
    assert any(m['code'] == 'restore_overwrites' for m in plan.messages)


def test_point_details_from_manifest(folder):
    d = safety.create_point(folder.path, ['save3.hg', 'mf_save3.hg'], 'x', 2)
    info = {'id': '_OlympianRestorePoints/' + d.name}
    det = ops.point_details(folder.path, info)
    assert det['from_slot'] == 2 and det['save_name'] == 'PreLivingShipv2' and det['playtime'] > 0


def test_session_stage_save_discard(folder):
    before = {p.name: p.read_bytes() for p in folder.path.glob('*.hg')}
    s = Session(folder, 1)
    plan = ops.plan_set_primary(folder, 1, 3, files=s.views())
    assert plan.staged and not any(m['code'] == 'game_unknown' for m in plan.messages)
    s.stage(plan)
    plan2 = ops.plan_rename(folder, 1, 'Sesion', files=s.views())
    s.stage(plan2)
    assert s.dirty and len(s.changes) == 2
    assert {p.name: p.read_bytes() for p in folder.path.glob('*.hg')} == before   # nothing written yet
    assert s.describe()['files'][0]['primary_ship'] == 3
    s.reload()                                                                     # discard
    assert not s.dirty
    s.stage(ops.plan_set_primary(folder, 1, 3, files=s.views()))
    s.stage(ops.plan_rename(folder, 1, 'Sesion', files=s.views()))
    rep = s.save()
    assert all(v['ok'] for v in rep['verified']) and not s.dirty
    f, p, k = _psd(folder, 1)
    assert p[k.s('PrimaryShip')] == 3 and f.meta.name == 'Sesion'


def test_session_save_guard(folder):
    import os, time
    s = Session(folder, 1)
    s.stage(ops.plan_set_primary(folder, 1, 3, files=s.views()))
    t = time.time() + 10
    os.utime(folder.path / 'save.hg', (t, t))
    with pytest.raises(RuntimeError, match='changed_on_disk'):
        s.save()


# ---------------------------------------------------------------- phase 2a: ships ----------
from olympian.core import ships as SH  # noqa: E402

# fixture slot 1: 0 Firefly (sentinel S), 1-2 living, 3-4 corvettes, 5 sentinel C (unnamed),
# 6-7 exotic S, 8-9 sentinels S, 10-11 empty


def _ships(folder, fi=1):
    f = SaveFile(folder.path, fi).load()
    return f.psd()[f.keys.s('ShipOwnership')], f.psd(), f.keys


def _stage_and_save(folder, ship, op, **params):
    s = Session(folder, 1)
    plan = SH.plan_ship(folder, 1, ship, op, params, files=s.views())
    assert not plan.blocked, plan.messages
    s.stage(plan)
    rep = s.save()
    assert all(v['ok'] for v in rep['verified'])
    return plan


def test_ship_actions_respect_protections(folder):
    for ship, op in [(3, 'expand'), (3, 'delete'), (3, 'duplicate'), (3, 'seed'), (3, 'copy_tech'),
                     (1, 'expand'), (1, 'copy_tech'), (10, 'repair'),
                     (0, 'set_class'), (5, 'set_class'), (0, 'set_sc')]:          # class/SC editing removed
        plan = SH.plan_ship(folder, 1, ship, op, {'seed': '0x1', 'cls': 'S', 'from': 0})
        assert plan.blocked, (ship, op)


def test_ship_rename_only_touches_that_name(folder):
    before, _, k = _ships(folder)
    _stage_and_save(folder, 5, 'rename', name='Centinela Uno')
    after, _, _ = _ships(folder)
    assert after[5][k.s('Name')] == 'Centinela Uno'
    assert [s for i, s in enumerate(after) if i != 5] == [s for i, s in enumerate(before) if i != 5]


def test_ship_repair_and_unlock(folder):
    # damage a module and lock cells on the exotic (ship 7) through a crafted edit, then repair it
    f = SaveFile(folder.path, 1).load()
    k = f.keys
    plan = SH.plan_ship(folder, 1, 7, 'repair')
    assert not plan.blocked
    before, _, _ = _ships(folder)
    inv = before[7][k.s('Inventory')]
    ops.apply(plan)
    after, _, _ = _ships(folder)
    a = after[7][k.s('Inventory')]
    assert len(a[k.s('ValidSlotIndices')]) == a[k.s('Width')] * a[k.s('Height')] >= len(inv[k.s('ValidSlotIndices')])
    assert all(x[k.s('DamageFactor')] == 0 for x in after[7][k.s('Inventory_TechOnly')][k.s('Slots')]
               if x[k.s('Type')][k.s('InventoryType')] == 'Technology')
    # corvette repair never changes its grid
    plan = SH.plan_ship(folder, 1, 4, 'repair')
    ops.apply(plan)
    after2, _, _ = _ships(folder)
    assert after2[4][k.s('Inventory')][k.s('ValidSlotIndices')] == after[4][k.s('Inventory')][k.s('ValidSlotIndices')]


def test_ship_expand_s_only(folder):
    before, _, k = _ships(folder)
    plan = SH.plan_ship(folder, 1, 5, 'expand')          # class C -> blocked
    assert plan.blocked
    _stage_and_save(folder, 7, 'expand')                  # exotic S 7x5 -> cargo 10x12; tech untouched
    after, _, _ = _ships(folder)
    inv = after[7][k.s('Inventory')]
    assert (inv[k.s('Width')], inv[k.s('Height')], len(inv[k.s('ValidSlotIndices')])) == (10, 12, 120)
    cells = [(x[k.s('Index')][k.s('X')], x[k.s('Index')][k.s('Y')]) for x in inv[k.s('Slots')]]
    assert all(0 <= x < 10 and 0 <= y < 12 for x, y in cells)
    assert after[7][k.s('Inventory_TechOnly')] == before[7][k.s('Inventory_TechOnly')]


def test_ship_recharge(folder):
    before, _, k = _ships(folder)
    tech = before[0][k.s('Inventory_TechOnly')][k.s('Slots')]
    low = {x[k.s('Id')] for x in tech if 0 < x[k.s('Amount')] < x[k.s('MaxAmount')]}
    assert low == {'^HYPERDRIVE_ROBO', '^SHIPJUMP_ROBO'}     # 76/120 and 126/200 in the fixture
    _stage_and_save(folder, 0, 'recharge')
    after, _, _ = _ships(folder)
    for o, x in zip(tech, after[0][k.s('Inventory_TechOnly')][k.s('Slots')]):
        if x[k.s('Id')] in low:
            assert x[k.s('Amount')] == x[k.s('MaxAmount')]
        else:                                                  # -1 (no charge) and 0 stay exactly as they were
            assert x == o


def test_ship_duplicate_and_delete(folder):
    before, p, k = _ships(folder)
    _stage_and_save(folder, 2, 'duplicate')               # living ship -> first empty slot (11th)
    after, p2, _ = _ships(folder)
    assert after[10] == before[2] and after[:10] == before[:10]
    assert p2[k.s('ShipUsesLegacyColours')][10] == p[k.s('ShipUsesLegacyColours')][2]
    _stage_and_save(folder, 10, 'delete')
    after2, _, _ = _ships(folder)
    assert after2 == before
    assert SH.plan_ship(folder, 1, p[k.s('PrimaryShip')], 'delete').blocked     # primary can't be deleted


def test_ship_seed(folder):
    _stage_and_save(folder, 6, 'seed', seed='0xabc123')
    after, _, k = _ships(folder)
    assert after[6][k.s('Resource')][k.s('Seed')] == [True, '0xABC123']
    assert SH.plan_ship(folder, 1, 6, 'seed', {'seed': 'nope'}).blocked


def test_ship_copy_tech(folder):
    before, _, k = _ships(folder)
    g = k.s('Inventory_TechOnly')
    assert SH.plan_ship(folder, 1, 7, 'copy_tech', {'from': 0}).blocked          # exotic 10x3 too small for 43 modules
    _stage_and_save(folder, 9, 'copy_tech', **{'from': 6})                       # exotic modules -> sentinel S 10x6
    after, _, _ = _ships(folder)
    items = after[9][g][k.s('Slots')]
    src = before[6][g][k.s('Slots')]
    assert items[:len(src)] == src
    cells = [(x[k.s('Index')][k.s('X')], x[k.s('Index')][k.s('Y')]) for x in items]
    assert len(cells) == len(set(cells))
    fams = [SH.family(x[k.s('Id')]) for x in items]
    src_fams = {SH.family(x[k.s('Id')]) for x in src}
    assert all(f in src_fams for f in fams[:len(src)]) and not any(f in src_fams for f in fams[len(src):])




def test_sc_layout_is_read_only_and_described(folder):
    f = SaveFile(folder.path, 1).load()
    info = SH.summary(f.psd()['@Cs'][0], f.keys)
    assert info['sc_shape']['code'] == 'square' and 'set_sc' not in info['allowed'] and 'set_class' not in info['allowed']


def test_backup_records_session_changes(folder):
    from olympian.core import safety as SF
    _stage_and_save(folder, 5, 'rename', name='Uno')
    s = Session(folder, 1)
    s.stage(SH.plan_ship(folder, 1, 0, 'recharge', {}, files=s.views()))
    s.stage(ops.plan_rename(folder, 1, 'Partida X', files=s.views()))
    s.save()
    autos = [p for p in SF.list_points(folder.path) if p['kind'] == 'auto']
    newest = max(autos, key=lambda p: p['created'])
    assert newest['slot'] == 1 and [c['code'] for c in newest['changes']] == ['chg_ship_recharge', 'chg_rename']
    stats = ops.describe_slot(folder, 1)['files'][0]['ships'][0]['info']['stats']
    assert set(stats) == {'damage', 'shield', 'hyperdrive', 'agile'} and stats['damage'] > 0


def test_changelog_and_revert(folder):
    before = {fi: SaveFile(folder.path, fi).load().raw for fi in (1, 2)}
    _stage_and_save(folder, 5, 'rename', name='A')
    mid = {fi: SaveFile(folder.path, fi).load().raw for fi in (1, 2)}
    _stage_and_save(folder, 5, 'rename', name='B')
    log = ops.slot_changelog(folder.path, 1)
    assert [e['change']['p']['name'] for e in log[:2]] == ['B', '-'] or log[0]['change']['code'] == 'chg_ship_rename'
    oldest = log[-1]                                        # the first save -> back to the original files
    s = Session(folder, 1)
    plan = ops.plan_revert(folder, 1, oldest['point'], s.views())
    codes = [m['code'] for m in plan.messages]
    assert plan.staged and not plan.blocked and codes.count('revert_item') == 2 and 'revert_no_dupes' in codes
    s.stage(plan)
    assert {fi: SaveFile(folder.path, fi).load().raw for fi in (1, 2)} == {fi: SaveFile(folder.path, fi).load().raw for fi in (1, 2)}
    s.save()
    after = {fi: SaveFile(folder.path, fi).load().raw for fi in (1, 2)}
    assert after == before                                  # exactly the original JSON again
    assert ops.plan_revert(folder, 2, oldest['point'], Session(folder, 2).views()).blocked   # other slot


# ---------------------------------------------------------------- phase 2b: game data -------
from olympian.core import gamedata as GD  # noqa: E402


def test_gamedata_names_and_flags():
    assert GD.load() and GD.load()['game']['signature']
    assert GD.name('^HYPERDRIVE_ROBO', 'es') == 'Núcleo Carmesí' and GD.name('^HYPERDRIVE_ROBO', 'en') == 'Crimson Core'
    assert GD.name('^UP_HYP4#02507', 'es') == 'Mejora de clase S de Hiperturbo'
    assert GD.name('^UP_HYP4#02507', 'en') == 'S-Class Hyperdrive Upgrade'
    assert GD.chargeable('^HYPERDRIVE_ROBO') is True and GD.chargeable('^SHIPSCAN_ECON') is False
    assert GD.chargeable('^UP_HYP4#02507') is False and GD.chargeable('^NOT_AN_ITEM') is None
    assert len(GD.names('es')) > 2000


def test_recharge_uses_game_data(folder):
    before, _, k = _ships(folder)
    g = k.s('Inventory_TechOnly')
    tech = before[6][g][k.s('Slots')]                      # exotic OctoPussy: hyperdrive at 0/120
    empty = [x for x in tech if x[k.s('Id')] == '^HYPERDRIVE' and x[k.s('Amount')] == 0]
    assert empty
    _stage_and_save(folder, 6, 'recharge')
    after, _, _ = _ships(folder)
    for o, x in zip(tech, after[6][g][k.s('Slots')]):
        if GD.chargeable(o[k.s('Id')]):
            assert x[k.s('Amount')] == x[k.s('MaxAmount')]
        else:
            assert x == o                                   # non-chargeable (incl. 0 and -1) untouched


def test_ship_move_and_swap(folder):
    before, _, k = _ships(folder)
    g = k.s('Inventory_TechOnly')
    slots = before[0][g][k.s('Slots')]
    cell = lambda x: (x[k.s('Index')][k.s('X')], x[k.s('Index')][k.s('Y')])
    occupied = {cell(x) for x in slots}
    valid = {(c[k.s('X')], c[k.s('Y')]) for c in before[0][g][k.s('ValidSlotIndices')]}
    free = sorted(valid - occupied)
    a, b = cell(slots[0]), cell(slots[1])
    if free:                                               # move into an empty cell
        _stage_and_save(folder, 0, 'move', grid='tech', **{'from': list(a), 'to': list(free[0])})
        after, _, _ = _ships(folder)
        assert cell(after[0][g][k.s('Slots')][0]) == free[0]
        a = free[0]
    _stage_and_save(folder, 0, 'move', grid='tech', **{'from': list(a), 'to': list(b)})   # swap
    after, _, _ = _ships(folder)
    s2 = after[0][g][k.s('Slots')]
    assert cell(s2[0]) == b and cell(s2[1]) == a
    assert [x[k.s('Id')] for x in s2] == [x[k.s('Id')] for x in slots]          # same modules, same order
    assert SH.plan_ship(folder, 1, 0, 'move', {'grid': 'tech', 'from': [0, 0], 'to': [0, 0]}).blocked
    assert SH.plan_ship(folder, 1, 0, 'move', {'grid': 'tech', 'from': list(b), 'to': [20, 20]}).blocked


# ---------------------------------------------------------------- phase 2c: inventories ----
import collections  # noqa: E402
import copy as _copy  # noqa: E402

from olympian.core import gamedata as GD, inventories as INV  # noqa: E402


def _inv_totals(files):
    k = files[0].keys
    t = collections.Counter()
    for c in INV.containers(files[0]):
        for it in c['inv'][k.s('Slots')]:
            t[(it[k.s('Id')], INV._type(it, k))] += it[k.s('Amount')]
    return t


def _byid(f):
    return {c['id']: c for c in INV.containers(f)}


def _cells(c, k):
    return {INV._xy(it, k): it for it in c['inv'][k.s('Slots')]}


def _first_free(c, k):
    occ = _cells(c, k)
    return next(v for v in sorted(c['valid'], key=lambda v: (v[1], v[0])) if v not in occ)


def _crafted(folder, mutate):
    """Copies of slot 1 with the parsed JSON changed by mutate(psd, k) (for edge cases a real save lacks)."""
    out = []
    for f in ops.load_slot(folder, 1):
        k = f.keys
        j = _copy.deepcopy(f.json)
        mutate(j[k.s('BaseContext')][k.s('PlayerStateData')], k)
        v = _copy.copy(f)
        v.raw = jspan.dumps_value(j)
        v._json, v._keys = None, (None, None)
        out.append(v)
    return out


def test_stack_rule_matches_every_item(folder):
    """MaxAmount = min(cap, size[level][type][group] x StackMultiplier) for every table item of the fixture."""
    checked = 0
    for fi in folder.file_indices():
        f = folder.file(fi).load()
        k = f.keys
        level = INV.level_of(f.psd(), k)
        sizes = GD.stack_sizes(level)
        assert sizes, level
        for c in INV.containers(f):
            for it in c['inv'][k.s('Slots')]:
                t, m = INV._type(it, k), GD.stack_mult(it[k.s('Id')])
                if t in INV.TYPE_ORDER and m > 0:
                    assert it[k.s('MaxAmount')] == min(sizes['cap'][t], sizes[t][c['group']] * m), (c['id'], it)
                    checked += 1
    assert checked > 500


def test_inventory_moves_end_to_end(folder):
    s = Session(folder, 1)
    before = _inv_totals(s.views())
    f0 = s.views()[0]
    k = f0.keys
    C = _byid(f0)
    untouched = {cid: _copy.deepcopy(c['inv']) for cid, c in C.items() if cid not in ('chest2', 'suit', 'chest6')}

    # 1) a product stack bigger than the exosuit limit: only what fits goes, the rest stays
    src = next(it for it in C['chest2']['inv'][k.s('Slots')]
               if INV._type(it, k) == 'Product' and GD.stack_mult(it[k.s('Id')]) > 0
               and it[k.s('Amount')] > 10 * GD.stack_mult(it[k.s('Id')]))
    a, amount, iid = INV._xy(src, k), src[k.s('Amount')], src[k.s('Id')]
    to = _first_free(C['suit'], k)
    p = INV.plan_inventory(folder, 1, 'move', {'src': 'chest2', 'from': list(a), 'dst': 'suit', 'to': list(to)},
                           files=s.views())
    assert not p.blocked and p.messages[-1]['code'] == 'inv_move_part', p.messages
    s.stage(p)
    C = _byid(s.views()[0])
    lim = 10 * GD.stack_mult(iid)
    moved = _cells(C['suit'], k)[to]
    assert moved[k.s('Id')] == iid and moved[k.s('Amount')] == lim and moved[k.s('MaxAmount')] == lim
    assert _cells(C['chest2'], k)[a][k.s('Amount')] == amount - lim

    # 2) drop it back onto its own stack: merges up to the chest limit
    p = INV.plan_inventory(folder, 1, 'move', {'src': 'suit', 'from': list(to), 'dst': 'chest2', 'to': list(a)},
                           files=s.views())
    assert not p.blocked and p.messages[-1]['code'] == 'inv_merge_what', p.messages
    s.stage(p)
    C = _byid(s.views()[0])
    assert _cells(C['chest2'], k)[a][k.s('Amount')] == amount and to not in _cells(C['suit'], k)

    # 3) swap two different items between containers, then move within one container
    x = next(it for it in C['chest2']['inv'][k.s('Slots')] if INV._type(it, k) == 'Substance')
    y = next(it for it in C['chest6']['inv'][k.s('Slots')] if INV._type(it, k) in INV.TYPE_ORDER
             and it[k.s('Id')] != x[k.s('Id')])
    xa, ya = INV._xy(x, k), INV._xy(y, k)
    p = INV.plan_inventory(folder, 1, 'move', {'src': 'chest2', 'from': list(xa), 'dst': 'chest6', 'to': list(ya)},
                           files=s.views())
    assert not p.blocked and p.messages[-1]['code'] == 'inv_swap_what', p.messages
    s.stage(p)
    C = _byid(s.views()[0])
    assert _cells(C['chest6'], k)[ya][k.s('Id')] == x[k.s('Id')] and _cells(C['chest2'], k)[xa][k.s('Id')] == y[k.s('Id')]
    free6 = _first_free(C['chest6'], k)
    s.stage(INV.plan_inventory(folder, 1, 'move', {'src': 'chest6', 'from': list(ya), 'dst': 'chest6', 'to': list(free6)},
                               files=s.views()))

    # the cached parse equals a fresh parse, totals are unchanged, nothing else moved
    for v in s.views():
        assert v.json == jspan.loads(v.raw)
    assert _inv_totals(s.views()) == before
    rep = s.save()
    assert all(v['ok'] for v in rep['verified'])
    files = ops.load_slot(folder, 1)
    assert _inv_totals(files) == before
    C2 = _byid(files[0])
    for cid, inv in untouched.items():
        assert C2[cid]['inv'] == inv, cid
    assert _cells(C2['chest6'], k)[free6][k.s('Id')] == x[k.s('Id')]


def test_inventory_guards(folder):
    files = ops.load_slot(folder, 1)
    k = files[0].keys
    C = _byid(files[0])
    it = C['chest6']['inv'][k.s('Slots')][0]
    a = list(INV._xy(it, k))
    bad = [{'src': 'chest6', 'from': a, 'dst': 'chest6', 'to': a},                      # same cell
           {'src': 'chest6', 'from': [9, 9], 'dst': 'suit', 'to': [0, 0]},               # empty source
           {'src': 'chest6', 'from': a, 'dst': 'suit', 'to': [40, 40]},                  # not a cell
           {'src': 'nope', 'from': a, 'dst': 'suit', 'to': [0, 0]}]
    for p in bad:
        assert INV.plan_inventory(folder, 1, 'move', p, files=files).blocked, p


def test_inventory_edge_cases(folder):
    # technology never leaves its inventory; an item with an unknown limit is not moved to another one
    def mutate(psd, k):
        slots = psd[k.s('Chest6Inventory')][k.s('Slots')]
        slots[0][k.s('Type')][k.s('InventoryType')] = 'Technology'
        slots[1][k.s('Id')] = '^NOT_IN_TABLES'
        slots[1][k.s('Type')][k.s('InventoryType')] = 'Product'
        slots[1][k.s('MaxAmount')], slots[1][k.s('Amount')] = 7, 3
    files = _crafted(folder, mutate)
    k = files[0].keys
    C = _byid(files[0])
    s6 = C['chest6']['inv'][k.s('Slots')]
    to = list(_first_free(C['suit'], k))
    p = INV.plan_inventory(folder, 1, 'move', {'src': 'chest6', 'from': list(INV._xy(s6[0], k)), 'dst': 'suit', 'to': to},
                           files=files)
    assert p.blocked and p.messages[-1]['code'] == 'inv_tech_cross'
    p = INV.plan_inventory(folder, 1, 'move', {'src': 'chest6', 'from': list(INV._xy(s6[1], k)), 'dst': 'suit', 'to': to},
                           files=files)
    assert p.blocked and p.messages[-1]['code'] == 'inv_limit_unknown'
    # ... but inside the same inventory both can move
    free6 = list(_first_free(C['chest6'], k))
    assert not INV.plan_inventory(folder, 1, 'move', {'src': 'chest6', 'from': list(INV._xy(s6[0], k)), 'dst': 'chest6',
                                                      'to': free6}, files=files).blocked


def test_inventory_files_differ_blocked(folder):
    files = ops.load_slot(folder, 1)
    if len(files) < 2:
        pytest.skip('slot with one file')

    def mutate(psd, k):
        psd[k.s('Chest6Inventory')][k.s('Slots')][0][k.s('Amount')] += 1
    other = _crafted(folder, mutate)
    k = files[0].keys
    it = _byid(files[0])['chest6']['inv'][k.s('Slots')][0]
    to = list(_first_free(_byid(files[0])['chest6'], k))
    p = INV.plan_inventory(folder, 1, 'move', {'src': 'chest6', 'from': list(INV._xy(it, k)), 'dst': 'chest6', 'to': to},
                           files=[files[0], other[1]])
    assert p.blocked and p.messages[-1]['code'] == 'inv_files_differ'


def test_inventory_organize_merges_and_sorts(folder):
    def mutate(psd, k):                    # split one substance into three small stacks spread over the chest
        slots = psd[k.s('Chest6Inventory')][k.s('Slots')]
        base = next(x for x in slots if x[k.s('Type')][k.s('InventoryType')] == 'Substance') if any(
            x[k.s('Type')][k.s('InventoryType')] == 'Substance' for x in slots) else None
        if base is None:
            base = _copy.deepcopy(slots[0])
            base[k.s('Id')], base[k.s('Type')][k.s('InventoryType')] = '^FUEL1', 'Substance'
            base[k.s('MaxAmount')] = 9999
            slots.append(base)
        used = {(x[k.s('Index')][k.s('X')], x[k.s('Index')][k.s('Y')]) for x in slots}
        valid = [(v[k.s('X')], v[k.s('Y')]) for v in psd[k.s('Chest6Inventory')][k.s('ValidSlotIndices')]]
        free = [v for v in valid if v not in used][:2]
        base[k.s('Amount')] = 100
        for (x, y) in free:
            dup = _copy.deepcopy(base)
            dup[k.s('Index')] = {k.s('X'): x, k.s('Y'): y}
            slots.append(dup)
    files = _crafted(folder, mutate)
    k = files[0].keys
    before = _inv_totals(files)
    n_before = len(_byid(files[0])['chest6']['inv'][k.s('Slots')])
    p = INV.plan_inventory(folder, 1, 'sort', {'inv': 'chest6', 'key': 'rarity', 'dir': 'asc'}, files=files)
    assert not p.blocked, p.messages
    assert p.messages[-1]['code'] == 'inv_sort_what' and p.messages[-1]['p']['merged'] == 2
    new = []
    for f, c in zip(files, p.changes):
        v = _copy.copy(f)
        v.raw, v._json, v._keys = c.new_raw, None, (None, None)
        new.append(v)
    assert _inv_totals(new) == before
    slots = _byid(new[0])['chest6']['inv'][k.s('Slots')]
    assert len(slots) == n_before - 2                          # the two extra stacks were merged
    known = [x for x in sorted(slots, key=lambda x: (INV._xy(x, k)[1], INV._xy(x, k)[0]))
             if INV._type(x, k) in INV.TYPE_ORDER and GD.item(x[k.s('Id')])]
    ranks = [INV.rank(x[k.s('Id')]) for x in known]
    assert ranks == sorted(ranks)                                # common first, rare last
    # sorting again the same way changes nothing
    assert INV.plan_inventory(folder, 1, 'sort', {'inv': 'chest6', 'key': 'rarity', 'dir': 'asc'}, files=new).blocked


def _apply(files, plan):
    out = []
    for f, c in zip(files, plan.changes):
        v = _copy.copy(f)
        v.raw, v._json, v._keys = c.new_raw, None, (None, None)
        out.append(v)
    return out


def test_binary_item_ids_resolve():
    """Compact IDs seen in a real save: '^' + 2 flag bytes + LE one-at-a-time hash + '#seed'."""
    def bid(hexs):
        return '^' + bytes.fromhex(hexs).decode('utf-8', 'surrogateescape') + '#12345'
    plain = GD._oaat(b'UP_HYP3').to_bytes(4, 'little').hex()
    cases = {'8080' + plain: 'UP_HYP3',
             '80894d45e64e': 'UP_PULSE2',      # 2nd flag byte: bytes 0 and 3 had bit 0x20 flipped
             '8088fa5baf43': 'UP_SENGUN',
             '9080587c19ec': 'UP_SMG4',        # 1st flag byte: byte 0 was '#', stored as 'X'
             'a0855758503b': 'UP_S_SHL4'}      # both kinds of flag
    for h, want in cases.items():
        assert GD.is_binary(bid(h)) and GD.resolve(bid(h)) == want, (h, GD.resolve(bid(h)))
        assert GD.item(bid(h)) and GD.name(bid(h), 'es')
    assert GD.resolve(bid('8080deadbeef')) is None
    assert GD.resolve('^FUEL1') == 'FUEL1' and not GD.is_binary('^UP_HYP4#02507')


def test_inventory_sort_by_value_both_ways(folder):
    files = ops.load_slot(folder, 1)
    k = files[0].keys
    for d in ('asc', 'desc'):
        p = INV.plan_inventory(folder, 1, 'sort', {'inv': 'chest2', 'key': 'value', 'dir': d}, files=files)
        if p.blocked:
            assert p.messages[-1]['code'] == 'inv_nothing'
            continue
        new = _apply(files, p)
        assert _inv_totals(new) == _inv_totals(files)
        slots = sorted(_byid(new[0])['chest2']['inv'][k.s('Slots')], key=lambda x: (INV._xy(x, k)[1], INV._xy(x, k)[0]))
        vals = [INV.value(x[k.s('Id')]) for x in slots if GD.item(x[k.s('Id')]) and INV._type(x, k) in INV.TYPE_ORDER]
        assert vals == sorted(vals, reverse=(d == 'desc'))


def test_inventory_gather_group(folder):
    files = ops.load_slot(folder, 1)
    k = files[0].keys
    before = _inv_totals(files)
    srcs = INV.gather_sources(_byid(files[0]), 'chest6', 'storage')
    groups = collections.Counter(INV.item_group(x[k.s('Id')], INV._type(x, k))
                                 for cid in srcs for x in _byid(files[0])[cid]['inv'][k.s('Slots')])
    grp = next(g for g, _n in groups.most_common() if g)
    p = INV.plan_inventory(folder, 1, 'gather', {'inv': 'chest6', 'group': grp, 'from': 'storage'}, files=files)
    assert not p.blocked, p.messages
    new = _apply(files, p)
    assert _inv_totals(new) == before                                   # nothing created or lost
    C = _byid(new[0])
    left = sum(1 for cid in srcs for x in C[cid]['inv'][k.s('Slots')]
               if INV.item_group(x[k.s('Id')], INV._type(x, k)) == grp)
    warned = any(m['code'] in ('inv_gather_left', 'inv_gather_unknown') for m in p.messages)
    assert left == 0 or warned
    cells = [INV._xy(x, k) for x in C['chest6']['inv'][k.s('Slots')]]
    assert len(cells) == len(set(cells)) and set(cells) <= C['chest6']['valid'] | {INV._xy(x, k) for x in
                                                                                  _byid(files[0])['chest6']['inv'][k.s('Slots')]}
    # exosuit and ships are only touched when asked
    for cid, c in C.items():
        if c['kind'] in ('suit', 'ship'):
            assert c['inv'] == _byid(files[0])[cid]['inv']
    # only storage containers can gather; bad group / scope rejected
    assert INV.plan_inventory(folder, 1, 'gather', {'inv': 'suit', 'group': grp}, files=files).blocked
    assert INV.plan_inventory(folder, 1, 'gather', {'inv': 'chest6', 'group': 'nope'}, files=files).blocked


def test_inventory_rename_container(folder):
    s = Session(folder, 1)
    raw0 = {f.file_index: f.raw for f in s.views()}
    p = INV.plan_inventory(folder, 1, 'rename', {'inv': 'chest6', 'name': '  Gases   y más '}, files=s.views())
    assert not p.blocked and p.messages[-1]['p']['new'] == 'Gases y más'
    s.stage(p)
    for v in s.views():
        assert v.json == jspan.loads(v.raw)
    assert all(v['ok'] for v in s.save()['verified'])
    files = ops.load_slot(folder, 1)
    k = files[0].keys
    for f in files:
        assert f.psd()[k.s('Chest6Inventory')][k.s('Name')] == 'Gases y más'
        old = jspan.loads(raw0[f.file_index])
        new = f.json
        old[k.s('BaseContext')][k.s('PlayerStateData')][k.s('Chest6Inventory')][k.s('Name')] = 'Gases y más'
        assert old == new                                          # nothing else changed
    # empty name -> the game's default; same name / not a container -> refused
    p = INV.plan_inventory(folder, 1, 'rename', {'inv': 'chest6', 'name': ''}, files=files)
    assert not p.blocked and p.messages[-1]['code'] == 'inv_rename_default'
    assert INV.plan_inventory(folder, 1, 'rename', {'inv': 'chest6', 'name': 'Gases y más'}, files=files).blocked
    assert INV.plan_inventory(folder, 1, 'rename', {'inv': 'suit', 'name': 'x'}, files=files).blocked
    assert INV.plan_inventory(folder, 1, 'rename', {'inv': 'chest6', 'name': 'a\x07b'}, files=files).blocked


def test_inventory_gather_selected_items_into_corvette(folder):
    files = ops.load_slot(folder, 1)
    k = files[0].keys
    C = _byid(files[0])
    if 'corvette' not in C:
        pytest.skip('no corvette storage in the fixture')
    srcs = INV.gather_sources(C, 'corvette', 'all')
    found = {}
    for cid in srcs:
        for x in C[cid]['inv'][k.s('Slots')]:
            g = INV.item_group(x[k.s('Id')], INV._type(x, k))
            if g:
                found.setdefault(g, set()).add(GD.resolve(x[k.s('Id')]))
    grp, rids = max(found.items(), key=lambda kv: len(kv[1]))
    pick = sorted(rids, key=INV.value, reverse=True)[:2]          # "only the most valuable ones"
    p = INV.plan_inventory(folder, 1, 'gather', {'inv': 'corvette', 'group': grp, 'from': 'all', 'ids': pick}, files=files)
    assert not p.blocked, p.messages
    new = _apply(files, p)
    assert _inv_totals(new) == _inv_totals(files)
    N = _byid(new[0])
    # nothing outside the picked items moved
    for cid in srcs:
        for x in C[cid]['inv'][k.s('Slots')]:
            if GD.resolve(x[k.s('Id')]) not in pick:
                assert x in N[cid]['inv'][k.s('Slots')], (cid, x[k.s('Id')])
    arrived = {GD.resolve(x[k.s('Id')]) for x in N['corvette']['inv'][k.s('Slots')]} - \
              {GD.resolve(x[k.s('Id')]) for x in C['corvette']['inv'][k.s('Slots')]}
    assert arrived <= set(pick)
    # "any item" needs an explicit pick; an empty pick is refused
    assert INV.plan_inventory(folder, 1, 'gather', {'inv': 'corvette', 'group': 'any'}, files=files).blocked
    assert INV.plan_inventory(folder, 1, 'gather', {'inv': 'corvette', 'group': grp, 'ids': []}, files=files).blocked
    assert not INV.plan_inventory(folder, 1, 'gather', {'inv': 'corvette', 'group': 'any', 'ids': pick, 'from': 'all'},
                                  files=files).blocked


def _stackable(c, k, pred):
    return next(x for x in c['inv'][k.s('Slots')] if INV._type(x, k) in INV.TYPE_ORDER and pred(x))


def test_inventory_item_actions(folder):
    s = Session(folder, 1)
    k = s.views()[0].keys
    C = _byid(s.views()[0])
    cid = next(c for c in ('chest2', 'chest6', 'chest9', 'freighter')
               if len(C[c]['valid']) - len(C[c]['inv'][k.s('Slots')]) >= 2)
    it = _stackable(C[cid], k, lambda x: 2 <= x[k.s('Amount')] < x[k.s('MaxAmount')])
    cell, iid, a, mx = list(INV._xy(it, k)), it[k.s('Id')], it[k.s('Amount')], it[k.s('MaxAmount')]
    key = (iid, INV._type(it, k))

    def total():
        return _inv_totals(s.views())[key]
    t0 = total()
    # split keeps the total
    p = INV.plan_inventory(folder, 1, 'split', {'inv': cid, 'cell': cell, 'n': 1}, files=s.views())
    assert not p.blocked, p.messages
    s.stage(p)
    assert total() == t0 and _cells(_byid(s.views()[0])[cid], k)[tuple(cell)][k.s('Amount')] == a - 1
    # fill adds exactly up to the max
    p = INV.plan_inventory(folder, 1, 'fill', {'inv': cid, 'cell': cell}, files=s.views())
    assert not p.blocked and p.summary['code'] == 'chg_inv_fill', p.messages
    s.stage(p)
    assert total() == t0 + (mx - (a - 1)) and _cells(_byid(s.views()[0])[cid], k)[tuple(cell)][k.s('Amount')] == mx
    # duplicate adds one more full stack
    p = INV.plan_inventory(folder, 1, 'dup', {'inv': cid, 'cell': cell}, files=s.views())
    assert not p.blocked, p.messages
    s.stage(p)
    t_dup = total()
    assert t_dup == t0 + (mx - (a - 1)) + mx
    # delete removes that stack
    p = INV.plan_inventory(folder, 1, 'delete', {'inv': cid, 'cell': cell}, files=s.views())
    assert not p.blocked, p.messages
    s.stage(p)
    assert total() == t_dup - mx and tuple(cell) not in _cells(_byid(s.views()[0])[cid], k)
    for v in s.views():
        assert v.json == jspan.loads(v.raw)
    assert all(v['ok'] for v in s.save()['verified'])
    assert _inv_totals(ops.load_slot(folder, 1))[key] == t_dup - mx
    # guards
    files = ops.load_slot(folder, 1)
    C = _byid(files[0])
    full = _stackable(C[cid], k, lambda x: x[k.s('Amount')] == x[k.s('MaxAmount')])
    assert INV.plan_inventory(folder, 1, 'fill', {'inv': cid, 'cell': list(INV._xy(full, k))}, files=files).blocked
    one = _stackable(C[cid], k, lambda x: x[k.s('Amount')] >= 2)
    for n in (0, one[k.s('Amount')], 'x'):
        assert INV.plan_inventory(folder, 1, 'split', {'inv': cid, 'cell': list(INV._xy(one, k)), 'n': n}, files=files).blocked
    assert INV.plan_inventory(folder, 1, 'delete', {'inv': cid, 'cell': [40, 40]}, files=files).blocked


def test_inventory_move_selected(folder):
    files = ops.load_slot(folder, 1)
    k = files[0].keys
    C = _byid(files[0])
    src = 'chest9'
    picks = [list(INV._xy(x, k)) for x in C[src]['inv'][k.s('Slots')] if INV._type(x, k) in INV.TYPE_ORDER][:5]
    p = INV.plan_inventory(folder, 1, 'move_many', {'src': src, 'cells': picks, 'dst': 'freighter'}, files=files)
    assert not p.blocked, p.messages
    new = _apply(files, p)
    assert _inv_totals(new) == _inv_totals(files)
    N = _byid(new[0])
    left = [x for x in N[src]['inv'][k.s('Slots')] if list(INV._xy(x, k)) in picks]
    warned = any(m['code'] == 'inv_gather_left' for m in p.messages)
    assert not left or warned
    untouched = [x for x in C[src]['inv'][k.s('Slots')] if list(INV._xy(x, k)) not in picks]
    assert all(x in N[src]['inv'][k.s('Slots')] for x in untouched)
    for bad in ({'src': src, 'cells': picks, 'dst': src}, {'src': src, 'cells': [], 'dst': 'suit'},
                {'src': src, 'cells': picks + [picks[0]], 'dst': 'suit'}):
        assert INV.plan_inventory(folder, 1, 'move_many', bad, files=files).blocked


def test_inventory_add_items_and_rules(folder):
    s = Session(folder, 1)
    k = s.views()[0].keys
    C = {c['id']: c for c in INV.containers(s.views()[0], tech=True)}

    def free(cid):
        occ = {INV._xy(x, k) for x in C[cid]['inv'][k.s('Slots')]}
        return next((list(v) for v in sorted(C[cid]['valid'], key=lambda v: (v[1], v[0])) if v not in occ), None)
    cid = next(c for c in C if C[c]['kind'] == 'chest' and free(c))
    cell = free(cid)
    before = _inv_totals(s.views()).get(('^FUEL1', 'Substance'), 0)
    p = INV.plan_inventory(folder, 1, 'add', {'inv': cid, 'cell': cell, 'id': 'FUEL1', 'n': 321}, files=s.views())
    assert not p.blocked, p.messages
    s.stage(p)
    new = _cells({c['id']: c for c in INV.containers(s.views()[0])}[cid], k)[tuple(cell)]
    assert new[k.s('Id')] == '^FUEL1' and new[k.s('Amount')] == 321 and new[k.s('MaxAmount')] == 9999
    assert INV._type(new, k) == 'Substance'
    assert _inv_totals(s.views())[('^FUEL1', 'Substance')] == before + 321
    # storage never takes technology; amounts outside 1..max refused; taken cell refused
    assert INV.plan_inventory(folder, 1, 'add', {'inv': cid, 'cell': free(cid), 'id': 'HYPERDRIVE'}, files=s.views()).blocked
    assert INV.plan_inventory(folder, 1, 'add', {'inv': cid, 'cell': free(cid), 'id': 'FUEL1', 'n': 10 ** 6},
                              files=s.views()).blocked
    assert INV.plan_inventory(folder, 1, 'add', {'inv': cid, 'cell': cell, 'id': 'FUEL1', 'n': 1}, files=s.views()).blocked
    # ship technology grids follow the ship's kind
    kinds = {c: C[c].get('ship_type') for c in C if C[c]['kind'] == 'shiptech' and free(c)}
    living = next((c for c, t in kinds.items() if t == 'living'), None)
    regular = next((c for c, t in kinds.items() if t not in ('living', 'corvette')), None)
    corvette = next((c for c, t in kinds.items() if t == 'corvette'), None)
    if living:
        assert INV.accepts(C[living], 'UP_HYP3') and INV.accepts(C[living], 'HYPERDRIVE')
        assert INV.accepts(C[living], 'UA_HYP3') is None
        assert all(r[1] in ('tech', 'proc') for r in INV.addable(s.views(), living)['items'])
    if regular:
        assert INV.accepts(C[regular], 'UA_HYP3') and INV.accepts(C[regular], 'FUEL1')
        items = {r[0] for r in INV.addable(s.views(), regular)['items']}
        here = {GD.resolve(x[k.s('Id')]) for x in C[regular]['inv'][k.s('Slots')]}
        assert not (items & {h for h in here if (GD.item(h) or {}).get('k') == 'tech'})   # no second copy offered
        techs = sorted(r for r in items if (GD.item(r) or {}).get('k') == 'tech')
        rid = next((r for r in techs if GD.item(r).get('charge')), techs[0])
        p = INV.plan_inventory(folder, 1, 'add', {'inv': regular, 'cell': free(regular), 'id': rid}, files=s.views())
        assert not p.blocked, p.messages
        s.stage(p)
        t = next(x for x in s.views()[0].psd()[k.s('ShipOwnership')][C[regular]['ship']][k.s('Inventory_TechOnly')][k.s('Slots')]
                 if x[k.s('Id')] == '^' + rid)
        assert INV._type(t, k) == 'Technology'
        if GD.item(rid).get('charge'):
            assert t[k.s('Amount')] == t[k.s('MaxAmount')] == GD.item(rid)['charge_amount']
        else:
            assert (t[k.s('Amount')], t[k.s('MaxAmount')]) == (-1, 100)
        p = INV.plan_inventory(folder, 1, 'add', {'inv': regular, 'cell': free(regular), 'id': rid}, files=s.views())
        assert p.blocked                                                    # only one of each technology
    if corvette:
        assert INV.addable(s.views(), corvette)['items'] == []
    for v in s.views():
        assert v.json == jspan.loads(v.raw)
    assert all(v['ok'] for v in s.save()['verified'])
    assert _inv_totals(ops.load_slot(folder, 1))[('^FUEL1', 'Substance')] == before + 321


# ---------------------------------------------------------------- phase 3: JSON viewer ----
from olympian.core import jsonview as JV  # noqa: E402


def test_json_viewer_nodes_search_export(folder):
    f = ops.load_slot(folder, 1)[0]
    k = f.keys
    j = f.json
    root = JV.node(j, [])
    assert {c['name'] for c in root['children']} >= {'Version', 'BaseContext', 'CommonStateData'}
    psd = [k.s('BaseContext'), k.s('PlayerStateData')]
    n = JV.node(j, psd)
    assert n['rpath'] == 'BaseContext.PlayerStateData' and n['total'] == len(f.psd())
    assert len(n['children']) == min(JV.PAGE, n['total'])
    names = {c['name'] for c in n['children']}
    assert {'UniverseAddress', 'ShipOwnership'} <= names and not any(c['name'] == ';l5' for c in n['children'])
    # pages
    if n['total'] > 10:
        p2 = JV.node(j, psd, offset=10)
        assert p2['children'][0]['k'] == list(f.psd())[10]
    # notes and computed dependencies
    prim = f.psd()[k.s('PrimaryShip')]
    codes = [x['code'] for x in JV.node(j, psd + [k.s('ShipOwnership'), prim])['notes']]
    assert 'jv_ref_primary' in codes and 'jv_ship' in codes
    assert any(x['code'] == 'jv_points_ship' for x in JV.node(j, psd + [k.s('PrimaryShip')])['notes'])
    assert any(x['code'] == 'jv_address' for x in JV.node(j, psd + [k.s('UniverseAddress')])['notes'])
    # item names next to IDs; binary bytes shown as \xNN
    slot = next(x for x in f.psd()[k.s('Chest1Inventory')][k.s('Slots')])
    leaf = JV.node(j, psd + [k.s('Chest1Inventory'), k.s('Slots'), 0, k.s('Id')])
    assert leaf['value'] == JV.show_str(slot[k.s('Id')]) and '\udc80' not in leaf['value']
    if GD.resolve(slot[k.s('Id')]):
        assert leaf['hint'] == GD.name(slot[k.s('Id')], 'es')
    # bad paths are refused
    for bad in (['nope'], psd + [k.s('ShipOwnership'), 9999], [0]):
        with pytest.raises(KeyError):
            JV.node(j, bad)
    # search finds readable key names and values
    r = JV.search(j, 'PrimaryShip')
    assert any(x['path'] == psd + [k.s('PrimaryShip')] for x in r['results'])
    assert JV.search(j, 'x')['results'] == []
    # export: original keys is a valid fragment equal to the save's data; readable uses names
    frag = json.loads(JV.export(j, psd + [k.s('Chest1Inventory')], False))
    assert frag == f.psd()[k.s('Chest1Inventory')]
    legible = json.loads(JV.export(j, psd + [k.s('Chest1Inventory')], True))
    assert 'Slots' in legible and 'ValidSlotIndices' in legible


# ---------------------------------------------------------------- phase 4: missions ----
from olympian.core import missions as MI  # noqa: E402


def _mlist(f):
    k = f.keys
    return [((e[k.s('Mission')] or '').lstrip('^'), e[k.s('Progress')]) for e in f.psd()[k.s('MissionProgress')]]


def test_missions_describe(folder):
    files = ops.load_slot(folder, 1)
    d = MI.describe(files)
    assert len(d['missions']) == len(files[0].psd()[files[0].keys.s('MissionProgress')])
    assert sum(m['current'] for m in d['missions']) >= 1
    known = [m for m in d['missions'] if m['known']]
    assert len(known) > 0.9 * len(d['missions'])                 # the game's tables know almost all of them
    assert any(m['title'] for m in known)
    for m in d['missions']:
        assert m['state'] == ('inactive' if m['progress'] == -1 else 'mark' if m['progress'] == MI.MARK else 'stage')
        if m['state'] == 'stage' and m['n'] is not None:
            assert 0 <= m['progress'] <= m['n']                 # Progress is a position in the stage tree


def test_missions_reset_chain_delete_stage(folder):
    s = Session(folder, 1)
    E = MI.describe(s.views())['missions']
    # a series with later steps that are not all reset yet
    start = next(m for m in E if MI.series_after(E, m['i']) and m['progress'] != -1 and not m['dup'])
    chain = [E[j]['id'] for j in MI.series_after(E, start['i'])]
    before = [_mlist(f) for f in s.views()]
    p = MI.plan_missions(folder, 1, 'reset', {'sel': [[start['id'], 1]], 'chain': True}, files=s.views())
    assert not p.blocked, p.messages
    s.stage(p)
    for f, old in zip(s.views(), before):
        new = _mlist(f)
        assert len(new) == len(old)
        for (mid, pr), (mid0, pr0) in zip(new, old):
            assert mid == mid0
            assert pr == (-1 if mid in [start['id']] + chain else pr0)
    # deleting the active mission is refused; another one is removed from every file that has it
    cur = MI.describe(s.views())['current']
    assert MI.plan_missions(folder, 1, 'delete', {'sel': [[cur, 1]]}, files=s.views()).blocked
    victim = next(m for m in MI.describe(s.views())['missions'] if not m['current'] and not m['dup'] and not m['crit'])
    counts = [sum(1 for x in _mlist(f) if x[0] == victim['id']) for f in s.views()]
    p = MI.plan_missions(folder, 1, 'delete', {'sel': [[victim['id'], 1]]}, files=s.views())
    assert not p.blocked, p.messages
    s.stage(p)
    for f, c in zip(s.views(), counts):
        assert sum(1 for x in _mlist(f) if x[0] == victim['id']) == max(0, c - 1)
    # stage: range checked, value written
    m = next(m for m in MI.describe(s.views())['missions'] if m['state'] == 'stage' and m['n'] and m['n'] > 2 and not m['dup'])
    assert MI.plan_missions(folder, 1, 'stage', {'sel': [[m['id'], 1]], 'to': m['n'] + 1}, files=s.views()).blocked
    to = 1 if m['progress'] != 1 else 2
    p = MI.plan_missions(folder, 1, 'stage', {'sel': [[m['id'], 1]], 'to': to}, files=s.views())
    assert not p.blocked, p.messages
    s.stage(p)
    assert next(pr for mid, pr in _mlist(s.views()[0]) if mid == m['id']) == to
    for v in s.views():
        assert v.json == jspan.loads(v.raw)
    assert all(v['ok'] for v in s.save()['verified'])
    again = ops.load_slot(folder, 1)
    assert next(pr for mid, pr in _mlist(again[0]) if mid == m['id']) == to
    # bad selections
    for bad in ({'sel': []}, {'sel': [['NOPE', 1]]}, {'sel': [['x']]}):
        assert MI.plan_missions(folder, 1, 'reset', bad, files=again).blocked


def test_missions_reset_active_switches_to_neutral(folder):
    s = Session(folder, 1)
    k = s.views()[0].keys
    curs = {f.file_index: (f.psd()[k.s('CurrentMissionID')] or '').lstrip('^') for f in s.views()}
    cur = curs[s.views()[0].file_index]
    d = MI.describe(s.views())['missions']
    if not any(m['id'] == cur for m in d):
        pytest.skip('active mission not in the list')
    p = MI.plan_missions(folder, 1, 'reset', {'sel': [[cur, 1]]}, files=s.views())
    assert not p.blocked, p.messages
    assert any(m['code'] == 'mis_switched' for m in p.messages)
    assert p.summary['code'] == 'chg_mis_reset_switch'
    s.stage(p)
    for f in s.views():
        psd = f.psd()
        if curs[f.file_index] != cur:
            assert (psd[k.s('CurrentMissionID')] or '').lstrip('^') == curs[f.file_index]   # untouched there
            continue
        new = (psd[k.s('CurrentMissionID')] or '').lstrip('^')
        assert new and new != cur and (psd[k.s('PreviousMissionID')] or '').lstrip('^') == cur
        assert isinstance(psd[k.s('CurrentMissionSeed')], int)
        e = next(e for e in psd[k.s('MissionProgress')] if e[k.s('Mission')].lstrip('^') == new)
        assert e[k.s('Progress')] not in (-1, MI.MARK)                                    # a running mission
        assert all(x[k.s('Progress')] == -1 for x in psd[k.s('MissionProgress')] if x[k.s('Mission')].lstrip('^') == cur)
        assert f.json == jspan.loads(f.raw)
    assert all(v['ok'] for v in s.save()['verified'])


def test_missions_steps_from_game_texts(folder):
    E = MI.describe(ops.load_slot(folder, 1))['missions']
    running = [m for m in E if m['state'] == 'stage' and m['n'] and m['progress'] < m['n'] and MI.info(m['id']).get('h')]
    assert running
    for m in running[:40]:
        st = MI.steps(m['id'], m['progress'])
        assert st['now'] or st['next']
        for o in (st['now'], st['next']):
            if o:
                assert o['main'] and '%' not in o['main'] and '\n' not in o['main']
    m = next(m for m in E if m['state'] == 'inactive' and (MI.info(m['id']) or {}).get('h'))
    st = MI.steps(m['id'], -1)
    assert st['now'] is None and st['next']['main']
    assert MI.steps('SETTLE_TUT', MI.MARK) == {'now': None, 'next': None}
    assert MI.steps('NOPE', 3) == {'now': None, 'next': None}


# ------------------------------------------------------------------ phase 5: settlements ------------------
from olympian.core import jsonedit as JE, settlements as ST  # noqa: E402
from olympian.core.pathedit import PathEdit  # noqa: E402


def _outside_same(old: bytes, new: bytes, spans_old):
    """Bytes outside the given old spans are identical (in order)."""
    pos = 0
    rest = new
    for s0, e0 in sorted(spans_old):
        assert rest.startswith(old[pos:s0])
        rest = rest[len(old[pos:s0]):]
        pos = e0
    assert rest.endswith(old[pos:])


def test_settlement_game_data():
    sd = GD.settlement()
    assert len(sd['perks']) >= 80 and sd['max_perks'] == 18
    assert sd['stats']['names'][5] == 'Debt' and sd['stats']['good_high'][:3] == [True, True, True]
    assert sd['strength']['Production']['PositiveWide'][0] < sd['strength']['Production']['PositiveWide'][1]
    assert GD.text(sd['perks']['STARTING_NEG33']['n'], 'en')
    assert GD.text('UI_SETTLEMENT_PROD_DEBT_TIP', 'es') and GD.text('VEHICLE_MECH_TITLE_L', 'en') == 'Minotaur'


def test_settlements_describe_only_own(folder):
    files = ops.load_slot(folder, 1)
    d = ST.describe(files, 'es')
    assert d['available'] and d['owner_found']
    k = files[0].keys
    every = files[0].psd()[k.s('SettlementStatesV2')]
    assert 0 < len(d['settlements']) < len(every)                  # visited settlements are left out
    ids = ST.owner_ids(files[0])
    for s in d['settlements']:
        o = every[s['index']][k.s('Owner')]
        assert o[k.s('UID')] in ids or o[k.s('LID')] in ids
        assert [x['key'] for x in s['stats']] == GD.settlement()['stats']['names']
        assert all(p['known'] for p in s['perks'])
        assert s['advice'] and all(a['code'].startswith('adv_') for a in s['advice'])
        for p in s['perks']:
            assert '%' not in p['name'] + p['desc']
    assert [r['id'] for r in d['route']][:2] == ['SETTLEMENT_PRE', 'SETTLE_CLAIM']


def test_settlement_actions_staged_and_saved(folder):
    s = Session(folder, 1)
    d = ST.describe(s.views(), 'es')['settlements']
    a = next(x for x in d if x['pending']) if any(x['pending'] for x in d) else None
    b = d[0]
    k = s.views()[0].keys
    before = {f.file_index: f.raw for f in s.views()}
    # rename
    p = ST.plan_settlement(folder, 1, 'rename', {'uid': b['uid'], 'name': '  Nueva   Olimpia '}, files=s.views())
    assert not p.blocked and p.summary['code'] == 'chg_set_rename'
    s.stage(p)
    for f in s.views():
        i = ST._find(f, b['uid'])
        if i is None:
            continue
        assert f.psd()[k.s('SettlementStatesV2')][i][k.s('Name')] == 'Nueva Olimpia'
        assert f.json == jspan.loads(f.raw)
        sp = jspan.index_for(before[f.file_index]).get(k.psd + [k('SettlementStatesV2'), i, k('Name')])
        _outside_same(before[f.file_index], f.raw, [sp])
    # remove two perks (ids must match)
    b = next(x for x in ST.describe(s.views(), 'es')['settlements'] if x['uid'] == b['uid'])
    pick = [1, 2]
    bad = ST.plan_settlement(folder, 1, 'perk_remove', {'uid': b['uid'], 'perks': pick, 'ids': ['^NOPE', '^NOPE2']}, files=s.views())
    assert bad.blocked
    p = ST.plan_settlement(folder, 1, 'perk_remove', {'uid': b['uid'], 'perks': pick, 'ids': [b['perks'][i]['id'] for i in pick]},
                           files=s.views())
    assert not p.blocked, p.messages
    s.stage(p)
    after = next(x for x in ST.describe(s.views(), 'es')['settlements'] if x['uid'] == b['uid'])
    assert [x['id'] for x in after['perks']] == [x['id'] for x in b['perks'] if x['i'] not in pick]
    assert [x['v'] for x in after['stats']] == [x['v'] for x in b['stats']]          # stats are never touched
    # dismiss the pending decision
    if a:
        p = ST.plan_settlement(folder, 1, 'skip_decision', {'uid': a['uid']}, files=s.views())
        assert not p.blocked
        s.stage(p)
        a2 = next(x for x in ST.describe(s.views(), 'es')['settlements'] if x['uid'] == a['uid'])
        assert a2['pending'] is None and a2['last_judgement'] > a['last_judgement']
        assert ST.plan_settlement(folder, 1, 'skip_decision', {'uid': a['uid']}, files=s.views()).blocked
    # invalid names / unknown settlement
    assert ST.plan_settlement(folder, 1, 'rename', {'uid': b['uid'], 'name': 'x' * 41}, files=s.views()).blocked
    assert ST.plan_settlement(folder, 1, 'rename', {'uid': 'nope', 'name': 'x'}, files=s.views()).blocked
    with pytest.raises(LookupError):
        ST.plan_settlement(folder, 1, 'set_stats', {'uid': b['uid']}, files=s.views())
    rep = s.save()
    assert all(v['ok'] for v in rep['verified'])
    again = ST.describe(ops.load_slot(folder, 1), 'es')['settlements']
    assert any(x['name'] == 'Nueva Olimpia' for x in again)


def test_pathedit_verifies_and_rejects_overlap(folder):
    f = ops.load_slot(folder, 1)[0]
    k = f.keys
    pe = PathEdit(f)
    pe.set(k.psd + [k('SettlementStatesV2'), 0, k('Name')], 'Á"\\ test')
    new = pe.result()
    assert jspan.loads(new) == pe.patched_json()
    pe2 = PathEdit(f)
    pe2.set(k.psd + [k('SettlementStatesV2'), 0], {})
    pe2.set(k.psd + [k('SettlementStatesV2'), 0, k('Name')], 'x')
    with pytest.raises(RuntimeError):
        pe2.result()


# ---------------------------------------------- phase 5: exocraft cargo and technology grids --------------
def _free(c):
    used = {(i[0], i[1]) for i in c['items']}
    return next(list(v) for v in c['valid'] if tuple(v) not in used)


def test_inventory_vehicles_and_tech_grids(folder):
    s = Session(folder, 1)
    d = INV.describe(s.views(), 'en')
    by = {c['id']: c for c in d['containers']}
    assert 'suittech' in by and 'freightertech' in by and not any(c['kind'] == 'shiptech' for c in d['containers'])
    veh = [c for c in d['containers'] if c['kind'] == 'vehicle']
    assert veh and all(c['vname'] for c in veh) and 'vehicle4' not in by
    mts = [c for c in d['containers'] if c['kind'] == 'mttech']
    assert sum(c['active'] for c in mts) == 1
    k = s.views()[0].keys
    # rules: suit grid takes exosuit tech, not ship tech; cargo takes no tech
    assert INV.accepts(by['suittech'] | {'kind': 'suittech'}, 'HYPERDRIVE') == 'inv_add_wrong_grid'
    assert INV.accepts({'kind': 'mttech'}, 'UP_LASER1') is None
    assert INV.accepts({'kind': 'vehicle'}, 'LAUNCHFUEL') is None and INV.accepts({'kind': 'vehicle'}, 'HYPERDRIVE') == 'inv_add_not_cargo'
    assert not any(x[0].startswith('T_') and x[0] in INV._templates() for x in INV.addable(s.views(), 'suittech')['items'])
    # uninstall: core technology refused, other technology removed in both multi-tool copies
    core = next(i for i in by['suittech']['items'] if (GD.item(i[2]) or {}).get('core'))
    assert INV.plan_inventory(folder, 1, 'delete', {'inv': 'suittech', 'cell': core[:2]}, files=s.views()).blocked
    act = next(c for c in mts if c['active'])
    it = act['items'][0]
    p = INV.plan_inventory(folder, 1, 'delete', {'inv': act['id'], 'cell': it[:2]}, files=s.views())
    assert not p.blocked and p.summary['code'] == 'chg_inv_uninstall'
    s.stage(p)
    for f in s.views():
        psd = f.psd()
        store = psd[k.s('Multitools')][act['mt']][k.s('Store')]
        assert store == psd[k.s('WeaponInventory')] and len(store[k.s('Slots')]) == len(act['items']) - 1
        assert f.json == jspan.loads(f.raw)
    # install in the freighter grid and move exocraft cargo into a chest
    d = INV.describe(s.views(), 'en')
    by = {c['id']: c for c in d['containers']}
    rid = next(r for r, kind, mx in INV.addable(s.views(), 'freightertech')['items'] if kind == 'tech')
    p = INV.plan_inventory(folder, 1, 'add', {'inv': 'freightertech', 'cell': _free(by['freightertech']), 'id': rid}, files=s.views())
    assert not p.blocked, p.messages
    s.stage(p)
    v = next((c for c in d['containers'] if c['kind'] == 'vehicle' and c['items']), None)
    if v:
        chest = next(c for c in d['containers'] if c['kind'] == 'chest' and len(c['items']) < len(c['valid']))
        p = INV.plan_inventory(folder, 1, 'move', {'src': v['id'], 'from': v['items'][0][:2], 'dst': chest['id'],
                                                  'to': _free(chest)}, files=s.views())
        assert not p.blocked, p.messages
        s.stage(p)
    assert all(x['ok'] for x in s.save()['verified'])


def test_proc_modules_use_template_icon():
    assert GD._proc_icons()['CV_INV1'] == GD.item('T_CR_INV')['icon']
    assert all(GD.icon(i) for i, v in GD.load()['items'].items() if v['k'] == 'proc')


# --------------------------------------------------------- phase 5: JSON viewer expert mode ---------------
def test_json_expert_edit(folder):
    s = Session(folder, 1)
    f = s.views()[0]
    k = f.keys
    base = [k.s('BaseContext'), k.s('PlayerStateData')]
    path_name = base + [k.s('SettlementStatesV2'), 0, k.s('Name')]
    # type checks
    assert JE.plan_json_edit(folder, 1, {'file': 0, 'path': path_name, 'value': 5}, files=s.views()).blocked
    assert JE.plan_json_edit(folder, 1, {'file': 0, 'path': base, 'value': 'x'}, files=s.views()).blocked
    assert JE.plan_json_edit(folder, 1, {'file': 0, 'path': base + ['nope'], 'value': 'x'}, files=s.views()).blocked
    assert JE.plan_json_edit(folder, 1, {'file': 0, 'path': path_name, 'value': 'a\nb'}, files=s.views()).blocked
    old = jsonview_resolve(f.json, path_name)
    assert JE.plan_json_edit(folder, 1, {'file': 0, 'path': path_name, 'value': old}, files=s.views()).blocked
    # a text, an int, a float and a bool
    pos = base + [k.s('SettlementStatesV2'), 0, k.s('Population')]
    flt = next(p for p in _scalar_paths(f.json, float))
    bl = next(p for p in _scalar_paths(f.json, bool))
    for path, value in ((path_name, 'Editada'), (pos, 7), (flt, 1.5), (bl, not jsonview_resolve(f.json, bl))):
        p = JE.plan_json_edit(folder, 1, {'file': 0, 'path': path, 'value': value}, files=s.views())
        assert not p.blocked, (path, p.messages)
        assert len(p.changes) == 1 and p.summary['code'] == 'chg_json_edit'
        s.stage(p)
        v = s.views()[0]
        assert jsonview_resolve(v.json, path) == value and type(jsonview_resolve(v.json, path)) is type(value)
        assert v.json == jspan.loads(v.raw)
    assert JE.plan_json_edit(folder, 1, {'file': 0, 'path': pos, 'value': 2.5}, files=s.views()).blocked   # int stays int
    other = s.views()[1]
    assert jsonview_resolve(other.json, path_name) != 'Editada'                                         # one file only
    assert all(x['ok'] for x in s.save()['verified'])


def jsonview_resolve(j, path):
    from olympian.core import jsonview
    return jsonview.resolve(j, path)


def _scalar_paths(j, typ, path=None):
    path = path or []
    if isinstance(j, dict):
        for kk, v in j.items():
            yield from _scalar_paths(v, typ, path + [kk])
    elif isinstance(j, list):
        for i, v in enumerate(j[:3]):
            yield from _scalar_paths(v, typ, path + [i])
    elif type(j) is typ:
        yield path


# ------------------------------------------------- v0.8.0: folders of the "Zeus" versions are migrated -----
def test_legacy_folders_migrate(folder):
    p = folder.path
    old = p / '_ZeusRespaldos' / '2026-11-01_10-00-00_save'
    old.mkdir(parents=True)
    (old / 'save.hg').write_bytes((p / 'save.hg').read_bytes())
    (old / 'zeus_info.json').write_text(json.dumps({'kind': 'auto', 'slot': 1, 'created': 1, 'changes': []}), encoding='utf-8')
    (p / '_ZeusRespaldos' / 'historial.json').write_text(json.dumps(
        [{'slot': 1, 'time': 1, 'changes': [], 'backup': str(old)}]), encoding='utf-8')
    pt = p / '_ZeusPuntos' / '2026-11-01_09-00-00__mine'
    pt.mkdir(parents=True)
    (pt / 'zeus_info.json').write_text(json.dumps({'kind': 'point', 'label': 'mine', 'slot': 1, 'created': 0}), encoding='utf-8')
    # a newer folder already exists: entries are merged, nothing overwritten
    new = p / safety.BACKUP_DIR
    new.mkdir()
    (new / safety.HISTORY_FILE).write_text(json.dumps([{'slot': 1, 'time': 2, 'changes': []}]), encoding='utf-8')
    done = safety.migrate_legacy(p)
    assert done and not (p / '_ZeusRespaldos').exists() and not (p / '_ZeusPuntos').exists()
    ids = {x['id'] for x in safety.list_points(p)}
    assert f'{safety.BACKUP_DIR}/{old.name}' in ids and f'{safety.POINTS_DIR}/{pt.name}' in ids
    hist = safety.read_history(p)
    assert [h['time'] for h in hist] == [1, 2] and safety.BACKUP_DIR in hist[0]['backup']
    assert safety.migrate_legacy(p) == []                       # nothing left to do
    # old info files still work for revert / annotate
    safety.annotate(p / safety.BACKUP_DIR / old.name, slot=1)
    assert safety.point_dir(p, f'{safety.BACKUP_DIR}/{old.name}')
