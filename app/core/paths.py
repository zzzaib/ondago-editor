"""Central paths + environment bootstrap.

Everything lives inside the install folder so the app is fully portable:
  tools/   -> ffmpeg, ollama, uv, python
  models/  -> whisper, hf cache, rembg, ollama models, fonts, misc
  data/    -> settings, tokens, b-roll cache, jobs
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
MODELS = ROOT / "models"
DATA = ROOT / "data"
JOBS = DATA / "jobs"
CACHE = DATA / "cache"
FONTS = MODELS / "fonts"
CONFIG = ROOT / "config"

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

for _d in (DATA, JOBS, CACHE):
    _d.mkdir(parents=True, exist_ok=True)


def _prepend_path(p: Path) -> None:
    if p.exists():
        os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")
        if IS_WIN and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(p))
            except OSError:
                pass


def setup_environment() -> None:
    """Must be called before importing torch / faster_whisper / rembg."""
    os.environ.setdefault("HF_HOME", str(MODELS / "hf"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("U2NET_HOME", str(MODELS / "rembg"))
    os.environ.setdefault("OLLAMA_MODELS", str(MODELS / "ollama"))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # Apple Silicon: run missing Metal ops on the CPU

    _prepend_path(TOOLS / "ffmpeg" / "bin")
    _prepend_path(TOOLS / "ollama")

    # CTranslate2 (faster-whisper) needs cuBLAS 12 + cuDNN 9. PyTorch CUDA wheels
    # ship exactly those DLLs, so expose them to the loader.
    for sp in [p for p in sys.path if p.endswith("site-packages")]:
        _prepend_path(Path(sp) / "torch" / "lib")
        for b in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            _prepend_path(Path(b))


def ffmpeg_bin() -> str:
    local = TOOLS / "ffmpeg" / "bin" / ("ffmpeg.exe" if IS_WIN else "ffmpeg")
    return str(local) if local.exists() else (shutil.which("ffmpeg") or "ffmpeg")


def ffprobe_bin() -> str:
    local = TOOLS / "ffmpeg" / "bin" / ("ffprobe.exe" if IS_WIN else "ffprobe")
    return str(local) if local.exists() else (shutil.which("ffprobe") or "ffprobe")


def ollama_bin() -> str | None:
    local = TOOLS / "ollama" / ("ollama.exe" if IS_WIN else "ollama")
    if local.exists():
        return str(local)
    if IS_MAC:
        app = Path("/Applications/Ollama.app/Contents/Resources/ollama")
        if app.exists():
            return str(app)
    return shutil.which("ollama")


def font_path(name: str) -> Path:
    p = FONTS / name
    if p.exists():
        return p
    # fallbacks for dev machines
    for cand in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/impact.ttf",
    ]:
        if Path(cand).exists():
            return Path(cand)
    return p


setup_environment()
