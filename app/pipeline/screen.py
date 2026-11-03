"""Screen recordings / tutorials.

A talking-head edit cuts every pause. In a tutorial that is wrong: while you are silent you are usually
clicking, typing or scrolling, and cutting those moments makes the viewer miss steps. So for screen
recordings:

  * we measure where and how much the screen changes (2 frames per second, small size);
  * silent parts where the screen moves are SPED UP (2-8x) instead of cut; silent parts where nothing
    happens are still cut;
  * punch-in zooms centre on the part of the screen that is changing (the action), not on a face;
  * no slow "camera push" (it would crop the edges of the UI) and Reels use the fit + blurred layout.

A small webcam in the corner does not make it a talking-head video: faces must be big to count."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from ..core.ffmpeg import iter_frames

FPS = 2.0


def activity(video: str | Path, rotate: int = 0, width: int = 480, log: Callable[[str], None] | None = None) -> list[dict]:
    """[{t, amount, box}] - amount = share of the frame that changed since the previous sample (0..1),
    box = normalised (x0, y0, x1, y1) around the change, or None.

    Works on 8x8-pixel blocks so typing a few characters still counts, and ignores areas that move almost
    all the time (a live webcam corner, a playing video) so they don't make every pause look busy."""
    import cv2
    B = 8
    grids: list[np.ndarray] = []
    times: list[float] = []
    prev = None
    shape = None
    for t, fr in iter_frames(video, fps=FPS, width=width, rotate=rotate):
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        h, w = g.shape
        hh, ww = h // B * B, w // B * B
        if prev is not None:
            m = (cv2.absdiff(g, prev)[:hh, :ww] > 18).astype(np.uint16)
            grids.append(m.reshape(hh // B, B, ww // B, B).sum(axis=(1, 3)))
            times.append(float(t))
            shape = (hh, ww)
        prev = g
    out: list[dict] = [{"t": 0.0, "amount": 0.0, "box": None}]
    if not grids:
        return out
    stack = np.stack(grids) > 1
    persistent = stack.mean(axis=0) > 0.5  # moves in most samples: webcam / video player, not an action
    total = shape[0] * shape[1]
    gh, gw = stack.shape[1:]
    for t, grid in zip(times, grids):
        g2 = np.where(persistent, 0, grid)
        amt = float(g2.sum()) / total
        box = None
        if amt > 0.00015:
            ys, xs = np.nonzero(g2 > 1)
            if len(xs):
                x0, x1 = np.percentile(xs, [3, 97])
                y0, y1 = np.percentile(ys, [3, 97])
                box = (round(float(x0 / gw), 3), round(float(y0 / gh), 3), round(float((x1 + 1) / gw), 3),
                       round(float((y1 + 1) / gh), 3))
        out.append({"t": round(t, 2), "amount": round(amt, 6), "box": box})
    if log:
        busy = sum(1 for a in out if a["amount"] > BUSY)
        log(f"Screen activity: {busy}/{len(out)} samples with something changing on screen"
            + (f" (ignoring an always-moving area, e.g. a webcam: {persistent.mean() * 100:.0f}% of the frame)"
               if persistent.any() else ""))
    return out


BUSY = 0.0003  # share of the frame that must change for "something is happening on screen"


def is_screen_recording(info: dict, face_track: list[dict], act: list[dict] | None = None) -> bool:
    """No big face for most of the video = a screen recording / tutorial (a webcam corner doesn't count)."""
    if info.get("height", 0) > info.get("width", 0) * 1.2:
        return False  # vertical phone clip
    big = [f for f in face_track if f.get("cx") is not None and f.get("size", 0) >= 0.14]
    return len(big) < 0.35 * max(1, len(face_track))


def _busy(act: list[dict], s: float, e: float) -> float:
    """Share of the samples in [s, e] where the screen visibly changes."""
    xs = [a for a in act if s <= a["t"] <= e]
    if not xs:
        return 0.0
    return sum(1 for a in xs if a["amount"] > BUSY) / len(xs)


def speed_silences(cuts: list[dict], act: list[dict], cfg: dict, log: Callable[[str], None] | None = None) -> list[dict]:
    """Silence cuts during which the screen is moving become fast-forwards instead of jumps."""
    mx = float(cfg.get("screen_max_speed", 8.0))
    target = float(cfg.get("screen_fast_target", 1.6))  # a sped-up stretch lasts about this long
    n = 0
    out = []
    for c in cuts:
        dur = c["e"] - c["s"]
        if c.get("kind") == "silence" and dur >= 1.0 and _busy(act, c["s"], c["e"]) >= 0.3:
            sp = round(max(2.0, min(mx, dur / target)), 2)
            out.append({**c, "kind": "speed", "speed": sp, "text": f"{sp:g}x fast-forward (screen action)"})
            n += 1
        else:
            out.append(c)
    if log and n:
        log(f"Screen recording: {n} silent moments with on-screen action are sped up instead of cut")
    return out


def action_track(act: list[dict]) -> list[dict]:
    """A 'face track'-shaped list that follows the on-screen action, so zooms centre on what changes."""
    out = []
    last = None
    for a in act:
        b = a.get("box")
        if b and a["amount"] > BUSY and (b[2] - b[0]) < 0.85:
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if last:  # smooth: the camera shouldn't twitch
                cx, cy = last[0] * 0.5 + cx * 0.5, last[1] * 0.5 + cy * 0.5
            last = (cx, cy)
            out.append({"t": a["t"], "cx": round(cx, 3), "cy": round(cy, 3), "size": 0.2})
        else:
            out.append({"t": a["t"], "cx": None, "cy": None, "size": 0})
    return out


def action_zooms(act: list[dict], min_len: float = 2.0, gap: float = 6.0) -> list[tuple[float, float]]:
    """Moments where the action stays in one small area (typing in a field, clicking through a menu):
    a zoom-in there makes small UI text readable, like Screen Studio-style auto zoom."""
    runs: list[tuple[float, float]] = []
    cur: list[dict] = []

    def flush():
        if len(cur) >= 2 and cur[-1]["t"] - cur[0]["t"] >= min_len - 1 / FPS:
            s, e = cur[0]["t"] - 0.3, cur[-1]["t"] + 0.6
            if not runs or s - runs[-1][1] >= gap:
                runs.append((max(0.0, s), e))

    for a in act:
        b = a.get("box")
        small = b and a["amount"] > BUSY and (b[2] - b[0]) * (b[3] - b[1]) < 0.20
        if small:
            c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            if cur:
                pb = cur[-1]["box"]
                pc = ((pb[0] + pb[2]) / 2, (pb[1] + pb[3]) / 2)
                if abs(c[0] - pc[0]) > 0.15 or abs(c[1] - pc[1]) > 0.15:
                    flush()
                    cur = []
            cur.append(a)
        else:
            flush()
            cur = []
    flush()
    return runs
