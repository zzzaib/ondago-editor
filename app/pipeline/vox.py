"""Vox-style explainer graphics, drawn frame by frame with Pillow (offline, CPU, any computer).

The look: warm paper with grain that "boils", everything animated at 12 frames per second (the cut-out stop-motion
stutter), bold condensed headlines that drop in word by word, a yellow highlighter swiping across key words,
numbers that count up next to a dot chart or bars, a world map that pushes in to a country, timelines, and photos
as paper clippings with tape. Each graphic is a PNG sequence that render.py lays over the edit like B-roll; the
whip-pan in and out (with motion blur) is baked into the first and last frames.

kinds: headline, quote, number, map, timeline, photo
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from ..core.paths import FONTS

VOX_FPS = 12
KINDS = ("headline", "quote", "number", "map", "timeline", "photo")
DUR = {"headline": 3.2, "quote": 4.4, "number": 3.8, "map": 4.6, "timeline": 5.0, "photo": 3.4}

PAPER = (238, 231, 214)
SHEET = (251, 248, 241)
INK = (30, 27, 25)
MUTE = (112, 102, 90)
LAND = (214, 203, 180)
LAND_EDGE = (236, 229, 212)
DOT = (221, 211, 190)
YELLOW = (255, 221, 58)
RED = (214, 52, 42)

SERIF = "LibreBaskerville.ttf"
SERIF_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/librebaskerville/LibreBaskerville%5Bwght%5D.ttf"
WORLD = Path(__file__).parent / "assets" / "world_110m.json"

STOP = set("""a an the and or but of in on at for with from by is are was were be to it this that these those its as
into over than so very just""".split())


# ================================================================== basics
def ensure_fonts(log=None) -> None:
    """The serif used for quotes / labels is small (170 KB) and downloaded once if the installer didn't."""
    p = FONTS / SERIF
    if p.exists():
        return
    try:
        import requests
        r = requests.get(SERIF_URL, timeout=20)
        if r.ok and len(r.content) > 50_000:
            FONTS.mkdir(parents=True, exist_ok=True)
            p.write_bytes(r.content)
    except Exception as e:  # noqa: BLE001
        log and log(f"Explainer serif font not available ({e}); using Poppins")


_fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(kind: str, size: float) -> ImageFont.FreeTypeFont:
    """display = Anton (condensed caps), serif / serif_bold = Libre Baskerville, sans = Poppins."""
    size = max(8, int(size))
    key = (kind, size)
    if key in _fonts:
        return _fonts[key]
    f = None
    try:
        if kind == "display" and (FONTS / "Anton-Regular.ttf").exists():
            f = ImageFont.truetype(str(FONTS / "Anton-Regular.ttf"), size)
        elif kind.startswith("serif") and (FONTS / SERIF).exists():
            f = ImageFont.truetype(str(FONTS / SERIF), size)
            try:
                f.set_variation_by_name("Bold" if kind == "serif_bold" else "Regular")
            except Exception:  # noqa: BLE001
                pass
        if f is None:
            name = "Poppins-Bold.ttf" if kind in ("serif_bold", "display") else "Poppins-SemiBold.ttf"
            f = ImageFont.truetype(str(FONTS / name), size)
    except Exception:  # noqa: BLE001
        f = ImageFont.load_default(size)
    _fonts[key] = f
    return f


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out(x: float) -> float:
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_io(x: float) -> float:
    x = clamp01(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def back(x: float) -> float:
    x = clamp01(x)
    c1 = 1.9
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def prog(t: float, a: float, d: float) -> float:
    return clamp01((t - a) / max(1e-6, d))


def jit(frame: int, key: str, amp: float = 1.6) -> tuple[float, float]:
    """Tiny per-frame wobble: the hand-made 'boil' of stop-motion graphics."""
    h = hashlib.md5(f"{frame}|{key}".encode()).digest()
    return (h[0] / 255 - 0.5) * 2 * amp, (h[1] / 255 - 0.5) * 2 * amp


def rnd(seed: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.md5(seed.encode()).hexdigest()[:8], 16))


def text_w(f: ImageFont.FreeTypeFont, s: str) -> float:
    return f.getlength(s)


def cap(f: ImageFont.FreeTypeFont) -> float:
    """Height of a capital letter above the baseline (layout uses baselines, so fonts never overlap)."""
    try:
        return float(-f.getbbox("H", anchor="ls")[1])
    except Exception:  # noqa: BLE001
        return f.size * 0.72


def wrap(words: list[str], f: ImageFont.FreeTypeFont, max_w: float) -> list[list[str]]:
    lines: list[list[str]] = []
    cur: list[str] = []
    for w in words:
        if cur and text_w(f, " ".join(cur + [w])) > max_w:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def fit(words: list[str], kind: str, max_w: float, max_h: float, max_lines: int, start: float,
        lead: float = 1.08) -> tuple[ImageFont.FreeTypeFont, list[list[str]]]:
    size = start
    while size > 12:
        f = font(kind, size)
        lines = wrap(words, f, max_w)
        if len(lines) <= max_lines and len(lines) * size * lead <= max_h and \
                all(text_w(f, " ".join(ln)) <= max_w for ln in lines):
            return f, lines
        size *= 0.93
    f = font(kind, 12)
    return f, wrap(words, f, max_w)


# ================================================================== paper
_paper: dict[tuple, Image.Image] = {}


