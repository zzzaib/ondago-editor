"""Hinglish captions: Devanagari transcript -> Roman script the way Indian creators type it.

Whisper transcribes Hindi/Hinglish speech in Devanagari (and even writes English words like
"video" as "वीडियो"). Captions for Hinglish reels are normally Roman ("aaj hum video edit karenge").

1. The local LLM converts word-by-word (keeps English words English, natural spellings).
2. A rule-based transliterator (with Hindi schwa deletion) is the offline fallback for any word
   the LLM skips or mangles. Word timings are never touched - only the text of each word changes.
"""
from __future__ import annotations

import re
from typing import Callable

DEV = re.compile(r"[ऀ-ॿ]")

CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "d", "ढ़": "rh",
    "फ़": "f", "य़": "y",
}
NUKTA_MAP = {"क": "क़", "ख": "ख़", "ग": "ग़", "ज": "ज़", "ड": "ड़", "ढ": "ढ़", "फ": "फ़", "य": "य़"}
VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
          "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e"}
MATRAS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
          "ौ": "au", "ॉ": "o", "ॅ": "e"}
VIRAMA, NUKTA, ANUSVARA, CHANDRA, VISARGA = "्", "़", "ं", "ँ", "ः"
DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}
LABIALS = {"p", "ph", "b", "bh", "m"}
PUNCT = ".,!?;:\"'()[]{}\u2026\u0964\u0965-\u2013\u2014 "

# common words where rules alone sound off
COMMON = {"है": "hai", "हैं": "hain", "नहीं": "nahi", "क्या": "kya", "मैं": "main", "में": "mein", "यह": "yeh",
          "वह": "woh", "और": "aur", "भी": "bhi", "तो": "toh", "हम": "hum", "तुम": "tum", "आप": "aap",
          "कि": "ki", "की": "ki", "का": "ka", "के": "ke", "को": "ko", "से": "se", "पे": "pe", "पर": "par",
          "था": "tha", "थी": "thi", "थे": "the", "हाँ": "haan", "हां": "haan", "ना": "na", "जो": "jo",
          "कैसे": "kaise", "ऐसे": "aise", "वैसे": "waise", "बहुत": "bahut", "अच्छा": "accha", "ठीक": "theek",
          "मतलब": "matlab", "क्योंकि": "kyunki", "लेकिन": "lekin", "अगर": "agar", "सब": "sab", "कुछ": "kuch",
          "एक": "ek", "दो": "do", "अब": "ab", "जब": "jab", "तब": "tab", "यार": "yaar", "भाई": "bhai"}


def has_devanagari(text: str) -> bool:
    return bool(DEV.search(text or ""))


def _syllables(word: str) -> list[list]:
    """[[consonant_roman|None, vowel_roman|None, inherent_schwa(bool)], ...]"""
    out: list[list] = []
    chars = list(word)
    i = 0
    while i < len(chars):
        c = chars[i]
        if c in NUKTA_MAP and i + 1 < len(chars) and chars[i + 1] == NUKTA:
            c = NUKTA_MAP[c]
            i += 1
        if c in CONS:
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if nxt in MATRAS:
                out.append([CONS[c], MATRAS[nxt], False])
                i += 2
                continue
            if nxt == VIRAMA:
                out.append([CONS[c], None, False])
                i += 2
                continue
            out.append([CONS[c], "a", True])
        elif c in VOWELS:
            out.append([None, VOWELS[c], False])
        elif c in (ANUSVARA, CHANDRA):
            out.append(["~n", None, False])
        elif c == VISARGA:
            out.append(["h", None, False])
        elif c in DIGITS:
            out.append([DIGITS[c], None, False])
        elif c not in (NUKTA, VIRAMA, "‍", "‌"):
            out.append([c, None, False])
        i += 1
    return out


