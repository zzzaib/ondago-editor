"""Cut detection: silences, fillers (recognised + unrecognised), retakes."""
from __future__ import annotations

import difflib
import re

_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", _PUNCT.sub(" ", text.lower())).strip()


def flatten_words(transcript: dict) -> list[dict]:
    out = []
    for seg in transcript["segments"]:
        for w in seg["words"]:
            out.append({**w, "seg": seg["id"]})
    return out


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def detect_pause_and_filler_cuts(transcript: dict, vad: list[tuple[float, float]], duration: float, cfg: dict) -> list[dict]:
    fillers = {norm(f) for f in cfg.get("filler_words", [])}
    remove_fillers = cfg.get("remove_fillers", True)
    remove_silences = cfg.get("remove_silences", True)
    detect_unrec = cfg.get("detect_unrecognized_fillers", True) and remove_fillers
    max_pause = float(cfg.get("max_pause", 0.45))
    keep_pause = float(cfg.get("keep_pause", 0.18))
    half = keep_pause / 2

    words = flatten_words(transcript)
    is_filler = [remove_fillers and norm(w["w"]) in fillers for w in words]
    content = [w for w, f in zip(words, is_filler) if not f]
    filler_words = [w for w, f in zip(words, is_filler) if f]

    cuts: list[dict] = []

    def add(s, e, kind, text=""):
        if e - s >= 0.06:
            cuts.append({"s": round(s, 3), "e": round(e, 3), "kind": kind, "text": text, "enabled": True})

    if not content:
        return cuts

    bounds = [(-1.0, 0.0)] + [(w["s"], w["e"]) for w in content] + [(duration, duration + 1.0)]
    for i in range(len(bounds) - 1):
        a = bounds[i][1]          # end of previous content word
        b = bounds[i + 1][0]      # start of next content word
        if b - a <= 0.05:
            continue
        head, tail = i == 0, i == len(bounds) - 2
        inner_fill = [f for f in filler_words if f["s"] >= a - 0.05 and f["e"] <= b + 0.05]
        # Speech Whisper didn't write down. Only treat it as an "um" when it is short AND clearly
        # separated from the words around it - otherwise it is probably a real (e.g. Hindi) word.
        unrec = []
        if detect_unrec and not head and not tail:
            for vs, ve in vad:
                ov0, ov1 = max(vs, a), min(ve, b)
                if ov1 - ov0 < 0.12:
                    continue
                isolated = ov0 - a >= 0.12 and b - ov1 >= 0.12
                if isolated and ov1 - ov0 <= 0.7:
                    unrec.append((ov0, ov1))
        if inner_fill or unrec:
            label = " ".join(f["w"] for f in inner_fill) or "(um/uh)"
            add(a + 0.05, b - 0.05, "filler", label)
        elif remove_silences and (b - a > max_pause or head or tail):
            add(0.0 if head else a + half, duration if tail else b - half, "silence")
    return cuts


def protect_words(cuts: list[dict], transcript: dict, vad: list[tuple[float, float]], cfg: dict,
                  audio=None, sr: int = 16000) -> list[dict]:
    """Never cut into a spoken word.

    Whisper's word timestamps are often a little late on the start and early on the end, which is
    what clips the first/last sound of a word. Each kept word gets a safety margin, is stretched to
    the voice-activity region around it, and every cut is trimmed so it can't touch those regions.
    Cut edges are then nudged to the quietest nearby point so joins are clean.
    """
    pre = float(cfg.get("word_pad_before", 0.10))
    post = float(cfg.get("word_pad_after", 0.16))
    fillers = {norm(f) for f in cfg.get("filler_words", [])} if cfg.get("remove_fillers", True) else set()
    prot: list[tuple[float, float, int]] = []
    for w in flatten_words(transcript):
        if norm(w["w"]) in fillers:
            continue
        s, e = w["s"] - pre, w["e"] + post
        for vs, ve in vad:  # extend to the voiced region the word sits in (bounded)
            if vs <= w["e"] <= ve:
                e = max(e, min(ve + 0.04, w["e"] + 0.45))
            if vs <= w["s"] <= ve:
                s = min(s, max(vs - 0.04, w["s"] - 0.30))
        prot.append((max(0.0, s), e, w["seg"]))
    prot.sort()

    voiced = [(max(0.0, vs - 0.05), ve + 0.08, -1) for vs, ve in vad]
    out = []
    for c in cuts:
        seg_ids = set(c.get("seg_ids", []))
        pieces = [(c["s"], c["e"])]
        # a "silence" must really be silent: also keep any voice Whisper didn't transcribe
        guard = prot + voiced if c["kind"] == "silence" else prot
        for ps, pe, seg in guard:
            if seg in seg_ids or pe <= c["s"] or ps >= c["e"]:
                continue
            nxt = []
            for a, b in pieces:
                if pe <= a or ps >= b:
                    nxt.append((a, b))
                    continue
                if ps > a:
                    nxt.append((a, ps))
                if pe < b:
                    nxt.append((pe, b))
            pieces = nxt
        for a, b in pieces:
            if audio is not None:
                a, b = _quiet_edges(audio, sr, a, b)
            if b - a >= (0.12 if c["kind"] == "silence" else 0.08):
                out.append({**c, "s": round(a, 3), "e": round(b, 3)})
    return out


