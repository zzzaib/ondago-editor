"""What can this computer run? One row per feature: which engine it will use (GPU / Apple GPU / CPU), how fast that
is, and what to do about it. Shown on the 'This computer' page and in the phone app's settings.

Everything here is detection only - nothing is downloaded or changed. Heavy checks (PyTorch import, test encodes)
take a few seconds the first time, so callers should run report() off the GUI thread."""
from __future__ import annotations

import platform
import subprocess
import sys
import time

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
_FLAGS = 0x08000000 if IS_WIN else 0  # no console window flashing up on Windows

# status -> (label, short meaning)
LEVELS = {"fast": "Fast", "ok": "Works", "slow": "Slow", "off": "Not available"}

_cache: dict | None = None


def _run(cmd: list[str], timeout: float = 8) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_FLAGS)
        return out.stdout if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


# ================================================================== the machine
def ram_gb() -> float:
    try:
        if IS_MAC:
            return int(_run(["sysctl", "-n", "hw.memsize"]).strip() or 0) / 1024 ** 3
        if IS_WIN:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1024 ** 3
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemTotal:"):
                    return int(ln.split()[1]) / 1024 ** 2
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def nvidia_info() -> list[dict]:
    """[{name, vram_gb, driver}] straight from nvidia-smi (works even when PyTorch can't use the card)."""
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    gpus = []
    for ln in out.splitlines():
        p = [x.strip() for x in ln.split(",")]
        if len(p) >= 3 and p[0]:
            try:
                vram = float(p[1]) / 1024
            except ValueError:
                vram = 0.0
            gpus.append({"name": p[0], "vram_gb": vram, "driver": p[2]})
    return gpus


def cpu_name() -> str:
    if IS_MAC:
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or platform.processor()
    if IS_WIN:
        out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"])
        if out.strip():
            return out.strip().splitlines()[0].strip()
    try:
        with open("/proc/cpuinfo") as f:
            for ln in f:
                if ln.startswith("model name"):
                    return ln.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return platform.processor() or platform.machine()


def mac_rosetta() -> bool:
    """Intel Python running on an Apple-Silicon Mac (through Rosetta): everything would be much slower."""
    return IS_MAC and _run(["sysctl", "-n", "sysctl.proc_translated"]).strip() == "1"


def os_name() -> str:
    if IS_MAC:
        v = platform.mac_ver()[0]
        return f"macOS {v}" if v else "macOS"
    if IS_WIN:
        rel, ver = platform.release(), platform.version()
        try:
            if rel == "10" and int(ver.split(".")[-1]) >= 22000:
                rel = "11"
        except ValueError:
            pass
        return f"Windows {rel}"
    return f"{platform.system()} {platform.release()}"


def torch_info() -> dict:
    d = {"version": "", "cuda": False, "mps": False, "cap": None, "error": ""}
    try:
        import torch
        d["version"] = torch.__version__.split("+")[0]
        from . import gpu
        d["cuda"] = gpu.has_cuda()
        d["mps"] = gpu.has_mps()
        d["cap"] = gpu.capability()
        if torch.cuda.is_available() and not d["cuda"]:
            d["error"] = "PyTorch sees the NVIDIA card but can't run on it (card too old for this PyTorch build)"
    except Exception as e:  # noqa: BLE001
        d["error"] = f"PyTorch not loadable: {e}"[:200]
    return d


def machine() -> dict:
    from . import gpu
    names = list(gpu.gpu_names())
    nv = nvidia_info()
    arch = platform.machine().lower()
    apple_silicon = IS_MAC and (arch == "arm64" or mac_rosetta())
    ram = ram_gb()
    vram = max((g["vram_gb"] for g in nv), default=0.0)
    ven = gpu.vendor()
    return {
        "os": os_name(), "cpu": cpu_name(), "cores": __import__("os").cpu_count() or 0, "arch": arch,
        "ram_gb": round(ram, 1), "gpus": names, "vendor": ven, "nvidia": nv, "vram_gb": round(vram, 1),
        "apple_silicon": apple_silicon, "intel_mac": IS_MAC and not apple_silicon, "rosetta": mac_rosetta(),
        "unified_gb": round(ram, 1) if apple_silicon else 0.0,
    }


