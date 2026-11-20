"""Optional app lock: PIN + time-based code (TOTP, RFC 6238) from an authenticator app.

Works offline: the authenticator and Olympian share a secret once (QR code or typed key) and both
compute the same 6-digit code from the clock. Stored in the settings folder, outside the app folder:
  * the PIN only as a PBKDF2-SHA256 verifier (with salt);
  * the TOTP secret encrypted with a key derived from the PIN (HMAC-SHA256 keystream + HMAC tag);
  * one-time recovery codes as salted SHA-256 hashes.
It stops other people from using Olympian on this PC; it does not protect the save files themselves
(they can still be changed from the file explorer), which the FAQ says plainly.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from pathlib import Path

ITER = 200_000
PERIOD, DIGITS, WINDOW = 30, 6, 1          # 30-second codes, one step of clock drift either way
N_RECOVERY = 8
ISSUER = 'Olympian NMS Save Editor'


def _b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode().rstrip('=')


def _unb32(s: str) -> bytes:
    s = s.strip().replace(' ', '').upper()
    return base64.b32decode(s + '=' * (-len(s) % 8))


def totp(secret: bytes, t: float | None = None, step: int = 0) -> str:
    c = int((time.time() if t is None else t) // PERIOD) + step
    h = hmac.new(secret, struct.pack('>Q', c), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    n = (struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(n).zfill(DIGITS)


def check_totp(secret: bytes, code: str, t: float | None = None) -> bool:
    code = ''.join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS:
        return False
    return any(hmac.compare_digest(totp(secret, t, s), code) for s in range(-WINDOW, WINDOW + 1))


def _derive(pin: str, salt: bytes, it: int) -> tuple[bytes, bytes]:
    k = hashlib.pbkdf2_hmac('sha256', pin.encode('utf-8'), salt, it, 64)
    return k[:32], k[32:]                    # (verifier, encryption key)


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out, i = b'', 0
    while len(out) < n:
        out += hmac.new(key, nonce + struct.pack('>I', i), hashlib.sha256).digest()
        i += 1
    return out[:n]


def _seal(key: bytes, data: bytes) -> dict:
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(key, nonce, len(data))))
    tag = hmac.new(key, b'tag' + nonce + ct, hashlib.sha256).digest()
    return {'nonce': nonce.hex(), 'ct': ct.hex(), 'tag': tag.hex()}


def _open(key: bytes, box: dict) -> bytes:
    nonce, ct = bytes.fromhex(box['nonce']), bytes.fromhex(box['ct'])
    if not hmac.compare_digest(hmac.new(key, b'tag' + nonce + ct, hashlib.sha256).digest(), bytes.fromhex(box['tag'])):
        raise ValueError('lock_bad_pin')
    return bytes(a ^ b for a, b in zip(ct, _stream(key, nonce, len(ct))))


def valid_pin(pin: str) -> bool:
    return isinstance(pin, str) and pin.isdigit() and 4 <= len(pin) <= 12


class Lock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.cfg: dict | None = None
        try:
            self.cfg = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.cfg = None
        self._secret: bytes | None = None     # kept in memory only while the app is unlocked
        self.pending: dict | None = None      # setup in progress (not saved until the first code checks)

    @property
    def enabled(self) -> bool:
        return bool(self.cfg and self.cfg.get('v') == 1)

    # -- setup ------------------------------------------------------------------------------------
    def begin(self, pin: str, label: str = 'NMS') -> dict:
        if not valid_pin(pin):
            raise ValueError('lock_pin_format')
        secret = secrets.token_bytes(20)
        self.pending = {'pin': pin, 'secret': secret}
        key = _b32(secret)
        uri = (f'otpauth://totp/{ISSUER.replace(" ", "%20")}:{label}?secret={key}'
               f'&issuer={ISSUER.replace(" ", "%20")}&algorithm=SHA1&digits={DIGITS}&period={PERIOD}')
        return {'key': ' '.join(key[i:i + 4] for i in range(0, len(key), 4)), 'uri': uri}

    def confirm(self, code: str) -> list[str]:
        """Save the lock once the authenticator shows a matching code. Returns the recovery codes."""
        if not self.pending:
            raise ValueError('lock_no_setup')
        if not check_totp(self.pending['secret'], code):
            raise ValueError('lock_bad_code')
        salt = secrets.token_bytes(16)
        ver, key = _derive(self.pending['pin'], salt, ITER)
        rec = [secrets.token_hex(5).upper() for _ in range(N_RECOVERY)]
        rsalt = secrets.token_bytes(16)
        cfg = {'v': 1, 'iter': ITER, 'salt': salt.hex(), 'verifier': ver.hex(),
               'secret': _seal(key, self.pending['secret']), 'rsalt': rsalt.hex(),
               'recovery': [hashlib.sha256(rsalt + r.encode()).hexdigest() for r in rec], 'created': time.time()}
        self._write(cfg)
        self._secret = self.pending['secret']
        self.pending = None
        return [f'{r[:5]}-{r[5:]}' for r in rec]

    def _write(self, cfg: dict | None):
        if cfg is None:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        else:
            tmp = self.path.with_name(self.path.name + '.tmp')
            tmp.write_text(json.dumps(cfg, indent=1), encoding='utf-8')
            os.replace(tmp, self.path)
        self.cfg = cfg

    # -- use --------------------------------------------------------------------------------------
    def _pin_key(self, pin: str) -> bytes:
        c = self.cfg
        ver, key = _derive(str(pin), bytes.fromhex(c['salt']), int(c['iter']))
        if not hmac.compare_digest(ver, bytes.fromhex(c['verifier'])):
            raise ValueError('lock_bad_pin')
        return key

    def unlock(self, pin: str, code: str) -> bool:
        """PIN and the current code: opens the app for this run."""
        if not self.enabled:
            return True
        secret = _open(self._pin_key(pin), self.cfg['secret'])
        if not check_totp(secret, code):
            raise ValueError('lock_bad_code')
        self._secret = secret
        return True

    def recover(self, code: str) -> bool:
        """A one-time recovery code: unlocks this run (the code is spent). From there the lock can be
        turned off or set up again with a new PIN; the old secret stays unreadable without the PIN."""
        if not self.enabled:
            return True
        c = self.cfg
        norm = ''.join(ch for ch in str(code).upper() if ch.isalnum())
        h = hashlib.sha256(bytes.fromhex(c['rsalt']) + norm.encode()).hexdigest()
        if h not in c['recovery']:
            raise ValueError('lock_bad_recovery')
        c = dict(c, recovery=[x for x in c['recovery'] if x != h])
        self._write(c)
        self._secret = None
        return True

    def code_ok(self, code: str) -> bool:
        """A fresh code for a destructive action (the app is already unlocked)."""
        if not self.enabled:
            return True
        if self._secret is None:              # unlocked with a recovery code: no secret in memory
            raise ValueError('lock_reset_needed')
        return check_totp(self._secret, code)

    def disable(self, pin: str | None = None, code: str | None = None, recovered: bool = False):
        if not self.enabled:
            return
        if not recovered:
            if not self.code_ok(code or ''):
                raise ValueError('lock_bad_code')
            self._pin_key(pin or '')
        self._write(None)
        self._secret = None

    def recovery_left(self) -> int:
        return len(self.cfg.get('recovery') or []) if self.enabled else 0