def _quiet_edges(audio, sr: int, a: float, b: float, search: float = 0.06, win: float = 0.01):
    """Move each edge INWARD (never outward) to the lowest-energy 10 ms window within `search` s."""
    import numpy as np

    def quietest(t0: float, t1: float) -> float:
        i0, i1 = int(max(0, t0) * sr), int(min(len(audio) / sr, t1) * sr)
        n = int(win * sr)
        if i1 - i0 <= n * 2:
            return t0
        seg = audio[i0:i1].astype(np.float32) ** 2
        e = np.convolve(seg, np.ones(n, np.float32), mode="valid")
        return (i0 + int(np.argmin(e)) + n / 2) / sr

    if b - a < 2 * search + 0.05:
        return a, b
    return quietest(a, a + search), quietest(b - search, b)


def detect_retakes_heuristic(transcript: dict, threshold: float = 0.72) -> list[int]:
    """Segment ids that look like an abandoned / repeated take of the following segment."""
    segs = transcript["segments"]
    bad: list[int] = []
    for i in range(len(segs) - 1):
        a = norm(segs[i]["text"])
        if len(a.split()) < 3:
            continue
        for j in (i + 1, i + 2):
            if j >= len(segs):
                break
            b = norm(segs[j]["text"])
            if not b:
                continue
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            a_words = a.split()
            prefix = len(a_words) >= 3 and b.startswith(" ".join(a_words[: max(3, int(len(a_words) * 0.6))]))
            if ratio >= threshold or prefix:
                bad.append(segs[i]["id"])
                break
    return bad


def _sim(a: str, b: str) -> float:
    """How much of a is said again in b (word overlap + sequence similarity)."""
    wa, wb = a.split(), b.split()
    if not wa or not wb:
        return 0.0
    overlap = len(set(wa) & set(wb)) / max(1, len(set(wa)))
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    return max(seq, overlap * 0.9)


def validate_retakes(transcript: dict, ids: list[int], heuristic: list[int], log=None) -> list[int]:
    """The editor model sometimes flags real content as a 'retake'. Only cut a segment when the same thing is
    said again shortly after (keep the last take), or when it's a short abandoned fragment. The opening hook and
    the closing lines are never cut unless they're clearly repeated."""
    segs = transcript["segments"]
    idx = {s["id"]: i for i, s in enumerate(segs)}
    heur = set(heuristic)
    keep: list[int] = []
    n = len(segs)
    for sid in sorted(set(ids)):
        i = idx.get(sid)
        if i is None:
            continue
        s = segs[i]
        a = norm(s["text"])
        dur = s["e"] - s["s"]
        best = 0.0
        for j in range(i + 1, min(n, i + 4)):
            if segs[j]["s"] - s["e"] > 30:
                break
            best = max(best, _sim(a, norm(segs[j]["text"])))
        short_fragment = dur <= 2.5 and len(a.split()) <= 6
        edge = i == 0 or i >= n - 2 or s["s"] < 8.0
        need = 0.6 if (edge or dur > 10) else 0.4
        ok = (sid in heur and best >= 0.35) or best >= need or (short_fragment and not edge and best >= 0.2) or (
            short_fragment and not edge and len(a.split()) <= 2)
        if ok:
            keep.append(sid)
        elif log:
            log(f"Kept segment {sid} ({dur:.1f}s) that was flagged as a retake: it isn't repeated later")
    return keep


def retake_cuts(transcript: dict, seg_ids: list[int], kind: str = "retake") -> list[dict]:
    by_id = {s["id"]: s for s in transcript["segments"]}
    cuts = []
    for sid in sorted(set(seg_ids)):
        s = by_id.get(sid)
        if not s:
            continue
        cuts.append({"s": round(max(0.0, s["s"] - 0.03), 3), "e": round(s["e"] + 0.12, 3), "kind": kind,
                     "text": s["text"], "enabled": True, "seg_ids": [sid]})
    return cuts


def finalize(cuts: list[dict]) -> list[dict]:
    cuts = sorted(cuts, key=lambda c: (c["s"], c["e"]))
    for i, c in enumerate(cuts):
        c["id"] = i
    return cuts
