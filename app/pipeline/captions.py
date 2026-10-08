"""Animated word-by-word captions + pop-up callouts as an ASS subtitle file (burned in by FFmpeg/libass)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from ..core.paths import font_path
from .cuts import norm


@dataclass
class Style:
    font_file: str
    upper: bool
    size_landscape: float      # fraction of width
    size_portrait: float
    primary: str               # ASS &HAABBGGRR
    highlight: str
    keyword: str
    outline: float
    shadow: float
    border_style: int          # 1 outline, 3 opaque box
    back: str
    max_words_land: int
    max_words_port: int
    word_highlight: bool = True
    pop: bool = True
    outline_col: str = "&H00000000"  # outline colour, or the BOX colour when border_style == 3


STYLES = {
    "bold_pop": Style("Anton-Regular.ttf", True, 0.05, 0.095, "&H00FFFFFF", "&H0000E5FF", "&H0066FF66", 5, 0, 1,
                      "&H00000000", 4, 3),
    "clean": Style("Poppins-Bold.ttf", False, 0.036, 0.062, "&H00FFFFFF", "&H00FFD27A", "&H0000E5FF", 9, 0, 3,
                   "&H00000000", 6, 4, True, False, "&H60000000"),
    "minimal": Style("Poppins-SemiBold.ttf", False, 0.032, 0.055, "&H00FFFFFF", "&H00FFFFFF", "&H00FFFFFF", 2.5, 1.5,
                     1, "&H80000000", 8, 5, False, False),
}
CALLOUT_FONT = "Anton-Regular.ttf"


def font_family(file: str) -> tuple[str, bool]:
    p = font_path(file)
    try:
        fam, sty = ImageFont.truetype(str(p), 20).getname()
        return fam, "bold" in sty.lower()
    except Exception:
        return "Arial", True


def ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def esc(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[dict], max_words: int, max_chars: int) -> list[list[dict]]:
    chunks, cur = [], []
    for i, w in enumerate(words):
        if cur:
            gap = w["s"] - cur[-1]["e"]
            text_len = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            if len(cur) >= max_words or text_len > max_chars or gap > 0.35 or cur[-1]["w"][-1:] in ".?!,;:":
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], callouts: list[dict], W: int, H: int, style_name: str = "bold_pop",
              keywords: list[str] | None = None, hook: str = "", show_captions: bool = True,
              cta: str = "", duration: float = 0.0, cards: list[dict] | None = None) -> str:
    """words/callouts already in OUTPUT time: words [{w,s,e}], callouts [{s,e,text}]."""
    st = STYLES.get(style_name, STYLES["bold_pop"])
    portrait = H > W
    fam, bold = font_family(st.font_file)
    cfam, _ = font_family(CALLOUT_FONT)
    size = int(W * (st.size_portrait if portrait else st.size_landscape))
    margin_v = int(H * (0.27 if portrait else 0.075))
    call_size = int(W * (0.095 if portrait else 0.058))
    call_margin = int(H * (0.17 if portrait else 0.07))
    outline = st.outline * W / 1080 * (1.0 if portrait else 0.75)
    kw = {norm(k) for k in (keywords or []) if k}

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{fam},{size},{st.primary},&H000000FF,{st.outline_col},{st.back},{-1 if bold else 0},0,0,0,100,100,0,0,{st.border_style},{outline:.1f},{st.shadow},2,{int(W * 0.08)},{int(W * 0.08)},{margin_v},1
Style: Callout,{cfam},{call_size},&H00FFFFFF,&H000000FF,&H00242DFF,&H00000000,0,0,0,0,100,100,1,0,3,{max(6, int(call_size * 0.18))},0,8,40,40,{call_margin},1
Style: CTA,{cfam},{int(call_size * 0.95)},&H00000000,&H000000FF,&H0000E5FF,&H00000000,0,0,0,0,100,100,1,0,3,{max(8, int(call_size * 0.25))},0,2,40,40,{int(H * (0.42 if portrait else 0.30))},1
Style: Card,{cfam},{int(W * (0.14 if portrait else 0.11))},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,2,0,1,{max(4, int(W * 0.006))},{max(4, int(W * 0.006))},5,{int(W * 0.08)},{int(W * 0.08)},0,1
Style: Hook,{cfam},{int(call_size * 0.9)},&H00000000,&H000000FF,&H0000E5FF,&H00000000,0,0,0,0,100,100,1,0,3,{max(6, int(call_size * 0.2))},0,8,40,40,{call_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev: list[str] = []
    if show_captions and words:
        max_words = st.max_words_port if portrait else st.max_words_land
        max_chars = 18 if portrait else 34
        chunks = chunk_words(words, max_words, max_chars)
        for ci, ch in enumerate(chunks):
            c_start = ch[0]["s"]
            nxt = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else ch[-1]["e"] + 0.6
            c_end = min(ch[-1]["e"] + 0.3, nxt)
            if c_end <= c_start:
                continue
            texts = [esc(w["w"].upper() if st.upper else w["w"]) for w in ch]
            if not st.word_highlight:
                body = " ".join(_kw(t, w, kw, st) for t, w in zip(texts, ch))
                ev.append(f"Dialogue: 0,{ts(c_start)},{ts(c_end)},Cap,,0,0,0,,{_pop(st)}{body}")
                continue
            for i, w in enumerate(ch):
                s = c_start if i == 0 else w["s"]
                e = ch[i + 1]["s"] if i + 1 < len(ch) else c_end
                if e <= s:
                    continue
                parts = []
                for j, (t, ww) in enumerate(zip(texts, ch)):
                    if j == i and not st.pop:
                        parts.append(f"{{\\c{st.highlight}}}{t}{{\\c{st.primary}}}")
                    elif j == i:  # the spoken word pops: overshoot then settle (bounce)
                        parts.append(f"{{\\c{st.highlight}\\fscx128\\fscy128\\t(0,110,\\fscx110\\fscy110)}}{t}"
                                     f"{{\\c{st.primary}\\fscx100\\fscy100}}")
                    else:
                        parts.append(_kw(t, ww, kw, st))
                lead = "{\\fad(60,0)}" if i == 0 else ""
                ev.append(f"Dialogue: 0,{ts(s)},{ts(e)},Cap,,0,0,0,,{lead}{' '.join(parts)}")
    for c in callouts:
        txt = esc(c["text"].upper())[:40]
        ev.append(f"Dialogue: 1,{ts(c['s'])},{ts(c['e'])},Callout,,0,0,0,,"
                  f"{{\\fscx30\\fscy30\\frz-3\\t(0,140,\\fscx112\\fscy112)\\t(140,230,\\fscx100\\fscy100)\\fad(0,180)}}{txt}")
    if hook:
        ev.append(f"Dialogue: 2,{ts(0)},{ts(2.6)},Hook,,0,0,0,,"
                  f"{{\\fscx40\\fscy40\\t(0,160,\\fscx105\\fscy105)\\t(160,260,\\fscx100\\fscy100)\\fad(0,200)}}"
                  f"{esc(hook.upper())[:48]}")
    for c in cards or []:
        st_ms = int((c["e"] - c["s"]) * 1000)
        ev.append(f"Dialogue: 4,{ts(c['s'] + 0.12)},{ts(c['e'] - 0.05)},Card,,0,0,0,,"
                  f"{{\\fscx40\\fscy40\\t(0,170,\\fscx108\\fscy108)\\t(170,280,\\fscx100\\fscy100)"
                  f"\\t(280,{max(300, st_ms)},\\fscx106\\fscy106)\\fad(0,120)}}{esc(c['text'].upper())[:32]}")
    if cta and duration > 4:
        t0 = max(0.0, duration - 2.4)
        ev.append(f"Dialogue: 3,{ts(t0)},{ts(duration)},CTA,,0,0,0,,"
                  f"{{\\fscx20\\fscy20\\t(0,180,\\fscx112\\fscy112)\\t(180,300,\\fscx100\\fscy100)"
                  f"\\t(900,1100,\\frz-4)\\t(1100,1300,\\frz4)\\t(1300,1450,\\frz0)}}{esc(cta.upper())[:40]}")
    return head + "\n".join(ev) + "\n"


def _kw(text: str, w: dict, kw: set[str], st: Style) -> str:
    if kw and norm(w["w"]) in kw:
        return f"{{\\c{st.keyword}}}{text}{{\\c{st.primary}}}"
    return text


def _pop(st: Style) -> str:
    return "{\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)}" if st.pop else "{\\fad(60,0)}"


def write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8-sig")
    return path
