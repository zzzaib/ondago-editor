"""Reaching the laptop from a phone, without a static IP.

* LAN          : http://<laptop-ip>:<port>  (same Wi-Fi)
* Tailscale    : free private network between your own devices - works on mobile data, nothing exposed publicly.
                 Optional HTTPS via `tailscale serve` (https://<laptop>.<tailnet>.ts.net).
* Cloudflare   : public https link. "quick" = random *.trycloudflare.com link that changes when the app restarts
                 (sent to your phone through ntfy); "named" = your own fixed hostname using a tunnel token.
Also: keep-awake, start-with-Windows and phone notifications (ntfy)."""
from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from ..core.paths import IS_MAC, IS_WIN, ROOT, TOOLS

CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0
Log = Callable[[str], None]


def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
                           creationflags=CREATE_NO_WINDOW)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


# ------------------------------------------------------------------ LAN
def lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:  # noqa: BLE001
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.", "100.")))


# ------------------------------------------------------------ Windows Firewall / network type
FW_RULE = "OnDaGo phone app"


def _ps(script: str, timeout: float = 15) -> tuple[int, str]:
    return _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=timeout)


def lan_status(port: int) -> dict:
    """{'networks': [{'name', 'alias', 'category'}], 'rule': bool} - why a phone on the same Wi-Fi can't connect."""
    out = {"networks": [], "rule": False}
    if not IS_WIN:
        return out
    rc, txt = _ps("Get-NetConnectionProfile | Select-Object Name,InterfaceAlias,@{n='Cat';e={[string]$_.NetworkCategory}}"
                  " | ConvertTo-Json -Compress")
    if rc == 0 and txt.strip():
        try:
            data = json.loads(txt[txt.index("["):] if txt.lstrip().startswith("[") else txt[txt.index("{"):])
            for n in data if isinstance(data, list) else [data]:
                out["networks"].append({"name": n.get("Name", ""), "alias": n.get("InterfaceAlias", ""),
                                        "category": n.get("Cat", "")})
        except Exception:  # noqa: BLE001
            pass
    rc, txt = _ps(f"(Get-NetFirewallRule -DisplayName '{FW_RULE}' -ErrorAction SilentlyContinue | "
                  f"Get-NetFirewallPortFilter | Where-Object LocalPort -eq '{port}') -ne $null")
    out["rule"] = rc == 0 and "True" in txt
    return out


