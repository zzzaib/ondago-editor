"""OnDaGo Remote: edit from your phone (Android / iPhone / any browser) while the laptop does the work.

The desktop app (or `python -m app.server`) runs this FastAPI server in a background thread. The phone gets a
small installable web app (PWA) from it. Everything heavy runs on the laptop through the shared task queue."""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..core import settings
from ..core.job import Job
from ..core.paths import CACHE, DATA, JOBS
from ..core.tasks import MANAGER, TaskInfo
from ..version import VERSION
from . import auth, net

STATIC = Path(__file__).parent / "static"
UPLOADS = DATA / "uploads"
PARTIAL = UPLOADS / ".partial"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
MEDIA_EXT = VIDEO_EXT | {".jpg", ".jpeg", ".png", ".webp"}
MAX_CHUNK = 16 * 1024 * 1024
COOKIE = "ondago_token"

# settings the phone may change per edit (and save as defaults)
OPTION_KEYS = ["language", "remove_fillers", "remove_silences", "remove_retakes", "captions", "caption_style",
               "caption_script", "broll_mode", "broll_missing_action", "zoom_style", "rotation", "callouts",
               "progress_bar", "smooth_zoom", "sfx", "color_grade", "music", "output_youtube", "output_reel",
               "reel_mode", "review_before_render", "doodles", "doodle_density", "doodle_outline", "doodle_ai",
               "callout_doodles", "broll_layout", "max_pause", "preset"]


def _safe_job(name: str) -> Job:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,120}", name or ""):
        raise HTTPException(404, "no such project")
    d = JOBS / name
    if not (d / "plan.json").exists():
        raise HTTPException(404, "no such project")
    return Job(d)


def _inside(p: Path, roots: list[Path]) -> bool:
    try:
        rp = p.resolve()
    except Exception:  # noqa: BLE001
        return False
    for r in roots:
        try:
            rp.relative_to(r.resolve())
            return True
        except ValueError:
            continue
    return False


def _folders(cfg: dict) -> list[Path]:
    roots = [Path(p) for p in cfg.get("remote_folders") or [] if p]
    if not roots:
        home = Path.home()
        roots = [home / n for n in ("Videos", "Desktop", "Downloads", "Pictures")]
        od = os.environ.get("OneDrive")
        if od:
            roots.append(Path(od))
    return [r for r in roots if r.exists()]


def instance_id() -> str:
    s = settings.load()
    if not s.get("remote_instance_id"):
        settings.update(remote_instance_id=secrets.token_hex(6))
        s = settings.load()
    return s["remote_instance_id"]


def _route(req: Request) -> tuple[str, str]:
    """How did this request reach us? lan | tailscale | public | local  (+ the client's IP)."""
    import ipaddress
    peer = req.client.host if req.client else ""
    ip = _client_ip(req)
    if req.headers.get("cf-connecting-ip"):
        return "public", ip
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "public", ip
    if a in ipaddress.ip_network("100.64.0.0/10"):
        return "tailscale", ip
    if a.is_loopback:
        return "local", ip
    if a.is_private or a.is_link_local:
        return "lan", ip
    return ("public" if peer in ("127.0.0.1", "::1") else "lan"), ip


_PUB: dict = {"ip": "", "t": 0.0}


def _public_ip() -> str:
    """The laptop's internet address (cached) - if the phone shows the same one, they share a router."""
    if time.time() - _PUB["t"] < 600:
        return _PUB["ip"]
    ip = ""
    try:
        import requests
        txt = requests.get("https://1.1.1.1/cdn-cgi/trace", timeout=5).text
        ip = next((ln.split("=", 1)[1].strip() for ln in txt.splitlines() if ln.startswith("ip=")), "")
    except Exception:  # noqa: BLE001
        pass
    _PUB.update(ip=ip, t=time.time())
    return ip


def _client_ip(req: Request) -> str:
    ip = req.client.host if req.client else "?"
    if ip in ("127.0.0.1", "::1"):  # behind cloudflared / tailscale serve
        ip = req.headers.get("cf-connecting-ip") or req.headers.get("x-forwarded-for", ip).split(",")[0].strip()
    return ip


