"""Hand-drawn doodle library: every doodle is a list of pen strokes in a unit box (x right, y down).

Strokes are drawn in order, like a person with a marker would. The renderer in doodles.py adds the
wobble, tapering, "line boil" and the draw-on animation, so shapes here stay clean and simple."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

Stroke = np.ndarray  # (N, 2) float points in 0..1


@dataclass
class Shape:
    strokes: list[Stroke]
    fill: list[int] = field(default_factory=list)  # closed strokes that get a colour fill once drawn
    aspect: float = 1.0                            # width / height of the doodle box
    float_up: bool = False                         # gentle upward drift (hearts, sparkles, music)
    pops: bool = True                              # little overshoot scale on entry


# ------------------------------------------------------------------ primitives
def line(a, b, n: int = 16) -> Stroke:
    t = np.linspace(0, 1, n)[:, None]
    return np.array(a, float) * (1 - t) + np.array(b, float) * t


def poly(*pts, n_per: int = 10) -> Stroke:
    out = [line(pts[i], pts[i + 1], n_per)[:-1] for i in range(len(pts) - 1)]
    return np.vstack(out + [np.array([pts[-1]], float)])


def bez(p0, p1, p2, p3, n: int = 40) -> Stroke:
    t = np.linspace(0, 1, n)[:, None]
    p0, p1, p2, p3 = (np.array(p, float) for p in (p0, p1, p2, p3))
    return (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3


def arc(cx, cy, rx, ry, a0, a1, n: int = 48) -> Stroke:
    a = np.linspace(math.radians(a0), math.radians(a1), n)
    return np.stack([cx + rx * np.cos(a), cy + ry * np.sin(a)], 1)


def loop(cx, cy, rx, ry, start: float = -100, overshoot: float = 28, n: int = 80, wob: float = 0.04,
         seed: int = 0) -> Stroke:
    """A hand-drawn ellipse: starts, goes round, overshoots a little and doesn't close perfectly."""
    rng = np.random.default_rng(seed)
    a = np.linspace(math.radians(start), math.radians(start + 360 + overshoot), n)
    ph = rng.uniform(0, 6.28)
    grow = np.linspace(0.97, 1.05, n)  # slight spiral so the ends don't meet
    r = grow * (1 + wob * np.sin(2 * a + ph))
    return np.stack([cx + rx * r * np.cos(a), cy + ry * r * np.sin(a)], 1)


def star_pts(cx, cy, r, inner: float = 0.45, points: int = 5, rot: float = -90) -> Stroke:
    pts = []
    for i in range(points * 2 + 1):
        rr = r if i % 2 == 0 else r * inner
        a = math.radians(rot + i * 180 / points)
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return poly(*pts, n_per=6)


def sparkle_pts(cx, cy, r, n: int = 18) -> Stroke:
    """Four-point twinkle with concave (curved-in) sides."""
    tips = [(cx, cy - r), (cx + r * 0.78, cy), (cx, cy + r), (cx - r * 0.78, cy)]
    out = []
    for i in range(4):
        a, b = np.array(tips[i]), np.array(tips[(i + 1) % 4])
        c = np.array([cx, cy]) + ((a + b) / 2 - [cx, cy]) * 0.18
        out.append(bez(a, a + (c - a) * 0.8, b + (c - b) * 0.8, b, n)[:-1])
    out.append(np.array([tips[0]], float))
    return np.vstack(out)


def heart_pts(cx, cy, s, n: int = 80) -> Stroke:
    t = np.linspace(0.5 * math.pi, 2.5 * math.pi, n)
    x = 16 * np.sin(t) ** 3
    y = -(13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t))
    return np.stack([cx + x / 34 * s, cy + y / 34 * s - 0.02 * s], 1)


def dot(cx, cy, r: float = 0.025) -> Stroke:
    return loop(cx, cy, r, r, overshoot=40, n=24, wob=0.0)


def arrow_head(tip, frm, size: float = 0.12, spread: float = 28) -> list[Stroke]:
    tip, frm = np.array(tip, float), np.array(frm, float)
    d = tip - frm
    ang = math.atan2(d[1], d[0])
    out = []
    for sgn in (-1, 1):
        a = ang + math.pi + sgn * math.radians(spread)
        out.append(line(tip + [size * math.cos(a), size * math.sin(a)], tip, 10))
    return out


def mirror(sh: Shape) -> Shape:
    return Shape([np.stack([1 - s[:, 0], s[:, 1]], 1) for s in sh.strokes], sh.fill, sh.aspect, sh.float_up, sh.pops)


