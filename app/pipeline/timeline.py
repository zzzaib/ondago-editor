"""Source-time <-> edited-time mapping.

Everything in plan.json is stored in SOURCE time. At render time we compute the
list of kept ranges (source minus enabled cuts, snapped to the frame grid) and map
words / b-roll / callouts into EDIT time.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass


@dataclass
class Timeline:
    keeps: list[tuple[float, float]]  # source ranges, sorted, non-overlapping

    def __post_init__(self):
        self.starts = [s for s, _ in self.keeps]
        self.offsets = []
        acc = 0.0
        for s, e in self.keeps:
            self.offsets.append(acc)
            acc += e - s
        self.duration = acc

    def to_edit(self, t: float, snap: str = "next") -> float | None:
        """Map a source time to edit time. If t is inside a cut: snap to next/prev keep or None."""
        i = bisect_right(self.starts, t) - 1
        if i >= 0 and self.keeps[i][0] <= t <= self.keeps[i][1]:
            return self.offsets[i] + (t - self.keeps[i][0])
        if snap == "none":
            return None
        if snap == "next":
            j = i + 1
            return self.offsets[j] if j < len(self.keeps) else self.duration
        # prev
        return self.offsets[i] + (self.keeps[i][1] - self.keeps[i][0]) if i >= 0 else 0.0

    def range_to_edit(self, s: float, e: float) -> tuple[float, float] | None:
        a = self.to_edit(s, "next")
        b = self.to_edit(e, "prev")
        if a is None or b is None or b - a < 0.05:
            return None
        return a, b

    def to_source(self, t: float) -> float:
        i = bisect_right(self.offsets, t) - 1
        i = max(0, min(i, len(self.keeps) - 1))
        return self.keeps[i][0] + (t - self.offsets[i])


def compute_keeps(duration: float, cuts: list[dict], fps: float, min_keep: float = 0.12) -> list[tuple[float, float]]:
    """Subtract enabled cuts from [0, duration] and snap to the frame grid."""
    spans = sorted((max(0.0, c["s"]), min(duration, c["e"])) for c in cuts if c.get("enabled", True) and c["e"] > c["s"])
    merged: list[list[float]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + 1e-3:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    keeps: list[tuple[float, float]] = []
    cur = 0.0
    for s, e in merged:
        if s > cur:
            keeps.append((cur, s))
        cur = max(cur, e)
    if cur < duration:
        keeps.append((cur, duration))

    def snap(x: float) -> float:
        return round(round(x * fps) / fps, 6)

    out = []
    for s, e in keeps:
        s2, e2 = snap(s), snap(e)
        if e2 - s2 >= max(min_keep, 1.0 / fps):
            out.append((s2, e2))
    return out or [(0.0, snap(duration))]
