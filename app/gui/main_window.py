from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from ..core import settings as settings_mod
from ..core.job import Job
from ..core.paths import DATA, IS_WIN, ROOT
from ..version import VERSION
from .create_page import CreateTab, ProjectsPage
from .remote_tab import RemoteTab, autostart_server
from .review import ReviewTab
from .theme import QSS

VIDEO_FILTER = "Videos (*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.MP4 *.MOV)"


def open_path(p: str | Path):
    p = str(p)
    if IS_WIN:
        os.startfile(p)  # noqa: S606
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(p))


def combo(items: list[tuple[str, str]], value: str) -> QComboBox:
    c = QComboBox()
    for label, key in items:
        c.addItem(label, key)
    i = c.findData(value)
    c.setCurrentIndex(max(0, i))
    return c



# ===================================================================== Settings
class SettingsTab(QScrollArea):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        outer = QHBoxLayout(body)
        outer.addStretch(1)
        col = QWidget()
        col.setMaximumWidth(900)
        outer.addWidget(col, 12)
        outer.addStretch(1)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(28, 26, 28, 36)
        lay.setSpacing(12)
        h = QLabel("Settings")
        h.setObjectName("h1")
        lay.addWidget(h)
        sub = QLabel("All optional. The presets on the New edit page already cover most videos.")
        sub.setObjectName("muted")
        lay.addWidget(sub)
        s = win.cfg
        self.w: dict[str, QWidget] = {}

        def line(key, placeholder="", password=False):
            e = QLineEdit(str(s.get(key, "")))
            e.setPlaceholderText(placeholder)
            if password:
                e.setEchoMode(QLineEdit.Password)
            self.w[key] = e
            return e

        def folder(key, title):
            box = QHBoxLayout()
            e = line(key, "not set")
            b = QPushButton("Browse")
            b.clicked.connect(lambda: (lambda d: d and e.setText(d))(QFileDialog.getExistingDirectory(self, title)))
            box.addWidget(e)
            box.addWidget(b)
            return box

        def num(key, lo, hi, step=0.05, decimals=2):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(decimals)
            sp.setValue(float(s.get(key, 0)))
            self.w[key] = sp
            return sp

        def chk(key, label):
            c = QCheckBox(label)
            c.setChecked(bool(s.get(key)))
            self.w[key] = c
            return c

        def cmb(key, items):
            c = combo(items, s.get(key))
            self.w[key] = c
            return c

        g = QGroupBox("General")
        f = QFormLayout(g)
        f.addRow("Spoken language", cmb("language", [("Auto detect", "auto"), ("English", "en"), ("Hinglish (Hindi + English)", "hinglish"), ("Hindi", "hi"),
                                                     ("Urdu", "ur"), ("Spanish", "es"), ("German", "de"),
                                                     ("French", "fr")]))
        f.addRow("Names / terms in your videos", line("vocabulary", "e.g. Meta Quest, Ray-Ban, Unreal Engine, Zaan"))
        f.addRow("", QLabel("Helps the speech model and proof-reader spell brands, products and your name correctly."))
        f.addRow("", chk("hinglish_asr", "Use the Hinglish speech model for Hindi/Hinglish (Roman captions, more accurate)"))
        f.addRow("", chk("transcript_cleanup", "Proof-read the transcript with the editor model"))
        f.addRow("Output folder", folder("output_dir", "Output folder"))
        f.addRow("", QLabel("Empty = a '<video>_OnDaGo' folder next to your raw video"))
        f.addRow("YouTube shape", cmb("youtube_format", [("Always 16:9 (vertical videos get blurred sides)", "landscape"),
                                                         ("Keep the recording's shape", "keep")]))
        f.addRow("Video encoder", cmb("encoder", [("Auto (NVIDIA NVENC if available)", "auto"), ("NVENC", "nvenc"),
                                                 ("CPU x264", "x264")]))
        lay.addWidget(g)

        g = QGroupBox("AI models")
        f = QFormLayout(g)
        f.addRow("Editor LLM (Ollama tag)", line("llm_model"))
        f.addRow("", QLabel("qwen3.5:9b fits 8 GB VRAM. Use qwen3.5:4b on 6 GB cards. Missing models download automatically."))
        f.addRow("Whisper model", cmb("whisper_model", [("large-v3-turbo (best)", "large-v3-turbo"),
                                                        ("medium", "medium"), ("small (fast)", "small")]))
        lay.addWidget(g)

        g = QGroupBox("Cutting")
        f = QFormLayout(g)
        f.addRow("Cut pauses longer than (s)", num("max_pause", 0.2, 3.0))
        f.addRow("Leave this much pause (s)", num("keep_pause", 0.0, 1.0))
        fw = QLineEdit(", ".join(s.get("filler_words", [])))
        self.w["filler_words"] = fw
        f.addRow("Filler words", fw)
        f.addRow("Safety margin before words (s)", num("word_pad_before", 0.0, 0.5, 0.02))
        f.addRow("Safety margin after words (s)", num("word_pad_after", 0.0, 0.6, 0.02))
        f.addRow("", chk("detect_unrecognized_fillers", "Also cut sounds Whisper didn't transcribe (um/uh/breaths)"))
        lay.addWidget(g)

        g = QGroupBox("B-roll")
        f = QFormLayout(g)
        f.addRow("My B-roll library (offline)", folder("broll_library", "B-roll folder"))
        f.addRow("Transition", cmb("broll_transition", [("Slide in", "slide"), ("Fade", "fade")]))
        f.addRow("Amount", cmb("broll_density", [("Low (every ~40 s)", "low"), ("Medium (every ~20 s)", "medium"),
                                                 ("High (every ~12 s)", "high")]))
        f.addRow("", chk("broll_verify", "Double-check matches with the vision model"))
        f.addRow("Library match strictness", num("broll_library_threshold", 0.001, 0.9, 0.01, 3))
        f.addRow("Pexels API key (free)", line("pexels_key", "pexels.com/api", True))
        f.addRow("Pixabay API key (free)", line("pixabay_key", "pixabay.com/api/docs", True))
        links = QLabel('<a style="color:#B8A8FF" href="https://www.pexels.com/api/">Get Pexels key</a> &nbsp; '
                       '<a style="color:#B8A8FF" href="https://pixabay.com/api/docs/">Get Pixabay key</a>')
        links.setOpenExternalLinks(True)
        f.addRow("", links)
        lay.addWidget(g)

        g = QGroupBox("Doodles")
        f = QFormLayout(g)
        f.addRow("How many", cmb("doodle_density", [("A few (every ~30 s)", "low"), ("Some (every ~15 s)", "medium"),
                                                   ("Lots (every ~8 s)", "high")]))
        f.addRow("", chk("callout_doodles", "Underline / circle the pop-up text with a marker"))
        f.addRow("", QLabel("Turn doodles, the outline and AI doodles on or off per video on the Create tab."))
        lay.addWidget(g)

        g = QGroupBox("Audio")
        f = QFormLayout(g)
        f.addRow("", chk("denoise", "Remove background noise (RNNoise)"))
        f.addRow("Loudness target (LUFS)", num("loudness_lufs", -24, -9, 1, 0))
        f.addRow("Music folder", folder("music_folder", "Music folder"))
        f.addRow("Music volume (dB)", num("music_volume_db", -40, -6, 1, 0))
        lay.addWidget(g)

        g = QGroupBox("Reel / vertical")
        f = QFormLayout(g)
        f.addRow("Reel end-card text", line("cta_reel", "e.g. Follow for more (empty = off)"))
        f.addRow("YouTube end-card text", line("cta_youtube", "e.g. Subscribe for more (empty = off)"))
        f.addRow("Sound effects volume (dB)", num("sfx_volume_db", -30, 6, 1, 0))
        sp = QSpinBox()
        sp.setRange(15, 900)
        sp.setValue(int(s.get("reel_max_seconds", 90)))
        self.w["reel_max_seconds"] = sp
        f.addRow("Max highlight length (s)", sp)
        f.addRow("Vertical layout", cmb("vertical_layout", [("Follow my face (crop)", "face"),
                                                           ("Fit + blurred background", "blur")]))
        lay.addWidget(g)

        g = QGroupBox("Thumbnails")
        f = QFormLayout(g)
        f.addRow("Background", cmb("thumbnail_background", [("AI generated (FLUX)", "ai"), ("Blurred frame", "blur"),
                                                            ("Color gradient", "gradient")]))
        sp2 = QSpinBox()
        sp2.setRange(1, 3)
        sp2.setValue(int(s.get("thumbnail_count", 3)))
        self.w["thumbnail_count"] = sp2
        f.addRow("Variants", sp2)
        f.addRow("", chk("thumbnail_vlm_pick", "Let the vision model pick the most expressive frames"))
        lay.addWidget(g)

        g = QGroupBox("YouTube")
        f = QFormLayout(g)
        cs = QHBoxLayout()
        e = line("youtube_client_secret", "client_secret.json (OAuth Desktop app)")
        b = QPushButton("Browse")
        b.clicked.connect(lambda: (lambda p: p and e.setText(p))(
            QFileDialog.getOpenFileName(self, "client_secret.json", "", "JSON (*.json)")[0]))
        cs.addWidget(e)
        cs.addWidget(b)
        f.addRow("OAuth client file", cs)
        self.yt_status = QLabel("")
        cb = QPushButton("Connect YouTube account")
        cb.clicked.connect(self.connect_youtube)
        f.addRow(cb, self.yt_status)
        f.addRow("Default privacy", cmb("youtube_privacy", [("Private", "private"), ("Unlisted", "unlisted"),
                                                           ("Public", "public")]))
        f.addRow("Category", cmb("youtube_category_id", [("People & Blogs", "22"), ("Gaming", "20"),
                                                        ("Education", "27"), ("Science & Technology", "28"),
                                                        ("Entertainment", "24"), ("Howto & Style", "26")]))
        help_yt = QLabel("One-time: console.cloud.google.com -> new project -> enable 'YouTube Data API v3' -> "
                         "OAuth consent screen (External, add yourself as test user) -> Credentials -> "
                         "OAuth client ID -> Desktop app -> download JSON. Note: until Google audits your "
                         "project, API uploads are forced to Private.")
        help_yt.setWordWrap(True)
        help_yt.setObjectName("muted")
        f.addRow(help_yt)
        lay.addWidget(g)

        g = QGroupBox("Instagram")
        f = QFormLayout(g)
        f.addRow("Instagram user ID", line("instagram_user_id"))
        f.addRow("Access token (long-lived)", line("instagram_access_token", "", True))
        f.addRow("Graph API version", line("instagram_api_version"))
        f.addRow("", chk("instagram_share_to_feed", "Also show the Reel on my profile grid"))
        self.ig_status = QLabel("")
        ib = QPushButton("Test Instagram connection")
        ib.clicked.connect(self.test_instagram)
        f.addRow(ib, self.ig_status)
        help_ig = QLabel("One-time: Instagram Professional (Business/Creator) account -> developers.facebook.com -> "
                         "create app -> add 'Instagram API' -> generate a token with instagram_business_basic + "
                         "instagram_business_content_publish -> paste token and your IG user id here.")
        help_ig.setWordWrap(True)
        help_ig.setObjectName("muted")
        f.addRow(help_ig)
        lay.addWidget(g)

        save = QPushButton("Save settings")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        lay.addWidget(save)
        lay.addStretch()

    def save(self):
        s = self.win.cfg
        for k, w in self.w.items():
            if isinstance(w, QLineEdit):
                s[k] = [x.strip() for x in w.text().split(",") if x.strip()] if k == "filler_words" else w.text().strip()
            elif isinstance(w, QCheckBox):
                s[k] = w.isChecked()
            elif isinstance(w, QComboBox):
                s[k] = w.currentData()
            elif isinstance(w, QSpinBox):
                s[k] = w.value()
            elif isinstance(w, QDoubleSpinBox):
                s[k] = w.value()
        settings_mod.update(**{k: s[k] for k in self.w})
        self.win.statusBar().showMessage("Settings saved", 3000)

    def connect_youtube(self):
        self.save()
        from ..upload import youtube
        try:
            name = youtube.connect(self.win.cfg.get("youtube_client_secret", ""))
            self.yt_status.setText(f"Connected: {name}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "YouTube", str(e))

    def test_instagram(self):
        self.save()
        from ..upload import instagram
        s = self.win.cfg
        try:
            name = instagram.check(s["instagram_user_id"], s["instagram_access_token"], s["instagram_api_version"])
            self.ig_status.setText(f"Connected: @{name}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Instagram", str(e))


# ======================================================================== Window
class _Bridge(QObject):
    event = Signal(dict)


NAV = [("create", "✨   New edit"), ("projects", "🗂   Projects"), ("review", "🎬   Review && publish"),
       ("remote", "📱   Phone app"), ("settings", "⚙️   Settings"), ("log", "📝   Log")]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"OnDaGo Editor {VERSION}")
        ico = ROOT / "app" / "gui" / "icon.ico"
        self.icon = QIcon(str(ico)) if ico.exists() else QIcon()
        self.setWindowIcon(self.icon)
        self.resize(1360, 880)
        self.setMinimumSize(980, 640)
        settings_mod.ensure_secret()
        self.cfg = settings_mod.load()
        self.current_job: Job | None = None
        self.logfile = open(DATA / "app.log", "a", encoding="utf-8")

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(228)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 18, 14, 14)
        sv.setSpacing(4)
        brand = QLabel("OnDaGo")
        brand.setObjectName("brand")
        sv.addWidget(brand)
        self.nav: dict[str, QPushButton] = {}
        for key, text in NAV:
            b = QPushButton(text)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            sv.addWidget(b)
            self.nav[key] = b
            if key == "review":
                sv.addSpacing(12)
        sv.addStretch()
        self.phone_pill = QPushButton("Phone app off")
        self.phone_pill.setObjectName("chip")
        self.phone_pill.setCursor(Qt.PointingHandCursor)
        self.phone_pill.setCheckable(True)
        self.phone_pill.clicked.connect(lambda: self.go("remote"))
        sv.addWidget(self.phone_pill, 0, Qt.AlignLeft)
        foot = QLabel(f"v{VERSION}  ·  runs offline on your PC")
        foot.setObjectName("navfoot")
        sv.addWidget(foot)
        h.addWidget(side)

        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.logview = QPlainTextEdit()
        self.logview.setObjectName("log")
        self.logview.setReadOnly(True)
        self.logview.setMaximumBlockCount(5000)
        self.projects = ProjectsPage(self)
        self.create = CreateTab(self)
        self.review = ReviewTab(self)
        self.settings = SettingsTab(self)
        self.remote = RemoteTab(self)
        logpage = QWidget()
        lv = QVBoxLayout(logpage)
        lv.setContentsMargins(28, 26, 28, 24)
        lv.setSpacing(8)
        t = QLabel("Log")
        t.setObjectName("h1")
        lv.addWidget(t)
        tip = QLabel("Everything the editor is doing, step by step. Handy if something goes wrong.")
        tip.setObjectName("muted")
        lv.addWidget(tip)
        lv.addWidget(self.logview, 1)
        self.pages = {"create": self.create, "projects": self.projects, "review": self.review,
                      "remote": self.remote, "settings": self.settings, "log": logpage}
        for w in self.pages.values():
            self.stack.addWidget(w)
        self.go("create")

        self.statusBar().showMessage("Ready - drop a video to start")
        self._quitting = False
        self._tray_hint = False
        # phone-app tasks -> refresh lists + tray messages (signal hops to the GUI thread)
        from ..core.tasks import MANAGER
        self.bridge = _Bridge()
        self.bridge.event.connect(self._remote_event)
        MANAGER.subscribe(self.bridge.event.emit)
        self.tray = None
        autostart_server(self)
        self.update_tray()

    def go(self, name: str):
        w = self.pages.get(name)
        if w is None:
            return
        self.stack.setCurrentWidget(w)
        for k, b in self.nav.items():
            b.setChecked(k == name)
        if name == "projects":
            self.projects.refresh()

    def log(self, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.logview.appendPlainText(line)
        try:
            self.logfile.write(line + "\n")
            self.logfile.flush()
        except Exception:
            pass

    def _remote_event(self, ev: dict):
        if ev.get("type") == "log":
            self.logview.appendPlainText(ev.get("line", ""))
            return
        t = ev.get("task", {})
        if t.get("state") in ("done", "failed"):
            self.projects.refresh()
            if self.review.job and self.review.job.name == t.get("job") and not (
                    self.review.task and self.review.task.isRunning()):
                self.review.load(Job(self.review.job.dir))
            if self.tray and not self.isVisible():
                self.tray.showMessage("OnDaGo", f"{t.get('label')} {'finished' if t['state'] == 'done' else 'failed'}",
                                      self.icon, 5000)

    def open_job(self, job: Job, focus: str | None = None):
        self.current_job = job
        self.review.load(job, focus)
        self.go("review")

    def notify(self, title: str, msg: str):
        self.statusBar().showMessage(f"{title}: {msg}", 15000)
        QApplication.alert(self)

    # ------------------------------------------------------------ tray / background
    def update_tray(self):
        from ..remote.server import get_server
        on = get_server().running()
        self.phone_pill.setText("●  Phone app on" if on else "○  Phone app off")
        self.phone_pill.setChecked(on)
        want = on and QSystemTrayIcon.isSystemTrayAvailable()
        if want and self.tray is None:
            self.tray = QSystemTrayIcon(self.icon, self)
            self.tray.setToolTip("OnDaGo Editor - phone app on")
            m = QMenu()
            a_open = QAction("Open OnDaGo", m)
            a_open.triggered.connect(self.show_front)
            a_copy = QAction("Copy phone link", m)
            a_copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(get_server().best_url()))
            a_quit = QAction("Quit (phone app stops)", m)
            a_quit.triggered.connect(self.quit_app)
            for a in (a_open, a_copy, a_quit):
                m.addAction(a)
            self.tray.setContextMenu(m)
            self.tray.activated.connect(lambda r: self.show_front() if r in (
                QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
            self._tray_menu = m
            self.tray.show()
        elif not want and self.tray is not None:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None

    def show_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        from ..remote.server import get_server
        if not self._quitting and self.tray and get_server().running() and settings_mod.load().get("close_to_tray", True):
            e.ignore()
            self.hide()
            if not self._tray_hint:
                self._tray_hint = True
                self.tray.showMessage("OnDaGo keeps running", "Your phone can still reach it. Right-click the tray "
                                      "icon to quit.", self.icon, 6000)
            return
        self.quit_app()
        e.accept()

    def quit_app(self):
        from ..remote.server import get_server
        self._quitting = True
        t = getattr(self.projects, "_cover_task", None)
        if t is not None and t.isRunning():
            t.wait(4000)
        try:
            get_server().stop()
        except Exception:  # noqa: BLE001
            pass
        QApplication.quit()


def run(background: bool = False):
    import hashlib

    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    app = QApplication(sys.argv)
    app.setApplicationName("OnDaGo Editor")
    app.setStyleSheet(QSS)
    # one OnDaGo at a time: a second launch just brings the first one to the front
    key = "OnDaGoEditor-" + hashlib.md5(str(ROOT).lower().encode()).hexdigest()[:10]
    sock = QLocalSocket()
    sock.connectToServer(key)
    if sock.waitForConnected(400):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(400)
        return
    QLocalServer.removeServer(key)
    single = QLocalServer()
    single.listen(key)
    app.setQuitOnLastWindowClosed(False)
    w = MainWindow()

    def on_conn():
        c = single.nextPendingConnection()
        if c:
            c.readyRead.connect(lambda: (c.readAll(), w.show_front()))
            w.show_front()
    single.newConnection.connect(on_conn)
    if background and w.tray is not None:
        w.tray.showMessage("OnDaGo is running", "The phone app is on. Open me from the tray icon.", w.icon, 5000)
    else:
        w.show()
    sys.exit(app.exec())
