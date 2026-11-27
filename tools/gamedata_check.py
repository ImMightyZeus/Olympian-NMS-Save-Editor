"""Check the tools Olympian needs to read the game's files, and time a full rebuild of its game data.

It reads the game's .pak files and never writes to the game folder or to olympian/data: everything
is built in a temporary folder that is deleted at the end (--keep leaves it). The results are
printed and saved to gamedata_check_report.txt in the Olympian folder.

What it checks:
  - game fingerprint (BankSignatures.bin) against the data that ships with Olympian
  - .NET runtime, MBINCompiler, HGPAKtool and Pillow: installed version and latest release
  - MBINCompiler can read this version of the game (the rebuild itself is the test)

Downloads are optional and always asked first, unless --yes is given. MBINCompiler goes to
%LOCALAPPDATA%\\OlympianNMS\\tools and is checked against the SHA-256 published by GitHub.

Usage:
  python tools/gamedata_check.py [--pcbanks DIR] [--mbinc EXE] [--check] [--yes] [--offline]
                                 [--no-icons] [--keep] [--lang es|en]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import locale
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

from olympian.core import gamedata  # noqa: E402

VERSION = '1.0'
WINDOWS = os.name == 'nt'
MBINC_REPO = 'monkeyman192/MBINCompiler'
DOTNET_URL = 'https://dotnet.microsoft.com/download/dotnet/10.0'
WINGET_DOTNET = ['winget', 'install', '-e', '--id', 'Microsoft.DotNet.Runtime.10',
                 '--accept-package-agreements', '--accept-source-agreements']
PY_PACKAGES = ('hgpaktool', 'pillow')
SHIPTYPES = 'textures/ui/frontend/icons/shiptypes/'
ICON_SIZE, SHIPTYPE_SIZE, WEBP_QUALITY = 64, 128, 80
REPORT = ROOT / 'gamedata_check_report.txt'

MSG = {
    'title': ('Olympian: verificación de datos del juego', 'Olympian: game data check'),
    'game_dir': ('Carpeta del juego', 'Game folder'),
    'no_game': ('No encontré la carpeta PCBANKS del juego. Indícala con --pcbanks "ruta\\GAMEDATA\\PCBANKS".',
                'Could not find the game\'s PCBANKS folder. Pass it with --pcbanks "path\\GAMEDATA\\PCBANKS".'),
    'versions': ('Versiones', 'Versions'),
    'fingerprint': ('Huella del juego', 'Game fingerprint'),
    'fp_same': ('coincide con los datos de Olympian', 'matches Olympian\'s data'),
    'fp_diff': ('el juego cambió desde que se generaron los datos de Olympian',
                'the game changed since Olympian\'s data was built'),
    'fp_unknown': ('no se pudo comparar', 'could not compare'),
    'installed': ('instalado', 'installed'),
    'latest': ('última', 'latest'),
    'missing': ('no instalado', 'not installed'),
    'up_to_date': ('al día', 'up to date'),
    'outdated': ('hay una versión más nueva', 'a newer version is available'),
    'offline': ('no se pudo consultar la última versión', 'could not look up the latest release'),
    'dotnet_none': ('.NET 8 o 10 no está instalado; MBINCompiler lo necesita',
                    '.NET 8 or 10 is not installed; MBINCompiler needs it'),
    'ask_dotnet': ('¿Instalar el runtime de .NET 10 con winget? Windows puede pedir permiso de administrador.',
                   'Install the .NET 10 runtime with winget? Windows may ask for administrator permission.'),
    'dotnet_manual': ('Instala el runtime de .NET 10 desde {url} y vuelve a ejecutar esta verificación.',
                      'Install the .NET 10 runtime from {url} and run this check again.'),
    'ask_mbinc': ('¿Descargar MBINCompiler {tag} ({asset}, {mb:.1f} MB) desde GitHub?',
                  'Download MBINCompiler {tag} ({asset}, {mb:.1f} MB) from GitHub?'),
    'ask_pip': ('¿Instalar o actualizar {names} con pip en este Python?',
                'Install or update {names} with pip in this Python?'),
    'downloading': ('Descargando {name}...', 'Downloading {name}...'),
    'hash_ok': ('SHA-256 verificado', 'SHA-256 verified'),
    'hash_bad': ('El archivo descargado no coincide con el SHA-256 de GitHub; lo descarté.',
                 'The downloaded file does not match GitHub\'s SHA-256; it was discarded.'),
    'no_hash': ('GitHub no publicó SHA-256 para este archivo; solo se verificó el tamaño.',
                'GitHub published no SHA-256 for this file; only its size was checked.'),
    'dl_fail': ('No se pudo descargar: {err}', 'Download failed: {err}'),
    'skipped': ('omitido', 'skipped'),
    'cannot_run': ('Faltan herramientas; no se puede medir la generación de datos.',
                   'Tools are missing; the data rebuild cannot be timed.'),
    'data': ('Datos (tablas y textos)', 'Data (tables and texts)'),
    'icons': ('Íconos', 'Icons'),
    'step_extract': ('leer los .pak', 'read the .pak files'),
    'step_convert': ('convertir con MBINCompiler', 'convert with MBINCompiler'),
    'step_parse': ('procesar y guardar', 'process and save'),
    'step_icons_read': ('leer los .pak', 'read the .pak files'),
    'step_icons_conv': ('decodificar y convertir a WebP', 'decode and convert to WebP'),
    'total': ('Total', 'Total'),
    'mbinc_fail': ('MBINCompiler no pudo leer esta versión del juego. Probablemente aún no hay una versión '
                   'de MBINCompiler para ella. Detalle: {err}',
                   'MBINCompiler could not read this game version. There is probably no MBINCompiler release '
                   'for it yet. Detail: {err}'),
    'same_data': ('idéntico a los datos que trae Olympian', 'identical to the data Olympian ships'),
    'diff_data': ('distinto a los datos que trae Olympian ({detail})', 'different from the data Olympian ships ({detail})'),
    'diff_expected': ('esperado: el juego cambió', 'expected: the game changed'),
    'icons_same': ('los mismos {n} íconos que trae Olympian', 'the same {n} icons Olympian ships'),
    'icons_diff': ('{n} íconos; faltan {miss}, nuevos {new} respecto a los de Olympian',
                   '{n} icons; {miss} missing and {new} new compared with Olympian\'s'),
    'icons_missing_pak': ('{n} íconos no están en los .pak', '{n} icons are not in the .pak files'),
    'kept': ('Archivos generados en: {path}', 'Generated files kept in: {path}'),
    'report': ('Informe guardado en: {path}', 'Report saved to: {path}'),
    'paste': ('Si algo falló, incluye el contenido de ese archivo al reportarlo.',
              'If something failed, include the contents of that file when you report it.'),
    'yes_no': ('[s/N]', '[y/N]'),
}


class Out:
    """Prints and keeps the lines for the report file."""

    def __init__(self, lang: str):
        self.i = 0 if lang == 'es' else 1
        self.lines: list[str] = []

    def t(self, key: str, **kw) -> str:
        return MSG[key][self.i].format(**kw)

    def __call__(self, text: str = ''):
        print(text, flush=True)
        self.lines.append(text)

    def note(self, text: str):
        self.lines.append(text)

    def save(self):
        text = '\n'.join(self.lines) + '\n'
        home = str(Path.home())
        REPORT.write_text(text.replace(home, '~'), encoding='utf-8')


# ------------------------------------------------------------------------------- helpers --------

def get_json(url: str) -> dict | list | None:
    req = urllib.request.Request(url, headers={'User-Agent': f'OlympianNMS-gamedata-check/{VERSION}',
                                               'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode('utf-8'))
    except (OSError, ValueError):
        return None


def vkey(v: str | None) -> tuple:
    """Sort key for versions such as 'v7.14.0-pre1' or '12.0.0'; a pre-release sorts before its final."""
    m = re.match(r'v?(\d+(?:\.\d+)*)(?:-?([0-9A-Za-z.]+))?', v or '')
    if not m:
        return ((),)
    nums = tuple(int(x) for x in m.group(1).split('.'))
    nums += (0,) * (4 - len(nums))
    pre = m.group(2)
    return nums, 0 if pre else 1, pre or ''


def ask(out: Out, question: str, assume_yes: bool) -> bool:
    if assume_yes:
        out(f'{question} -> yes')
        return True
    if not sys.stdin or not sys.stdin.isatty():
        out(f'{question} -> {out.t("skipped")}')
        return False
    answer = input(f'{question} {out.t("yes_no")} ').strip().lower()
    out.note(f'{question} -> {answer or "n"}')
    return answer in ('s', 'si', 'sí', 'y', 'yes')


def tools_dir() -> Path:
    if WINDOWS:
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        base = Path.home() / '.local' / 'share'
    return base / 'OlympianNMS' / 'tools'


def default_lang() -> str:
    try:
        s = json.loads((Path(os.environ.get('APPDATA', '')) / 'OlympianNMS' / 'settings.json').read_text(encoding='utf-8'))
        if s.get('ui_lang') in ('es', 'en'):
            return s['ui_lang']
    except (OSError, ValueError):
        pass
    loc = (locale.getlocale()[0] or os.environ.get('LANG') or '').lower()
    return 'es' if loc.startswith(('es', 'spanish')) else 'en'


def find_pcbanks(arg: str | None) -> Path | None:
    if arg:
        p = Path(arg)
        if (p / 'GAMEDATA' / 'PCBANKS').is_dir():
            p = p / 'GAMEDATA' / 'PCBANKS'
        return p if p.is_dir() else None
    dirs = list(gamedata.find_game_dirs())
    for gog in (Path(r"C:\GOG Games\No Man's Sky"), Path(r"C:\Program Files (x86)\GOG Galaxy\Games\No Man's Sky")):
        if (gog / 'GAMEDATA' / 'PCBANKS').is_dir():
            dirs.append(gog)
    return dirs[0] / 'GAMEDATA' / 'PCBANKS' if dirs else None


# ------------------------------------------------------------------------------- tools ----------

def dotnet_majors() -> set[int]:
    candidates = ['dotnet']
    if WINDOWS:
        candidates.append(str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'dotnet' / 'dotnet.exe'))
    for exe in candidates:
        try:
            r = subprocess.run([exe, '--list-runtimes'], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        found = {int(m) for m in re.findall(r'^Microsoft\.NETCore\.App (\d+)\.', r.stdout, re.M)}
        if found:
            return found
    return set()


def mbinc_asset(majors: set[int]) -> str | None:
    """MBINCompiler build that runs on the installed .NET (10 first, then 8)."""
    if sys.platform == 'darwin':
        return None
    if 10 in majors:
        return 'MBINCompiler-dotnet10.exe' if WINDOWS else 'MBINCompiler-linux-dotnet10'
    if 8 in majors:
        return 'MBINCompiler.exe' if WINDOWS else 'MBINCompiler-linux'
    return None


def managed_mbinc() -> Path:
    return tools_dir() / 'MBINCompiler' / ('MBINCompiler.exe' if WINDOWS else 'MBINCompiler')


def mbinc_version(exe: Path | None) -> str | None:
    if not exe or not exe.exists():
        return None
    try:
        r = subprocess.run([str(exe), 'version'], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?', r.stdout)
    return m.group(0) if m else None


def download_mbinc(out: Out, release: dict, asset_name: str) -> Path | None:
    asset = next((a for a in release.get('assets', []) if a.get('name') == asset_name), None)
    if not asset:
        out(out.t('dl_fail', err=f'{asset_name} not in {release.get("tag_name")}'))
        return None
    dest = managed_mbinc()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.download')
    out(out.t('downloading', name=f'{asset_name} {release.get("tag_name")}'))
    try:
        req = urllib.request.Request(asset['browser_download_url'], headers={'User-Agent': f'OlympianNMS-gamedata-check/{VERSION}'})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, 'wb') as f:
            shutil.copyfileobj(r, f)
    except OSError as e:
        out(out.t('dl_fail', err=e))
        tmp.unlink(missing_ok=True)
        return None
    data = tmp.read_bytes()
    digest = asset.get('digest') or ''
    if len(data) != asset.get('size') or (digest.startswith('sha256:') and hashlib.sha256(data).hexdigest() != digest[7:]):
        out(out.t('hash_bad'))
        tmp.unlink(missing_ok=True)
        return None
    out(out.t('hash_ok') if digest.startswith('sha256:') else out.t('no_hash'))
    os.replace(tmp, dest)
    if not WINDOWS:
        dest.chmod(0o755)
    (dest.parent / 'installed.json').write_text(json.dumps({
        'tag': release.get('tag_name'), 'asset': asset_name, 'sha256': hashlib.sha256(data).hexdigest(),
        'installed': datetime.now().isoformat(timespec='seconds')}, indent=1), encoding='utf-8')
    return dest


def py_version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


# ------------------------------------------------------------------------------- icons ----------

def icon_jobs(data: dict, pak_names: set[str]) -> dict[str, tuple[str, int]]:
    """zip name -> (path in the .pak, max size), the same set olympian/data/icons.zip holds."""
    jobs = {}
    for it in data['items'].values():
        p = it.get('icon')
        if p:
            jobs[gamedata._icon_key(p)] = (p.replace('//', '/'), ICON_SIZE)
    for n in pak_names:
        base = n[len(SHIPTYPES):]
        if n.startswith(SHIPTYPES) and base.startswith('type') and base.endswith('.dds') and '/' not in base:
            jobs[f'shiptype~{base[:-4]}.webp'] = (n, SHIPTYPE_SIZE)
    return jobs


def to_webp(raw: bytes, size: int) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im = im.convert('RGBA')
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, 'WEBP', quality=WEBP_QUALITY)
    return buf.getvalue()


def build_icons(pcbanks: Path, data: dict, out_zip: Path, timings: dict) -> tuple[int, int]:
    """Returns (icons written, icons not found in the .pak files)."""
    from hgpaktool.api import HGPAKFile
    t0 = time.perf_counter()
    paks = sorted(pcbanks.glob('NMSARC.*.pak'))
    indexes = {}
    for pak in paks:
        with HGPAKFile(pak) as f:
            names = {n for n in f.filenames if n.startswith('textures/ui/')}
        if names:
            indexes[pak] = names
    all_names = set().union(*indexes.values()) if indexes else set()
    jobs = icon_jobs(data, all_names)
    wanted = {p for p, _ in jobs.values()}
    raw: dict[str, bytes] = {}
    for pak, names in indexes.items():
        here = sorted((wanted & names) - raw.keys())
        if here:
            with HGPAKFile(pak) as f:
                raw.update(f.extract(here))
    timings['icons_read'] = time.perf_counter() - t0

    t1 = time.perf_counter()
    todo = [(key, raw[p], size) for key, (p, size) in sorted(jobs.items()) if p in raw]
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
        webps = list(pool.map(lambda j: (j[0], to_webp(j[1], j[2])), todo))
    with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_STORED) as z:
        for key, b in webps:
            z.writestr(key, b)
    timings['icons_convert'] = time.perf_counter() - t1
    return len(webps), len(jobs) - len(todo)


# ------------------------------------------------------------------------------- compare --------

def comparable(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ('built', 'compiler')}


def data_difference(new: dict, old: dict | None) -> str | None:
    """None when equal, else a short description."""
    if old is None:
        return 'no data'
    if comparable(new) == comparable(old):
        return None
    parts = []
    for key in ('items', 'missions'):
        a, b = len(new.get(key) or {}), len(old.get(key) or {})
        if a != b:
            parts.append(f'{key} {b} -> {a}')
    changed = [k for k in comparable(new) if new.get(k) != old.get(k)]
    if changed:
        parts.append('changed: ' + ', '.join(changed))
    return '; '.join(parts)


# ------------------------------------------------------------------------------- main -----------

def main() -> int:
    ap = argparse.ArgumentParser(description='Check the game data tools and time a rebuild of Olympian\'s game data.')
    ap.add_argument('--pcbanks', help='game folder or its GAMEDATA\\PCBANKS folder')
    ap.add_argument('--mbinc', help='use this MBINCompiler instead of the one managed by Olympian')
    ap.add_argument('--check', action='store_true', help='only check versions, do not time the rebuild')
    ap.add_argument('--yes', action='store_true', help='answer yes to every download question')
    ap.add_argument('--offline', action='store_true', help='do not use the network (no update check, no downloads)')
    ap.add_argument('--no-icons', action='store_true', help='skip the icons')
    ap.add_argument('--keep', action='store_true', help='keep the generated files')
    ap.add_argument('--lang', choices=('es', 'en'))
    args = ap.parse_args()
    out = Out(args.lang or default_lang())

    out(f'{out.t("title")} (v{VERSION}) - {datetime.now():%Y-%m-%d %H:%M}')
    out(f'Olympian: {ROOT}')
    out(f'{platform.platform()} | CPU: {os.cpu_count()} | Python {platform.python_version()}')
    try:
        return run(args, out)
    finally:
        out()
        out.save()
        print(out.t('report', path=REPORT))
        print(out.t('paste'))


def run(args, out: Out) -> int:
    problems = 0
    pcbanks = find_pcbanks(args.pcbanks)
    if not pcbanks:
        out(out.t('no_game'))
        return 1
    out(f'{out.t("game_dir")}: {pcbanks}')

    shipped = gamedata.load()
    sig = pcbanks / 'BankSignatures.bin'
    current = hashlib.sha256(sig.read_bytes()).hexdigest()[:16] if sig.is_file() else None
    data_sig = (shipped or {}).get('game', {}).get('signature')
    fp_note = out.t('fp_unknown') if not (current and data_sig) else out.t('fp_same') if current == data_sig else out.t('fp_diff')
    out(f'{out.t("fingerprint")}: {current} | Olympian: {data_sig} -> {fp_note}')
    game_changed = bool(current and data_sig and current != data_sig)

    out()
    out(f'== {out.t("versions")} ==')
    online = not args.offline

    # .NET
    majors = dotnet_majors()
    out(f'.NET: {", ".join(str(m) for m in sorted(majors)) or out.t("missing")}')
    if not mbinc_asset(majors) and not args.mbinc:
        out('  ' + out.t('dotnet_none'))
        if WINDOWS and online and shutil.which('winget') and ask(out, out.t('ask_dotnet'), args.yes):
            r = subprocess.run(WINGET_DOTNET)
            out(f'  winget -> {r.returncode}')
            majors = dotnet_majors()
        if not mbinc_asset(majors):
            out('  ' + out.t('dotnet_manual', url=DOTNET_URL))
            problems += 1

    # MBINCompiler
    exe = Path(args.mbinc) if args.mbinc else managed_mbinc()
    have = mbinc_version(exe)
    release = get_json(f'https://api.github.com/repos/{MBINC_REPO}/releases/latest') if online else None
    latest = release.get('tag_name') if isinstance(release, dict) else None
    note = out.t('missing') if not have else out.t('offline') if not latest else \
        out.t('up_to_date') if vkey(have) >= vkey(latest) else out.t('outdated')
    out(f'MBINCompiler: {out.t("installed")} {have or "-"} | {out.t("latest")} {latest or "-"} -> {note}')
    asset = mbinc_asset(majors)
    if not args.mbinc and latest and asset and (not have or vkey(have) < vkey(latest)):
        size = next((a.get('size', 0) for a in release.get('assets', []) if a.get('name') == asset), 0)
        if ask(out, out.t('ask_mbinc', tag=latest, asset=asset, mb=size / 1e6), args.yes):
            if download_mbinc(out, release, asset):
                have = mbinc_version(exe)
                out(f'MBINCompiler: {out.t("installed")} {have or "-"}')
    if not have:
        problems += 1

    # Python packages
    pending = []
    for dist in PY_PACKAGES:
        mine = py_version(dist)
        info = get_json(f'https://pypi.org/pypi/{dist}/json') if online else None
        newest = info['info']['version'] if isinstance(info, dict) and 'info' in info else None
        note = out.t('missing') if not mine else out.t('offline') if not newest else \
            out.t('up_to_date') if vkey(mine) >= vkey(newest) else out.t('outdated')
        out(f'{dist}: {out.t("installed")} {mine or "-"} | {out.t("latest")} {newest or "-"} -> {note}')
        # a missing package is required; of the updates, only HGPAKtool's matters (new .pak formats)
        if online and (not mine or (dist == 'hgpaktool' and newest and vkey(mine) < vkey(newest))):
            pending.append(dist)
    if pending and ask(out, out.t('ask_pip', names=', '.join(pending)), args.yes):
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', *pending])
        out(f'  pip -> {r.returncode}')
    for dist in PY_PACKAGES:
        if not py_version(dist):
            problems += 1

    if args.check:
        return 1 if problems else 0
    if problems:
        out()
        out(out.t('cannot_run'))
        return 1

    # Timing
    work = Path(tempfile.mkdtemp(prefix='olympian_check_'))
    try:
        import build_gamedata
        out()
        out(f'== {out.t("data")} ==')
        t = {}
        start = time.perf_counter()
        try:
            data = build_gamedata.build(pcbanks, str(exe), work / 'gamedata.json', t)
        except SystemExit as e:
            out(out.t('mbinc_fail', err=str(e)[-400:]))
            return 1
        total_data = time.perf_counter() - start
        for step in ('extract', 'convert', 'parse'):
            out(f'  {out.t("step_" + step):32} {t.get(step, 0):7.1f} s')
        out(f'  {out.t("total"):32} {total_data:7.1f} s')
        diff = data_difference(data, shipped)
        out('  -> ' + (out.t('same_data') if diff is None else
                       out.t('diff_data', detail=diff) + (f' ({out.t("diff_expected")})' if game_changed else '')))
        if diff is not None and not game_changed:
            problems += 1

        total_icons = 0.0
        if not args.no_icons:
            out()
            out(f'== {out.t("icons")} ==')
            t = {}
            start = time.perf_counter()
            written, not_found = build_icons(pcbanks, data, work / 'icons.zip', t)
            total_icons = time.perf_counter() - start
            out(f'  {out.t("step_icons_read"):32} {t["icons_read"]:7.1f} s')
            out(f'  {out.t("step_icons_conv"):32} {t["icons_convert"]:7.1f} s  ({os.cpu_count()} threads)')
            out(f'  {out.t("total"):32} {total_icons:7.1f} s')
            if not_found:
                out('  ' + out.t('icons_missing_pak', n=not_found))
            try:
                old = set(zipfile.ZipFile(gamedata.ICONS).namelist())
            except (OSError, zipfile.BadZipFile):
                old = set()
            new = set(zipfile.ZipFile(work / 'icons.zip').namelist())
            out('  -> ' + (out.t('icons_same', n=written) if new == old else
                           out.t('icons_diff', n=written, miss=len(old - new), new=len(new - old))))

        out()
        out(f'{out.t("total")}: {total_data + total_icons:.1f} s')
    finally:
        if args.keep:
            out(out.t('kept', path=work))
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
