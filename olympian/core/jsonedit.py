"""JSON viewer expert mode: change one value of the save by hand.

Deliberately narrow, so a typo cannot break the file's structure:
  * only text, number and true/false values (no objects or lists, no adding or removing keys);
  * the new value keeps the old one's type (a whole number stays a whole number, a text stays a text);
  * texts that are binary IDs (stored modules) are not editable here;
  * one file of the slot at a time - the one shown in the viewer - so the user knows exactly what changes.
Like every other change it is staged in the working copy, verified byte by byte (nothing outside that value
changes, the value reads back exactly) and only written on Save, with a backup.
"""
from __future__ import annotations

from . import gamedata, jsonview
from .ops import FileChange, Plan, _common_checks, load_slot, msg
from .pathedit import PathEdit
from .savefile import SaveFile, SaveFolder, file_names

TEXT_MAX = 500
INT_MIN, INT_MAX = -(2 ** 63), 2 ** 64 - 1


def _path(raw) -> list | None:
    out = []
    for p in raw if isinstance(raw, list) else []:
        if isinstance(p, bool) or not isinstance(p, (str, int)):
            return None
        out.append(p)
    return out or None


def _coerce(old, new):
    """New value with the old value's type, or a reason code."""
    if isinstance(old, bool):
        return (new, None) if isinstance(new, bool) else (None, 'json_type_bool')
    if isinstance(old, int):
        if isinstance(new, bool) or not isinstance(new, (int, float)) or (isinstance(new, float) and not new.is_integer()):
            return None, 'json_type_int'
        new = int(new)
        return (new, None) if INT_MIN <= new <= INT_MAX else (None, 'json_type_int')
    if isinstance(old, float):
        if isinstance(new, bool) or not isinstance(new, (int, float)) or new != new or new in (float('inf'), float('-inf')):
            return None, 'json_type_float'
        return float(new), None
    if isinstance(old, str):
        if not isinstance(new, str) or len(new) > TEXT_MAX or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in new):
            return None, 'json_type_text'
        return new, None
    return None, 'json_not_scalar'


def plan_json_edit(folder: SaveFolder, slot: int, params: dict, files: list[SaveFile] | None = None) -> Plan:
    plan = Plan('json_edit', folder.path, staged=files is not None)
    files = files if files is not None else load_slot(folder, slot)
    _common_checks(plan, files, check_game=not plan.staged)
    try:
        fi = int(params.get('file') or 0)
    except (TypeError, ValueError):
        fi = -1
    path = _path(params.get('path'))
    if not 0 <= fi < len(files) or path is None:
        plan.messages.append(msg('block', 'json_bad_path'))
        return plan
    f = files[fi]
    try:
        old = jsonview.resolve(f.json, path)
    except KeyError:
        plan.messages.append(msg('block', 'json_bad_path'))
        return plan
    if isinstance(old, (dict, list)) or old is None:
        plan.messages.append(msg('block', 'json_not_scalar'))
        return plan
    if isinstance(old, str) and gamedata.is_binary(old):
        plan.messages.append(msg('block', 'json_binary'))
        return plan
    new, why = _coerce(old, params.get('value'))
    if why:
        plan.messages.append(msg('block', why, max=TEXT_MAX))
        return plan
    if new == old and type(new) is type(old):
        plan.messages.append(msg('block', 'json_same'))
        return plan
    pe = PathEdit(f)
    pe.set(path, new)
    fc = FileChange(f.file_index, f, pe.result())
    fc.json = pe.patched_json()
    plan.changes.append(fc)
    fname = file_names(f.file_index)[0]
    where = jsonview.rpath(path)
    shown = lambda v: jsonview.show_str(v) if isinstance(v, str) else v
    plan.messages.append(msg('warn', 'json_edit_what', path=where, old=shown(old), new=shown(new), file=fname))
    if len(files) > 1:
        plan.messages.append(msg('info', 'json_one_file', file=fname))
    for n in jsonview.notes(f.json, path):
        if n.get('level') in ('warn', 'danger'):          # the viewer's own risk notes for this place
            plan.messages.append(msg('warn', 'json_note', note=n['code']))
    plan.messages.append(msg('warn', 'json_expert_risk'))
    plan.summary = msg('info', 'chg_json_edit', path=where, old=shown(old), new=shown(new), file=fname)
    return plan
