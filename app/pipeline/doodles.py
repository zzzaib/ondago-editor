"""Animated doodles: hand-drawn icons, arrows and outlines that draw themselves on screen.

Planning   : the editor LLM marks moments (idea -> lightbulb above your head, "I/me" -> arrow at you,
             money talk -> rupee/dollar scribble, ...). A keyword heuristic covers LLM-less runs.
Placement  : uses the face track so doodles sit above / beside your head and never cover your face,
             the captions or a callout.
Rendering  : every doodle is drawn stroke by stroke with a wobbly marker line, tapered ends and
             "line boil" (the redraw shimmer of hand-made animation), then exported as a short
             transparent PNG sequence that FFmpeg overlays.
Extras     : subject outline (an animated white sticker-outline around you on key moments) and
             AI custom doodles (FLUX draws a doodle of anything; RTX GPUs only).
"""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..core.paths import CACHE, font_path
from . import doodle_shapes as ds
from .cuts import norm

Log = Callable[[str], None]
DOODLE_FPS = 30
BOIL_FPS = 10   # hand-drawn animation is redrawn ~10x per second ("on threes")
SS = 2          # supersampling for smooth lines


# =================================================================== planning
HEURISTIC = [  # (kind, words that trigger it)  - English + common Hinglish
    ("idea", "idea ideas tip tips trick tricks hack realise realize socho samjho"),
    ("money", "money paisa paise rupees rupee price cost free earn earning salary dollars budget sasta mehenga"),
    ("growth", "growth grow increase double triple results views subscribers badh badhega"),
    ("fast", "fast faster speed quick quickly instantly launch boost jaldi turant"),
    ("time", "time minutes minute hours hour seconds years saal ghante mins deadline"),
    ("love", "love favourite favorite pyaar pasand loved"),
    ("best", "best king top number greatest sabse"),
    ("warning", "careful warning mistake mistakes never danger galti dhyan"),
    ("question", "why how what kya kaise kyun kyon question"),
    ("magic", "magic ai automatic automatically jaadu"),
    ("yes", "correct exactly done perfect sahi"),
    ("no", "wrong dont don't stop galat mat"),
    ("search", "search find research dhundo"),
    ("music", "music song songs gaana"),
    ("win", "win winner won jeet achievement award"),
    ("hot", "viral trending hot fire"),
]


def heuristic_doodles(segments: list[dict], exclude: set[int], every: float) -> list[dict]:
    out, last = [], -1e9
    table = [(k, set(ws.split())) for k, ws in HEURISTIC]
    for s in segments:
        if s["id"] in exclude or s["s"] - last < every or s["s"] < 3.0:
            continue
        for w in s.get("words", []):
            n = norm(w["w"])
            hit = next((k for k, ws in table if n in ws), None)
            if hit:
                out.append({"segment_id": s["id"], "kind": hit, "anchor_words": w["w"], "word": "", "prompt": ""})
                last = s["s"]
                break
    return out


def build_doodles(raw: list[dict], transcript: dict, duration: float, excluded: set[int], cfg: dict,
                  ai_ok: bool) -> list[dict]:
    """LLM/heuristic picks -> timed doodle records (SOURCE time) stored in plan['doodles']."""
    segs = {s["id"]: s for s in transcript["segments"]}
    hinglish = "hinglish" in str(cfg.get("_language", "")) or cfg.get("caption_script") == "hinglish"
    gap = {"low": 22.0, "medium": 12.0, "high": 7.0}.get(cfg.get("doodle_density", "medium"), 12.0)
    out: list[dict] = []
    for r in sorted(raw, key=lambda r: segs.get(r.get("segment_id"), {}).get("s", 1e9)):
        seg = segs.get(r.get("segment_id"))
        kind = r.get("kind", "")
        if not seg or seg["id"] in excluded or kind not in ds.KINDS:
            continue
        if kind == "custom" and not (ai_ok and r.get("prompt")):
            continue
        start = seg["s"]
        anchor = norm(r.get("anchor_words", "")).split()
        if anchor:
            for w in seg.get("words", []):
                if norm(w["w"]) == anchor[0]:
                    start = w["s"]
                    break
        if start < 2.0 or (out and start - out[-1]["s"] < gap):
            continue
        shape, where, color, sound = ds.KINDS[kind]
        if shape == "money" and hinglish:
            shape = "rupee"
        out.append({"id": len(out), "s": round(start, 3), "e": round(min(duration, start + 1.9), 3), "kind": kind,
                    "shape": shape, "anchor": where, "color": color, "sound": sound,
                    "word": re.sub(r"[^A-Za-z0-9!?]", "", (r.get("word") or "WOW").upper())[:6] or "WOW",
                    "prompt": (r.get("prompt") or "")[:80], "file": "", "line": seg["text"][:140],
                    "enabled": True, "source": r.get("_src", "llm")})
    return out


