"""Phase 2b: data read from the game's own files (names, categories, which technology is chargeable).

olympian/data/gamedata.json is generated from the player's installed No Man's Sky with
tools/build_gamedata.py (HGPAKtool + MBINCompiler). The game's BankSignatures.bin fingerprint is
stored with it so Olympian can tell when the game has been updated since.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data' / 'gamedata.json'
ICONS = Path(__file__).resolve().parent.parent / 'data' / 'icons.zip'
LANGS = ('es', 'es-ES', 'en', 'ru')


@lru_cache(maxsize=1)
def load() -> dict | None:
    try:
        return json.loads(DATA.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def base_id(item_id: str) -> str:
    """'^UP_HYP4#02507' -> 'UP_HYP4'."""
    return (item_id or '').lstrip('^').split('#')[0]


def _oaat(b: bytes) -> int:
    """Jenkins one-at-a-time hash (the game uses it for compact item IDs)."""
    h = 0
    for c in b:
        h = (h + c) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    return (h + (h << 15)) & 0xFFFFFFFF


@lru_cache(maxsize=1)
def _by_hash() -> dict[bytes, str]:
    d = load()
    return {_oaat(i.encode()).to_bytes(4, 'little'): i for i in (d['items'] if d else {})}


def is_binary(item_id: str) -> bool:
    return any(0xDC80 <= ord(ch) <= 0xDCFF for ch in (item_id or ''))


def resolve(item_id: str) -> str | None:
    """Real base ID of an item. Some items (upgrade modules kept in storage, among others) are saved with a
    compact binary ID: '^' + 2 flag bytes + the 4-byte little-endian one-at-a-time hash of the ID + '#seed'.
    Flags (worked out from a real save, every case checked against the container names the player gave):
      2nd byte, bit i -> hash byte i is stored with bit 0x20 flipped;
      1st byte, bit 4+i (i < 3) -> hash byte i was '#' (0x23) and is stored as 'X'.
    Bit 7 of both flag bytes is always set (marker).
    Returns None when the ID cannot be decoded (it is then shown as a procedural item)."""
    if not is_binary(item_id):
        return base_id(item_id)
    b = (item_id or '').encode('utf-8', 'surrogateescape').lstrip(b'^').split(b'#')[0]
    if len(b) != 6 or not (b[0] & 0x80 and b[1] & 0x80):
        return None
    h = bytearray(b[2:])
    for i in range(4):
        if b[1] >> i & 1:
            h[i] ^= 0x20
        if i < 3 and b[0] >> (4 + i) & 1:   # bit 7 of each flag byte is the marker
            h[i] = 0x23
    return _by_hash().get(bytes(h))


def item(item_id: str) -> dict | None:
    d = load()
    if not d:
        return None
    rid = resolve(item_id)
    return d['items'].get(rid) if rid else None


def stack_sizes(level: str) -> dict | None:
    """Stack sizes of one difficulty level ('High' / 'Normal' / 'Low') from difficultyconfig."""
    d = load()
    return (d.get('stack_sizes') or {}).get(level) if d else None


def stack_mult(item_id: str) -> int:
    """StackMultiplier from the product/substance table (0 = unknown or not stackable)."""
    it = item(item_id)
    return int(it.get('stack') or 0) if it else 0


def name(item_id: str, lang: str = 'es') -> str | None:
    d = load()
    it = item(item_id)
    if not d or not it:
        return None
    texts = d['texts'].get(lang if lang in LANGS else 'en', {})
    n = texts.get(it.get('name') or '')
    sub = texts.get(it.get('sub') or '')
    if sub and n and '%NAME%' in sub:
        return sub.replace('%NAME%', n)
    return n


def names(lang: str = 'es') -> dict[str, str]:
    """All item names in one language (for the interface)."""
    d = load()
    if not d:
        return {}
    out = {}
    for iid in d['items']:
        n = name(iid, lang)
        if n:
            out[iid] = n
    return out


_INPUT_TOKEN = re.compile(r'\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*\b')


def _clean_desc(text: str) -> str:
    """Game description without the sentences that name input buttons (CYCLEWEAPON, FE_ALT1...): the game
    replaces those with button icons, which a tooltip cannot show."""
    paras = []
    for para in (text or '').split('\n\n'):
        keep = [x for x in re.split(r'(?<=[.!?])\s+', para.strip())
                if x and not any(len(t) >= 4 for t in _INPUT_TOKEN.findall(x))]
        if keep:
            paras.append(' '.join(keep))
    return '\n\n'.join(paras)


def stat_labels(lang: str = 'es') -> dict:
    """The game's own label for each stat (Heat Dispersion, Life Support Tanks...) in that language."""
    d = load()
    if not d:
        return {}
    texts = d['texts'].get(lang if lang in LANGS else 'en', {})
    meta = Path(__file__).resolve().parent.parent / 'data' / 'statsmeta.json'
    try:
        stats = json.loads(meta.read_text(encoding='utf-8'))['stats']
    except (OSError, ValueError, KeyError):
        return {}
    return {s: texts[s] for s in stats if s in texts}


