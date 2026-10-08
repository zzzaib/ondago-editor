"""OFFLINE B-roll #2 (and thumbnail backgrounds): FLUX.2 [klein] 4B (Apache-2.0).

On an 8 GB card the transformer + Qwen3 text encoder are loaded in 4-bit
(bitsandbytes) with model CPU offload. Falls back to sequential offload."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ...core.gpu import ai_images_ok, bf16_ok, free_vram, has_cuda
from ...core.paths import MODELS

REPO = "black-forest-labs/FLUX.2-klein-4B"


def installed() -> bool:
    hub = MODELS / "hf" / "hub" / ("models--" + REPO.replace("/", "--"))
    return hub.exists() and any(hub.rglob("*.safetensors"))


def available() -> tuple[bool, str]:
    """(usable, reason-if-not): model downloaded AND a GPU that can run it in reasonable time."""
    if not installed():
        return False, "AI image model not installed (re-run the installer without -Lite)"
    return ai_images_ok()


class ImageGen:
    def __init__(self, log: Callable[[str], None] = print):
        self.log = log
        self.pipe = None

    def load(self):
        if self.pipe is not None:
            return
        ok, why = available()
        if not ok:
            raise RuntimeError(why)
        import torch
        from diffusers import Flux2KleinPipeline

        dtype = torch.bfloat16 if bf16_ok() else torch.float16
        if has_cuda():
            try:
                from diffusers import PipelineQuantizationConfig
                q = PipelineQuantizationConfig(
                    quant_backend="bitsandbytes_4bit",
                    quant_kwargs={"load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
                                  "bnb_4bit_compute_dtype": dtype},
                    components_to_quantize=["transformer", "text_encoder"])
                self.pipe = Flux2KleinPipeline.from_pretrained(REPO, dtype=dtype, quantization_config=q)
                self.pipe.enable_model_cpu_offload()
                self.log("FLUX.2 klein loaded (4-bit, model offload)")
                return
            except Exception as e:  # noqa: BLE001
                self.log(f"4-bit load failed ({e}); using sequential offload (slower)")
                free_vram()
            self.pipe = Flux2KleinPipeline.from_pretrained(REPO, dtype=dtype)
            self.pipe.enable_sequential_cpu_offload()
        else:
            self.pipe = Flux2KleinPipeline.from_pretrained(REPO, dtype=torch.float32)
        try:
            self.pipe.vae.enable_tiling()
        except Exception:
            pass

    def generate(self, prompt: str, out: Path, width: int = 1344, height: int = 768, seed: int = 0) -> Path:
        import torch

        self.load()
        g = torch.Generator(device="cpu").manual_seed(seed)
        img = self.pipe(prompt=prompt, width=width, height=height, guidance_scale=1.0, num_inference_steps=4,
                        generator=g).images[0]
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, quality=95)
        return out

    def close(self):
        self.pipe = None
        free_vram()


def broll_prompt(description: str) -> str:
    return (f"{description}. Cinematic documentary b-roll photograph, natural light, shallow depth of field, "
            f"realistic, high detail, no text, no watermark")
