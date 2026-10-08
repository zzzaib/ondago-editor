"""Pipeline orchestration: analyze() -> (review) -> render() -> export.

VRAM discipline for 8 GB: Whisper -> unload -> Qwen (Ollama) -> SigLIP -> Qwen-VL -> unload -> FLUX -> unload.
Only one large model is resident at any time."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from ..core import ffmpeg
from ..core.job import Job
from . import captions, cuts, face, render
from .broll import BrollResolver, build_slots
from .planner import Planner, fmt_time
from .timeline import Timeline, compute_keeps
from .transcribe import speech_regions, transcribe

Log = Callable[[str], None]
Prog = Callable[[float, str], None]


class Stages:
    """Maps per-stage 0..1 progress into an overall 0..1 bar."""

    def __init__(self, weights: dict[str, float], cb: Prog):
        tot = sum(weights.values())
        self.w = {k: v / tot for k, v in weights.items()}
        self.order = list(weights)
        self.cb = cb

    def sub(self, name: str) -> Prog:
        base = sum(self.w[k] for k in self.order[: self.order.index(name)])
        return lambda p, msg="": self.cb(min(1.0, base + self.w[name] * max(0.0, min(1.0, p))), msg)


# =================================================================== ANALYZE
def analyze(job: Job, cfg: dict, log: Log, progress: Prog) -> None:
    st = Stages({"probe": 2, "transcribe": 22, "llm": 25, "faces": 6, "broll": 25, "thumbs": 20}, progress)
    plan = job.plan
    src = job.source
    p = st.sub("probe")
    p(0, "Reading video")
    info = ffmpeg.probe(src)
    if info["duration"] <= 0:
        raise RuntimeError("Could not read video duration.")
    # ---- orientation: fix phone clips saved sideways (landscape recorded with a portrait flag, etc.)
    rot_cfg = str(cfg.get("rotation", "auto"))
    if rot_cfg == "auto":
        p(0.3, "Checking video orientation")
        rot = face.detect_rotation(src, info["duration"], log=log)
    else:
        rot = int(rot_cfg) % 360
    if rot:
        log(f"Video appears sideways - rotating {rot} degrees clockwise")
    info["rotate"] = rot
    if rot % 180 == 90:
        info["width"], info["height"] = info["height"], info["width"]
    log(f"Source: {info['width']}x{info['height']} "
        f"({'portrait' if info['height'] > info['width'] else 'landscape'}), {info['fps']:.2f} fps, "
        f"{info['duration']:.1f}s")
    plan["media"] = info
    wav = job.path("audio16k.wav")
    if info["has_audio"]:
        ffmpeg.extract_audio_wav(src, wav)
    p(1, "")

    # ---- speech
    p = st.sub("transcribe")
    if info["has_audio"]:
        p(0, "Transcribing speech (Whisper)")
        lang = cfg.get("language", "auto")
        script_pref = cfg.get("caption_script", "auto")
        tr = transcribe(wav, cfg.get("whisper_model", "large-v3-turbo"), lang,
                        cfg.get("whisper_compute_type", "int8_float16"), on_progress=lambda x: p(x, "Transcribing"),
                        log=log, hinglish_asr=cfg.get("hinglish_asr", True) and script_pref != "native",
                        vocabulary=cfg.get("vocabulary", ""))
        vad = speech_regions(wav)
    else:
        tr, vad = {"language": "en", "duration": info["duration"], "segments": []}, []
    plan["language"] = tr["language"]

    # ---- editor LLM (also used for Hinglish captions)
    p = st.sub("llm")
    planner = Planner({**cfg, "_language": tr["language"]}, log=log)
    p(0.02, "Loading editor model (Qwen 3.5)")
    planner.prepare(lambda x, m: p(0.02 + 0.2 * x, f"Downloading LLM: {m}"))

    script = cfg.get("caption_script", "auto")
    hinglish = script != "native" and (script == "hinglish" or cfg.get("language") == "hinglish"
                                       or tr["language"] in ("hi", "ur", "hinglish"))
    if hinglish and tr["segments"]:
        from .hinglish import to_hinglish
        p(0.22, "Converting captions to Hinglish (Roman script)")
        to_hinglish(tr, planner.llm if planner.ok else None, planner.model, log=log)
        plan["caption_script"] = "hinglish"
    if cfg.get("transcript_cleanup", True) and planner.ok and tr["segments"]:
        from .transcript_fix import fix_transcript
        p(0.23, "Proof-reading the transcript (names, English words)")
        fix_transcript(tr, planner.llm, planner.model, cfg.get("vocabulary", ""),
                       "Hinglish in Roman letters" if hinglish else "", log=log)
    job.save_json("transcript.json", tr)
    cfg = {**cfg, "_language": "hinglish (Hindi + English, Roman script)" if hinglish else tr["language"]}
    planner.cfg = cfg

    # ---- mechanical cuts
    p(0.24, "Finding silences and filler words")
    all_cuts = cuts.detect_pause_and_filler_cuts(tr, vad, info["duration"], cfg) if tr["segments"] else []
    segs = tr["segments"]
    retake_ids: list[int] = []
    if cfg.get("remove_retakes", True) and segs:
        p(0.25, "Detecting retakes and false starts")
        heur = cuts.detect_retakes_heuristic(tr)
        llm_ids = planner.find_retakes(segs)
        retake_ids = sorted(set(heur) | set(llm_ids))
        all_cuts += cuts.retake_cuts(tr, retake_ids)
    excluded = set(retake_ids)
    if tr["segments"]:
        audio = None
        try:
            from faster_whisper.audio import decode_audio
            audio = decode_audio(str(wav), sampling_rate=16000)
        except Exception:  # noqa: BLE001
            pass
        before = sum(c["e"] - c["s"] for c in all_cuts)
        all_cuts = cuts.protect_words(all_cuts, tr, vad, cfg, audio)
        log(f"Word protection: kept {before - sum(c['e'] - c['s'] for c in all_cuts):.2f}s that would have clipped words")
    plan["cuts"] = cuts.finalize(all_cuts)
    visuals = {"broll": [], "callouts": [], "keywords": [], "zoom_ids": []}
    meta: dict = {}
    if segs:
        p(0.45, "Planning B-roll, callouts and zooms")
        visuals = planner.plan_visuals(segs, excluded, info["duration"])
        if cfg.get("output_reel", True) and cfg.get("reel_mode", "highlight") == "highlight":
            p(0.7, "Picking the best Reel moment")
            plan["short"] = planner.pick_short(segs, excluded, float(cfg.get("reel_max_seconds", 90)))
        p(0.8, "Writing title, description, tags")
        meta = planner.metadata(segs, excluded)
    plan["keywords"] = visuals.get("keywords", [])
    by_id = {s["id"]: s for s in segs}
    plan["zooms"] = [{"s": by_id[i]["s"], "e": by_id[i]["e"]} for i in visuals.get("zoom_ids", []) if i in by_id]
    plan["callouts"] = [{"s": by_id[c["segment_id"]]["s"], "e": by_id[c["segment_id"]]["s"] + 1.6,
                         "text": c["text"][:40], "enabled": True}
                        for c in visuals.get("callouts", []) if c["segment_id"] in by_id]
    plan["metadata"] = {
        "title": (meta.get("titles") or [""])[0], "titles": meta.get("titles", []),
        "description": meta.get("description", ""), "chapters": meta.get("chapters", []),
        "tags": meta.get("tags", []), "instagram_caption": meta.get("instagram_caption", ""),
        "hashtags": meta.get("hashtags", []), "thumbnail_texts": meta.get("thumbnail_texts", []),
        "thumbnail_bg_prompt": meta.get("thumbnail_bg_prompt", ""),
    }
    job.save()

    # ---- faces
    p = st.sub("faces")
    p(0, "Tracking face for zooms and vertical crop")
    trk = face.track(src, fps=2.0, rotate=info.get("rotate", 0))
    job.save_json("faces.json", trk)
    plan["face_ratio"] = face.face_ratio(trk)

    # ---- b-roll
    p = st.sub("broll")
    slots = build_slots(visuals, tr, info["duration"], excluded) if cfg.get("broll_mode", "both") != "off" else []
    plan["broll"] = BrollResolver(cfg, job.dir, log, planner).resolve(slots, on_progress=p)
    job.save()

    # ---- thumbnails
    p = st.sub("thumbs")
    keeps = compute_keeps(info["duration"], plan["cuts"], info["fps"])
    from .thumbnail import make_thumbnails
    try:
        plan["thumbnails"] = make_thumbnails(src, keeps, job.dir, plan["metadata"]["thumbnail_texts"],
                                             plan["metadata"]["thumbnail_bg_prompt"] or "colorful abstract studio",
                                             cfg, planner, log, p, rotate=info.get("rotate", 0))
    except Exception as e:  # noqa: BLE001
        log(f"Thumbnail generation failed: {e}")
        plan["thumbnails"] = {"options": [], "choice": 0}
    planner.unload()
    need = [s for s in plan["broll"] if s["status"] == "needs_input"]
    plan["stage"] = "needs_broll" if need else "analyzed"
    job.save()
    log(f"Analysis done: {len(plan['cuts'])} cuts, {len(plan['broll'])} B-roll slots ({len(need)} need you), "
        f"{len(plan['callouts'])} callouts")
    progress(1.0, "Analysis complete")


# ==================================================================== RENDER
def _timeline(job: Job, cfg: dict):
    info = job.plan["media"]
    keeps = compute_keeps(info["duration"], job.plan.get("cuts", []), info["fps"])
    trk = job.load_json("faces.json", [])
    zooms = [(z["s"], z["e"]) for z in job.plan.get("zooms", [])]
    pieces = render.build_pieces(keeps, zooms, cfg.get("zoom_style", "jumpcut"), min(60.0, info["fps"]), trk,
                                 smooth=cfg.get("smooth_zoom", True))
    tl = Timeline([(pc["s"], pc["e"]) for pc in pieces])
    return keeps, pieces, tl, trk


def _edit_words(job: Job, tl: Timeline, excluded_ranges) -> list[dict]:
    tr = job.load_json("transcript.json", {"segments": []})
    fillers = {cuts.norm(f) for f in job.plan.get("_filler_words", [])}
    out = []
    for w in cuts.flatten_words(tr):
        mid = (w["s"] + w["e"]) / 2
        if tl.to_edit(mid, "none") is None:
            continue
        if cuts.norm(w["w"]) in fillers:
            continue
        s = tl.to_edit(w["s"], "next")
        e = tl.to_edit(w["e"], "prev")
        if e is None or s is None or e <= s:
            e = (s or 0) + 0.12
        out.append({"w": w["w"], "s": s, "e": e})
    return out


def render_all(job: Job, cfg: dict, log: Log, progress: Prog) -> dict:
    plan = job.plan
    info = plan["media"]
    want_yt = cfg.get("output_youtube", True)
    want_reel = cfg.get("output_reel", True)
    st = Stages({"base": 35, "youtube": 35 if want_yt else 0.01, "reel": 25 if want_reel else 0.01, "export": 5},
                progress)
    plan["_filler_words"] = cfg.get("filler_words", []) if cfg.get("remove_fillers", True) else []
    keeps, pieces, tl, trk = _timeline(job, cfg)
    base = render.render_base(job.source, info, pieces, job.dir, cfg, st.sub("base"), log)
    base_info = ffmpeg.probe(base)
    words = _edit_words(job, tl, None)
    # map visuals into edit time
    broll = []
    for s in plan.get("broll", []):
        if s["status"] != "resolved":
            continue
        r = tl.range_to_edit(s["s"], s["e"])
        if r:
            broll.append({**s, "t0": r[0], "t1": min(r[1], r[0] + (s["e"] - s["s"]))})
    callouts = []
    for c in plan.get("callouts", []):
        if not c.get("enabled", True) or not cfg.get("callouts", True):
            continue
        r = tl.range_to_edit(c["s"], c["e"])
        if r:
            callouts.append({"s": r[0], "e": r[0] + 1.6, "text": c["text"]})
    kw = plan.get("keywords", []) if cfg.get("caption_keywords", True) else []
    outputs = {}

    if want_yt:
        W, H = render.output_dims(base_info, False, cfg)
        ass = captions.write(job.path("captions_youtube.ass"),
                             captions.build_ass(words, callouts, W, H, cfg.get("caption_style", "bold_pop"), kw,
                                                show_captions=cfg.get("captions", True),
                                                cta=cfg.get("cta_youtube", ""), duration=tl.duration,
                                                cards=_cards(broll, 0.0, tl.duration)))
        outputs["youtube"] = str(render.render_final(
            job.dir, base, base_info, job.path("youtube.mp4"), vertical=False, t0=0, t1=tl.duration,
            broll=broll, ass_file=ass.name, cfg=cfg, pieces=pieces, face_track=trk, layout="",
            on_progress=st.sub("youtube"), log=log,
            sfx_events=sound_events(broll, callouts, pieces, 0.0, tl.duration, False, bool(cfg.get("cta_youtube")))))

    if want_reel:
        t0, t1, hook = 0.0, tl.duration, ""
        sh = plan.get("short")
        tr = job.load_json("transcript.json", {"segments": []})
        by_id = {s["id"]: s for s in tr["segments"]}
        if cfg.get("reel_mode", "highlight") == "highlight" and sh and sh.get("start_id") in by_id:
            a = tl.to_edit(by_id[sh["start_id"]]["s"], "next")
            b = tl.to_edit(by_id[sh.get("end_id", sh["start_id"])]["e"], "prev")
            if a is not None and b is not None and b - a > 5:
                t0, t1, hook = max(0.0, a - 0.05), min(tl.duration, b + 0.3), sh.get("hook", "")
        max_len = float(cfg.get("reel_max_seconds", 90)) if cfg.get("reel_mode") == "highlight" else 900.0
        t1 = min(t1, t0 + max_len)
        rw = [{"w": w["w"], "s": w["s"] - t0, "e": w["e"] - t0} for w in words if t0 <= w["s"] < t1]
        hook_end = t0 + (2.8 if hook else 0.0)  # don't stack a callout on top of the opening hook
        rc = [{**c, "s": c["s"] - t0, "e": c["e"] - t0} for c in callouts if hook_end <= c["s"] < t1 - 1]
        ass = captions.write(job.path("captions_reel.ass"),
                             captions.build_ass(rw, rc, 1080, 1920, cfg.get("caption_style", "bold_pop"), kw, hook=hook,
                                                show_captions=cfg.get("captions", True),
                                                cta=cfg.get("cta_reel", ""), duration=t1 - t0,
                                                cards=_cards(broll, t0, t1)))
        layout = cfg.get("vertical_layout", "face")
        if layout == "face" and plan.get("face_ratio", 0) < 0.3:
            layout = "blur"  # screen recordings / no face: letterbox on blurred background
        outputs["reel"] = str(render.render_final(
            job.dir, base, base_info, job.path("reel.mp4"), vertical=True, t0=t0, t1=t1, broll=broll,
            ass_file=ass.name, cfg=cfg, pieces=pieces, face_track=trk, layout=layout,
            on_progress=st.sub("reel"), log=log,
            sfx_events=sound_events(broll, [c for c in callouts if c["s"] >= hook_end], pieces, t0, t1, bool(hook),
                                    bool(cfg.get("cta_reel")))))
        plan["reel_range"] = [t0, t1]

    p = st.sub("export")
    p(0.2, "Exporting")
    plan["outputs"] = outputs
    plan["edit_duration"] = tl.duration
    plan["metadata"]["chapters_text"] = chapters_text(job, tl)
    plan["export_dir"] = str(export(job, cfg))
    plan["stage"] = "rendered"
    job.save()
    p(1.0, "Done")
    log(f"Finished: {plan['export_dir']}")
    return outputs


def _cards(broll: list[dict], t0: float, t1: float) -> list[dict]:
    out = []
    for b in broll:
        if b.get("kind") == "graphic" and t0 <= b["t0"] < t1 and b["t1"] - b["t0"] >= 0.8:
            out.append({"s": b["t0"] - t0, "e": min(b["t1"], t1) - t0, "text": b.get("card_text", "")})
    return out


def sound_events(broll: list[dict], callouts: list[dict], pieces: list[dict], t0: float, t1: float,
                 hook: bool, cta: bool) -> list[tuple[float, str, float]]:
    """Where the whooshes and pops go, in output time."""
    ev: list[tuple[float, str, float]] = []
    for b in broll:
        if t0 <= b["t0"] < t1:
            ev.append((b["t0"] - t0, "whoosh", 0.0))
    for c in callouts:
        if t0 <= c["s"] < t1:
            ev.append((c["s"] - t0, "pop", -3.0))
    acc = 0.0
    for p in pieces:  # punch-in zoom cuts get a soft whoosh
        if p.get("emph") and t0 + 0.5 <= acc < t1:
            ev.append((acc - t0, "whoosh", -7.0))
        acc += p["e"] - p["s"]
    if hook:
        ev.append((0.04, "pop", 0.0))
    dur = t1 - t0
    if cta and dur > 4:
        ev.append((dur - 2.4, "riser", -6.0))
        ev.append((dur - 2.4, "pop", 0.0))
    # don't stack sounds closer than 0.35 s
    ev.sort()
    out: list[tuple[float, str, float]] = []
    for e in ev:
        if not out or e[0] - out[-1][0] >= 0.35:
            out.append(e)
    return out


def chapters_text(job: Job, tl: Timeline) -> str:
    tr = job.load_json("transcript.json", {"segments": []})
    by_id = {s["id"]: s for s in tr["segments"]}
    ch = []
    for c in job.plan.get("metadata", {}).get("chapters", []):
        s = by_id.get(c.get("segment_id"))
        if not s:
            continue
        t = tl.to_edit(s["s"], "next") or 0.0
        if not ch:
            t = 0.0
        if ch and t - ch[-1][0] < 10:
            continue
        ch.append((t, c.get("title", "").strip()[:60]))
    if len(ch) < 3:
        return ""
    return "\n".join(f"{fmt_time(t)} {title}" for t, title in ch)


def youtube_description(plan: dict) -> str:
    m = plan.get("metadata", {})
    if m.get("youtube_description"):
        return m["youtube_description"]
    parts = [m.get("description", "").strip()]
    if m.get("chapters_text"):
        parts.append("Chapters:\n" + m["chapters_text"])
    credits = sorted({f"{s['source'].title()} - {s.get('credit') or 'stock'}" for s in plan.get("broll", [])
                      if s.get("status") == "resolved" and s.get("source") in ("pexels", "pixabay")})
    if credits:
        parts.append("Stock footage: " + ", ".join(credits))
    tags = m.get("hashtags", [])[:3]
    if tags:
        parts.append(" ".join("#" + t for t in tags))
    return "\n\n".join(p for p in parts if p)


def instagram_caption(plan: dict) -> str:
    m = plan.get("metadata", {})
    if m.get("instagram_caption_full"):
        return m["instagram_caption_full"]
    tags = " ".join("#" + t for t in m.get("hashtags", [])[:15])
    return (m.get("instagram_caption", "").strip() + "\n\n" + tags).strip()[:2200]


def export(job: Job, cfg: dict) -> Path:
    plan = job.plan
    src = job.source
    base_dir = Path(cfg["output_dir"]) if cfg.get("output_dir") else src.parent
    out = base_dir / f"{src.stem}_OnDaGo"
    out.mkdir(parents=True, exist_ok=True)
    for k, v in plan.get("outputs", {}).items():
        shutil.copy(v, out / f"{src.stem}_{k}.mp4")
    th = plan.get("thumbnails", {})
    if th.get("options"):
        sel = th["options"][min(th.get("choice", 0), len(th["options"]) - 1)]
        shutil.copy(sel["path"], out / f"{src.stem}_thumbnail.jpg")
        shutil.copy(sel["cover"], out / f"{src.stem}_reel_cover.jpg")
    m = plan.get("metadata", {})
    txt = (f"TITLE\n{m.get('title', '')}\n\nALTERNATIVE TITLES\n" + "\n".join(m.get("titles", [])) +
           f"\n\nYOUTUBE DESCRIPTION\n{youtube_description(plan)}\n\nTAGS\n{', '.join(m.get('tags', []))}"
           f"\n\nINSTAGRAM CAPTION\n{instagram_caption(plan)}\n")
    (out / f"{src.stem}_metadata.txt").write_text(txt, encoding="utf-8")
    (out / f"{src.stem}_metadata.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
