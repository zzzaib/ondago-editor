"""Audio post: denoise -> gentle compression -> optional music bed with ducking -> loudness normalisation."""
from __future__ import annotations

import random
import shutil
from pathlib import Path

from ..core.paths import MODELS

RNNOISE = MODELS / "misc" / "rnnoise_std.rnnn"
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def prepare_job_assets(job_dir: Path) -> bool:
    """Copy the RNNoise model next to the job so filtergraphs can use a relative path (Windows-safe)."""
    if RNNOISE.exists():
        dst = job_dir / "rn.rnnn"
        if not dst.exists():
            shutil.copy(RNNOISE, dst)
        return True
    return False


def pick_music(folder: str) -> Path | None:
    if not folder or not Path(folder).exists():
        return None
    tracks = [p for p in Path(folder).rglob("*") if p.suffix.lower() in MUSIC_EXT]
    return random.choice(tracks) if tracks else None


def voice_chain(cfg: dict, have_rnnoise: bool) -> str:
    parts = ["highpass=f=70", "lowpass=f=16000"]
    if cfg.get("denoise", True):
        parts.append("arnndn=m=rn.rnnn:mix=0.85" if have_rnnoise else "afftdn=nf=-25")
    parts.append("acompressor=threshold=-21dB:ratio=3:attack=8:release=150:makeup=2")
    return ",".join(parts)


def build_filter(cfg: dict, voice_in: str, music_in: str | None, duration: float, have_rnnoise: bool,
                 sfx_in: str | None = None) -> str:
    """Returns filtergraph text ending in [aout]."""
    lufs = float(cfg.get("loudness_lufs", -14))
    g = [f"{voice_in}aresample=48000,{voice_chain(cfg, have_rnnoise)},aformat=channel_layouts=stereo[voice]"]
    if music_in:
        vol = float(cfg.get("music_volume_db", -20))
        fade_st = max(0.0, duration - 2.5)
        g.append("[voice]asplit=2[vmain][vsc]")
        g.append(f"{music_in}aresample=48000,aformat=channel_layouts=stereo,volume={vol}dB,"
                 f"afade=t=in:d=1.5,atrim=0:{duration:.3f},afade=t=out:st={fade_st:.3f}:d=2.5[mus]")
        g.append("[mus][vsc]sidechaincompress=threshold=0.02:ratio=10:attack=30:release=500[duck]")
        g.append("[vmain][duck]amix=inputs=2:duration=first:normalize=0[mix]")
        src = "[mix]"
    else:
        src = "[voice]"
    if sfx_in:
        vol = float(cfg.get("sfx_volume_db", -6))
        g.append(f"{sfx_in}aresample=48000,aformat=channel_layouts=stereo,volume={vol}dB[sfx]")
        g.append(f"{src}[sfx]amix=inputs=2:duration=first:normalize=0[withsfx]")
        src = "[withsfx]"
    g.append(f"{src}loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo[aout]")
    return ";".join(g)
