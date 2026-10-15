"""Planning the Vox-style explainer graphics: which moments get a headline card, a highlighted quote, a counting
number, a map or a timeline. The editor LLM proposes them (planner.py); everything is checked against what was
actually said, and a keyword/number/place/year scan fills in when the LLM is unavailable or gives too few.

The result is a list of normal B-roll slots with kind 'vox' (so Review, the phone app and the renderer treat them
like any other cutaway)."""
from __future__ import annotations

import re
from pathlib import Path

from .cuts import norm
from .vox import ALIASES, DUR, KINDS, find_country, parse_items, parse_number, preview

EVERY = {"low": 30.0, "medium": 18.0, "high": 11.0}

STOP = set("""a an the and or but if then so to of in on at for with from by is are was were be been being it this that
these those i you he she we they me my your our their his her its as not no yes do does did have has had will would can
could should just like really very also about into over than too what which who whom when where why how all any some
there here up down out get got going gonna wanna kind sort thing things lot okay ok yeah um uh hmm right well now one two
because let let's i'm it's that's don't you're we're they're i've you've hai ki ka ke ko se aur toh bhi""".split())

# words that are also country names but usually mean something else in speech
AMBIGUOUS = {"us", "chad", "jordan", "turkey", "niger", "guinea", "georgia"}

NUM_RE = re.compile(
    r"(?<![\w.])((?:\$|₹|rs\.?\s?|€|£)?\d[\d,]*(?:\.\d+)?)\s?(%|percent|per cent|x\b|times|million|billion|"
    r"trillion|crore|lakh|lakhs|thousand|k\b|hours|minutes|seconds|years|days|people|users|gb|mb|fps)?", re.I)
YEAR_RE = re.compile(r"\b(1[6-9]\d\d|20\d\d)\b")


def _words(text: str) -> list[str]:
    return [w for w in norm(text).split() if w and w not in STOP and len(w) > 2]


def _anchor_time(seg: dict, anchor: str) -> float:
    a = norm(anchor).split()
    if a:
        for w in seg.get("words", []):
            if norm(w["w"]) == a[0]:
                return w["s"]
    return seg["s"]


# ------------------------------------------------------------- LLM items
def schema_items(kinds: list[str] | None = None) -> dict:
    return {"type": "array", "items": {"type": "object", "properties": {
        "segment_id": {"type": "integer"}, "kind": {"type": "string", "enum": kinds or list(KINDS[:5])},
        "anchor_words": {"type": "string"}, "text": {"type": "string"}, "detail": {"type": "string"},
        "place": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}},
        "required": ["segment_id", "kind", "anchor_words", "text", "detail", "place", "items"]}}


def prompt_text(n: int) -> str:
    return (f"6. explainer: about {n} Vox-style explainer graphics that make the video easier to understand. kind is "
            "one of: headline = a bold 2-6 word title card for a key point or a new section (text); quote = the "
            "speaker's key sentence shown as a highlighted text excerpt (detail = that sentence, copied from the "
            "segment; text = the 2-6 most important words of it, copied exactly); number = a striking figure the "
            "speaker says (text = the figure exactly, like '70%', '5x', '$2 billion', '1.5 crore', '10,000'; detail "
            "= 2-6 words saying what it measures); map = a country or big city the speaker talks about (place = "
            "its English name; text = a 1-3 word label or empty); timeline = when the speaker mentions 2-4 years "
            "(items = 'YEAR: 2-5 word event' strings in order; text = a 2-5 word title). Use number / map / "
            "timeline ONLY when that figure, place or those years are really in the transcript. anchor_words = "
            "the exact 1-3 consecutive words where the graphic should start. Leave unused fields empty. Spread "
            "them out and don't put them on B-roll moments.\n")