def build_outlines(zooms: list[dict], duration: float, cfg: dict) -> list[dict]:
    """Subject-outline moments: the strongest emphasis zooms, spaced apart."""
    gap = {"low": 40.0, "medium": 22.0, "high": 12.0}.get(cfg.get("doodle_density", "medium"), 22.0)
    out: list[dict] = []
    for z in sorted(zooms, key=lambda z: z["s"]):
        if z["e"] - z["s"] < 1.0 or z["s"] < 2.0 or (out and z["s"] - out[-1]["s"] < gap):
            continue
        out.append({"id": len(out), "s": round(z["s"], 3), "e": round(min(duration, z["s"] + 2.0, z["e"] + 0.4), 3),
                    "enabled": True})
    return out


# ================================================================ drawing core
def _resample(p: np.ndarray, step: float) -> np.ndarray:
    if len(p) < 2:
        return p
    d = np.r_[0, np.cumsum(np.hypot(*np.diff(p, axis=0).T))]
    if d[-1] < 1e-6:
        return p[:1]
    n = max(2, int(d[-1] / step) + 1)
    s = np.linspace(0, d[-1], n)
    return np.stack([np.interp(s, d, p[:, 0]), np.interp(s, d, p[:, 1])], 1)


def _length(p: np.ndarray) -> float:
    return float(np.hypot(*np.diff(p, axis=0).T).sum()) if len(p) > 1 else 0.0


def _wobble(p: np.ndarray, amp: float, seed: int, boil: int) -> np.ndarray:
    """Low-frequency marker wobble; phases drift a little with each boil step (the shimmer)."""
    if len(p) < 2 or amp <= 0:
        return p
    rng = np.random.default_rng(seed)
    ph = rng.uniform(0, 2 * math.pi, 4) + np.random.default_rng(seed * 31 + boil).uniform(-0.7, 0.7, 4)
    f = rng.uniform(0.6, 1.6, 2)
    s = np.linspace(0, 1, len(p))
    dx = np.sin(2 * math.pi * f[0] * s + ph[0]) * 0.6 + np.sin(2 * math.pi * 2.3 * f[1] * s + ph[1]) * 0.4
    dy = np.sin(2 * math.pi * f[1] * s + ph[2]) * 0.6 + np.sin(2 * math.pi * 2.1 * f[0] * s + ph[3]) * 0.4
    j = np.random.default_rng(seed * 7 + boil).normal(0, amp * 0.25, 2)
    return p + np.stack([dx, dy], 1) * amp + j


def _ease_out_back(k: float) -> float:
    c1, c3 = 1.9, 2.9
    return 1 + c3 * (k - 1) ** 3 + c1 * (k - 1) ** 2


