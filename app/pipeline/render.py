"""Rendering.

1. base.mkv   : the cut ("jump-cut") edit, with punch-in zooms, frame-accurate, near-lossless.
2. finals     : B-roll overlays + captions/callouts + progress bar + audio post, one per output
                (YouTube 16:9 / Reel 9:16 face-tracked or blur-fill, full or highlight).
All filtergraph file references are RELATIVE to the job folder (cwd) -> no Windows path escaping issues.
"""
from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from pathlib import Path
from typing import Callable

from ..core import ffmpeg
from .audio import build_filter, pick_music, prepare_job_assets
from .face import center_in

Progress = Callable[[float, str], None]


def clean_still(path: str, folder: Path, max_side: int) -> str:
    """Re-save a B-roll photo as a plain, clean JPEG. Downloaded 'photos' are sometimes WebP with a .jpg name or
    carry broken EXIF; looped for every frame, the decoder then prints an error per frame, which used to fill
    FFmpeg's error pipe and freeze the render. Also pre-sizes it, so the per-frame scaling is cheap."""
    import hashlib

    from PIL import Image, ImageOps
    src = Path(path)
    try:
        st = src.stat()
        key = hashlib.md5(f"{src.resolve()}|{st.st_size}|{st.st_mtime_ns}|{max_side}".encode()).hexdigest()[:16]
        out = folder / f"still_{key}.jpg"
        if out.exists():
            return str(out)
        folder.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (16, 16, 24))
                bg.paste(im, mask=im.getchannel("A"))
                im = bg
            else:
                im = im.convert("RGB")
            k = max_side / max(im.size)
            if k < 1:
                im = im.resize((max(2, round(im.width * k)), max(2, round(im.height * k))), Image.LANCZOS)
            tmp = out.with_suffix(".part.jpg")
            im.save(tmp, "JPEG", quality=93, subsampling=0)
            tmp.replace(out)
        return str(out)
    except Exception:  # noqa: BLE001  (unreadable by PIL: let FFmpeg try the original)
        return str(path)


def canvas_for(info: dict) -> tuple[int, int]:
    w, h = info["width"], info["height"]
    if w >= h:
        return (1920, 1080) if w >= 1600 or h >= 900 else (1280, 720)
    return (1080, 1920)


def output_dims(base_info: dict, vertical: bool, cfg: dict) -> tuple[int, int]:
    """Reel is always 9:16. YouTube is 16:9 (vertical sources get blurred side fill) unless 'keep' is chosen."""
    bw, bh = base_info["width"], base_info["height"]
    if vertical:
        return 1080, 1920
    if cfg.get("youtube_format", "landscape") == "keep":
        return bw, bh
    return (1920, 1080) if max(bw, bh) >= 1600 else (1280, 720)


def even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


# ----------------------------------------------------------------- pieces
def build_pieces(keeps: list[tuple[float, float]], zoom_ranges: list[tuple[float, float]], style: str,
                 fps: float, face_track: list[dict], smooth: bool = True) -> list[dict]:
    """Split keeps at emphasis boundaries and assign a zoom factor + crop centre to every piece."""
    def snap(x):
        return round(round(x * fps) / fps, 6)

    pieces = []
    for k, (s, e) in enumerate(keeps):
        cuts = sorted({snap(x) for zs, ze in zoom_ranges for x in (zs, ze) if s + 0.4 < x < e - 0.4})
        bounds = [s] + cuts + [e]
        for i in range(len(bounds) - 1):
            a, b = bounds[i], bounds[i + 1]
            mid = (a + b) / 2
            emph = any(zs <= mid <= ze for zs, ze in zoom_ranges)
            pieces.append({"s": a, "e": b, "keep": k, "emph": emph})
    zoom_alt = 0
    for i, p in enumerate(pieces):
        z = 1.0
        if style in ("jumpcut", "emphasis") and p["emph"]:
            z = 1.18
        elif style == "jumpcut":
            if i > 0 and pieces[i - 1]["keep"] != p["keep"]:
                zoom_alt ^= 1
            z = 1.08 if zoom_alt else 1.0
        cx, cy = center_in(face_track, p["s"], p["e"]) if face_track else (0.5, 0.4)
        p.update({"z": z, "cx": cx, "cy": min(0.6, cy + 0.08)})  # keep a bit of headroom
        # slow "camera push-in" on longer static shots keeps the frame alive
        p["push"] = bool(smooth and style != "off" and (p["e"] - p["s"]) >= 2.2 and not p["emph"])
    return pieces


