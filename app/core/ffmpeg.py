"""FFmpeg helpers: probe, run-with-progress, encoder detection, frame streaming."""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from .paths import IS_WIN, ffmpeg_bin, ffprobe_bin

CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0


class FFmpegError(RuntimeError):
    pass


def _popen(args, **kw):
    if IS_WIN:
        kw.setdefault("creationflags", CREATE_NO_WINDOW)
    return subprocess.Popen(args, **kw)


def run(args: list[str], cwd: str | Path | None = None, duration: float | None = None,
        on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] | None = None) -> None:
    """Run ffmpeg (args WITHOUT the binary). Reports 0..1 progress if duration given."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", "-loglevel", "error", *args]
    if log:
        log("ffmpeg " + " ".join(str(a) for a in args)[:600])
    p = _popen(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               text=True, encoding="utf-8", errors="replace")
    assert p.stdout is not None
    for line in p.stdout:
        if on_progress and duration and line.startswith("out_time_us="):
            try:
                us = int(line.split("=", 1)[1])
                on_progress(max(0.0, min(1.0, us / 1e6 / duration)))
            except ValueError:
                pass
    err = p.stderr.read() if p.stderr else ""
    rc = p.wait()
    if rc != 0:
        raise FFmpegError(f"ffmpeg failed ({rc}):\n{err[-3000:]}")


def probe(path: str | Path) -> dict:
    cmd = [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                         creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
    if out.returncode != 0:
        raise FFmpegError(out.stderr)
    data = json.loads(out.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    info = {"duration": float(data["format"].get("duration", 0) or 0), "has_audio": a is not None,
            "width": 0, "height": 0, "fps": 30.0, "rotation": 0}
    if v:
        info["width"], info["height"] = int(v["width"]), int(v["height"])
        num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den) if float(den) else 30.0
        info["fps"] = fps if 1 < fps < 241 else 30.0
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        rot = rot or int((v.get("tags") or {}).get("rotate", 0) or 0)
        info["rotation"] = rot % 360
        if info["rotation"] in (90, 270):  # ffmpeg autorotates on decode
            info["width"], info["height"] = info["height"], info["width"]
    return info


@lru_cache(maxsize=1)
def available_encoders() -> str:
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True,
                         creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
    return out.stdout


@lru_cache(maxsize=4)
def nvenc_works() -> bool:
    if "h264_nvenc" not in available_encoders():
        return False
    try:
        run(["-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"])
        return True
    except Exception:
        return False


def video_codec_args(setting: str = "auto", quality: str = "final") -> list[str]:
    """Encoder args. quality: 'final' or 'intermediate' (near-lossless)."""
    use_nv = setting == "nvenc" or (setting == "auto" and nvenc_works())
    if use_nv:
        cq = "16" if quality == "intermediate" else "20"
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", cq, "-b:v", "0",
                "-profile:v", "high", "-pix_fmt", "yuv420p"]
    crf = "14" if quality == "intermediate" else "19"
    preset = "veryfast" if quality == "intermediate" else "medium"
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]


# Extra clockwise rotation applied on top of the file's own rotation metadata (fixes sideways phone clips)
ROT_VF = {0: "", 90: "transpose=1", 270: "transpose=2", 180: "hflip,vflip"}


def rot_prefix(rotate: int) -> str:
    vf = ROT_VF.get(int(rotate or 0) % 360, "")
    return vf + "," if vf else ""


def iter_frames(path: str | Path, fps: float = 4.0, width: int = 480, start: float | None = None,
                duration: float | None = None, rotate: int = 0) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (timestamp, BGR frame) at a fixed sample rate, downscaled for analysis."""
    args = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    info = probe(path)
    w0, h0 = info["width"], info["height"]
    if int(rotate or 0) % 180 == 90:
        w0, h0 = h0, w0
    h = int(round(h0 * width / max(1, w0) / 2) * 2)
    args += ["-vf", f"{rot_prefix(rotate)}fps={fps},scale={width}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-an", "pipe:1"]
    p = _popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = width * h * 3
    i = 0
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield (start or 0.0) + i / fps, np.frombuffer(buf, np.uint8).reshape(h, width, 3)
            i += 1
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def extract_frame(path: str | Path, t: float, out: str | Path, rotate: int = 0) -> Path:
    vf = ["-vf", ROT_VF[int(rotate) % 360]] if ROT_VF.get(int(rotate or 0) % 360) else []
    run(["-ss", f"{max(0.0, t):.3f}", "-i", str(path), *vf, "-frames:v", "1", "-q:v", "2", str(out)])
    return Path(out)


def extract_audio_wav(path: str | Path, out: str | Path, sr: int = 16000) -> Path:
    run(["-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(out)])
    return Path(out)
