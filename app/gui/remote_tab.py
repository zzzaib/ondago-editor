"""Settings -> Phone & remote: run OnDaGo from your phone (Android / iPhone / any browser), from anywhere."""
from __future__ import annotations

import sys

import secrets

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
                               QWidget)

from ..core import settings as settings_mod
from ..core.paths import DATA
from ..remote import auth, net
from ..remote.server import get_server

MAC = sys.platform == "darwin"


def _muted(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setObjectName("muted")
    lb.setOpenExternalLinks(True)
    lb.setTextInteractionFlags(Qt.TextBrowserInteraction)
    return lb


class RemoteTab(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        outer = QHBoxLayout(body)
        outer.addStretch(1)
        col = QWidget()
        col.setMaximumWidth(960)
        outer.addWidget(col, 12)
        outer.addStretch(1)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(28, 26, 28, 36)
        lay.setSpacing(12)
        h1 = QLabel("Phone app")
        h1.setObjectName("h1")
        lay.addWidget(h1)
        sub = QLabel("Run edits from your phone - at home on Wi-Fi or from anywhere.")
        sub.setObjectName("muted")
        lay.addWidget(sub)
        s = settings_mod.load()
        self.server = get_server(win.log)

        # ------------------------------------------------ on/off
        g = QGroupBox("Turn it on")
        f = QFormLayout(g)
        f.addRow(_muted("Edit from your phone while this laptop does the work: pick a video (from the phone or this PC), "
                        "choose options, fix B-roll, watch the result and publish. Works on Android, iPhone and any browser."))
        self.c_on = QCheckBox("Turn on the phone app")
        self.c_on.setChecked(bool(s.get("remote_enabled")))
        f.addRow(self.c_on)
        pw = QHBoxLayout()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("new password (8+ characters)")
        bpw = QPushButton("Set password")
        bpw.clicked.connect(self.set_password)
        pw.addWidget(self.pw, 1)
        pw.addWidget(bpw)
        f.addRow("Phone-app password", pw)
        self.pw_state = QLabel("")
        f.addRow("", self.pw_state)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(s.get("remote_port", 8765)))
        f.addRow("Port", self.port)
        self.bind = QComboBox()
        self.bind.addItem("Wi-Fi + Tailscale (recommended)", "all")
        self.bind.addItem("Only through the public link / Tailscale HTTPS", "local")
        self.bind.setCurrentIndex(max(0, self.bind.findData(s.get("remote_bind", "all"))))
        f.addRow("Who can connect", self.bind)
        row = QHBoxLayout()
        self.b_start = QPushButton("Start")
        self.b_start.setObjectName("primary")
        self.b_start.clicked.connect(self.toggle)
        self.state = QLabel("")
        row.addWidget(self.b_start)
        row.addWidget(self.state, 1)
        f.addRow(row)
        lay.addWidget(g)

        # ------------------------------------------------ pairing
        g = QGroupBox("Connect your phone")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.qr = QLabel()
        self.qr.setFixedSize(220, 220)
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setStyleSheet("background:#fff;border-radius:10px;color:#333;")
        h.addWidget(self.qr)
        right = QVBoxLayout()
        right.addWidget(_muted("Scan with your phone camera to open the app and log in (the code works once, for 10 min). "
                               "Then use 'Add to Home Screen' so it opens like an app."))
        self.link = QLineEdit()
        self.link.setReadOnly(True)
        right.addWidget(self.link)
        bb = QHBoxLayout()
        b1 = QPushButton("New QR code")
        b1.clicked.connect(self.refresh_qr)
        b2 = QPushButton("Copy link")
        b2.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.server.best_url()))
        bb.addWidget(b1)
        bb.addWidget(b2)
        right.addLayout(bb)
        self.urls = QLabel("")
        self.urls.setWordWrap(True)
        self.urls.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self.urls)
        right.addStretch()
        h.addLayout(right, 1)
        v.addLayout(h)
        lay.addWidget(g)

        # ------------------------------------------------ from anywhere
        g = QGroupBox("Use it from anywhere (no static IP needed)")
        f = QFormLayout(g)
        self.ts = QLabel("")
        self.ts.setWordWrap(True)
        bts = QPushButton("Get Tailscale (free)")
        bts.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://tailscale.com/download")))
        tsr = QHBoxLayout()
        tsr.addWidget(self.ts, 1)
        tsr.addWidget(bts)
        f.addRow("Tailscale", tsr)
        f.addRow(_muted("Recommended. Install Tailscale on this laptop AND your phone, log in with the same account. "
                        "Your phone can then reach the laptop on mobile data too - privately, nothing is exposed to the "
                        "internet."))
        self.c_tsh = QCheckBox("Also give it an HTTPS address (tailscale serve) - lets the phone app install fully")
        self.c_tsh.setChecked(bool(s.get("tailscale_https")))
        f.addRow(self.c_tsh)
        self.tunnel = QComboBox()
        self.tunnel.addItem("Off", "off")
        self.tunnel.addItem("Quick public link (random address, changes on restart)", "quick")
        self.tunnel.addItem("My own address (Cloudflare tunnel token)", "named")
        self.tunnel.setCurrentIndex(max(0, self.tunnel.findData(s.get("remote_tunnel", "off"))))
        f.addRow("Public link", self.tunnel)
        self.cf_token = QLineEdit(s.get("cloudflare_token", ""))
        self.cf_token.setEchoMode(QLineEdit.Password)
        self.cf_token.setPlaceholderText("tunnel token from Cloudflare Zero Trust -> Networks -> Tunnels")
        f.addRow("Tunnel token", self.cf_token)
        self.pub_url = QLineEdit(s.get("public_url", ""))
        self.pub_url.setPlaceholderText("https://edit.yourdomain.com")
        f.addRow("Your address", self.pub_url)
        f.addRow(_muted("A public link works from any browser (even a friend's phone) and is protected by your password. "
                        "The quick link is free with no account; its address changes each time, so turn on phone "
                        "notifications below to receive the new link automatically."))
        lay.addWidget(g)

        # ------------------------------------------------ same Wi-Fi
        g = QGroupBox("Same Wi-Fi: direct, fast, no internet data")
        f = QFormLayout(g)
        f.addRow(_muted("When your phone is on the same Wi-Fi/router as this laptop, uploads and videos go straight "
                        "between them. " + ("If macOS asks whether OnDaGo (Python) may accept incoming network "
                                            "connections, click Allow." if MAC else
                                            "Windows blocks this if the Wi-Fi is set to 'Public' or no firewall rule "
                                            "exists.")))
        self.lan_state = QLabel("checking...")
        self.lan_state.setWordWrap(True)
        f.addRow("Status", self.lan_state)
        self.b_lan = QPushButton("Allow my phone on this Wi-Fi")
        self.b_lan.clicked.connect(self.fix_lan)
        f.addRow(self.b_lan)
        lay.addWidget(g)
        if MAC:
            self.b_lan.hide()

        # ------------------------------------------------ staying online
        g = QGroupBox("Keep the laptop ready")
        f = QFormLayout(g)
        osname = "your Mac" if MAC else "Windows"
        self.c_awake = QCheckBox(f"Don't let {osname} sleep while the phone app is on")
        self.c_awake.setChecked(bool(s.get("keep_awake", True)))
        self.c_auto = QCheckBox("Start OnDaGo in the background when I log in" + ("" if MAC else " to Windows"))
        self.c_auto.setChecked(net.autostart_enabled() or bool(s.get("start_with_windows")))
        self.c_tray = QCheckBox("Closing the window keeps OnDaGo running in the " + ("menu bar" if MAC else "tray"))
        self.c_tray.setChecked(bool(s.get("close_to_tray", True)))
        for c in (self.c_awake, self.c_auto, self.c_tray):
            f.addRow(c)
        f.addRow(_muted("Keep the MacBook plugged in and the lid open (or use it with an external display); a closed "
                        "lid on battery puts any Mac to sleep." if MAC else
                        "Plug the laptop in. If you close the lid, set Windows to do nothing on lid close: Control Panel -> "
                        "Power Options -> 'Choose what closing the lid does' -> When plugged in: Do nothing."))
        lay.addWidget(g)

        # ------------------------------------------------ notifications
        g = QGroupBox("Phone notifications (ntfy - free, no account)")
        f = QFormLayout(g)
        nt = QHBoxLayout()
        self.ntfy = QLineEdit(s.get("ntfy_topic", ""))
        self.ntfy.setPlaceholderText("a secret topic name, e.g. ondago-k3x9w2")
        bg = QPushButton("Make one")
        bg.clicked.connect(lambda: self.ntfy.setText("ondago-" + secrets.token_hex(5)))
        bt = QPushButton("Send test")
        bt.clicked.connect(self.test_ntfy)
        nt.addWidget(self.ntfy, 1)
        nt.addWidget(bg)
        nt.addWidget(bt)
        f.addRow("Topic", nt)
        f.addRow(_muted('Install the <a style="color:#B8A8FF" href="https://ntfy.sh">ntfy</a> app on your phone and '
                        "subscribe to the same topic. You'll get a ping when an edit is ready, needs your input, or the "
                        "public link changes. Treat the topic like a password."))
        lay.addWidget(g)

        # ------------------------------------------------ folders
        g = QGroupBox("Folders the phone can open")
        v = QVBoxLayout(g)
        v.addWidget(_muted("Videos in these folders can be picked from the phone without uploading. "
                           "Empty = Videos, Desktop, Downloads, Pictures (and OneDrive)."))
        self.folders = QListWidget()
        self.folders.setMaximumHeight(110)
        for p in s.get("remote_folders") or []:
            self.folders.addItem(p)
        v.addWidget(self.folders)
        fb = QHBoxLayout()
        ba = QPushButton("Add folder")
        ba.clicked.connect(self.add_folder)
        br = QPushButton("Remove")
        br.clicked.connect(lambda: [self.folders.takeItem(self.folders.row(i)) for i in self.folders.selectedItems()])
        fb.addWidget(ba)
        fb.addWidget(br)
        fb.addStretch()
        v.addLayout(fb)
        lay.addWidget(g)

        bottom = QHBoxLayout()
        save = QPushButton("Save && apply")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        out = QPushButton("Sign out all phones")
        out.setObjectName("danger")
        out.clicked.connect(self.sign_out_all)
        bottom.addWidget(save)
        bottom.addStretch()
        bottom.addWidget(out)
        lay.addLayout(bottom)
        lay.addStretch()

        QTimer.singleShot(800, self.check_lan)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(8000)
        QTimer.singleShot(300, self.refresh)

    # ------------------------------------------------------------ actions
    def set_password(self):
        pw = self.pw.text()
        if len(pw) < 8:
            QMessageBox.warning(self, "Password", "Use at least 8 characters.")
            return
        auth.set_password(pw)
        self.pw.clear()
        self.refresh()
        self.win.statusBar().showMessage("Phone-app password saved", 3000)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder the phone can open")
        if d:
            self.folders.addItem(d)

    def check_lan(self):
        if MAC:
            ips = net.lan_ips()
            self.lan_state.setText(("On this Wi-Fi: " + ", ".join(f"http://{i}:{self.port.value()}" for i in ips))
                                   if ips else "Not connected to a Wi-Fi/LAN")
            return
        st = net.lan_status(self.port.value())
        self._lan = st
        pub = [n for n in st["networks"] if n["category"] == "Public"]
        parts = []
        for n in st["networks"]:
            ok = n["category"] in ("Private", "DomainAuthenticated")
            parts.append(f"{n['alias']} ({n['name']}): <b>{n['category']}</b> {'✓' if ok else '- phones blocked'}")
        parts.append("Firewall rule: " + ("<b>allowed</b> ✓" if st["rule"] else "<b>missing</b>"))
        self.lan_state.setText("<br>".join(parts) or "No network found")
        self.b_lan.setEnabled(bool(pub) or not st["rule"])
        self.b_lan.setText("Allow my phone on this Wi-Fi" if (pub or not st["rule"]) else "All set ✓")

    def fix_lan(self):
        st = getattr(self, "_lan", None) or net.lan_status(self.port.value())
        pub = [n for n in st["networks"] if n["category"] == "Public"]
        make_private = []
        if pub:
            names = ", ".join(f"'{n['name']}'" for n in pub)
            r = QMessageBox.question(self, "Home network?", f"Windows treats {names} as a Public network, which blocks your "
                                     "phone.\n\nIs this your own home/office Wi-Fi? Choose Yes to mark it Private "
                                     "(recommended at home). Choose No on cafe/hotel Wi-Fi.",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                make_private = [n["alias"] for n in pub]
        QMessageBox.information(self, "Windows will ask", "Windows will now ask for admin permission once, to allow "
                                "OnDaGo's port on Private networks" + (" and mark your Wi-Fi Private." if make_private else "."))
        ok, msg = net.allow_lan(self.port.value(), make_private)
        if not ok:
            QMessageBox.warning(self, "Not changed", msg)
        self.check_lan()

    def test_ntfy(self):
        cfg = {**settings_mod.load(), "ntfy_topic": self.ntfy.text().strip()}
        ok = net.notify(cfg, "OnDaGo test", "Notifications work! You'll hear from me when an edit is ready.",
                        click=self.server.best_url() if self.server.running() else "")
        QMessageBox.information(self, "ntfy", "Sent - check your phone." if ok else
                                "Couldn't send. Check the topic and your internet connection.")

    def sign_out_all(self):
        if QMessageBox.question(self, "Sign out", "Sign out every phone/browser? They'll need the password or a new QR "
                                "code.") == QMessageBox.Yes:
            auth.sign_out_everyone()
            self.refresh_qr()

    def _collect(self) -> dict:
        return {"remote_enabled": self.c_on.isChecked(), "remote_port": self.port.value(),
                "remote_bind": self.bind.currentData(), "tailscale_https": self.c_tsh.isChecked(),
                "remote_tunnel": self.tunnel.currentData(), "cloudflare_token": self.cf_token.text().strip(),
                "public_url": self.pub_url.text().strip().rstrip("/"), "keep_awake": self.c_awake.isChecked(),
                "start_with_windows": self.c_auto.isChecked(), "close_to_tray": self.c_tray.isChecked(),
                "ntfy_topic": self.ntfy.text().strip(),
                "remote_folders": [self.folders.item(i).text() for i in range(self.folders.count())]}

    def save(self):
        vals = self._collect()
        settings_mod.update(**vals)
        if vals["start_with_windows"] != net.autostart_enabled():
            ok, err = net.set_autostart(vals["start_with_windows"])
            if not ok and err:
                QMessageBox.warning(self, "Start with Windows", err)
        was = self.server.running()
        if was:
            self.server.stop()
        if vals["remote_enabled"]:
            self.start()
        self.win.cfg.update(vals)
        self.win.update_tray()
        self.refresh()

    def start(self):
        ok, msg = self.server.start()
        if not ok:
            QMessageBox.warning(self, "Phone app", msg)
        self.refresh_qr()

    def toggle(self):
        if self.server.running():
            self.server.stop()
            settings_mod.update(remote_enabled=False)
            self.c_on.setChecked(False)
        else:
            settings_mod.update(**{**self._collect(), "remote_enabled": True})
            self.c_on.setChecked(True)
            self.start()
        self.win.update_tray()
        self.refresh()

    def refresh_qr(self):
        if not self.server.running():
            self.qr.setText("Start the phone app\nto see the QR code")
            self.link.setText("")
            return
        url = f"{self.server.best_url()}/#/?pair={auth.new_pair_code()}"
        out = net.qr_png(url, DATA / "pair_qr.png")
        pm = QPixmap(str(out)) if out else QPixmap()
        if not pm.isNull():
            self.qr.setPixmap(pm.scaled(210, 210, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.qr.setText("pip install qrcode\nfor a QR code")
        self.link.setText(self.server.best_url())

    def refresh(self):
        s = settings_mod.load()
        self.pw_state.setText("Password is set" if s.get("remote_password_hash") else
                              "<span style='color:#FFB020'>No password yet - set one to turn the phone app on</span>")
        running = self.server.running()
        self.b_start.setText("Stop" if running else "Start")
        self.state.setText(f"Running on port {self.server.port}" if running else "Off")
        if not self.isVisible() and not running:
            return
        ts = net.tailscale_info()
        if not ts["installed"]:
            self.ts.setText("Not installed")
        elif not ts["running"]:
            self.ts.setText("Installed but not connected - open Tailscale and log in")
        else:
            self.ts.setText(f"Connected: {ts['ip']}  ({ts['dns'] or 'no MagicDNS name'})")
        if running:
            u = self.server.urls()
            lines = [f"<b>On this Wi-Fi:</b> {x}" for x in u["lan"][:2]]
            if u["tailscale"]:
                lines.append(f"<b>Tailscale:</b> {u['tailscale']}")
            if u["tailscale_https"]:
                lines.append(f"<b>Tailscale HTTPS:</b> {u['tailscale_https']}")
            if u["public"]:
                lines.append(f"<b>Public link:</b> {u['public']}")
            elif s.get("remote_tunnel") in ("quick", "named"):
                err = self.server.tunnel.error if self.server.tunnel else ""
                lines.append("<b>Public link:</b> starting..." + (f" ({err})" if err else ""))
            if self.server.error:
                lines.append(f"<span style='color:#FFB020'>{self.server.error}</span>")
            self.urls.setText("<br>".join(lines))
            if self.link.text() != self.server.best_url():
                self.refresh_qr()
        else:
            self.urls.setText("")
            if self.qr.pixmap() is None or self.qr.pixmap().isNull():
                self.qr.setText("Start the phone app\nto see the QR code")


def autostart_server(win) -> None:
    """Called once at app start: bring the phone app up if it was on."""
    s = settings_mod.load()
    if s.get("remote_enabled") and s.get("remote_password_hash"):
        ok, msg = get_server(win.log).start()
        if not ok:
            win.log(f"Phone app not started: {msg}")
