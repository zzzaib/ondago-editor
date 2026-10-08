"""The 'editor brain': local LLM (Qwen 3.5 via Ollama) turns the transcript into
structured edit decisions. Every call has a deterministic fallback so the
pipeline never dies because a small model produced garbage."""
from __future__ import annotations

import re
from collections import Counter
from typing import Callable

from ..core.ollama import Ollama
from .cuts import norm

STOP = set("""a an the and or but if then so to of in on at for with from by is are was were be been being it this that
these those i you he she we they me my your our their his her its as not no yes do does did have has had will would can
could should just like really very also about into over than too what which who whom when where why how all any some
there here up down out get got going gonna wanna kind sort thing things lot okay ok yeah um uh hmm right well now one two
because let let's i'm it's that's don't you're we're they're i've you've""".split())

SYSTEM = ("You are a senior YouTube video editor. You receive a timestamped transcript of a raw talking-head recording, "
          "one segment per line formatted as [id] mm:ss text. Answer ONLY with JSON matching the schema.")


def fmt_time(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def transcript_lines(segments: list[dict], exclude: set[int] | None = None) -> list[str]:
    exclude = exclude or set()
    return [f"[{s['id']}] {fmt_time(s['s'])} {s['text']}" for s in segments if s["id"] not in exclude]


def _chunks(lines: list[str], size: int = 110, overlap: int = 6) -> list[list[str]]:
    if len(lines) <= size:
        return [lines]
    out, i = [], 0
    while i < len(lines):
        out.append(lines[i:i + size])
        i += size - overlap
    return out


# ------------------------------------------------------------------ schemas
EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "retakes": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["retakes"],
}

SHORT_SCHEMA = {
    "type": "object",
    "properties": {"start_id": {"type": "integer"}, "end_id": {"type": "integer"}, "hook": {"type": "string"}},
    "required": ["start_id", "end_id", "hook"],
}

VISUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "broll": {"type": "array", "items": {"type": "object", "properties": {
            "segment_id": {"type": "integer"},
            "anchor_words": {"type": "string"},
            "description": {"type": "string"},
            "queries": {"type": "array", "items": {"type": "string"}},
            "shot_suggestion": {"type": "string"},
            "card_text": {"type": "string"},
        }, "required": ["segment_id", "anchor_words", "description", "queries", "shot_suggestion", "card_text"]}},
        "callouts": {"type": "array", "items": {"type": "object", "properties": {
            "segment_id": {"type": "integer"}, "text": {"type": "string"}}, "required": ["segment_id", "text"]}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "zoom_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["broll", "callouts", "keywords", "zoom_ids"],
}

META_SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
        "chapters": {"type": "array", "items": {"type": "object", "properties": {
            "segment_id": {"type": "integer"}, "title": {"type": "string"}}, "required": ["segment_id", "title"]}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "instagram_caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "thumbnail_texts": {"type": "array", "items": {"type": "string"}},
        "thumbnail_bg_prompt": {"type": "string"},
    },
    "required": ["titles", "description", "chapters", "tags", "instagram_caption", "hashtags", "thumbnail_texts",
                 "thumbnail_bg_prompt"],
}

DENSITY = {"low": 40.0, "medium": 20.0, "high": 12.0}


