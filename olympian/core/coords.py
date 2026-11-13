"""Galaxy list and coordinate conversions (portal glyphs <-> voxel address <-> galactic coordinates)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from .keys import DATA_DIR

GLYPHS = ['Sunset', 'Bird', 'Face', 'Diplo', 'Eclipse', 'Balloon', 'Boat', 'Bug',
          'Dragonfly', 'Galaxy', 'Voxel', 'Fish', 'Tent', 'Rocket', 'Tree', 'Atlas']


@lru_cache(maxsize=1)
def galaxies() -> list[dict]:
    return json.loads((DATA_DIR / 'galaxies.json').read_text(encoding='utf-8'))


def _signed(v: int, bits: int) -> int:
    return v - (1 << bits) if v >= (1 << (bits - 1)) else v


@dataclass
class Address:
    galaxy: int          # RealityIndex (0 = Euclid)
    x: int               # VoxelX  (-2048..2047)
    y: int               # VoxelY  (-128..127)
    z: int               # VoxelZ  (-2048..2047)
    system: int          # SolarSystemIndex (0..4095)
    planet: int = 0      # PlanetIndex (0..15)

    def validate(self) -> list[str]:
        errs = []
        if not 0 <= self.galaxy <= 255:
            errs.append('galaxy')
        if not -2048 <= self.x <= 2047:
            errs.append('x')
        if not -128 <= self.y <= 127:
            errs.append('y')
        if not -2048 <= self.z <= 2047:
            errs.append('z')
        if not 0 <= self.system <= 0xFFF:
            errs.append('system')
        if not 0 <= self.planet <= 15:
            errs.append('planet')
        return errs

    # Portal address: P SSS YY ZZZ XXX (12 hex digits, two's complement for Y/Z/X)
    def to_glyphs(self) -> str:
        return '%X%03X%02X%03X%03X' % (self.planet, self.system, self.y & 0xFF, self.z & 0xFFF, self.x & 0xFFF)

    @classmethod
    def from_glyphs(cls, code: str, galaxy: int) -> 'Address':
        c = re.sub(r'[^0-9A-Fa-f]', '', code)
        if len(c) != 12:
            raise ValueError('glyph_length')
        return cls(galaxy=galaxy, planet=int(c[0], 16), system=int(c[1:4], 16), y=_signed(int(c[4:6], 16), 8),
                   z=_signed(int(c[6:9], 16), 12), x=_signed(int(c[9:12], 16), 12))

    # Galactic coordinates as shown by the game/community: XXXX:YYYY:ZZZZ:SSSS
    def to_galactic(self) -> str:
        return '%04X:%04X:%04X:%04X' % (self.x + 0x7FF, self.y + 0x7F, self.z + 0x7FF, self.system)

    @classmethod
    def from_galactic(cls, text: str, galaxy: int, planet: int = 0) -> 'Address':
        parts = [p for p in re.split(r'[^0-9A-Fa-f]+', text.strip()) if p]
        if len(parts) != 4:
            raise ValueError('galactic_format')
        x, y, z, s = (int(p, 16) for p in parts)
        return cls(galaxy=galaxy, x=x - 0x7FF, y=y - 0x7F, z=z - 0x7FF, system=s, planet=planet)


def galaxy_name(ri: int) -> str:
    g = galaxies()
    return g[ri]['name'] if 0 <= ri < len(g) else f'#{ri + 1}'