def validate(items: list[dict], segs_by_id: dict[int, dict]) -> list[dict]:
    """Keep only beats that match what was said; repair or downgrade the rest."""
    out = []
    for it in items or []:
        seg = segs_by_id.get(it.get("segment_id"))
        kind = str(it.get("kind", "")).lower()
        if not seg or kind not in KINDS:
            continue
        line = seg["text"]
        text = re.sub(r"\s+", " ", str(it.get("text", "")).strip())[:60]
        detail = re.sub(r"\s+", " ", str(it.get("detail", "")).strip())[:220]
        spec = {"kind": kind, "text": text, "detail": detail}
        if kind == "number":
            pn = parse_number(text)
            digits = re.sub(r"\D", "", text)
            if not pn or not digits or digits[:2] not in re.sub(r"\D", "", line):
                spec = {"kind": "headline", "text": detail or text, "detail": ""}
        elif kind == "map":
            place = str(it.get("place") or text).strip()
            ctry = find_country(place)
            said = norm(place) in norm(line) or (ctry and any(norm(n) in norm(line) for n in [ctry["n"]] + ctry["a"]))
            if not ctry or not said:
                continue
            spec.update(place=place, text=text if text and len(text) <= 24 else "")
        elif kind == "timeline":
            its = parse_items(it.get("items"))
            if len(its) < 2:
                continue
            spec["items"] = [f"{y}: {lab}" for y, lab in its]
        elif kind == "quote":
            if not detail or len(norm(detail).split()) < 5:
                detail = line
            spec.update(detail=detail, text=text or "")
        elif kind == "headline":
            if not text:
                continue
            spec["text"] = " ".join(text.split()[:7])
        out.append({"segment_id": seg["id"], "anchor_words": it.get("anchor_words", ""), "spec": spec})
    return out


# -------------------------------------------------------------- heuristics
def heuristic(segs: list[dict], every: float) -> list[dict]:
    """Facts that are easy to spot without the LLM: numbers, places and years. (Headlines need judgement, so they
    only come from the LLM - its explainer list or its punchy pop-up texts.)"""
    out: list[dict] = []
    last = -1e9

    def take(seg, spec, anchor=""):
        nonlocal last
        out.append({"segment_id": seg["id"], "anchor_words": anchor, "spec": spec, "_s": seg["s"]})
        last = seg["s"]

    # years -> timeline (2+ different years within ~30 s)
    yrs = [(s, m.group(1), m.start()) for s in segs for m in YEAR_RE.finditer(s["text"])]
    used_t: set[int] = set()
    for i, (s, y, pos) in enumerate(yrs):
        if s["id"] in used_t:
            continue
        group = [(s2, y2, p2) for s2, y2, p2 in yrs[i:] if s2["s"] - s["s"] <= 30]
        years = {g[1] for g in group}
        if len(years) >= 2:
            items, seen = [], set()
            for s2, y2, p2 in group:
                if y2 in seen:
                    continue
                seen.add(y2)
                rest = s2["text"][p2 + 4:]
                nxt = YEAR_RE.search(rest)
                rest = rest[:nxt.start()] if nxt else rest
                after = [w for w in re.findall(r"[\w']+", rest) if w.lower() not in ("and", "in", "the", "then")][:4]
                items.append(f"{y2}: {' '.join(after) or s2['text'][:24]}")
            take(s, {"kind": "timeline", "text": "", "items": items[:4]}, y)
            used_t |= {g[0]["id"] for g in group}
    for s in segs:
        t = s["text"]
        # places
        for m in re.finditer(r"[A-Z][\w'.]+(?:\s[A-Z][\w'.]+)?", t):
            name = m.group(0).strip(".")
            if name.lower() in AMBIGUOUS:
                continue
            if find_country(name):
                label = name.upper() if name.lower() in ALIASES else ""  # a city: label it with the city
                take(s, {"kind": "map", "place": name, "text": label}, name.split()[0])
                break
        # striking numbers (a unit, or a big plain number that isn't a year)
        for m in NUM_RE.finditer(t):
            num, unit = m.group(1), (m.group(2) or "").lower()
            plain = re.sub(r"\D", "", num)
            if YEAR_RE.fullmatch(plain) and not unit:
                continue
            if not unit and len(plain) < 3:
                continue
            unit_s = {"percent": "%", "per cent": "%", "times": "x", "k": "k", "lakhs": "lakh"}.get(unit, unit)
            text = f"{num}{'' if unit_s in ('%', 'x', 'k') else ' '}{unit_s}".strip()
            label = " ".join(re.findall(r"[\w']+", t[m.end():])[:5])
            take(s, {"kind": "number", "text": text, "detail": label},
                 num.split()[0].strip("$₹€£"))
            break
    return out


