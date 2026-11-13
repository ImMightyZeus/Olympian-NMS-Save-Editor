"""Steam/PC No Man's Sky data files (saveN.hg): chunked LZ4 block streams.

Each chunk: 16-byte header <uint32 magic 0xFEEDA1E5, compressed size, decompressed size, 0>
followed by an LZ4 *block* (no frame). Chunks are at most 0x80000 decompressed bytes.
"""
from __future__ import annotations

import struct

MAGIC = 0xFEEDA1E5
CHUNK = 0x80000

try:  # fast path (bundled wheel on Windows)
    import lz4.block as _lz4

    def _decompress_block(data: bytes, size: int) -> bytes:
        return _lz4.decompress(data, uncompressed_size=size)

    def _compress_block(data: bytes) -> bytes:
        return _lz4.compress(data, store_size=False)

    HAVE_LZ4 = True
except Exception:  # pragma: no cover - fallback only used if the wheel is missing
    HAVE_LZ4 = False

    def _decompress_block(data: bytes, size: int) -> bytes:
        out = bytearray()
        i, n = 0, len(data)
        while i < n:
            token = data[i]; i += 1
            lit = token >> 4
            if lit == 15:
                while True:
                    b = data[i]; i += 1; lit += b
                    if b != 255:
                        break
            out += data[i:i + lit]; i += lit
            if i >= n:
                break
            off = data[i] | (data[i + 1] << 8); i += 2
            ml = token & 15
            if ml == 15:
                while True:
                    b = data[i]; i += 1; ml += b
                    if b != 255:
                        break
            ml += 4
            start = len(out) - off
            if off <= 0 or start < 0:
                raise ValueError('LZ4: offset inválido')
            for k in range(ml):
                out.append(out[start + k])
        if len(out) != size:
            raise ValueError('LZ4: tamaño inesperado')
        return bytes(out)

    def _compress_block(data: bytes) -> bytes:
        # Literal-only block: valid LZ4, no compression (larger file, still readable).
        n = len(data)
        head = bytearray()
        if n >= 15:
            head.append(0xF0)
            rest = n - 15
            while rest >= 255:
                head.append(255); rest -= 255
            head.append(rest)
        else:
            head.append(n << 4)
        return bytes(head) + data


def is_hg(data: bytes) -> bool:
    return len(data) >= 16 and struct.unpack_from('<I', data)[0] == MAGIC


def decompress(data: bytes) -> bytes:
    out = bytearray()
    off = 0
    while off < len(data):
        if len(data) - off < 16:
            raise ValueError('Archivo truncado (cabecera de bloque incompleta)')
        magic, csize, usize, _ = struct.unpack_from('<IIII', data, off)
        if magic != MAGIC:
            raise ValueError(f'Firma de bloque inválida en {off}')
        off += 16
        block = data[off:off + csize]
        if len(block) != csize:
            raise ValueError('Archivo truncado (bloque incompleto)')
        out += _decompress_block(block, usize)
        off += csize
    return bytes(out)


def compress(raw: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(raw), CHUNK):
        c = raw[i:i + CHUNK]
        cc = _compress_block(c)
        out += struct.pack('<IIII', MAGIC, len(cc), len(c), 0) + cc
    return bytes(out)