# =================================================================== server
class RemoteServer:
    def __init__(self, log: Callable[[str], None] = print):
        self.log = log
        self.thread: threading.Thread | None = None
        self.uv = None
        self.tunnel: net.Tunnel | None = None
        self.awake = net.KeepAwake()
        self.port = 8765
        self.public_url = ""
        self.ts_https = ""
        self.error = ""
        self.started = 0.0

    # ------------------------------------------------------------ lifecycle
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self) -> tuple[bool, str]:
        if self.running():
            return True, "already running"
        cfg = settings.load()
        if not cfg.get("remote_password_hash"):
            return False, "Set a phone-app password first."
        settings.ensure_secret()
        import uvicorn
        self.port = int(cfg.get("remote_port", 8765))
        host = "127.0.0.1" if cfg.get("remote_bind") == "local" else "0.0.0.0"
        try:  # fail fast with a clear message if the port is taken
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if os.name != "nt":  # on Windows SO_REUSEADDR would let us bind a port that's already taken
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, self.port))
        except OSError:
            return False, f"Port {self.port} is already in use (is OnDaGo already running?)."
        app = create_app(self)
        config = uvicorn.Config(app, host=host, port=self.port, log_level="warning", access_log=False, log_config=None,
                                timeout_keep_alive=30, proxy_headers=False, lifespan="off")
        self.uv = uvicorn.Server(config)
        self.uv.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self.thread = threading.Thread(target=self.uv.run, name="ondago-remote", daemon=True)
        self.thread.start()
        self.started = time.time()
        if cfg.get("keep_awake", True):
            self.awake.start()
        mode = cfg.get("remote_tunnel", "off")
        if mode in ("quick", "named"):
            if mode == "named" and not cfg.get("cloudflare_token"):
                self.log("Public link: 'named' mode needs a Cloudflare tunnel token in Settings.")
            else:
                self.tunnel = net.Tunnel(self.port, mode, cfg.get("cloudflare_token", ""), cfg.get("public_url", ""),
                                         self.log, self._on_public_url)
                self.tunnel.start()
        if cfg.get("tailscale_https"):
            threading.Thread(target=self._ts_serve, daemon=True).start()
        self.log(f"Phone app running on port {self.port}")
        return True, "started"

    def stop(self):
        if self.tunnel:
            self.tunnel.stop()
            self.tunnel = None
        self.public_url = ""
        self.awake.stop()
        if self.uv:
            self.uv.should_exit = True
        if self.thread:
            self.thread.join(timeout=5)
        self.thread = None
        self.uv = None

    def _ts_serve(self):
        ok, msg = net.tailscale_serve(self.port, self.log)
        self.ts_https = msg if ok else ""
        if not ok:
            self.error = f"Tailscale HTTPS: {msg}"

    def _on_public_url(self, url: str):
        self.public_url = url
        cfg = settings.load()
        net.notify(cfg, "OnDaGo is online", f"Open your editor: {url}", click=url, tags="link")

    # ------------------------------------------------------------ info
    def urls(self) -> dict:
        cfg = settings.load()
        port = self.port
        ts = net.tailscale_info()
        out = {"local": f"http://127.0.0.1:{port}", "lan": [f"http://{ip}:{port}" for ip in net.lan_ips()],
               "tailscale": f"http://{ts['ip']}:{port}" if ts.get("ip") else "",
               "tailscale_name": f"http://{ts['dns'].split('.')[0]}:{port}" if ts.get("dns") else "",
               "tailscale_https": self.ts_https, "public": self.public_url or (
                   cfg.get("public_url", "") if cfg.get("remote_tunnel") == "named" else ""),
               "tailscale_installed": ts.get("installed", False), "tailscale_running": ts.get("running", False)}
        if cfg.get("remote_bind") == "local":
            out["lan"], out["tailscale"], out["tailscale_name"] = [], "", ""
        return out

    def best_url(self) -> str:
        u = self.urls()
        return u["public"] or u["tailscale_https"] or u["tailscale"] or (u["lan"][0] if u["lan"] else u["local"])

    def job_url(self, name: str) -> str:
        return f"{self.best_url()}/#/job/{name}"


SERVER: RemoteServer | None = None


def get_server(log: Callable[[str], None] = print) -> RemoteServer:
    global SERVER
    if SERVER is None:
        SERVER = RemoteServer(log)
    return SERVER


# ============================================================ serialisers
def _media_url(job: Job, path: str | None, kind: str, key: Any = "") -> str:
    if not path or not Path(path).exists():
        return ""
    v = int(Path(path).stat().st_mtime)
    return f"/media/{job.name}/{kind}/{key}?v={v}" if key != "" else f"/media/{job.name}/{kind}?v={v}"


def job_summary(job: Job) -> dict:
    p = job.plan
    t = MANAGER.for_job(job.name)
    th = p.get("thumbnails", {})
    opts = th.get("options", [])
    thumb = ""
    if opts:
        i = min(th.get("choice", 0), len(opts) - 1)
        thumb = _media_url(job, opts[i].get("path"), "thumb", i)
    if not thumb:
        from ..core import covers
        thumb = _media_url(job, str(covers.cover_path(job.dir)), "cover")
    need = sum(1 for s in p.get("broll", []) if s.get("status") == "needs_input")
    return {"name": job.name, "title": Path(p.get("source", "")).name, "stage": p.get("stage", "new"),
            "created": p.get("created", 0), "thumb": thumb, "need_broll": need,
            "duration": p.get("media", {}).get("duration", 0), "edit_duration": p.get("edit_duration", 0),
            "outputs": sorted(p.get("outputs", {})), "published": p.get("published", {}),
            "task": t.public() if t else None}


