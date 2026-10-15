"""One-tap edit styles. Each preset is just a bundle of normal settings, so 'Advanced options' can still
change any single thing afterwards (the preset then shows as 'Custom')."""
from __future__ import annotations

PRESETS: list[dict] = [
    {"id": "talking", "emoji": "🎙️", "title": "Talking head",
     "desc": "Clean cuts, captions, B-roll, doodles and split-screen moments. Best for most videos.",
     "options": {"remove_fillers": True, "remove_silences": True, "remove_retakes": True, "captions": True,
                 "caption_style": "bold_pop", "zoom_style": "jumpcut", "smooth_zoom": True, "callouts": True,
                 "progress_bar": True, "sfx": True, "color_grade": True, "music": False, "broll_mode": "both",
                 "broll_missing_action": "auto", "broll_layout": "auto", "doodles": True, "doodle_density": "medium",
                 "doodle_outline": True, "callout_doodles": True, "vox_graphics": False}},
    {"id": "energetic", "emoji": "⚡", "title": "Energetic Reel",
     "desc": "Fast punch-ins, lots of doodles, sound effects and music. Made for Reels and Shorts.",
     "options": {"remove_fillers": True, "remove_silences": True, "remove_retakes": True, "captions": True,
                 "caption_style": "bold_pop", "zoom_style": "jumpcut", "smooth_zoom": True, "callouts": True,
                 "progress_bar": True, "sfx": True, "color_grade": True, "music": True, "broll_mode": "both",
                 "broll_missing_action": "auto", "broll_layout": "auto", "doodles": True, "doodle_density": "high",
                 "doodle_outline": True, "callout_doodles": True, "max_pause": 0.35}},
    {"id": "explainer", "emoji": "📰", "title": "Vox explainer",
     "desc": "Explainer graphics on paper: title cards, highlighted quotes, counting numbers, maps and timelines "
             "in a 12 fps cut-out style.",
     "options": {"remove_fillers": True, "remove_silences": True, "remove_retakes": True, "captions": True,
                 "caption_style": "clean", "zoom_style": "emphasis", "smooth_zoom": True, "callouts": False,
                 "progress_bar": False, "sfx": True, "color_grade": True, "music": False, "broll_mode": "both",
                 "broll_missing_action": "auto", "broll_layout": "auto", "doodles": False,
                 "doodle_density": "low", "doodle_outline": False, "callout_doodles": False, "vox_graphics": True,
                 "vox_density": "medium"}},
    {"id": "tutorial", "emoji": "💻", "title": "Tutorial / screen",
     "desc": "Calm and clear: clean captions, gentle zooms, few effects. Good for screen recordings.",
     "options": {"remove_fillers": True, "remove_silences": True, "remove_retakes": True, "captions": True,
                 "caption_style": "clean", "zoom_style": "emphasis", "smooth_zoom": False, "callouts": True,
                 "progress_bar": True, "sfx": False, "color_grade": False, "music": False, "broll_mode": "off",
                 "broll_missing_action": "skip", "broll_layout": "full", "doodles": True, "doodle_density": "low",
                 "doodle_outline": False, "callout_doodles": True, "vox_graphics": False}},
    {"id": "minimal", "emoji": "🤍", "title": "Minimal",
     "desc": "Just a clean edit: dead air removed, simple captions, no extras.",
     "options": {"remove_fillers": True, "remove_silences": True, "remove_retakes": True, "captions": True,
                 "caption_style": "minimal", "zoom_style": "off", "smooth_zoom": False, "callouts": False,
                 "progress_bar": False, "sfx": False, "color_grade": True, "music": False, "broll_mode": "off",
                 "broll_missing_action": "skip", "broll_layout": "full", "doodles": False, "doodle_density": "low",
                 "doodle_outline": False, "callout_doodles": False, "vox_graphics": False}},
]

BY_ID = {p["id"]: p for p in PRESETS}


def apply(cfg: dict, preset_id: str) -> dict:
    p = BY_ID.get(preset_id)
    return {**cfg, **(p["options"] if p else {}), "preset": preset_id if p else "custom"}


def detect(cfg: dict) -> str:
    """Which preset do these settings match? ('custom' if any preset option differs)."""
    for p in PRESETS:
        if all(cfg.get(k) == v for k, v in p["options"].items()):
            return p["id"]
    return "custom"
