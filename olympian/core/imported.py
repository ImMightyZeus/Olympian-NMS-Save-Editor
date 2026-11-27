"""A separate space for other players' saves: import (Steam or Xbox/Game Pass), view or edit, export back.

Each imported save lives in its own folder under %APPDATA%/OlympianNMS/imported/<id>/, always in Steam form as
slot 1 (save.hg = autosave, save2.hg = manual save) plus import.json. It never touches the player's own saves.

Xbox (Game Pass, folder "wgs"): containers.index lists containers (Slot<N>Auto / Slot<N>Manual); each one has a
"data" blob (the same LZ4 .hg format as Steam) and a 360-byte "meta" that is the Steam manifest from offset
0x44 on, unencrypted. Writing back to Xbox (export_xbox) does what the game does on a save (xboxwrite.py,
tested in game on 23/nov): only into the wgs folder the save was imported from, only if that slot has not
changed since the import, with the game closed and a full copy of the folder first.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from . import hgfile, ops
from .meta import MAGIC, Meta
from .savefile import SaveFile, SaveFolder, file_names, files_of_slot

STEAM_HEADER = 0x44
XBOX_META = 360
MAX_IMPORT = 400 * 1024 * 1024
_ID = re.compile(r'^imp_\d{8}_\d{6}(_\d+)?$')
_XSLOT = re.compile(r'^Slot(\d+)(Auto|Manual)$')


def root(base: Path) -> Path:
    p = base / 'imported'
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---- reading the source ------------------------------------------------------------------------

def _rd_index(p: Path) -> list[dict]:
    d = p.read_bytes()
    o = 0

    def i32():
        nonlocal o
        v = struct.unpack_from('<i', d, o)[0]
        o += 4
        return v

    def i64():
        nonlocal o
        v = struct.unpack_from('<q', d, o)[0]
        o += 8
        return v

    def ws():
        nonlocal o
        n = i32()
        s = d[o:o + 2 * n].decode('utf-16le')
        o += 2 * n
        return s
    i32()
    n = i64()
    ws(); i64(); i32(); ws(); i64()
    out = []
    for _ in range(n):
        e = {'name': ws(), 'name2': ws(), 'etag': ws(), 'num': d[o]}
        o += 1
        e['state'] = i32()
        e['dir'] = uuid.UUID(bytes_le=d[o:o + 16]).hex.upper()
        o += 16
        e['time'], _z, e['size'] = i64(), i64(), i64()
        out.append(e)
    return out


def _rd_container(p: Path) -> dict[str, str]:
    d = p.read_bytes()
    _ver, n = struct.unpack_from('<ii', d, 0)
    out = {}
    for i in range(n):
        q = 8 + 160 * i
        out[d[q:q + 128].decode('utf-16le').rstrip('\0')] = uuid.UUID(bytes_le=d[q + 128:q + 144]).hex.upper()
    return out


def _xbox_root(p: Path) -> Path | None:
    for c in [p, *p.glob('*'), *p.glob('*/*'), *p.glob('*/*/*')]:
        if c.is_dir() and (c / 'containers.index').is_file():
            return c
    return None


def _xbox_files(r: Path) -> dict[int, dict[str, tuple[bytes, bytes]]]:
    """{slot: {'Auto'|'Manual': (data, meta)}} of an Xbox wgs container folder."""
    out: dict[int, dict] = {}
    for e in _rd_index(r / 'containers.index'):
        m = _XSLOT.match(e['name'])
        if not m:
            continue
        d = r / e['dir']
        files = _rd_container(d / f"container.{e['num']}")
        if 'data' not in files or 'meta' not in files:
            continue
        data, meta = (d / files['data']).read_bytes(), (d / files['meta']).read_bytes()
        out.setdefault(int(m.group(1)), {})[m.group(2)] = (data, meta)
    return out


def steam_meta_from_xbox(meta: bytes, meta_index: int) -> Meta:
    """Steam manifest (decrypted) for an Xbox save: the fixed Steam header, then the Xbox meta as is."""
    if len(meta) < XBOX_META:
        raise ValueError('xbox_meta')
    head = bytearray(STEAM_HEADER)
    struct.pack_into('<II', head, 0, MAGIC, 0x7D4)
    raw = head + bytearray(meta[:XBOX_META]) + bytearray(4)
    m = Meta(raw, meta_index)
    m.update_sizes(struct.unpack_from('<I', meta, 0x10)[0], 0, 0)
    return m


class Source:
    """What the user pointed at: a Steam save folder (or a .zip of one) or an Xbox wgs folder."""

    def __init__(self, path: str):
        p = Path(str(path).strip().strip('"'))
        if not p.exists():
            raise FileNotFoundError('import_missing')
        self._tmp = None
        if p.is_file() and p.suffix.lower() == '.zip':
            self._tmp = Path(tempfile.mkdtemp(prefix='olympian_imp_'))
            with zipfile.ZipFile(p) as z:
                total = 0
                for i in z.infolist():
                    name = Path(i.filename)
                    if i.is_dir() or name.is_absolute() or '..' in name.parts:
                        continue
                    total += i.file_size
                    if total > MAX_IMPORT:
                        raise ValueError('import_too_big')
                    dst = self._tmp / name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(z.read(i))
            p = self._tmp
        if not p.is_dir():
            raise ValueError('import_kind')
        self.path = p
        self.xbox = _xbox_root(p)
        self.steam = None
        if not self.xbox:
            for c in [p, *p.glob('*'), *p.glob('*/*')]:
                if c.is_dir() and SaveFolder(c).file_indices():
                    self.steam = SaveFolder(c)
                    break
        if not (self.xbox or self.steam):
            raise ValueError('import_kind')
        self._x = _xbox_files(self.xbox) if self.xbox else None

    @property
    def kind(self) -> str:
        return 'xbox' if self.xbox else 'steam'

    def close(self):
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)

    def slots(self) -> list[int]:
        return sorted(self._x) if self.xbox else sorted(self.steam.slots())

    def raws(self, slot: int) -> list[tuple[int, bytes, Meta]]:
        """[(workspace file index 1|2, raw json, decrypted manifest)] of one slot."""
        out = []
        if self.xbox:
            for fi, kind in ((1, 'Auto'), (2, 'Manual')):
                if kind in self._x.get(slot, {}):
                    data, meta = self._x[slot][kind]
                    raw = hgfile.decompress(data) if hgfile.is_hg(data) else data
                    out.append((fi, raw, steam_meta_from_xbox(meta, fi + 1)))
        else:
            for n, fi in enumerate(files_of_slot(slot), 1):
                f = self.steam.file(fi)
                if f.exists():
                    f.load()
                    out.append((n, f.raw, f.meta))
        if not out:
            raise LookupError('import_slot')
        return out


# ---- the imported space ------------------------------------------------------------------------

def _write_pair(folder: Path, fi: int, raw: bytes, meta: Meta, name: str | None = None):
    m = Meta(bytearray(meta.raw), fi + 1)
    comp = hgfile.compress(raw)
    m.update_sizes(len(raw), len(comp), int(time.time()))
    if name is not None:
        m.set_name(name)
    hg_name, mf_name = file_names(fi)
    from .savefile import verify_pair
    mf = m.encrypt_for(fi + 1)
    problems = verify_pair(comp, mf, fi + 1, raw)
    if problems:
        raise ValueError('import_verify')
    (folder / hg_name).write_bytes(comp)
    (folder / mf_name).write_bytes(mf)


def scan(path: str) -> dict:
    s = Source(path)
    try:
        out = []
        for slot in s.slots():
            raws = s.raws(slot)
            tmp = Path(tempfile.mkdtemp(prefix='olympian_scan_'))
            try:
                for fi, raw, meta in raws:
                    _write_pair(tmp, fi, raw, meta)
                d = ops.describe_slot(SaveFolder(tmp), 1)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            d['slot'] = slot
            out.append(d)
        return {'kind': s.kind, 'slots': out}
    finally:
        s.close()


def do_import(base: Path, path: str, slot: int, label: str, readonly: bool) -> dict:
    s = Source(path)
    try:
        raws = s.raws(int(slot))
        stamp = time.strftime('%Y%m%d_%H%M%S')
        rid, n = f'imp_{stamp}', 1
        while (root(base) / rid).exists():
            n += 1
            rid = f'imp_{stamp}_{n}'
        d = root(base) / rid
        d.mkdir()
        try:
            for fi, raw, meta in raws:
                _write_pair(d, fi, raw, meta)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        info = {'id': rid, 'label': (label or '').strip()[:60], 'kind': s.kind, 'slot': int(slot),
                'source': str(path), 'imported': int(time.time()), 'readonly': bool(readonly)}
        if s.xbox and not s._tmp:                       # a folder (not a .zip): it can be written back
            info['xbox'] = {'root': str(s.xbox), 'state': _xbox_state(s.xbox, int(slot))}
        (d / 'import.json').write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding='utf-8')
        return info
    finally:
        s.close()


def _xbox_state(r: Path, slot: int) -> dict:
    """{container: [num, time, size]} of one slot, to notice if the game saved over it after the import."""
    from . import xboxwrite as X
    names = (f'Slot{slot}Auto', f'Slot{slot}Manual')
    return {e.name: [e.num, e.time, e.size] for e in X.read_index(Path(r) / 'containers.index').entries
            if e.name in names}


def folder_of(base: Path, rid: str) -> Path:
    if not _ID.match(rid or ''):
        raise LookupError('import_unknown')
    d = root(base) / rid
    if not (d / 'import.json').is_file():
        raise LookupError('import_unknown')
    return d


def info(base: Path, rid: str) -> dict:
    return json.loads((folder_of(base, rid) / 'import.json').read_text(encoding='utf-8'))


def listing(base: Path) -> list[dict]:
    out = []
    for d in sorted(root(base).glob('imp_*'), reverse=True):
        try:
            i = info(base, d.name)
            i['summary'] = ops.describe_slot(SaveFolder(d), 1)
        except Exception as e:  # noqa: BLE001
            i = {'id': d.name, 'error': str(e)}
        out.append(i)
    return out


def set_readonly(base: Path, rid: str, readonly: bool) -> dict:
    p = folder_of(base, rid) / 'import.json'
    i = json.loads(p.read_text(encoding='utf-8'))
    i['readonly'] = bool(readonly)
    p.write_text(json.dumps(i, ensure_ascii=False, indent=1), encoding='utf-8')
    return i


def remove(base: Path, rid: str):
    shutil.rmtree(folder_of(base, rid))


INSTALL = {
    'es': ("Partida devuelta por Olympian NMS Save Editor ({label}).\n\n"
           "Cómo instalarla (PC, Steam):\n"
           "1. Cierra No Man's Sky.\n"
           "2. Abre la carpeta de tus partidas: %APPDATA%\\HelloGames\\NMS\\st_<tu número>\n"
           "3. Haz una copia de esa carpeta por seguridad.\n"
           "4. Copia ahí los 4 archivos de esta carpeta ({files}). Reemplazan el slot {slot}.\n"
           "5. Abre el juego en esta misma computadora. Si usas Steam Cloud, deja que suba la versión nueva.\n\n"
           "La partida sigue siendo tuya: nadie más la hizo propia."),
    'en': ("Save returned by Olympian NMS Save Editor ({label}).\n\n"
           "How to install it (PC, Steam):\n"
           "1. Close No Man's Sky.\n"
           "2. Open your save folder: %APPDATA%\\HelloGames\\NMS\\st_<your number>\n"
           "3. Make a copy of that folder to be safe.\n"
           "4. Copy the 4 files of this folder there ({files}). They replace slot {slot}.\n"
           "5. Start the game on this same computer. If you use Steam Cloud, let it upload the new version.\n\n"
           "The save is still yours: nobody made it their own."),
    'ru': ("Сохранение, возвращённое Olympian NMS Save Editor ({label}).\n\n"
           "Как установить (ПК, Steam):\n"
           "1. Закройте No Man's Sky.\n"
           "2. Откройте папку сохранений: %APPDATA%\\HelloGames\\NMS\\st_<ваш номер>\n"
           "3. Сделайте копию этой папки на всякий случай.\n"
           "4. Скопируйте туда 4 файла из этой папки ({files}). Они заменят слот {slot}.\n"
           "5. Запустите игру на этом же компьютере. Если используете Steam Cloud, дайте ему загрузить новую версию.\n\n"
           "Сохранение по-прежнему ваше: никто не присвоил его."),
}


def export_steam(base: Path, rid: str, dest: str, slot: int, own_folder: str | None, lang: str = 'es') -> dict:
    """Write the imported save as Steam files for `slot` into a new subfolder of `dest` (never the user's own
    save folder), with install instructions for its owner."""
    src = folder_of(base, rid)
    i = info(base, rid)
    if not 1 <= int(slot) <= 15:
        raise ValueError('slot_invalid')
    d = Path(str(dest).strip().strip('"'))
    if not d.is_dir():
        raise FileNotFoundError('export_dest')
    if own_folder:
        own, dd = Path(own_folder).resolve(), d.resolve()
        if dd == own or own in dd.parents:              # PermissionError is an OSError: check outside any try
            raise PermissionError('export_own_folder')
    safe = re.sub(r'[^\w\- ]+', '', i.get('label') or '').strip().replace(' ', '_')[:40] or 'save'
    out = d / f'Olympian_export_{safe}_{time.strftime("%Y%m%d_%H%M%S")}'
    out.mkdir()
    names = []
    for n, fi in enumerate(files_of_slot(int(slot)), 1):
        f = SaveFile(src, n)
        if not f.exists():
            continue
        f.load()
        _write_pair(out, fi, f.raw, f.meta)
        names += list(file_names(fi))
    lang = lang if lang in INSTALL else 'en'
    (out / ('LEEME.txt' if lang == 'es' else 'README.txt')).write_text(
        INSTALL[lang].format(label=i.get('label') or rid, files=', '.join(names), slot=slot), encoding='utf-8')
    return {'folder': str(out), 'files': names}


# ---- writing back to Xbox / Game Pass ---------------------------------------------------------------

def _xbox_checks(base: Path, rid: str) -> tuple[dict, Path, list[str]]:
    i = info(base, rid)
    problems = []
    x = i.get('xbox') or {}
    r = Path(x.get('root') or '')
    if i.get('kind') != 'xbox':
        problems.append('xbox_not_xbox')
    elif not x:
        problems.append('xbox_reimport')                 # imported from a .zip or before this feature
    elif not (r / 'containers.index').is_file():
        problems.append('xbox_missing')
    elif _xbox_state(r, int(i['slot'])) != x.get('state'):
        problems.append('xbox_changed')                  # the game saved over that slot after the import
    return i, r, problems


def _xbox_pairs(src: Path, slot: int):
    """[(container name, raw json, 360-byte Xbox meta)] from the workspace (slot 1: auto, manual)."""
    out = []
    for n, kind in ((1, 'Auto'), (2, 'Manual')):
        f = SaveFile(src, n)
        if not f.exists():
            continue
        f.load()
        out.append((f'Slot{slot}{kind}', f.raw, bytes(f.meta.raw[STEAM_HEADER:STEAM_HEADER + XBOX_META])))
    return out


def xbox_preview(base: Path, rid: str, game_running) -> dict:
    i, r, problems = _xbox_checks(base, rid)
    if game_running is True:
        problems.append('game_running')
    writes = []
    if not problems:
        from . import xboxwrite as X
        names = {e.name for e in X.read_index(r / 'containers.index').entries}
        for name, raw, _m in _xbox_pairs(folder_of(base, rid), int(i['slot'])):
            if name not in names:
                problems.append('xbox_no_container')
            writes.append({'container': name, 'bytes': len(raw)})
    return {'root': str(r), 'slot': i.get('slot'), 'writes': writes, 'problems': problems,
            'game_unknown': game_running is None}


def export_xbox(base: Path, rid: str, game_running) -> dict:
    """Write the workspace back into the wgs folder it came from, as the game saves a container."""
    from . import xboxwrite as X
    pre = xbox_preview(base, rid, game_running)
    if pre['problems']:
        raise PermissionError(pre['problems'][0])
    i = info(base, rid)
    r, slot = Path(pre['root']), int(i['slot'])
    src = folder_of(base, rid)
    pairs = _xbox_pairs(src, slot)
    backup = src / '_xbox_backups' / time.strftime('%Y%m%d_%H%M%S')
    shutil.copytree(r, backup)                           # the whole wgs folder, before touching it
    written = []
    for name, raw, meta in pairs:
        data, m2 = X.pack_save(raw, meta)
        X.replace_container(r, name, data, m2)
        written.append(name)
    got = _xbox_files(r).get(slot, {})                   # read back what the game will read
    for name, raw, _meta in pairs:
        kind = 'Auto' if name.endswith('Auto') else 'Manual'
        data, meta = got.get(kind, (b'', b''))
        back = hgfile.decompress(data) if hgfile.is_hg(data) else data
        if back != raw or struct.unpack_from('<I', meta, 0x10)[0] != len(raw):
            raise ValueError('xbox_verify')
    i['xbox']['state'] = _xbox_state(r, slot)
    (src / 'import.json').write_text(json.dumps(i, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'root': str(r), 'written': written, 'backup': str(backup)}