# ------------------------------------------------------------------ doodles
def lightbulb() -> Shape:
    bulb =np.vstack([line((0.40, 0.66), (0.36, 0.57), 6), arc(0.5, 0.40, 0.21, 0.22, 140, 400, 60),
                      line((0.64, 0.57), (0.60, 0.66), 6)])
    rays = [line((0.5, 0.10), (0.5, 0.02), 6), line((0.24, 0.20), (0.17, 0.13), 6),
            line((0.76, 0.20), (0.83, 0.13), 6), line((0.14, 0.42), (0.05, 0.42), 6),
            line((0.86, 0.42), (0.95, 0.42), 6)]
    return Shape([bulb, line((0.40, 0.72), (0.60, 0.72), 8), line((0.42, 0.79), (0.58, 0.79), 8),
                  line((0.46, 0.86), (0.54, 0.86), 6), bez((0.45, 0.60), (0.43, 0.48), (0.57, 0.48), (0.55, 0.60), 20),
                  *rays])


def question() -> Shape:
    def q(x, y, s):
        top = bez((x - 0.16 * s, y - 0.18 * s), (x - 0.15 * s, y - 0.42 * s), (x + 0.20 * s, y - 0.42 * s),
                  (x + 0.17 * s, y - 0.16 * s), 30)
        down = bez((x + 0.17 * s, y - 0.16 * s), (x + 0.14 * s, y - 0.02 * s), (x, y - 0.04 * s), (x, y + 0.12 * s), 20)
        return [np.vstack([top, down[1:]]), dot(x, y + 0.28 * s, 0.03 * s)]
    return Shape([*q(0.38, 0.46, 1.0), *q(0.78, 0.40, 0.62)])


def exclaim() -> Shape:
    out = []
    for x, s, tilt in ((0.40, 1.0, -0.05), (0.70, 0.7, 0.06)):
        out.append(line((x - tilt, 0.12 + (1 - s) * 0.3), (x, 0.66), 12))
        out.append(dot(x + tilt * 0.3, 0.82, 0.035 * s))
    return Shape(out)


def burst() -> Shape:
    """Comic 'emphasis lines' radiating around a centre (used around the head)."""
    out = []
    n = 12
    for i in range(n):
        a = math.radians(i * 360 / n + (8 if i % 2 else -4))
        r0, r1 = (0.38, 0.49) if i % 2 else (0.31, 0.49)
        out.append(line((0.5 + r0 * math.cos(a), 0.5 + r0 * math.sin(a)),
                        (0.5 + r1 * math.cos(a), 0.5 + r1 * math.sin(a)), 6))
    return Shape(out, pops=False)


def hearts() -> Shape:
    return Shape([heart_pts(0.36, 0.55, 0.52), heart_pts(0.74, 0.30, 0.30), heart_pts(0.80, 0.72, 0.20)],
                 fill=[0, 1, 2], float_up=True)


def stars() -> Shape:
    return Shape([star_pts(0.38, 0.55, 0.30), star_pts(0.78, 0.25, 0.17), star_pts(0.80, 0.75, 0.12)],
                 fill=[0, 1, 2])


def sparkle() -> Shape:
    return Shape([sparkle_pts(0.40, 0.52, 0.30), sparkle_pts(0.78, 0.24, 0.16), sparkle_pts(0.76, 0.78, 0.12)],
                 fill=[0, 1, 2], float_up=True)


def crown() -> Shape:
    body = poly((0.14, 0.72), (0.10, 0.28), (0.32, 0.50), (0.50, 0.18), (0.68, 0.50), (0.90, 0.28),
                (0.86, 0.72), (0.14, 0.72), n_per=10)
    return Shape([body, line((0.16, 0.82), (0.84, 0.82), 14), dot(0.10, 0.22, 0.03), dot(0.50, 0.11, 0.035),
                  dot(0.90, 0.22, 0.03)], fill=[0], aspect=1.5)


def arrow_me() -> Shape:
    """Curved arrow pointing LEFT (towards the speaker); mirrored when the face is on the right."""
    shaft = bez((0.92, 0.22), (0.72, 0.05), (0.30, 0.15), (0.12, 0.62), 50)
    return Shape([shaft, *arrow_head((0.12, 0.62), shaft[-6], 0.16)], aspect=1.3, pops=False)