def tips(lang: str = 'es') -> dict:
    """Tooltip data for every technology, as the game describes it: subtitle, class, description and,
    for procedural upgrades, each stat with the range the game rolls in (the exact roll depends on the seed)."""
    d = load()
    if not d:
        return {}
    texts = d['texts'].get(lang if lang in LANGS else 'en', {})
    out = {}
    for iid, it in d['items'].items():
        if it.get('k') not in ('tech', 'proc'):
            continue
        tip = {}
        if it.get('k') == 'tech' and texts.get(it.get('sub') or ''):
            tip['s'] = texts[it['sub']]
        if it.get('cls'):
            tip['c'] = it['cls']
        desc = _clean_desc(texts.get(it.get('desc') or '', ''))
        if desc:
            tip['d'] = desc
        if it.get('ranges'):
            tip['r'] = [r for r in it['ranges'] if r[0]]
        if tip:
            out[iid] = tip
    return out


def chargeable(item_id: str) -> bool | None:
    """True/False from the game data; None when the item isn't in the data (unknown)."""
    it = item(item_id)
    if not it or it.get('k') not in ('tech', 'proc'):
        return None
    return bool(it.get('charge'))


def tech_cat(item_id: str) -> str | None:
    """The game's TechnologyCategory of a technology or upgrade module (an upgrade takes its template's):
    Ship, AllShips, AllShipsExceptAlien, RobotShip, AlienShip, Corvette... None when unknown."""
    it = item(base_id(item_id))
    if not it:
        return None
    if it.get('k') == 'tech':
        return it.get('cat')
    if it.get('k') == 'proc':
        return (item(it.get('template') or '') or {}).get('cat')
    return None


_ADJ = None


def adj_group(item_id: str) -> str | None:
    """Adjacency family of a technology: the base stat it improves. A technology and its upgrade modules share
    it (photon cannon + photon cannon upgrades), which is what the game links with a coloured frame when they
    touch. Procedural upgrades carry the base technology's name; None when unknown or not a technology."""
    global _ADJ
    d = load()
    it = item(item_id)
    if not d or not it or it.get('k') not in ('tech', 'proc'):
        return None
    if _ADJ is None:
        _ADJ = {v['name']: v['stat'] for v in d['items'].values()
                if v.get('k') == 'tech' and v.get('stat') and v.get('name') and not v.get('upgrade')}
    if it.get('k') == 'tech':
        # warp drive access modules (cadmium/emeril/indium) share the hyperdrive stat but do not link in the
        # game (checked by the user, 18/nov)
        if str(it.get('name') or '').startswith('HYP_ACCESS'):
            return None
        return it.get('stat') or None
    g = _ADJ.get(it.get('name'))
    if g:
        return g
    first = (it.get('stats') or [''])[0]
    cands = [b for b in set(_ADJ.values()) if first.startswith(b)]
    return max(cands, key=len) if cands else None


# ------------------------------------------------------------------------- game install ---------
_LIB = re.compile(r'"path"\s+"([^"]+)"')


