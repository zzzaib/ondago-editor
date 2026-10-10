"""Sound design for the edit: whooshes on B-roll cuts, pops on text, a riser into the hook.

All sounds are synthesised here (no downloads, no licensing questions) and mixed into ONE
stereo track per output, which FFmpeg then blends under the voice."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000
_rng = np.random.default_rng(7)


def _env(n: int, attack: float, release: float) -> np.ndarray:
    a = max(1, int(attack * n))
    r = max(1, int(release * n))
    env = np.ones(n, np.float32)
    env[:a] = np.linspace(0, 1, a) ** 2
    env[-r:] = np.linspace(1, 0, r) ** 2
    return env


def _onepole_sweep(x: np.ndarray, f0: float, f1: float) -> np.ndarray:
    """Low-pass filter whose cutoff glides from f0 to f1 (gives the 'air moving' feel)."""
    n = len(x)
    fc = np.geomspace(f0, f1, n)
    a = np.exp(-2 * np.pi * fc / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(n):  # short sounds only, the python loop is fine
        acc = (1 - a[i]) * x[i] + a[i] * acc
        y[i] = acc
    return y


def whoosh(length: float = 0.55) -> np.ndarray:
    n = int(length * SR)
    noise = _rng.standard_normal(n).astype(np.float32)
    up = _onepole_sweep(noise, 300, 6000)
    y = up * _env(n, 0.55, 0.45)
    pan = np.linspace(-0.6, 0.6, n)
    st = np.stack([y * (1 - pan) / 2 * 2, y * (1 + pan) / 2 * 2], axis=1)
    return st / (np.abs(st).max() + 1e-9)


def pop(length: float = 0.13) -> np.ndarray:
    n = int(length * SR)
    t = np.arange(n) / SR
    f = np.geomspace(900, 260, n)
    ph = 2 * np.pi * np.cumsum(f) / SR
    y = np.sin(ph) * np.exp(-t * 38)
    y[: int(0.002 * SR)] += _rng.standard_normal(int(0.002 * SR)) * 0.3  # tiny click transient
    y = y.astype(np.float32)
    return np.stack([y, y], axis=1) / (np.abs(y).max() + 1e-9)


def riser(length: float = 0.9) -> np.ndarray:
    n = int(length * SR)
    f = np.geomspace(180, 1400, n)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.35
    noise = _onepole_sweep(_rng.standard_normal(n).astype(np.float32), 400, 9000) * 0.8
    y = (tone + noise) * (np.linspace(0, 1, n) ** 2) * _env(n, 0.02, 0.06)
    y = y.astype(np.float32)
    return np.stack([y, y], axis=1) / (np.abs(y).max() + 1e-9)


def scribble(length: float = 0.42) -> np.ndarray:
    """Marker-on-paper scratch: band-limited noise with a fast, irregular stroke rhythm."""
    n = int(length * SR)
    t = np.arange(n) / SR
    noise = _rng.standard_normal(n).astype(np.float32)
    hp = noise - _onepole_sweep(noise, 1800, 1800)          # remove the low rumble
    band = _onepole_sweep(hp, 7000, 5000)
    rate = 14 + 6 * np.sin(2 * np.pi * 1.3 * t)
    strokes = 0.35 + 0.65 * np.clip(np.sin(2 * np.pi * np.cumsum(rate) / SR), 0, 1) ** 0.7
    y = band * strokes * _env(n, 0.08, 0.3)
    y = y.astype(np.float32)
    return np.stack([y * 0.9, y], axis=1) / (np.abs(y).max() + 1e-9)


def ding(length: float = 0.9) -> np.ndarray:
    """Bright 'idea' bell."""
    n = int(length * SR)
    t = np.arange(n) / SR
    y = np.zeros(n, np.float32)
    for f, a, d in ((1568, 1.0, 5.0), (2349, 0.55, 6.5), (3136, 0.35, 8.0), (4699, 0.18, 11.0)):
        y += (a * np.sin(2 * np.pi * f * t) * np.exp(-t * d)).astype(np.float32)
    y[: int(0.003 * SR)] *= np.linspace(0, 1, int(0.003 * SR))
    return np.stack([y, y], axis=1) / (np.abs(y).max() + 1e-9)


def sparkle(length: float = 0.7) -> np.ndarray:
    """Quick rising chime glissando (stars, hearts, crowns)."""
    n = int(length * SR)
    y = np.zeros(n, np.float32)
    notes = [2093, 2637, 3136, 3951, 4186]
    for i, f in enumerate(notes):
        st = int(i * 0.055 * SR)
        m = n - st
        tt = np.arange(m) / SR
        y[st:] += (0.6 * np.sin(2 * np.pi * f * tt) * np.exp(-tt * 9)).astype(np.float32)
    pan = np.linspace(-0.4, 0.4, n)
    st = np.stack([y * (1 - pan), y * (1 + pan)], axis=1)
    return st / (np.abs(st).max() + 1e-9)


_CACHE: dict[str, np.ndarray] = {}


def sound(name: str) -> np.ndarray:
    if name not in _CACHE:
        _CACHE[name] = {"whoosh": whoosh, "pop": pop, "riser": riser, "scribble": scribble, "ding": ding,
                        "sparkle": sparkle}.get(name, pop)()
    return _CACHE[name]


def build_track(events: list[tuple[float, str, float]], duration: float, out: Path) -> Path | None:
    """events: (time_s, sound_name, gain_db). Sound is placed so its peak lands on time_s."""
    if not events or duration <= 0:
        return None
    n = int(duration * SR) + SR
    mix = np.zeros((n, 2), np.float32)
    peak_offset = {"whoosh": 0.30, "pop": 0.0, "riser": 0.85, "scribble": 0.0, "ding": 0.0, "sparkle": 0.0}
    for t, name, gain_db in events:
        s = sound(name) * (10 ** ((gain_db - 6.0) / 20))  # -6 dB headroom
        start = int(max(0.0, t - peak_offset.get(name, 0.0)) * SR)
        end = min(n, start + len(s))
        if end > start:
            mix[start:end] += s[: end - start]
    mix = np.clip(mix[: int(duration * SR)], -1, 1)
    pcm = (mix * 32767).astype("<i2")
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out
