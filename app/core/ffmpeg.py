"""FFmpeg helpers: probe, run-with-progress, encoder detection, frame streaming."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from collections import deque
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


STALL_SECONDS = 300  # no progress for this long = FFmpeg is stuck: stop it instead of waiting forever


def run(args: list[str], cwd: str | Path | None = None, duration: float | None = None,
        on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] | None = None) -> None:
    """Run ffmpeg (args WITHOUT the binary). Reports 0..1 progress if duration given.

    stderr is drained on its own thread (a full error pipe - only 4 KB on Windows - would otherwise freeze FFmpeg),
    a watchdog stops FFmpeg if it makes no progress for STALL_SECONDS, and long jobs log their progress."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", "-loglevel", "error", *args]
    if log:
        log("ffmpeg " + " ".join(str(a) for a in args)[:600])
    p = _popen(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               text=True, encoding="utf-8", errors="replace")
    assert p.stdout is not None and p.stderr is not None
    err_tail: deque[str] = deque(maxlen=60)
    err_count = [0]

    def drain():
        for ln in p.stderr:
            err_count[0] += 1
            err_tail.append(ln.rstrip())
    t_err = threading.Thread(target=drain, daemon=True)
    t_err.start()

    state = {"last": time.monotonic(), "pos": -1.0, "ended": False, "stalled": False}

    def watchdog():
        while p.poll() is None:
            time.sleep(5)
            if not state["ended"] and time.monotonic() - state["last"] > STALL_SECONDS and p.poll() is None:
                state["stalled"] = True
                p.kill()
                return
    threading.Thread(target=watchdog, daemon=True).start()

    t0 = time.monotonic()
    next_log = 0.1
    speed = ""
    for line in p.stdout:
        if line.startswith("out_time_us="):
            try:
                pos = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            if pos > state["pos"] + 1e-3:
                state["pos"], state["last"] = pos, time.monotonic()
            if duration:
                frac = max(0.0, min(1.0, pos / duration))
                if on_progress:
                    on_progress(frac)
                if log and duration > 20 and frac >= next_log:
                    el = time.monotonic() - t0
                    left = el * (1 - frac) / max(frac, 1e-3)
                    log(f"  encoding {int(frac * 100)}%  ({speed or '?'} speed, about {int(left // 60)}m "
                        f"{int(left % 60):02d}s left)")
                    next_log = int(frac * 10 + 1) / 10
        elif line.startswith("speed="):
            speed = line.split("=", 1)[1].strip()
        elif line.startswith("progress=end"):
            state["ended"] = True
    rc = p.wait()
    t_err.join(timeout=5)
    err = "\n".join(err_tail)
    if state["stalled"]:
        raise FFmpegError(f"FFmpeg stopped making progress for {STALL_SECONDS // 60} minutes and was stopped "
                          f"(at {state['pos']:.1f}s).\n{err[-2000:]}")
    if rc != 0:
        raise FFmpegError(f"ffmpeg failed ({rc}):\n{err[-3000:]}")
    if err_count[0] > 50 and log:
        log(f"  note: FFmpeg reported {err_count[0]} warnings, e.g. {err_tail[0][:160] if err_tail else ''}")


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
            "width": 0, "height": 0, "fps": 30.0, "rotation": 0, "transfer": "", "pix_fmt": ""}
    if v:
        info["width"], info["height"] = int(v["width"]), int(v["height"])
        info["transfer"] = v.get("color_transfer", "") or ""
        info["pix_fmt"] = v.get("pix_fmt", "") or ""
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


NVENC_ERROR = ""


# GPU video encoders, tried in this order by "auto". All three come with the FFmpeg build the installer downloads.
HW_ENCODERS = {"nvenc": ("h264_nvenc", "NVIDIA NVENC"), "amf": ("h264_amf", "AMD AMF"),
               "qsv": ("h264_qsv", "Intel Quick Sync")}
_HW_TEST = {"nvenc": ["-pix_fmt", "yuv420p", "-c:v", "h264_nvenc"],
            "amf": ["-pix_fmt", "yuv420p", "-c:v", "h264_amf", "-usage", "transcoding"],
            "qsv": ["-pix_fmt", "nv12", "-c:v", "h264_qsv"]}
HW_ERRORS: dict[str, str] = {}


@lru_cache(maxsize=8)
def hw_works(kind: str) -> bool:
    """Can this GPU encoder actually encode here? (needs the right GPU + driver). Reason kept in HW_ERRORS."""
    global NVENC_ERROR
    codec, _ = HW_ENCODERS[kind]
    if codec not in available_encoders():
        HW_ERRORS[kind] = f"this FFmpeg build has no {codec}"
    else:
        try:
            run(["-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=0.5", *_HW_TEST[kind], "-f", "null", "-"])
            HW_ERRORS.pop(kind, None)
            return True
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            low = msg.lower()
            absent = {"nvenc": ("cannot load nvcuda", "cannot load libcuda", "no nvenc capable devices"),
                      "amf": ("amfrt64", "dll", "failed to open", "no device"),
                      "qsv": ("mfx session", "no device", "unsupported")}[kind]
            if any(a in low for a in absent):
                HW_ERRORS[kind] = "no " + {"nvenc": "NVIDIA", "amf": "AMD", "qsv": "Intel"}[kind] + " GPU/driver found"
            else:
                HW_ERRORS[kind] = _nvenc_reason(msg) if kind == "nvenc" else _hw_reason(msg, codec)
    if kind == "nvenc":
        NVENC_ERROR = HW_ERRORS[kind]
    return False


