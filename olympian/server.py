"""Local HTTP server for the Olympian NMS Save Editor interface (127.0.0.1 only, per-launch token)."""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import shutil
import urllib.parse
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import autosort, gamedata, hgfile, imported, inventories, jsonedit, jsonview, missions, ops, overview, safety, seedlog, settlements, ships
from .core.lock import Lock
from .core.coords import Address, galaxies, GLYPHS
from .core.savefile import SaveFolder, default_roots, files_of_slot, file_names
from .core.session import Session

VERSION = '0.10.0'
WEB = Path(__file__).resolve().parent / 'web'
PLAN_TTL = 15 * 60


def app_data_dir() -> Path:
    """Where Olympian keeps its own files: %APPDATA% on Windows, ~/Library/Application Support on macOS,
    $XDG_CONFIG_HOME (or ~/.config) on Linux."""
    if os.environ.get('APPDATA'):
        return Path(os.environ['APPDATA'])
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support'
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')


def settings_path() -> Path:
    base = app_data_dir()
    p = base / 'OlympianNMS'
    p.mkdir(parents=True, exist_ok=True)
    legacy = base / 'ZeusNMS' / 'settings.json'          # settings of versions up to 0.7.0
    if not (p / 'settings.json').exists() and legacy.is_file():
        shutil.copy2(legacy, p / 'settings.json')
    return p / 'settings.json'


def load_settings() -> dict:
    defaults = {'ui_lang': 'es', 'term_lang': 'es', 'folder': None}
    try:
        defaults.update(json.loads(settings_path().read_text(encoding='utf-8')))
    except Exception:  # noqa: BLE001
        pass
    return defaults


def save_settings(s: dict):
    settings_path().write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding='utf-8')


