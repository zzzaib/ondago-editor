"""B-roll orchestration.

slot lifecycle:  pending -> resolved | needs_input | skipped
  * mode toggle  : off / offline / online / both
  * offline      : your own footage library (SigLIP search) + optional AI stills (FLUX)
  * online       : Pexels + Pixabay
  * verification : Qwen-VL checks the best candidates actually match the moment
  * nothing good : slot becomes `needs_input` -> the Review tab asks you, shows what
                   to shoot / search for, and lets you drop your own file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..cuts import norm
from . import generate, online
from .library import Library

VERIFY_SCHEMA = {"type": "object", "properties": {"score": {"type": "integer"}, "reason": {"type": "string"}},
                 "required": ["score", "reason"]}


def build_slots(visual_plan: dict, transcript: dict, duration: float, excluded: set[int]) -> list[dict]:
    segs = {s["id"]: s for s in transcript["segments"]}
    slots: list[dict] = []
    for b in sorted(visual_plan.get("broll", []), key=lambda b: segs[b["segment_id"]]["s"]):
        seg = segs.get(b["segment_id"])
        if not seg or seg["id"] in excluded:
            continue
        start = seg["s"]
        anchor = norm(b.get("anchor_words", "")).split()
        if anchor:
            for w in seg["words"]:
                if norm(w["w"]) == anchor[0]:
                    start = w["s"]
                    break
        if start < 2.5:  # keep the opening on your face (the hook)
            continue
        if slots and start < slots[-1]["e"] + 2.0:
            continue
        end = min(duration, start + 3.2)
        slots.append({
            "id": len(slots), "seg_id": seg["id"], "s": round(start, 3), "e": round(end, 3),
            "line": seg["text"], "description": b.get("description", ""), "queries": b.get("queries", [])[:3],
            "shot_suggestion": b.get("shot_suggestion", ""),
            "card_text": (b.get("card_text") or (b.get("queries") or [""])[0]).upper()[:32],
            "status": "pending", "source": "", "file": "",
            "file_start": 0.0, "kind": "", "score": 0.0, "preview": "", "candidates": [], "user_locked": False,
        })
    return slots


class BrollResolver:
    def __init__(self, cfg: dict, job_dir: Path, log: Callable[[str], None] = print, planner=None):
        self.cfg, self.dir, self.log, self.planner = cfg, Path(job_dir), log, planner
        self.mode = cfg.get("broll_mode", "both")
        self.verified = False
        self.preview_dir = self.dir / "broll_previews"
        self.preview_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- main
    def resolve(self, slots: list[dict], on_progress: Callable[[float, str], None] | None = None,
                only: set[int] | None = None) -> list[dict]:
        prog = on_progress or (lambda p, m: None)
        todo = [s for s in slots if not s.get("user_locked") and (only is None or s["id"] in only)
                and s["status"] in ("pending", "needs_input")]
        if self.mode == "off" or not todo:
            for s in todo:
                s["status"] = "skipped"
            return slots

        use_lib = self.mode in ("offline", "both") and self.cfg.get("broll_library") and \
            Path(self.cfg["broll_library"]).exists()
        use_online = self.mode in ("online", "both") and online.available(self.cfg)
        if self.mode in ("online", "both") and not online.has_video_keys(self.cfg):
            self.log("No Pexels/Pixabay key yet - using free openly-licensed photos. Add a free Pexels key in "
                     "Settings for real video clips.")

        # 1) local library (GPU: SigLIP, small)
        if use_lib:
            lib = Library(self.cfg["broll_library"], log=self.log)
            try:
                prog(0.02, "Indexing your B-roll library")
                lib.build(lambda p: prog(0.02 + 0.2 * p, "Indexing your B-roll library"))
                used: set = set()
                th = float(self.cfg.get("broll_library_threshold", 0.05))
                for s in todo:
                    cands = [c for c in lib.search(s["queries"] + [s["description"]], top_k=3, used=used)
                             if c["score"] >= th * 0.5]
                    for c in cands:
                        c["preview"] = self._lib_preview(c)
                    s["candidates"] = cands
            except Exception as e:  # noqa: BLE001
                self.log(f"B-roll library search failed: {e}")
            finally:
                lib.close()

        # 2) online stock
        if use_online:
            for i, s in enumerate(todo):
                prog(0.25 + 0.3 * i / max(1, len(todo)), f"Searching stock footage: {', '.join(s['queries'][:2])}")
                found = online.search(s["queries"], self.cfg, min_dur=s["e"] - s["s"], log=self.log, per_query=3)
                for c in found:
                    c["preview"] = online.download_preview(c, self.preview_dir) or ""
                s["candidates"] = s.get("candidates", []) + found

        # 3) verify with the vision LLM
        if self.cfg.get("broll_verify", True) and self.planner is not None and self.planner.ok:
            self._verify(todo, lambda p: prog(0.55 + 0.3 * p, "Checking B-roll matches with vision model"))

        # 4) choose
        need_ai = []
        for s in todo:
            best = self._choose(s)
            if best:
                self._apply(s, best)
            else:
                act = self.cfg.get("broll_missing_action", "auto")
                if act in ("ai", "auto") and generate.available()[0]:
                    need_ai.append(s)
                elif act in ("card", "auto"):  # decide for me: animated keyword card, never stop and ask
                    make_card(s)
                elif act == "skip":
                    s["status"] = "skipped"
                else:
                    s["status"] = "needs_input"

        # 5) AI stills for the rest (only if allowed)
        if need_ai:
            if self.planner:
                self.planner.unload()  # free VRAM for FLUX
            self.generate_ai(need_ai, lambda p: prog(0.85 + 0.15 * p, "Generating AI B-roll images"))

        # download chosen online clips
        for s in todo:
            if s["status"] == "resolved" and s["source"] in ("pexels", "pixabay", "openverse") and not s.get("file"):
                try:
                    s["file"] = online.download_video(s["_chosen"])
                except Exception as e:  # noqa: BLE001
                    self.log(f"Download failed: {e}")
                    s["status"] = "needs_input"
            s.pop("_chosen", None)
        prog(1.0, "B-roll ready")
        return slots

    # ------------------------------------------------------------- helpers
    def _lib_preview(self, c: dict) -> str:
        from ...core.ffmpeg import extract_frame
        if c["kind"] == "image":
            return c["file"]
        out = self.preview_dir / f"lib_{abs(hash((c['file'], c['file_start']))) % 10**10}.jpg"
        if not out.exists():
            try:
                extract_frame(c["file"], c["file_start"] + 0.5, out)
            except Exception:
                return ""
        return str(out)

    def _verify(self, slots: list[dict], prog: Callable[[float], None]) -> None:
        jobs = [(s, c) for s in slots for c in [c for c in s.get("candidates", []) if c.get("preview")][:6]]
        for i, (s, c) in enumerate(jobs):
            try:
                r = self.planner.llm.chat_json(
                    self.planner.model, "You judge stock footage for a video editor. Reply in JSON.",
                    f"The speaker says: \"{s['line']}\"\nWanted cutaway: {s['description']}\n"
                    f"Rate 0-10 how well this image works as the cutaway (10 = perfect, 5 = loosely related, "
                    f"0 = unrelated).", schema=VERIFY_SCHEMA, images=[c["preview"]], ctx=4096)
                c["vlm"] = int(r.get("score", 0))
                c["vlm_reason"] = r.get("reason", "")[:160]
                self.verified = True
            except Exception as e:  # noqa: BLE001
                self.log(f"Vision check failed: {e}")
                return
            prog((i + 1) / max(1, len(jobs)))

    def _choose(self, s: dict) -> dict | None:
        th = float(self.cfg.get("broll_library_threshold", 0.05))
        ok = []
        for c in s.get("candidates", []):
            if "vlm" in c:
                good = c["vlm"] >= 6
                rank = c["vlm"] + (0.5 if c["source"] == "library" else 0)  # prefer your own footage
            elif self.verified:
                good, rank = False, 0.0  # vision check is on: never use a clip it didn't approve
            elif c["source"] == "library":
                good, rank = c["score"] >= th, 5 + c["score"]
            else:
                good, rank = True, 4.0  # no vision model available: trust API relevance
            if good:
                ok.append((rank, c))
        return max(ok, key=lambda x: x[0])[1] if ok else None

    def _apply(self, s: dict, c: dict) -> None:
        s.update({"status": "resolved", "source": c["source"], "kind": c["kind"], "preview": c.get("preview", ""),
                  "file_start": c.get("file_start", 0.0), "score": c.get("vlm", c.get("score", 0)),
                  "file": c.get("file", ""), "credit": c.get("author", ""), "page": c.get("page", "")})
        if c["source"] in ("pexels", "pixabay", "openverse"):
            s["file"] = ""
            s["_chosen"] = c

    def use_candidate(self, s: dict, idx: int) -> None:
        c = s["candidates"][idx]
        self._apply(s, c)
        if s.get("_chosen"):
            s["file"] = online.download_video(s.pop("_chosen"))
        s["user_locked"] = True

    def generate_ai(self, slots: list[dict], prog: Callable[[float], None] | None = None) -> None:
        gen = generate.ImageGen(log=self.log)
        try:
            for i, s in enumerate(slots):
                out = self.dir / "broll_ai" / f"slot_{s['id']}.jpg"
                gen.generate(generate.broll_prompt(s["description"] or s["line"]), out, seed=s["id"])
                s.update({"status": "resolved", "source": "ai", "kind": "image", "file": str(out),
                          "preview": str(out), "file_start": 0.0, "score": 0})
                if prog:
                    prog((i + 1) / len(slots))
        except Exception as e:  # noqa: BLE001
            self.log(f"AI image generation failed: {e}")
            for s in slots:
                if s["status"] != "resolved":
                    s["status"] = "needs_input"
        finally:
            gen.close()


def make_card(slot: dict, text: str | None = None) -> None:
    """Animated text card: always relevant, rendered on the fly (no footage needed)."""
    slot.update({"status": "resolved", "source": "card", "kind": "graphic", "file": "", "preview": "",
                 "card_text": (text or slot.get("card_text") or "").upper()[:32] or "WATCH THIS",
                 "file_start": 0.0, "user_locked": True})


def set_user_file(slot: dict, path: str, file_start: float = 0.0) -> None:
    from .library import IMAGE_EXT
    kind = "image" if Path(path).suffix.lower() in IMAGE_EXT else "video"
    slot.update({"status": "resolved", "source": "user", "kind": kind, "file": path, "file_start": file_start,
                 "preview": path if kind == "image" else "", "user_locked": True})