def crop_expr(p: dict, sw: int, sh: int) -> str:
    z = p["z"]
    if z <= 1.001:
        return ""
    cw, ch = even(sw / z), even(sh / z)
    x = min(max(0, p["cx"] * sw - cw / 2), sw - cw)
    y = min(max(0, p["cy"] * sh - ch / 2), sh - ch)
    return f"crop={cw}:{ch}:{int(x)}:{int(y)},"


def render_base(src: Path, info: dict, pieces: list[dict], job_dir: Path, cfg: dict, on_progress: Progress,
                log: Callable[[str], None]) -> Path:
    W, H = canvas_for(info)
    fps = min(60.0, info["fps"])
    seg_dir = job_dir / "segments"
    if seg_dir.exists():
        shutil.rmtree(seg_dir)
    seg_dir.mkdir()
    vcodec = ffmpeg.video_codec_args(cfg.get("encoder", "auto"), "intermediate")
    done = [0]

    def one(i_p):
        i, p = i_p
        dur = p["e"] - p["s"]
        nframes = max(1, int(round(dur * fps)))
        out = seg_dir / f"seg_{i:05d}.mkv"
        push = ""
        if p.get("push"):
            amt = min(0.07, 0.018 * dur)  # ~2% per second, max 7%
            push = (f",scale=w='trunc({W}*(1+{amt:.4f}*t/{dur:.4f})/2)*2':h=-2:eval=frame:flags=bicubic,"
                    f"crop={W}:{H}")
        vf = f"{ffmpeg.hdr_to_sdr(info)}{ffmpeg.rot_prefix(info.get('rotate', 0))}" \
             f"{crop_expr(p, info['width'], info['height'])}" \
             f"scale={W}:{H}:force_original_aspect_ratio=increase," \
             f"crop={W}:{H}{push},setsar=1,fps={fps:.6f}"
        fade = min(0.01, dur / 4)
        args = ["-ss", f"{p['s']:.4f}", "-i", str(src)]
        if not info["has_audio"]:
            args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        args += ["-t", f"{dur:.4f}", "-map", "0:v:0", "-map", "0:a:0" if info["has_audio"] else "1:a:0",
                 "-vf", vf, "-frames:v", str(nframes),
                 "-af", f"aresample=48000,afade=t=in:d={fade:.4f},afade=t=out:st={max(0, dur - fade):.4f}:d={fade:.4f},"
                        f"apad,atrim=0:{nframes / fps:.6f}",
                 *vcodec, "-g", str(int(fps * 2)), "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(out)]
        ffmpeg.run(args)
        done[0] += 1
        on_progress(done[0] / len(pieces), f"Cutting {done[0]}/{len(pieces)}")
        return out

    workers = 3 if ffmpeg.pick_encoder(cfg.get("encoder", "auto")) != "x264" else 2
    with ThreadPoolExecutor(workers) as ex:
        outs = list(ex.map(one, enumerate(pieces)))
    lst = job_dir / "concat.txt"
    lst.write_text("".join(f"file 'segments/{o.name}'\n" for o in outs), encoding="utf-8")
    base = job_dir / "base.mkv"
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", "base.mkv"], cwd=job_dir)
    shutil.rmtree(seg_dir, ignore_errors=True)
    log(f"Base edit: {len(pieces)} pieces -> {base.name}")
    return base


# ------------------------------------------------------------- vertical crop
def vertical_crop_cmds(pieces: list[dict], face_track: list[dict], base_w: int, base_h: int, t0: float, t1: float,
                       job_dir: Path, name: str) -> tuple[int, str, list[tuple[float, int]]]:
    """Face-follow 9:16 crop of the landscape base. Writes a sendcmd file; returns (crop_w, filename, events)."""
    cw = even(base_h * 9 / 16)
    lines, t_edit = [], 0.0
    events: list[tuple[float, int]] = []
    last_x = None
    for p in pieces:
        dur = p["e"] - p["s"]
        a, b = t_edit, t_edit + dur
        t_edit = b
        if b < t0 or a > t1:
            continue
        z, cx = p["z"], p["cx"]
        x0 = 0.0
        if z > 1.001:
            x0 = min(max(0.0, cx - 0.5 / z), 1 - 1 / z)
        # sample face positions inside this piece every 1.5s for a gentle follow
        t = p["s"]
        while t < p["e"] - 1e-3:
            fx, _ = center_in(face_track, t, min(p["e"], t + 1.5), default=(cx, 0.4))
            fx_base = (fx - x0) * z
            x = int(min(max(0, fx_base * base_w - cw / 2), base_w - cw))
            if last_x is None or abs(x - last_x) > base_w * 0.03:
                last_x = x
            te = max(0.0, (a + (t - p["s"])) - t0)
            lines.append(f"{te:.3f} crop@vc x {last_x};")
            events.append((te, last_x))
            t += 1.5
    fn = f"{name}_crop.cmd"
    (job_dir / fn).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cw, fn, events


# ------------------------------------------------------------ face mapping
class OutMapper:
    """Where is the speaker's face in the OUTPUT frame at output time t? (for doodle placement)"""

    def __init__(self, src_info: dict, base_info: dict, pieces: list[dict], face_track: list[dict], W: int, H: int,
                 vertical: bool, layout: str, t0: float, crop: tuple[int, list[tuple[float, int]]] | None = None):
        self.sw, self.sh = src_info["width"], src_info["height"]
        self.bw, self.bh = base_info["width"], base_info["height"]
        self.pieces, self.trk = pieces, [f for f in face_track if f.get("cx") is not None]
        self.W, self.H, self.vertical, self.layout, self.t0 = W, H, vertical, layout, t0
        self.crop = crop
        self.acc = []
        a = 0.0
        for p in pieces:
            self.acc.append(a)
            a += p["e"] - p["s"]

    def _src_face(self, ts: float):
        best = None
        for f in self.trk:
            d = abs(f["t"] - ts)
            if d <= 0.6 and (best is None or d < best[0]):
                best = (d, f)
        return best[1] if best else None

    def at(self, t: float):
        te = t + self.t0
        i = max(0, min(len(self.pieces) - 1, sum(1 for a in self.acc if a <= te) - 1))
        p = self.pieces[i]
        ts = p["s"] + (te - self.acc[i])
        f = self._src_face(ts)
        if not f:
            return None
        sx, sy, ssz = f["cx"] * self.sw, f["cy"] * self.sh, f.get("size", 0.2) * self.sh
        z = p.get("z", 1.0)
        if z > 1.001:
            cw, ch = even(self.sw / z), even(self.sh / z)
            x0 = min(max(0, p["cx"] * self.sw - cw / 2), self.sw - cw)
            y0 = min(max(0, p["cy"] * self.sh - ch / 2), self.sh - ch)
        else:
            cw, ch, x0, y0 = self.sw, self.sh, 0.0, 0.0
        k = max(self.bw / cw, self.bh / ch)
        bx = (sx - x0) * k - (cw * k - self.bw) / 2
        by = (sy - y0) * k - (ch * k - self.bh) / 2
        bs = ssz * k
        W, H, bw, bh = self.W, self.H, self.bw, self.bh
        if self.vertical and bw > bh:
            if self.layout == "face" and self.crop:
                cwv, ev = self.crop
                cx = next((x for tt, x in reversed(ev) if tt <= t + 1e-3), ev[0][1] if ev else (bw - cwv) // 2)
                sc = W / cwv
                return (bx - cx) * sc, by * sc, bs * sc
            sc = W / bw
            return bx * sc, by * sc + (H - bh * sc) / 2 - H * 0.06, bs * sc
        if not self.vertical and bh > bw and W > H:
            from .layout import pillar_rect
            px0, py0, fw, fh = pillar_rect({"width": bw, "height": bh}, W, H)
            sc = fh / bh
            return bx * sc + px0, by * sc + py0, bs * sc
        sc = max(W / bw, H / bh)
        return bx * sc - (bw * sc - W) / 2, by * sc - (bh * sc - H) / 2, bs * sc

    def face(self, s: float, e: float):
        pts = [self.at(t) for t in np.arange(s, max(s + 0.01, e), 0.25)]
        pts = [p for p in pts if p and 0 <= p[0] <= self.W and 0 <= p[1] <= self.H]
        if len(pts) < max(1, len(np.arange(s, max(s + 0.01, e), 0.25)) // 2):
            return None
        arr = np.array(pts)
        return float(np.median(arr[:, 0])), float(np.median(arr[:, 1])), float(np.median(arr[:, 2]))


# ----------------------------------------------------------------- final
def render_final(job_dir: Path, base: Path, base_info: dict, out: Path, *, vertical: bool, t0: float, t1: float,
                 broll: list[dict], ass_file: str | None, cfg: dict, pieces: list[dict], face_track: list[dict],
                 layout: str, on_progress: Progress, log: Callable[[str], None],
                 sfx_events: list[tuple[float, str, float]] | None = None, doodles: list[dict] | None = None,
                 outlines: list[dict] | None = None, src_info: dict | None = None,
                 busy: list[dict] | None = None) -> Path:
    bw, bh = base_info["width"], base_info["height"]
    fps = min(60.0, base_info["fps"])
    W, H = output_dims(base_info, vertical, cfg)
    dur = t1 - t0
    have_rn = prepare_job_assets(job_dir)
    fonts_dir = job_dir / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    from ..core.paths import FONTS
    if FONTS.exists():
        for f in FONTS.glob("*.ttf"):
            if not (fonts_dir / f.name).exists():
                shutil.copy(f, fonts_dir / f.name)

    inputs = ["-ss", f"{t0:.4f}", "-t", f"{dur:.4f}", "-i", str(base)]
    g: list[str] = []
    idx = 1
    # ---- subject outline (drawn in base-video space, so it follows every crop/zoom below)
    src_v = "[0:v]"
    for oi, o in enumerate(outlines or []):
        s0, e0 = o["s"], min(o["e"], dur)
        if e0 - s0 < 0.4 or not o.get("frames"):
            continue
        inputs += ["-framerate", "10", "-i", f"{o['dir']}/f_%04d.png"]
        g.append(f"[{idx}:v]scale={bw}:{bh},format=rgba,setpts=PTS-STARTPTS+{s0:.3f}/TB[ol{oi}]")
        g.append(f"{src_v}[ol{oi}]overlay=0:0:eof_action=pass:enable='between(t,{s0:.3f},{e0:.3f})'[sv{oi}]")
        src_v = f"[sv{oi}]"
        idx += 1
    crop = None
    # ---- split-screen moments (you glide aside, the B-roll / card fills a panel next to you)
    from . import layout as lay
    lmode = lay.mode_for(vertical, base_info, W, H)
    geo = lay.geometry(lmode, W, H, base_info)
    win = []
    for b in broll:
        s_, e_ = max(b["t0"], t0) - t0, min(b["t1"], t1) - t0
        graphic = b.get("kind") == "graphic"
        if b.get("style") == "split" and e_ - s_ >= 0.8 and (graphic or (b.get("file") and Path(b["file"]).exists())):
            win.append((s_, e_))
    mdir = job_dir / "masks"
    # ---- main video
    pillar = not vertical and bh > bw and W > H
    if vertical and bw > bh:
        if layout == "face":
            cw, cmd, cev = vertical_crop_cmds(pieces, face_track, bw, bh, t0, t1, job_dir, out.stem)
            crop = (cw, cev)
            g.append(f"{src_v}sendcmd=f={cmd},crop@vc=w={cw}:h={bh}:x={(bw - cw) // 2}:y=0,scale={W}:{H},setsar=1[v0]")
        else:  # blur-fill
            g.append(f"{src_v}split=2[bgs][fgs];[bgs]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                     f"boxblur=25:2,eq=brightness=-0.08:saturation=1.2[bg];[fgs]scale={W}:-2[fg];"
                     f"[bg][fg]overlay=(W-w)/2:(H-h)/2-{int(H * 0.06)},setsar=1[v0]")
    elif pillar:
        # vertical recording -> 16:9: you as a rounded card on a soft blurred backdrop; glides left for split moments
        px0, py0, fw, fh = lay.pillar_rect(base_info, W, H)
        tx = geo["card_to"][0]
        X = f"{px0}" + "".join(f"+({tx - px0})*{lay.ease(s_, e_)}" for s_, e_ in win)
        mk = lay.mask_png(mdir, fw, fh)
        fr, pad = lay.frame_png(mdir, fw, fh)
        inputs += ["-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{dur:.3f}", "-i", f"masks/{mk}",
                   "-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{dur:.3f}", "-i", f"masks/{fr}"]
        mi, fi = idx, idx + 1
        idx += 2
        g.append(f"{src_v}split=2[bgs][fgs];[bgs]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,"
                 f"crop={W // 4}:{H // 4},boxblur=12:2,scale={W}:{H},eq=brightness=-0.16:saturation=1.15,"
                 f"vignette=PI/5[bg];[fgs]scale={fw}:{fh},format=yuva420p[fg0];[{mi}:v]format=gray[mk];"
                 f"[fg0][mk]alphamerge[fg];[bg][{fi}:v]overlay=x='{X}-{pad}':y={py0 - pad}:eval=frame[bgf];"
                 f"[bgf][fg]overlay=x='{X}':y={py0}:eval=frame,setsar=1[v0]")
    else:
        g.append(f"{src_v}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[v0]")
    cur = "[v0]"
    if cfg.get("color_grade", True):  # gentle "creator look": contrast, colour, crispness
        g.append(f"{cur}eq=contrast=1.06:saturation=1.16:gamma=0.98,unsharp=5:5:0.45:5:5:0.0[vg]")
        cur = "[vg]"
    mapper0 = OutMapper(src_info or base_info, base_info, pieces, face_track, W, H, vertical, layout, t0, crop)
    grade = ",eq=contrast=1.06:saturation=1.16:gamma=0.98" if cfg.get("color_grade", True) else ""

    def base_input(S: float, bd: float) -> int:
        """The speaker picture for a card comes from its own read of base.mkv (no huge frame queues)."""
        nonlocal idx
        inputs.extend(["-ss", f"{t0 + S:.4f}", "-t", f"{bd:.4f}", "-i", str(base)])
        idx += 1
        return idx - 1

    def out_to_base(x: float, y: float, w: float, h: float, S: float) -> tuple[int, int, int, int]:
        """Output-frame rectangle -> the same pixels in base.mkv."""
        if vertical and bw > bh and layout == "face" and crop:
            cwv, ev = crop
            cx = next((xx for tt, xx in reversed(ev) if tt <= S + 1e-3), ev[0][1] if ev else (bw - cwv) // 2)
            k = cwv / W
            return int(cx + x * k), int(y * k), int(w * k), int(h * k)
        if vertical and bw > bh:  # blur layout: base scaled to output width, shifted up a little
            k = bw / W
            yo = (H - bh / k) / 2 - H * 0.06
            return int(x * k), int((y - yo) * k), int(w * k), int(h * k)
        k = max(bw / W, bh / H) if not vertical else bw / W
        ox, oy = (bw - W * k) / 2, (bh - H * k) / 2
        return int(ox + x * k), int(oy + y * k), int(w * k), int(h * k)

    def card_from_base(i: int, S: float, bd: float, rect: tuple[int, int, int, int], cw: int, ch: int) -> str:
        bx, by, bw2, bh2 = rect
        bx, by = max(0, min(bx, bw - 2)), max(0, min(by, bh - 2))
        bw2, bh2 = max(2, min(bw2, bw - bx)) // 2 * 2, max(2, min(bh2, bh - by)) // 2 * 2
        return f"[{i}:v]crop={bw2}:{bh2}:{bx}:{by},scale={cw}:{ch}{grade},fps={fps:.6f},setsar=1"
    # ---- b-roll overlays (footage, images, or animated text-card backgrounds)
    n_b = 0
    wi = 0
    for bi, b in enumerate(broll):
        s, e = max(b["t0"], t0), min(b["t1"], t1)
        graphic = b.get("kind") == "graphic"
        if e - s < 0.8 or (not graphic and (not b.get("file") or not Path(b["file"]).exists())):
            continue
        bd = e - s
        S, E = s - t0, e - t0
        split = b.get("style") == "split" and wi < len(win) and abs(win[wi][0] - S) < 1e-3
        px, py, pw, ph = geo["panel"] if split else (0, 0, W, H)
        if split and lmode == "pip":
            px, py, pw, ph = 0, 0, W, H
        if graphic:
            src = (f"gradients=s={pw}x{ph}:c0=0x140C2E:c1=0x6A3DFF:c2=0xFF3D7F:nb_colors=3:speed=0.03:"
                   f"d={bd:.3f}:r={fps:.3f},vignette=PI/4")
        elif b["kind"] == "image":
            still = clean_still(b["file"], job_dir / "stills", max(int(pw * 1.12), int(ph * 1.12)))
            inputs += ["-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{bd:.3f}", "-i", still]
            src = (f"[{idx}:v]scale={int(pw * 1.12)}:{int(ph * 1.12)}:force_original_aspect_ratio=increase,"
                   f"crop={pw}:{ph}:x='(iw-ow)*t/{bd:.3f}':y='(ih-oh)/2'")
            idx += 1
        else:
            inputs += ["-ss", f"{b.get('file_start', 0):.3f}", "-t", f"{bd:.3f}", "-i", b["file"]]
            src = f"[{idx}:v]scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph}"
            idx += 1
        if not graphic and cfg.get("color_grade", True):
            src += ",eq=contrast=1.04:saturation=1.1"
        fd = min(0.15, bd / 4)
        if split and lmode in ("pillar", "side"):
            ez = lay.ease(S, E)
            if lmode == "side":  # the rest of the frame softly blurs away behind you
                bi_in = base_input(S, bd)
                g.append(f"[{bi_in}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}{grade},"
                         f"fps={fps:.6f},setsar=1,split[cb{wi}][cc{wi}]")
                g.append(f"[cb{wi}]scale={W // 4}:{H // 4},boxblur=10:2,scale={W}:{H},eq=brightness=-0.18,"
                         f"format=yuva420p,fade=t=in:st=0:d=0.3:alpha=1,fade=t=out:st={max(0, bd - 0.3):.3f}:d=0.3:"
                         f"alpha=1,setpts=PTS-STARTPTS+{S:.3f}/TB[bb{wi}]")
                g.append(f"{cur}[bb{wi}]overlay=0:0:eof_action=pass:enable='between(t,{S:.3f},{E:.3f})'[ob{wi}]")
                cur = f"[ob{wi}]"
            # panel: rounded, slides in from the right while you glide aside
            pm = lay.mask_png(mdir, pw, ph, 0.05)
            pfr, ppad = lay.frame_png(mdir, pw, ph, 0.05)
            inputs += ["-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{bd:.3f}", "-i", f"masks/{pm}",
                       "-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{bd:.3f}", "-i", f"masks/{pfr}"]
            mi, fi = idx, idx + 1
            idx += 2
            g.append(f"{src},fps={fps:.6f},setsar=1,format=yuva420p[pb{bi}];[{mi}:v]format=gray[pm{bi}];"
                     f"[pb{bi}][pm{bi}]alphamerge,setpts=PTS-STARTPTS+{S:.3f}/TB[b{bi}];"
                     f"[{fi}:v]format=rgba,setpts=PTS-STARTPTS+{S:.3f}/TB[pf{bi}]")
            PX = f"{px}+({W - px})*(1-{ez})"
            g.append(f"{cur}[pf{bi}]overlay=x='{PX}-{ppad}':y={py - ppad}:eval=frame:eof_action=pass:"
                     f"enable='between(t,{S:.3f},{E:.3f})'[of{bi}]")
            g.append(f"[of{bi}][b{bi}]overlay=x='{PX}':y={py}:eval=frame:eof_action=pass:"
                     f"enable='between(t,{S:.3f},{E:.3f})'[o{bi}]")
            cur = f"[o{bi}]"
            if lmode == "side":  # your face region lifts out as a card and glides to the left
                face = mapper0.face(S, min(E, S + 1.0)) or (W * 0.5, H * 0.4, H * 0.3)
                cw, ch = geo["card_size"]
                sx = int(min(max(0, face[0] - cw / 2), W - cw)) // 2 * 2
                sy = int(min(max(0, face[1] - ch * 0.42), H - ch)) // 2 * 2
                tx, ty = geo["card_to"]
                cm = lay.mask_png(mdir, cw, ch)
                cfr, cpad = lay.frame_png(mdir, cw, ch)
                inputs += ["-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{bd:.3f}", "-i", f"masks/{cm}",
                           "-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{bd:.3f}", "-i", f"masks/{cfr}"]
                mi, fi = idx, idx + 1
                idx += 2
                g.append(f"[cc{wi}]crop={cw}:{ch}:{sx}:{sy},format=yuva420p[ck{wi}];[{mi}:v]format=gray[cmk{wi}];"
                         f"[ck{wi}][cmk{wi}]alphamerge,setpts=PTS-STARTPTS+{S:.3f}/TB[card{wi}];"
                         f"[{fi}:v]format=rgba,setpts=PTS-STARTPTS+{S:.3f}/TB[cf{wi}]")
                CX, CY = f"{sx}+({tx - sx})*{ez}", f"{sy}+({ty - sy})*{ez}"
                g.append(f"{cur}[cf{wi}]overlay=x='{CX}-{cpad}':y='{CY}-{cpad}':eval=frame:eof_action=pass:"
                         f"enable='between(t,{S:.3f},{E:.3f})'[ocf{wi}]")
                g.append(f"[ocf{wi}][card{wi}]overlay=x='{CX}':y='{CY}':eval=frame:eof_action=pass:"
                         f"enable='between(t,{S:.3f},{E:.3f})'[oc{wi}]")
                cur = f"[oc{wi}]"
            wi += 1
            n_b += 1
            continue
        trans = cfg.get("broll_transition", "slide")
        fade_in = "" if trans == "slide" else f"fade=t=in:st=0:d={fd:.3f}:alpha=1,"
        g.append(f"{src},fps={fps:.6f},setsar=1,format=yuva420p,"
                 f"{fade_in}fade=t=out:st={bd - fd:.3f}:d={fd:.3f}:alpha=1,"
                 f"setpts=PTS-STARTPTS+{S:.3f}/TB[b{bi}]")
        # "slide": slides in from the right with an ease-out; anything else: soft fade
        ox = f"x='if(lt(t,{S + 0.28:.3f}),W*pow(1-(t-{S:.3f})/0.28,3),0)':y=0:" if trans == "slide" else ""
        g.append(f"{cur}[b{bi}]overlay={ox}eof_action=pass:enable='between(t,{S:.3f},{E:.3f})'[o{bi}]")
        cur = f"[o{bi}]"
        if split and lmode == "pip":  # Reel: you stay on screen as a picture-in-picture card
            face = mapper0.face(S, min(E, S + 1.0)) or (W * 0.5, H * 0.4, H * 0.25)
            cw, ch = geo["card_size"]
            # show head + shoulders: take a region ~2x the card and shrink it into the card
            z = min(1.9, W / cw, H / ch)
            rw, rh = cw * z, ch * z
            rx = min(max(0, face[0] - rw / 2), W - rw)
            ry = min(max(0, face[1] - rh * 0.42), H - rh)
            sx = int(min(max(0, face[0] - cw / 2), W - cw)) // 2 * 2
            sy = int(min(max(0, face[1] - ch * 0.45), H - ch)) // 2 * 2
            tx, ty = geo["card_to"]
            ez = lay.ease(S, E)
            cm = lay.mask_png(mdir, cw, ch, 0.08)
            cfr, cpad = lay.frame_png(mdir, cw, ch, 0.08)
            inputs += ["-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{bd:.3f}", "-i", f"masks/{cm}",
                       "-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{bd:.3f}", "-i", f"masks/{cfr}"]
            mi, fi = idx, idx + 1
            idx += 2
            src_in = base_input(S, bd)
            g.append(f"{card_from_base(src_in, S, bd, out_to_base(rx, ry, rw, rh, S), cw, ch)},"
                     f"format=yuva420p[ck{wi}];[{mi}:v]format=gray[cmk{wi}];"
                     f"[ck{wi}][cmk{wi}]alphamerge,setpts=PTS-STARTPTS+{S:.3f}/TB[card{wi}];"
                     f"[{fi}:v]format=rgba,setpts=PTS-STARTPTS+{S:.3f}/TB[cf{wi}]")
            CX, CY = f"{sx}+({tx - sx})*{ez}", f"{sy}+({ty - sy})*{ez}"
            g.append(f"{cur}[cf{wi}]overlay=x='{CX}-{cpad}':y='{CY}-{cpad}':eval=frame:eof_action=pass:"
                     f"enable='between(t,{S:.3f},{E:.3f})'[ocf{wi}]")
            g.append(f"[ocf{wi}][card{wi}]overlay=x='{CX}':y='{CY}':eval=frame:eof_action=pass:"
                     f"enable='between(t,{S:.3f},{E:.3f})'[oc{wi}]")
            cur = f"[oc{wi}]"
            wi += 1
        n_b += 1
    # ---- doodles (hand-drawn animated icons, placed around the face)
    if doodles:
        from .doodles import DOODLE_FPS, callout_box, place, render_frames
        mapper = OutMapper(src_info or base_info, base_info, pieces, face_track, W, H, vertical, layout, t0, crop)
        portrait = H > W
        cap_size = W * (0.095 if portrait else 0.05)
        cap_bottom = H * (0.73 if portrait else 0.925)
        band = (cap_bottom - cap_size * 2.4, cap_bottom + cap_size * 0.3) if cfg.get("captions", True) else (H, H)
        ddir = job_dir / "doodles" / out.stem
        placed = 0
        for di, d in enumerate(doodles):
            ds0, de0 = d["s"], min(d["e"], dur - 0.05)
            if de0 - ds0 < 0.6:
                continue
            if d.get("box"):
                bx0, by0, bw0, bh0 = d["box"]
                pl = {"x": int(bx0), "y": int(by0), "w": int(bw0) // 2 * 2, "h": int(bh0) // 2 * 2,
                      "flip": bool(d.get("flip"))}
            elif not d.get("callout_text") and any(ds0 < we + 0.2 and de0 > ws - 0.2 for ws, we in win):
                continue  # you're a small card right now - the panel carries the visual
            elif d.get("callout_text"):
                bx = callout_box(d["callout_text"], W, H)
                bw_, bh_ = bx[2] - bx[0], bx[3] - bx[1]
                if d["shape"] == "underline":
                    box = (bx[0] - bw_ * 0.06, bx[3] - bh_ * 0.05, bw_ * 1.12, bh_ * 0.62)
                else:
                    box = (bx[0] - bw_ * 0.16, bx[1] - bh_ * 0.42, bw_ * 1.32, bh_ * 1.84)
                pl = {"x": int(box[0]), "y": int(box[1]), "w": int(box[2]) // 2 * 2, "h": int(box[3]) // 2 * 2,
                      "flip": False}
            else:
                face = mapper.face(ds0, de0)
                avoid = [callout_box(c["text"], W, H) for c in (busy or []) if c["s"] < de0 and c["e"] > ds0]
                pl = place(d, W, H, face, avoid, band)
            if not pl or pl["w"] < 24 or pl["h"] < 24:
                continue
            ev = {**d, "flip": pl["flip"]}
            fdir = ddir / f"d{di:03d}"
            n = render_frames(ev, pl["w"], pl["h"], de0 - ds0, fdir)
            if not n:
                continue
            inputs += ["-framerate", str(DOODLE_FPS), "-i", f"doodles/{out.stem}/d{di:03d}/f_%04d.png"]
            g.append(f"[{idx}:v]format=rgba,setpts=PTS-STARTPTS+{ds0:.3f}/TB[dd{di}]")
            g.append(f"{cur}[dd{di}]overlay=x={pl['x']}:y={pl['y']}:eof_action=pass:"
                     f"enable='between(t,{ds0:.3f},{de0:.3f})'[do{di}]")
            cur = f"[do{di}]"
            idx += 1
            placed += 1
            if sfx_events is not None and d.get("sound"):
                sfx_events.append((ds0 + 0.05, d["sound"], -9.0 if d["sound"] == "scribble" else -6.0))
        if placed:
            log(f"Doodles: {placed} drawn for {out.name}")
            if sfx_events:
                sfx_events.sort()
    # ---- progress bar
    if cfg.get("progress_bar", True):
        ph = max(6, H // 140)
        g.append(f"color=c=0xFFD000:s={W}x{ph}:r={fps:.3f}:d={dur:.3f}[pb]")
        g.append(f"{cur}[pb]overlay=x='-w+w*t/{dur:.3f}':y=H-h:eof_action=pass[pbo]")
        cur = "[pbo]"
    # ---- captions / callouts
    if ass_file:
        g.append(f"{cur}ass={ass_file}:fontsdir=fonts[vc]")
        cur = "[vc]"
    g.append(f"{cur}format=yuv420p[vout]")
    # ---- audio
    music = pick_music(cfg.get("music_folder", "")) if cfg.get("music") else None
    music_in = None
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        music_in = f"[{idx}:a]"
        idx += 1
        log(f"Music: {music.name}")
    sfx_in = None
    if sfx_events and cfg.get("sfx", True):
        from .sfx import build_track
        trk = build_track(sfx_events, dur, job_dir / f"{out.stem}_sfx.wav")
        if trk:
            inputs += ["-i", trk.name]
            sfx_in = f"[{idx}:a]"
            idx += 1
    g.append(build_filter(cfg, "[0:a]", music_in, dur, have_rn, sfx_in))

    graph = ";".join(g)
    (job_dir / f"{out.stem}_graph.txt").write_text(graph.replace(";", ";\n"), encoding="utf-8")  # for debugging
    args = [*inputs, "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
            *ffmpeg.video_codec_args(cfg.get("encoder", "auto"), "final"), "-r", f"{fps:.3f}",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-t", f"{dur:.3f}", str(out)]
    log(f"Rendering {out.name} ({W}x{H}, {dur:.1f}s, {n_b} b-roll)")
    ffmpeg.run(args, cwd=job_dir, duration=dur, on_progress=lambda p: on_progress(p, f"Rendering {out.name}"),
               log=log)
    return out
