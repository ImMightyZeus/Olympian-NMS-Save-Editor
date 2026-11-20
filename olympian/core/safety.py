"""Safety net: game-running check, automatic backups, restore points, atomic writes."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BACKUP_DIR = '_OlympianBackups'
POINTS_DIR = '_OlympianRestorePoints'
INFO_FILE = 'olympian_info.json'
# names used up to v0.7.0 (when the app was still called "Zeus"); migrated on first use of a save folder
LEGACY_DIRS = {'_ZeusRespaldos': BACKUP_DIR, '_ZeusPuntos': POINTS_DIR}
LEGACY_INFO = 'zeus_info.json'
LEGACY_HISTORY = 'historial.json'
_SAFE = re.compile(r'[^A-Za-z0-9_\-]+')
KEEP_BACKUPS = 10                      # a purge never goes below the newest automatic backups
EXTRA: Path | None = None              # optional second location for every backup (set from the settings)


def game_running() -> bool | None:
    """True/False on Windows; None when it cannot be determined (other OS)."""
    if sys.platform != 'win32':
        return None
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq NMS.exe', '/NH'], capture_output=True,
                             text=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        return 'nms.exe' in out.lower()
    except Exception:  # noqa: BLE001
        return None


def _stamp() -> str:
    return time.strftime('%Y-%m-%d_%H-%M-%S')


def _clean(label: str) -> str:
    return _SAFE.sub('_', label).strip('_')[:60] or 'point'


def _copy_files(folder: Path, names: list[str], dest: Path, info: dict) -> Path:
    dest.mkdir(parents=True, exist_ok=False)
    copied = []
    for n in names:
        src = folder / n
        if src.is_file():
            shutil.copy2(src, dest / n)
            copied.append(n)
    info = dict(info, files=copied, created=time.time())
    (dest / INFO_FILE).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding='utf-8')
    return dest


def backup(folder: Path, names: list[str], action: str) -> Path:
    """Automatic backup before any write. Never overwritten."""
    dest = folder / BACKUP_DIR / f'{_stamp()}_{_clean(action)}'
    n = 1
    while dest.exists():
        n += 1
        dest = folder / BACKUP_DIR / f'{_stamp()}_{_clean(action)}_{n}'
    d = _copy_files(folder, names, dest, {'kind': 'auto', 'action': action})
    mirror(folder, d)
    return d


# ---------------------------------------------------------------- second backup location -------
def extra_root(folder: Path, extra: Path | None = None) -> Path | None:
    """Where this save folder's copies go in the extra location (one subfolder per save folder)."""
    base = extra if extra is not None else EXTRA
    if not base:
        return None
    return Path(base) / f'OlympianNMS_{_clean(Path(folder).name)}'


def check_extra(path: str, folder: Path | None) -> Path:
    """A usable extra location: an existing folder, outside the save folder."""
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError('extra_invalid')
    p = p.resolve()
    if folder is not None:
        f = Path(folder).resolve()
        if p == f or f in p.parents:
            raise ValueError('extra_inside')
    return p


def mirror(folder: Path, d: Path) -> bool:
    """Copy one backup or restore point to the extra location. The local copy is what protects the
    write, so a failure here (USB unplugged, cloud folder offline) is logged and the write goes on."""
    root = extra_root(folder)
    if root is None:
        return False
    try:
        target = root / d.parent.name / d.name
        if not target.exists():
            shutil.copytree(d, target)
        return True
    except OSError as e:
        print('extra backup copy failed:', e, flush=True)
        return False


def _dir_size(p: Path) -> int:
    n = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                n += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return n


def usage(folder: Path) -> dict:
    """Disk space used by backups and restore points, here and in the extra location."""
    out = {}
    for key, base in (('local', Path(folder)), ('extra', extra_root(folder))):
        if base is None:
            continue
        b, p = base / BACKUP_DIR, base / POINTS_DIR
        out[key] = {'backups': _dir_size(b) if b.is_dir() else 0,
                    'n_backups': sum(1 for d in b.iterdir() if d.is_dir()) if b.is_dir() else 0,
                    'points': _dir_size(p) if p.is_dir() else 0,
                    'n_points': sum(1 for d in p.iterdir() if d.is_dir()) if p.is_dir() else 0,
                    'path': str(base)}
    return out