def find_game_dirs() -> list[Path]:
    """No Man's Sky install folders found through Steam (default path + libraryfolders.vdf)."""
    out = []
    steam_roots = [Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Steam',
                   Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Steam']
    home = Path.home()
    steam_roots += [home / '.steam' / 'steam', home / '.local' / 'share' / 'Steam',
                    home / '.var' / 'app' / 'com.valvesoftware.Steam' / '.local' / 'share' / 'Steam']
    libs = []
    for root in steam_roots:
        vdf = root / 'steamapps' / 'libraryfolders.vdf'
        libs.append(root)
        try:
            libs += [Path(p.replace('\\\\', '\\')) for p in _LIB.findall(vdf.read_text(encoding='utf-8', errors='ignore'))]
        except OSError:
            pass
    for lib in libs:
        d = lib / 'steamapps' / 'common' / "No Man's Sky"
        if (d / 'GAMEDATA' / 'PCBANKS').is_dir() and d not in out:
            out.append(d)
    return out


def installed_signature(game_dir: Path) -> str | None:
    p = Path(game_dir) / 'GAMEDATA' / 'PCBANKS' / 'BankSignatures.bin'
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def status(game_dir: str | None = None) -> dict:
    d = load()
    dirs = [Path(game_dir)] if game_dir else find_game_dirs()
    current = installed_signature(dirs[0]) if dirs else None
    data_sig = d['game'].get('signature') if d else None
    return {'available': bool(d), 'items': len(d['items']) if d else 0, 'built': d.get('built') if d else None,
            'compiler': d.get('compiler') if d else None, 'game_dir': str(dirs[0]) if dirs else None,
            'up_to_date': None if not (current and data_sig) else current == data_sig,
            'source': d.get('source') if d else None}


# ------------------------------------------------------------------------- icons ----------------
_PROC_CAT_ICON = {'Combat': 'damage', 'Protection': 'protection', 'Utilities': 'generic', 'Scan': 'scan'}


def _icon_key(path: str) -> str:
    return path.replace('//', '/').replace('textures/ui/', '')[:-4].replace('/', '~') + '.webp'


@lru_cache(maxsize=1)
def _proc_icons() -> dict:
    """Procedural upgrades have no icon of their own in the tables. The game draws them with the icon of
    their template technology (Template, e.g. CV_INV1 -> T_CR_INV, the corvette modules among them);
    without one, the icon of the technology whose base stat they improve, else the upgrade category icon."""
    d = load()
    if not d:
        return {}
    techs = [(v.get('stat') or '', v.get('icon'), k) for k, v in d['items'].items()
             if v.get('k') == 'tech' and v.get('icon') and v.get('stat')]
    out = {}
    for iid, v in d['items'].items():
        if v.get('k') != 'proc':
            continue
        tpl = d['items'].get(v.get('template') or '') or {}
        if tpl.get('icon'):
            out[iid] = tpl['icon']
            continue
        stat = (v.get('stats') or [''])[0] or ''
        cands = [t for t in techs if t[0] and stat.startswith(t[0])]
        cands.sort(key=lambda t: (len(t[0]) * -1, 'ROBO' in t[2] or 'ALIEN' in t[2], len(t[2])))
        out[iid] = cands[0][1] if cands else \
            f"textures/ui/frontend/icons/categories/upgradecat.{_PROC_CAT_ICON.get(v.get('cat'), 'generic')}.dds"
    return out


@lru_cache(maxsize=1)
def _zip():
    import zipfile
    try:
        return zipfile.ZipFile(ICONS)
    except (OSError, zipfile.BadZipFile):
        return None


UI_ICONS = {      # game's own UI icons used by the Summary view (read from icons.zip, never shipped loose)
    **{f'glyph{c}': f'frontend~icons~update3~portalsymbol.{c}.webp' for c in '0123456789abcdef'},
    'units': 'frontend~icons~substances~currency.units.webp',
    'nanites': 'frontend~icons~substances~currency.nanites.webp',
    'quicksilver': 'frontend~icons~substances~currency.quicksilver.webp',
    'gek': 'frontend~missions~missionfaction.gek.webp',
    'korvax': 'frontend~missions~missionfaction.korvax.webp',
    'vykeen': 'frontend~missions~missionfaction.vykeen.webp',
    'builder': 'frontend~missions~missionfaction.builder.webp',
    'tradeguild': 'frontend~icons~rank~factions.tradeguild.on.webp',
    'explorerguild': 'frontend~icons~rank~factions.explorerguild.on.webp',
    'warriorguild': 'frontend~icons~rank~factions.warriorguild.on.webp',
    'pirate': 'frontend~icons~rank~factions.pirate.on.webp',
    'freighter': 'frontend~icons~technology~render.freighterhd.webp',
    'exocraft': 'frontend~icons~u4products~proctech~roguetech.exocraft.webp',
    'weapon': 'frontend~icons~technology~render.laser1.webp',
    'home': 'frontend~icons~buildable~buildable.basecoreflag.webp',     # base computer: the game's "home"
}


def ui_icon(name: str) -> bytes | None:
    z = _zip()
    key = UI_ICONS.get(name)
    if not z or not key:
        return None
    try:
        return z.read(key)
    except KeyError:
        return None


def icon(item_id: str) -> bytes | None:
    it = item(item_id)
    if not it:
        return None
    path = it.get('icon') or _proc_icons().get(resolve(item_id) or '')
    z = _zip()
    if not path or not z:
        return None
    try:
        return z.read(_icon_key(path))
    except KeyError:
        return None


SHIP_TYPE_ICON = {'living': 'bio', 'hauler': 'cargo', 'corvette': 'corvette', 'exotic': 'exotic', 'fighter': 'fighter',
                  'solar': 'sail', 'explorer': 'science', 'interceptor': 'sentinel', 'shuttle': 'shuttle', 'ship': None}


def text(key: str, lang: str = 'es') -> str:
    """A game text in the content language (English when that language lacks it); '' when unknown."""
    d = load() or {}
    tx = d.get('texts') or {}
    return (tx.get(lang if lang in LANGS else 'en') or {}).get(key or '') or (tx.get('en') or {}).get(key or '') or ''


def settlement() -> dict:
    """Settlement perks and rules from the game (phase 5); {} with game data from an older build."""
    return (load() or {}).get('settlement') or {}


def ship_icon(ship_type: str) -> bytes | None:
    """The game's own emblem for a ship type (textures/ui/frontend/icons/shiptypes)."""
    name = SHIP_TYPE_ICON.get(ship_type, None)
    z = _zip()
    if not z:
        return None
    try:
        return z.read(f'shiptype~type.{name}.webp' if name else 'shiptype~type.webp')
    except KeyError:
        return None
