"""YouTube Data API v3 upload (resumable) + custom thumbnail.

One-time setup: Settings -> 'Connect YouTube' asks for an OAuth *Desktop app* client_secret.json
(Google Cloud Console). The browser consent happens once; the token is stored in data/youtube_token.json."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..core.paths import DATA

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
TOKEN = DATA / "youtube_token.json"


def _creds(client_secret: str | None = None, interactive: bool = True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:
            creds = None
    if not interactive:
        raise RuntimeError("YouTube is not connected. Open Settings -> Connect YouTube.")
    if not client_secret or not Path(client_secret).exists():
        raise RuntimeError("Select your Google OAuth client_secret.json in Settings first.")
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", open_browser=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


def connect(client_secret: str) -> str:
    from googleapiclient.discovery import build
    yt = build("youtube", "v3", credentials=_creds(client_secret, True), cache_discovery=False)
    ch = yt.channels().list(part="snippet", mine=True).execute()
    items = ch.get("items", [])
    return items[0]["snippet"]["title"] if items else "(no channel)"


def connected() -> bool:
    return TOKEN.exists()


def upload(video: str, title: str, description: str, tags: list[str], privacy: str = "private",
           category_id: str = "22", thumbnail: str | None = None, publish_at: str | None = None,
           on_progress: Callable[[float], None] | None = None, log: Callable[[str], None] = print) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    yt = build("youtube", "v3", credentials=_creds(interactive=False), cache_discovery=False)
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if publish_at:
        status.update({"privacyStatus": "private", "publishAt": publish_at})
    body = {
        "snippet": {"title": title[:100], "description": description[:4900],
                    "tags": [t[:30] for t in tags][:30], "categoryId": str(category_id)},
        "status": status,
    }
    media = MediaFileUpload(video, chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        st, resp = req.next_chunk(num_retries=5)
        if st and on_progress:
            on_progress(st.progress())
    vid = resp["id"]
    log(f"YouTube upload complete: https://youtu.be/{vid}")
    if thumbnail and Path(thumbnail).exists():
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumbnail, mimetype="image/jpeg")).execute()
            log("Custom thumbnail set")
        except HttpError as e:
            log(f"Thumbnail not set (channel may need phone verification for custom thumbnails): {e}")
    if on_progress:
        on_progress(1.0)
    return f"https://youtu.be/{vid}"
