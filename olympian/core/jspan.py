"""Byte-faithful JSON navigation.

Saves are edited *in place*: we locate the byte span of a value and replace only those bytes,
so floats, key order and any odd bytes in strings are preserved exactly as the game wrote them.
"""
from __future__ import annotations

import json
import re

_STR = re.compile(rb'"(?:[^"\\]|\\.)*"', re.S)
_WS = re.compile(rb'[ \t\r\n]*')
_SCALAR = re.compile(rb'[^,\]\}\s]+')
_NEXT = re.compile(rb'["\[\]{}]')


def ws(b: bytes, i: int) -> int:
    return _WS.match(b, i).end()


# Fast path: Python's C JSON scanner on a latin-1 view of the bytes. latin-1 maps every byte to
# exactly one character, so string indexes == byte indexes. Only used to find where a value ends.
_SCAN = json.JSONDecoder(strict=False).scan_once
_VIEW: list = [None, None]          # [bytes object, its latin-1 str] (cache of the last document)


def _latin1(b: bytes) -> str:
    if _VIEW[0] is not b:
        _VIEW[0], _VIEW[1] = b, b.decode('latin-1')
    return _VIEW[1]


def skip(b: bytes, i: int) -> int:
    """Return the index just after the JSON value starting at i."""
    i = ws(b, i)
    if b[i:i + 1] in (b'{', b'['):
        try:
            return _SCAN(_latin1(b), i)[1]
        except (StopIteration, ValueError):
            pass                       # unusual content: fall back to the exact byte walker
    return _skip_py(b, i)


def _skip_py(b: bytes, i: int) -> int:
    """Reference implementation (pure Python byte walker)."""
    i = ws(b, i)
    c = b[i:i + 1]
    if c == b'"':
        return _STR.match(b, i).end()
    if c in (b'{', b'['):
        depth = 0
        while True:
            m = _NEXT.search(b, i)          # jump straight to the next quote/bracket
            if not m:
                raise ValueError('JSON truncado')
            i = m.start()
            c = b[i]
            if c == 0x22:                    # '"'
                i = _STR.match(b, i).end()
                continue
            if c in (0x7B, 0x5B):            # '{' '['
                depth += 1
            else:                            # '}' ']'
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
    m = _SCALAR.match(b, i)
    if not m:
        raise ValueError(f'Valor JSON inválido en {i}')
    return m.end()


def obj_items(b: bytes, i: int):
    """Yield (key_bytes, key_start, value_start, value_end) for the object starting at i."""
    i = ws(b, i)
    if b[i:i + 1] != b'{':
        raise ValueError('Se esperaba un objeto')
    i += 1
    while True:
        i = ws(b, i)
        if b[i:i + 1] == b'}':
            return
        m = _STR.match(b, i)
        key = b[m.start() + 1:m.end() - 1]
        ks = i
        i = ws(b, m.end())
        if b[i:i + 1] != b':':
            raise ValueError('Se esperaba ":"')
        i = ws(b, i + 1)
        e = skip(b, i)
        yield key, ks, i, e
        i = ws(b, e)
        if b[i:i + 1] == b',':
            i += 1


def arr_items(b: bytes, i: int) -> list[tuple[int, int]]:
    i = ws(b, i)
    if b[i:i + 1] != b'[':
        raise ValueError('Se esperaba una lista')
    i += 1
    out = []
    while True:
        i = ws(b, i)
        if b[i:i + 1] == b']':
            return out
        e = skip(b, i)
        out.append((i, e))
        i = ws(b, e)
        if b[i:i + 1] == b',':
            i += 1


def get(b: bytes, path, i: int = 0) -> tuple[int, int]:
    """Span (start, end) of the value at path (list of key-bytes or int indices) from position i."""
    s = ws(b, i)
    e = skip(b, s)
    for p in path:
        if isinstance(p, int):
            s, e = arr_items(b, s)[p]
        else:
            for k, _ks, vs, ve in obj_items(b, s):
                if k == p:
                    s, e = vs, ve
                    break
            else:
                raise KeyError(p)
    return s, e