# -------------------------------------------------------------- slots
def build_slots(items: list[dict], transcript: dict, duration: float, excluded: set[int], existing: list[dict],
                cfg: dict, callouts: list[dict] | None = None) -> list[dict]:
    segs = [s for s in transcript.get("segments", []) if s["id"] not in excluded]
    by_id = {s["id"]: s for s in segs}
    every = EVERY.get(cfg.get("vox_density", "medium"), 18.0)
    beats = validate(items, by_id)
    want = max(1, int(duration / every))
    if len(beats) < want * 0.6:  # LLM missing or thin: add what the transcript itself offers
        beats += heuristic(segs, every)
    if len(beats) < want * 0.6 and callouts:  # the LLM's punchy pop-up lines make good title cards
        beats += [{"segment_id": c["segment_id"], "anchor_words": "", "spec": {"kind": "headline", "text": c["text"]}}
                  for c in callouts if c.get("segment_id") in by_id and len(str(c.get("text", "")).split()) >= 2]
    busy = [(b["s"] - 1.0, b["e"] + 1.0) for b in existing if b.get("status") != "skipped"]
    chosen: list[dict] = []
    # concrete graphics first (maps, numbers, timelines), then quotes, then headlines
    rank = {"timeline": 0, "map": 1, "number": 2, "quote": 3, "headline": 4, "photo": 5}
    for b in sorted(beats, key=lambda b: rank.get(b["spec"]["kind"], 9)):
        seg = by_id.get(b["segment_id"])
        if not seg:
            continue
        s = _anchor_time(seg, b.get("anchor_words", ""))
        if s < 2.5:  # the opening stays on your face (the hook)
            continue
        d = DUR.get(b["spec"]["kind"], 3.5)
        e = min(duration - 0.3, s + d)
        if e - s < 2.0:
            continue
        if any(s < e2 + 0.8 and e > s2 - 0.8 for s2, e2 in busy):
            continue
        if any(abs(s - c["s"]) < every * 0.55 for c in chosen):
            continue
        if len(chosen) >= want * 1.4 + 1:
            break
        chosen.append({"s": round(s, 3), "e": round(e, 3), "seg": seg, "spec": b["spec"]})
        busy.append((s, e))
    slots = []
    for c in sorted(chosen, key=lambda c: c["s"]):
        sp = c["spec"]
        label = {"headline": "Title card", "quote": "Highlighted quote", "number": "Animated number",
                 "map": "Map", "timeline": "Timeline"}.get(sp["kind"], "Explainer")
        what = sp.get("place") or sp.get("text") or ", ".join(sp.get("items", [])[:3])
        slots.append({
            "id": -1, "seg_id": c["seg"]["id"], "s": c["s"], "e": c["e"], "line": c["seg"]["text"],
            "description": f"Explainer graphic - {label}: {what}"[:160], "queries": [], "shot_suggestion": "",
            "card_text": str(sp.get("text") or sp.get("place") or "").upper()[:32], "status": "resolved",
            "source": "explainer", "file": "", "file_start": 0.0, "kind": "vox", "vox": sp, "score": 0,
            "preview": "", "candidates": [], "user_locked": False,
        })
    return slots


def merge(broll: list[dict], vox_slots: list[dict]) -> list[dict]:
    allv = sorted(broll + vox_slots, key=lambda s: s["s"])
    for i, s in enumerate(allv):
        s["id"] = i
    return allv


def make_previews(slots: list[dict], job_dir: Path) -> None:
    for s in slots:
        if s.get("kind") == "vox" and s.get("vox"):
            s["preview"] = preview(s["vox"], Path(job_dir) / "vox_previews" / f"v{s['id']:03d}.jpg")