def _autos(base: Path) -> list[Path]:
    """Automatic backups (oldest first). Restore points made by hand are never in this list."""
    d = base / BACKUP_DIR
    out = []
    if d.is_dir():
        for c in sorted(d.iterdir()):
            ip = info_path(c)
            if not (c.is_dir() and ip.is_file()):
                continue
            try:
                info = json.loads(ip.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if info.get('kind') == 'auto':
                out.append(c)
    return out


def purge_plan(folder: Path, keep: int = KEEP_BACKUPS) -> dict:
    """Which automatic backups a purge removes: all but the newest `keep`, in each location."""
    keep = max(KEEP_BACKUPS, int(keep))
    out = {}
    for key, base in (('local', Path(folder)), ('extra', extra_root(folder))):
        if base is None:
            continue
        autos = _autos(base)
        gone = autos[:-keep] if len(autos) > keep else []
        out[key] = {'remove': [g.name for g in gone], 'bytes': sum(_dir_size(g) for g in gone),
                    'kept': len(autos) - len(gone)}
    return out


def purge(folder: Path, keep: int = KEEP_BACKUPS) -> dict:
    """Delete the automatic backups purge_plan lists (never restore points, never the newest ones)."""
    plan = purge_plan(folder, keep)
    done = {}
    for key, base in (('local', Path(folder)), ('extra', extra_root(folder))):
        if key not in plan:
            continue
        n, freed = 0, 0
        for name in plan[key]['remove']:
            d = base / BACKUP_DIR / name
            if d.parent != base / BACKUP_DIR or not info_path(d).is_file():
                continue
            size = _dir_size(d)
            try:
                shutil.rmtree(d)
                n += 1
                freed += size
            except OSError as e:
                print('purge: could not remove', d, e, flush=True)
        done[key] = {'removed': n, 'freed': freed, 'kept': plan[key]['kept']}
    return done


def annotate(backup_dir, **extra) -> None:
    """Add details (slot, changes saved right after this backup) to a backup's info file."""
    p = info_path(Path(backup_dir))
    info = json.loads(p.read_text(encoding='utf-8'))
    info.update(extra)
    atomic_write(p, json.dumps(info, ensure_ascii=False, indent=1).encode('utf-8'))


def create_point(folder: Path, names: list[str], label: str, slot: int) -> Path:
    dest = folder / POINTS_DIR / f'{_stamp()}__{_clean(label)}'
    d = _copy_files(folder, names, dest, {'kind': 'point', 'label': label, 'slot': slot})
    mirror(folder, d)
    return d


def list_points(folder: Path) -> list[dict]:
    out = []
    hist = {Path(h['backup']).name: h for h in read_history(folder) if h.get('backup')}
    root = extra_root(folder)
    for kind_dir in (POINTS_DIR, BACKUP_DIR):
        base = folder / kind_dir
        local = {d.name for d in base.iterdir()} if base.is_dir() else set()
        dirs = [(d, '') for d in base.iterdir()] if base.is_dir() else []
        if root is not None and (root / kind_dir).is_dir():           # copies that only exist there
            dirs += [(d, 'X/') for d in (root / kind_dir).iterdir() if d.name not in local]
        for d, pre in sorted(dirs, key=lambda x: x[0].name, reverse=True):
            info_p = info_path(d)
            if d.is_dir() and info_p.is_file():
                try:
                    info = json.loads(info_p.read_text(encoding='utf-8'))
                except Exception:  # noqa: BLE001
                    continue
                info['id'] = f'{pre}{kind_dir}/{d.name}'
                info['extra'] = bool(pre)
                if 'changes' not in info and d.name in hist:      # backups made by v0.2.1
                    info['changes'] = hist[d.name].get('changes', [])
                out.append(info)
    return out


def point_dir(folder: Path, point_id: str) -> Path:
    base = Path(folder)
    if point_id.startswith('X/'):                     # a copy in the extra location
        base, point_id = extra_root(folder), point_id[2:]
        if base is None:
            raise FileNotFoundError('point_missing')
    kind, _, name = point_id.partition('/')
    if kind not in (POINTS_DIR, BACKUP_DIR) or not name or '/' in name or '\\' in name or name in ('.', '..'):
        raise ValueError('point_invalid')
    d = base / kind / name
    if not info_path(d).is_file():
        raise FileNotFoundError('point_missing')
    return d


def atomic_write(path: Path, data: bytes):
    tmp = path.with_name(path.name + '.olympian_tmp')
    tmp.write_bytes(data)
    os.replace(tmp, path)


HISTORY_FILE = 'history.json'


def info_path(d: Path) -> Path:
    """The info file of a backup or restore point (older ones still carry the legacy name)."""
    p = Path(d) / INFO_FILE
    if not p.is_file() and (Path(d) / LEGACY_INFO).is_file():
        return Path(d) / LEGACY_INFO
    return p


def migrate_legacy(folder: Path) -> list[str]:
    """Move the folders of older versions to their current names, keeping every backup:
    _ZeusRespaldos -> _OlympianBackups, _ZeusPuntos -> _OlympianRestorePoints, historial.json -> history.json.
    When both old and new exist, entries are moved one by one (never overwriting) and the histories merged.
    Returns what was done (for the log). Never deletes a backup."""
    folder = Path(folder)
    done = []
    for old, new in LEGACY_DIRS.items():
        src, dst = folder / old, folder / new
        if not src.is_dir():
            continue
        if not dst.exists():
            os.replace(src, dst)
            done.append(f'{old} -> {new}')
            continue
        for child in sorted(src.iterdir()):
            if child.name == LEGACY_HISTORY:
                continue
            target = dst / child.name
            if not target.exists():
                os.replace(child, target)
                done.append(f'{old}/{child.name} -> {new}/')
        old_hist = src / LEGACY_HISTORY
        if old_hist.is_file():
            _merge_history(old_hist, dst / HISTORY_FILE)
            done.append(f'{old}/{LEGACY_HISTORY} merged')
        try:
            src.rmdir()                       # only when it is empty now
        except OSError:
            pass
    old_hist = folder / BACKUP_DIR / LEGACY_HISTORY
    if old_hist.is_file():
        _merge_history(old_hist, folder / BACKUP_DIR / HISTORY_FILE)
        done.append(f'{LEGACY_HISTORY} -> {HISTORY_FILE}')
    return done


def _merge_history(old: Path, new: Path):
    def read(p):
        try:
            v = json.loads(p.read_text(encoding='utf-8'))
            return v if isinstance(v, list) else []
        except (OSError, ValueError):
            return []
    items = read(old) + (read(new) if new.is_file() else [])
    items.sort(key=lambda e: e.get('time') or 0 if isinstance(e, dict) else 0)
    for e in items:                           # history entries keep the backup path: point it to the new folder
        if isinstance(e, dict) and isinstance(e.get('backup'), str):
            for o, n in LEGACY_DIRS.items():
                e['backup'] = e['backup'].replace(o, n)
    atomic_write(new, json.dumps(items[-500:], ensure_ascii=False, indent=1).encode('utf-8'))
    os.remove(old)


def log_history(folder: Path, entry: dict) -> None:
    """Keep a list of what Olympian changed in each slot (shown on the start screen)."""
    d = Path(folder) / BACKUP_DIR
    d.mkdir(exist_ok=True)
    p = d / HISTORY_FILE
    try:
        items = json.loads(p.read_text(encoding='utf-8'))
        if not isinstance(items, list):
            items = []
    except (OSError, ValueError):
        items = []
    items.append(entry)
    atomic_write(p, json.dumps(items[-500:], ensure_ascii=False, indent=1).encode('utf-8'))


def read_history(folder: Path) -> list[dict]:
    try:
        items = json.loads((Path(folder) / BACKUP_DIR / HISTORY_FILE).read_text(encoding='utf-8'))
        return items if isinstance(items, list) else []
    except (OSError, ValueError):
        return []
