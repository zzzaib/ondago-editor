"""Downloads every model/asset the app needs. Safe to re-run (skips what is already there).

usage: python installer/setup_models.py [--lite] [--llm qwen3.5:9b]
  --lite : skip the FLUX.2 klein image model (~16 GB). AI B-roll stills / AI thumbnail backgrounds
           then fall back to blurred / gradient backgrounds.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.paths import FONTS, MODELS, setup_environment  # noqa: E402

setup_environment()

FONT_URLS = {
    "Anton-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
    "Poppins-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Bold.ttf",
    "Poppins-SemiBold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-SemiBold.ttf",
    "Poppins-ExtraBold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-ExtraBold.ttf",
    # glyph fallback for Hindi (Devanagari) captions
    "NotoSansDevanagari-Bold.ttf": "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts/"
                                   "NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Bold.ttf",
}
YUNET_URLS = [
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
]
RNNOISE_URL = "https://raw.githubusercontent.com/richardpl/arnndn-models/master/std.rnnn"

failures: list[str] = []


def step(name: str):
    print(f"\n=== {name} ===", flush=True)


def ok(msg: str):
    print(f"  [ok] {msg}", flush=True)


def fetch(urls: list[str] | str, dest: Path, min_size: int = 1000) -> bool:
    if dest.exists() and dest.stat().st_size >= min_size:
        ok(f"{dest.name} already present")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    for url in [urls] if isinstance(urls, str) else urls:
        for attempt in range(3):
            try:
                tmp = dest.with_suffix(dest.suffix + ".part")
                req = urllib.request.Request(url, headers={"User-Agent": "OnDaGoEditor-installer"})
                with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
                    shutil.copyfileobj(r, fh)
                if tmp.stat().st_size < min_size:  # e.g. a git-lfs pointer file
                    tmp.unlink()
                    raise RuntimeError("file too small")
                tmp.replace(dest)
                ok(f"{dest.name}")
                return True
            except Exception as e:  # noqa: BLE001
                print(f"  retry {attempt + 1}/3 {url}: {e}", flush=True)
                time.sleep(2)
    return False


def hf(repo: str, patterns: list[str] | None = None):
    from huggingface_hub import snapshot_download
    path = snapshot_download(repo, allow_patterns=patterns, max_workers=4)
    ok(f"{repo} -> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lite", action="store_true")
    ap.add_argument("--llm", default="qwen3.5:9b")
    a = ap.parse_args()

    step("Fonts")
    for name, url in FONT_URLS.items():
        if not fetch(url, FONTS / name, 10_000):
            failures.append(f"font {name}")

    step("Face detector (YuNet) + RNNoise voice denoiser")
    if not fetch(YUNET_URLS, MODELS / "misc" / "face_detection_yunet_2023mar.onnx", 100_000):
        failures.append("YuNet (falls back to OpenCV Haar)")
    if not fetch(RNNOISE_URL, MODELS / "misc" / "rnnoise_std.rnnn", 10_000):
        failures.append("RNNoise (falls back to FFT denoise)")

    step("Speech recognition: Whisper large-v3-turbo")
    try:
        from faster_whisper import download_model
        out = MODELS / "whisper" / "large-v3-turbo"
        if (out / "model.bin").exists():
            ok("already present")
        else:
            download_model("large-v3-turbo", output_dir=str(out))
            ok(str(out))
    except Exception as e:  # noqa: BLE001
        failures.append(f"whisper: {e}")
        print(f"  [FAIL] {e}")

    step("Hinglish speech model (Oriserve Whisper-Hindi2Hinglish-Apex, Apache-2.0)")
    try:
        from app.pipeline.transcribe import ensure_hinglish_model
        if ensure_hinglish_model(print):
            ok("hinglish-apex")
        else:
            failures.append("hinglish model (standard Whisper + transliteration will be used)")
    except Exception as e:  # noqa: BLE001
        failures.append(f"hinglish model: {e}")
        print(f"  [FAIL] {e}")

    step("B-roll search: SigLIP")
    try:
        hf("google/siglip-base-patch16-224", ["*.json", "*.safetensors", "*.model", "*.txt"])
    except Exception as e:  # noqa: BLE001
        failures.append(f"siglip: {e}")
        print(f"  [FAIL] {e}")

    step("Background removal: BiRefNet portrait")
    try:
        from rembg import new_session
        new_session("birefnet-portrait")
        ok("birefnet-portrait")
    except Exception as e:  # noqa: BLE001
        failures.append(f"rembg: {e}")
        print(f"  [FAIL] {e}")

    step("Doodles: person outline model (u2net human segmentation, ~170 MB)")
    try:
        from app.pipeline.doodles import _seg_session
        if _seg_session(lambda m: None):
            ok("u2net_human_seg")
        else:
            failures.append("outline model (the glowing outline effect will be skipped)")
    except Exception as e:  # noqa: BLE001
        failures.append(f"outline model: {e}")
        print(f"  [FAIL] {e}")

    if not a.lite:
        step("AI images: FLUX.2 [klein] 4B (~16 GB, Apache-2.0)")
        try:
            hf("black-forest-labs/FLUX.2-klein-4B", ["model_index.json", "*/*"])  # diffusers folders only
        except Exception as e:  # noqa: BLE001
            failures.append(f"flux: {e}")
            print(f"  [FAIL] {e}")
    else:
        step("AI images: skipped (--lite)")

    step(f"Editor brain: {a.llm} via Ollama")
    try:
        from app.core.ollama import Ollama
        ol = Ollama(log=print)
        last = [-1]

        def prog(p, status):
            pct = int(p * 100)
            if pct != last[0] and pct % 5 == 0:
                last[0] = pct
                print(f"  {status} {pct}%", flush=True)

        ol.ensure_model(a.llm, prog)
        ok(a.llm)
        from app.core import ollama as _o
        if _o._server_proc is not None:
            _o._server_proc.terminate()
    except Exception as e:  # noqa: BLE001
        failures.append(f"ollama {a.llm}: {e}")
        print(f"  [FAIL] {e}")

    step("Self check")
    try:
        import torch
        cuda = torch.cuda.is_available()
        ok(f"torch {torch.__version__}, CUDA: {cuda}" + (f" ({torch.cuda.get_device_name(0)})" if cuda else ""))
        if not cuda:
            print("  [WARN] GPU not available to PyTorch - everything will run on CPU (slow).")
    except Exception as e:  # noqa: BLE001
        failures.append(f"torch: {e}")
    try:
        from app.core.ffmpeg import encoder_summary
        ok(f"FFmpeg video encoder: {encoder_summary('auto')}")
    except Exception as e:  # noqa: BLE001
        failures.append(f"ffmpeg: {e}")

    print()
    if failures:
        print("Finished with problems:")
        for f in failures:
            print("  -", f)
        critical = [f for f in failures if f.startswith(("whisper", "torch", "ffmpeg"))]
        sys.exit(1 if critical else 0)
    print("All models ready.")


if __name__ == "__main__":
    main()