def rule_translit(word: str) -> str:
    core = word.strip()
    body = core.strip(PUNCT)
    i0 = core.find(body) if body else 0
    pre, post = core[:i0], core[i0 + len(body):]
    if body in COMMON:
        return (pre + COMMON[body] + post).replace("\u0964", ".").replace("\u0965", ".")
    syl = _syllables(body)
    # final schwa deletion
    for j in range(len(syl) - 1, -1, -1):
        if syl[j][0] == "~n":
            continue
        if syl[j][2] and len(syl) > 1:
            syl[j][1] = None
        break
    # medial schwa deletion  V C a C V  ->  V C C V   (right to left)
    for j in range(len(syl) - 2, 0, -1):
        cur, prev, nxt = syl[j], syl[j - 1], syl[j + 1]
        if cur[2] and cur[1] == "a" and prev[1] and nxt[0] not in (None, "~n") and nxt[1]:
            cur[1] = None
    out = []
    for k, (c, v, _) in enumerate(syl):
        if c == "~n":
            nxt_c = syl[k + 1][0] if k + 1 < len(syl) else None
            out.append("m" if nxt_c in LABIALS else "n")
            continue
        out.append((c or "") + (v or ""))
    s = "".join(out)
    # Hinglish spelling habits
    s = re.sub(r"aa$", "a", s)
    s = re.sub(r"ee$", "i", s)
    s = re.sub(r"oo$", "u", s)
    return (pre + s + post).replace("\u0964", ".").replace("\u0965", ".")


SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "integer"}, "words": {"type": "array", "items": {"type": "string"}}},
        "required": ["id", "words"]}}},
    "required": ["lines"],
}

SYSTEM = ("You convert Hindi/Hinglish transcripts written in Devanagari into natural Hinglish written in the "
          "Roman alphabet, exactly how Indian YouTubers write captions. Reply only with JSON.")


def to_hinglish(transcript: dict, llm=None, model: str = "", log: Callable[[str], None] = print,
                batch: int = 25) -> int:
    """In-place: rewrites word text (and segment text) to Roman Hinglish. Returns words changed."""
    segs = [s for s in transcript.get("segments", []) if has_devanagari(s.get("text", ""))]
    if not segs:
        return 0
    llm_out: dict[int, list[str]] = {}
    if llm is not None:
        for b in range(0, len(segs), batch):
            chunk = segs[b:b + batch]
            lines = "\n".join(f'{{"id": {s["id"]}, "words": {[w["w"] for w in s["words"]]}}}' for s in chunk)
            prompt = ("Convert every word to Roman-script Hinglish. Rules:\n"
                      "- Hindi words: common spellings (है->hai, नहीं->nahi, क्या->kya, करेंगे->karenge, "
                      "अच्छा->accha).\n"
                      "- English words written in Devanagari become normal English (वीडियो->video, "
                      "एडिटिंग->editing, टूल->tool).\n"
                      "- Words already in Roman letters stay exactly the same. Keep punctuation.\n"
                      "- The output 'words' list for each id MUST have exactly the same number of items as the "
                      "input, in the same order (one output word per input word).\n\n" + lines)
            try:
                r = llm.chat_json(model, SYSTEM, prompt, schema=SCHEMA, ctx=8192, temperature=0.1)
                for ln in r.get("lines", []):
                    if isinstance(ln.get("id"), int) and isinstance(ln.get("words"), list):
                        llm_out[ln["id"]] = [str(w) for w in ln["words"]]
            except Exception as e:  # noqa: BLE001
                log(f"Hinglish LLM pass failed ({e}); using rule-based transliteration")
                break
    changed = 0
    for s in segs:
        out = llm_out.get(s["id"])
        ok = out is not None and len(out) == len(s["words"])
        for i, w in enumerate(s["words"]):
            if not has_devanagari(w["w"]):
                continue
            new = out[i].strip() if ok and out[i].strip() and not has_devanagari(out[i]) else rule_translit(w["w"])
            w["orig"] = w["w"]
            w["w"] = new
            changed += 1
        s["text_orig"] = s["text"]
        s["text"] = " ".join(w["w"] for w in s["words"])
    log(f"Hinglish captions: converted {changed} words in {len(segs)} segments "
        f"({len(llm_out)} by LLM, rest by rules)")
    return changed
