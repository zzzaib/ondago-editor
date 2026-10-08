"""ONLINE B-roll: Pexels + Pixabay free stock-video APIs (free keys, pasted once in Settings)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

import requests

from ...core.paths import CACHE

DL_DIR = CACHE / "broll_online"
DL_DIR.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "OnDaGoEditor/1.0"}


def _pick_file(files: list[dict], target_w: int = 1920) -> dict | None:
    mp4 = [f for f in files if (f.get("file_type") or "video/mp4").endswith("mp4") and f.get("width")]
    if not mp4:
        return None
    good = [f for f in mp4 if f["width"] >= 1280] or mp4
    return min(good, key=lambda f: abs(f["width"] - target_w))


def search_pexels(query: str, key: str, min_dur: float, orientation: str = "landscape", n: int = 6) -> list[dict]:
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key, **UA},
                     params={"query": query, "per_page": n, "orientation": orientation, "size": "medium"}, timeout=20)
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        f = _pick_file(v.get("video_files", []))
        if not f or v.get("duration", 0) < min_dur:
            continue
        out.append({"source": "pexels", "id": f"pexels_{v['id']}", "url": f["link"], "preview_url": v.get("image"),
                    "page": v.get("url"), "author": (v.get("user") or {}).get("name", ""), "kind": "video",
                    "duration": v.get("duration", 0), "query": query, "file_start": 0.0})
    return out


def search_pixabay(query: str, key: str, min_dur: float, n: int = 6) -> list[dict]:
    r = requests.get("https://pixabay.com/api/videos/", headers=UA,
                     params={"key": key, "q": query[:100], "per_page": max(3, n), "safesearch": "true"}, timeout=20)
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        vids = h.get("videos", {})
        f = next((vids[k] for k in ("large", "medium", "small") if vids.get(k, {}).get("url")), None)
        if not f or h.get("duration", 0) < min_dur:
            continue
        out.append({"source": "pixabay", "id": f"pixabay_{h['id']}", "url": f["url"],
                    "preview_url": f.get("thumbnail") or (vids.get("medium") or {}).get("thumbnail"),
                    "page": h.get("pageURL"), "author": h.get("user", ""), "kind": "video",
                    "duration": h.get("duration", 0), "query": query, "file_start": 0.0})
    return out


def search(queries: list[str], cfg: dict, min_dur: float, log: Callable[[str], None] = print,
           per_query: int = 3) -> list[dict]:
    results: list[dict] = []
    seen = set()
    for q in queries[:3]:
        for name, fn in (("pexels", lambda q: search_pexels(q, cfg["pexels_key"], min_dur, n=per_query + 2)),
                         ("pixabay", lambda q: search_pixabay(q, cfg["pixabay_key"], min_dur, n=per_query + 2))):
            if not cfg.get(f"{name}_key"):
                continue
            try:
                for c in fn(q)[:per_query]:
                    if c["id"] not in seen:
                        seen.add(c["id"])
                        results.append(c)
            except Exception as e:  # noqa: BLE001
                log(f"{name} search failed for '{q}': {e}")
    return results


def _download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, headers=UA, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    tmp.replace(dest)
    return dest


def download_preview(c: dict, folder: Path) -> str | None:
    if not c.get("preview_url"):
        return None
    name = hashlib.md5(c["preview_url"].encode()).hexdigest()[:16] + ".jpg"
    try:
        return str(_download(c["preview_url"], folder / name))
    except Exception:
        return None


def download_video(c: dict) -> str:
    return str(_download(c["url"], DL_DIR / f"{c['id']}.mp4"))


def available(cfg: dict) -> bool:
    return bool(cfg.get("pexels_key") or cfg.get("pixabay_key"))
