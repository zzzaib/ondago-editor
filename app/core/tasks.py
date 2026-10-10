"""Background task queue shared by the phone app (remote server) and anything else that wants to queue work.

One worker thread runs tasks in order. Heavy GPU actions additionally take actions.GPU_LOCK, so a render started
from the desktop window and one started from the phone never fight over the 8 GB of VRAM."""
from __future__ import annotations

import itertools
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .paths import DATA

_ids = itertools.count(1)


@dataclass
class TaskInfo:
    id: int
    job: str
    label: str
    state: str = "queued"  # queued | running | done | failed | cancelled
    progress: float = 0.0
    msg: str = ""
    result: Any = None
    error: str = ""
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    log: deque = field(default_factory=lambda: deque(maxlen=300))

    def public(self) -> dict:
        res = self.result if isinstance(self.result, (str, int, float, bool, type(None))) else str(self.result)
        return {"id": self.id, "job": self.job, "label": self.label, "state": self.state,
                "progress": round(self.progress, 4), "msg": self.msg, "result": res, "error": self.error[-600:],
                "created": self.created, "started": self.started, "finished": self.finished}


class TaskManager:
    def __init__(self):
        self.tasks: dict[int, TaskInfo] = {}
        self._fns: dict[int, tuple[Callable, tuple, dict, Callable | None]] = {}
        self._order: deque[int] = deque()
        self._cv = threading.Condition()
        self._subs: list[Callable[[dict], None]] = []
        self._thread: threading.Thread | None = None
        self._logfile = None
        self._last_emit: dict[int, float] = {}

    # ------------------------------------------------------------ public
    def submit(self, job: str, label: str, fn: Callable, *args, after: Callable | None = None, **kwargs) -> TaskInfo:
        """fn(*args, log=..., progress=..., **kwargs). `after(task)` runs in the worker when it finishes."""
        t = TaskInfo(next(_ids), job, label)
        with self._cv:
            self.tasks[t.id] = t
            self._fns[t.id] = (fn, args, kwargs, after)
            self._order.append(t.id)
            self._ensure_worker()
            self._cv.notify()
        self._emit(t, force=True)
        return t

    def cancel(self, tid: int) -> bool:
        with self._cv:
            t = self.tasks.get(tid)
            if not t or t.state != "queued":
                return False
            t.state = "cancelled"
            try:
                self._order.remove(tid)
            except ValueError:
                pass
        self._emit(t, force=True)
        return True

    def subscribe(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        self._subs.append(cb)
        return lambda: self._subs.remove(cb) if cb in self._subs else None

    def for_job(self, job: str) -> TaskInfo | None:
        ts = [t for t in self.tasks.values() if t.job == job]
        active = [t for t in ts if t.state in ("running", "queued")]
        pick = active or ts
        return max(pick, key=lambda t: t.id) if pick else None

    def busy(self) -> bool:
        return any(t.state in ("running", "queued") for t in self.tasks.values())

    def snapshot(self, limit: int = 50) -> list[dict]:
        return [t.public() for t in sorted(self.tasks.values(), key=lambda t: -t.id)[:limit]]

    # ------------------------------------------------------------ worker
    def _ensure_worker(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="ondago-tasks", daemon=True)
            self._thread.start()

    def _loop(self):
        while True:
            with self._cv:
                while not self._order:
                    self._cv.wait()
                tid = self._order.popleft()
                t = self.tasks[tid]
                fn, args, kwargs, after = self._fns.pop(tid)
            if t.state == "cancelled":
                continue
            t.state, t.started = "running", time.time()
            self._emit(t, force=True)
            try:
                t.result = fn(*args, log=lambda m, t=t: self._log(t, m), progress=lambda p, m="", t=t: self._prog(t, p, m),
                              **kwargs)
                t.state, t.progress = "done", 1.0
            except Exception as e:  # noqa: BLE001
                t.error = f"{e}"
                self._log(t, traceback.format_exc())
                t.state = "failed"
            t.finished = time.time()
            self._emit(t, force=True)
            if after:
                try:
                    after(t)
                except Exception:  # noqa: BLE001
                    self._log(t, traceback.format_exc())

    def _log(self, t: TaskInfo, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] [{t.job[:24]}] {msg}"
        t.log.append(line)
        try:
            if self._logfile is None:
                self._logfile = open(DATA / "app.log", "a", encoding="utf-8")
            self._logfile.write(line + "\n")
            self._logfile.flush()
        except Exception:  # noqa: BLE001
            pass
        for cb in list(self._subs):
            try:
                cb({"type": "log", "task": t.id, "job": t.job, "line": line[:500]})
            except Exception:  # noqa: BLE001
                pass

    def _prog(self, t: TaskInfo, p: float, msg: str = ""):
        t.progress = max(0.0, min(1.0, float(p)))
        if msg:
            t.msg = msg
        self._emit(t)

    def _emit(self, t: TaskInfo, force: bool = False):
        now = time.time()
        if not force and now - self._last_emit.get(t.id, 0) < 0.4:  # max ~2.5 updates/s per task
            return
        self._last_emit[t.id] = now
        ev = {"type": "task", "task": t.public()}
        for cb in list(self._subs):
            try:
                cb(ev)
            except Exception:  # noqa: BLE001
                pass


MANAGER = TaskManager()
