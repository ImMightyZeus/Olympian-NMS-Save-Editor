"""Read the parent stat and value kind of each GcStatsTypes value from NMS.exe (data tables only, no code).

Usage:  python tools/build_statsmeta.py <NMS.exe> <out json>

Two tables in .rdata:
- the enum name table: 16-byte entries {int value; pad; char *name}
- the stat info table: 24-byte entries {int stat; int parent; int ...; int ...; int ...; int kind}
Both are found by their shape, so a patch that moves them does not matter. If they cannot be found the
bundled olympian/data/statsmeta.json stays as it is.
"""
from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

import pefile

ANCHOR = b'Weapon_Laser_Mining_Speed\0'      # GcStatsTypes value 3
ANCHOR_VALUE = 3


def _sections(pe):
    base = pe.OPTIONAL_HEADER.ImageBase
    for s in pe.sections:
        if s.Name.rstrip(b'\0') in (b'.rdata', b'.data'):
            yield base + s.VirtualAddress, s.get_data()


def _cstr(blobs, va: int) -> str | None:
    for start, data in blobs:
        if start <= va < start + len(data):
            end = data.find(b'\0', va - start, va - start + 128)
            if end < 0:
                return None
            s = data[va - start:end]
            return s.decode('ascii') if re.fullmatch(rb'[A-Za-z0-9_]+', s) else None
    return None


def enum_names(blobs) -> list[str]:
    for start, data in blobs:
        i = data.find(ANCHOR)
        if i < 0:
            continue
        ptr = struct.pack('<Q', start + i)
        for s2, d2 in blobs:
            j = d2.find(ptr)
            while j >= 0:
                e = j - 8                                     # entry start
                if e >= 0 and struct.unpack_from('<i', d2, e)[0] == ANCHOR_VALUE:
                    first = e - ANCHOR_VALUE * 16
                    out = []
                    while first + len(out) * 16 + 16 <= len(d2):
                        v, p = struct.unpack_from('<i4xQ', d2, first + len(out) * 16)
                        name = _cstr(blobs, p) if v == len(out) else None
                        if not name:
                            break
                        out.append(name)
                    if len(out) > 100:
                        return out
                j = d2.find(ptr, j + 1)
    raise LookupError('stat names not found')


def stat_info(blobs, n: int) -> list[tuple[int, int]]:
    """[(parent, kind)] per stat: the 24-byte table whose first field counts 0..n-1."""
    probe = b''.join(struct.pack('<i', i) + b'\0' * 20 for i in range(3))
    for start, data in blobs:
        for off in range(0, len(data) - n * 24, 4):
            if struct.unpack_from('<i', data, off)[0] != 0:
                continue
            if any(struct.unpack_from('<i', data, off + 24 * i)[0] != i for i in range(1, n)):
                continue
            rows = [struct.unpack_from('<6i', data, off + 24 * i) for i in range(n)]
            if all(0 <= r[1] < n and 0 <= r[5] <= 3 for r in rows):
                return [(r[1], r[5]) for r in rows]
    raise LookupError('stat info table not found')


def build(exe: Path) -> dict:
    pe = pefile.PE(str(exe), fast_load=True)
    blobs = list(_sections(pe))
    names = enum_names(blobs)
    info = stat_info(blobs, len(names))
    return {'stats': {nm: [names[p], k] for nm, (p, k) in zip(names, info)}}


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    out = Path(sys.argv[2])
    d = build(Path(sys.argv[1]))
    old = json.loads(out.read_text(encoding='utf-8')) if out.exists() else {}
    d = {'game_build': old.get('game_build', ''), 'note': old.get('note', ''), **d}
    out.write_text(json.dumps(d, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'{len(d["stats"])} stats -> {out}')
