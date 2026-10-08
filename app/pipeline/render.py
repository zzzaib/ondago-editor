"""Rendering.

1. base.mkv   : the cut ("jump-cut") edit, with punch-in zooms, frame-accurate, near-lossless.
2. finals     : B-roll overlays + captions/callouts + progress bar + audio post, one per output
                (YouTube 16:9 / Reel 9:16 face-tracked or blur-fill, full or highlight).
All filtergraph file references are RELATIVE to the job folder (cwd) -> no Windows path escaping issues.
"""
from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from ..core import ffmpeg
from .audio import build_filter, pick_music, prepare_job_assets
from .face import center_in

Progress = Callable[[float, str], None]


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
        vf = f"{ffmpeg.rot_prefix(info.get('rotate', 0))}{crop_expr(p, info['width'], info['height'])}" \
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

    workers = 3 if "nvenc" in " ".join(vcodec) else 2
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
                       job_dir: Path, name: str) -> tuple[int, str]:
    """Face-follow 9:16 crop of the landscape base. Writes a sendcmd file; returns (crop_w, filename)."""
    cw = even(base_h * 9 / 16)
    lines, t_edit = [], 0.0
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
            t += 1.5
    fn = f"{name}_crop.cmd"
    (job_dir / fn).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cw, fn


# ----------------------------------------------------------------- final
def render_final(job_dir: Path, base: Path, base_info: dict, out: Path, *, vertical: bool, t0: float, t1: float,
                 broll: list[dict], ass_file: str | None, cfg: dict, pieces: list[dict], face_track: list[dict],
                 layout: str, on_progress: Progress, log: Callable[[str], None],
                 sfx_events: list[tuple[float, str, float]] | None = None) -> Path:
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
    # ---- main video
    if vertical and bw > bh:
        if layout == "face":
            cw, cmd = vertical_crop_cmds(pieces, face_track, bw, bh, t0, t1, job_dir, out.stem)
            g.append(f"[0:v]sendcmd=f={cmd},crop@vc=w={cw}:h={bh}:x={(bw - cw) // 2}:y=0,scale={W}:{H},setsar=1[v0]")
        else:  # blur-fill
            g.append(f"[0:v]split=2[bgs][fgs];[bgs]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                     f"boxblur=25:2,eq=brightness=-0.08:saturation=1.2[bg];[fgs]scale={W}:-2[fg];"
                     f"[bg][fg]overlay=(W-w)/2:(H-h)/2-{int(H * 0.06)},setsar=1[v0]")
    elif not vertical and bh > bw and W > H:
        # vertical recording -> 16:9 YouTube: sharp video centred, blurred copy filling the sides
        g.append(f"[0:v]split=2[bgs][fgs];[bgs]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                 f"boxblur=30:2,eq=brightness=-0.1:saturation=1.2[bg];[fgs]scale=-2:{H}[fg];"
                 f"[bg][fg]overlay=(W-w)/2:0,setsar=1[v0]")
    else:
        g.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[v0]")
    cur = "[v0]"
    if cfg.get("color_grade", True):  # gentle "creator look": contrast, colour, crispness
        g.append(f"{cur}eq=contrast=1.06:saturation=1.16:gamma=0.98,unsharp=5:5:0.45:5:5:0.0[vg]")
        cur = "[vg]"
    idx = 1
    # ---- b-roll overlays (footage, images, or animated text-card backgrounds)
    n_b = 0
    for bi, b in enumerate(broll):
        s, e = max(b["t0"], t0), min(b["t1"], t1)
        graphic = b.get("kind") == "graphic"
        if e - s < 0.8 or (not graphic and (not b.get("file") or not Path(b["file"]).exists())):
            continue
        bd = e - s
        if graphic:
            src = (f"gradients=s={W}x{H}:c0=0x140C2E:c1=0x6A3DFF:c2=0xFF3D7F:nb_colors=3:speed=0.03:"
                   f"d={bd:.3f}:r={fps:.3f},vignette=PI/4")
        elif b["kind"] == "image":
            inputs += ["-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{bd:.3f}", "-i", b["file"]]
            src = (f"[{idx}:v]scale={int(W * 1.12)}:{int(H * 1.12)}:force_original_aspect_ratio=increase,"
                   f"crop={W}:{H}:x='(iw-ow)*t/{bd:.3f}':y='(ih-oh)/2'")
            idx += 1
        else:
            inputs += ["-ss", f"{b.get('file_start', 0):.3f}", "-t", f"{bd:.3f}", "-i", b["file"]]
            src = f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
            idx += 1
        fd = min(0.15, bd / 4)
        trans = cfg.get("broll_transition", "slide")
        fade_in = "" if trans == "slide" else f"fade=t=in:st=0:d={fd:.3f}:alpha=1,"
        if not graphic and cfg.get("color_grade", True):
            src += ",eq=contrast=1.04:saturation=1.1"
        g.append(f"{src},fps={fps:.6f},setsar=1,format=yuva420p,"
                 f"{fade_in}fade=t=out:st={bd - fd:.3f}:d={fd:.3f}:alpha=1,"
                 f"setpts=PTS-STARTPTS+{s - t0:.3f}/TB[b{bi}]")
        S = s - t0
        # "slide": slides in from the right with an ease-out; anything else: soft fade
        ox = f"x='if(lt(t,{S + 0.28:.3f}),W*pow(1-(t-{S:.3f})/0.28,3),0)':y=0:" if trans == "slide" else ""
        g.append(f"{cur}[b{bi}]overlay={ox}eof_action=pass:enable='between(t,{S:.3f},{e - t0:.3f})'[o{bi}]")
        cur = f"[o{bi}]"
        n_b += 1
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
