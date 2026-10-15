"""Create (drop a video -> pick a style -> go) and Projects pages of the desktop app."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from .. import actions
from ..core import presets as presets_mod
from ..core import settings as settings_mod
from ..core.job import Job
from ..core.paths import DATA
from .workers import Task

VIDEO_FILTER = "Videos (*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.MP4 *.MOV)"
STEPS = [("Understanding", 0.17), ("Planning", 0.30), ("B-roll & thumbnails", 0.55), ("Rendering", 0.985), ("Done", 1.01)]


def combo(items: list[tuple[str, str]], value) -> QComboBox:
    c = QComboBox()
    for label, key in items:
        c.addItem(label, key)
    c.setCurrentIndex(max(0, c.findData(value)))
    return c


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def fmt_dur(s: float) -> str:
    s = int(s or 0)
    return f"{s // 60}:{s % 60:02d}"


# ------------------------------------------------------------------ widgets
class DropZone(QFrame):
    def __init__(self, on_file):
        super().__init__()
        self.setObjectName("drop")
        self.on_file = on_file
        self.setAcceptDrops(True)
        self.setMinimumHeight(170)
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.addStretch()
        for text, name in (("🎬", "h1"), ("Drop your raw video here", "h2"), ("or click to choose  ·  MP4, MOV, MKV", "muted")):
            lb = label(text, name)
            lb.setAlignment(Qt.AlignCenter)
            lay.addWidget(lb)
        lay.addStretch()

    def mousePressEvent(self, e):
        f, _ = QFileDialog.getOpenFileName(self, "Choose raw video", "", VIDEO_FILTER)
        if f:
            self.on_file(f)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, e):
        self._hover(False)

    def dropEvent(self, e):
        self._hover(False)
        urls = [u.toLocalFile() for u in e.mimeData().urls()]
        if urls:
            self.on_file(urls[0])

    def _hover(self, on: bool):
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)


class PresetCard(QFrame):
    clicked = Signal(str)

    def __init__(self, p: dict):
        super().__init__()
        self.pid = p["id"]
        self.setObjectName("preset")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(112)
        self.setMaximumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)
        lay.addWidget(label(f'{p["emoji"]}  {p["title"]}', "h2"))
        lay.addWidget(label(p["desc"], "small", True))
        lay.addStretch()

    def mousePressEvent(self, e):
        self.clicked.emit(self.pid)

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)


class Stepper(QWidget):
    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.labels = []
        for i, (name, _) in enumerate(STEPS):
            lb = label(f"{i + 1}  {name}", "pill")
            lay.addWidget(lb)
            self.labels.append(lb)
        lay.addStretch()

    def set_progress(self, p: float):
        cur = next((i for i, (_, end) in enumerate(STEPS) if p < end), len(STEPS) - 1)
        for i, lb in enumerate(self.labels):
            name = STEPS[i][0]
            lb.setObjectName("pillAccent" if i <= cur else "pill")
            lb.setText(f"{'✓' if i < cur else i + 1}  {name}")
            lb.style().unpolish(lb)
            lb.style().polish(lb)


# ------------------------------------------------------------------ Create
class CreateTab(QScrollArea):
    """Drop a video, pick a style, press one button. Everything else lives under 'Advanced options'."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        outer = QHBoxLayout(body)
        outer.addStretch(1)
        col = QVBoxLayout()
        col.setContentsMargins(0, 26, 0, 36)
        col.setSpacing(14)
        outer.addLayout(col, 12)
        outer.addStretch(1)
        s = win.cfg
        self.path: str | None = None
        self.task: Task | None = None
        self.last_job: Job | None = None

        col.addWidget(label("New edit", "h1"))
        col.addWidget(label("Drop a raw recording. OnDaGo cuts it, adds captions, B-roll and doodles, and makes a "
                            "YouTube video and a Reel.", "muted", True))

        # ---- source
        self.drop = DropZone(self.set_file)
        col.addWidget(self.drop)
        self.filecard = QFrame()
        self.filecard.setObjectName("filecard")
        fl = QHBoxLayout(self.filecard)
        self.thumb = QLabel()
        self.thumb.setFixedSize(128, 72)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background:#0F0F16;border-radius:8px;font-size:22pt;")
        fl.addWidget(self.thumb)
        info = QVBoxLayout()
        self.fname = label("", "h2")
        self.fmeta = label("", "muted")
        info.addWidget(self.fname)
        info.addWidget(self.fmeta)
        fl.addLayout(info, 1)
        ch = QPushButton("Change video")
        ch.clicked.connect(self.drop.mousePressEvent)
        fl.addWidget(ch)
        self.filecard.hide()
        col.addWidget(self.filecard)

        # ---- style presets
        hdr = QHBoxLayout()
        hdr.addWidget(label("Style", "h2"))
        self.preset_state = label("", "pillAccent")
        hdr.addWidget(self.preset_state)
        hdr.addStretch()
        col.addLayout(hdr)
        grid = QGridLayout()
        grid.setSpacing(10)
        self.cards: dict[str, PresetCard] = {}
        for i, p in enumerate(presets_mod.PRESETS):
            c = PresetCard(p)
            c.clicked.connect(self.apply_preset)
            grid.addWidget(c, i // 3, i % 3)
            self.cards[p["id"]] = c
        col.addLayout(grid)

        # ---- outputs
        col.addWidget(label("Make", "h2"))
        orow = QHBoxLayout()
        self.o_yt = QPushButton("▶  YouTube  16:9")
        self.o_reel = QPushButton("📱  Instagram Reel  9:16")
        for b, k in ((self.o_yt, "output_youtube"), (self.o_reel, "output_reel")):
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setChecked(bool(s.get(k, True)))
            orow.addWidget(b)
        self.reel_mode = combo([("Reel: full if under 3 min", "auto"), ("Reel: best highlight", "highlight"),
                                ("Reel: full video", "full")], s.get("reel_mode", "auto"))
        orow.addWidget(self.reel_mode)
        orow.addStretch()
        col.addLayout(orow)

        # ---- advanced
        self.adv_btn = QPushButton("Advanced options  ▾")
        self.adv_btn.setObjectName("ghost")
        self.adv_btn.setCheckable(True)
        self.adv_btn.toggled.connect(self._toggle_adv)
        ar = QHBoxLayout()
        ar.addWidget(self.adv_btn)
        ar.addStretch()
        col.addLayout(ar)
        self.adv = QWidget()
        self.adv.hide()
        self._build_advanced(s)
        col.addWidget(self.adv)

        # ---- go
        self.start = QPushButton("✨  Edit my video")
        self.start.setObjectName("primary")
        self.start.setEnabled(False)
        self.start.setMinimumHeight(52)
        self.start.clicked.connect(self.run)
        col.addWidget(self.start)

        # ---- progress
        self.prog = QFrame()
        self.prog.setObjectName("panel")
        pl = QVBoxLayout(self.prog)
        pl.setContentsMargins(16, 14, 16, 14)
        top = QHBoxLayout()
        self.p_title = label("Editing…", "h2")
        self.p_time = label("", "muted")
        top.addWidget(self.p_title, 1)
        top.addWidget(self.p_time)
        pl.addLayout(top)
        self.stepper = Stepper()
        pl.addWidget(self.stepper)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        pl.addWidget(self.bar)
        self.status = label("", "muted", True)
        pl.addWidget(self.status)
        self.done_row = QHBoxLayout()
        self.b_watch = QPushButton("▶  Review && publish")
        self.b_watch.setObjectName("primary")
        self.b_watch.clicked.connect(lambda: self.last_job and self.win.open_job(self.last_job))
        self.b_folder = QPushButton("📂  Open output folder")
        self.b_folder.clicked.connect(self._open_out)
        self.done_row.addWidget(self.b_watch)
        self.done_row.addWidget(self.b_folder)
        self.done_row.addStretch()
        pl.addLayout(self.done_row)
        self.prog.hide()
        col.addWidget(self.prog)
        col.addStretch()
        self._t0 = 0.0
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self.sync_preset()

    # ------------------------------------------------------------ advanced options
    def _build_advanced(self, s: dict):
        grid = QGridLayout(self.adv)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        def group(title: str):
            fr = QFrame()
            fr.setObjectName("card")
            v = QVBoxLayout(fr)
            v.setContentsMargins(14, 12, 14, 12)
            v.addWidget(label(title, "h2"))
            return fr, v

        def chk(text, key):
            c = QCheckBox(text)
            c.setChecked(bool(s.get(key)))
            c.toggled.connect(self.sync_preset)
            return c

        def row(v, text, w):
            h = QHBoxLayout()
            h.addWidget(label(text, "muted"))
            h.addStretch()
            h.addWidget(w)
            if isinstance(w, QComboBox):
                w.setMinimumWidth(180)
                w.currentIndexChanged.connect(self.sync_preset)
            v.addLayout(h)

        fr, v = group("✂️  Clean-up")
        self.c_fill = chk("Remove um / uh", "remove_fillers")
        self.c_sil = chk("Remove silences", "remove_silences")
        self.c_ret = chk("Remove retakes (only when repeated)", "remove_retakes")
        for w in (self.c_fill, self.c_sil, self.c_ret):
            v.addWidget(w)
        self.rot = combo([("Auto-fix", "auto"), ("As recorded", "0"), ("Rotate right", "90"), ("Rotate left", "270"),
                          ("Upside down", "180")], str(s.get("rotation", "auto")))
        row(v, "Orientation", self.rot)
        self.lang = combo([("Auto detect", "auto"), ("Hinglish", "hinglish"), ("English", "en"), ("Hindi", "hi"),
                           ("Urdu", "ur"), ("Spanish", "es"), ("German", "de"), ("French", "fr")], s.get("language", "auto"))
        row(v, "Language", self.lang)
        v.addStretch()
        grid.addWidget(fr, 0, 0)

        fr, v = group("🎨  Look")
        self.cap = combo([("Bold pop", "bold_pop"), ("Clean box", "clean"), ("Minimal", "minimal"), ("No captions", "off")],
                         s["caption_style"] if s.get("captions", True) else "off")
        row(v, "Captions", self.cap)
        self.script = combo([("Auto", "auto"), ("Hinglish (Roman)", "hinglish"), ("Native script", "native")],
                            s.get("caption_script", "auto"))
        row(v, "Caption script", self.script)
        self.zoom = combo([("Punch-ins", "jumpcut"), ("Emphasis only", "emphasis"), ("No zooms", "off")],
                          s.get("zoom_style", "jumpcut"))
        row(v, "Zooms", self.zoom)
        self.c_push = chk("Smooth camera push-ins", "smooth_zoom")
        self.c_grade = chk("Colour grade", "color_grade")
        self.c_call = chk("Pop-up text", "callouts")
        self.c_bar = chk("Progress bar", "progress_bar")
        self.c_sfx = chk("Sound effects", "sfx")
        self.c_music = chk("Background music", "music")
        for w in (self.c_push, self.c_grade, self.c_call, self.c_bar, self.c_sfx, self.c_music):
            v.addWidget(w)
        self.c_vox = chk("Vox-style explainer graphics", "vox_graphics")
        self.c_vox.setToolTip("Paper-texture title cards, highlighted quotes, counting numbers, maps and timelines "
                              "(12 fps cut-out look). Runs on any computer.")
        v.addWidget(self.c_vox)
        self.vdens = combo([("A few", "low"), ("Some", "medium"), ("Lots", "high")], s.get("vox_density", "medium"))
        row(v, "Explainer graphics", self.vdens)
        self.vdens.setEnabled(self.c_vox.isChecked())
        self.c_vox.toggled.connect(self.vdens.setEnabled)
        grid.addWidget(fr, 0, 1)

        fr, v = group("✨  Doodles")
        self.c_dood = chk("Animated doodles", "doodles")
        self.dens = combo([("A few", "low"), ("Some", "medium"), ("Lots", "high")], s.get("doodle_density", "medium"))
        self.c_outline = chk("Glowing outline on key moments", "doodle_outline")
        self.c_cdood = chk("Underline / circle pop-up text", "callout_doodles")
        self.c_ai = chk("AI custom doodles (RTX GPUs)", "doodle_ai")
        self.c_ai.setEnabled(False)
        self.ai_ok = False
        v.addWidget(self.c_dood)
        row(v, "How many", self.dens)
        for w in (self.c_outline, self.c_cdood, self.c_ai):
            v.addWidget(w)
        self.c_dood.toggled.connect(self._dood_enable)
        v.addStretch()
        grid.addWidget(fr, 1, 0)

        fr, v = group("🎞️  B-roll")
        self.broll = combo([("Library + online", "both"), ("Offline only", "offline"), ("Online only", "online"),
                            ("No B-roll", "off")], s.get("broll_mode", "both"))
        row(v, "Source", self.broll)
        self.missing = combo([("Decide for me", "auto"), ("Ask me", "ask"), ("Text card", "card"), ("AI image", "ai"),
                              ("Skip", "skip")], s.get("broll_missing_action", "auto"))
        row(v, "If nothing fits", self.missing)
        self.blayout = combo([("Auto", "auto"), ("Always split-screen", "split"), ("Full-screen cutaways", "full")],
                             s.get("broll_layout", "auto"))
        row(v, "Layout", self.blayout)
        self.c_review = QCheckBox("Let me review before rendering")
        self.c_review.setChecked(bool(s.get("review_before_render", False)))
        v.addWidget(self.c_review)
        v.addStretch()
        grid.addWidget(fr, 1, 1)
        QTimer.singleShot(1500, self._check_ai)

    def _toggle_adv(self, on: bool):
        self.adv.setVisible(on)
        self.adv_btn.setText("Advanced options  ▴" if on else "Advanced options  ▾")

    def _dood_enable(self, on: bool):
        for w in (self.dens, self.c_outline, self.c_cdood):
            w.setEnabled(on)
        self.c_ai.setEnabled(on and self.ai_ok)

    def _check_ai(self):
        try:
            from ..pipeline.broll.generate import available
            ok, why = available()
        except Exception as e:  # noqa: BLE001
            ok, why = False, str(e)
        self.ai_ok = ok
        self.c_ai.setEnabled(ok and self.c_dood.isChecked())
        if not ok:
            self.c_ai.setChecked(False)
            self.c_ai.setToolTip(f"Not available on this PC: {why}")

    # ------------------------------------------------------------ presets
    def values(self) -> dict:
        cap = self.cap.currentData()
        return {
            "remove_fillers": self.c_fill.isChecked(), "remove_silences": self.c_sil.isChecked(),
            "remove_retakes": self.c_ret.isChecked(), "captions": cap != "off",
            "caption_style": cap if cap != "off" else self.win.cfg.get("caption_style", "bold_pop"),
            "caption_script": self.script.currentData(), "zoom_style": self.zoom.currentData(),
            "smooth_zoom": self.c_push.isChecked(), "callouts": self.c_call.isChecked(),
            "progress_bar": self.c_bar.isChecked(), "sfx": self.c_sfx.isChecked(), "color_grade": self.c_grade.isChecked(),
            "music": self.c_music.isChecked(), "broll_mode": self.broll.currentData(),
            "broll_missing_action": self.missing.currentData(), "broll_layout": self.blayout.currentData(),
            "doodles": self.c_dood.isChecked(), "doodle_density": self.dens.currentData(),
            "doodle_outline": self.c_outline.isChecked(), "callout_doodles": self.c_cdood.isChecked(),
            "doodle_ai": self.c_ai.isChecked() and self.ai_ok, "rotation": self.rot.currentData(),
            "vox_graphics": self.c_vox.isChecked(), "vox_density": self.vdens.currentData(),
            "language": self.lang.currentData(), "output_youtube": self.o_yt.isChecked(),
            "output_reel": self.o_reel.isChecked(), "reel_mode": self.reel_mode.currentData(),
            "review_before_render": self.c_review.isChecked(),
            "max_pause": self.win.cfg.get("max_pause", 0.45),
        }

    def apply_preset(self, pid: str):
        p = presets_mod.BY_ID[pid]["options"]
        self._applying = True
        cb = {"remove_fillers": self.c_fill, "remove_silences": self.c_sil, "remove_retakes": self.c_ret,
              "smooth_zoom": self.c_push, "callouts": self.c_call, "progress_bar": self.c_bar, "sfx": self.c_sfx,
              "color_grade": self.c_grade, "music": self.c_music, "doodles": self.c_dood,
              "doodle_outline": self.c_outline, "callout_doodles": self.c_cdood, "vox_graphics": self.c_vox}
        for k, w in cb.items():
            if k in p:
                w.setChecked(bool(p[k]))
        for k, w in (("zoom_style", self.zoom), ("broll_mode", self.broll), ("broll_missing_action", self.missing),
                     ("broll_layout", self.blayout), ("doodle_density", self.dens), ("vox_density", self.vdens)):
            if k in p:
                w.setCurrentIndex(max(0, w.findData(p[k])))
        self.cap.setCurrentIndex(max(0, self.cap.findData(p.get("caption_style", "bold_pop") if p.get("captions", True)
                                                            else "off")))
        if "max_pause" in p:
            self.win.cfg["max_pause"] = p["max_pause"]
        else:
            self.win.cfg["max_pause"] = settings_mod.defaults().get("max_pause", 0.45)
        self._applying = False
        self.sync_preset()

    def sync_preset(self, *_):
        if getattr(self, "_applying", False) or not hasattr(self, "c_review"):
            return
        cur = presets_mod.detect(self.values())
        for pid, c in self.cards.items():
            c.set_selected(pid == cur)
        self.preset_state.setText("Custom" if cur == "custom" else "")
        self.preset_state.setVisible(cur == "custom")

    # ------------------------------------------------------------ file
    def set_file(self, f: str):
        self.path = f
        self.drop.hide()
        self.filecard.show()
        self.fname.setText(Path(f).name)
        self.fmeta.setText("Reading video…")
        self.thumb.setText("🎞️")
        self.start.setEnabled(True)
        t = Task(_probe_and_thumb, f)
        t.done.connect(self._file_info)
        t.failed.connect(lambda e: self.fmeta.setText("Couldn't read this file"))
        self._probe = t
        t.start()

    def _file_info(self, res):
        info, thumb = res
        if not info:
            return
        shape = "vertical" if info["height"] > info["width"] else "landscape"
        self.fmeta.setText(f"{fmt_dur(info['duration'])}  ·  {info['width']}×{info['height']} {shape}  ·  "
                           f"{info['fps']:.0f} fps")
        if thumb:
            pm = QPixmap(thumb)
            if not pm.isNull():
                self.thumb.setPixmap(pm.scaled(128, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ------------------------------------------------------------ run
    def collect(self) -> dict:
        vals = self.values()
        vals["preset"] = presets_mod.detect(vals)
        self.win.cfg.update(vals)
        settings_mod.update(**vals)
        return self.win.cfg

    def run(self):
        if not self.path or not Path(self.path).exists():
            return
        cfg = self.collect()
        if not (cfg["output_youtube"] or cfg["output_reel"]):
            QMessageBox.warning(self, "Nothing to make", "Pick YouTube, Reel or both.")
            return
        job = Job.create(self.path)
        self.win.current_job = self.last_job = job
        self.start.setEnabled(False)
        self.start.setText("Editing…")
        self.prog.show()
        self.p_title.setText(f"Editing {Path(self.path).name}")
        for i in range(self.done_row.count()):
            w = self.done_row.itemAt(i).widget()
            if w:
                w.hide()
        self.bar.setValue(0)
        self.stepper.set_progress(0)
        self._t0 = time.time()
        self._clock.start(1000)
        self.task = Task(actions.full_auto, job, cfg)
        self.task.progress.connect(self.on_progress)
        self.task.log.connect(self.win.log)
        self.task.done.connect(lambda stage: self.finished(job, stage))
        self.task.failed.connect(self.failed)
        self.task.start()
        QTimer.singleShot(200, lambda: self.ensureWidgetVisible(self.prog))

    def _tick(self):
        el = int(time.time() - self._t0)
        self.p_time.setText(f"{el // 60}:{el % 60:02d}")

    def on_progress(self, p: float, msg: str):
        self.bar.setValue(int(p * 1000))
        self.stepper.set_progress(p)
        if msg:
            self.status.setText(msg)

    def _end(self):
        self._clock.stop()
        self.start.setEnabled(True)
        self.start.setText("✨  Edit my video")

    def finished(self, job: Job, stage: str):
        self._end()
        job = Job(job.dir)
        self.last_job = job
        self.win.projects.refresh()
        self.bar.setValue(1000)
        if stage == "rendered":
            self.stepper.set_progress(1.0)
            self.p_title.setText("Your edit is ready 🎉")
            self.status.setText(f"Saved to {job.plan.get('export_dir', '')}")
            self.b_watch.show()
            self.b_folder.show()
            self.win.notify("Edit finished", f"Saved to {job.plan.get('export_dir', '')}")
        else:
            n = sum(1 for s in job.plan.get("broll", []) if s["status"] == "needs_input")
            self.p_title.setText("Your input is needed" if n else "Ready for your review")
            self.status.setText(f"{n} moment(s) need B-roll - choose in Review." if n else "Check it and press Render.")
            self.b_watch.show()
            self.win.open_job(job, focus="broll" if n else None)

    def failed(self, err: str):
        self._end()
        self.p_title.setText("Something went wrong")
        self.status.setText(err[:300] + "  (details in Log)")

    def _open_out(self):
        from .main_window import open_path
        d = self.last_job and Job(self.last_job.dir).plan.get("export_dir")
        if d and Path(d).exists():
            open_path(d)

    def refresh_recent(self):  # kept for older callers
        self.win.projects.refresh()


def _probe_and_thumb(path: str, log, progress):
    from ..core import ffmpeg
    info = ffmpeg.probe(path)
    out = DATA / "pick_thumb.jpg"
    try:
        ffmpeg.run(["-ss", f"{min(2.0, info['duration'] / 3):.2f}", "-i", path, "-frames:v", "1", "-vf", "scale=256:-2",
                    str(out)])
        thumb = str(out)
    except Exception:  # noqa: BLE001
        thumb = ""
    return info, thumb


# ------------------------------------------------------------------ Projects
class ProjectsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        top = QHBoxLayout()
        top.addWidget(label("Projects", "h1"))
        top.addStretch()
        nb = QPushButton("＋  New edit")
        nb.setObjectName("primary")
        nb.clicked.connect(lambda: win.go("create"))
        top.addWidget(nb)
        lay.addLayout(top)
        lay.addWidget(label("Double-click a project to review, re-render or publish it.", "muted"))
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(256, 144))
        self.list.setGridSize(QSize(290, 214))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setSpacing(10)
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(lambda it: self.win.open_job(Job(it.data(Qt.UserRole))))
        lay.addWidget(self.list, 1)
        self.empty = label("No projects yet. Go to New edit and drop a video.", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(240)
        lay.addWidget(self.empty, 1)
        self._tried: set[str] = set()
        self._cover_task: Task | None = None

    def refresh(self):
        self.list.clear()
        jobs = Job.list_recent(60)
        self.empty.setVisible(not jobs)
        self.list.setVisible(bool(jobs))
        stage_txt = {"rendered": "✅ Done", "needs_broll": "⚠️ Needs you", "analyzed": "📝 Ready to review",
                     "new": "⏳ In progress"}
        missing = []
        for j in jobs:
            p = j.plan
            th = p.get("thumbnails", {}).get("options", [])
            icon_path = th[min(p["thumbnails"].get("choice", 0), len(th) - 1)]["path"] if th else ""
            if not icon_path or not Path(icon_path).exists():
                cover = j.dir / "cover.jpg"
                icon_path = str(cover) if cover.exists() else ""
                if not icon_path and str(j.dir) not in self._tried:
                    missing.append(str(j.dir))
            name = Path(p.get("source", "")).name or j.name
            dur = p.get("edit_duration") or p.get("media", {}).get("duration", 0)
            it = QListWidgetItem(f"{name}\n{stage_txt.get(p.get('stage', ''), p.get('stage', ''))}  ·  {fmt_dur(dur)}  ·  "
                                 f"{time.strftime('%d %b', time.localtime(p.get('created', 0)))}")
            it.setIcon(QIcon(_tile(icon_path)))
            it.setToolTip(str(j.dir))
            it.setData(Qt.UserRole, str(j.dir))
            self.list.addItem(it)
        if missing and not (self._cover_task and self._cover_task.isRunning()):
            self._tried.update(missing)
            t = Task(_make_covers, missing)
            t.done.connect(lambda n: n and self.refresh())
            self._cover_task = t
            t.start()


TILE_W, TILE_H = 256, 144


def _tile(path: str) -> QPixmap:
    """Project card image: the frame fitted (not cropped) on a dark tile, or a placeholder."""
    from PySide6.QtGui import QColor, QFont, QPainter
    tile = QPixmap(TILE_W, TILE_H)
    tile.fill(QColor("#171722"))
    src = QPixmap(path) if path and Path(path).exists() else QPixmap()
    qp = QPainter(tile)
    qp.setRenderHint(QPainter.SmoothPixmapTransform)
    if src.isNull():
        f = QFont()
        f.setPointSize(26)
        qp.setFont(f)
        qp.setPen(QColor("#4A4A66"))
        qp.drawText(tile.rect(), Qt.AlignCenter, "🎬")
    else:
        sc = src.scaled(TILE_W, TILE_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        qp.drawPixmap((TILE_W - sc.width()) // 2, (TILE_H - sc.height()) // 2, sc)
    qp.end()
    return tile


def _make_covers(dirs: list[str], log, progress) -> int:
    from ..core import covers
    return covers.make_many(dirs)