class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.settings = load_settings()
        self.plans: dict[str, ops.Plan] = {}
        self.session: Session | None = None
        self.last_ping = time.time()
        self.bye_at: float | None = None
        self.live = 0
        self.slot_cache: dict = {}
        self.stopping = False
        self.lock = threading.RLock()
        self.migrated: set[str] = set()
        self.workspace: str | None = None      # id of the imported save being viewed or edited
        self.applock = Lock(settings_path().with_name('lock.json'))
        self.unlocked = not self.applock.enabled
        self.recovered = False                 # unlocked with a recovery code (lock must be set up again)
        self.fails, self.wait_until = 0, 0.0
        self._set_extra()
        settlements.LOTS_PATH = settings_path().with_name('lots.json')
        seedlog.PATH = settings_path().with_name('seeds.json')

    def _set_extra(self):
        p = self.settings.get('backup_extra')
        safety.EXTRA = Path(p) if p and Path(p).is_dir() else None

    def _failed(self):
        self.fails += 1
        if self.fails >= 5:                    # slow down guessing: 30 s after every fifth miss
            self.wait_until = time.time() + 30
            self.fails = 0

    def _lock_state(self) -> dict:
        return {'enabled': self.applock.enabled, 'unlocked': self.unlocked, 'recovered': self.recovered,
                'recovery_left': self.applock.recovery_left(),
                'wait': max(0, int(self.wait_until - time.time()))}

    def _lock_api(self, route: str, body: dict):
        L = self.applock
        if route == '/api/lock/unlock':
            if time.time() < self.wait_until:
                raise PermissionError('lock_wait')
            try:
                L.unlock(str(body.get('pin') or ''), str(body.get('code') or ''))
            except ValueError:
                self._failed()
                raise
            self.unlocked, self.fails = True, 0
            return self._lock_state()
        if route == '/api/lock/recover':
            if time.time() < self.wait_until:
                raise PermissionError('lock_wait')
            try:
                L.recover(str(body.get('code') or ''))
            except ValueError:
                self._failed()
                raise
            self.unlocked, self.recovered, self.fails = True, True, 0
            return self._lock_state()
        if not self.unlocked:
            raise PermissionError('locked')
        if route == '/api/lock/begin':
            if L.enabled and not self.recovered:
                raise PermissionError('lock_already')
            return L.begin(str(body.get('pin') or ''), 'NMS')
        if route == '/api/lock/confirm':
            if L.enabled and self.recovered:   # replaced: the old configuration goes
                L.disable(recovered=True)
            codes = L.confirm(str(body.get('code') or ''))
            self.recovered = False
            return {'recovery': codes, **self._lock_state()}
        if route == '/api/lock/disable':
            L.disable(str(body.get('pin') or ''), str(body.get('code') or ''), recovered=self.recovered)
            self.recovered = False
            return self._lock_state()
        raise LookupError(route)

    # -- folder ------------------------------------------------------------------------------
    def folder(self) -> SaveFolder:
        if self.workspace:
            return SaveFolder(imported.folder_of(settings_path().parent, self.workspace))
        p = self.settings.get('folder')
        if not p:
            roots = default_roots()
            if not roots:
                raise FileNotFoundError('no_folder')
            p = str(roots[0])
            self.settings['folder'] = p
        path = Path(p)
        if not path.is_dir():
            raise FileNotFoundError('no_folder')
        if str(path) not in self.migrated:
            self.migrated.add(str(path))
            try:
                for line in safety.migrate_legacy(path):
                    print('migrated:', line, flush=True)
            except OSError as e:                # a backup folder in use: try again next time
                self.migrated.discard(str(path))
                print('migration postponed:', e, flush=True)
        return SaveFolder(path)

    def text_lang(self) -> str:
        """Language of the game's sentences (advice, objectives): the interface language, keeping the
        Spanish variant chosen for game terms. Names keep following term_lang."""
        term = self.settings.get('term_lang') or 'es'
        if self.settings.get('ui_lang') in ('en', 'ru'):
            return self.settings['ui_lang']
        return term if term.startswith('es') else 'es'

    def _session(self) -> Session:
        if self.session is None:
            raise LookupError('no_session')
        return self.session

    def _points(self) -> list[dict]:
        f = self.folder()
        pts = safety.list_points(f.path)
        for p in pts:
            p.update(ops.point_details(f.path, p))
        return pts

    # -- API ---------------------------------------------------------------------------------
    def api(self, method: str, route: str, body: dict):
        with self.lock:
            return self._api(method, route, body)

    def _api(self, method: str, route: str, body: dict):
        if route.startswith('/api/lock/') and method == 'POST':
            return self._lock_api(route, body)
        if not self.unlocked and route not in ('/api/ping', '/api/bye', '/api/state'):
            raise PermissionError('locked')
        if route == '/api/ping':
            self.last_ping = time.time()
            self.bye_at = None
            return {'ok': True, 'dirty': bool(self.session and self.session.dirty)}
        if route == '/api/bye':
            self.bye_at = time.time()
            return {'ok': True}
        if route == '/api/state':
            folder = None
            try:
                folder = str(self.folder().path)
            except FileNotFoundError:
                pass
            return {'version': VERSION, 'folder': folder, 'roots': [str(r) for r in default_roots()],
                    'game_running': safety.game_running(), 'lz4': hgfile.HAVE_LZ4,
                    'session': self.session.slot if self.session else None, 'lock': self._lock_state(),
                    'settings': {k: self.settings.get(k) for k in ('ui_lang', 'term_lang', 'lang_chosen', 'backup_extra',
                                                                'allow_unprotected', 'lock_offered', 'shield_sort_seen')}}
        if route == '/api/settings' and method == 'POST':
            if body.get('ui_lang') in ('es', 'en', 'ru'):
                self.settings['ui_lang'] = body['ui_lang']
            if body.get('term_lang') in gamedata.LANGS:
                self.settings['term_lang'] = body['term_lang']
            if body.get('lang_chosen'):
                self.settings['lang_chosen'] = True
            for key in ('allow_unprotected', 'lock_offered', 'shield_sort_seen'):
                if key in body:
                    self.settings[key] = bool(body.get(key))
            if 'backup_extra' in body:
                v = str(body.get('backup_extra') or '').strip()
                if v:
                    try:
                        folder = self.folder().path
                    except FileNotFoundError:
                        folder = None
                    v = str(safety.check_extra(v, folder))
                self.settings['backup_extra'] = v or None
                self._set_extra()
            save_settings(self.settings)
            return {'ok': True}
        if route == '/api/folder' and method == 'POST':
            p = Path(str(body.get('path', '')))
            if not p.is_dir() or not SaveFolder(p).file_indices():
                raise ValueError('folder_invalid')
            if self.session and self.session.dirty:
                raise PermissionError('session_dirty')
            self.session = None
            self.settings['folder'] = str(p)
            save_settings(self.settings)
            return {'ok': True}
        if route == '/api/gamedata':
            lang = self.settings.get('term_lang') or 'es'
            if lang not in gamedata.LANGS:
                lang = 'es'
            alt = 'es' if lang == 'en' else 'en'               # search also finds items in a second language
            return {'status': gamedata.status(self.settings.get('game_dir')), 'lang': lang,
                    'names': gamedata.names(lang), 'names_alt': gamedata.names(alt), 'tips': gamedata.tips(lang),
                    'stats': gamedata.stat_labels(lang)}
        if route == '/api/galaxies':
            return {'galaxies': galaxies(), 'glyphs': GLYPHS}
        if route == '/api/slots':
            f = self.folder()
            out = []
            for slot in sorted(f.slots()):
                try:
                    stamp = tuple((n, (f.path / n).stat().st_mtime_ns) for fi in files_of_slot(slot)
                                  for n in file_names(fi) if (f.path / n).is_file())
                    key = (str(f.path), slot)
                    hit = self.slot_cache.get(key)
                    if not hit or hit[0] != stamp:
                        hit = (stamp, ops.describe_slot(f, slot))
                        self.slot_cache[key] = hit
                    out.append(hit[1])
                except Exception as e:  # noqa: BLE001
                    out.append({'slot': slot, 'error': str(e)})
            hist = safety.read_history(f.path)
            for d in out:
                d['history'] = [h for h in hist if h.get('slot') == d.get('slot')][-8:][::-1]
            return {'slots': out}
        if route == '/api/convert' and method == 'POST':
            gal = int(body.get('galaxy', 0))
            if body.get('glyphs'):
                a = Address.from_glyphs(body['glyphs'], gal)
            elif body.get('galactic'):
                a = Address.from_galactic(body['galactic'], gal, int(body.get('planet', 0)))
            else:
                a = Address(gal, int(body['x']), int(body['y']), int(body['z']), int(body['system']), int(body.get('planet', 0)))
            return {'address': a.__dict__, 'glyphs': a.to_glyphs(), 'galactic': a.to_galactic(), 'errors': a.validate()}

        # -- editing session ----------------------------------------------------------------
        if route == '/api/session/open' and method == 'POST':
            slot = int(body['slot'])
            if self.workspace:                                 # back to the player's own saves
                if self.session and self.session.dirty:
                    raise PermissionError('session_dirty')
                self.workspace, self.session = None, None
            if self.session and self.session.dirty and self.session.slot != slot:
                raise PermissionError('session_dirty')
            if not (self.session and self.session.slot == slot):
                self.session = Session(self.folder(), slot)
            return self._describe()
        if route == '/api/session':
            self._session()
            return self._describe()
        if route.startswith('/api/session/json') and method == 'POST':      # phase 3: read-only JSON viewer
            views = self._session().views()
            fi = int(body.get('file') or 0)
            if not 0 <= fi < len(views):
                raise LookupError('file')
            j, lang = views[fi].json, self.settings.get('term_lang') or 'es'
            if route == '/api/session/json':
                d = jsonview.node(j, list(body.get('path') or []), int(body.get('offset') or 0), lang=lang)
                d['files'] = [file_names(v.file_index)[0] for v in views]
                return d
            if route == '/api/session/json/search':
                return jsonview.search(j, str(body.get('q') or ''), lang)
            if route == '/api/session/json/export':
                return {'text': jsonview.export(j, list(body.get('path') or []), bool(body.get('names')))}
        if route == '/api/session/missions/steps' and method == 'POST':
            return missions.steps(str(body.get('id') or ''), int(body.get('progress', -1)), self.text_lang())
        if route == '/api/session/missions':                                   # phase 4
            return missions.describe(self._session().views(), self.settings.get('term_lang') or 'es')
        if route == '/api/session/seeds':
            sess = self._session()
            v = sess.views()[0]
            k = v.keys
            lst = [{'index': i, 'model_path': sh[k.s('Resource')][k.s('Filename')]}
                   for i, sh in enumerate(v.psd()[k.s('ShipOwnership')])]
            return {'history': seedlog.history(self.folder().path.name, sess.slot, lst)}
        if route == '/api/session/overview':
            return overview.describe(self._session().views(), self.settings.get('term_lang') or 'es')
        if route == '/api/session/settlements':                                # phase 5
            sess = self._session()
            base = self.folder().path
            ex = safety.extra_root(base)
            settlements.learn_history([base / safety.BACKUP_DIR, base / safety.POINTS_DIR] +
                                      ([ex / safety.BACKUP_DIR, ex / safety.POINTS_DIR] if ex else []))
            return settlements.describe(sess.views(), self.settings.get('term_lang') or 'es', self.text_lang())
        if route == '/api/session/autosort' and method == 'POST':
            v = self._session().views()[0]
            grid, valid, bsv, cid = autosort.from_save(v, int(body.get('ship', -1)))
            r = autosort.plan(grid, valid, [str(p) for p in body.get('priorities') or []], bsv)
            r['inv'] = cid
            return r
        if route == '/api/session/addable' and method == 'POST':
            return inventories.addable(self._session().views(), str(body.get('inv') or ''))
        if route == '/api/session/inventories':
            d = inventories.describe(self._session().views(), self.settings.get('term_lang') or 'es')
            d['sizes'] = gamedata.stack_sizes(d['level']) if d['level'] else None
            return d
        if route == '/api/session/discard' and method == 'POST':
            self._session().reload()
            return self._describe()
        if route in ('/api/session/undo', '/api/session/redo') and method == 'POST':
            sess = self._session()
            (sess.undo if route.endswith('undo') else sess.redo)()
            return self._describe()
        if route == '/api/session/close' and method == 'POST':
            if self.session and self.session.dirty:
                raise PermissionError('session_dirty')
            self.session = None
            self.workspace = None
            return {'ok': True}

        # -- imported saves (other players'), kept apart from the player's own ---------------
        if route.startswith('/api/imported'):
            return self._imported(route, body)
        if route == '/api/session/save_plan' and method == 'POST':
            plan = self._session().save_plan()
            self._keep(plan)
            return plan.public()

        if route == '/api/plan' and method == 'POST':
            return self.plan(body)
        if route == '/api/apply' and method == 'POST':
            plan = self.plans.pop(str(body.get('id')), None)
            if plan is None or time.time() - plan.created > PLAN_TTL:
                raise ValueError('plan_expired')
            if plan.needs_code and not self.applock.code_ok(str(body.get('code') or '')):
                self.plans[plan.id] = plan           # the user can type the code again
                raise ValueError('lock_bad_code')
            if plan.delete_files and self.session and self.session.slot == plan.dest_slot:
                if self.session.dirty:
                    raise PermissionError('session_dirty')
                self.session = None
            if plan.staged:
                if not self.session or plan.changes and plan.changes[0].target_index not in self.session.work:
                    raise ValueError('plan_expired')
                return self.session.stage(plan)
            if plan.action == 'save':
                return self._session().save()
            report = ops.apply(plan)
            if plan.dest_slot and plan.summary:
                try:
                    if report.get('backup'):
                        safety.annotate(report['backup'], slot=plan.dest_slot, changes=[plan.summary])
                    safety.log_history(self.folder().path, {'slot': plan.dest_slot, 'time': time.time(),
                                                            'changes': [plan.summary], 'backup': report.get('backup')})
                except (OSError, ValueError):
                    pass
            # a disk operation into the open slot: refresh the working copy (it was clean, see plan())
            if self.session and any(c.target_index in self.session.work for c in plan.changes):
                self.session.reload()
            return report
        if route == '/api/points':
            return {'points': self._points()}
        if route == '/api/backups':
            f = self.folder()
            return {'usage': safety.usage(f.path), 'purge': safety.purge_plan(f.path), 'keep': safety.KEEP_BACKUPS,
                    'needs_code': self.applock.enabled}
        if route == '/api/backups/purge' and method == 'POST':
            f = self.folder()
            if self.applock.enabled and not self.applock.code_ok(str(body.get('code') or '')):
                raise ValueError('lock_bad_code')
            if safety.game_running():
                raise PermissionError('game_running')
            done = safety.purge(f.path)
            return {'done': done, 'usage': safety.usage(f.path)}
        if route == '/api/points/create' and method == 'POST':
            f = self.folder()
            slot = int(body['slot'])
            names = [n for fi in files_of_slot(slot) for n in file_names(fi) if (f.path / n).is_file()]
            if not names:
                raise FileNotFoundError('slot_empty')
            d = safety.create_point(f.path, names, str(body.get('label') or f'slot{slot}'), slot)
            return {'ok': True, 'path': str(d)}
        raise LookupError(route)

    def _describe(self) -> dict:
        d = self.session.describe()
        d['changelog'] = ops.slot_changelog(self.folder().path, self.session.slot)
        d['imported'] = imported.info(settings_path().parent, self.workspace) if self.workspace else None
        return d

    def _imported(self, route: str, body: dict):
        base = settings_path().parent
        if route == '/api/imported':
            return {'items': imported.listing(base), 'xbox_export': False}
        if route == '/api/imported/scan':
            return imported.scan(str(body.get('path') or ''))
        if route == '/api/imported/add':
            return imported.do_import(base, str(body.get('path') or ''), int(body.get('slot') or 1),
                                      str(body.get('label') or ''), bool(body.get('readonly')))
        rid = str(body.get('id') or '')
        if route == '/api/imported/open':
            if self.session and self.session.dirty:
                raise PermissionError('session_dirty')
            i = imported.info(base, rid)
            self.workspace = rid
            self.session = Session(self.folder(), 1, imported=True, readonly=bool(i.get('readonly')))
            return self._describe()
        if route == '/api/imported/readonly':
            if self.workspace == rid and self.session and self.session.dirty:
                raise PermissionError('session_dirty')
            i = imported.set_readonly(base, rid, bool(body.get('readonly')))
            if self.workspace == rid and self.session:
                self.session.readonly = bool(i['readonly'])
            return i
        if route == '/api/imported/remove':
            if self.workspace == rid:
                raise PermissionError('import_open')
            imported.remove(base, rid)
            return {'ok': True}
        if route == '/api/imported/export':
            if self.workspace == rid and self.session and self.session.dirty:
                raise PermissionError('session_dirty')
            if (body.get('format') or 'steam') == 'xbox':       # back into the Game Pass folder it came from
                if body.get('mode') == 'apply':
                    return imported.export_xbox(base, rid, safety.game_running())
                return imported.xbox_preview(base, rid, safety.game_running())
            own = self.settings.get('folder') or (str(default_roots()[0]) if default_roots() else None)
            return imported.export_steam(base, rid, str(body.get('dest') or ''), int(body.get('slot') or 1), own,
                                         self.settings.get('ui_lang') or 'es')
        raise LookupError(route)

    def _keep(self, plan: ops.Plan):
        now = time.time()
        for k in [k for k, v in self.plans.items() if now - v.created > PLAN_TTL]:
            del self.plans[k]
        self.plans[plan.id] = plan

    def plan(self, body: dict) -> dict:
        f = self.folder()
        a = body.get('action')
        p = body.get('params', {})
        if self.workspace and a in ('copy_slot', 'restore_to', 'delete_slot', 'ship_transfer'):
            raise PermissionError('import_own_only')      # never mix another player's save with the player's own
        if self.workspace and self.session and self.session.readonly:
            raise PermissionError('import_readonly')
        if a in ('set_primary', 'travel', 'rename', 'reorder', 'ship', 'inv', 'mis', 'settle', 'json', 'revert'):   # staged
            s = self._session()
            views = s.views()
            if a == 'set_primary':
                plan = ops.plan_set_primary(f, s.slot, int(p['ship']), files=views)
            elif a == 'travel':
                addr = Address(int(p['galaxy']), int(p['x']), int(p['y']), int(p['z']), int(p['system']),
                               int(p.get('planet', 0)))
                plan = ops.plan_travel(f, s.slot, addr, p.get('spawn', 'ship'), files=views)
            elif a == 'rename':
                plan = ops.plan_rename(f, s.slot, str(p.get('name', '')), files=views)
            elif a == 'revert':
                plan = ops.plan_revert(f, s.slot, str(p['point']), views)
            elif a == 'ship':
                plan = ships.plan_ship(f, s.slot, int(p['ship']), str(p['op']), p, files=views)
            elif a == 'mis':
                p = {**p, 'lang': self.settings.get('term_lang') or 'es'}
                plan = missions.plan_missions(f, s.slot, str(p['op']), p, files=views)
            elif a == 'settle':
                p = {**p, 'lang': self.settings.get('term_lang') or 'es'}
                plan = settlements.plan_settlement(f, s.slot, str(p['op']), p, files=views)
            elif a == 'json':
                plan = jsonedit.plan_json_edit(f, s.slot, p, files=views)
            elif a == 'inv':
                p = {**p, 'lang': self.settings.get('term_lang') or 'es'}     # names are sorted in the content language
                plan = inventories.plan_inventory(f, s.slot, str(p['op']), p, files=views)
            else:
                plan = ops.plan_reorder(f, s.slot, [int(x) for x in p['order']], files=views)
        elif a == 'copy_slot':
            plan = ops.plan_copy_slot(f, int(p['src_file']), int(p['dest_slot']), str(p.get('name', '')))
        elif a == 'restore_to':
            plan = ops.plan_restore_to(f, str(p['point']), int(p['dest_slot']))
        elif a == 'delete_slot':
            plan = ops.plan_delete_slot(f, int(p['slot']))
        elif a == 'ship_transfer':
            s = self._session()
            plan = ships.plan_ship_transfer(f, s.slot, int(p['ship']), [int(x) for x in p.get('dest_slots') or []],
                                            bool(p.get('move')))
            if s.dirty:
                plan.messages.append(ops.msg('block', 'ship_transfer_save_first'))
        else:
            raise LookupError(a)
        # overwriting or deleting a whole slot: only with protection on, or with "I live alone" chosen in Settings
        overwrites = a == 'delete_slot' or (a == 'copy_slot' and any(
            f.file(fi).exists() for fi in ops.files_of_slot(int(p['dest_slot'])) if 1 <= int(p['dest_slot']) <= 15))
        if overwrites and not (self.applock.enabled or self.settings.get('allow_unprotected')):
            plan.messages.append(ops.msg('block', 'needs_protection'))
        if self.applock.enabled and (overwrites or a == 'restore_to' or (a == 'ship' and p.get('op') == 'delete')
                                     or (a == 'ship_transfer' and p.get('move'))):
            plan.needs_code = True
        if not plan.staged and self.session and self.session.dirty \
                and any(c.target_index in self.session.work for c in plan.changes):
            plan.messages.append(ops.msg('block', 'dest_open_unsaved', slot=self.session.slot))
        self._keep(plan)
        return plan.public()


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Olympian'

        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, payload: bytes, ctype: str):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, code: int, obj):
            # ensure_ascii: some item IDs in saves are binary (kept as surrogate escapes); \u escapes keep them safe
            self._send(code, json.dumps(obj, ensure_ascii=True).encode('ascii'), 'application/json; charset=utf-8')

        def _handle(self, method: str):
            path, _, query = self.path.partition('?')
            if path == '/api/live' and method == 'GET':
                return self._live(query)
            if (path.startswith('/icons/') or path.startswith('/shipicon/') or path.startswith('/uiicon/')) and method == 'GET':
                data = gamedata.icon(urllib.parse.unquote(path[7:])) if path.startswith('/icons/') \
                    else gamedata.ui_icon(path[8:]) if path.startswith('/uiicon/') else gamedata.ship_icon(path[10:])
                if not data:
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'image/webp')
                self.send_header('Cache-Control', 'max-age=86400')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path.startswith('/api/'):
                qtok = query[2:] if query.startswith('t=') else None
                if self.headers.get('X-Olympian-Token') != app.token and not (path == '/api/bye' and qtok == app.token):
                    return self._json(403, {'error': 'forbidden'})
                body = {}
                if method == 'POST':
                    n = int(self.headers.get('Content-Length') or 0)
                    if n > 1_000_000:
                        return self._json(413, {'error': 'too_large'})
                    body = json.loads(self.rfile.read(n) or b'{}')
                try:
                    return self._json(200, app.api(method, path, body))
                except PermissionError as e:
                    return self._json(409, {'error': str(e) or 'blocked'})
                except RuntimeError as e:  # changed_on_disk / verify_failed: nothing was written
                    return self._json(409, {'error': str(e)})
                except (FileNotFoundError, ValueError, KeyError, LookupError) as e:
                    return self._json(400, {'error': str(e).strip("'")})
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    return self._json(500, {'error': f'{type(e).__name__}: {e}'})
            if method != 'GET':
                return self._json(405, {'error': 'method'})
            rel = 'index.html' if path in ('/', '') else path.lstrip('/')
            target = (WEB / rel).resolve()
            if WEB not in target.parents and target != WEB / 'index.html' or not target.is_file():
                return self._send(404, b'not found', 'text/plain')
            ctype = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json'):
                ctype += '; charset=utf-8'
            self._send(200, target.read_bytes(), ctype)

        def _live(self, query: str):
            """Open while the window is open. When the window closes the connection drops and we notice
            on the next heartbeat - no dependence on browser timers (which slow down when minimized)."""
            if not secrets.compare_digest(query[2:] if query.startswith('t=') else '', app.token):
                return self._json(403, {'error': 'forbidden'})
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            with app.lock:
                app.live += 1
                app.bye_at = None
            try:
                while not app.stopping:
                    self.wfile.write(b': hb\n\n')
                    self.wfile.flush()
                    app.last_ping = time.time()
                    time.sleep(1.5)
            except OSError:
                pass
            finally:
                with app.lock:
                    app.live -= 1
                    if app.live <= 0:
                        app.bye_at = time.time()

        def do_GET(self):
            self._handle('GET')

        def do_POST(self):
            self._handle('POST')

    return Handler


def serve(port: int = 0) -> tuple[ThreadingHTTPServer, App]:
    app = App()
    srv = ThreadingHTTPServer(('127.0.0.1', port), make_handler(app))
    srv.daemon_threads = True
    return srv, app
