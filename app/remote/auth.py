"""Phone-app login: a password (PBKDF2-hashed in settings) or a one-time pairing code from the QR in the
desktop app. Successful logins get a signed token (HttpOnly cookie) valid for 60 days."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from ..core import settings

TOKEN_DAYS = 60
_pairs: dict[str, float] = {}
_fails: dict[str, list[float]] = {}
_lock = threading.Lock()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    it = 200_000
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, it)
    return f"pbkdf2${it}${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, h = stored.split("$")
        if algo != "pbkdf2":
            return False
        got = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), h)
    except Exception:  # noqa: BLE001
        return False


def set_password(pw: str) -> None:
    settings.update(remote_password_hash=hash_password(pw))


def _sig(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:40]


def make_token() -> str:
    secret = settings.ensure_secret()
    body = f"{int(time.time() + TOKEN_DAYS * 86400)}.{secrets.token_hex(8)}"
    return f"{body}.{_sig(secret, body)}"


def valid_token(tok: str | None) -> bool:
    if not tok or tok.count(".") != 2:
        return False
    exp, nonce, sig = tok.split(".")
    secret = settings.load().get("remote_secret", "")
    if not secret or not hmac.compare_digest(_sig(secret, f"{exp}.{nonce}"), sig):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


def sign_out_everyone() -> None:
    settings.update(remote_secret=secrets.token_urlsafe(32))


def new_pair_code(minutes: int = 10) -> str:
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    with _lock:
        now = time.time()
        for k in [k for k, v in _pairs.items() if v < now]:
            _pairs.pop(k, None)
        _pairs[code] = now + minutes * 60
    return code


def use_pair_code(code: str) -> bool:
    code = (code or "").strip().upper()
    with _lock:
        exp = _pairs.pop(code, None)
    return bool(exp and exp > time.time())


def throttled(ip: str) -> bool:
    """True if this address had 6+ failed logins in the last 10 minutes."""
    now = time.time()
    with _lock:
        lst = [t for t in _fails.get(ip, []) if now - t < 600]
        _fails[ip] = lst
        return len(lst) >= 6


def failed(ip: str) -> None:
    with _lock:
        _fails.setdefault(ip, []).append(time.time())
