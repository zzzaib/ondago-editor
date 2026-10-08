"""OFFLINE B-roll #1: your own footage folder, searched by meaning with SigLIP.

Videos are sampled every 2 s, images used directly. The index is cached and
updated incrementally (only new / modified files are re-embedded)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from ...core.ffmpeg import iter_frames, probe
from ...core.gpu import device, free_vram, half_ok
from ...core.paths import CACHE

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
EMBED_MODEL = "google/siglip-base-patch16-224"


class Embedder:
    def __init__(self, model_id: str = EMBED_MODEL):
        import torch
        from transformers import AutoModel, AutoProcessor

        self.torch = torch
        self.dev = device()
        self.dtype = torch.float16 if (self.dev == "cuda" and half_ok()) else torch.float32
        self.model = AutoModel.from_pretrained(model_id, dtype=self.dtype).to(self.dev).eval()
        self.proc = AutoProcessor.from_pretrained(model_id)
        self.scale = float(self.model.logit_scale.exp().item())
        self.bias = float(self.model.logit_bias.item()) if hasattr(self.model, "logit_bias") else 0.0

    @staticmethod
    def _as_tensor(out):
        if hasattr(out, "pooler_output") and out.pooler_output is not None:
            return out.pooler_output
        if hasattr(out, "image_embeds"):
            return out.image_embeds
        if hasattr(out, "text_embeds"):
            return out.text_embeds
        return out

    def images(self, imgs: list[Image.Image]) -> np.ndarray:
        with self.torch.no_grad():
            inp = self.proc(images=imgs, return_tensors="pt").to(self.dev)
            inp["pixel_values"] = inp["pixel_values"].to(self.dtype)
            f = self._as_tensor(self.model.get_image_features(**inp)).float()
            f = f / f.norm(dim=-1, keepdim=True)
        return f.cpu().numpy()

    def texts(self, texts: list[str]) -> np.ndarray:
        with self.torch.no_grad():
            inp = self.proc(text=[f"a photo of {t.lower()}." for t in texts], padding="max_length",
                            max_length=64, truncation=True, return_tensors="pt").to(self.dev)
            f = self._as_tensor(self.model.get_text_features(**inp)).float()
            f = f / f.norm(dim=-1, keepdim=True)
        return f.cpu().numpy()

    def prob(self, cos: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-(cos * self.scale + self.bias)))

    def close(self):
        del self.model
        free_vram()


class Library:
    def __init__(self, folder: str | Path, log: Callable[[str], None] = print):
        self.folder = Path(folder)
        self.log = log
        key = hashlib.md5(str(self.folder.resolve()).lower().encode()).hexdigest()[:12]
        self.index_npz = CACHE / f"library_{key}.npz"
        self.index_json = CACHE / f"library_{key}.json"
        self.entries: list[dict] = []
        self.emb: np.ndarray | None = None
        self.embedder: Embedder | None = None

    def files(self) -> list[Path]:
        if not self.folder.exists():
            return []
        return sorted(p for p in self.folder.rglob("*") if p.suffix.lower() in VIDEO_EXT | IMAGE_EXT)

    def build(self, on_progress: Callable[[float], None] | None = None) -> None:
        old_entries, old_emb = [], None
        if self.index_npz.exists() and self.index_json.exists():
            old_entries = json.loads(self.index_json.read_text(encoding="utf-8"))
            old_emb = np.load(self.index_npz)["emb"]
        old_by_file: dict[str, list[int]] = {}
        for i, e in enumerate(old_entries):
            old_by_file.setdefault(e["file"], []).append(i)

        files = self.files()
        entries, embs = [], []
        self.embedder = self.embedder or Embedder()
        for n, f in enumerate(files):
            mtime = f.stat().st_mtime
            idx = old_by_file.get(str(f), [])
            if idx and old_emb is not None and all(abs(old_entries[i]["mtime"] - mtime) < 1 for i in idx):
                entries += [old_entries[i] for i in idx]
                embs.append(old_emb[idx])
                continue
            try:
                frames, meta = [], []
                if f.suffix.lower() in IMAGE_EXT:
                    frames.append(Image.open(f).convert("RGB"))
                    meta.append({"file": str(f), "t": 0.0, "kind": "image", "mtime": mtime, "dur": 0.0})
                else:
                    dur = probe(f)["duration"]
                    step = 2.0 if dur < 600 else dur / 300
                    for t, fr in iter_frames(f, fps=1.0 / step, width=384):
                        frames.append(Image.fromarray(fr[:, :, ::-1]))
                        meta.append({"file": str(f), "t": round(t, 2), "kind": "video", "mtime": mtime, "dur": dur})
                for b in range(0, len(frames), 32):
                    embs.append(self.embedder.images(frames[b:b + 32]))
                entries += meta
                self.log(f"Indexed {f.name} ({len(meta)} frames)")
            except Exception as e:  # noqa: BLE001
                self.log(f"Skipping {f.name}: {e}")
            if on_progress:
                on_progress((n + 1) / max(1, len(files)))
        self.entries = entries
        self.emb = np.concatenate(embs) if embs else np.zeros((0, 768), np.float32)
        np.savez_compressed(self.index_npz, emb=self.emb)
        self.index_json.write_text(json.dumps(entries), encoding="utf-8")

    def search(self, queries: list[str], top_k: int = 3, used: set[tuple[str, float]] | None = None) -> list[dict]:
        if self.emb is None or not len(self.entries):
            return []
        self.embedder = self.embedder or Embedder()
        q = self.embedder.texts(queries)
        cos = (self.emb @ q.T).max(axis=1)
        prob = self.embedder.prob(cos)
        order = np.argsort(-prob)
        out, seen_files = [], set()
        used = used or set()
        for i in order:
            e = self.entries[int(i)]
            if (e["file"], e["t"]) in used or e["file"] in seen_files:
                continue
            seen_files.add(e["file"])
            out.append({"source": "library", "file": e["file"], "file_start": max(0.0, e["t"] - 0.5),
                        "kind": e["kind"], "score": float(prob[i]), "cos": float(cos[i])})
            if len(out) >= top_k:
                break
        return out

    def close(self):
        if self.embedder:
            self.embedder.close()
            self.embedder = None