class Planner:
    def __init__(self, cfg: dict, log: Callable[[str], None] = print):
        self.cfg = cfg
        self.log = log
        self.model = cfg.get("llm_model", "qwen3.5:9b")
        self.llm = Ollama(cfg.get("ollama_url", "http://127.0.0.1:11434"), log=log)
        self.ok = True

    def _ask(self, prompt: str, schema: dict) -> dict | None:
        if not self.ok:
            return None
        try:
            return self.llm.chat_json(self.model, SYSTEM, prompt, schema=schema, ctx=int(self.cfg.get("llm_ctx", 16384)))
        except Exception as e:  # noqa: BLE001
            self.log(f"LLM unavailable, using heuristic fallback: {e}")
            self.ok = False
            return None

    def prepare(self, on_progress: Callable[[float, str], None] | None = None) -> None:
        try:
            self.llm.ensure_model(self.model, on_progress)
        except Exception as e:  # noqa: BLE001
            self.log(f"Could not prepare LLM ({e}); heuristic mode.")
            self.ok = False

    # ------------------------------------------------------------ retakes
    def find_retakes(self, segments: list[dict]) -> list[int]:
        ids: set[int] = set()
        valid = {s["id"] for s in segments}
        for chunk in _chunks(transcript_lines(segments)):
            r = self._ask(
                "Find segments that must be REMOVED because the speaker repeated / restarted / fumbled them "
                "(false starts, abandoned sentences, a line said twice where a later take is better, "
                "'wait let me say that again', mic checks, off-camera talk). Keep the LAST good take. "
                "Never remove unique content. Return their ids in 'retakes' (empty list if none).\n\n"
                + "\n".join(chunk), EDIT_SCHEMA)
            if r is None:
                return []
            ids.update(i for i in r.get("retakes", []) if isinstance(i, int) and i in valid)
        # safety: never let the model delete more than 35% of the video
        if len(ids) > max(2, 0.35 * len(segments)):
            self.log("LLM flagged too many retakes; ignoring its retake list.")
            return []
        return sorted(ids)

    # -------------------------------------------------------------- short
    def pick_short(self, segments: list[dict], exclude: set[int], max_seconds: float) -> dict:
        segs = [s for s in segments if s["id"] not in exclude]
        r = self._ask(
            f"Pick the single most engaging, self-contained continuous passage for an Instagram Reel of at most "
            f"{int(max_seconds)} seconds. It must start with a strong hook and make sense without context. "
            f"Return first and last segment id (inclusive) and a 3-6 word on-screen hook.\n\n"
            + "\n".join(transcript_lines(segs)), SHORT_SCHEMA)
        ids = [s["id"] for s in segs]
        if r and r.get("start_id") in ids and r.get("end_id") in ids and r["end_id"] >= r["start_id"]:
            return {"start_id": r["start_id"], "end_id": r["end_id"], "hook": r.get("hook", "")[:60]}
        # fallback: from the start, as many segments as fit
        start, total, end = segs[0]["id"], 0.0, segs[0]["id"]
        for s in segs:
            total += s["e"] - s["s"]
            if total > max_seconds:
                break
            end = s["id"]
        return {"start_id": start, "end_id": end, "hook": ""}

    # ------------------------------------------------------------ visuals
    def plan_visuals(self, segments: list[dict], exclude: set[int], duration: float) -> dict:
        segs = [s for s in segments if s["id"] not in exclude]
        per = DENSITY.get(self.cfg.get("broll_density", "medium"), 20.0)
        result = {"broll": [], "callouts": [], "keywords": [], "zoom_ids": []}
        lines = transcript_lines(segs)
        chunks = _chunks(lines)
        for chunk in chunks:
            span = max(30.0, duration * len(chunk) / max(1, len(lines)))
            n_broll = max(1, round(span / per))
            n_call = max(1, round(span / 45))
            r = self._ask(
                f"Plan visuals for this part of the video.\n"
                f"1. broll: about {n_broll} moments where cutaway footage would make the video more engaging. "
                f"For each: segment_id; anchor_words = the exact 1-3 consecutive words from that segment where the "
                f"cutaway should start; description = what the footage shows (concrete, visual); queries = 3 "
                f"stock-footage search queries in English, ordered from specific to broad, 1-3 words each, plain "
                f"visual nouns that a stock library really has (e.g. 'Meta Quest 2' -> 'vr headset', 'person vr', "
                f"'virtual reality'; 'AR glasses' -> 'smart glasses', 'futuristic glasses', 'eyeglasses'); never brand "
                f"or product names; shot_suggestion = how the creator could film or capture it themselves (e.g. "
                f"'screen recording of ...', 'close-up of ...'); card_text = 2-4 punchy words for an animated text "
                f"card if no footage fits (e.g. '10 SAAL BAAD', 'META QUEST 2', '5x SMALLER').\n"
                f"Pick moments where the speaker talks about a concrete, showable thing or a strong claim - never "
                f"greetings, filler or the first 3 seconds.\n"
                f"2. callouts: about {n_call} punchy on-screen text pops (max 4 words) for key statements.\n"
                f"3. keywords: up to 15 important single words to highlight in captions.\n"
                f"4. zoom_ids: segment ids with emotional or important emphasis for a punch-in zoom.\n\n"
                + "\n".join(chunk), VISUAL_SCHEMA)
            if r is None:
                return self._heuristic_visuals(segs, per)
            for k in result:
                result[k].extend(r.get(k, []))
        valid = {s["id"] for s in segs}
        result["broll"] = [b for b in result["broll"] if b.get("segment_id") in valid and b.get("queries")]
        result["callouts"] = [c for c in result["callouts"] if c.get("segment_id") in valid and c.get("text")]
        result["zoom_ids"] = [i for i in result["zoom_ids"] if i in valid]
        result["keywords"] = list(dict.fromkeys(norm(k) for k in result["keywords"] if k))[:25]
        return result

    def _heuristic_visuals(self, segs: list[dict], per: float) -> dict:
        broll, t_last = [], -1e9
        for s in segs:
            if s["s"] - t_last < per or s["e"] - s["s"] < 1.5:
                continue
            kws = [w for w in norm(s["text"]).split() if w not in STOP and len(w) > 3]
            if len(kws) < 2:
                continue
            q = " ".join(sorted(kws, key=len, reverse=True)[:2])
            broll.append({"segment_id": s["id"], "anchor_words": "", "description": s["text"][:120],
                          "queries": [q, kws[0]], "shot_suggestion": f"Footage illustrating: {s['text'][:80]}",
                          "card_text": q.upper()})
            t_last = s["s"]
        words = Counter(w for s in segs for w in norm(s["text"]).split() if w not in STOP and len(w) > 4)
        return {"broll": broll, "callouts": [], "keywords": [w for w, _ in words.most_common(10)], "zoom_ids": []}

    # ----------------------------------------------------------- metadata
    def metadata(self, segments: list[dict], exclude: set[int]) -> dict:
        segs = [s for s in segments if s["id"] not in exclude]
        lines = transcript_lines(segs)
        if len(lines) > 400:  # keep context small: sample evenly
            step = len(lines) / 400
            lines = [lines[int(i * step)] for i in range(400)]
        lang = self.cfg.get("_language", "en")
        r = self._ask(
            f"Write publishing metadata for this video (spoken language code: {lang}; write in the same language "
            f"the speaker uses, English if mixed).\n"
            f"- titles: 3 click-worthy but honest YouTube titles (max 70 chars).\n"
            f"- description: 2 short paragraphs for YouTube (no timestamps, no hashtags).\n"
            f"- chapters: 3-8 chapters, first one must be the first segment; segment_id + short title.\n"
            f"- tags: 10-15 YouTube tags.\n"
            f"- instagram_caption: 1-3 lines with a hook and a call to action.\n"
            f"- hashtags: 8-12 relevant hashtags without '#'.\n"
            f"- thumbnail_texts: 3 alternative thumbnail texts, 2-4 words each, ALL CAPS style, curiosity-driven.\n"
            f"- thumbnail_bg_prompt: an image-generation prompt for a bold, colorful, uncluttered background scene "
            f"related to the topic (no people, no text).\n\n" + "\n".join(lines), META_SCHEMA)
        if r:
            r["hashtags"] = [h.lstrip("#").replace(" ", "") for h in r.get("hashtags", []) if h]
            r["chapters"] = [c for c in r.get("chapters", []) if c.get("segment_id") in {s["id"] for s in segs}]
            if r.get("titles") and r.get("thumbnail_texts"):
                return r
        first = segs[0]["text"] if segs else "My video"
        words = Counter(w for s in segs for w in norm(s["text"]).split() if w not in STOP and len(w) > 3)
        top = [w for w, _ in words.most_common(12)]
        title = re.sub(r"\s+", " ", first)[:70]
        return {"titles": [title], "description": " ".join(s["text"] for s in segs[:4])[:500],
                "chapters": [], "tags": top, "instagram_caption": title, "hashtags": top[:8],
                "thumbnail_texts": [" ".join(top[:3]).upper() or "WATCH THIS"],
                "thumbnail_bg_prompt": f"vibrant abstract background about {' '.join(top[:3])}, bokeh, studio lighting"}

    def unload(self) -> None:
        if self.ok:
            self.llm.unload(self.model)