# ================================================================= the features
def _row(fid: str, title: str, what: str, status: str, engine: str, note: str = "", tip: str = "",
         action: dict | None = None) -> dict:
    return {"id": fid, "title": title, "what": what, "status": status, "level": LEVELS[status], "engine": engine,
            "note": note, "tip": tip, "action": action}


def _encode_row(cfg: dict, m: dict) -> dict:
    from . import ffmpeg
    try:
        enc = ffmpeg.pick_encoder(cfg.get("encoder", "auto"))
    except Exception as e:  # noqa: BLE001
        return _row("encode", "Video export", "Turning the edit into the final MP4 files", "off", "FFmpeg missing",
                    str(e)[:200], "Re-run the installer - it downloads FFmpeg.")
    tried = {k: v for k, v in ffmpeg.HW_ERRORS.items() if "has no" not in v and not v.startswith("no ")}
    if enc in ffmpeg.HW_ENCODERS:
        name = ffmpeg.HW_ENCODERS[enc][1]
        return _row("encode", "Video export", "Turning the edit into the final MP4 files", "fast",
                    f"{name} (graphics chip)", "Exports use the video engine in your graphics chip.")
    tip, note = "", "No graphics-chip encoder could be used, so the processor encodes (several times slower)."
    if "nvenc" in tried:
        note = "NVIDIA encoder failed: " + tried["nvenc"]
        tip = "Update your NVIDIA driver (nvidia.com/drivers or the NVIDIA app), then press 'Check again'."
    elif "amf" in tried:
        note = "AMD encoder failed: " + tried["amf"]
        tip = "Update your AMD Adrenalin driver (amd.com/support), then press 'Check again'."
    elif "qsv" in tried:
        note = "Intel Quick Sync failed: " + tried["qsv"]
        tip = "Update your Intel graphics driver, then press 'Check again'."
    elif "vt" in tried:
        note = "VideoToolbox failed: " + tried["vt"]
    elif cfg.get("encoder") == "x264":
        note, tip = "Settings force the CPU encoder.", "Set Settings -> Video encoder to Auto."
        return _row("encode", "Video export", "Turning the edit into the final MP4 files", "slow",
                    "CPU (x264)", note, tip, {"label": "Use Auto", "set": {"encoder": "auto"}})
    return _row("encode", "Video export", "Turning the edit into the final MP4 files", "slow", "CPU (x264)", note, tip)


def _whisper_row(m: dict) -> dict:
    title, what = "Speech to text", "Whisper large-v3-turbo: writes the transcript every cut and caption is built on"
    n = 0
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
    except Exception:  # noqa: BLE001
        pass
    if n > 0:
        return _row("whisper", title, what, "fast", "NVIDIA GPU (CUDA)")
    cores = m["cores"]
    if m["apple_silicon"]:
        return _row("whisper", title, what, "ok", f"CPU ({cores} cores)",
                    "Whisper's engine (CTranslate2) has no Apple-GPU mode, so it runs on the CPU. Apple chips are "
                    "quick at this, but an NVIDIA PC is faster.")
    why = ("Whisper's engine only uses NVIDIA GPUs." if m["vendor"] in ("amd", "intel") or IS_MAC else
           "No NVIDIA GPU usable by Whisper." if not m["nvidia"] else
           "The NVIDIA card is there but CUDA isn't working for Whisper - updating the driver usually fixes it.")
    status = "ok" if cores >= 8 else "slow"
    return _row("whisper", title, what, status, f"CPU ({cores} cores)", why + " Slower, but it works.",
                "" if status == "ok" else "Pick 'small (fast)' in Settings -> Whisper model for quicker transcripts.")


