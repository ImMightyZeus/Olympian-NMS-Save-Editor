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
    return _copy_files(folder, names, dest, {'kind': 'auto', 'action': action})


def annotate(backup_dir, **extra) -> None:
    """Add details (slot, changes saved right after this backup) to a backup's info file."""
    p = info_path(Path(backup_dir))
    info = json.loads(p.read_text(encoding='utf-8'))
    info.update(extra)
    atomic_write(p, json.dumps(info, ensure_ascii=False, indent=1).encode('utf-8'))


def create_point(folder: Path, names: list[str], label: str, slot: int) -> Path:
    dest = folder / POINTS_DIR / f'{_stamp()}__{_clean(label)}'
    return _copy_files(folder, names, dest, {'kind': 'point', 'label': label, 'slot': slot})


def list_points(folder: Path) -> list[dict]:
    out = []
    hist = {Path(h['backup']).name: h for h in read_history(folder) if h.get('backup')}
    for kind_dir in (POINTS_DIR, BACKUP_DIR):
        base = folder / kind_dir
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir(), reverse=True):
            info_p = info_path(d)
            if d.is_dir() and info_p.is_file():
                try:
                    info = json.loads(info_p.read_text(encoding='utf-8'))
                except Exception:  # noqa: BLE001
                    continue
                info['id'] = f'{kind_dir}/{d.name}'
                if 'changes' not in info and d.name in hist:      # backups made by v0.2.1
                    info['changes'] = hist[d.name].get('changes', [])
                out.append(info)
    return out


def point_dir(folder: Path, point_id: str) -> Path:
    kind, _, name = point_id.partition('/')
    if kind not in (POINTS_DIR, BACKUP_DIR) or not name or '/' in name or '\\' in name or name in ('.', '..'):
        raise ValueError('point_invalid')
    d = folder / kind / name
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
