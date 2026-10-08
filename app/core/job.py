"""A Job = one raw video being edited. All state lives in <job>/plan.json so the
user can close the app, come back, tweak things in Review and re-render."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .paths import JOBS


def _jsonable(o: Any):
    """numpy scalars/arrays (from OpenCV, SigLIP, etc.) -> plain Python for json."""
    if hasattr(o, "item") and callable(o.item):
        try:
            return o.item()
        except (ValueError, TypeError):
            pass
    if hasattr(o, "tolist"):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    return str(o)


class Job:
    def __init__(self, folder: Path):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.plan_file = self.dir / "plan.json"
        self.plan: dict[str, Any] = {}
        if self.plan_file.exists():
            self.plan = json.loads(self.plan_file.read_text(encoding="utf-8"))

    @classmethod
    def create(cls, source: str | Path) -> "Job":
        source = Path(source).resolve()
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", source.stem)[:40] or "video"
        folder = JOBS / f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}"
        job = cls(folder)
        job.plan = {"source": str(source), "created": time.time(), "stage": "new"}
        job.save()
        return job

    @classmethod
    def list_recent(cls, n: int = 30) -> list["Job"]:
        dirs = sorted([d for d in JOBS.iterdir() if (d / "plan.json").exists()], key=lambda d: d.stat().st_mtime,
                      reverse=True)
        return [cls(d) for d in dirs[:n]]

    @property
    def source(self) -> Path:
        return Path(self.plan["source"])

    @property
    def name(self) -> str:
        return self.dir.name

    def path(self, *parts: str) -> Path:
        p = self.dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def save(self) -> None:
        tmp = self.plan_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.plan, indent=2, ensure_ascii=False, default=_jsonable), encoding="utf-8")
        tmp.replace(self.plan_file)

    def load_json(self, name: str, default: Any = None) -> Any:
        p = self.dir / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def save_json(self, name: str, data: Any) -> None:
        self.path(name).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=_jsonable), encoding="utf-8")