def recommend_llm(m: dict) -> tuple[str, str]:
    """(model tag, reason) that fits this machine's memory."""
    if m["nvidia"]:
        v = m["vram_gb"]
        if v >= 7.5:
            return "qwen3.5:9b", f"{v:.0f} GB of graphics memory fits the 9B model"
        return "qwen3.5:4b", f"{v:.0f} GB of graphics memory - the 4B model fits fully on the GPU"
    if m["apple_silicon"]:
        if m["ram_gb"] >= 15.5:
            return "qwen3.5:9b", f"{m['ram_gb']:.0f} GB unified memory fits the 9B model"
        return "qwen3.5:4b", f"{m['ram_gb']:.0f} GB unified memory - the 4B model leaves room for everything else"
    if m["vendor"] in ("amd", "intel") and not IS_MAC:
        return "", ""  # can't tell from here how much of the model Ollama puts on this GPU
    return "qwen3.5:4b", "runs on the CPU, where the smaller 4B model is much quicker"


def _llm_engine(m: dict) -> tuple[str, str, str]:
    """What Ollama will run on: (status, engine, note)."""
    if m["nvidia"]:
        return "fast", "NVIDIA GPU (CUDA)", ""
    if m["apple_silicon"]:
        return "fast", "Apple GPU (Metal)", "Uses the chip's unified memory."
    if m["vendor"] == "amd" and not IS_MAC:
        return "ok", "AMD GPU (ROCm / Vulkan) or CPU", (
            "Ollama uses ROCm on Radeon RX 7000-series cards and Vulkan on most other Radeons; if neither works "
            "it falls back to the CPU.")
    if m["vendor"] == "intel" and not IS_MAC:
        return "ok", "Intel GPU (Vulkan) or CPU", "Ollama uses Vulkan when the Intel driver supports it, else the CPU."
    if m["intel_mac"]:
        return "slow", "CPU", "Intel Macs can't use their graphics chip for the editor model."
    return "slow", "CPU", "No supported GPU for the editor model, so it runs on the processor."


def _llm_row(cfg: dict, m: dict) -> dict:
    status, engine, note = _llm_engine(m)
    rec, why = recommend_llm(m)
    cur = cfg.get("llm_model", "qwen3.5:9b")
    live = _ollama_live(cfg)
    if not rec and "CPU only" in live:
        rec, why = "qwen3.5:4b", "Ollama is running the model on the CPU, where the 4B model is much quicker"
    tip, action = "", None
    if rec and cur != rec and cur.startswith("qwen3.5:"):
        if rec == "qwen3.5:4b":
            tip = f"Recommended: {rec} ({why}). {cur} will work but slower."
        else:
            tip = f"You could use {rec} ({why}) for smarter edits."
        action = {"label": f"Use {rec}", "set": {"llm_model": rec}}
    if live:
        note = live
    note = (note + " " if note else "") + f"Using {cur}."
    return _row("llm", "Editor brain", "Qwen 3.5 via Ollama: finds retakes, plans B-roll, graphics, titles",
                status, engine, note.strip(), tip, action)


def _ollama_live(cfg: dict) -> str:
    """If the editor model is loaded right now, Ollama tells us how much of it sits on the GPU."""
    try:
        import requests
        r = requests.get(cfg.get("ollama_url", "http://127.0.0.1:11434") + "/api/ps", timeout=1.5)
        for x in r.json().get("models", []):
            size, vram = x.get("size") or 0, x.get("size_vram") or 0
            if size:
                pct = round(100 * vram / size)
                where = "fully on the GPU" if pct >= 99 else "on the CPU only" if pct == 0 else f"{pct}% on the GPU"
                return f"Right now {x.get('name', 'the model')} is loaded {where}."
    except Exception:  # noqa: BLE001
        pass
    return ""


def _vision_row(cfg: dict, m: dict) -> dict:
    status, engine, _ = _llm_engine(m)
    tip = ""
    if status == "slow" and cfg.get("broll_verify", True):
        tip = "To save time, turn off Settings -> B-roll -> 'Double-check matches with the vision model'."
    return _row("vision", "Vision checks", "The editor model looks at B-roll options and thumbnail frames",
                status, engine, "Same engine as the editor brain.", tip)