def check() -> Shape:
    return Shape([poly((0.12, 0.52), (0.38, 0.80), (0.90, 0.16), n_per=18)], pops=False)


def cross() -> Shape:
    return Shape([line((0.18, 0.18), (0.82, 0.82), 20), line((0.82, 0.20), (0.20, 0.80), 20)], pops=False)


def money() -> Shape:
    s = bez((0.70, 0.26), (0.55, 0.12), (0.22, 0.20), (0.32, 0.40), 24)
    s2 = bez((0.32, 0.40), (0.42, 0.56), (0.76, 0.48), (0.70, 0.70), 24)
    s3 = bez((0.70, 0.70), (0.64, 0.88), (0.32, 0.86), (0.26, 0.72), 18)
    return Shape([np.vstack([s, s2[1:], s3[1:]]), line((0.50, 0.06), (0.50, 0.94), 18),
                  loop(0.86, 0.80, 0.10, 0.10, n=30, wob=0.02), loop(0.12, 0.18, 0.08, 0.08, n=30, wob=0.02)])


def rupee() -> Shape:
    return Shape([line((0.24, 0.14), (0.80, 0.14), 12), line((0.24, 0.32), (0.80, 0.32), 12),
                  np.vstack([line((0.24, 0.14), (0.44, 0.14), 4), bez((0.44, 0.14), (0.74, 0.14), (0.74, 0.50),
                                                                     (0.44, 0.50), 24)[1:],
                             line((0.44, 0.50), (0.26, 0.50), 6)[1:]]),
                  line((0.30, 0.52), (0.74, 0.90), 16),
                  loop(0.88, 0.72, 0.09, 0.09, n=28, wob=0.02)])


def graph_up() -> Shape:
    axes = poly((0.10, 0.08), (0.10, 0.90), (0.94, 0.90), n_per=14)
    zig = poly((0.18, 0.76), (0.38, 0.56), (0.52, 0.66), (0.84, 0.22), n_per=10)
    return Shape([axes, zig, *arrow_head((0.84, 0.22), (0.62, 0.52), 0.14)], aspect=1.15, pops=False)


def rocket() -> Shape:
    body = np.vstack([bez((0.38, 0.70), (0.30, 0.42), (0.40, 0.18), (0.50, 0.06), 30),
                      bez((0.50, 0.06), (0.60, 0.18), (0.70, 0.42), (0.62, 0.70), 30)[1:],
                      line((0.62, 0.70), (0.38, 0.70), 8)[1:]])
    fin_l = poly((0.36, 0.52), (0.22, 0.68), (0.24, 0.80), (0.38, 0.70), n_per=6)
    fin_r = poly((0.64, 0.52), (0.78, 0.68), (0.76, 0.80), (0.62, 0.70), n_per=6)
    flame = poly((0.42, 0.74), (0.46, 0.90), (0.50, 0.80), (0.54, 0.96), (0.58, 0.74), n_per=6)
    return Shape([body, loop(0.50, 0.36, 0.07, 0.07, n=30, wob=0.0), fin_l, fin_r, flame,
                  line((0.20, 0.90), (0.12, 0.98), 6), line((0.80, 0.90), (0.88, 0.98), 6)], aspect=0.8)


def lightning() -> Shape:
    bolt = poly((0.58, 0.04), (0.24, 0.52), (0.48, 0.52), (0.36, 0.96), (0.78, 0.40), (0.52, 0.40),
                (0.70, 0.04), (0.58, 0.04), n_per=8)
    return Shape([bolt], fill=[0], aspect=0.75)


def clock() -> Shape:
    ticks = []
    for i in range(4):
        a = math.radians(i * 90 - 90)
        ticks.append(line((0.5 + 0.30 * math.cos(a), 0.5 + 0.30 * math.sin(a)),
                          (0.5 + 0.36 * math.cos(a), 0.5 + 0.36 * math.sin(a)), 4))
    return Shape([loop(0.5, 0.5, 0.43, 0.43, n=80), *ticks, poly((0.5, 0.24), (0.5, 0.5), (0.70, 0.60), n_per=10),
                  line((0.08, 0.10), (0.02, 0.04), 5), line((0.92, 0.10), (0.98, 0.04), 5)])


