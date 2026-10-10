"""High-level actions used by the GUI (each runs inside a background Task)."""
from __future__ import annotations

import functools
import threading
from pathlib import Path

from .core.job import Job
from .pipeline import runner
from .pipeline.broll import BrollResolver, online


# One heavy GPU job at a time, whether it was started from the desktop window or from the phone.
GPU_LOCK = threading.RLock()


def heavy(fn):
    @functools.wraps(fn)
    def wrap(job, cfg, *args, log, progress, **kw):
        if not GPU_LOCK.acquire(blocking=False):
            progress(0.0, "Waiting - another edit is using the GPU")
            log("Waiting for the GPU (another edit is running)")
            GPU_LOCK.acquire()
        try:
            return fn(job, cfg, *args, log=log, progress=progress, **kw)
        finally:
            GPU_LOCK.release()
    return wrap


@heavy
def full_auto(job: Job, cfg: dict, log, progress) -> str:
    """Raw video -> analysis -> (asks only if B-roll is missing) -> render -> export."""
    runner.analyze(job, cfg, log, lambda p, m: progress(p * 0.55, m))
    if _stops(job, cfg):
        return job.plan["stage"]
    runner.render_all(job, cfg, log, lambda p, m: progress(0.55 + 0.45 * p, m))
    return "rendered"


def _stops(job: Job, cfg: dict) -> bool:
    stage = job.plan.get("stage")
    if stage == "needs_broll" and cfg.get("broll_missing_action", "auto") == "ask":
        return True
    return bool(cfg.get("review_before_render", False)) and stage in ("analyzed", "needs_broll")


@heavy
def render(job: Job, cfg: dict, log, progress) -> str:
    runner.render_all(job, cfg, log, progress)
    return "rendered"


def search_slot(job: Job, cfg: dict, slot_id: int, query: str, log, progress) -> int:
    slot = next(s for s in job.plan["broll"] if s["id"] == slot_id)
    if not online.available(cfg):
        raise RuntimeError("Add a free Pixabay API key in Settings to search online video (pixabay.com/api/docs).")
    progress(0.1, f"Searching '{query}'")
    found = online.search([query], cfg, min_dur=slot["e"] - slot["s"], log=log, per_query=6)
    pdir = job.dir / "broll_previews"
    pdir.mkdir(exist_ok=True)
    for i, c in enumerate(found):
        c["preview"] = online.download_preview(c, pdir) or ""
        progress(0.1 + 0.9 * (i + 1) / max(1, len(found)), "Loading previews")
    slot["candidates"] = found + [c for c in slot.get("candidates", []) if c.get("source") == "library"]
    job.save()
    return len(found)


def use_candidate(job: Job, cfg: dict, slot_id: int, idx: int, log, progress) -> None:
    slot = next(s for s in job.plan["broll"] if s["id"] == slot_id)
    progress(0.2, "Downloading clip")
    BrollResolver(cfg, job.dir, log).use_candidate(slot, idx)
    job.save()
    progress(1.0, "Clip ready")


@heavy
def ai_slot(job: Job, cfg: dict, slot_id: int, log, progress) -> None:
    from .pipeline.broll import generate
    ok, why = generate.available()
    if not ok:
        raise RuntimeError(f"AI images unavailable: {why}")
    slot = next(s for s in job.plan["broll"] if s["id"] == slot_id)
    BrollResolver(cfg, job.dir, log).generate_ai([slot], lambda p: progress(p, "Generating AI image"))
    slot["user_locked"] = True
    job.save()


@heavy
def regen_thumbs(job: Job, cfg: dict, texts: list[str], log, progress) -> None:
    from .pipeline.planner import Planner
    from .pipeline.thumbnail import make_thumbnails
    from .pipeline.timeline import compute_keeps
    info = job.plan["media"]
    keeps = compute_keeps(info["duration"], job.plan.get("cuts", []), info["fps"])
    planner = Planner(cfg, log)
    if not planner.llm.alive():
        try:
            planner.llm.ensure_server()
        except Exception:
            planner.ok = False
    job.plan["metadata"]["thumbnail_texts"] = texts
    job.plan["thumbnails"] = make_thumbnails(job.source, keeps, job.dir, texts,
                                             job.plan["metadata"].get("thumbnail_bg_prompt") or "colorful studio",
                                             cfg, planner, log, progress,
                                             rotate=job.plan.get("media", {}).get("rotate", 0))
    job.save()


def upload_youtube(job: Job, cfg: dict, log, progress) -> str:
    from .upload import youtube
    m = job.plan["metadata"]
    vid = job.plan.get("outputs", {}).get("youtube")
    if not vid or not Path(vid).exists():
        raise RuntimeError("Render the YouTube version first.")
    th = job.plan.get("thumbnails", {})
    thumb = th["options"][th.get("choice", 0)]["path"] if th.get("options") else None
    url = youtube.upload(vid, m.get("title", job.source.stem), m.get("youtube_description") or
                         runner.youtube_description(job.plan), m.get("tags", []), cfg.get("youtube_privacy", "private"),
                         cfg.get("youtube_category_id", "22"), thumb, m.get("publish_at") or None,
                         on_progress=lambda p: progress(p, "Uploading to YouTube"), log=log)
    job.plan.setdefault("published", {})["youtube"] = url
    job.save()
    return url


def upload_instagram(job: Job, cfg: dict, log, progress) -> str:
    from .upload import instagram
    vid = job.plan.get("outputs", {}).get("reel")
    if not vid or not Path(vid).exists():
        raise RuntimeError("Render the Reel version first.")
    if not cfg.get("instagram_user_id") or not cfg.get("instagram_access_token"):
        raise RuntimeError("Add your Instagram user id and access token in Settings.")
    m = job.plan["metadata"]
    url = instagram.upload_reel(vid, m.get("instagram_caption_full") or runner.instagram_caption(job.plan),
                                cfg["instagram_user_id"], cfg["instagram_access_token"],
                                cfg.get("instagram_api_version", "v23.0"), cfg.get("instagram_share_to_feed", True),
                                on_progress=lambda p: progress(p, "Uploading to Instagram"), log=log)
    job.plan.setdefault("published", {})["instagram"] = url
    job.save()
    return url


def make_preview(job: Job, kind: str, log=print, progress=None) -> Path:
    """Small 540p copy of an output for streaming to a phone (cached next to the job)."""
    from .core import ffmpeg
    src = job.plan.get("outputs", {}).get(kind)
    if not src or not Path(src).exists():
        raise FileNotFoundError(kind)
    out = job.path(f"preview720_{kind}.mp4")
    if out.exists() and out.stat().st_mtime >= Path(src).stat().st_mtime:
        return out
    vf = "scale=-2:720" if kind == "youtube" else "scale=720:-2"
    tmp = out.with_suffix(".part.mp4")
    dur = float(job.plan.get("edit_duration") or 0) or None
    ffmpeg.run(["-i", src, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)], duration=dur,
               on_progress=(lambda p: progress(p, "Making phone preview")) if progress else None)
    tmp.replace(out)
    return out
