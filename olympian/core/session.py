"""Editing session: the slot the user chose, with a working copy in memory.

Edits (travel, primary ship, reorder, rename) change only the working copy. Nothing touches the
disk until save(), which goes through the same safe path as before: game check, mtime guard,
automatic backup, build + verify, atomic write, re-read.
"""
from __future__ import annotations

import copy
import threading
import time

from . import jspan, ops, safety
from .savefile import SaveFile, SaveFolder, file_names


class Session:
    def __init__(self, folder: SaveFolder, slot: int, *, imported: bool = False, readonly: bool = False):
        self.folder = folder
        self.slot = slot
        self.imported = imported                # another player's save in the imported space (not the game's)
        self.readonly = readonly
        self.reload()

    def reload(self):
        self.orig: list[SaveFile] = ops.load_slot(self.folder, self.slot)
        self.work = {f.file_index: f.raw for f in self.orig}
        self.names: dict[int, str | None] = {f.file_index: None for f in self.orig}
        self.parsed: dict[int, object] = {}     # parsed JSON matching self.work, when an action supplied it
        self.changes: list[dict] = []
        self._undo: list[dict] = []            # state before each staged change (same order as self.changes)
        self._redo: list[tuple[dict, dict]] = []   # (state after, change) of undone changes
        self._views: list[SaveFile] | None = None
        threading.Thread(target=self._warm, daemon=True).start()

    def _warm(self):
        """Pre-build the key index in the background so the first edit is instant."""
        try:
            for f in self.orig:
                k = f.keys
                jspan.index_for(f.raw).get(k.psd + [k('PrimaryShip')])
        except Exception:  # noqa: BLE001 - best effort only
            pass

    # -- views -------------------------------------------------------------------------------
    def views(self) -> list[SaveFile]:
        """Working-copy files; cached (parsed JSON included) until the next staged change."""
        if self._views is not None:
            return self._views
        out = []
        for f in self.orig:
            v = copy.copy(f)
            v.raw = self.work[f.file_index]
            p = self.parsed.get(f.file_index)
            v._json = f._json if v.raw is f.raw else (p[1] if p and p[0] is v.raw else None)
            v._keys = (None, None)
            out.append(v)
        self._views = out
        return out

    @property
    def dirty(self) -> bool:
        return bool(self.changes) or any(self.work[f.file_index] != f.raw for f in self.orig) \
            or any(self.names.values())

    def describe(self) -> dict:
        d = ops.describe_slot(self.folder, self.slot, self.views(), self.names)
        d.update({'dirty': self.dirty, 'changes': self.changes,
                  'can_undo': bool(self._undo), 'can_redo': bool(self._redo)})
        return d

    # -- undo / redo, one staged change at a time ------------------------------------------------
    def _state(self) -> dict:
        return {'work': dict(self.work), 'names': dict(self.names), 'parsed': dict(self.parsed)}

    def _restore(self, st: dict):
        self.work, self.names, self.parsed = dict(st['work']), dict(st['names']), dict(st['parsed'])
        self._views = None

    def undo(self) -> bool:
        if not self._undo or not self.changes:
            return False
        self._redo.append((self._state(), self.changes.pop()))
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        after, change = self._redo.pop()
        self._undo.append(self._state())
        self._restore(after)
        self.changes.append(change)
        return True

    # -- staging -----------------------------------------------------------------------------
    def stage(self, plan: ops.Plan) -> dict:
        if self.readonly:
            raise PermissionError('import_readonly')
        if plan.blocked:
            raise PermissionError('blocked')
        for c in plan.changes:
            if c.target_index not in self.work:
                raise ValueError('not_in_session')
        self._undo.append(self._state())
        self._redo.clear()
        for c in plan.changes:
            self.work[c.target_index] = c.new_raw
            pj = getattr(c, 'json', None)
            if pj is not None:
                self.parsed[c.target_index] = (c.new_raw, pj)
            else:
                self.parsed.pop(c.target_index, None)
            if c.name:
                self.names[c.target_index] = c.name
        self.changes.append({'action': plan.action, 'summary': plan.summary, 'time': time.time(), 'meta': plan.meta})
        self._views = None
        return {'staged': True, 'pending': len(self.changes)}

    # -- saving ------------------------------------------------------------------------------
    def save_plan(self) -> ops.Plan:
        plan = ops.Plan('save', self.folder.path)
        ops._game_check(plan)
        for f in self.orig:
            raw, name = self.work[f.file_index], self.names[f.file_index]
            if raw != f.raw or name:
                plan.changes.append(ops.FileChange(f.file_index, f, raw, name=name))
        if not plan.changes:
            plan.messages.append(ops.msg('block', 'nothing_to_save'))
        else:
            plan.messages.append(ops.msg('info', 'save_what', n=len(self.changes),
                                         files=', '.join(file_names(c.target_index)[0] for c in plan.changes)))
            plan.messages.append(ops.msg('ok', 'save_backup'))
        return plan

    def save(self) -> dict:
        plan = self.save_plan()
        changes = [c['summary'] for c in self.changes if c.get('summary')]
        report = ops.apply(plan)
        try:
            if report.get('backup'):
                safety.annotate(report['backup'], slot=self.slot, changes=changes)
            safety.log_history(self.folder.path, {'slot': self.slot, 'time': time.time(), 'changes': changes,
                                                  'backup': report.get('backup')})
        except (OSError, ValueError):
            pass                               # the history is a convenience; the save itself succeeded
        try:
            from . import seedlog
            for c in self.changes:
                if (c.get('meta') or {}).get('seed'):
                    seedlog.record(self.folder.path.name, self.slot, c['meta']['seed'])
        except (OSError, ValueError):
            pass
        self.reload()
        return report
