"""Context-aware transcript correction.

Speech models mishear names and English words inside Hinglish ("kompaikt", "reban", "Meta Quest
3 mein to cam"). The editor LLM reads the whole transcript plus your vocabulary list and fixes
obvious mishearings WITHOUT paraphrasing. The corrected words are then re-aligned onto the original
word timings, so captions stay in sync.
"""
from __future__ import annotations

import difflib
import re
from typing import Callable

SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "integer"}, "text": {"type": "string"}}, "required": ["id", "text"]}}},
    "required": ["lines"],
}

SYSTEM = ("You are a meticulous subtitle proof-reader for Indian YouTube creators. You fix speech-recognition "
          "mistakes only. Reply only with JSON.")


def _norm(t: str) -> str:
    return re.sub(r"[^\w']", "", t.lower())


def realign(words: list[dict], new_text: str) -> list[dict]:
    """Map corrected tokens back onto the original word timings."""
    new = [t for t in new_text.split() if t.strip()]
    if not new:
        return words
    a = [_norm(w["w"]) for w in words]
    b = [_norm(t) for t in new]
    out: list[dict] = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                out.append({**words[i1 + k], "w": new[j1 + k]})
        elif op == "replace" and (i2 - i1) == (j2 - j1):  # same count: one-to-one, keep exact timings
            for k in range(i2 - i1):
                out.append({**words[i1 + k], "w": new[j1 + k]})
        elif op == "replace":
            src, dst = words[i1:i2], new[j1:j2]
            s0, e0 = src[0]["s"], src[-1]["e"]
            total = sum(max(1, len(t)) for t in dst)
            t = s0
            for tok in dst:
                d = (e0 - s0) * max(1, len(tok)) / total
                out.append({"w": tok, "s": round(t, 3), "e": round(t + d, 3), "p": min(w.get("p", 1) for w in src)})
                t += d
        elif op == "insert":
            # new words with no audio counterpart: squeeze them into the previous word's slot
            anchor = out[-1] if out else {"s": words[0]["s"], "e": words[0]["s"] + 0.1}
            span = max(0.06, anchor["e"] - anchor["s"])
            n = j2 - j1 + 1
            if out:
                out[-1] = {**out[-1], "e": round(anchor["s"] + span / n, 3)}
            for k, tok in enumerate(new[j1:j2], start=1):
                out.append({"w": tok, "s": round(anchor["s"] + span * k / n, 3),
                            "e": round(anchor["s"] + span * (k + 1) / n, 3), "p": 0.5})
        # "delete": the word was a mis-hearing of nothing -> drop
    return out or words


def fix_transcript(transcript: dict, llm, model: str, vocabulary: str = "", style: str = "",
                   log: Callable[[str], None] = print, batch: int = 18) -> int:
    segs = transcript.get("segments", [])
    if not segs or llm is None:
        return 0
    vocab = ", ".join(v.strip() for v in vocabulary.split(",") if v.strip())
    full = " ".join(s["text"] for s in segs)[:6000]
    changed = 0
    for b in range(0, len(segs), batch):
        chunk = segs[b:b + batch]
        lines = "\n".join(f'{{"id": {s["id"]}, "text": {s["text"]!r}}}' for s in chunk)
        prompt = (
            "Fix speech-recognition mistakes in these subtitle lines.\n"
            "Rules:\n"
            "- Only fix words that were clearly mis-heard: brand/product names, English words spelled "
            "phonetically (kompaikt -> compact, sirph -> sirf, aakchuli -> actually, reban -> Ray-Ban), "
            "numbers, broken words.\n"
            f"- Language/style: {style or 'same as the speaker'}. Hinglish stays in Roman letters.\n"
            "- Do NOT paraphrase, translate, reorder, summarise or add words. Keep fillers (um, uh, hmm).\n"
            "- If a line is fine, return it unchanged. Return every id.\n"
            + (f"- Names/terms that appear in this video: {vocab}\n" if vocab else "")
            + f"\nWhole video for context: {full}\n\nLines to fix:\n{lines}")
        try:
            r = llm.chat_json(model, SYSTEM, prompt, schema=SCHEMA, ctx=12288, temperature=0.1)
        except Exception as e:  # noqa: BLE001
            log(f"Transcript correction skipped ({e})")
            return changed
        by_id = {ln.get("id"): str(ln.get("text", "")) for ln in r.get("lines", []) if isinstance(ln, dict)}
        for s in chunk:
            new = by_id.get(s["id"], "").strip()
            if not new or new == s["text"]:
                continue
            ratio = difflib.SequenceMatcher(None, s["text"].lower(), new.lower()).ratio()
            n_old, n_new = len(s["text"].split()), len(new.split())
            if ratio < 0.55 or abs(n_new - n_old) > max(2, n_old * 0.3):
                continue  # the model rewrote the line instead of correcting it -> ignore
            s.setdefault("text_asr", s["text"])
            s["words"] = realign(s["words"], new)
            s["text"] = new
            s["s"], s["e"] = s["words"][0]["s"], s["words"][-1]["e"]
            changed += 1
    log(f"Transcript correction: fixed {changed} of {len(segs)} lines")
    return changed
