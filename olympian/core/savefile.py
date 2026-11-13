"""Save folders, slots and individual save files."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import hgfile, jspan
from .keys import Keys
from .meta import Meta

MAX_FILE_INDEX = 30  # 15 slots x (2 files)
_SAVE_RE = re.compile(r'^save(\d*)\.hg$', re.I)


def file_names(file_index: int) -> tuple[str, str]:
    n = '' if file_index == 1 else str(file_index)
    return f'save{n}.hg', f'mf_save{n}.hg'


def slot_of(file_index: int) -> int:
    return (file_index + 1) // 2


def files_of_slot(slot: int) -> tuple[int, int]:
    return 2 * slot - 1, 2 * slot


def default_roots() -> list[Path]:
    roots = []
    env = os.environ.get('OLYMPIAN_SAVE_DIR') or os.environ.get('ZEUS_SAVE_DIR')
    if env:
        roots.append(Path(env))
    appdata = os.environ.get('APPDATA')
    if appdata:
        base = Path(appdata) / 'HelloGames' / 'NMS'
        if base.is_dir():
            roots += sorted(p for p in base.iterdir() if p.is_dir() and (p.name.startswith('st_') or p.name == 'DefaultUser'))
    return roots


@dataclass
class SaveFile:
    folder: Path
    file_index: int
    data: bytes = b''
    raw: bytes = b''
    meta: Meta | None = None
    mtime_hg: float = 0.0
    mtime_mf: float = 0.0
    _json: object = field(default=None, repr=False)
    _keys: tuple = field(default=(None, None), repr=False)   # (raw it was computed for, Keys)

    @property
    def meta_index(self) -> int:
        return self.file_index + 1

    @property
    def hg_path(self) -> Path:
        return self.folder / file_names(self.file_index)[0]

    @property
    def mf_path(self) -> Path:
        return self.folder / file_names(self.file_index)[1]

    @property
    def slot(self) -> int:
        return slot_of(self.file_index)

    def exists(self) -> bool:
        return self.hg_path.is_file() and self.mf_path.is_file()

    def load(self) -> 'SaveFile':
        self.data = self.hg_path.read_bytes()
        self.mtime_hg = self.hg_path.stat().st_mtime
        mf = self.mf_path.read_bytes()
        self.mtime_mf = self.mf_path.stat().st_mtime
        self.meta = Meta.load(mf, self.meta_index)
        self.raw = hgfile.decompress(self.data) if hgfile.is_hg(self.data) else self.data
        self._json = None
        return self

    @property
    def keys(self) -> Keys:
        # Obfuscated saves have BaseContext as "vLc"; plain (older) saves spell it out.
        if self._keys[0] is not self.raw:
            self._keys = (self.raw, Keys(obfuscated=b'"BaseContext"' not in self.raw))
        return self._keys[1]

    @property
    def json(self):
        if self._json is None:
            self._json = jspan.loads(self.raw)
        return self._json

    def psd(self) -> dict:
        k = self.keys
        return self.json[k.s('BaseContext')][k.s('PlayerStateData')]

    def changed_on_disk(self) -> bool:
        return (self.hg_path.stat().st_mtime != self.mtime_hg) or (self.mf_path.stat().st_mtime != self.mtime_mf)

    # ---- writing -------------------------------------------------------------------------
    def build(self, new_raw: bytes, *, meta_index: int | None = None, name: str | None = None,
              base_meta: Meta | None = None) -> tuple[bytes, bytes]:
        """Return (hg_bytes, mf_bytes) for new_raw, encrypted for meta_index (default: this file)."""
        mi = self.meta_index if meta_index is None else meta_index
        m = Meta(bytearray((base_meta or self.meta).raw), mi)
        comp = hgfile.compress(new_raw)
        m.update_sizes(len(new_raw), len(comp), int(time.time()))
        if name is not None:
            m.set_name(name)
        return comp, m.encrypt_for(mi)


def verify_pair(hg: bytes, mf: bytes, meta_index: int, expected_raw: bytes) -> list[str]:
    """Re-read freshly built bytes exactly as the game would. Returns a list of problems (empty = ok)."""
    problems = []
    try:
        raw = hgfile.decompress(hg)
        if raw != expected_raw:
            problems.append('El contenido descomprimido no coincide con lo esperado.')
        jspan.loads(raw)
    except Exception as e:  # noqa: BLE001
        problems.append(f'El archivo no se puede leer: {e}')
    try:
        m = Meta.load(mf, meta_index)
        if m.size_decompressed != len(expected_raw):
            problems.append('El manifest no indica el tamaño correcto.')
    except Exception as e:  # noqa: BLE001
        problems.append(f'El manifest no se puede descifrar: {e}')
    return problems


@dataclass
class SaveFolder:
    path: Path

    def file_indices(self) -> list[int]:
        out = []
        for p in self.path.iterdir():
            m = _SAVE_RE.match(p.name)
            if m:
                idx = int(m.group(1) or 1)
                if 1 <= idx <= MAX_FILE_INDEX and (self.path / file_names(idx)[1]).is_file():
                    out.append(idx)
        return sorted(out)

    def file(self, file_index: int) -> SaveFile:
        return SaveFile(self.path, file_index)

    def slots(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for i in self.file_indices():
            out.setdefault(slot_of(i), []).append(i)
        return out