def _flux_row(m: dict) -> dict:
    title, what = "AI images", "FLUX.2 klein: AI B-roll stills, AI doodles and AI thumbnail backgrounds"
    from . import gpu
    ok, why = gpu.ai_images_ok()
    if not ok:
        if m["apple_silicon"] or IS_MAC:
            note = ("Not available on Mac: OnDaGo runs this model in 4-bit on NVIDIA cards (bitsandbytes), which "
                    "has no Mac version yet.")
        elif m["vendor"] in ("amd", "intel"):
            note = "Needs an NVIDIA RTX graphics card. AMD and Intel GPUs can't run this model here yet."
        else:
            note = why[:1].upper() + why[1:] + "."
        return _row("flux", title, what, "off", "-", note,
                    "Everything else still works: B-roll comes from your library, stock sites or animated cards, "
                    "thumbnails use a blurred-frame background.")
    try:
        from ..pipeline.broll import generate
        installed = generate.installed()
    except Exception:  # noqa: BLE001
        installed = False
    if not installed:
        return _row("flux", title, what, "off", "NVIDIA GPU (CUDA)", "Your GPU can do it, but the model isn't "
                    "downloaded (Lite install).", "Re-run install.bat without -Lite to add it (~16 GB).")
    v = m["vram_gb"]
    if v and v < 7.5:
        return _row("flux", title, what, "slow", "NVIDIA GPU (CUDA)",
                    f"{v:.0f} GB graphics memory: works with memory offloading, but each image takes a while.")
    return _row("flux", title, what, "fast", "NVIDIA GPU (CUDA)", "4-bit model with memory offloading.")


def _siglip_row(m: dict) -> dict:
    from . import gpu
    dev = gpu.device()
    title, what = "B-roll library search", "SigLIP: finds matching clips in your own footage folder by meaning"
    if dev == "cuda":
        return _row("siglip", title, what, "fast", "NVIDIA GPU (CUDA)")
    if dev == "mps":
        return _row("siglip", title, what, "fast", "Apple GPU (Metal)")
    return _row("siglip", title, what, "ok", "CPU",
                "The first scan of a big folder takes a while; after that only new files are scanned.")


def _rembg_row(m: dict) -> dict:
    title, what = "Cut-outs", "Background removal for thumbnails and the glowing outline doodle"
    try:
        import onnxruntime as ort
        prov = ort.get_available_providers()
    except Exception:  # noqa: BLE001
        prov = []
    if "CUDAExecutionProvider" in prov:
        return _row("rembg", title, what, "fast", "NVIDIA GPU (CUDA)")
    if "DmlExecutionProvider" in prov:
        return _row("rembg", title, what, "fast", "GPU (DirectML)")
    if "CoreMLExecutionProvider" in prov:
        return _row("rembg", title, what, "ok", "Apple Neural Engine / CPU")
    return _row("rembg", title, what, "ok", "CPU", "A couple of seconds per thumbnail - fine on any computer.")


def _always_row() -> dict:
    return _row("core", "Cutting, captions, doodles, Vox graphics",
                "Silence / filler cuts, face tracking, captions, doodles, split-screen and explainer graphics",
                "fast", "CPU", "Works the same on every Windows PC and Mac.")


def _verdict(m: dict, rows: list[dict]) -> tuple[str, str]:
    """(headline, mood) for the top of the page. mood: great / good / basic"""
    by = {r["id"]: r for r in rows}
    gpu = ", ".join(m["gpus"][:2]) or ("Apple chip" if IS_MAC else "no graphics card found")
    if m["rosetta"]:
        return ("OnDaGo is running through Rosetta (Intel mode) on an Apple-Silicon Mac - everything is much "
                "slower. Re-run install.command so it installs the Apple-Silicon version."), "basic"
    off = [r["title"] for r in rows if r["status"] == "off"]
    slow = [r["title"] for r in rows if r["status"] == "slow"]
    if not off and not slow:
        return f"Great: {gpu} runs every feature at full speed.", "great"
    if by["llm"]["status"] == "fast" and by["encode"]["status"] == "fast":
        miss = " and ".join(off) if off else ""
        extra = f" {', '.join(slow)} {'is' if len(slow) == 1 else 'are'} slower." if slow else ""
        return (f"Good: {gpu} handles everything{' except ' + miss if miss else ''}.{extra}"), "good"
    return (f"Basic: {gpu}. Everything still works, but the AI steps run on the processor, so an edit takes "
            f"longer." + (f" Not available: {', '.join(off)}." if off else "")), "basic"