def obj_index(b: bytes, i: int) -> dict[bytes, tuple[int, int]]:
    """One pass over an object: {key: (value_start, value_end)}."""
    return {k: (vs, ve) for k, _ks, vs, ve in obj_items(b, i)}


_INDEXES: list = []   # [(bytes object, Index)] for the most recent documents


def index_for(b: bytes) -> 'Index':
    """Shared Index for a bytes object, so successive edits of the same document reuse the work."""
    for doc, idx in _INDEXES:
        if doc is b:
            return idx
    idx = Index(b)
    _INDEXES.insert(0, (b, idx))
    del _INDEXES[12:]
    return idx


class Index:
    """Caches object key indexes so repeated lookups inside a big save stay fast."""

    def __init__(self, b: bytes):
        self.b = b
        self._cache: dict[int, dict] = {}

    def get(self, path, i: int = 0) -> tuple[int, int]:
        s = ws(self.b, i)
        e = None
        for p in path:
            if isinstance(p, int):
                s, e = arr_items(self.b, s)[p]
            else:
                idx = self._cache.get(s)
                if idx is None:
                    idx = self._cache[s] = obj_index(self.b, s)
                if p not in idx:
                    raise KeyError(p)
                s, e = idx[p]
        if e is None:
            e = skip(self.b, s)
        return s, e

    def has(self, path, i: int = 0) -> bool:
        try:
            self.get(path, i)
            return True
        except (KeyError, IndexError):
            return False


def carry_index(old: bytes, new: bytes, edits) -> None:
    """After replacing spans of `old` to produce `new`, register an Index for `new` that reuses the
    work done on `old`: every cached position is shifted by the size changes of the edits before it.
    Objects that were inside (or were) a replaced span are dropped and rebuilt on demand."""
    src = next((idx for doc, idx in _INDEXES if doc is old), None)
    if src is None or not src._cache:
        return
    ed = sorted((s0, e0, len(r) - (e0 - s0)) for s0, e0, r in edits)

    def shift(p):
        d = 0
        for s0, e0, delta in ed:
            if e0 <= p:
                d += delta
            elif s0 < p:          # strictly inside a replaced span
                return None
        return p + d

    dst = Index(new)
    for o, keys in src._cache.items():
        if any(s0 <= o < e0 for s0, e0, _ in ed):
            continue
        no = shift(o)
        if no is None:
            continue
        nk = {}
        for k, (a, z) in keys.items():
            na, nz = shift(a), shift(z)
            if na is None or nz is None:
                nk = None
                break
            nk[k] = (na, nz)
        if nk is not None:
            dst._cache[no] = nk
    _INDEXES.insert(0, (new, dst))
    del _INDEXES[12:]


def has(b: bytes, path, i: int = 0) -> bool:
    try:
        get(b, path, i)
        return True
    except (KeyError, IndexError):
        return False


def loads(b: bytes):
    """Parse save JSON (tolerating trailing NULs and non-UTF-8 bytes)."""
    return json.loads(b.rstrip(b'\0').decode('utf-8', 'surrogateescape'))


def dumps_value(v) -> bytes:
    """Serialize a *new* value compactly, like the game does."""
    return json.dumps(v, separators=(',', ':'), ensure_ascii=False).encode('utf-8', 'surrogateescape')


class Editor:
    """Collects non-overlapping span replacements and applies them in one go."""

    def __init__(self, data: bytes):
        self.data = data
        self.index = index_for(data)
        self.edits: list[tuple[int, int, bytes]] = []

    def replace(self, span: tuple[int, int], new: bytes):
        self.edits.append((span[0], span[1], new))

    def has(self, path, at: int = 0) -> bool:
        return self.index.has(path, at)

    def set(self, path, value, at: int = 0):
        self.replace(self.index.get(path, at), dumps_value(value) if not isinstance(value, bytes) else value)

    def apply(self) -> bytes:
        ed = sorted(self.edits, key=lambda t: t[0], reverse=True)
        for a, b2 in zip(ed, ed[1:]):
            if b2[1] > a[0]:
                raise ValueError('Ediciones superpuestas (error interno)')
        out = bytearray(self.data)
        for s, e, r in ed:
            out[s:e] = r
        new = bytes(out)
        carry_index(self.data, new, ed)
        return new
