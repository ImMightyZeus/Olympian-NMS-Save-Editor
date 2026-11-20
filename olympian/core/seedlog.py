"""History of appearance seeds the player applied with Olympian, per save and ship.

Kept in the settings folder (outside the app folder). For each ship: the seed it had before the first
change ("original") and every seed applied after it, in order, so the player can go back or rotate.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

PATH: Path | None = None
MAX_PER_SHIP = 30


def _load() -> dict:
    try:
        d = json.loads(PATH.read_text(encoding='utf-8')) if PATH else {}
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _key(folder: str, slot: int, kind: str, index: int, model: str) -> str:
    return f'{folder}|{slot}|{kind}|{index}|{model}'


def record(folder: str, slot: int, ch: dict) -> None:
    if not PATH:
        return
    d = _load()
    k = _key(folder, slot, ch.get('kind', 'ship'), int(ch['index']), ch.get('model', ''))
    e = d.setdefault(k, {'name': ch.get('name', ''), 'seeds': []})
    seeds = e['seeds']
    if not seeds:
        seeds.append({'seed': ch['old'], 'original': True, 'time': time.time()})
    if not any(s['seed'] == ch['new'] for s in seeds):
        seeds.append({'seed': ch['new'], 'time': time.time()})
    if len(seeds) > MAX_PER_SHIP:                       # keep the original and the newest ones
        e['seeds'] = seeds[:1] + seeds[-(MAX_PER_SHIP - 1):]
    e['name'] = ch.get('name', '') or e.get('name', '')
    e['last'] = ch['new']
    tmp = PATH.with_name(PATH.name + '.tmp')
    tmp.write_text(json.dumps(d, indent=1), encoding='utf-8')
    os.replace(tmp, PATH)


def history(folder: str, slot: int, ships: list[dict]) -> dict:
    """{ship index: [seeds...]} for the ships of this save whose model still matches."""
    d = _load()
    out = {}
    for sh in ships:
        if not sh.get('model_path'):
            continue
        e = d.get(_key(folder, slot, 'ship', sh['index'], sh['model_path']))
        if e and len(e.get('seeds') or []) > 1:
            out[sh['index']] = e['seeds']
    return out