def _draw_stroke(dr: ImageDraw.ImageDraw, pts: np.ndarray, upto: float, width: float, color, taper: bool = True):
    """Draw the first `upto` fraction (0..1) of a stroke with a tapered marker."""
    n = len(pts)
    if n < 2 or upto <= 0:
        return
    m = max(2, int(math.ceil(upto * (n - 1))) + 1)
    seg = pts[:m]
    s = np.linspace(0, (m - 1) / (n - 1), m)
    for i in range(m - 1):
        w = width * ((0.62 + 0.38 * math.sin(math.pi * min(1.0, s[i]))) if taper else 1.0)
        wi = max(1, int(round(w)))
        x0, y0 = seg[i]
        x1, y1 = seg[i + 1]
        dr.line([(x0, y0), (x1, y1)], fill=color, width=wi)
        r = w / 2
        dr.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=color)
    x0, y0 = seg[0]
    r = width * (0.62 if taper else 1.0) / 2
    dr.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=color)


_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    k = (name, size)
    if k not in _FONT_CACHE:
        try:
            _FONT_CACHE[k] = ImageFont.truetype(str(font_path(name)), size)
        except Exception:  # noqa: BLE001
            _FONT_CACHE[k] = ImageFont.load_default()
    return _FONT_CACHE[k]


