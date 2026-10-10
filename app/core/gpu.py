from __future__ import annotations

import gc
from functools import lru_cache


def free_vram() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:
        pass


@lru_cache(maxsize=1)
def capability() -> tuple[int, int] | None:
    """CUDA compute capability of GPU 0 as seen by PyTorch, or None."""
    try:
        import torch
        if torch.cuda.is_available():
            return tuple(torch.cuda.get_device_capability(0))
    except Exception:
        pass
    return None


@lru_cache(maxsize=1)
def has_cuda() -> bool:
    """True only if PyTorch can actually run kernels on this GPU (its build includes this architecture)."""
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        maj, mnr = torch.cuda.get_device_capability(0)
        archs = torch.cuda.get_arch_list()
        if archs and f"sm_{maj}{mnr}" not in archs and not any(
                a.startswith(f"sm_{maj}") and int(a[3:]) <= maj * 10 + mnr for a in archs):
            return False
        torch.zeros(1, device="cuda").add_(1)  # smoke test: raises 'no kernel image' if unsupported
        return True
    except Exception:
        return False


def device() -> str:
    return "cuda" if has_cuda() else "cpu"


def half_ok() -> bool:
    """fp16 is only fast on Volta (7.0) and newer. Pascal (GTX 10xx) runs fp16 at a tiny fraction of fp32."""
    cap = capability()
    return has_cuda() and cap is not None and cap >= (7, 0)


def bf16_ok() -> bool:
    cap = capability()
    return has_cuda() and cap is not None and cap >= (8, 0)


@lru_cache(maxsize=1)
def gpu_names() -> tuple[str, ...]:
    """Names of the graphics cards (NVIDIA via nvidia-smi, anything else - AMD / Intel - via Windows)."""
    import subprocess
    import sys
    flags = 0x08000000 if sys.platform == "win32" else 0
    names: list[str] = []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=5, creationflags=flags)
        if out.returncode == 0:
            names += [x.strip() for x in out.stdout.splitlines() if x.strip()]
    except Exception:  # noqa: BLE001
        pass
    if sys.platform == "win32":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_VideoController).Name"], capture_output=True, text=True,
                                 timeout=10, creationflags=flags)
            for x in out.stdout.splitlines():
                x = x.strip()
                if x and x not in names and "basic display" not in x.lower() and "virtual" not in x.lower():
                    names.append(x)
        except Exception:  # noqa: BLE001
            pass
    return tuple(names)


def vendor() -> str:
    """'nvidia' / 'amd' / 'intel' / '' - the best graphics card in this PC."""
    low = " | ".join(gpu_names()).lower()
    for v, keys in (("nvidia", ("nvidia", "geforce", "rtx", "quadro")), ("amd", ("amd", "radeon")),
                    ("intel", ("intel",))):
        if any(k in low for k in keys):
            return v
    return ""


def ai_images_ok() -> tuple[bool, str]:
    """FLUX.2 klein on this machine? Needs an RTX-class GPU (Turing 7.5+); Pascal would take many minutes per image."""
    cap = capability()
    if not has_cuda():
        if vendor() in ("amd", "intel"):
            return False, "AI images need an NVIDIA RTX GPU (AMD / Intel GPUs can't run this model yet)"
        return False, "no usable NVIDIA GPU for PyTorch (AI images need an RTX card)"
    if cap is not None and cap < (7, 5):
        return False, f"GPU compute capability {cap[0]}.{cap[1]} is too old for AI image generation (needs RTX 20xx or newer)"
    return True, ""
