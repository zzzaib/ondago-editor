from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import actions
from ..core import settings as settings_mod
from ..core.job import Job
from ..core.paths import DATA, IS_WIN
from .review import ReviewTab
from .theme import QSS
from .workers import Task

VIDEO_FILTER = "Videos (*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.MP4 *.MOV)"


def open_path(p: str | Path):
    p = str(p)
    if IS_WIN:
        os.startfile(p)  # noqa: S606
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(p))


# ======================================================================= Create
class DropZone(QFrame):
    def __init__(self, on_file):
        super().__init__()
        self.setObjectName("drop")
        self.on_file = on_file
        self.setAcceptDrops(True)
        self.setMinimumHeight(190)
        lay = QVBoxLayout(self)
        t = QLabel("Drop your raw video here")
        t.setObjectName("h1")
        t.setAlignment(Qt.AlignCenter)
        s = QLabel("or click to browse  -  MP4, MOV, MKV")
        s.setObjectName("muted")
        s.setAlignment(Qt.AlignCenter)
        lay.addStretch()
        lay.addWidget(t)
        lay.addWidget(s)
        lay.addStretch()
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        f, _ = QFileDialog.getOpenFileName(self, "Choose raw video", "", VIDEO_FILTER)
        if f:
            self.on_file(f)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setProperty("hover", True)
            self.style().polish(self)

    def dragLeaveEvent(self, e):
        self.setProperty("hover", False)
        self.style().polish(self)

    def dropEvent(self, e):
        self.setProperty("hover", False)
        self.style().polish(self)
        urls = [u.toLocalFile() for u in e.mimeData().urls()]
        if urls:
            self.on_file(urls[0])


def combo(items: list[tuple[str, str]], value: str) -> QComboBox:
    c = QComboBox()
    for label, key in items:
        c.addItem(label, key)
    i = c.findData(value)
    c.setCurrentIndex(max(0, i))
    return c


class CreateTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        s = win.cfg
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        root.addLayout(left, 3)
        self.drop = DropZone(self.set_file)
        left.addWidget(self.drop)
        self.file_label = QLabel("No video selected")
        self.file_label.setObjectName("muted")
        left.addWidget(self.file_label)

        opts = QGroupBox("What should I do?")
        g = QGridLayout(opts)
        self.c_fill = QCheckBox("Remove filler words (um, uh)")
        self.c_fill.setChecked(s["remove_fillers"])
        self.c_sil = QCheckBox("Remove silences")
        self.c_sil.setChecked(s["remove_silences"])
        self.c_ret = QCheckBox("Remove retakes / false starts")
        self.c_ret.setChecked(s["remove_retakes"])
        self.c_call = QCheckBox("Pop-up text callouts")
        self.c_call.setChecked(s["callouts"])
        self.c_bar = QCheckBox("Progress bar")
        self.c_bar.setChecked(s["progress_bar"])
        self.c_push = QCheckBox("Smooth camera push-ins")
        self.c_push.setChecked(s.get("smooth_zoom", True))
        self.c_sfx = QCheckBox("Sound effects (whoosh / pop)")
        self.c_sfx.setChecked(s.get("sfx", True))
        self.c_grade = QCheckBox("Colour grade (contrast + vibrance)")
        self.c_grade.setChecked(s.get("color_grade", True))
        self.script = combo([("Auto (Hinglish speech -> Roman Hinglish)", "auto"), ("Hinglish in Roman letters", "hinglish"),
                             ("Native script (e.g. Devanagari)", "native")], s.get("caption_script", "auto"))
        self.c_music = QCheckBox("Background music (ducked)")
        self.c_music.setChecked(s["music"])
        self.cap = combo([("Bold Pop (word highlight)", "bold_pop"), ("Clean box", "clean"), ("Minimal", "minimal"),
                          ("No captions", "off")], s["caption_style"] if s["captions"] else "off")
        self.broll = combo([("Offline + Online", "both"), ("Offline only (my library + AI)", "offline"),
                            ("Online only (Pexels / Pixabay)", "online"), ("No B-roll", "off")], s["broll_mode"])
        self.missing = combo([("Ask me + suggest what to use", "ask"), ("Use an animated text card", "card"),
                              ("Generate an AI image", "ai"), ("Skip that moment", "skip")], s["broll_missing_action"])
        self.zoom = combo([("Jump-cut punch-ins + emphasis", "jumpcut"), ("Emphasis zooms only", "emphasis"),
                           ("No zooms", "off")], s["zoom_style"])
        self.rot = combo([("Auto-detect (fixes sideways phone videos)", "auto"), ("As recorded", "0"),
                          ("Rotate 90\u00b0 right", "90"), ("Rotate 90\u00b0 left", "270"), ("Rotate 180\u00b0", "180")],
                         str(s.get("rotation", "auto")))
        self.o_yt = QCheckBox("YouTube 16:9")
        self.o_yt.setChecked(s["output_youtube"])
        self.o_reel = QCheckBox("Instagram Reel 9:16")
        self.o_reel.setChecked(s["output_reel"])
        self.reel_mode = combo([("Best highlight (AI picks)", "highlight"), ("Full video", "full")], s["reel_mode"])
        self.c_review = QCheckBox("Let me review before rendering")
        self.c_review.setChecked(s.get("review_before_render", False))
        r = 0
        for w in (self.c_fill, self.c_sil, self.c_ret):
            g.addWidget(w, r, 0, 1, 2)
            r += 1
        g.addWidget(QLabel("Captions"), r, 0); g.addWidget(self.cap, r, 1); r += 1
        g.addWidget(QLabel("B-roll"), r, 0); g.addWidget(self.broll, r, 1); r += 1
        g.addWidget(QLabel("If no B-roll fits"), r, 0); g.addWidget(self.missing, r, 1); r += 1
        g.addWidget(QLabel("Zoom"), r, 0); g.addWidget(self.zoom, r, 1); r += 1
        g.addWidget(QLabel("Orientation"), r, 0); g.addWidget(self.rot, r, 1); r += 1
        g.addWidget(QLabel("Caption script"), r, 0); g.addWidget(self.script, r, 1); r += 1
        for w in (self.c_call, self.c_bar, self.c_push, self.c_sfx, self.c_grade, self.c_music):
            g.addWidget(w, r, 0, 1, 2)
            r += 1
        outs = QHBoxLayout()
        outs.addWidget(self.o_yt)
        outs.addWidget(self.o_reel)
        outs.addWidget(self.reel_mode)
        g.addWidget(QLabel("Outputs"), r, 0); g.addLayout(outs, r, 1); r += 1
        g.addWidget(self.c_review, r, 0, 1, 2)
        left.addWidget(opts)

        self.start = QPushButton("Auto-Edit My Video")
        self.start.setObjectName("primary")
        self.start.setEnabled(False)
        self.start.clicked.connect(self.run)
        left.addWidget(self.start)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        left.addWidget(self.bar)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        left.addWidget(self.status)
        left.addStretch()

        right = QVBoxLayout()
        root.addLayout(right, 2)
        right.addWidget(QLabel("Recent projects (double-click to open)"))
        self.recent = QListWidget()
        self.recent.itemDoubleClicked.connect(lambda it: self.win.open_job(Job(it.data(Qt.UserRole))))
        right.addWidget(self.recent)
        self.refresh_recent()
        self.path: str | None = None
        self.task: Task | None = None

    def refresh_recent(self):
        self.recent.clear()
        for j in Job.list_recent():
            stage = j.plan.get("stage", "?")
            it = QListWidgetItem(f"{Path(j.plan.get('source', '')).name}   [{stage}]   "
                                 f"{time.strftime('%d %b %H:%M', time.localtime(j.plan.get('created', 0)))}")
            it.setData(Qt.UserRole, str(j.dir))
            self.recent.addItem(it)

    def set_file(self, f: str):
        self.path = f
        self.file_label.setText(f)
        self.start.setEnabled(True)

    def collect(self) -> dict:
        s = self.win.cfg
        cap = self.cap.currentData()
        s.update({
            "remove_fillers": self.c_fill.isChecked(), "remove_silences": self.c_sil.isChecked(),
            "remove_retakes": self.c_ret.isChecked(), "callouts": self.c_call.isChecked(),
            "progress_bar": self.c_bar.isChecked(), "music": self.c_music.isChecked(),
            "smooth_zoom": self.c_push.isChecked(), "sfx": self.c_sfx.isChecked(),
            "color_grade": self.c_grade.isChecked(), "caption_script": self.script.currentData(),
            "captions": cap != "off", "caption_style": cap if cap != "off" else s["caption_style"],
            "broll_mode": self.broll.currentData(), "broll_missing_action": self.missing.currentData(),
            "zoom_style": self.zoom.currentData(), "rotation": self.rot.currentData(),
            "output_youtube": self.o_yt.isChecked(),
            "output_reel": self.o_reel.isChecked(), "reel_mode": self.reel_mode.currentData(),
            "review_before_render": self.c_review.isChecked(),
        })
        settings_mod.save(s)
        return s

    def run(self):
        if not self.path or not Path(self.path).exists():
            return
        cfg = self.collect()
        if not (cfg["output_youtube"] or cfg["output_reel"]):
            QMessageBox.warning(self, "Nothing to export", "Pick at least one output.")
            return
        job = Job.create(self.path)
        self.win.current_job = job
        self.start.setEnabled(False)
        self.task = Task(actions.full_auto, job, cfg)
        self.task.progress.connect(self.on_progress)
        self.task.log.connect(self.win.log)
        self.task.done.connect(lambda stage: self.finished(job, stage))
        self.task.failed.connect(self.failed)
        self.task.start()

    def on_progress(self, p: float, msg: str):
        self.bar.setValue(int(p * 1000))
        if msg:
            self.status.setText(msg)

    def finished(self, job: Job, stage: str):
        self.start.setEnabled(True)
        self.bar.setValue(1000)
        self.refresh_recent()
        job = Job(job.dir)
        if stage == "rendered":
            self.status.setText("Done! Your videos, thumbnail and metadata are ready.")
            self.win.notify("Edit finished", f"Saved to {job.plan.get('export_dir', '')}")
        elif stage == "needs_broll":
            n = sum(1 for s in job.plan.get("broll", []) if s["status"] == "needs_input")
            self.status.setText(f"{n} moment(s) need B-roll from you - see the Review tab.")
            self.win.notify("Your input needed", f"I couldn't find B-roll for {n} moment(s). "
                            "I've suggested what to use - add a file, search, generate, or skip.")
        else:
            self.status.setText("Analysis ready - review and press Render.")
        self.win.open_job(job, focus="broll" if stage == "needs_broll" else None)

    def failed(self, err: str):
        self.start.setEnabled(True)
        self.status.setText("Failed: " + err[:200])
        QMessageBox.critical(self, "Something went wrong", err[:2000] + "\n\nSee the Log tab for details.")