def nvenc_works() -> bool:
    return hw_works("nvenc")


@lru_cache(maxsize=8)
def pick_encoder(setting: str = "auto") -> str:
    """'nvenc' / 'amf' / 'qsv' / 'x264'. A forced GPU encoder that doesn't work falls back to x264."""
    if setting in HW_ENCODERS:
        return setting if hw_works(setting) else "x264"
    if setting == "auto":
        for k in HW_ENCODERS:
            if hw_works(k):
                return k
    return "x264"


def encoder_summary(setting: str = "auto") -> str:
    k = pick_encoder(setting)
    if k != "x264":
        return f"{HW_ENCODERS[k][1]} (GPU, fast)"
    tried = [HW_ENCODERS[x][1] + ": " + HW_ERRORS[x] for x in HW_ENCODERS
             if x in HW_ERRORS and "has no" not in HW_ERRORS[x] and not HW_ERRORS[x].startswith("no ")
             and (setting == "auto" or setting == x)]
    return "CPU x264" + (f" (no GPU encoder usable - {' | '.join(tried)[:500]})" if tried else "")


def _hw_reason(err: str, codec: str) -> str:
    lines = [ln.strip() for ln in err.splitlines() if ln.strip() and not ln.startswith("ffmpeg failed")]
    best = next((ln for ln in lines if any(k in ln.lower() for k in (codec.split("_")[1], "driver", "device",
                                                                       "init", "load", "mfx", "vpl"))), None)
    return (best or (lines[0] if lines else "unknown error"))[:300]


def _nvenc_reason(err: str) -> str:
    """Pick the line that explains why NVENC failed (FFmpeg's last line is usually a generic summary)."""
    lines = [ln.strip() for ln in err.splitlines() if ln.strip() and not ln.startswith("ffmpeg failed")]
    keys = ("driver", "nvenc api", "required", "cannot load", "nvcuda", "nvencodeapi", "no capable devices",
            "openencodesession", "no nvenc capable", "unsupported device", "out of memory", "incompatible")
    best = next((ln for ln in lines if any(k in ln.lower() for k in keys)), None)
    best = best or next((ln for ln in lines if "nvenc" in ln.lower()), None) or (lines[0] if lines else "unknown error")
    low = best.lower()
    if "required" in low and ("api" in low or "driver" in low):
        best += "  -> update your NVIDIA driver (GeForce Experience / nvidia.com/drivers), then restart OnDaGo"
    elif "cannot load" in low or "nvcuda" in low or "nvencodeapi" in low:
        best += "  -> NVIDIA driver not found: install/update the NVIDIA driver"
    return best[:400]


def video_codec_args(setting: str = "auto", quality: str = "final") -> list[str]:
    """Encoder args. quality: 'final' or 'intermediate' (near-lossless)."""
    kind = pick_encoder(setting)
    tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    inter = quality == "intermediate"
    cap = [] if inter else ["-maxrate", "16M", "-bufsize", "32M"]
    if kind == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "15" if inter else "18",
                "-b:v", "0", *cap, "-spatial-aq", "1", "-profile:v", "high", "-pix_fmt", "yuv420p", *tags]
    if kind == "amf":
        q = (14, 15, 16) if inter else (18, 19, 21)
        return ["-c:v", "h264_amf", "-usage", "transcoding", "-quality", "quality", "-rc", "cqp",
                "-qp_i", str(q[0]), "-qp_p", str(q[1]), "-qp_b", str(q[2]), "-profile:v", "high",
                "-pix_fmt", "yuv420p", *tags]
    if kind == "qsv":
        return ["-c:v", "h264_qsv", "-preset", "slow", "-global_quality", "16" if inter else "20",
                "-profile:v", "high", "-pix_fmt", "nv12", *tags]
    crf = "13" if inter else "18"
    preset = "veryfast" if inter else "medium"
    # final: high quality, but capped so a 3-min 60 fps Reel stays a sensible size to upload
    extra = [] if inter else ["-profile:v", "high", "-aq-mode", "3", *cap]
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf, *extra, "-pix_fmt", "yuv420p", *tags]


@lru_cache(maxsize=1)
def has_filter(name: str) -> bool:
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True,
                         creationflags=CREATE_NO_WINDOW if IS_WIN else 0)
    return f" {name} " in out.stdout


def hdr_to_sdr(info: dict) -> str:
    """iPhone/Android HDR (HLG / PQ) -> normal SDR colours, otherwise HDR clips look washed out and grey."""
    if info.get("transfer") not in ("arib-std-b67", "smpte2084"):
        return ""
    if has_filter("zscale") and has_filter("tonemap"):
        return ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")
    return "colorspace=all=bt709:trc=bt709:format=yuv420p,"


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
