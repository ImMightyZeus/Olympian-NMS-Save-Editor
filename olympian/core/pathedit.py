"""Byte-faithful edits of single values at JSON paths (phase 5 settlements, JSON viewer expert mode).

Each edit replaces exactly the bytes of one value; everything else in the file stays byte-identical. The
result is verified independently before it is accepted:
  1) outside the replaced values the document is byte-identical to the original;
  2) every edited path parses back to exactly the new value;
  3) the document still parses as a whole (the caller's FileChange re-reads it before writing).
"""
from __future__ import annotations

import copy

from . import jspan
from .savefile import SaveFile


def _norm(path) -> list:
    return [p if isinstance(p, (int, bytes)) else str(p).encode() for p in path]


class PathEdit:
    def __init__(self, f: SaveFile):
        self.f, self.k, self.b = f, f.keys, f.raw
        self.idx = jspan.index_for(self.b)
        self.edits: list[tuple[list, object]] = []          # (full path from the root, new value)

    def get(self, path):
        s, e = self.idx.get(_norm(path))
        return jspan.loads(self.b[s:e])

    def set(self, path, value):
        self.edits.append((_norm(path), value))

    def spans(self):
        out = []
        for path, value in self.edits:
            s, e = self.idx.get(path)
            out.append((s, e, jspan.dumps_value(value)))
        return out

    def result(self) -> bytes:
        ed = sorted(self.spans(), key=lambda t: t[0])
        for x, y in zip(ed, ed[1:]):
            if x[1] > y[0]:
                raise RuntimeError('edit_overlap')
        out, pos = bytearray(), 0
        for s, e, r in ed:
            out += self.b[pos:s] + r
            pos = e
        out += self.b[pos:]
        new = bytes(out)
        po = pn = 0
        for s, e, r in ed:                                 # 1) nothing else changed
            if new[pn:pn + (s - po)] != self.b[po:s]:
                raise RuntimeError('edit_verify_outside')
            pn += (s - po) + len(r)
            po = e
        if new[pn:] != self.b[po:]:
            raise RuntimeError('edit_verify_outside')
        jspan.carry_index(self.b, new, ed)
        nidx = jspan.index_for(new)
        for path, value in self.edits:                     # 2) each edit reads back as intended
            s, e = nidx.get(path)
            if jspan.loads(new[s:e]) != value:
                raise RuntimeError('edit_verify')
        return new

    def patched_json(self):
        """The parsed document with the same edits (copies only along the edited paths)."""
        j = self.f.json
        if j is None:
            return None
        root = copy.copy(j)
        for path, value in self.edits:
            node = root
            keys = [p.decode() if isinstance(p, bytes) else p for p in path]
            for p in keys[:-1]:
                child = copy.copy(node[p])
                node[p] = child
                node = child
            node[keys[-1]] = copy.deepcopy(value)
        return root