# ===================================================================== Settings
class SettingsTab(QScrollArea):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        lay = QVBoxLayout(body)
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
        settings_mod.save(s)
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
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OnDaGo Editor")
        self.resize(1380, 900)
        self.cfg = settings_mod.load()
        self.current_job: Job | None = None
        self.logfile = open(DATA / "app.log", "a", encoding="utf-8")
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.create = CreateTab(self)
        self.review = ReviewTab(self)
        self.settings = SettingsTab(self)
        self.logview = QPlainTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setMaximumBlockCount(5000)
        self.tabs.addTab(self.create, "  Create  ")
        self.tabs.addTab(self.review, "  Review && Publish  ")
        self.tabs.addTab(self.settings, "  Settings  ")
        self.tabs.addTab(self.logview, "  Log  ")
        self.statusBar().showMessage("Ready - drop a video to start")

    def log(self, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.logview.appendPlainText(line)
        try:
            self.logfile.write(line + "\n")
            self.logfile.flush()
        except Exception:
            pass

    def open_job(self, job: Job, focus: str | None = None):
        self.current_job = job
        self.review.load(job, focus)
        self.tabs.setCurrentWidget(self.review)

    def notify(self, title: str, msg: str):
        self.statusBar().showMessage(f"{title}: {msg}", 15000)
        QApplication.alert(self)


def run():
    app = QApplication(sys.argv)
    app.setApplicationName("OnDaGo Editor")
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