def report(cfg: dict | None = None, refresh: bool = False) -> dict:
    """Full capability report (cached; refresh=True re-runs every check)."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    if cfg is None:
        from . import settings
        cfg = settings.load()
    if refresh:
        _clear()
    t = time.time()
    m = machine()
    m["torch"] = torch_info()
    rows = [_encode_row(cfg, m), _whisper_row(m), _llm_row(cfg, m), _vision_row(cfg, m), _flux_row(m),
            _siglip_row(m), _rembg_row(m), _always_row()]
    head, mood = _verdict(m, rows)
    _cache = {"machine": m, "features": rows, "verdict": head, "mood": mood, "checked": time.time(),
              "took": round(time.time() - t, 1), "limits": limits(m)}
    return _cache


def limits(m: dict) -> list[str]:
    """Plain-language list of what this kind of computer can't do (shown under the table)."""
    out = []
    if IS_MAC:
        out.append("AI images (FLUX) are not available on any Mac yet.")
        out.append("Speech to text runs on the CPU on Macs (no Apple-GPU version of Whisper's engine).")
        if m["intel_mac"]:
            out.append("Intel Macs use an older PyTorch (2.2) and run every AI step on the CPU.")
        elif m["ram_gb"] and m["ram_gb"] < 15.5:
            out.append("With 8 GB unified memory, use the qwen3.5:4b editor model and close other big apps "
                       "while editing.")
    elif m["vendor"] in ("amd", "intel") and not m["nvidia"]:
        out.append("AI images (FLUX) need an NVIDIA RTX card.")
        out.append("Speech to text and B-roll search run on the CPU (their engines only use NVIDIA GPUs on Windows).")
    elif m["nvidia"] and m["torch"].get("cap") and m["torch"]["cap"] < (7, 5):
        out.append("This NVIDIA card is older than RTX 20-series: AI images are off and fp16 tricks are disabled.")
    if m["nvidia"] and m["vram_gb"] and m["vram_gb"] < 7.5:
        out.append(f"{m['vram_gb']:.0f} GB graphics memory: use the qwen3.5:4b editor model.")
    return out


def _clear() -> None:
    from . import ffmpeg, gpu
    for f in (gpu.capability, gpu.has_cuda, gpu.has_mps, gpu.gpu_names, ffmpeg.hw_works, ffmpeg.pick_encoder,
              ffmpeg.available_encoders):
        try:
            f.cache_clear()
        except Exception:  # noqa: BLE001
            pass
    ffmpeg.HW_ERRORS.clear()
    try:
        ffmpeg.encoder_summary.cache_clear()
    except Exception:  # noqa: BLE001
        pass


def summary_text(r: dict) -> str:
    """Plain text version (installer self-check, logs, copy-to-clipboard)."""
    m = r["machine"]
    lines = [r["verdict"], "",
             f"{m['os']} - {m['cpu']} ({m['cores']} cores) - {m['ram_gb']:.0f} GB RAM",
             "Graphics: " + (", ".join(
                 f"{g['name']} ({g['vram_gb']:.0f} GB, driver {g['driver']})" for g in m["nvidia"])
                 or ", ".join(m["gpus"]) or "none found"), ""]
    for f in r["features"]:
        lines.append(f"[{f['level']:>13}] {f['title']}: {f['engine']}" + (f" - {f['note']}" if f["note"] else ""))
        if f["tip"]:
            lines.append(f"{'':16}tip: {f['tip']}")
    if r["limits"]:
        lines += ["", "Limits of this computer:"] + [f"  - {x}" for x in r["limits"]]
    return "\n".join(lines)
