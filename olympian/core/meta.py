"""Steam/PC manifest files (mf_saveN.hg): XXTEA-encrypted metadata.

The key depends on the *file index*: save.hg -> 1, save2.hg -> 2, ... ; meta index = file index + 1
(accountdata uses meta index 0). That is why a manifest cannot simply be renamed to another slot.

Layout used here (Worlds-era, 0x1B0 bytes; older sizes are handled for reading):
  0x00 magic 0xEEEEEEBE   0x04 format
  0x38 decompressed size  0x3C size on disk (compressed)
  0x4C total play time (seconds, uint64)
  0x58 save name (0x80 bytes, UTF-8, NUL padded)
  0xD8 save summary (0x80 bytes)
  0x164 timestamp (unix seconds)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = 0xEEEEEEBE
DELTA = 0x9E3779B9
M32 = 0xFFFFFFFF
BASE_KEY = list(struct.unpack('<4I', b'NAESEVADNAYRTNRG'))

OFF_SIZE_DECOMP = 0x38
OFF_SIZE_DISK = 0x3C
OFF_PLAYTIME = 0x4C
OFF_NAME = 0x58
OFF_SUMMARY = 0xD8
OFF_TIMESTAMP = 0x164
TEXT_LEN = 0x80


def _rotl(x: int, n: int) -> int:
    return ((x << n) | (x >> (32 - n))) & M32


def _key(meta_index: int) -> list[int]:
    k = BASE_KEY[:]
    k[0] = ((_rotl((meta_index ^ 0x1422CB8C) & M32, 13) * 5) + 0xE6546B64) & M32
    return k


def _mx(z, y, s, p, e, k):
    return ((((z >> 5) ^ (y << 2) & M32) + ((y >> 3) ^ (z << 4) & M32)) & M32) ^ (((s ^ y) + (k[(p & 3) ^ e] ^ z)) & M32)


def rounds_for(length: int) -> int:
    return 8 if length == 0x68 else 6


def decrypt(data: bytes, meta_index: int) -> bytes:
    n = len(data) // 4
    v = list(struct.unpack(f'<{n}I', data[:n * 4]))
    k = _key(meta_index)
    rounds = rounds_for(len(data))
    s = (rounds * DELTA) & M32
    y = v[0]
    for _ in range(rounds):
        e = (s >> 2) & 3
        for p in range(n - 1, 0, -1):
            z = v[p - 1]
            v[p] = (v[p] - _mx(z, y, s, p, e, k)) & M32
            y = v[p]
        z = v[n - 1]
        v[0] = (v[0] - _mx(z, y, s, 0, e, k)) & M32
        y = v[0]
        s = (s - DELTA) & M32
    return struct.pack(f'<{n}I', *v)


def encrypt(data: bytes, meta_index: int) -> bytes:
    n = len(data) // 4
    v = list(struct.unpack(f'<{n}I', data[:n * 4]))
    k = _key(meta_index)
    rounds = rounds_for(len(data))
    s = 0
    z = v[n - 1]
    for _ in range(rounds):
        s = (s + DELTA) & M32
        e = (s >> 2) & 3
        for p in range(n - 1):
            y = v[p + 1]
            v[p] = (v[p] + _mx(z, y, s, p, e, k)) & M32
            z = v[p]
        y = v[0]
        v[n - 1] = (v[n - 1] + _mx(z, y, s, n - 1, e, k)) & M32
        z = v[n - 1]
    return struct.pack(f'<{n}I', *v)


def _text(b: bytes) -> str:
    return b.split(b'\0', 1)[0].decode('utf-8', 'replace')


@dataclass
class Meta:
    raw: bytearray          # decrypted bytes
    meta_index: int

    @classmethod
    def load(cls, data: bytes, meta_index: int) -> 'Meta':
        dec = bytearray(decrypt(data, meta_index))
        if struct.unpack_from('<I', dec)[0] != MAGIC:
            raise ValueError('El manifest no se pudo descifrar (clave o formato desconocido).')
        return cls(dec, meta_index)

    def _u32(self, off):
        return struct.unpack_from('<I', self.raw, off)[0] if len(self.raw) >= off + 4 else None

    @property
    def size_decompressed(self):
        return self._u32(OFF_SIZE_DECOMP)

    @property
    def timestamp(self):
        return self._u32(OFF_TIMESTAMP)

    @property
    def playtime(self):
        if len(self.raw) < OFF_PLAYTIME + 8:
            return None
        return struct.unpack_from('<Q', self.raw, OFF_PLAYTIME)[0]

    @property
    def name(self):
        return _text(self.raw[OFF_NAME:OFF_NAME + TEXT_LEN]) if len(self.raw) >= OFF_NAME + TEXT_LEN else ''

    @property
    def summary(self):
        return _text(self.raw[OFF_SUMMARY:OFF_SUMMARY + TEXT_LEN]) if len(self.raw) >= OFF_SUMMARY + TEXT_LEN else ''

    def set_name(self, name: str):
        b = name.encode('utf-8')[:TEXT_LEN - 1]
        self.raw[OFF_NAME:OFF_NAME + TEXT_LEN] = b + b'\0' * (TEXT_LEN - len(b))

    def update_sizes(self, decompressed: int, on_disk: int, timestamp: int):
        struct.pack_into('<I', self.raw, OFF_SIZE_DECOMP, decompressed)
        struct.pack_into('<I', self.raw, OFF_SIZE_DISK, on_disk)
        if len(self.raw) >= OFF_TIMESTAMP + 4:
            struct.pack_into('<I', self.raw, OFF_TIMESTAMP, timestamp)

    def encrypt_for(self, meta_index: int) -> bytes:
        return encrypt(bytes(self.raw), meta_index)