def allow_lan(port: int, make_private: list[str] | None = None) -> tuple[bool, str]:
    """Opens ONE Windows admin prompt that lets phones on your home Wi-Fi reach OnDaGo:
    an inbound rule for this port on Private networks, and (if you agreed) marks your home Wi-Fi as Private."""
    if not IS_WIN:
        return False, "Only on Windows"
    lines = [f"Remove-NetFirewallRule -DisplayName '{FW_RULE}' -ErrorAction SilentlyContinue",
             f"New-NetFirewallRule -DisplayName '{FW_RULE}' -Direction Inbound -Protocol TCP -LocalPort {int(port)} "
             f"-Action Allow -Profile Private,Domain | Out-Null"]
    for alias in make_private or []:
        safe = alias.replace("'", "''")
        lines.append(f"Set-NetConnectionProfile -InterfaceAlias '{safe}' -NetworkCategory Private")
    script = "; ".join(lines)
    import base64
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    rc, txt = _ps("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
                  f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{enc}'", timeout=180)
    if rc != 0:
        return False, (txt.strip() or "The admin prompt was cancelled.")[:300]
    return True, "done"


# ------------------------------------------------------------ Tailscale
def tailscale_exe() -> str | None:
    cands = [shutil.which("tailscale")]
    if IS_WIN:
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            if base:
                cands.append(str(Path(base) / "Tailscale" / "tailscale.exe"))
    if IS_MAC:
        cands += ["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/usr/local/bin/tailscale",
                  "/opt/homebrew/bin/tailscale"]
    return next((c for c in cands if c and Path(c).exists()), None)


def tailscale_info() -> dict:
    """{installed, running, ip, dns, https_url}"""
    exe = tailscale_exe()
    info = {"installed": bool(exe), "running": False, "ip": "", "dns": "", "https_url": ""}
    if not exe:
        return info
    rc, out = _run([exe, "status", "--json"])
    if rc == 0:
        try:
            st = json.loads(out[out.index("{"):])
            me = st.get("Self", {})
            info["running"] = st.get("BackendState") == "Running"
            ips = [i for i in me.get("TailscaleIPs", []) if "." in i]
            info["ip"] = ips[0] if ips else ""
            info["dns"] = (me.get("DNSName") or "").rstrip(".")
        except Exception:  # noqa: BLE001
            pass
    return info


def tailscale_serve(port: int, log: Log = print) -> tuple[bool, str]:
    """Publish the app on https://<laptop>.<tailnet>.ts.net inside your tailnet (needs HTTPS enabled in Tailscale)."""
    exe = tailscale_exe()
    if not exe:
        return False, "Tailscale is not installed"
    rc, out = _run([exe, "serve", "--bg", f"http://127.0.0.1:{port}"], timeout=25)
    if rc != 0:
        rc, out = _run([exe, "serve", "--bg", "--https=443", f"http://127.0.0.1:{port}"], timeout=25)
    if rc != 0:
        log(f"tailscale serve failed: {out.strip()[:300]}")
        return False, out.strip()[:300] or "tailscale serve failed"
    info = tailscale_info()
    return True, f"https://{info['dns']}" if info["dns"] else "https (see tailscale serve status)"


# ------------------------------------------------------------ Cloudflare
_CF = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
CLOUDFLARED_URL = _CF + "cloudflared-windows-amd64.exe"
CLOUDFLARED_MAC = _CF + ("cloudflared-darwin-arm64.tgz" if platform.machine() == "arm64" else "cloudflared-darwin-amd64.tgz")


def cloudflared_path() -> Path:
    return TOOLS / "cloudflared" / ("cloudflared.exe" if IS_WIN else "cloudflared")


def ensure_cloudflared(log: Log = print) -> Path:
    exe = cloudflared_path()
    if exe.exists() and exe.stat().st_size > 1_000_000:
        return exe
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    if not (IS_WIN or IS_MAC):
        raise RuntimeError("Install cloudflared (https://github.com/cloudflare/cloudflared) to use a public link.")
    import requests
    log("Downloading cloudflared (Cloudflare Tunnel, ~40-60 MB) ...")
    exe.parent.mkdir(parents=True, exist_ok=True)
    tmp = exe.with_suffix(".part")
    with requests.get(CLOUDFLARED_MAC if IS_MAC else CLOUDFLARED_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    if IS_MAC:
        import tarfile
        with tarfile.open(tmp) as tf:
            member = next(m for m in tf.getmembers() if m.isfile() and m.name.split("/")[-1] == "cloudflared")
            with tf.extractfile(member) as src, open(exe, "wb") as dst:  # type: ignore[union-attr]
                shutil.copyfileobj(src, dst)
        tmp.unlink(missing_ok=True)
        exe.chmod(0o755)
    else:
        tmp.replace(exe)
    return exe


class Tunnel:
    """Keeps a cloudflared process alive; reports the public URL through on_url."""

    def __init__(self, port: int, mode: str, token: str, fixed_url: str, log: Log, on_url: Callable[[str], None]):
        self.port, self.mode, self.token, self.fixed_url = port, mode, token, fixed_url
        self.log, self.on_url = log, on_url
        self.proc: subprocess.Popen | None = None
        self.url = ""
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="ondago-tunnel", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _cmd(self, exe: Path) -> list[str]:
        if self.mode == "named":
            return [str(exe), "tunnel", "--no-autoupdate", "run", "--token", self.token]
        return [str(exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"]

    def _loop(self):
        backoff = 3
        while not self._stop.is_set():
            try:
                exe = ensure_cloudflared(self.log)
                self.proc = subprocess.Popen(self._cmd(exe), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                             encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
                if self.mode == "named" and self.fixed_url:
                    self._set_url(self.fixed_url)
                for line in self.proc.stdout:  # type: ignore[union-attr]
                    if self._stop.is_set():
                        break
                    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                    if m and self.mode != "named":
                        self._set_url(m.group(0))
                    if "ERR" in line and "Retrying" not in line:
                        self.error = line.strip()[-200:]
                self.proc.wait()
                backoff = 3
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                self.log(f"Public link error: {e}")
            if self._stop.is_set():
                break
            self.url = ""
            time.sleep(backoff)
            backoff = min(120, backoff * 2)

    def _set_url(self, url: str):
        if url != self.url:
            self.url = url
            self.error = ""
            self.log(f"Public link: {url}")
            try:
                self.on_url(url)
            except Exception:  # noqa: BLE001
                pass


# ------------------------------------------------------------ keep awake
class KeepAwake:
    """Stops Windows from going to sleep while the app is serving your phone (the screen may still turn off)."""
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_AWAYMODE_REQUIRED = 0x80000000, 0x00000001, 0x00000040

    def __init__(self):
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    _caf: subprocess.Popen | None = None

    def start(self):
        if IS_MAC:
            # macOS: `caffeinate -i` keeps the Mac awake (lid open / on power) for as long as OnDaGo runs
            if self._caf is None or self._caf.poll() is not None:
                try:
                    self._caf = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
                except Exception:  # noqa: BLE001
                    self._caf = None
            return
        if not IS_WIN or self._t:
            return
        self._t = threading.Thread(target=self._loop, name="ondago-awake", daemon=True)
        self._t.start()

    def _loop(self):
        k = ctypes.windll.kernel32  # type: ignore[attr-defined]
        while not self._stop.is_set():
            k.SetThreadExecutionState(self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED | self.ES_AWAYMODE_REQUIRED)
            self._stop.wait(60)
        k.SetThreadExecutionState(self.ES_CONTINUOUS)

    def stop(self):
        self._stop.set()
        self._t = None
        if self._caf is not None and self._caf.poll() is None:
            self._caf.terminate()
        self._caf = None


# ------------------------------------------------------------ autostart
def _startup_lnk() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / \
        "OnDaGo Editor (background).lnk"


LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.ondago.editor.plist"


def _mac_autostart(on: bool) -> tuple[bool, str]:
    try:
        if not on:
            LAUNCH_AGENT.unlink(missing_ok=True)
            return True, "removed"
        py = ROOT / ".venv" / "bin" / "python"
        LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_AGENT.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ondago.editor</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>-m</string><string>app.main</string>
    <string>--background</string></array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>RunAtLoad</key><true/>
</dict></plist>
""", encoding="utf-8")
        return True, "added"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def set_autostart(on: bool) -> tuple[bool, str]:
    """Start OnDaGo hidden in the tray / menu bar when you log in (so the phone app is always reachable)."""
    if IS_MAC:
        return _mac_autostart(on)
    if not IS_WIN:
        return False, "Only on Windows and macOS"
    lnk = _startup_lnk()
    if not on:
        try:
            lnk.unlink(missing_ok=True)
            return True, "removed"
        except Exception as e:  # noqa: BLE001
            return False, str(e)
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    ico = ROOT / "app" / "gui" / "icon.ico"
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);$s.TargetPath=$env:TGT;"
          "$s.Arguments='-m app.main --background';$s.WorkingDirectory=$env:WD;"
          "if(Test-Path $env:ICO){$s.IconLocation=$env:ICO};$s.Save()")
    env = {**os.environ, "LNK": str(lnk), "TGT": str(pyw), "WD": str(ROOT), "ICO": str(ico)}
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], env=env,
                           capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        return p.returncode == 0, (p.stderr or "").strip()[:300]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def autostart_enabled() -> bool:
    if IS_MAC:
        return LAUNCH_AGENT.exists()
    return IS_WIN and _startup_lnk().exists()


# ------------------------------------------------------------ notifications
def notify(cfg: dict, title: str, message: str, click: str = "", tags: str = "clapper") -> bool:
    """Push to your phone via ntfy (free app: Android / iOS). Only the short message is sent."""
    topic = (cfg.get("ntfy_topic") or "").strip()
    if not topic:
        return False
    server = (cfg.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    try:
        import requests
        headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore"), "Tags": tags}
        if click:
            headers["Click"] = click
        r = requests.post(f"{server}/{topic}", data=message.encode("utf-8"), headers=headers, timeout=10)
        return r.ok
    except Exception:  # noqa: BLE001
        return False


def qr_png(text: str, out: Path, box: int = 8) -> Path | None:
    try:
        import qrcode
        img = qrcode.make(text, box_size=box, border=2)
        img.save(out)
        return out
    except Exception:  # noqa: BLE001
        return None
