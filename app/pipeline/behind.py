"""'Text behind the speaker' - the big-title look of professional edits: a bold 2-4 word title appears
BEHIND you (your head and shoulders stay in front of the letters), animates in word by word, holds, and
fades away.

The person is cut out on every frame with the same segmentation model as the outline doodle
(u2net_human_seg via rembg, CPU-friendly). The title layer is drawn in BASE-video coordinates with the
person's shape removed from it, so it follows every zoom and crop of the YouTube and Reel versions."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..core.paths import FONTS

FPS = 15
MASK_W = 384
STOP = set("a an the and or of to in on at for is are was be it this that with my your our".split())

Log = Callable[[str], None]


def pick_moments(callouts: list[dict], busy: list[tuple[float, float]], duration: float, every: float = 14.0,
                 limit: int = 12) -> list[dict]:
    """Which pop-up texts become behind-you titles (edit time): spaced out, away from B-roll moments."""
    out: list[dict] = []
    for c in sorted(callouts, key=lambda c: c["s"]):
        words = str(c.get("text", "")).split()
        if not 1 <= len(words) <= 5 or c["s"] < 1.0 or c["s"] > duration - 2.5:
            continue
        s, e = c["s"] - 0.1, min(duration - 0.2, c["s"] + 2.3)
        if any(s < b1 + 0.4 and e > b0 - 0.4 for b0, b1 in busy):
            continue
        if out and s - out[-1]["s"] < every:
            continue
        out.append({"s": round(s, 3), "e": round(e, 3), "text": " ".join(words).upper(), "src": c})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ masks
def person_masks(base: Path, t_base: float, dur: float, out_dir: Path, log: Log) -> list[Path]:
    """One soft person mask per frame (L, MASK_W wide), cached on disk so YouTube and Reel share them.
    The model runs on every 2nd frame; the frames in between blend their neighbours (half the work)."""
    import cv2
    from rembg import remove

    from ..core.ffmpeg import iter_frames
    from .doodles import _seg_session
    out_dir.mkdir(parents=True, exist_ok=True)
    n_want = max(1, int(math.ceil(dur * FPS)))
    have = sorted(out_dir.glob("m_*.png"))
    if len(have) >= n_want:
        return have[:n_want]
    sess = _seg_session(log)
    if not sess:
        return []
    frames = [fr for _, fr in iter_frames(base, fps=FPS, width=MASK_W, start=t_base, duration=dur)][:n_want]
    if not frames:
        return []

    def seg(fr) -> np.ndarray:
        m = np.array(remove(Image.fromarray(fr[:, :, ::-1]), session=sess, only_mask=True))
        b = (m > 110).astype(np.uint8)
        cnt, lab, stats, _ = cv2.connectedComponentsWithStats(b, 8)
        if cnt > 1:  # the person = the biggest blob; drop specks
            big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            m = np.where(lab == big, m, 0).astype(np.uint8)
        return m

    keyed = {i: seg(frames[i]) for i in range(0, len(frames), 2)}
    if (len(frames) - 1) not in keyed:
        keyed[len(frames) - 1] = seg(frames[-1])
    paths = []
    for i in range(len(frames)):
        if i in keyed:
            m = keyed[i]
        else:
            m = ((keyed[i - 1].astype(np.uint16) + keyed[i + 1]) // 2).astype(np.uint8)
        p = out_dir / f"m_{i:04d}.png"
        Image.fromarray(m).save(p)
        paths.append(p)
    return paths


def _anchor(masks: list[Path]) -> tuple[float, float, float] | None:
    """(centre x, head top y, head height) of the person, normalised, from the first ~0.6 s of masks."""
    xs, tops, hs = [], [], []
    for p in masks[: max(1, int(FPS * 0.6))]:
        m = np.array(Image.open(p)) > 127
        if m.mean() < 0.02:
            continue
        rows = np.nonzero(m.any(axis=1))[0]
        top = rows[0]
        h, w = m.shape
        band = m[top: top + max(4, int(h * 0.18))]
        cols = np.nonzero(band.any(axis=0))[0]
        if not len(cols):
            continue
        xs.append(cols.mean() / w)
        tops.append(top / h)
        hs.append(min(0.45, (cols[-1] - cols[0]) / w * w / h * 1.35))
    if len(xs) < 2:
        return None
    return float(np.median(xs)), float(np.median(tops)), float(np.median(hs))


# ------------------------------------------------------------------ title
def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("Anton-Regular.ttf", "Poppins-ExtraBold.ttf"):
        try:
            return ImageFont.truetype(str(FONTS / name), size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default(size)


def _lines(words: list[str]) -> list[list[str]]:
    if len(words) <= 2:
        return [words]
    best = None
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        score = abs(len(a) - len(b))
        if best is None or score < best[0]:
            best = (score, k)
    k = best[1]
    return [words[:k], words[k:]]


def _ease_back(x: float) -> float:
    x = min(1.0, max(0.0, x))
    c1 = 1.7
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def render_frames(base: Path, item: dict, bw: int, bh: int, region_w: float, job_dir: Path, out_dir: Path,
                  log: Log) -> int:
    """PNG frames (half base resolution) of the title with the person cut out of it. region_w: how wide
    (base px) the title may be - narrower for the 9:16 Reel crop. Returns the frame count (0 = skip)."""
    dur = item["e"] - item["s"]
    masks = person_masks(base, item["t_base"], dur, job_dir / "behind" / f"mask_{item['id']:03d}", log)
    if len(masks) < max(3, int(dur * FPS * 0.6)):
        return 0
    anc = _anchor(masks)
    if anc is None:
        return 0
    cx, top, head = anc
    W, H = bw // 2, bh // 2
    region = region_w / 2 * 0.92
    words = item["text"].split()
    lines = _lines(words)
    key = max(range(len(words)), key=lambda i: (words[i].lower() not in STOP, len(words[i])))
    # size: as big as the region allows, at most ~30% of the frame height per line
    size = int(H * (0.30 if len(lines) == 1 else 0.22))
    f = _font(size)
    while size > 20 and max(f.getlength(" ".join(ln)) for ln in lines) > region:
        size = int(size * 0.92)
        f = _font(size)
    xc = min(max(cx * W, region / 2 + W * 0.02), W - region / 2 - W * 0.02)
    # where does it go vertically? Behind the head, but placed so the person covers only a small part of
    # the letters (about 15%): the overlap sells the effect, too much makes the title unreadable.
    ref = np.array(Image.open(masks[min(len(masks) - 1, 3)]).convert("L").resize((W, H), Image.BILINEAR)) > 127
    best = None
    for _ in range(6):
        cap = -f.getbbox("H", anchor="ls")[1]
        pitch = cap * 1.12
        block = cap + pitch * (len(lines) - 1)
        tw = max(f.getlength(" ".join(ln)) for ln in lines)
        x0, x1 = int(max(0, xc - tw / 2)), int(min(W, xc + tw / 2))
        lo, hi = int(H * 0.04 + cap), int(min(H * 0.8, (top + head * 0.9) * H + cap))
        for yb in range(lo, max(lo + 1, hi), max(2, H // 90)):  # yb = baseline of the first line
            r0, r1 = int(yb - cap), int(yb - cap + block)
            if r1 >= H:
                break
            cov = float(ref[r0:r1, x0:x1].mean()) if r1 > r0 and x1 > x0 else 0.0
            score = abs(cov - 0.15) + (0.05 if cov < 0.03 else 0.0)
            if best is None or score < best[0]:
                best = (score, yb, size)
        if best and best[0] < 0.08:
            break
        size = int(size * 0.88)  # nothing fits nicely: try a smaller title
        f = _font(size)
    size = best[2] if best else size
    f = _font(size)
    cap = -f.getbbox("H", anchor="ls")[1]
    pitch = cap * 1.12
    y0 = best[1] if best else H * 0.3
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.png"):
        old.unlink()
    n = len(masks)
    gi = 0
    layout = []
    for li, ln in enumerate(lines):
        lw = f.getlength(" ".join(ln))
        x = xc - lw / 2
        for w in ln:
            layout.append((w, x, y0 + li * pitch, gi == key))
            x += f.getlength(w + " ")
            gi += 1
    stroke = max(2, size // 28)
    for i, mp in enumerate(masks):
        t = i / FPS
        out = max(0.0, min(1.0, (dur - t) / 0.28))  # fade out at the end
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d, ds = ImageDraw.Draw(lay), ImageDraw.Draw(sh)
        drift = 1.0 + 0.03 * t / max(0.1, dur)  # slow push keeps it alive
        for j, (w, x, yb, is_key) in enumerate(layout):
            p = min(1.0, max(0.0, (t - 0.08 * j) / 0.32))
            if p <= 0:
                continue
            k = _ease_back(p)
            a = int(255 * min(1.0, p * 1.8) * out)
            lift = (1 - k) * cap * 0.55
            xx = xc + (x - xc) * drift
            col = (255, 214, 0) if is_key else (255, 255, 255)
            ds.text((xx + stroke * 1.5, yb + lift + stroke * 2), w, font=f, fill=(0, 0, 0, int(a * 0.55)), anchor="ls")
            d.text((xx, yb + lift), w, font=f, fill=col + (a,), anchor="ls", stroke_width=stroke,
                   stroke_fill=(20, 16, 30, a))
        sh = sh.filter(ImageFilter.GaussianBlur(max(2, size // 14)))
        frame = Image.alpha_composite(sh, lay)
        # cut the person out of the title: you stand in front of it
        m = Image.open(mp).convert("L").resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.2))
        a_ch = np.array(frame.getchannel("A"), np.float32) * (1.0 - np.array(m, np.float32) / 255.0)
        frame.putalpha(Image.fromarray(a_ch.astype(np.uint8)))
        frame.save(out_dir / f"f_{i:04d}.png", compress_level=1)
    return n