def job_detail(job: Job) -> dict:
    from ..pipeline import runner
    p = job.plan
    d = job_summary(job)
    slots = []
    for s in p.get("broll", []):
        cands = [{"i": i, "source": c.get("source", ""), "vlm": c.get("vlm"), "reason": c.get("vlm_reason", ""),
                  "preview": _media_url(job, c.get("preview"), "cand", f"{s['id']}/{i}")}
                 for i, c in enumerate(s.get("candidates", [])[:8])]
        slots.append({k: s.get(k) for k in ("id", "s", "e", "line", "description", "shot_suggestion", "queries",
                                             "card_text", "status", "source", "kind", "credit")} |
                     {"preview": _media_url(job, s.get("preview"), "slot", s["id"]), "candidates": cands})
    m = p.get("metadata", {})
    th = p.get("thumbnails", {})
    outs = {k: {"preview": f"/media/{job.name}/out/{k}?q=preview", "full": f"/media/{job.name}/out/{k}?q=full",
                "size": Path(v).stat().st_size if Path(v).exists() else 0}
            for k, v in p.get("outputs", {}).items()}
    cuts = [c for c in p.get("cuts", []) if c.get("enabled", True)]
    d.update({
        "media": p.get("media", {}), "language": p.get("language", ""), "broll": slots,
        "doodles": [{k: x.get(k) for k in ("id", "s", "kind", "shape", "enabled", "line", "word", "prompt")}
                    for x in p.get("doodles", [])],
        "outlines": p.get("outlines", []),
        "callouts": [{"i": i, "s": c["s"], "text": c["text"], "enabled": c.get("enabled", True)}
                     for i, c in enumerate(p.get("callouts", []))],
        "cuts": {"count": len(cuts), "seconds": round(sum(c["e"] - c["s"] for c in cuts), 1)},
        "thumbnails": [{"i": i, "url": _media_url(job, o.get("path"), "thumb", i)}
                       for i, o in enumerate(th.get("options", []))],
        "thumb_choice": th.get("choice", 0), "thumb_texts": m.get("thumbnail_texts", []),
        "metadata": {"title": m.get("title", ""), "titles": m.get("titles", []),
                     "description": runner.youtube_description(p) if p.get("metadata") else "",
                     "tags": m.get("tags", []), "instagram_caption": runner.instagram_caption(p) if p.get(
                         "metadata") else "", "publish_at": m.get("publish_at", "")},
        "outputs_detail": outs, "export_dir": p.get("export_dir", ""), "reel_range": p.get("reel_range"),
        "log": list(MANAGER.for_job(job.name).log)[-60:] if MANAGER.for_job(job.name) else [],
    })
    return d


