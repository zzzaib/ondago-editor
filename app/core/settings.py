from __future__ import annotations

import json
from typing import Any

from .paths import CONFIG, DATA

SETTINGS_FILE = DATA / "settings.json"


def defaults() -> dict[str, Any]:
    return json.loads((CONFIG / "default_settings.json").read_text(encoding="utf-8"))


def load() -> dict[str, Any]:
    s = defaults()
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return s


def save(s: dict[str, Any]) -> None:
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
