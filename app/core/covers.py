"""A small cover image per project (cover.jpg in the job folder) for project lists when no thumbnail exists yet."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from . import ffmpeg
from .job import Job

_lock = threading.Lock()
_tried: set[str] = set()


def cover_path(job_dir: str | Path) -> Path:
    return Path(job_dir) / "cover.jpg"


def make(job_dir: str | Path) -> bool:
    """Grab one frame from the edit (or the raw video). Each project is only tried once per run."""
    d = Path(job_dir)
    out = cover_path(d)
    if out.exists():
        return True
    with _lock:
        if str(d) in _tried:
            return False
        _tried.add(str(d))
    try:
        plan = Job(d).plan
    except Exception:  # noqa: BLE001
        return False
    cands = [d / "youtube.mp4", d / "reel.mp4", d / "base.mkv"]
    if plan.get("source"):
        cands.append(Path(plan["source"]))
    src = next((c for c in cands if c.is_file()), None)
    if not src:
        return False
    st = d.stat()  # keep the folder's date: project lists are sorted by it
    try:
        dur = ffmpeg.probe(str(src)).get("duration", 0) or 0
        tmp = out.with_name("cover.part.jpg")
        ffmpeg.run(["-ss", f"{min(3.0, dur / 3):.2f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=360:-2",
                    "-q:v", "4", "-f", "image2", str(tmp)])
        tmp.replace(out)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns))
        except OSError:
            pass


def make_many(dirs: list[str], log=None, progress=None) -> int:
    return sum(1 for d in dirs if make(d))


def make_in_background(dirs: list[str]) -> None:
    dirs = [d for d in dirs if not cover_path(d).exists() and d not in _tried]
    if dirs:
        threading.Thread(target=make_many, args=(dirs,), daemon=True).start()
