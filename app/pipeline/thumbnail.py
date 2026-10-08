"""YouTube thumbnails (1280x720) + Reel covers (1080x1920).

frame scoring (face size, sharpness, centring) -> optional vision-LLM pick of the most expressive
frames -> background removal (BiRefNet via rembg) -> AI / blurred / gradient background ->
subject with white outline + shadow -> huge outlined hook text (Pillow, so text is always crisp)."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from ..core.ffmpeg import extract_frame, iter_frames
from ..core.paths import font_path
from .face import FaceDetector

PALETTES = [
    {"text": (255, 255, 255), "accent": (255, 214, 0), "grad": ((20, 20, 60), (240, 40, 90))},
    {"text": (255, 230, 0), "accent": (255, 255, 255), "grad": ((10, 40, 90), (0, 190, 255))},
    {"text": (255, 255, 255), "accent": (0, 255, 140), "grad": ((40, 10, 70), (255, 120, 0))},
]

PICK_SCHEMA = {"type": "object", "properties": {"best": {"type": "array", "items": {"type": "integer"}}},
               "required": ["best"]}


def candidate_frames(video: Path, keeps: list[tuple[float, float]], n: int = 8, rotate: int = 0) -> list[dict]:
    det = FaceDetector()
    scored = []
    for t, fr in iter_frames(video, fps=1.0, width=640, rotate=rotate):
        if not any(s + 0.3 <= t <= e - 0.3 for s, e in keeps):
            continue
        h, w = fr.shape[:2]
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        faces = det.detect(fr)
        if faces:
            x, y, fw, fh, conf = faces[0]
            x0, y0 = int(max(0, x)), int(max(0, y))
            crop = gray[y0:int(y0 + fh), x0:int(x0 + fw)]
            sharp = cv2.Laplacian(crop, cv2.CV_64F).var() if crop.size else 0
            cxn = (x + fw / 2) / w
            score = (fh / h) * 3 + min(sharp, 400) / 400 + conf - abs(cxn - 0.5)
            scored.append({"t": t, "score": score, "face": True})
        else:
            sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
            sat = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)[:, :, 1].mean() / 255
            scored.append({"t": t, "score": min(sharp, 800) / 800 * 0.5 + sat * 0.5 - 1.0, "face": False})
    scored.sort(key=lambda d: d["score"], reverse=True)
    picked: list[dict] = []
    for c in scored:
        if all(abs(c["t"] - p["t"]) > 5 for p in picked):
            picked.append(c)
        if len(picked) >= n:
            break
    return picked


def vlm_rank(cands: list[dict], job_dir: Path, planner, log) -> list[dict]:
    if not planner or not planner.ok or len(cands) < 2:
        return cands
    paths = []
    for i, c in enumerate(cands):
        p = job_dir / "thumbs" / f"cand_{i}.jpg"
        p.parent.mkdir(exist_ok=True)
        img = Image.open(c["path"]).convert("RGB")
        img.thumbnail((512, 512))
        img.save(p, quality=85)
        paths.append(p)
    try:
        r = planner.llm.chat_json(planner.model, "You are a YouTube thumbnail designer. Reply in JSON.",
                                  f"Here are {len(paths)} video frames numbered 0 to {len(paths) - 1} in order. "
                                  f"Pick the 3 best for a YouTube thumbnail: expressive face, eyes open, mouth not "
                                  f"mid-word, sharp, good lighting. Return their numbers, best first.",
                                  schema=PICK_SCHEMA, images=paths, ctx=8192)
        order = [i for i in r.get("best", []) if isinstance(i, int) and 0 <= i < len(cands)]
        order = list(dict.fromkeys(order)) + [i for i in range(len(cands)) if i not in order]
        return [cands[i] for i in order]
    except Exception as e:  # noqa: BLE001
        log(f"Vision pick failed ({e}); using score order")
        return cands


def cutout(img: Image.Image, log) -> Image.Image | None:
    try:
        from rembg import new_session, remove
        global _SESSION
        if "_SESSION" not in globals():
            _SESSION = new_session("birefnet-portrait")
        out = remove(img, session=_SESSION)
        a = np.array(out)[:, :, 3]
        return out if (a > 128).mean() > 0.03 else None
    except Exception as e:  # noqa: BLE001
        log(f"Background removal unavailable: {e}")
        return None


def gradient(size, c1, c2) -> Image.Image:
    w, h = size
    x = np.linspace(0, 1, w)[None, :, None]
    y = np.linspace(0, 1, h)[:, None, None]
    t = np.clip(0.6 * x + 0.4 * y, 0, 1)
    arr = (np.array(c1)[None, None, :] * (1 - t) + np.array(c2)[None, None, :] * t).astype(np.uint8)
    img = Image.fromarray(arr)
    # soft radial glow
    glow = Image.new("L", size, 0)
    ImageDraw.Draw(glow).ellipse([w * 0.45, -h * 0.2, w * 1.2, h * 1.1], fill=110)
    glow = glow.filter(ImageFilter.GaussianBlur(w // 8))
    return Image.composite(Image.new("RGB", size, (255, 255, 255)), img, glow.point(lambda v: v // 2))


def fit_cover(img: Image.Image, size) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), size, Image.LANCZOS)


def blurred_bg(frame: Image.Image, size) -> Image.Image:
    bg = fit_cover(frame, size).filter(ImageFilter.GaussianBlur(18))
    bg = ImageEnhance.Brightness(bg).enhance(0.6)
    return ImageEnhance.Color(bg).enhance(1.5)


def draw_text_block(canvas: Image.Image, text: str, box: tuple[int, int, int, int], pal: dict, font_file: str) -> None:
    x0, y0, x1, y1 = box
    words = text.upper().split()
    if not words:
        return
    # split into up to 3 balanced lines
    best = [" ".join(words)]
    if len(words) >= 3:
        cut = (len(words) + 1) // 2
        best = [" ".join(words[:cut]), " ".join(words[cut:])]
    if len(words) >= 6:
        k = (len(words) + 2) // 3
        best = [" ".join(words[i:i + k]) for i in range(0, len(words), k)]
    draw = ImageDraw.Draw(canvas)
    size = 260
    fp = str(font_path(font_file))
    while size > 30:
        font = ImageFont.truetype(fp, size)
        widths = [draw.textbbox((0, 0), ln, font=font)[2] for ln in best]
        line_h = int(size * 1.02)
        if max(widths) <= (x1 - x0) and line_h * len(best) <= (y1 - y0):
            break
        size -= 6
    font = ImageFont.truetype(fp, size)
    line_h = int(size * 1.02)
    total = line_h * len(best)
    y = y0 + ((y1 - y0) - total) // 2
    stroke = max(4, size // 14)
    for i, ln in enumerate(best):
        color = pal["accent"] if (i == len(best) - 1 and len(best) > 1) else pal["text"]
        # hard drop shadow then outlined text
        draw.text((x0 + stroke, y + stroke), ln, font=font, fill=(0, 0, 0), stroke_width=stroke, stroke_fill=(0, 0, 0))
        draw.text((x0, y), ln, font=font, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += line_h


def compose(frame: Image.Image, subject: Image.Image | None, bg: Image.Image, text: str, pal: dict,
            size=(1280, 720), font_file: str = "Anton-Regular.ttf") -> Image.Image:
    W, H = size
    canvas = fit_cover(bg, size)
    portrait = H > W
    if subject is not None:
        bbox = subject.getbbox()
        subj = subject.crop(bbox) if bbox else subject
        target_h = int(H * (0.62 if portrait else 0.98))
        scale = target_h / subj.height
        subj = subj.resize((max(1, int(subj.width * scale)), target_h), Image.LANCZOS)
        if not portrait and subj.width > W * 0.6:
            s2 = W * 0.6 / subj.width
            subj = subj.resize((int(subj.width * s2), int(subj.height * s2)), Image.LANCZOS)
        alpha = subj.split()[3]
        outline = alpha.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(1))
        shadow = alpha.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(18))
        px = W - subj.width + int(W * 0.02) if not portrait else (W - subj.width) // 2
        py = H - subj.height
        canvas.paste((0, 0, 0), (px + 12, py + 6), shadow.point(lambda v: int(v * 0.6)))
        canvas.paste((255, 255, 255), (px, py), outline)
        canvas.paste(subj, (px, py), alpha)
        text_box = (int(W * 0.04), int(H * 0.08), int(px + subj.width * 0.12), int(H * 0.92)) if not portrait \
            else (int(W * 0.06), int(H * 0.06), int(W * 0.94), int(H * 0.36))
        if not portrait and text_box[2] - text_box[0] < W * 0.38:
            text_box = (text_box[0], text_box[1], int(W * 0.5), text_box[3])
    else:
        canvas = fit_cover(frame, size)
        dark = Image.new("L", size, 0)
        ImageDraw.Draw(dark).rectangle([0, 0, W, H], fill=0)
        grad = Image.linear_gradient("L").rotate(90 if not portrait else 0).resize(size)
        canvas = Image.composite(Image.new("RGB", size, (0, 0, 0)), canvas, grad.point(lambda v: int(v * 0.75)))
        text_box = (int(W * 0.05), int(H * 0.1), int(W * 0.62), int(H * 0.9)) if not portrait \
            else (int(W * 0.06), int(H * 0.08), int(W * 0.94), int(H * 0.4))
    canvas = ImageEnhance.Contrast(canvas).enhance(1.08)
    draw_text_block(canvas, text, text_box, pal, font_file)
    return canvas


def make_thumbnails(source: Path, keeps, job_dir: Path, texts: list[str], bg_prompt: str, cfg: dict, planner,
                    log: Callable[[str], None], on_progress: Callable[[float, str], None], rotate: int = 0) -> dict:
    out_dir = job_dir / "thumbs"
    out_dir.mkdir(exist_ok=True)
    n = max(1, int(cfg.get("thumbnail_count", 3)))
    on_progress(0.05, "Finding thumbnail frames")
    cands = candidate_frames(source, keeps, n=8, rotate=rotate)
    if not cands:
        cands = [{"t": keeps[0][0] + 1.0, "score": 0, "face": False}]
    for i, c in enumerate(cands):
        c["path"] = str(extract_frame(source, c["t"], out_dir / f"frame_{i}.jpg", rotate=rotate))
    if cfg.get("thumbnail_vlm_pick", True) and any(c["face"] for c in cands):
        on_progress(0.2, "Vision model choosing best expressions")
        cands = vlm_rank(cands, job_dir, planner, log)
    if planner:
        planner.unload()
    chosen = cands[:n]

    # background(s)
    bg_mode = cfg.get("thumbnail_background", "ai")
    ai_bg = None
    if bg_mode == "ai":
        from .broll import generate
        ok_ai, why_ai = generate.available()
        if ok_ai:
            on_progress(0.35, "Generating AI thumbnail background")
            gen = generate.ImageGen(log=log)
            try:
                ai_bg = Image.open(gen.generate(bg_prompt + ", vibrant, high contrast, depth of field, no text",
                                                out_dir / "bg_ai.jpg", 1344, 768, seed=random.randint(0, 9999)))
            except Exception as e:  # noqa: BLE001
                log(f"AI background failed: {e}")
            finally:
                gen.close()
        else:
            log(f"AI thumbnail background skipped ({why_ai}); using blurred background")

    results = []
    texts = [t for t in texts if t and t.strip()] or ["WATCH THIS"]
    for i, c in enumerate(chosen):
        on_progress(0.6 + 0.4 * i / len(chosen), f"Designing thumbnail {i + 1}/{len(chosen)}")
        frame = Image.open(c["path"]).convert("RGB")
        subject = cutout(frame, log) if c["face"] else None
        pal = PALETTES[i % len(PALETTES)]
        if ai_bg is not None and i != 1:
            bg = ai_bg
        elif bg_mode == "gradient" or (ai_bg is not None and i == 1):
            bg = gradient((1280, 720), *pal["grad"])
        else:
            bg = blurred_bg(frame, (1280, 720))
        text = texts[i % len(texts)]
        thumb = compose(frame, subject, bg, text, pal, (1280, 720))
        p = out_dir / f"thumb_{i}.jpg"
        thumb.save(p, quality=92)
        cover = compose(frame, subject, bg if bg is not ai_bg else ai_bg, text, pal, (1080, 1920))
        pc = out_dir / f"cover_{i}.jpg"
        cover.save(pc, quality=92)
        results.append({"path": str(p), "cover": str(pc), "t": c["t"], "text": text})
    on_progress(1.0, "Thumbnails ready")
    return {"options": results, "choice": 0}