def fire() -> Shape:
    outer = np.vstack([bez((0.50, 0.96), (0.14, 0.96), (0.10, 0.56), (0.34, 0.30), 30),
                       bez((0.34, 0.30), (0.34, 0.50), (0.44, 0.50), (0.46, 0.04), 24)[1:],
                       bez((0.46, 0.04), (0.74, 0.30), (0.98, 0.62), (0.50, 0.96), 34)[1:]])
    inner = np.vstack([bez((0.50, 0.90), (0.32, 0.90), (0.30, 0.70), (0.44, 0.56), 20),
                       bez((0.44, 0.56), (0.48, 0.66), (0.54, 0.62), (0.56, 0.48), 12)[1:],
                       bez((0.56, 0.48), (0.72, 0.66), (0.70, 0.90), (0.50, 0.90), 20)[1:]])
    return Shape([outer, inner], fill=[0], aspect=0.8)


def target() -> Shape:
    return Shape([loop(0.46, 0.54, 0.40, 0.40, n=80), loop(0.46, 0.54, 0.24, 0.24, n=60, seed=2),
                  dot(0.46, 0.54, 0.05), line((0.96, 0.04), (0.50, 0.50), 20), line((0.96, 0.04), (0.84, 0.06), 5),
                  line((0.96, 0.04), (0.94, 0.16), 5)])


def eyes() -> Shape:
    return Shape([loop(0.28, 0.5, 0.20, 0.30, n=60), loop(0.72, 0.5, 0.20, 0.30, n=60, seed=3),
                  dot(0.33, 0.56, 0.07), dot(0.77, 0.56, 0.07)], fill=[], aspect=1.6)


def spiral() -> Shape:
    a = np.linspace(0, 5.2 * math.pi, 120)
    r = 0.02 + 0.42 * a / a[-1]
    return Shape([np.stack([0.5 + r * np.cos(a), 0.5 + r * np.sin(a)], 1)], pops=False)


def smile() -> Shape:
    return Shape([loop(0.5, 0.5, 0.44, 0.44, n=80), dot(0.36, 0.40, 0.045), dot(0.64, 0.40, 0.045),
                  arc(0.5, 0.52, 0.24, 0.20, 20, 160, 30)])


def music() -> Shape:
    return Shape([line((0.36, 0.72), (0.36, 0.18), 14), line((0.80, 0.62), (0.80, 0.10), 14),
                  poly((0.36, 0.18), (0.80, 0.10), (0.80, 0.22), (0.36, 0.30), n_per=8),
                  loop(0.26, 0.74, 0.11, 0.08, n=36, wob=0.0), loop(0.70, 0.64, 0.11, 0.08, n=36, wob=0.0)],
                 fill=[3, 4], float_up=True)


def magnifier() -> Shape:
    return Shape([loop(0.40, 0.40, 0.30, 0.30, n=72), line((0.62, 0.62), (0.92, 0.92), 14),
                  arc(0.40, 0.40, 0.18, 0.18, 200, 260, 12)])


def trophy() -> Shape:
    cup = np.vstack([line((0.24, 0.10), (0.76, 0.10), 10), bez((0.76, 0.10), (0.78, 0.48), (0.62, 0.58),
                                                               (0.50, 0.58), 24)[1:],
                     bez((0.50, 0.58), (0.38, 0.58), (0.22, 0.48), (0.24, 0.10), 24)[1:]])
    return Shape([cup, arc(0.22, 0.26, 0.12, 0.12, 90, 270, 20), arc(0.78, 0.26, 0.12, 0.12, -90, 90, 20),
                  line((0.50, 0.58), (0.50, 0.76), 8), poly((0.32, 0.88), (0.36, 0.76), (0.64, 0.76), (0.68, 0.88),
                                                             (0.32, 0.88), n_per=6)], fill=[0], aspect=0.95)


def wow_burst() -> Shape:
    """Comic explosion bubble - the renderer puts a word (WOW / BOOM / OMG) inside."""
    pts = []
    n = 14
    for i in range(n + 1):
        a = math.radians(i * 360 / n - 90)
        r = 0.48 if i % 2 == 0 else 0.32
        jit = 0.03 * math.sin(i * 2.7)
        pts.append((0.5 + (r + jit) * math.cos(a) * 1.0, 0.5 + (r + jit) * math.sin(a) * 0.86))
    return Shape([poly(*pts, n_per=5)], fill=[0], aspect=1.25)


def underline() -> Shape:
    x = np.linspace(0.02, 0.98, 80)
    y = 0.5 + 0.18 * np.sin(x * 2 * math.pi * 2.2 + 0.4) * np.linspace(0.6, 1.0, 80)
    return Shape([np.stack([x, y], 1)], aspect=6.0, pops=False)


