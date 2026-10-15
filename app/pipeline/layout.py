"""Split-screen "push aside" layouts: while you talk, you glide to one side and the other side shows the B-roll,
a photo or an animated keyword card (with a doodle). Three flavours, picked per output:

  pillar : vertical recording -> 16:9 YouTube. You are a rounded card in the middle; for a B-roll moment you slide
           to the left and the panel slides in on the right.
  side   : landscape recording -> 16:9 YouTube. Your face region lifts out as a card and glides left; the rest of
           the frame blurs away and the panel slides in on the right.
  pip    : 9:16 Reel. The B-roll fills the screen and you stay visible as a picture-in-picture card.

Everything is plain FFmpeg filters (overlay expressions + alpha masks), so it renders in the same single pass."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

EASE_D = 0.45  # seconds for the glide in / out


def even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


def mode_for(vertical_out: bool, base_info: dict, W: int, H: int) -> str:
    if vertical_out:
        return "pip"
    bw, bh = base_info["width"], base_info["height"]
    return "pillar" if bh > bw and W > H else "side"


def pillar_rect(base_info: dict, W: int, H: int) -> tuple[int, int, int, int]:
    """Where the vertical video sits in the 16:9 frame normally (a rounded card with breathing room)."""
    bw, bh = base_info["width"], base_info["height"]
    fh = even(H * 0.92)
    fw = even(bw * fh / bh)
    return (W - fw) // 2 // 2 * 2, (H - fh) // 2 // 2 * 2, fw, fh


def geometry(mode: str, W: int, H: int, base_info: dict) -> dict:
    """Card target + panel rectangle for a split moment (output pixels)."""
    if mode == "pillar":
        x, y, fw, fh = pillar_rect(base_info, W, H)
        tx = even(W * 0.045)
        px = tx + fw + even(W * 0.04)
        pw = even(W - px - W * 0.045)
        ph = even(min(H * 0.66, pw / 1.3))
        return {"card_to": (tx, y), "card": (x, y, fw, fh), "panel": (px, y, pw, ph)}
    if mode == "side":
        cw, ch = even(W * 0.40), even(H * 0.68)
        px = even(W * 0.48)
        return {"card_to": (even(W * 0.04), even(H * 0.08)), "card_size": (cw, ch),
                "panel": (px, even(H * 0.08), even(W - px - W * 0.04), ch)}
    cw, ch = even(W * 0.50), even(H * 0.26)  # pip
    return {"card_to": (even(W * 0.05), even(H * 0.34)), "card_size": (cw, ch), "panel": (0, 0, W, H)}


def caption_regions(broll: list[dict], mode: str, geo: dict, t0: float, t1: float) -> list[tuple[float, float, int]]:
    """While split, captions sit centred under the panel instead of across you."""
    if mode not in ("pillar", "side"):
        return []
    px, py, pw, ph = geo["panel"]
    return [(b["t0"] - t0, min(b["t1"], t1) - t0, px + pw // 2) for b in broll
            if b.get("style") == "split" and t0 <= b["t0"] < t1]


def assign_styles(broll: list[dict], mode: str, cfg: dict) -> None:
    """Which B-roll moments become split-screen. auto: always for vertical->YouTube, every other one otherwise."""
    pref = cfg.get("broll_layout", "auto")
    k = 0
    for b in broll:
        if mode == "pip" and (b.get("kind") == "vox" or (cfg.get("vox_graphics") and b.get("kind") == "image")):
            b["style"] = "full"  # explainer graphics fill the Reel; a picture-in-picture would cover them
            continue
        if pref == "full":
            b["style"] = "full"
        elif pref == "split" or mode == "pillar":
            b["style"] = "split"
        else:
            b["style"] = "split" if k % 2 == 0 else "full"
        k += 1


def ease(s: float, e: float, d: float = EASE_D) -> str:
    """FFmpeg expression: 0 outside, glides 0->1 in the first d seconds, 1->0 in the last d seconds."""
    d = max(0.15, min(d, (e - s) / 2.5))
    return f"(1-pow(1-clip(min((t-{s:.3f})/{d:.3f},({e:.3f}-t)/{d:.3f}),0,1),3))"


def mask_png(folder: Path, w: int, h: int, radius: float = 0.045) -> str:
    """Rounded-rectangle alpha mask (white = visible)."""
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"mask_{w}x{h}.png"
    if not f.exists():
        ss = 3
        im = Image.new("L", (w * ss, h * ss), 0)
        r = int(min(w, h) * radius * ss)
        ImageDraw.Draw(im).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=r, fill=255)
        im.resize((w, h), Image.LANCZOS).save(f)
    return f.name


def frame_png(folder: Path, w: int, h: int, radius: float = 0.045, border: int = 5, shadow: int = 26) -> tuple[str, int]:
    """Soft drop shadow + thin white border around a card, as one RGBA image. Returns (name, padding)."""
    folder.mkdir(parents=True, exist_ok=True)
    pad = shadow + border
    f = folder / f"frame_{w}x{h}.png"
    if not f.exists():
        W2, H2 = w + 2 * pad, h + 2 * pad
        sh = Image.new("L", (W2, H2), 0)
        r = int(min(w, h) * radius)
        ImageDraw.Draw(sh).rounded_rectangle([pad, pad + shadow // 3, pad + w, pad + h + shadow // 3], radius=r, fill=150)
        sh = sh.filter(ImageFilter.GaussianBlur(shadow / 2))
        out = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
        out.putalpha(sh)
        ring = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
        d = ImageDraw.Draw(ring)
        d.rounded_rectangle([pad - border, pad - border, pad + w + border - 1, pad + h + border - 1],
                            radius=r + border, outline=(255, 255, 255, 235), width=border)
        out.alpha_composite(ring)
        # punch the card area out so only shadow + ring remain
        hole = Image.new("L", (W2, H2), 255)
        ImageDraw.Draw(hole).rounded_rectangle([pad, pad, pad + w - 1, pad + h - 1], radius=r, fill=0)
        a = Image.composite(out.getchannel("A"), Image.new("L", (W2, H2), 0), hole)
        out.putalpha(a)
        out.save(f)
    return f.name, pad
