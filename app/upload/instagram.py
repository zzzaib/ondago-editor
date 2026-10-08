"""Instagram Reels publishing via the official Graph API, uploading the LOCAL file with
Meta's resumable upload (rupload.facebook.com) -> no public hosting needed.

Needs (Settings): Instagram professional account user id + long-lived access token.
Tokens starting with 'IG' use graph.instagram.com (Instagram Login); others graph.facebook.com."""
from __future__ import annotations

import os
import time
from typing import Callable

import requests


def _host(token: str) -> str:
    return "https://graph.instagram.com" if token.startswith("IG") else "https://graph.facebook.com"


def check(user_id: str, token: str, version: str = "v23.0") -> str:
    r = requests.get(f"{_host(token)}/{version}/{user_id}", params={"fields": "username", "access_token": token},
                     timeout=20)
    r.raise_for_status()
    return r.json().get("username", user_id)


def upload_reel(video: str, caption: str, user_id: str, token: str, version: str = "v23.0",
                share_to_feed: bool = True, thumb_offset_ms: int | None = None,
                on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] = print) -> str:
    host = _host(token)
    prog = on_progress or (lambda p: None)
    params = {"media_type": "REELS", "upload_type": "resumable", "caption": caption,
              "share_to_feed": "true" if share_to_feed else "false", "access_token": token}
    if thumb_offset_ms is not None:
        params["thumb_offset"] = str(int(thumb_offset_ms))
    r = requests.post(f"{host}/{version}/{user_id}/media", data=params, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Instagram container failed: {r.text}")
    cid = r.json()["id"]
    upload_uri = r.json().get("uri") or f"https://rupload.facebook.com/ig-api-upload/{version}/{cid}"
    size = os.path.getsize(video)
    log(f"Uploading reel ({size / 1e6:.1f} MB)")
    prog(0.05)
    with open(video, "rb") as fh:
        up = requests.post(upload_uri, data=fh, timeout=1800,
                           headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
    if not up.ok:
        raise RuntimeError(f"Instagram upload failed: {up.text}")
    prog(0.6)
    # wait for processing
    for i in range(120):
        s = requests.get(f"{host}/{version}/{cid}", params={"fields": "status_code,status", "access_token": token},
                         timeout=30).json()
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram processing failed: {s}")
        prog(0.6 + 0.3 * min(1.0, i / 40))
        time.sleep(5)
    else:
        raise RuntimeError("Instagram processing timed out")
    pub = requests.post(f"{host}/{version}/{user_id}/media_publish",
                        data={"creation_id": cid, "access_token": token}, timeout=60)
    if not pub.ok:
        raise RuntimeError(f"Instagram publish failed: {pub.text}")
    mid = pub.json()["id"]
    link = ""
    try:
        link = requests.get(f"{host}/{version}/{mid}", params={"fields": "permalink", "access_token": token},
                            timeout=20).json().get("permalink", "")
    except Exception:
        pass
    prog(1.0)
    log(f"Instagram reel published: {link or mid}")
    return link or mid