def paper(w: int, h: int, variant: int = 0, tone: tuple = PAPER) -> Image.Image:
    """Warm paper: soft blotches + fine grain + a few fibres + a gentle vignette. 3 variants per size so the
    grain changes from frame to frame (the 'boil')."""
    key = (w, h, variant % 3, tone)
    if key in _paper:
        return _paper[key]
    g = rnd(f"paper{w}x{h}{variant % 3}{tone}")

    def noise(div: int, amp: float) -> np.ndarray:
        sm = g.normal(0, 1, (max(2, h // div), max(2, w // div))).astype(np.float32)
        return np.asarray(Image.fromarray(sm, "F").resize((w, h), Image.BICUBIC)) * amp

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    vig = ((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) * -34
    lum = noise(60, 5.0) + noise(9, 2.2) + g.normal(0, 1, (h, w)).astype(np.float32) * 3.4 + vig
    arr = np.clip(np.array(tone, np.float32)[None, None, :] + lum[..., None] * np.array([1, 0.97, 0.9], np.float32),
                  0, 255).astype(np.uint8)
    im = Image.fromarray(arr, "RGB")
    d = ImageDraw.Draw(im, "RGBA")
    for _ in range(int(w * h / 14000)):
        x, y = g.uniform(0, w), g.uniform(0, h)
        a, ln = g.uniform(0, math.pi), g.uniform(6, 28) * max(w, h) / 1920
        c = int(g.uniform(90, 150))
        d.line([(x, y), (x + math.cos(a) * ln, y + math.sin(a) * ln)], fill=(c, c - 8, c - 20, int(g.uniform(10, 26))),
               width=1)
    _paper[key] = im
    return im


def textured(w: int, h: int, tone: tuple, seed: str, amp: float = 3.0) -> Image.Image:
    g = rnd(seed)
    arr = np.clip(np.array(tone, np.float32)[None, None, :] + g.normal(0, amp, (h, w, 1)).astype(np.float32), 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def shadow(alpha: Image.Image, blur: float, strength: int = 110) -> Image.Image:
    """Soft drop shadow (blurred at quarter size - looks the same, ~10x quicker)."""
    q = 4 if min(alpha.size) > 200 else 1
    small = alpha.resize((max(1, alpha.width // q), max(1, alpha.height // q)), Image.BILINEAR)
    sh = small.point(lambda v: v * strength // 255).filter(ImageFilter.GaussianBlur(max(0.5, blur / q)))
    sh = sh.resize(alpha.size, Image.BILINEAR)
    out = Image.new("RGBA", alpha.size, (40, 28, 12, 0))
    out.putalpha(sh)
    return out


def torn_mask(w: int, h: int, seed: str, edges: str = "tblr", amp: float = 0.012) -> Image.Image:
    """Rectangle with ragged, hand-torn edges."""
    g = rnd(seed)
    a = max(2.0, min(w, h) * amp)
    pts = []

    def edge(p0, p1, rough):
        n = max(8, int(math.dist(p0, p1) / 14))
        for i in range(n):
            f = i / n
            x, y = p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f
            if rough:
                dx, dy = (p1[1] - p0[1]), -(p1[0] - p0[0])
                L = math.hypot(dx, dy) or 1
                k = g.normal(0, a) - a
                x, y = x + dx / L * k, y + dy / L * k
            pts.append((x, y))

    edge((0, 0), (w, 0), "t" in edges)
    edge((w, 0), (w, h), "r" in edges)
    edge((w, h), (0, h), "b" in edges)
    edge((0, h), (0, 0), "l" in edges)
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m


def marker_poly(x0: float, y0: float, x1: float, y1: float, seed: str) -> list[tuple[float, float]]:
    """A highlighter stroke: slightly slanted, wobbly top and bottom, brushy ends."""
    g = rnd(seed)
    h = y1 - y0
    n = max(3, int((x1 - x0) / max(8.0, h * 0.6)))
    top = [(x0 + (x1 - x0) * i / n, y0 + g.normal(0, h * 0.04) - h * 0.03 * i / n) for i in range(n + 1)]
    bot = [(x0 + (x1 - x0) * i / n, y1 + g.normal(0, h * 0.04) - h * 0.03 * i / n) for i in range(n, -1, -1)]
    cap_r = [(x1 + h * 0.10, y0 + h * 0.3), (x1 + h * 0.16, y0 + h * 0.6), (x1 + h * 0.08, y1 - h * 0.05)]
    cap_l = [(x0 - h * 0.08, y1 - h * 0.1), (x0 - h * 0.14, y0 + h * 0.45), (x0 - h * 0.06, y0 + h * 0.1)]
    return top + cap_r + bot + cap_l


def multiply_layer(base: Image.Image, polys: list[list[tuple[float, float]]], color=YELLOW) -> Image.Image:
    if not polys:
        return base
    lay = Image.new("RGB", base.size, (255, 255, 255))
    d = ImageDraw.Draw(lay)
    for p in polys:
        if len(p) >= 3:
            d.polygon(p, fill=color)
    rgb = ImageChops.multiply(base.convert("RGB"), lay)
    if base.mode == "RGBA":
        rgb = rgb.convert("RGBA")
        rgb.putalpha(base.getchannel("A"))
    return rgb


def rough_line(d: ImageDraw.ImageDraw, pts: list[tuple[float, float]], width: float, fill, seed: str,
               wob: float = 1.2) -> None:
    g = rnd(seed)
    out = []
    for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
        n = max(2, int(math.dist((xa, ya), (xb, yb)) / 18))
        for i in range(n):
            f = i / n
            out.append((xa + (xb - xa) * f + g.normal(0, wob), ya + (yb - ya) * f + g.normal(0, wob)))
    out.append(pts[-1])
    d.line(out, fill=fill, width=max(1, int(width)), joint="curve")


# ================================================================ context
class Ctx:
    """Canvas geometry + timing for one graphic."""

    def __init__(self, w: int, h: int, dur: float, bottom: float, seed: str):
        self.w, self.h, self.dur, self.seed = w, h, dur, seed
        self.portrait = h > w * 1.15
        mx = w * (0.07 if not self.portrait else 0.08)
        top = h * (0.09 if not self.portrait else 0.10)
        self.box = (mx, top, w - mx, h * bottom)
        self.u = min(w, h)  # size unit

    @property
    def bw(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def bh(self) -> float:
        return self.box[3] - self.box[1]

    def bg(self, frame: int) -> Image.Image:
        return paper(self.w, self.h, frame).copy().convert("RGBA")


# =============================================================== headline
def _headline(c: Ctx, t: float, fr: int, s: dict) -> Image.Image:
    im = c.bg(fr)
    words = [w for w in re.sub(r"\s+", " ", str(s.get("text", ""))).upper().split() if w][:9] or ["WATCH", "THIS"]
    f, lines = fit(words, "display", c.bw * 0.94, c.bh * 0.70, 3 if not c.portrait else 4,
                   c.bh * (0.30 if not c.portrait else 0.22), 1.12)
    ch = cap(f)
    pitch = ch * 1.30
    detail = str(s.get("detail", "") or "").strip()
    fd, dl = (fit(detail.split(), "serif", c.bw * 0.86, ch * 1.2, 2, max(ch * 0.30, c.u * (0.046 if c.portrait else 0.034)))) if detail \
        else (None, [])
    dpitch = cap(fd) * 1.9 if fd else 0
    block_h = ch + pitch * (len(lines) - 1) + (ch * 0.55 + cap(fd) + dpitch * (len(dl) - 1) if dl else 0)
    base = c.box[1] + (c.bh - block_h) / 2 + ch  # first baseline
    key = s.get("key") or max(words, key=lambda w: (len(w) > 3 and w.lower() not in STOP, len(w)))
    pos = []  # (word, x, baseline, width)
    y = base
    for ln in lines:
        lw = text_w(f, " ".join(ln))
        x = c.box[0] + (c.bw - lw) / 2
        for w in ln:
            pos.append((w, x, y, text_w(f, w)))
            x += text_w(f, w + " ")
        y += pitch
    step = min(0.16, 1.1 / max(1, len(pos)))
    last = 0.12 + step * (len(pos) - 1)
    x0 = min(p[1] for p in pos)
    bar = ease_out(prog(t, 0.0, 0.35)) * c.bw * 0.12
    d = ImageDraw.Draw(im)
    by = base - ch - max(8, ch * 0.35)
    if bar > 2:
        d.rectangle([x0, by, x0 + bar, by + max(5, ch * 0.09)], fill=RED)
    hp = ease_out(prog(t, last + 0.25, 0.32))
    polys = []
    for i, (w, x, yb, ww) in enumerate(pos):
        if w == key and hp > 0:
            dx, dy = jit(fr, f"hl{i}", 1.0)
            polys.append(marker_poly(x - ch * 0.08 + dx, yb - ch * 0.92 + dy,
                                     x - ch * 0.08 + (ww + ch * 0.16) * hp + dx, yb + ch * 0.10 + dy, f"{c.seed}k"))
    im = multiply_layer(im, polys)
    lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for i, (w, x, yb, ww) in enumerate(pos):
        p = prog(t, 0.12 + step * i, 0.17)
        if p <= 0:
            continue
        dx, dy = jit(fr, f"w{i}", 1.4)
        drop = (1 - ease_out(p)) * -ch * 0.35
        d.text((x + dx, yb + dy + drop), w, font=f, fill=INK + (int(255 * min(1, p * 1.6)),), anchor="ls")
    if dl:
        p = prog(t, last + 0.35, 0.3)
        yy = pos[-1][2] + ch * 0.55 + cap(fd)
        for ln in dl:
            s2 = " ".join(ln)
            dx, dy = jit(fr, "det", 1.0)
            d.text((c.box[0] + (c.bw - text_w(fd, s2)) / 2 + dx, yy + dy), s2, font=fd, fill=MUTE + (int(255 * p),),
                   anchor="ls")
            yy += dpitch
    im.alpha_composite(lay)
    return im


# ================================================================== quote
def _find_span(words: list[str], phrase: str) -> tuple[int, int]:
    def nm(x):
        return re.sub(r"[^\w]", "", x.lower())
    ph = [nm(x) for x in phrase.split() if nm(x)]
    ws = [nm(x) for x in words]
    if ph:
        for i in range(len(ws)):
            if ws[i:i + len(ph)] == ph:
                return i, i + len(ph)
        for n in range(len(ph) - 1, 1, -1):  # partial match: longest run of the phrase
            for k in range(len(ph) - n + 1):
                sub = ph[k:k + n]
                for i in range(len(ws)):
                    if ws[i:i + n] == sub:
                        return i, i + n
    content = [i for i, w in enumerate(ws) if w and w not in STOP and len(w) > 3]
    if not content:
        return 0, min(3, len(words))
    mid = content[len(content) // 2]
    return max(0, mid - 1), min(len(words), mid + 2)


@lru_cache(maxsize=16)
def _quote_sheet(w: int, h: int, sentence: str, phrase: str, portrait: bool, seed: str):
    """Static parts of the quote card: the sheet, its text layer and where the highlight goes."""
    words = sentence.split()[:46]
    sw = int(w * (0.80 if not portrait else 0.90))
    pad = sw * 0.075
    f, lines = fit(words, "serif", sw - 2 * pad, h * (0.62 if not portrait else 0.60), 7 if not portrait else 10,
                   h * (0.095 if not portrait else 0.06), 1.55)
    lead = f.size * 1.55
    head = f.size * 1.9
    sh = int(pad * 1.1 + head + lead * len(lines) + pad * 0.6)
    sheet = textured(sw, sh, SHEET, seed + "sheet", 2.4).convert("RGBA")
    d = ImageDraw.Draw(sheet)
    qf = font("serif_bold", f.size * 2.6)
    d.text((pad * 0.75, pad * 0.35), "“", font=qf, fill=RED)
    for i in range(3):  # faint ruled lines at the top, like a page
        yy = pad * 0.55 + i * f.size * 0.32
        d.line([(pad * 1.9, yy), (sw - pad, yy)], fill=(214, 206, 192), width=max(1, int(f.size * 0.06)))
    txt = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    dt = ImageDraw.Draw(txt)
    a, b = _find_span(words, phrase)
    boxes = []
    k = 0
    y = pad * 1.1 + head - f.size
    for ln in lines:
        x = pad
        for wd in ln:
            ww = text_w(f, wd)
            dt.text((x, y), wd, font=f, fill=INK)
            if a <= k < b:
                boxes.append((x, y, ww))
            x += text_w(f, wd + " ")
            k += 1
        y += lead
    mask = torn_mask(sw, sh, seed + "tear", "b", 0.010)
    return sheet, txt, boxes, f.size, mask


def _quote(c: Ctx, t: float, fr: int, s: dict) -> Image.Image:
    im = c.bg(fr)
    sentence = str(s.get("detail") or s.get("text") or "").strip().strip('"')
    sheet, txt, boxes, fs, mask = _quote_sheet(int(c.w), int(c.bh), sentence, str(s.get("text", "")), c.portrait,
                                               c.seed)
    sh = sheet.copy()
    # highlight swipes across the phrase, word by word, line by line
    hp = prog(t, 0.75, 0.25 + 0.12 * len(boxes))
    if hp > 0 and boxes:
        rows: dict[float, list] = {}
        for x, y, ww in boxes:
            rows.setdefault(round(y), []).append((x, ww))
        total = sum(ww for _, _, ww in boxes)
        left = total * ease_io(hp)
        polys = []
        for i, (y, items) in enumerate(sorted(rows.items())):
            x0, x1 = items[0][0], items[-1][0] + items[-1][1]
            span = x1 - x0
            if left <= 0:
                break
            use = min(span, left)
            left -= span
            polys.append(marker_poly(x0 - fs * 0.1, y + fs * 0.05, x0 + use + fs * 0.05, y + fs * 1.32,
                                     f"{c.seed}q{i}"))
        sh = multiply_layer(sh, polys)
    sh.alpha_composite(txt)
    sh.putalpha(mask)
    # slow push in toward the highlighted words
    z = 1.0 + 0.07 * ease_io(t / c.dur)
    sw, shh = int(sh.width * z), int(sh.height * z)
    sh2 = sh.resize((sw, shh), Image.BICUBIC).rotate(-1.4, resample=Image.BICUBIC, expand=True)
    cx = c.w / 2
    cy = c.box[1] + c.bh / 2
    if boxes:
        hx = (boxes[0][0] + boxes[-1][0] + boxes[-1][2]) / 2 * z - sw / 2
        hy = (boxes[0][1] + boxes[-1][1]) / 2 * z - shh / 2
        k = 0.35 * ease_io(t / c.dur)
        cx, cy = cx - hx * k, cy - hy * k
    dx, dy = jit(fr, "sheet", 1.2)
    x, y = int(cx - sh2.width / 2 + dx), int(cy - sh2.height / 2 + dy)
    rise = (1 - ease_out(prog(t, 0, 0.3))) * c.h * 0.05
    sd = shadow(sh2.getchannel("A"), c.u * 0.012)
    im.alpha_composite(sd, (x + int(c.u * 0.006), int(y + c.u * 0.014 + rise)))
    im.alpha_composite(sh2, (x, int(y + rise)))
    return im


# ================================================================= number
NUM_RE = re.compile(r"^(\D*?)(\d[\d,]*(?:\.\d+)?)(.*)$")


def parse_number(text: str) -> tuple[str, float, int, bool, str] | None:
    m = NUM_RE.match(str(text).strip())
    if not m:
        return None
    pre, num, suf = m.group(1).strip(), m.group(2), m.group(3).strip()
    try:
        val = float(num.replace(",", ""))
    except ValueError:
        return None
    dec = len(num.split(".")[1]) if "." in num else 0
    return pre, val, dec, "," in num or (val >= 10000 and dec == 0), suf


def label_ok(s: dict) -> bool:
    return bool(str(s.get("detail") or "").strip())


def _fmt(v: float, dec: int, commas: bool) -> str:
    return f"{v:,.{dec}f}" if commas else f"{v:.{dec}f}"


def _number(c: Ctx, t: float, fr: int, s: dict) -> Image.Image:
    im = c.bg(fr)
    pn = parse_number(s.get("text", ""))
    if not pn:
        return _headline(c, t, fr, s)
    pre, val, dec, commas, suf = pn
    sl = suf.lower()
    pct = sl.startswith("%") or sl.startswith("percent")
    times = bool(re.match(r"^(x|×|times)\b", sl)) or sl in ("x", "×")
    visual = ("grid" if pct and 0 < val <= 100 else "bars" if times and 1 < val <= 1000 else "")
    word_suf = "" if pct or times else suf
    inline = "%" if pct else "×" if times else ""
    # layout: number block + optional visual
    if visual and not c.portrait:
        nb = (c.box[0], c.box[1], c.box[0] + c.bw * 0.50, c.box[3])
        vb = (c.box[0] + c.bw * 0.56, c.box[1] + c.bh * 0.08, c.box[2], c.box[3] - c.bh * 0.08)
    elif visual:
        nb = (c.box[0], c.box[1], c.box[2], c.box[1] + c.bh * 0.48)
        vb = (c.box[0] + c.bw * 0.10, c.box[1] + c.bh * 0.54, c.box[2] - c.bw * 0.10, c.box[3])
    else:
        nb, vb = c.box, None
    nbw, nbh = nb[2] - nb[0], nb[3] - nb[1]
    full = f"{pre}{_fmt(val, dec, commas)}{inline}"
    f = font("display", nbh * (0.52 if word_suf else 0.66))
    limit = nbh * (0.36 if word_suf or label_ok(s) else 0.5)  # leave room for the unit and the label
    while (text_w(f, full) > nbw * 0.94 or cap(f) > limit) and f.size > 20:
        f = font("display", f.size * 0.93)
    ch = cap(f)
    cnt = ease_out(prog(t, 0.15, 1.0))
    cur = val * cnt
    shown = f"{pre}{_fmt(cur if dec else math.floor(cur + 1e-9), dec, commas)}"
    label = str(s.get("detail") or "").strip()
    fl, ll = fit(label.split(), "serif", nbw * 0.92, ch * 0.9, 2, max(c.u * (0.048 if c.portrait else 0.036), ch * 0.2)) if label else (None, [])
    fs = font("display", f.size * 0.34)
    chs = cap(fs)
    lp_ = cap(fl) * 1.85 if fl else 0
    gap = ch * 0.30
    h_total = ch + (gap + chs if word_suf else 0) + (gap * 0.8 + cap(fl) + lp_ * (len(ll) - 1) if ll else 0)
    y = nb[1] + (nbh - h_total) / 2 + ch  # baseline of the figure
    lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    tw = text_w(f, full)
    x = nb[0] + (nbw - tw) / 2
    dx, dy = jit(fr, "num", 1.6)
    pop = back(prog(t, 0.0, 0.3))
    a = int(255 * min(1, pop * 1.5))
    yo = (1 - pop) * ch * 0.25
    d.text((x + dx, y + dy + yo), shown, font=f, fill=INK + (a,), anchor="ls")
    if inline:
        d.text((x + text_w(f, shown) + dx, y + dy + yo), inline, font=f, fill=RED + (a,), anchor="ls")
    if not visual:  # red underline swipes under the figure
        up = ease_out(prog(t, 0.9, 0.35))
        if up > 0:
            uy = y + ch * 0.12
            rough_line(d, [(x, uy), (x + tw * up, uy - ch * 0.03)], max(4, ch * 0.06), RED + (255,), c.seed + "u", 1.0)
    if word_suf:
        p = prog(t, 0.5, 0.25)
        s2 = word_suf.upper()
        y += gap + chs
        d.text((nb[0] + (nbw - text_w(fs, s2)) / 2 + dx, y + dy), s2, font=fs, fill=RED + (int(255 * p),), anchor="ls")
    if ll:
        p = prog(t, 0.7, 0.3)
        y += gap * 0.8 + cap(fl)
        for ln in ll:
            s2 = " ".join(ln)
            d.text((nb[0] + (nbw - text_w(fl, s2)) / 2 + dx * 0.5, y + dy * 0.5), s2, font=fl,
                   fill=MUTE + (int(255 * p),), anchor="ls")
            y += lp_
    if visual == "grid":
        _dot_grid(d, vb, val * ease_out(prog(t, 0.25, 1.3)), fr, c.portrait)
    elif visual == "bars":
        _bars(d, vb, val, ease_out(prog(t, 0.3, 1.1)), fr)
    im.alpha_composite(lay)
    return im


def _dot_grid(d: ImageDraw.ImageDraw, vb, filled: float, fr: int, portrait: bool) -> None:
    cols, rows = 10, 10
    bw, bh = vb[2] - vb[0], vb[3] - vb[1]
    cell = min(bw / cols, bh / rows)
    ox = vb[0] + (bw - cell * cols) / 2
    oy = vb[1] + (bh - cell * rows) / 2
    r = cell * 0.36
    n = int(round(filled))
    for i in range(cols * rows):
        cx = ox + (i % cols + 0.5) * cell
        cy = oy + (i // cols + 0.5) * cell
        dx, dy = jit(fr, f"dot{i // 7}", 0.8)
        col = RED if i < n else DOT
        d.ellipse([cx - r + dx, cy - r + dy, cx + r + dx, cy + r + dy], fill=col + (255,))


def _bars(d: ImageDraw.ImageDraw, vb, val: float, p: float, fr: int) -> None:
    bw, bh = vb[2] - vb[0], vb[3] - vb[1]
    fl = font("display", bh * 0.16)
    lab_w = text_w(fl, f"{val:g}×") + bh * 0.06
    bar_h = bh * 0.2
    full = bw - lab_w
    for i, (lab, frac, col) in enumerate([("1×", 1 / val, DOT), (f"{val:g}×", 1.0, RED)]):
        y = vb[1] + bh * (0.22 + 0.36 * i)
        dx, dy = jit(fr, f"bar{i}", 1.0)
        L = max(bar_h * 0.3, full * frac * (p if i else min(1, p * 3)))
        d.text((vb[0] + dx, y + bar_h / 2 + dy), lab, font=fl, fill=INK + (255,), anchor="lm")
        d.rectangle([vb[0] + lab_w + dx, y + dy, vb[0] + lab_w + L + dx, y + bar_h + dy], fill=col + (255,))


# ==================================================================== map
ALIASES = {
    "usa": "United States of America", "us": "United States of America", "u.s.": "United States of America",
    "america": "United States of America", "united states": "United States of America",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "britain": "United Kingdom", "great britain": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom",
    "uae": "United Arab Emirates", "emirates": "United Arab Emirates", "dubai": "United Arab Emirates",
    "abu dhabi": "United Arab Emirates", "saudi": "Saudi Arabia", "ksa": "Saudi Arabia", "korea": "South Korea",
    "holland": "Netherlands", "burma": "Myanmar", "persia": "Iran", "bharat": "India", "hindustan": "India",
    "turkiye": "Turkey", "türkiye": "Turkey", "czechia": "Czechia", "swiss": "Switzerland",
    "drc": "Dem. Rep. Congo", "ivory coast": "Côte d'Ivoire",
    # big cities -> their country (the map shows countries)
    "mumbai": "India", "delhi": "India", "new delhi": "India", "bangalore": "India", "bengaluru": "India",
    "kolkata": "India", "chennai": "India", "hyderabad": "India", "karachi": "Pakistan", "lahore": "Pakistan",
    "islamabad": "Pakistan", "dhaka": "Bangladesh", "london": "United Kingdom", "paris": "France", "berlin": "Germany",
    "tokyo": "Japan", "beijing": "China", "shanghai": "China", "new york": "United States of America",
    "california": "United States of America", "silicon valley": "United States of America",
    "texas": "United States of America", "toronto": "Canada", "sydney": "Australia", "moscow": "Russia",
    "istanbul": "Turkey", "riyadh": "Saudi Arabia", "doha": "Qatar", "cairo": "Egypt", "lagos": "Nigeria",
    "nairobi": "Kenya", "seoul": "South Korea", "taipei": "Taiwan", "jakarta": "Indonesia", "manila": "Philippines",
    "bangkok": "Thailand", "kathmandu": "Nepal", "colombo": "Sri Lanka", "kabul": "Afghanistan",
    "tehran": "Iran", "madrid": "Spain", "rome": "Italy", "amsterdam": "Netherlands",
}
DISPLAY = {"United States of America": "United States", "Dem. Rep. Congo": "DR Congo"}
# (lat, lon) so a city gets its pin in the right place on its country
CITY_LL = {
    "mumbai": (19.08, 72.88), "delhi": (28.61, 77.21), "new delhi": (28.61, 77.21), "bangalore": (12.97, 77.59),
    "bengaluru": (12.97, 77.59), "kolkata": (22.57, 88.36), "chennai": (13.08, 80.27), "hyderabad": (17.39, 78.49),
    "karachi": (24.86, 67.01), "lahore": (31.55, 74.34), "islamabad": (33.68, 73.05), "dhaka": (23.81, 90.41),
    "london": (51.51, -0.13), "paris": (48.86, 2.35), "berlin": (52.52, 13.40), "tokyo": (35.68, 139.69),
    "beijing": (39.90, 116.40), "shanghai": (31.23, 121.47), "new york": (40.71, -74.01),
    "california": (36.78, -119.42), "silicon valley": (37.39, -122.06), "texas": (31.97, -99.90),
    "toronto": (43.65, -79.38), "sydney": (-33.87, 151.21), "moscow": (55.76, 37.62), "istanbul": (41.01, 28.98),
    "riyadh": (24.71, 46.68), "doha": (25.29, 51.53), "cairo": (30.04, 31.24), "lagos": (6.52, 3.38),
    "nairobi": (-1.29, 36.82), "seoul": (37.57, 126.98), "taipei": (25.03, 121.57), "jakarta": (-6.21, 106.85),
    "manila": (14.60, 120.98), "bangkok": (13.76, 100.50), "kathmandu": (27.72, 85.32), "colombo": (6.93, 79.86),
    "kabul": (34.56, 69.21), "tehran": (35.69, 51.39), "madrid": (40.42, -3.70), "rome": (41.90, 12.50),
    "amsterdam": (52.37, 4.90), "dubai": (25.20, 55.27), "abu dhabi": (24.45, 54.38), "scotland": (56.49, -4.20),
    "wales": (52.13, -3.78), "england": (52.36, -1.17),
}


@lru_cache(maxsize=1)
def world() -> list[dict]:
    try:
        return json.loads(WORLD.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


@lru_cache(maxsize=1)
def _name_index() -> dict[str, int]:
    idx = {}
    for i, c in enumerate(world()):
        for n in [c["n"]] + c.get("a", []):
            idx[n.lower()] = i
        idx[c["iso"].lower()] = idx.get(c["iso"].lower(), i)
    for k, v in ALIASES.items():
        j = idx.get(v.lower())
        if j is not None:
            idx[k] = j
    return idx


def find_country(place: str) -> dict | None:
    p = re.sub(r"\s+", " ", str(place or "").strip().lower()).strip(" .")
    if not p:
        return None
    p = re.sub(r"^the ", "", p)
    i = _name_index().get(p)
    return world()[i] if i is not None else None


def _main_ring(c: dict) -> list:
    def area(r):
        return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]))) / 2
    return max(c["r"], key=area)


@lru_cache(maxsize=6)
def _land_tex(w: int, h: int, fr: int) -> Image.Image:
    tex = paper(w, h, fr % 3 + 1).convert("RGB")
    return ImageOps.autocontrast(tex, cutoff=2).point(lambda v: 200 + v * 55 // 255)


def _map(c: Ctx, t: float, fr: int, s: dict) -> Image.Image:
    ctry = find_country(s.get("place") or s.get("text"))
    if not ctry:
        return _headline(c, t, fr, {**s, "text": s.get("place") or s.get("text") or "AROUND THE WORLD"})
    ring = _main_ring(ctry)
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    lon0, lat0 = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    k_lat = math.cos(math.radians(max(-70.0, min(70.0, lat0))))
    m_ = 2.3 if not c.portrait else 1.8
    span_end = max((max(xs) - min(xs)) * k_lat * m_, (max(ys) - min(ys)) * m_ * c.w / c.h, 14.0 if c.portrait else 16.0)
    span_end = min(span_end, 200.0)
    span_start = min(span_end * 4.2, 300.0)
    zp = ease_io(prog(t, 0.15, 1.6))
    span = math.exp(math.log(span_start) + (math.log(span_end) - math.log(span_start)) * zp)
    span *= 1.0 - 0.04 * prog(t, 1.75, c.dur)  # keeps drifting in slowly
    clon = lon0 * (0.55 + 0.45 * zp)
    clat = lat0 * (0.4 + 0.6 * zp)
    ss = 2  # supersample for smooth coastlines
    W, H = c.w * ss, c.h * ss
    cy_off = (c.box[1] + c.box[3]) / 2 / c.h  # centre of the free area (captions sit below)
    scale = W / span  # px per degree (x already multiplied by cos(lat))

    def proj(lon, lat):
        return ((lon - clon) * k_lat * scale + W / 2, (clat - lat) * scale + H * cy_off)

    land = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(land)
    edge = max(2, int(ss * c.u * 0.0016))
    for cc in world():
        if cc["iso"] == "ATA" and ctry["iso"] != "ATA":
            continue
        is_t = cc is ctry
        for r in cc["r"]:
            pts = [proj(x, y) for x, y in r]
            if max(p[0] for p in pts) < 0 or min(p[0] for p in pts) > W or max(p[1] for p in pts) < 0 \
                    or min(p[1] for p in pts) > H:
                continue
            if not is_t:
                d.polygon(pts, fill=LAND + (255,), outline=LAND_EDGE + (255,), width=edge)
    fillp = prog(t, 1.25, 0.34)
    tp = []
    for r in ctry["r"]:
        pts = [proj(x, y) for x, y in r]
        tp.append(pts)
        col = tuple(int(LAND[i] + (RED[i] - LAND[i]) * fillp) for i in range(3))
        d.polygon(pts, fill=col + (255,))
    op = prog(t, 1.15, 0.3)
    if op > 0:
        for i, pts in enumerate(tp):
            if len(pts) > 2:
                dx, dy = jit(fr, f"ol{i}", 1.5 * ss)
                d.line([(x + dx, y + dy) for x, y in pts + pts[:1]], fill=INK + (int(255 * op),),
                       width=max(2, int(ss * c.u * 0.0035)), joint="curve")
    land = land.reduce(ss) if land.size != (c.w, c.h) else land
    if land.size != (c.w, c.h):
        land = land.resize((c.w, c.h), Image.BILINEAR)
    im = c.bg(fr)
    # land gets the paper's texture too (multiply), with a soft drop shadow for the cut-out look
    sd = shadow(land.getchannel("A"), c.u * 0.006, 70)
    im.alpha_composite(sd, (int(c.u * 0.004), int(c.u * 0.007)))
    land_rgb = ImageChops.multiply(land.convert("RGB"), _land_tex(c.w, c.h, fr % 3))
    land_rgb = land_rgb.convert("RGBA")
    land_rgb.putalpha(land.getchannel("A"))
    im.alpha_composite(land_rgb)
    # label tag with a leader line
    lp = back(prog(t, 1.65, 0.3))
    if lp > 0:
        city = CITY_LL.get(re.sub(r"\s+", " ", str(s.get("place") or "").strip().lower()))
        lx, ly = proj(city[1], city[0]) if city else proj(*(ctry.get("lab") or (lon0, lat0)))
        lx, ly = lx / ss, ly / ss
        name = str(s.get("text") or DISPLAY.get(ctry["n"], ctry["n"])).upper()[:26]
        fl = font("display", c.u * (0.075 if not c.portrait else 0.062))
        tw = text_w(fl, name)
        pad = fl.size * 0.32
        bw_, bh_ = tw + pad * 2, fl.size * 1.35
        tx = min(max(c.box[0], lx + c.u * 0.08), c.box[2] - bw_)
        ty = min(max(c.box[1], ly - c.u * 0.20), c.box[3] - bh_)
        if tx < lx < tx + bw_ and ty < ly < ty + bh_:
            ty = max(c.box[1], ly - bh_ - c.u * 0.12)
        dx, dy = jit(fr, "tag", 1.4)
        lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d2 = ImageDraw.Draw(lay)
        ex, ey = tx + dx, ty + bh_ / 2 + dy
        mx_, my_ = lx + (ex - lx) * lp, ly + (ey - ly) * lp
        d2.line([(lx, ly), (mx_, my_)], fill=INK + (255,), width=max(2, int(c.u * 0.004)))
        rr = c.u * 0.011
        if city:
            d2.ellipse([lx - rr * 1.9, ly - rr * 1.9, lx + rr * 1.9, ly + rr * 1.9], fill=SHEET + (255,))
        d2.ellipse([lx - rr, ly - rr, lx + rr, ly + rr], fill=INK + (255,))
        if lp > 0.6:
            sc = min(1.0, lp)
            box = [tx + dx, ty + dy + (1 - sc) * bh_ * 0.4, tx + bw_ * sc + dx, ty + bh_ + dy]
            d2.rectangle(box, fill=INK + (255,))
            if sc > 0.85:
                d2.text((tx + pad + dx, ty + bh_ / 2 + dy), name, font=fl, fill=SHEET + (255,), anchor="lm")
        im.alpha_composite(lay)
    return im


# =============================================================== timeline
def parse_items(items) -> list[tuple[str, str]]:
    out = []
    for it in items or []:
        s = str(it).strip()
        m = re.match(r"^\s*(\d{3,4}s?|(?:19|20)\d\d)\s*[:\-–—,]?\s*(.*)$", s)
        if m:
            out.append((m.group(1), m.group(2).strip()[:40]))
    out.sort(key=lambda x: int(re.sub(r"\D", "", x[0]) or 0))
    seen, uniq = set(), []
    for y, l in out:
        if y not in seen:
            seen.add(y)
            uniq.append((y, l))
    return uniq[:5]


def _timeline(c: Ctx, t: float, fr: int, s: dict) -> Image.Image:
    items = parse_items(s.get("items"))
    if len(items) < 2:
        return _headline(c, t, fr, s)
    im = c.bg(fr)
    lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    title = str(s.get("text") or "").upper()[:40]
    ft = font("display", c.u * (0.085 if not c.portrait else 0.08))
    y0 = c.box[1]
    if title:
        dx, dy = jit(fr, "tt", 1.0)
        bar = ease_out(prog(t, 0, 0.3)) * c.u * 0.09
        d.rectangle([c.box[0], y0, c.box[0] + bar, y0 + c.u * 0.012], fill=RED + (255,))
        d.text((c.box[0] + dx, y0 + c.u * 0.025 + dy), title, font=ft, fill=INK + (int(255 * prog(t, 0.05, 0.2)),))
        y0 += ft.size * 1.5
    n = len(items)
    fy = font("display", c.u * (0.11 if not c.portrait else 0.10))
    fl = font("serif", c.u * (0.040 if not c.portrait else 0.044))
    lw = max(3, int(c.u * 0.006))
    lp = ease_io(prog(t, 0.25, 0.7 + 0.25 * n))
    if not c.portrait:
        ly = y0 + (c.box[3] - y0) * 0.52
        xa, xb = c.box[0] + c.bw * 0.04, c.box[2] - c.bw * 0.04
        rough_line(d, [(xa, ly), (xa + (xb - xa) * lp, ly)], lw, INK + (255,), c.seed + "tl", 0.8)
        gap = (xb - xa) / n
        for i, (yr, lab) in enumerate(items):
            x = xa + gap * (i + 0.5)
            p = back(prog(t, 0.35 + (0.7 + 0.25 * n) * (i + 0.5) / n, 0.25))
            if p <= 0:
                continue
            dx, dy = jit(fr, f"ev{i}", 1.3)
            r = c.u * 0.022 * p
            d.ellipse([x - r + dx, ly - r + dy, x + r + dx, ly + r + dy], fill=RED + (255,), outline=INK + (255,),
                      width=max(2, lw // 2))
            a = int(255 * min(1, p))
            up = i % 2 == 0
            d.text((x + dx, ly - c.u * 0.05 + dy if up else ly + c.u * 0.05 + dy), yr, font=fy, fill=INK + (a,),
                   anchor="md" if up else "ma")
            lines = wrap(lab.split(), fl, gap * 0.92)[:2]
            yy = ly + c.u * 0.06 if up else ly + c.u * 0.06 + fy.size * 1.1
            if not up:
                pass
            for ln in lines:
                s2 = " ".join(ln)
                d.text((x + dx * 0.5, yy + dy * 0.5), s2, font=fl, fill=MUTE + (a,), anchor="ma")
                yy += fl.size * 1.4
    else:
        lx = c.box[0] + c.bw * 0.22
        ya, yb = y0 + c.bh * 0.03, c.box[3] - c.bh * 0.03
        rough_line(d, [(lx, ya), (lx, ya + (yb - ya) * lp)], lw, INK + (255,), c.seed + "tl", 0.8)
        gap = (yb - ya) / n
        for i, (yr, lab) in enumerate(items):
            y = ya + gap * (i + 0.5)
            p = back(prog(t, 0.35 + (0.7 + 0.25 * n) * (i + 0.5) / n, 0.25))
            if p <= 0:
                continue
            dx, dy = jit(fr, f"ev{i}", 1.3)
            r = c.u * 0.026 * p
            d.ellipse([lx - r + dx, y - r + dy, lx + r + dx, y + r + dy], fill=RED + (255,), outline=INK + (255,),
                      width=max(2, lw // 2))
            a = int(255 * min(1, p))
            d.text((lx - c.u * 0.05 + dx, y + dy), yr, font=fy, fill=INK + (a,), anchor="rm")
            lines = wrap(lab.split(), fl, c.box[2] - lx - c.u * 0.08)[:2]
            yy = y - fl.size * 0.7 * len(lines)
            for ln in lines:
                d.text((lx + c.u * 0.06 + dx * 0.5, yy + dy * 0.5), " ".join(ln), font=fl, fill=MUTE + (a,))
                yy += fl.size * 1.4
    im.alpha_composite(lay)
    return im


# ================================================================== photo
@lru_cache(maxsize=8)
def _clipping(path: str, w: int, h: int, seed: str):
    """The photo as a torn paper clipping: warm, a touch desaturated, white margin, chromatic fringe."""
    with Image.open(path) as src:
        img = ImageOps.exif_transpose(src).convert("RGB")
    img = ImageOps.fit(img, (w, h), Image.LANCZOS, centering=(0.5, 0.42))
    img = ImageOps.autocontrast(img, cutoff=1)
    gray = ImageOps.grayscale(img).convert("RGB")
    img = Image.blend(img, gray, 0.18)
    img = ImageChops.multiply(img, Image.new("RGB", img.size, (255, 246, 228)))
    r, g, b = img.split()  # lens fringe: red and blue a couple of pixels apart
    k = max(1, w // 600)
    img = Image.merge("RGB", (ImageChops.offset(r, k, 0), g, ImageChops.offset(b, -k, 0)))
    m = int(min(w, h) * 0.035)
    card = textured(w + 2 * m, h + 2 * m, SHEET, seed + "clip", 2.0).convert("RGBA")
    card.paste(img, (m, m))
    card.putalpha(torn_mask(card.width, card.height, seed + "edge", "tblr", 0.008))
    return card


def _photo(c: Ctx, t: float, fr: int, s: dict) -> Image.Image:
    im = c.bg(fr)
    path = s.get("image")
    if not path or not Path(path).exists():
        return _headline(c, t, fr, s)
    ar = 4 / 3 if not c.portrait else 4 / 4.6
    cw = c.bw * (0.74 if not c.portrait else 0.92)
    ch = cw / ar
    if ch > c.bh * 0.86:
        ch = c.bh * 0.86
        cw = ch * ar
    try:
        card = _clipping(str(path), int(cw), int(ch), c.seed)
    except Exception:  # noqa: BLE001
        return _headline(c, t, fr, s)
    g = rnd(c.seed + "rot")
    rot = float(g.uniform(-3.2, 3.2))
    z = 1.0 + 0.06 * (t / c.dur)
    card2 = card.resize((int(card.width * z), int(card.height * z)), Image.BICUBIC)
    card2 = card2.rotate(rot, resample=Image.BICUBIC, expand=True)
    # two strips of tape
    tw_, th_ = card2.width * 0.16, card2.height * 0.05
    for i, (fx, ang) in enumerate([(0.16, -18), (0.84, 14)]):
        tape = Image.new("RGBA", (int(tw_), int(th_)), (232, 222, 196, 170))
        tape = tape.rotate(ang + rot, expand=True, resample=Image.BICUBIC)
        card2.alpha_composite(tape, (int(card2.width * fx - tape.width / 2), int(card2.height * 0.02)))
    cx, cy = c.w / 2, c.box[1] + c.bh / 2
    dx, dy = jit(fr, "clip", 1.5)
    drop = (1 - back(prog(t, 0.0, 0.32))) * c.h * 0.06
    x, y = int(cx - card2.width / 2 + dx), int(cy - card2.height / 2 + dy - drop)
    sd = shadow(card2.getchannel("A"), c.u * 0.014, 120)
    im.alpha_composite(sd, (x + int(c.u * 0.008), y + int(c.u * 0.016)))
    im.alpha_composite(card2, (x, y))
    label = str(s.get("text") or "").upper().strip()[:30]
    lp = ease_out(prog(t, 0.45, 0.25))
    if label and lp > 0:
        fl = font("display", c.u * (0.06 if not c.portrait else 0.055))
        pad = fl.size * 0.35
        bw_, bh_ = text_w(fl, label) + 2 * pad, fl.size * 1.32
        lx = max(c.box[0], x + card2.width * 0.06)
        ly = min(c.box[3] - bh_, y + card2.height - bh_ * 0.6)
        lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d2 = ImageDraw.Draw(lay)
        jx, jy = jit(fr, "lab", 1.2)
        d2.rectangle([lx + jx, ly + jy, lx + jx + bw_ * lp, ly + jy + bh_], fill=INK + (255,))
        if lp > 0.9:
            d2.text((lx + pad + jx, ly + bh_ / 2 + jy), label, font=fl, fill=YELLOW + (255,), anchor="lm")
        im.alpha_composite(lay)
    return im


DRAW = {"headline": _headline, "quote": _quote, "number": _number, "map": _map, "timeline": _timeline,
        "photo": _photo}


# ============================================================ transitions
def whip(frame: Image.Image, shift: float, blur: float) -> Image.Image:
    """Slide the graphic sideways by `shift` px with a smooth horizontal motion blur `blur` px long."""
    pm = np.asarray(frame.convert("RGBA"), np.float32) / 255.0
    pm = pm.copy()
    pm[..., :3] *= pm[..., 3:4]  # premultiplied alpha, so edges blur into transparency correctly
    H, W, _ = pm.shape
    off = int(round(shift))
    sh = np.zeros_like(pm)
    if 0 <= off < W:
        sh[:, off:] = pm[:, :W - off]
    elif -W < off < 0:
        sh[:, :W + off] = pm[:, -off:]
    L = int(max(1, blur))
    if L > 1:  # box blur along x with a running sum
        pad = np.zeros((H, L, 4), np.float32)
        cs = np.cumsum(np.concatenate([pad, sh, pad], axis=1), axis=1)
        sh = (cs[:, L + L // 2:L + L // 2 + W] - cs[:, L // 2:L // 2 + W]) / L
    al = sh[..., 3:4]
    rgb = np.where(al > 1e-4, sh[..., :3] / np.maximum(al, 1e-4), 0)
    out = np.concatenate([rgb, al], axis=2)
    return Image.fromarray(np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), "RGBA")


IN_STEPS = [(0.60, 0.30), (0.20, 0.16), (0.04, 0.05)]     # (remaining shift, blur) as fraction of width
OUT_STEPS = [(-0.05, 0.06), (-0.24, 0.18), (-0.66, 0.30)]


# ================================================================== public
def frame_at(spec: dict, w: int, h: int, dur: float, t: float, bottom: float = 0.8, seed: str = "") -> Image.Image:
    ensure_fonts()
    kind = spec.get("kind", "headline")
    c = Ctx(w, h, dur, bottom, seed or hashlib.md5(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:10])
    fn = DRAW.get(kind, _headline)
    return fn(c, t, int(t * VOX_FPS), spec)


def render_frames(spec: dict, w: int, h: int, dur: float, out_dir: Path, whip_in_out: bool = True,
                  bottom: float = 0.8, log=None) -> int:
    """Write f_0001.png ... at 12 fps into out_dir. Re-uses frames from an identical earlier render."""
    w, h = int(w) // 2 * 2, int(h) // 2 * 2
    n = max(2, int(math.ceil(dur * VOX_FPS)))
    out_dir = Path(out_dir)
    sig = hashlib.md5(json.dumps([spec, w, h, round(dur, 3), whip_in_out, bottom, 3], sort_keys=True,
                                 default=str).encode()).hexdigest()
    stamp = out_dir / "spec.txt"
    if stamp.exists() and stamp.read_text() == sig and (out_dir / f"f_{n:04d}.png").exists():
        return n
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("f_*.png"):
        try:
            old.unlink()
        except OSError:
            pass
    ensure_fonts(log)
    seed = sig[:10]
    c = Ctx(w, h, dur, bottom, seed)
    fn = DRAW.get(spec.get("kind", "headline"), _headline)

    def one(i: int) -> None:
        t = i / VOX_FPS
        im = fn(c, t, i, spec)
        if whip_in_out:
            k_in, k_out = i, n - 1 - i
            if k_in < len(IN_STEPS):
                s_, b_ = IN_STEPS[k_in]
                im = whip(im, s_ * w, b_ * w)
            elif k_out < len(OUT_STEPS):
                s_, b_ = OUT_STEPS[len(OUT_STEPS) - 1 - k_out]
                im = whip(im, s_ * w, b_ * w)
        im.save(out_dir / f"f_{i + 1:04d}.png", compress_level=1)

    import os
    with ThreadPoolExecutor(max_workers=max(2, min(8, (os.cpu_count() or 4)))) as ex:
        list(ex.map(one, range(n)))
    stamp.write_text(sig)
    return n


def preview(spec: dict, out: Path, w: int = 640, h: int = 360) -> str:
    """One still for the Review list."""
    try:
        dur = DUR.get(spec.get("kind", "headline"), 3.5)
        im = frame_at(spec, w, h, dur, dur * 0.72, 0.9)
        out.parent.mkdir(parents=True, exist_ok=True)
        im.convert("RGB").save(out, quality=88)
        return str(out)
    except Exception:  # noqa: BLE001
        return ""
