"""Xbox / Game Pass (wgs) writing, for the controlled test only. Not wired to the UI.

What the game does on every save (seen comparing two copies of one wgs folder):
  * new random GUID names for the "data" and "meta" blobs of the container it saves;
  * container.<N+1> (version 4, two entries "data"/"meta", each GUID written twice) and the old
    container.<N> plus the old blobs removed;
  * containers.index: that entry gets N+1, the save time (FILETIME, UTC) and size = data + meta;
    the header gets the save time (its GUID belongs to the cloud sync and is left as is).
The etag of each entry is assigned by the cloud when the upload finishes (5-30 s after the save), so
a local write keeps the previous etag. Test of 23/nov (friend's Game Pass save, slot 2): the game loaded
the written save, saved over it twice and the cloud took it (new etags); the change survived.
"""
from __future__ import annotations

import shutil
import struct
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import hgfile

FILETIME_EPOCH = 116444736000000000   # 1601 -> 1970, in 100 ns
DATA_NAMES = ('data', 'meta')


def filetime(t: float | None = None) -> int:
    return int((time.time() if t is None else t) * 10_000_000) + FILETIME_EPOCH


@dataclass
class Entry:
    name: str
    name2: str
    etag: str
    num: int
    state: int
    dir: bytes          # GUID, 16 bytes as stored
    time: int
    zero: int
    size: int

    @property
    def folder(self) -> str:
        return uuid.UUID(bytes_le=self.dir).hex.upper()


@dataclass
class Index:
    version: int
    package: str
    time: int
    flag: int
    guid: str
    tail: int
    entries: list[Entry] = field(default_factory=list)


class _R:
    def __init__(self, d: bytes):
        self.d, self.o = d, 0

    def take(self, fmt):
        v = struct.unpack_from(fmt, self.d, self.o)[0]
        self.o += struct.calcsize(fmt)
        return v

    def ws(self):
        n = self.take('<i')
        s = self.d[self.o:self.o + 2 * n].decode('utf-16le')
        self.o += 2 * n
        return s


def read_index(p: Path) -> Index:
    r = _R(p.read_bytes())
    ix = Index(r.take('<i'), '', 0, 0, '', 0)
    n = r.take('<q')
    ix.package, ix.time, ix.flag, ix.guid, ix.tail = r.ws(), r.take('<q'), r.take('<i'), r.ws(), r.take('<q')
    for _ in range(n):
        name, name2, etag = r.ws(), r.ws(), r.ws()
        num = r.take('<B')
        state = r.take('<i')
        d = r.d[r.o:r.o + 16]
        r.o += 16
        ix.entries.append(Entry(name, name2, etag, num, state, d, r.take('<q'), r.take('<q'), r.take('<q')))
    if r.o != len(r.d):
        raise ValueError('xbox_index_trailing')
    return ix


def _ws(s: str) -> bytes:
    return struct.pack('<i', len(s)) + s.encode('utf-16le')


def write_index(ix: Index) -> bytes:
    out = struct.pack('<iq', ix.version, len(ix.entries))
    out += _ws(ix.package) + struct.pack('<qi', ix.time, ix.flag) + _ws(ix.guid) + struct.pack('<q', ix.tail)
    for e in ix.entries:
        out += _ws(e.name) + _ws(e.name2) + _ws(e.etag) + struct.pack('<Bi', e.num, e.state) + e.dir
        out += struct.pack('<qqq', e.time, e.zero, e.size)
    return out


def read_container(p: Path) -> dict[str, bytes]:
    d = p.read_bytes()
    _ver, n = struct.unpack_from('<ii', d, 0)
    return {d[8 + 160 * i:8 + 160 * i + 128].decode('utf-16le').rstrip('\0'): d[8 + 160 * i + 128:8 + 160 * i + 144]
            for i in range(n)}


def write_container(blobs: dict[str, bytes]) -> bytes:
    out = struct.pack('<ii', 4, len(blobs))
    for name, g in blobs.items():
        out += name.encode('utf-16le').ljust(128, b'\0') + g + g
    return out


def blob_name(g: bytes) -> str:
    return uuid.UUID(bytes_le=g).hex.upper()


def replace_container(root: Path, name: str, data: bytes, meta: bytes, now: float | None = None) -> dict:
    """Save one container (e.g. 'Slot2Manual') into the wgs folder `root` the way the game does."""
    ix = read_index(root / 'containers.index')
    e = next((x for x in ix.entries if x.name == name), None)
    if e is None:
        raise LookupError('xbox_container')
    d = root / e.folder
    old = read_container(d / f'container.{e.num}')
    new = {k: uuid.uuid4().bytes_le for k in DATA_NAMES}
    (d / blob_name(new['data'])).write_bytes(data)
    (d / blob_name(new['meta'])).write_bytes(meta)
    num = e.num + 1 if e.num < 255 else 1
    (d / f'container.{num}').write_bytes(write_container(new))
    removed = [f'container.{e.num}', *(blob_name(g) for g in old.values())]
    for f in removed:
        (d / f).unlink(missing_ok=True)
    t = filetime(now)
    e.num, e.time, e.size = num, t, len(data) + len(meta)
    ix.time = t          # the header GUID stays: the sync puts its own back (seen in the 23/nov test)
    (root / 'containers.index').write_bytes(write_index(ix))
    return {'folder': e.folder, 'container': f'container.{num}', 'removed': removed,
            'added': [blob_name(new['data']), blob_name(new['meta'])]}


def pack_save(raw: bytes, meta: bytes, playtime_delta: int = 0) -> tuple[bytes, bytes]:
    """(data, meta) for a container from decompressed save JSON and the previous 360-byte meta."""
    data = hgfile.compress(raw)
    m = bytearray(meta)
    struct.pack_into('<I', m, 0x10, len(raw))
    struct.pack_into('<I', m, 0x120, int(time.time()))
    if playtime_delta:
        struct.pack_into('<Q', m, 0x08, struct.unpack_from('<Q', m, 0x08)[0] + playtime_delta)
    return data, bytes(m)


def clean_copy(src: Path, dst: Path):
    """Copy of a wgs container folder with only the files its index points to (drops leftovers)."""
    ix = read_index(src / 'containers.index')
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / 'containers.index', dst / 'containers.index')
    for e in ix.entries:
        (dst / e.folder).mkdir(exist_ok=True)
        c = f'container.{e.num}'
        shutil.copy2(src / e.folder / c, dst / e.folder / c)
        for g in read_container(src / e.folder / c).values():
            shutil.copy2(src / e.folder / blob_name(g), dst / e.folder / blob_name(g))