# ================================================================== app
def create_app(server: RemoteServer) -> FastAPI:
    app = FastAPI(title="OnDaGo Remote", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)

    def authed(req: Request) -> bool:
        tok = req.cookies.get(COOKIE)
        if not tok and req.method in ("GET", "HEAD"):
            tok = req.query_params.get("t")  # <video src> on the fast local address can't send headers
        h = req.headers.get("authorization", "")
        if h.lower().startswith("bearer "):
            tok = h[7:]
        if not auth.valid_token(tok):
            raise HTTPException(401, "login required")
        if req.method not in ("GET", "HEAD") and req.headers.get("x-ondago") != "1":
            raise HTTPException(403, "missing header")  # CSRF guard: browsers can't add it cross-site
        return True

    def _login_response(req: Request) -> JSONResponse:
        tok = auth.make_token()
        r = JSONResponse({"ok": True, "token": tok})
        https = req.url.scheme == "https" or req.headers.get("x-forwarded-proto") == "https"
        r.set_cookie(COOKIE, tok, max_age=auth.TOKEN_DAYS * 86400, httponly=True, samesite="lax", secure=https)
        return r

    fast_paths = ("/api/ping", "/api/uploads", "/media/")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        cors = bool(origin) and request.url.path.startswith(fast_paths)
        if cors and request.method == "OPTIONS":  # preflight from the phone app's other address
            resp = Response(status_code=204)
        else:
            resp = await call_next(request)
        if cors:  # bearer-token only (no cookies), so allowing the calling origin is safe
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "GET, HEAD, PUT, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "authorization, content-type, x-ondago, range"
            resp.headers["Access-Control-Expose-Headers"] = "content-range, content-length, accept-ranges"
            resp.headers["Access-Control-Max-Age"] = "600"
            if request.headers.get("access-control-request-private-network"):
                resp.headers["Access-Control-Allow-Private-Network"] = "true"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    # ---------------------------------------------------------- static
    @app.get("/", response_class=HTMLResponse)
    def index():
        # cache-bust app.js/app.css so phones always pick up a new version right away
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for f in ("app.js", "app.css"):
            v = int((STATIC / f).stat().st_mtime)
            html = html.replace(f"/static/{f}", f"/static/{f}?v={v}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js")
    def sw():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript")

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/static/{name}")
    def static(name: str):
        f = STATIC / name
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name) or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "public, max-age=3600"})

    # ---------------------------------------------------------- auth
    @app.post("/api/login")
    async def login(req: Request):
        ip = _client_ip(req)
        if auth.throttled(ip):
            raise HTTPException(429, "Too many attempts. Try again in 10 minutes.")
        body = await req.json()
        stored = settings.load().get("remote_password_hash", "")
        ok = await run_in_threadpool(auth.check_password, str(body.get("password", "")), stored)
        if not ok:
            auth.failed(ip)
            await asyncio.sleep(1.0)
            raise HTTPException(401, "Wrong password")
        return _login_response(req)

    @app.post("/api/pair")
    async def pair(req: Request):
        ip = _client_ip(req)
        if auth.throttled(ip):
            raise HTTPException(429, "Too many attempts. Try again in 10 minutes.")
        body = await req.json()
        if not auth.use_pair_code(str(body.get("code", ""))):
            auth.failed(ip)
            raise HTTPException(401, "This pairing code expired. Show a new QR code on the laptop.")
        return _login_response(req)

    @app.post("/api/logout")
    def logout():
        r = JSONResponse({"ok": True})
        r.delete_cookie(COOKIE)
        return r

    @app.get("/api/ping")
    def ping():
        return {"app": "ondago", "id": instance_id()}

    @app.get("/api/lan", dependencies=[Depends(authed)])
    async def lan(req: Request):
        """Same Wi-Fi as the laptop? Then the phone can talk to it directly (fast, no internet data)."""
        via, ip = _route(req)
        same = via == "lan"
        if via == "public":
            mine = await run_in_threadpool(_public_ip)
            same = bool(mine) and mine == ip
        urls = server.urls()
        return {"via": via, "same_network": same, "lan": urls["lan"], "id": instance_id(),
                "token": auth.make_token(), "code": auth.new_pair_code(3) if same else ""}

    @app.get("/api/me")
    def me(req: Request):
        try:
            authed(req)
            return {"ok": True}
        except HTTPException:
            return JSONResponse({"ok": False}, status_code=200)

    # ---------------------------------------------------------- status / options
    @app.get("/api/status", dependencies=[Depends(authed)])
    def status():
        from ..pipeline.broll import online
        from ..upload import youtube
        cfg = settings.load()
        ai_ok, ai_why = _ai_status()
        return {"version": VERSION, "device": socket.gethostname(), "gpu": _gpu_name(), "busy": MANAGER.busy(),
                "ai_images": ai_ok, "ai_reason": ai_why, "youtube_connected": youtube.connected(),
                "instagram_ready": bool(cfg.get("instagram_user_id") and cfg.get("instagram_access_token")),
                "online_broll": online.available(cfg), "video_keys": online.has_video_keys(cfg),
                "urls": server.urls(), "tasks": MANAGER.snapshot(20)}

    @app.put("/api/keys", dependencies=[Depends(authed)])
    async def save_keys(req: Request):
        """Free stock-footage API keys can be pasted from the phone (write-only: never sent back)."""
        b = await _json(req)
        vals = {k: str(b[k]).strip()[:200] for k in ("pexels_key", "pixabay_key") if str(b.get(k, "")).strip()}
        if vals:
            settings.update(**vals)
        return {"ok": True, "saved": sorted(vals)}

    @app.get("/api/presets", dependencies=[Depends(authed)])
    def presets():
        from ..core import presets as pr
        return {"presets": pr.PRESETS, "current": pr.detect(settings.load())}

    @app.get("/api/options", dependencies=[Depends(authed)])
    def options():
        cfg = settings.load()
        return {k: cfg.get(k) for k in OPTION_KEYS}

    @app.put("/api/options", dependencies=[Depends(authed)])
    async def save_options(req: Request):
        body = await req.json()
        settings.update(**{k: v for k, v in body.items() if k in OPTION_KEYS})
        return {"ok": True}

    # ---------------------------------------------------------- folders
    @app.get("/api/folders", dependencies=[Depends(authed)])
    def folders():
        return [{"i": i, "name": r.name or str(r), "path": str(r)} for i, r in enumerate(_folders(settings.load()))]

    @app.get("/api/browse", dependencies=[Depends(authed)])
    def browse(root: int, path: str = ""):
        roots = _folders(settings.load())
        if not 0 <= root < len(roots):
            raise HTTPException(404)
        base = roots[root]
        d = (base / path) if path else base
        if not _inside(d, [base]) or not d.is_dir():
            raise HTTPException(404)
        dirs, files = [], []
        try:
            for e in sorted(d.iterdir(), key=lambda e: e.name.lower()):
                if e.name.startswith(".") or e.name.endswith("_OnDaGo"):
                    continue
                try:
                    if e.is_dir():
                        dirs.append({"name": e.name})
                    elif e.suffix.lower() in VIDEO_EXT:
                        st = e.stat()
                        files.append({"name": e.name, "size": st.st_size, "mtime": st.st_mtime})
                except OSError:
                    continue
        except PermissionError:
            raise HTTPException(403, "no access") from None
        files.sort(key=lambda f: -f["mtime"])
        rel = str(d.resolve().relative_to(base.resolve())).replace("\\", "/")
        return {"root": root, "path": "" if rel == "." else rel, "dirs": dirs, "files": files[:300]}

    # ---------------------------------------------------------- uploads (chunked, resumable)
    @app.post("/api/uploads", dependencies=[Depends(authed)])
    async def upload_init(req: Request):
        b = await req.json()
        name = re.sub(r"[^A-Za-z0-9._ \-]+", "_", Path(str(b.get("name", "video.mp4"))).name)[:80] or "video.mp4"
        size = int(b.get("size", 0))
        if Path(name).suffix.lower() not in MEDIA_EXT or size <= 0:
            raise HTTPException(400, "Only video or image files.")
        free = shutil.disk_usage(DATA).free
        if size > free - 2 * 1024 ** 3:
            raise HTTPException(507, "Not enough free disk space on the laptop.")
        key = re.sub(r"[^A-Za-z0-9]", "", str(b.get("key", "")))[:64]
        PARTIAL.mkdir(parents=True, exist_ok=True)
        uid = key or secrets.token_hex(8)
        meta = PARTIAL / f"{uid}.json"
        part = PARTIAL / f"{uid}.part"
        if meta.exists():
            m = json.loads(meta.read_text())
            if m.get("size") == size and m.get("name") == name:
                if m.get("done") and Path(m.get("path", "")).exists():
                    return {"id": uid, "received": size, "done": True}
                return {"id": uid, "received": part.stat().st_size if part.exists() else 0}
        meta.write_text(json.dumps({"name": name, "size": size, "created": time.time()}))
        part.write_bytes(b"")
        return {"id": uid, "received": 0}

    def _upload_meta(uid: str) -> tuple[dict, Path, Path]:
        if not re.fullmatch(r"[A-Za-z0-9]{1,64}", uid):
            raise HTTPException(404)
        meta = PARTIAL / f"{uid}.json"
        if not meta.exists():
            raise HTTPException(404, "unknown upload")
        return json.loads(meta.read_text()), meta, PARTIAL / f"{uid}.part"

    @app.put("/api/uploads/{uid}", dependencies=[Depends(authed)])
    async def upload_chunk(uid: str, req: Request, offset: int = 0):
        m, meta, part = _upload_meta(uid)
        if m.get("done"):
            return JSONResponse({"received": m["size"]}, status_code=409)
        have = part.stat().st_size if part.exists() else 0
        if offset != have:
            return JSONResponse({"received": have}, status_code=409)
        t0 = time.time()
        data = await req.body()
        secs = max(0.001, time.time() - t0)
        if len(data) > MAX_CHUNK:
            raise HTTPException(413, "chunk too big")
        if have + len(data) > m["size"]:
            raise HTTPException(400, "more data than announced")

        def write():
            with open(part, "ab") as fh:
                fh.write(data)
        await run_in_threadpool(write)
        via, ip = _route(req)
        server.log(f"Phone upload {m['name']}: {(have + len(data)) / 1e6:.1f} / {m['size'] / 1e6:.1f} MB  "
                   f"(+{len(data) / 1e6:.1f} MB in {secs:.1f}s = {len(data) / 1e6 / secs:.1f} MB/s, {via} {ip})")
        return {"received": have + len(data)}

    @app.post("/api/uploads/{uid}/complete", dependencies=[Depends(authed)])
    def upload_done(uid: str):
        m, meta, part = _upload_meta(uid)
        if m.get("done") and Path(m.get("path", "")).exists():
            return {"id": uid, "path_name": Path(m["path"]).name}
        if not part.exists() or part.stat().st_size != m["size"]:
            raise HTTPException(400, "upload incomplete")
        dst = UPLOADS / f"{time.strftime('%Y%m%d_%H%M%S')}_{uid[:6]}" / m["name"]  # keeps the original file name
        dst.parent.mkdir(parents=True, exist_ok=True)
        part.replace(dst)
        m.update({"done": True, "path": str(dst)})
        meta.write_text(json.dumps(m))
        return {"id": uid, "path_name": dst.name}

    def _uploaded(uid: str) -> Path:
        m, _, _ = _upload_meta(uid)
        p = Path(m.get("path", ""))
        if not m.get("done") or not p.exists():
            raise HTTPException(400, "upload not finished")
        return p

    # ---------------------------------------------------------- jobs
    @app.get("/api/jobs", dependencies=[Depends(authed)])
    def jobs():
        from ..core import covers
        out = [job_summary(j) for j in Job.list_recent(40)]
        covers.make_in_background([str(JOBS / d["name"]) for d in out if not d["thumb"]])
        return out

    @app.post("/api/jobs", dependencies=[Depends(authed)])
    async def new_job(req: Request):
        from .. import actions
        b = await req.json()
        src = b.get("source", {})
        if src.get("upload"):
            path = _uploaded(str(src["upload"]))
        else:
            roots = _folders(settings.load())
            r = int(src.get("root", -1))
            if not 0 <= r < len(roots):
                raise HTTPException(400, "pick a video")
            path = roots[r] / str(src.get("path", ""))
            if not _inside(path, [roots[r]]) or not path.is_file() or path.suffix.lower() not in VIDEO_EXT:
                raise HTTPException(400, "not a video")
        opts = {k: v for k, v in (b.get("options") or {}).items() if k in OPTION_KEYS}
        if b.get("save_defaults"):
            settings.update(**opts)
        cfg = {**settings.load(), **opts}
        if not (cfg.get("output_youtube") or cfg.get("output_reel")):
            raise HTTPException(400, "Pick at least one output.")
        job = Job.create(path)
        job.plan["remote_options"] = opts
        job.save()
        _submit(job, "Auto-edit", actions.full_auto, cfg)
        return job_summary(job)

    @app.get("/api/jobs/{name}", dependencies=[Depends(authed)])
    def job_get(name: str):
        return job_detail(_safe_job(name))

    @app.post("/api/jobs/{name}/render", dependencies=[Depends(authed)])
    async def job_render(name: str, req: Request):
        from .. import actions
        from ..pipeline.broll import make_card
        job = _safe_job(name)
        b = await _json(req)
        need = [s for s in job.plan.get("broll", []) if s["status"] == "needs_input"]
        if need:
            how = b.get("missing", "")
            if how == "cards":
                for s in need:
                    make_card(s)
            elif how == "skip":
                for s in need:
                    s["status"], s["user_locked"] = "skipped", True
            else:
                raise HTTPException(409, f"{len(need)} B-roll moment(s) still need a choice.")
            job.save()
        _submit(job, "Render", actions.render, _job_cfg(job))
        return job_summary(job)

    # ---------- b-roll
    @app.post("/api/jobs/{name}/broll/{sid}/{op}", dependencies=[Depends(authed)])
    async def broll_op(name: str, sid: int, op: str, req: Request):
        from .. import actions
        from ..pipeline.broll import make_card, set_user_file
        job = _safe_job(name)
        b = await _json(req)
        slots = job.plan.get("broll", [])
        slot = next((s for s in slots if s["id"] == sid), None)
        if slot is None:
            raise HTTPException(404, "no such moment")
        if op == "search":
            q = str(b.get("query", "")).strip()[:80]
            if not q:
                raise HTTPException(400, "type what to search")
            _submit(job, f"Search '{q}'", actions.search_slot, _job_cfg(job), sid, q)
        elif op == "use":
            _submit(job, "Use clip", actions.use_candidate, _job_cfg(job), sid, int(b.get("idx", 0)))
        elif op == "ai":
            _submit(job, "AI image", actions.ai_slot, _job_cfg(job), sid)
        elif op == "card":
            make_card(slot, (str(b.get("text", "")).strip() or None))
            job.save()
        elif op == "skip":
            slot["status"], slot["user_locked"] = ("skipped" if slot["status"] != "skipped" else
                                                   ("resolved" if slot.get("file") or slot.get("kind") == "graphic"
                                                    else "needs_input")), True
            job.save()
        elif op == "remove":
            job.plan["broll"] = [s for s in slots if s["id"] != sid]
            job.save()
        elif op == "file":
            f = _uploaded(str(b.get("upload", "")))
            set_user_file(slot, str(f), float(b.get("start", 0) or 0))
            job.save()
        else:
            raise HTTPException(404)
        return job_detail(_safe_job(name))

    # ---------- doodles / outlines / callouts
    @app.patch("/api/jobs/{name}/doodles/{did}", dependencies=[Depends(authed)])
    async def doodle_patch(name: str, did: int, req: Request):
        from ..pipeline import doodle_shapes as ds
        job = _safe_job(name)
        b = await _json(req)
        d = next((x for x in job.plan.get("doodles", []) if x["id"] == did), None)
        if not d:
            raise HTTPException(404)
        if "enabled" in b:
            d["enabled"] = bool(b["enabled"])
        k = b.get("kind")
        if k and k in ds.KINDS and k != "custom":
            shape, where, color, sound = ds.KINDS[k]
            if shape == "money" and job.plan.get("caption_script") == "hinglish":
                shape = "rupee"
            d.update({"kind": k, "shape": shape, "anchor": where, "color": color, "sound": sound})
        if "word" in b:
            d["word"] = re.sub(r"[^A-Za-z0-9!?]", "", str(b["word"]).upper())[:6] or "WOW"
        job.save()
        return job_detail(job)

    @app.post("/api/jobs/{name}/doodles", dependencies=[Depends(authed)])
    async def doodle_add(name: str, req: Request):
        from ..pipeline import doodle_shapes as ds
        job = _safe_job(name)
        b = await _json(req)
        k = b.get("kind", "amazing")
        if k not in ds.KINDS or k == "custom":
            raise HTTPException(400, "unknown doodle")
        t = _source_time(job, float(b.get("t", 0)), str(b.get("from", "youtube")))
        shape, where, color, sound = ds.KINDS[k]
        dl = job.plan.setdefault("doodles", [])
        dl.append({"id": max([x["id"] for x in dl], default=-1) + 1, "s": round(t, 3), "e": round(t + 1.9, 3),
                   "kind": k, "shape": shape, "anchor": where, "color": color, "sound": sound, "word": "WOW",
                   "prompt": "", "file": "", "line": "(added from phone)", "enabled": True, "source": "user"})
        dl.sort(key=lambda x: x["s"])
        job.save()
        return job_detail(job)

    @app.patch("/api/jobs/{name}/outlines/{oid}", dependencies=[Depends(authed)])
    async def outline_patch(name: str, oid: int, req: Request):
        job = _safe_job(name)
        b = await _json(req)
        o = next((x for x in job.plan.get("outlines", []) if x["id"] == oid), None)
        if not o:
            raise HTTPException(404)
        o["enabled"] = bool(b.get("enabled", True))
        job.save()
        return job_detail(job)

    @app.patch("/api/jobs/{name}/callouts/{i}", dependencies=[Depends(authed)])
    async def callout_patch(name: str, i: int, req: Request):
        job = _safe_job(name)
        b = await _json(req)
        cs = job.plan.get("callouts", [])
        if not 0 <= i < len(cs):
            raise HTTPException(404)
        if "enabled" in b:
            cs[i]["enabled"] = bool(b["enabled"])
        if "text" in b:
            cs[i]["text"] = str(b["text"]).strip()[:40]
        job.save()
        return job_detail(job)

    @app.post("/api/jobs/{name}/callouts", dependencies=[Depends(authed)])
    async def callout_add(name: str, req: Request):
        job = _safe_job(name)
        b = await _json(req)
        text = str(b.get("text", "")).strip()[:40]
        if not text:
            raise HTTPException(400, "type the text")
        t = _source_time(job, float(b.get("t", 0)), str(b.get("from", "youtube")))
        cs = job.plan.setdefault("callouts", [])
        cs.append({"s": t, "e": t + 1.6, "text": text, "enabled": True})
        cs.sort(key=lambda c: c["s"])
        job.save()
        return job_detail(job)

    # ---------- thumbnails / metadata / publish
    @app.post("/api/jobs/{name}/thumbnail", dependencies=[Depends(authed)])
    async def thumb_choice(name: str, req: Request):
        job = _safe_job(name)
        b = await _json(req)
        th = job.plan.setdefault("thumbnails", {})
        n = len(th.get("options", []))
        th["choice"] = max(0, min(n - 1, int(b.get("choice", 0)))) if n else 0
        job.save()
        return job_detail(job)

    @app.post("/api/jobs/{name}/thumbnails/regenerate", dependencies=[Depends(authed)])
    async def thumb_regen(name: str, req: Request):
        from .. import actions
        job = _safe_job(name)
        b = await _json(req)
        texts = [str(t).strip()[:40] for t in b.get("texts", []) if str(t).strip()][:3]
        _submit(job, "Thumbnails", actions.regen_thumbs, _job_cfg(job), texts)
        return job_summary(job)

    @app.put("/api/jobs/{name}/metadata", dependencies=[Depends(authed)])
    async def meta_put(name: str, req: Request):
        from ..pipeline import runner
        job = _safe_job(name)
        b = await _json(req)
        m = job.plan.setdefault("metadata", {})
        if "title" in b:
            m["title"] = str(b["title"]).strip()[:100]
        if "tags" in b:
            m["tags"] = [str(t).strip() for t in b["tags"] if str(t).strip()][:40]
        if "description" in b and b["description"] != runner.youtube_description(job.plan):
            m["youtube_description"] = str(b["description"])[:5000]
        if "instagram_caption" in b and b["instagram_caption"] != runner.instagram_caption(job.plan):
            m["instagram_caption_full"] = str(b["instagram_caption"])[:2200]
        if "publish_at" in b:
            m["publish_at"] = str(b["publish_at"] or "")
        job.save()
        return job_detail(job)

    @app.post("/api/jobs/{name}/publish/{where}", dependencies=[Depends(authed)])
    def publish(name: str, where: str):
        from .. import actions
        from ..upload import youtube
        job = _safe_job(name)
        if where == "youtube":
            if not youtube.connected():
                raise HTTPException(400, "Connect YouTube once in the desktop app (Settings) first.")
            _submit(job, "Upload to YouTube", actions.upload_youtube, _job_cfg(job))
        elif where == "instagram":
            _submit(job, "Upload to Instagram", actions.upload_instagram, _job_cfg(job))
        else:
            raise HTTPException(404)
        return job_summary(job)

    @app.delete("/api/tasks/{tid}", dependencies=[Depends(authed)])
    def cancel(tid: int):
        if not MANAGER.cancel(tid):
            raise HTTPException(409, "Only waiting tasks can be cancelled.")
        return {"ok": True}

    # ---------------------------------------------------------- live events (SSE)
    @app.get("/api/events", dependencies=[Depends(authed)])
    async def events(req: Request):
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        loop = asyncio.get_running_loop()

        def put(ev):
            if not q.full():
                q.put_nowait(ev)

        def cb(ev: dict):
            loop.call_soon_threadsafe(put, ev)

        unsub = MANAGER.subscribe(cb)

        async def gen():
            try:
                yield "retry: 3000\n\n"
                while True:
                    if await req.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), 15)
                        yield f"data: {json.dumps(ev, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                unsub()
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------------------------------------------------------- media
    roots_ok = [JOBS, UPLOADS, CACHE]

    def _file(p: str | None, extra_roots: list[Path] | None = None) -> Path:
        if not p:
            raise HTTPException(404)
        f = Path(p)
        if not f.is_file() or not _inside(f, roots_ok + (extra_roots or [])):
            raise HTTPException(404)
        return f

    @app.get("/media/{name}/thumb/{i}", dependencies=[Depends(authed)])
    def m_thumb(name: str, i: int):
        job = _safe_job(name)
        opts = job.plan.get("thumbnails", {}).get("options", [])
        if not 0 <= i < len(opts):
            raise HTTPException(404)
        return FileResponse(_file(opts[i].get("path")), headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/media/{name}/cover", dependencies=[Depends(authed)])
    def m_cover(name: str):
        from ..core import covers
        f = covers.cover_path(_safe_job(name).dir)
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/media/{name}/slot/{sid}", dependencies=[Depends(authed)])
    def m_slot(name: str, sid: int):
        job = _safe_job(name)
        s = next((x for x in job.plan.get("broll", []) if x["id"] == sid), None)
        lib = settings.load().get("broll_library")
        return FileResponse(_file((s or {}).get("preview"), [Path(lib)] if lib else None))

    @app.get("/media/{name}/cand/{sid}/{i}", dependencies=[Depends(authed)])
    def m_cand(name: str, sid: int, i: int):
        job = _safe_job(name)
        s = next((x for x in job.plan.get("broll", []) if x["id"] == sid), None)
        c = (s or {}).get("candidates", [])
        if not 0 <= i < len(c):
            raise HTTPException(404)
        lib = settings.load().get("broll_library")
        return FileResponse(_file(c[i].get("preview"), [Path(lib)] if lib else None))

    @app.get("/media/{name}/out/{kind}", dependencies=[Depends(authed)])
    async def m_out(name: str, kind: str, q: str = "preview", dl: int = 0):
        from .. import actions
        job = _safe_job(name)
        src = job.plan.get("outputs", {}).get(kind)
        if not src or not Path(src).exists():
            raise HTTPException(404, "not rendered yet")
        if q == "preview":
            try:
                f = await run_in_threadpool(actions.make_preview, job, kind)
            except Exception:  # noqa: BLE001
                f = Path(src)
        else:
            f = Path(src)
        f = _file(str(f))
        headers = {"Cache-Control": "private, max-age=600"}
        if dl:
            stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", job.source.stem)[:60]
            headers["Content-Disposition"] = f'attachment; filename="{stem}_{kind}.mp4"'
        return FileResponse(f, media_type="video/mp4", headers=headers)

    @app.get("/media/{name}/file/{what}", dependencies=[Depends(authed)])
    def m_file(name: str, what: str):
        """Extra downloads: thumbnail.jpg / cover.jpg / metadata.txt from the export folder."""
        job = _safe_job(name)
        d = job.plan.get("export_dir")
        if not d:
            raise HTTPException(404)
        stem = job.source.stem
        m = {"thumbnail": f"{stem}_thumbnail.jpg", "cover": f"{stem}_reel_cover.jpg",
             "metadata": f"{stem}_metadata.txt"}.get(what)
        f = Path(d) / m if m else None
        if not f or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, headers={"Content-Disposition": f'attachment; filename="{f.name}"'})

    return app