def circle_text() -> Shape:
    return Shape([loop(0.5, 0.5, 0.48, 0.44, start=200, overshoot=40, n=90, wob=0.03)], aspect=2.6, pops=False)


def squiggle() -> Shape:
    t = np.linspace(0, 1, 90)
    return Shape([np.stack([0.06 + 0.88 * t, 0.5 + 0.3 * np.sin(t * 6 * math.pi) * np.sin(t * math.pi)], 1)],
                 aspect=2.0, pops=False)


SHAPES = {
    "lightbulb": lightbulb, "question": question, "exclaim": exclaim, "burst": burst, "hearts": hearts,
    "stars": stars, "sparkle": sparkle, "crown": crown, "arrow_me": arrow_me, "check": check, "cross": cross,
    "money": money, "rupee": rupee, "graph_up": graph_up, "rocket": rocket, "lightning": lightning, "clock": clock,
    "fire": fire, "target": target, "eyes": eyes, "spiral": spiral, "smile": smile, "music": music,
    "magnifier": magnifier, "trophy": trophy, "wow": wow_burst, "underline": underline,
    "circle_text": circle_text, "squiggle": squiggle,
}

# what the editor model picks  ->  (shape, where it goes, colour, sound)
KINDS: dict[str, tuple[str, str, str, str]] = {
    "idea": ("lightbulb", "above_head", "yellow", "ding"),
    "question": ("question", "side_head", "white", "scribble"),
    "warning": ("exclaim", "side_head", "yellow", "pop"),
    "emphasis": ("burst", "around_head", "yellow", "scribble"),
    "love": ("hearts", "side_head", "pink", "sparkle"),
    "best": ("crown", "above_head", "yellow", "sparkle"),
    "amazing": ("stars", "side_head", "yellow", "sparkle"),
    "magic": ("sparkle", "side_head", "white", "sparkle"),
    "point_me": ("arrow_me", "side_head", "white", "scribble"),
    "yes": ("check", "free", "lime", "pop"),
    "no": ("cross", "free", "pink", "pop"),
    "money": ("money", "free", "lime", "scribble"),
    "growth": ("graph_up", "free", "lime", "scribble"),
    "fast": ("rocket", "free", "white", "whoosh"),
    "energy": ("lightning", "free", "yellow", "pop"),
    "time": ("clock", "free", "white", "scribble"),
    "hot": ("fire", "free", "orange", "scribble"),
    "goal": ("target", "free", "pink", "pop"),
    "look": ("eyes", "above_head", "white", "pop"),
    "confused": ("spiral", "side_head", "white", "scribble"),
    "happy": ("smile", "free", "yellow", "pop"),
    "music": ("music", "side_head", "cyan", "sparkle"),
    "search": ("magnifier", "free", "cyan", "scribble"),
    "win": ("trophy", "free", "yellow", "sparkle"),
    "wow": ("wow", "side_head", "yellow", "pop"),
    "custom": ("custom", "free", "white", "scribble"),
}

KIND_HELP = {
    "idea": "an idea, tip, trick or realisation", "question": "asking a question / wondering",
    "warning": "a warning, mistake or important caution", "emphasis": "a strong, shocking or key statement",
    "love": "love, liking, favourite", "best": "the best / king / number one", "amazing": "amazing, awesome",
    "magic": "magic, AI, something feeling magical", "point_me": "the speaker talks about himself (I, me, my)",
    "yes": "correct, done, yes", "no": "wrong, don't, no", "money": "money, price, free, earning, saving",
    "growth": "growth, increase, results going up", "fast": "speed, launch, quick, boost",
    "energy": "power, energy, electric", "time": "time, hours, minutes, years, deadline",
    "hot": "trending, hot, viral", "goal": "goal, target, focus", "look": "look at this / watch",
    "confused": "confusing, dizzy, complicated", "happy": "happy, fun, smile", "music": "music, song, audio",
    "search": "searching, finding, research", "win": "winning, achievement, trophy",
    "wow": "a WOW moment (give a 1-word exclamation in 'word')",
}

COLORS = {
    "yellow": (255, 214, 0), "white": (255, 255, 255), "pink": (255, 77, 141), "cyan": (61, 224, 255),
    "lime": (124, 255, 91), "orange": (255, 140, 40),
}


def get(shape_name: str, flip: bool = False) -> Shape:
    sh = SHAPES[shape_name]()
    return mirror(sh) if flip else sh
