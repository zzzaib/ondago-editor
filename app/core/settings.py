from __future__ import annotations

import json
import secrets
import threading
from typing import Any

from .paths import CONFIG, DATA

SETTINGS_FILE = DATA / "settings.json"
_LOCK = threading.Lock()


def defaults() -> dict[str, Any]:
    return json.loads((CONFIG / "default_settings.json").read_text(encoding="utf-8"))


def _read_file() -> dict[str, Any]:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def load() -> dict[str, Any]:
    s = defaults()
    s.update(_read_file())
    return s


def save(s: dict[str, Any]) -> None:
    """Merge-save: keys this caller doesn't know about (written by the phone app / another window) survive."""
    with _LOCK:
        cur = _read_file()
        cur.update(s)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)


def update(**kw: Any) -> dict[str, Any]:
    """Change only these keys (thread-safe); returns the full merged settings."""
    with _LOCK:
        cur = _read_file()
        cur.update(kw)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)
    return load()


def ensure_secret() -> str:
    """Random key that signs phone-app logins (rotating it signs every phone out)."""
    s = _read_file()
    if not s.get("remote_secret"):
        update(remote_secret=secrets.token_urlsafe(32))
    return load()["remote_secret"]