class DoodleArt:
    """One doodle at a fixed pixel size; .frame(t) returns an RGBA PIL image."""

    PAD = 0.2  # empty margin around the art so pop/rotation never clips

    def __init__(self, ev: dict, w: int, h: int, dur: float, seed: int = 0):
        self.ev, self.w, self.h, self.dur, self.seed = ev, int(w), int(h), dur, seed
        self.ss = 2 if max(self.w, self.h) <= 520 else 1  # supersample small art; big art is smooth enough
        self.flip = bool(ev.get("flip"))
        self.custom = None
        if ev.get("shape") == "custom" and ev.get("file") and Path(ev["file"]).exists():
            self.custom = _CustomInk(Path(ev["file"]), self.w, self.h, self.PAD, self.ss)
            self.shape = None
        else:
            self.shape = ds.get(ev.get("shape", "stars"), self.flip)
        col = ds.COLORS.get(ev.get("color", "yellow"), (255, 214, 0))
        self.color = (*col, 255)
        self.white = (255, 255, 255, 255)
        self.ink = (18, 16, 28, 235)
        base = min(self.w, self.h)
        self.pen = max(4.0, base * (0.040 if self.shape is None or len(self.shape.strokes) < 6 else 0.034)) * self.ss
        if ev.get("shape") in ("underline", "circle_text"):
            self.pen = max(5.0, min(self.h * 0.30, base * 0.5, 18.0)) * self.ss
        # unit -> canvas pixels (inside padding)
        cw, ch = self.w * self.ss, self.h * self.ss
        self.ox, self.oy = cw * self.PAD, ch * self.PAD
        self.sx, self.sy = cw * (1 - 2 * self.PAD), ch * (1 - 2 * self.PAD)
        if self.shape is not None:
            raw = [np.stack([s[:, 0] * self.sx + self.ox, s[:, 1] * self.sy + self.oy], 1) for s in self.shape.strokes]
            self.strokes = [_resample(s, max(2.0, self.pen * 0.35)) for s in raw]
            self.lens = [_length(s) for s in self.strokes]
            total = sum(self.lens) / self.ss  # output pixels
            self.draw_time = float(np.clip(0.22 + total / 2400.0, 0.30, 0.80))
            if ev.get("shape") in ("underline", "circle_text"):
                self.draw_time = 0.32
        else:
            self.draw_time = 0.6

    # ------------------------------------------------------------- timing
    def _transform(self, t: float) -> tuple[float, float, float, float]:
        """scale, rotation (deg), dy (px), alpha for time t."""
        sh = self.shape
        scale, alpha = 1.0, 1.0
        if (sh is None or sh.pops) and t < 0.32:
            scale = 0.72 + 0.28 * _ease_out_back(min(1.0, t / 0.32))
        rot = 2.2 * math.sin(2 * math.pi * 0.7 * t + self.seed)
        if sh is not None and not sh.pops:
            rot *= 0.3
        dy = 0.0
        if sh is not None and sh.float_up:
            dy = -self.h * self.ss * 0.05 * t / max(0.5, self.dur) + math.sin(t * 5) * self.h * self.ss * 0.008
        out = 0.2
        if t > self.dur - out:
            k = (t - (self.dur - out)) / out
            alpha = max(0.0, 1 - k)
            scale *= 1 - 0.18 * k
        return scale, rot, dy, alpha

    def _xf(self, p: np.ndarray, scale: float, rot: float, dy: float) -> np.ndarray:
        c = np.array([self.w * self.ss / 2, self.h * self.ss / 2])
        a = math.radians(rot)
        R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        return (p - c) @ R.T * scale + c + [0, dy]

    def frame(self, t: float) -> Image.Image:
        if self.custom is not None:
            sc, rot, dy, al = self._transform(t)
            img = self.custom.frame(min(1.0, t / self.draw_time), int(t * BOIL_FPS), sc, rot)
            return _fade(img, al)
        img = Image.new("RGBA", (self.w * self.ss, self.h * self.ss), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        boil = int(t * BOIL_FPS)
        scale, rot, dy, alpha = self._transform(t)
        prog = min(1.0, t / self.draw_time) * sum(self.lens)
        amp = self.pen * 0.18 + min(self.w, self.h) * self.ss * 0.004
        pts_list, upto, done_at = [], [], []
        acc = 0.0
        for i, (s, L) in enumerate(zip(self.strokes, self.lens)):
            u = 1.0 if L <= 0 else float(np.clip((prog - acc) / L, 0, 1))
            acc += L
            p = self._xf(_wobble(s, amp, self.seed * 97 + i, boil), scale, rot, dy)
            pts_list.append(p)
            upto.append(u)
            done_at.append(acc / max(1e-6, sum(self.lens)) * self.draw_time)
        fills = set(self.shape.fill)
        # 1) dark marker outline for contrast on any background
        for i, (p, u) in enumerate(zip(pts_list, upto)):
            _draw_stroke(dr, p, u, self.pen * 1.85 + 3 * self.ss, self.ink, taper=i not in fills)
        # 2) colour fills (stickers) fade in once their stroke is complete
        for i in fills:
            if i < len(pts_list) and upto[i] >= 1.0:
                k = min(1.0, (t - done_at[i]) / 0.12 + 0.35)
                col = (*self.color[:3], int(255 * k))
                dr.polygon([tuple(x) for x in pts_list[i]], fill=col)
        # 3) the marker line itself
        for i, (p, u) in enumerate(zip(pts_list, upto)):
            _draw_stroke(dr, p, u, self.pen, self.white if i in fills else self.color, taper=i not in fills)
        # 4) the word inside a WOW burst
        if self.ev.get("shape") == "wow" and upto and upto[0] >= 1.0:
            self._wow_text(img, t - done_at[0], scale, rot, dy)
        img = img.resize((self.w, self.h), Image.LANCZOS)
        return _fade(img, alpha)

    def _wow_text(self, img: Image.Image, since: float, scale: float, rot: float, dy: float):
        word = self.ev.get("word") or "WOW"
        k = min(1.0, max(0.0, since) / 0.22)
        pop = (0.5 + 0.5 * _ease_out_back(k)) * scale
        size = int(self.sy * 0.36 / max(1, len(word)) * 3.2 * pop)
        size = max(10, min(size, int(self.sy * 0.42 * pop)))
        f = _font("Anton-Regular.ttf", size)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        tw = d.textlength(word, font=f)
        cx, cy = img.size[0] / 2, img.size[1] / 2 + dy
        d.text((cx - tw / 2, cy - size * 0.62), word, font=f, fill=(30, 18, 60, 255),
               stroke_width=max(2, size // 14), stroke_fill=(255, 255, 255, 255))
        layer = layer.rotate(-rot - 6, center=(cx, cy), resample=Image.BICUBIC)
        img.alpha_composite(layer)


def _fade(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return img
    a = img.getchannel("A").point(lambda v: int(v * alpha))
    img.putalpha(a)
    return img


class _CustomInk:
    """AI-drawn doodle (black ink on white) -> revealed along its own contours, recoloured as a sticker."""

    def __init__(self, png: Path, w: int, h: int, pad: float, ss: int = SS):
        self.ss = ss
        import cv2
        self.cv2 = cv2
        g = np.array(Image.open(png).convert("L"))
        ink = (g < 128).astype(np.uint8)
        inner_w, inner_h = int(w * self.ss * (1 - 2 * pad)), int(h * self.ss * (1 - 2 * pad))
        ys, xs = np.nonzero(ink)
        if len(xs) == 0:
            ink = np.zeros((inner_h, inner_w), np.uint8)
        else:
            ink = ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            k = min(inner_w / ink.shape[1], inner_h / ink.shape[0])
            ink = cv2.resize(ink * 255, (max(1, int(ink.shape[1] * k)), max(1, int(ink.shape[0] * k))),
                             interpolation=cv2.INTER_AREA)
            ink = (ink > 100).astype(np.uint8)
        self.W, self.H = w * self.ss, h * self.ss
        canvas = np.zeros((self.H, self.W), np.uint8)
        y0, x0 = (self.H - ink.shape[0]) // 2, (self.W - ink.shape[1]) // 2
        canvas[y0:y0 + ink.shape[0], x0:x0 + ink.shape[1]] = ink
        # thicken thin AI lines so they read as marker strokes
        dist = cv2.distanceTransform(canvas, cv2.DIST_L2, 3)
        half = max(1.0, float(dist.max()))
        target = max(3.0, min(self.W, self.H) * 0.018)
        if half < target:
            k = int(round(target - half))
            canvas = cv2.dilate(canvas, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
        self.ink = canvas
        cs, _ = cv2.findContours(canvas, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        cs = sorted([c[:, 0, :].astype(np.float32) for c in cs if len(c) > 8], key=lambda c: (c[:, 1].min(), c[:, 0].min()))
        self.paths = cs
        self.total = sum(len(c) for c in cs) or 1
        self.brush = int(max(4, half * 2.5 + 4))

    def frame(self, prog: float, boil: int, scale: float, rot: float) -> Image.Image:
        cv2 = self.cv2
        reveal = np.zeros_like(self.ink)
        budget = int(prog * self.total)
        for c in self.paths:
            if budget <= 0:
                break
            n = min(len(c), budget)
            cv2.polylines(reveal, [c[:n].astype(np.int32).reshape(-1, 1, 2)], False, 1, self.brush)
            budget -= n
        if prog >= 1.0:
            reveal[:] = 1
        ink = self.ink & reveal
        k = max(9, int(min(self.W, self.H) * 0.035)) | 1
        out_m = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        rgba = np.zeros((self.H, self.W, 4), np.uint8)
        rgba[out_m > 0] = (18, 16, 28, 230)
        rgba[ink > 0] = (255, 255, 255, 255)
        img = Image.fromarray(rgba, "RGBA")
        jit = np.random.default_rng(boil).uniform(-1.2, 1.2)
        img = img.rotate(-(rot + jit), resample=Image.BICUBIC, center=(self.W / 2, self.H / 2))
        if abs(scale - 1) > 0.01:
            sw, sh = max(1, int(self.W * scale)), max(1, int(self.H * scale))
            sm = img.resize((sw, sh), Image.LANCZOS)
            img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
            img.paste(sm, ((self.W - sw) // 2, (self.H - sh) // 2), sm)
        return img.resize((self.W // self.ss, self.H // self.ss), Image.LANCZOS)


def render_frames(ev: dict, w: int, h: int, dur: float, out_dir: Path, fps: int = DOODLE_FPS) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("f_*.png"):
        f.unlink()
    art = DoodleArt(ev, w, h, dur, seed=int(ev.get("id", 0)) + 3)
    n = max(1, int(math.ceil(dur * fps)))
    for i in range(n):
        art.frame(i / fps).save(out_dir / f"f_{i:04d}.png", compress_level=1)
    return n


# ================================================================== placement
def _rect_overlap(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def place(ev: dict, W: int, H: int, face, busy: list[tuple], caption_band: tuple[float, float]) -> dict | None:
    """Choose the doodle box (x, y, w, h) in output pixels. face = (cx, cy, size_px) or None.

    Checks use the visible art (the box minus its transparent padding): it must stay inside the frame, off the
    face core (eyes to chin), out of the caption band and away from callouts."""
    portrait = H > W
    shape = ev.get("shape", "stars")
    aspect = ds.SHAPES[shape]().aspect if shape in ds.SHAPES else 1.0
    art_h = (0.15 * H) if portrait else (0.25 * H)
    margin = 0.03 * min(W, H)
    pad = DoodleArt.PAD
    core = None
    if face:
        fx, fy, fs = face
        fs = max(fs, 0.06 * H)
        core = (fx - fs * 0.55, fy - fs * 0.62, fx + fs * 0.55, fy + fs * 0.72)
    anchor = ev.get("anchor", "free")
    if not face and anchor != "free":
        anchor = "free"

    def art_box(cx, cy, aw, ah):
        return (cx - aw / 2, cy - ah / 2, cx + aw / 2, cy + ah / 2)

    def ok(a) -> bool:
        if a[0] < margin or a[1] < margin or a[2] > W - margin or a[3] > H - margin:
            return False
        area = (a[2] - a[0]) * (a[3] - a[1])
        if core and _rect_overlap(a, core) > 0.04 * area:
            return False
        if _rect_overlap(a, (0, caption_band[0], W, caption_band[1])) > 0.05 * area:
            return False
        return all(_rect_overlap(a, o) <= 0.0 for o in busy)

    def result(a, flip=False) -> dict:
        aw, ah = a[2] - a[0], a[3] - a[1]
        bw, bh = aw / (1 - 2 * pad), ah / (1 - 2 * pad)
        cx, cy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
        return {"x": int(cx - bw / 2), "y": int(cy - bh / 2), "w": int(bw) // 2 * 2, "h": int(bh) // 2 * 2,
                "flip": flip}

    if anchor == "around_head":
        fx, fy, fs = face
        ah = min(max(fs * 2.3, art_h), H * 0.9)
        a = art_box(fx, fy - fs * 0.05, ah, ah)
        return result(a)
    cands: list[tuple[float, tuple, bool]] = []
    for sc in (1.0, 0.85, 0.72, 0.6, 0.5):
        ah = art_h * sc
        aw = ah * aspect
        if aw > W * 0.42:
            aw = W * 0.42
            ah = aw / aspect
        if face:
            fx, fy, fs = face
            top = fy - fs * 0.62
            if anchor == "above_head":
                for dx in (0.0, -0.3, 0.3):
                    cands.append((sc * 10 - abs(dx) * 2, art_box(fx + dx * fs, top - ah * 0.55, aw, ah), False))
            if anchor in ("side_head", "above_head"):
                left_room, right_room = fx - fs * 0.55, W - (fx + fs * 0.55)
                for side in sorted((-1, 1), key=lambda sd: -(right_room if sd > 0 else left_room)):
                    for dy in (-0.55, -0.2, 0.15):
                        c = (fx + side * (fs * 0.55 + aw * 0.55), fy + dy * fs)
                        cands.append((sc * 8 - abs(dy) * 2, art_box(*c, aw, ah), side < 0))
                    # tucked into the upper corner next to the head
                    c = (fx + side * (fs * 0.45 + aw * 0.45), top - ah * 0.15)
                    cands.append((sc * 8 - 0.5, art_box(*c, aw, ah), side < 0))
        for gx in np.linspace(0.1, 0.9, 9):
            for gy in np.linspace(0.1, 0.86, 9):
                cx, cy = gx * W, gy * H
                score = sc * 6 - gy * 1.2
                if face:
                    d = math.hypot(cx - face[0], cy - face[1]) / max(1.0, face[2])
                    score -= abs(d - 1.3) * 0.9
                cands.append((score - 3 * (anchor != "free"), art_box(cx, cy, aw, ah),
                              cx < (face[0] if face else W / 2)))
    cands.sort(key=lambda c: -c[0])
    for _, a, fl in cands:
        if ok(a):
            return result(a, fl if ev.get("shape") == "arrow_me" else False)
    return None


def callout_box(text: str, W: int, H: int) -> tuple[float, float, float, float]:
    """Where libass draws a callout (mirrors captions.build_ass 'Callout' style geometry)."""
    portrait = H > W
    call_size = int(W * (0.095 if portrait else 0.058))
    margin = int(H * (0.17 if portrait else 0.07))
    pad = max(6, int(call_size * 0.18))
    f = _font("Anton-Regular.ttf", 100)
    a, d = f.getmetrics()
    k = call_size / max(1, a + d)
    tw = f.getlength(text.upper()) * k + len(text)
    cx = W / 2
    return cx - tw / 2 - pad, margin - pad, cx + tw / 2 + pad, margin + call_size + pad


# ============================================================== AI doodles
def ai_prompt(subject: str) -> str:
    return (f"a single simple hand-drawn doodle icon of {subject}, thick black marker line art, cute minimal "
            f"sketch, centered, plain white background, no shading, no color, no text")


def generate_custom(doodles: list[dict], log: Log, progress: Callable[[float], None] | None = None) -> None:
    """FLUX draws each 'custom' doodle once; results are cached by prompt."""
    todo = [d for d in doodles if d.get("shape") == "custom" and d.get("prompt") and not
            (d.get("file") and Path(d["file"]).exists())]
    if not todo:
        return
    from .broll.generate import ImageGen, available
    okk, why = available()
    if not okk:
        log(f"AI doodles skipped: {why}")
        for d in todo:
            d.update({"shape": "sparkle", "kind": "magic"})
        return
    cache = CACHE / "doodles"
    cache.mkdir(parents=True, exist_ok=True)
    gen = ImageGen(log=log)
    try:
        for i, d in enumerate(todo):
            key = hashlib.sha1(d["prompt"].lower().encode()).hexdigest()[:16]
            raw = cache / f"{key}_raw.png"
            ink = cache / f"{key}.png"
            if not ink.exists():
                gen.generate(ai_prompt(d["prompt"]), raw.with_suffix(".jpg"), width=512, height=512, seed=7)
                _clean_ink(raw.with_suffix(".jpg"), ink)
            d["file"] = str(ink)
            log(f"AI doodle ready: {d['prompt']}")
            if progress:
                progress((i + 1) / len(todo))
    except Exception as e:  # noqa: BLE001
        log(f"AI doodle generation failed ({e}); using built-in doodles")
        for d in todo:
            if not d.get("file"):
                d.update({"shape": "sparkle", "kind": "magic"})
    finally:
        gen.close()


def _clean_ink(src: Path, dst: Path) -> None:
    import cv2
    g = np.array(Image.open(src).convert("L"))
    g = cv2.GaussianBlur(g, (3, 3), 0)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(th, 8)
    keep = np.zeros_like(th)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 40:
            keep[lab == i] = 255
    Image.fromarray(255 - keep).save(dst)


# ============================================================ subject outline
_SEG = None


def _seg_session(log: Log):
    global _SEG
    if _SEG is None:
        from rembg import new_session
        for name in ("u2net_human_seg", "u2netp"):
            try:
                _SEG = new_session(name)
                log(f"Subject outline: using {name}")
                break
            except Exception as e:  # noqa: BLE001
                log(f"segmentation model {name} unavailable: {e}")
        if _SEG is None:
            _SEG = False
    return _SEG


def render_outline_frames(base: Path, t_base: float, dur: float, bw: int, bh: int, out_dir: Path, log: Log,
                          color=(255, 255, 255)) -> int:
    """Animated sticker-outline around the person, in BASE-video coordinates (half resolution)."""
    import cv2
    from rembg import remove

    from ..core.ffmpeg import iter_frames
    sess = _seg_session(log)
    if not sess:
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("f_*.png"):
        f.unlink()
    W, H = bw // 2, bh // 2
    aw = 384
    n = 0
    lw = max(3, int(H * 0.010))
    for i, (_, fr) in enumerate(iter_frames(base, fps=BOIL_FPS, width=aw, start=t_base, duration=dur)):
        t = i / BOIL_FPS
        img = Image.fromarray(fr[:, :, ::-1])
        m = np.array(remove(img, session=sess, only_mask=True))
        m = (m > 127).astype(np.uint8)
        cnt, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        if cnt > 1:
            big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            m = (lab == big).astype(np.uint8)
        frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if m.mean() > 0.01:
            mm = cv2.resize(m * 255, (W, H), interpolation=cv2.INTER_LINEAR)
            mm = cv2.GaussianBlur(mm, (0, 0), 3)
            mm = (mm > 127).astype(np.uint8)
            off = max(2, int(H * 0.012))
            mm = cv2.dilate(mm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * off + 1, 2 * off + 1)))
            cs, _ = cv2.findContours(mm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            if cs:
                c = max(cs, key=cv2.contourArea)[:, 0, :].astype(np.float32)
                c = _smooth_closed(c, 9)
                prog = min(1.0, t / 0.45)
                alpha = 1.0 if t < dur - 0.25 else max(0.0, (dur - t) / 0.25)
                frame = _draw_outline(c, W, H, prog, lw, color, alpha, i)
        frame.save(out_dir / f"f_{n:04d}.png", compress_level=1)
        n += 1
    return n


def _smooth_closed(c: np.ndarray, k: int) -> np.ndarray:
    if len(c) < k * 2:
        return c
    pad = np.vstack([c[-k:], c, c[:k]])
    ker = np.ones(k) / k
    xs = np.convolve(pad[:, 0], ker, "same")[k:-k]
    ys = np.convolve(pad[:, 1], ker, "same")[k:-k]
    return np.stack([xs, ys], 1)


def _draw_outline(c: np.ndarray, W: int, H: int, prog: float, lw: int, color, alpha: float, seed: int) -> Image.Image:
    # start at the top of the head and draw both ways at once
    top = int(np.argmin(c[:, 1]))
    c = np.roll(c, -top, axis=0)
    n = len(c)
    half = int(n / 2 * prog)
    keep = np.zeros(n, bool)
    keep[:half + 1] = True
    keep[n - half:] = True
    edge = (c[:, 0] < 3) | (c[:, 0] > W - 4) | (c[:, 1] > H - 4) | (c[:, 1] < 3)
    keep &= ~edge
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rng = np.random.default_rng(seed)
    cj = c + rng.normal(0, 0.6, c.shape)
    runs, cur = [], []
    for i in range(n):
        if keep[i]:
            cur.append(tuple(cj[i]))
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    if len(runs) > 1 and keep[0] and keep[-1]:
        runs[0] = runs[-1] + runs[0]
        runs.pop()
    for r in runs:
        if len(r) > 1:
            d.line(r, fill=(*color, 255), width=lw, joint="curve")
    glow = layer.filter(ImageFilter.GaussianBlur(lw * 1.6))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(_fade(glow, 0.9))
    out.alpha_composite(layer)
    return _fade(out, alpha)


def ensure_models(log: Log = print) -> None:
    """Installer hook: download the small person-segmentation model used by the outline effect."""
    _seg_session(log)