# ================================================================ helpers
async def _json(req: Request) -> dict:
    try:
        b = await req.json()
        return b if isinstance(b, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _job_cfg(job: Job) -> dict:
    return {**settings.load(), **(job.plan.get("remote_options") or {})}


def _source_time(job: Job, t: float, frm: str) -> float:
    from ..pipeline.timeline import Timeline, compute_keeps
    info = job.plan["media"]
    if frm == "reel" and job.plan.get("reel_range"):
        t += float(job.plan["reel_range"][0])
    tl = Timeline(compute_keeps(info["duration"], job.plan.get("cuts", []), info["fps"]))
    return max(0.0, min(info["duration"] - 0.5, tl.to_source(max(0.0, t))))


def _submit(job: Job, label: str, fn, cfg: dict, *args) -> TaskInfo:
    """Queue an action for a job; previews + phone notification are prepared when edits finish."""
    name = job.dir

    def run(*a, log, progress):
        j = Job(name)  # always work on the freshest plan.json
        return fn(j, cfg, *a, log=log, progress=progress)

    def after(t: TaskInfo):
        _after_task(Job(name), label, t, cfg)

    return MANAGER.submit(job.name, label, run, *args, after=after)


def _after_task(job: Job, label: str, t: TaskInfo, cfg: dict):
    srv = SERVER
    link = srv.job_url(job.name) if srv and srv.running() else ""
    title = Path(job.plan.get("source", "")).name
    if t.state == "failed":
        net.notify(cfg, "Edit failed", f"{title}: {t.error[:180]}", click=link, tags="warning")
        return
    stage = job.plan.get("stage")
    if label in ("Auto-edit", "Render"):
        if stage == "rendered":
            from .. import actions
            for k in job.plan.get("outputs", {}):
                try:
                    actions.make_preview(job, k)
                except Exception:  # noqa: BLE001
                    pass
            net.notify(cfg, "Your edit is ready", f"{title} - tap to watch and publish", click=link, tags="tada")
        elif stage == "needs_broll":
            n = sum(1 for s in job.plan.get("broll", []) if s["status"] == "needs_input")
            net.notify(cfg, "Your input needed", f"{title}: pick B-roll for {n} moment(s)", click=link, tags="eyes")
        elif stage == "analyzed":
            net.notify(cfg, "Ready for review", f"{title}: check it and tap Render", click=link, tags="eyes")
    elif label.startswith("Upload"):
        net.notify(cfg, "Published", f"{title}: {t.result}", click=str(t.result or link), tags="rocket")


def _gpu_name() -> str:
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=5, creationflags=net.CREATE_NO_WINDOW)
        return out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else ""
    except Exception:  # noqa: BLE001
        return ""


_AI_CACHE: tuple[bool, str] | None = None


def _ai_status() -> tuple[bool, str]:
    global _AI_CACHE
    if _AI_CACHE is None:
        try:
            from ..pipeline.broll.generate import available
            _AI_CACHE = available()
        except Exception as e:  # noqa: BLE001
            _AI_CACHE = (False, str(e))
    return _AI_CACHE
