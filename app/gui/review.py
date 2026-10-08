"""Review & Publish: preview, fix B-roll gaps (with suggestions), toggle cuts, edit text, pick thumbnail, upload."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDateTime, QSize, Qt, QUrl
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDateTimeEdit, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSplitter,
                               QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .. import actions
from ..core import settings as settings_mod
from ..core.job import Job
from ..pipeline import runner
from ..pipeline.broll import make_card, set_user_file
from ..pipeline.planner import fmt_time
from .workers import Task

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    HAVE_MM = True
except Exception:  # noqa: BLE001
    HAVE_MM = False


def pix(path: str, w: int, h: int) -> QPixmap | None:
    if not path or not Path(path).exists():
        return None
    p = QPixmap(path)
    return None if p.isNull() else p.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


# ===================================================================== Player
class Player(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.src = QComboBox()
        top.addWidget(QLabel("Preview:"))
        top.addWidget(self.src, 1)
        lay.addLayout(top)
        self.paths: dict[str, str] = {}
        if HAVE_MM:
            self.video = QVideoWidget()
            self.video.setMinimumSize(480, 270)
            self.player = QMediaPlayer()
            self.audio = QAudioOutput()
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.video)
            lay.addWidget(self.video, 1)
        else:
            lay.addWidget(QLabel("Video preview unavailable (Qt Multimedia missing)."))
            self.player = None
        ctr = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._scrub)
        self.time = QLabel("0:00 / 0:00")
        ctr.addWidget(self.play_btn)
        ctr.addWidget(self.slider, 1)
        ctr.addWidget(self.time)
        lay.addLayout(ctr)
        self.src.currentIndexChanged.connect(self._load)
        if self.player:
            self.player.positionChanged.connect(self._pos)

    def set_sources(self, paths: dict[str, str]):
        self.paths = {k: v for k, v in paths.items() if v and Path(v).exists()}
        self.src.blockSignals(True)
        self.src.clear()
        for k in self.paths:
            self.src.addItem(k)
        self.src.blockSignals(False)
        prefer = next((k for k in ("YouTube edit", "Reel") if k in self.paths), None)
        if prefer:
            self.src.setCurrentText(prefer)
        self._load()

    def _load(self):
        if not self.player or not self.paths:
            return
        self.player.setSource(QUrl.fromLocalFile(self.paths.get(self.src.currentText(), "")))

    def toggle(self):
        if not self.player:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setText("Play")
        else:
            self.player.play()
            self.play_btn.setText("Pause")

    def _pos(self, ms: int):
        d = max(1, self.player.duration())
        if not self.slider.isSliderDown():
            self.slider.setValue(int(ms * 1000 / d))
        self.time.setText(f"{fmt_time(ms / 1000)} / {fmt_time(d / 1000)}")

    def _scrub(self, v: int):
        if self.player:
            self.player.setPosition(int(self.player.duration() * v / 1000))

    def seek_source(self, t: float):
        if not self.player:
            return
        if self.src.currentText() != "Raw video" and "Raw video" in self.paths:
            self.src.setCurrentText("Raw video")
        self.player.setPosition(int(max(0, t - 1.0) * 1000))
        self.player.play()
        self.play_btn.setText("Pause")

    def current_source_time(self) -> float | None:
        if self.player and self.src.currentText() == "Raw video":
            return self.player.position() / 1000
        return None

    def release(self):
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())


# ================================================================== Slot card
class SlotCard(QFrame):
    def __init__(self, tab: "ReviewTab", slot: dict):
        super().__init__()
        self.tab, self.slot = tab, slot
        need = slot["status"] == "needs_input"
        self.setObjectName("cardWarn" if need else "card")
        lay = QHBoxLayout(self)
        prev = QLabel()
        prev.setFixedSize(200, 113)
        prev.setAlignment(Qt.AlignCenter)
        pm = pix(slot.get("preview", ""), 200, 113)
        if pm:
            prev.setPixmap(pm)
        else:
            prev.setText("no preview" if slot["status"] == "resolved" else "B-roll\nneeded")
            prev.setStyleSheet("background:#101018;border-radius:6px;color:#777;")
        lay.addWidget(prev)

        body = QVBoxLayout()
        lay.addLayout(body, 1)
        head = QHBoxLayout()
        t = QLabel(f"<b>{fmt_time(slot['s'])}</b>")
        src_label = f"text card: {slot.get('card_text', '')}" if slot.get("kind") == "graphic" else slot.get("source", "")
        badge = QLabel({"resolved": f"Ready - {src_label}", "needs_input": "Needs your input",
                        "skipped": "Skipped", "pending": "Pending"}.get(slot["status"], slot["status"]))
        badge.setObjectName({"resolved": "badgeOk", "needs_input": "badgeWarn"}.get(slot["status"], "badgeSkip"))
        head.addWidget(t)
        head.addWidget(badge)
        head.addStretch()
        watch = QPushButton("Watch moment")
        watch.clicked.connect(lambda: tab.player.seek_source(slot["s"]))
        head.addWidget(watch)
        body.addLayout(head)
        line = QLabel(f"<i>“{slot.get('line', '')[:180]}”</i>")
        line.setWordWrap(True)
        body.addWidget(line)
        if slot.get("description"):
            d = QLabel(f"<b>Idea:</b> {slot['description']}")
            d.setWordWrap(True)
            body.addWidget(d)
        if need or slot["status"] != "resolved":
            if slot.get("shot_suggestion"):
                sh = QLabel(f"<b>Shoot / capture it yourself:</b> {slot['shot_suggestion']}")
                sh.setWordWrap(True)
                body.addWidget(sh)
            if slot.get("queries"):
                q = QLabel(f"<b>Search terms:</b> {', '.join(slot['queries'])}")
                q.setWordWrap(True)
                body.addWidget(q)
        # candidates strip
        cands = slot.get("candidates", [])
        if cands:
            strip = QHBoxLayout()
            lbl = QLabel("Options:")
            lbl.setObjectName("muted")
            strip.addWidget(lbl)
            for i, c in enumerate(cands[:8]):
                b = QToolButton()
                b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                pm = pix(c.get("preview", ""), 120, 68)
                if pm:
                    b.setIcon(QIcon(pm))
                    b.setIconSize(QSize(120, 68))
                score = f" {c['vlm']}/10" if "vlm" in c else ""
                b.setText(f"{c['source']}{score}")
                b.setToolTip(c.get("vlm_reason") or c.get("page") or c.get("file", ""))
                b.clicked.connect(lambda _=False, i=i: tab.use_candidate(slot, i))
                strip.addWidget(b)
            strip.addStretch()
            body.addLayout(strip)
        # actions
        act = QHBoxLayout()
        self.q = QLineEdit((slot.get("queries") or [""])[0])
        self.q.setPlaceholderText("search stock footage...")
        sb = QPushButton("Search online")
        sb.clicked.connect(lambda: tab.search_slot(slot, self.q.text()))
        fb = QPushButton("Use my file...")
        fb.clicked.connect(lambda: tab.user_file(slot))
        ab = QPushButton("AI image")
        ab.clicked.connect(lambda: tab.ai_slot(slot))
        cb = QPushButton("Text card")
        cb.setToolTip(f"Animated text card: {slot.get('card_text', '')}")
        cb.clicked.connect(lambda: tab.card_slot(slot))
        kb = QPushButton("Skip" if slot["status"] != "skipped" else "Un-skip")
        kb.clicked.connect(lambda: tab.toggle_skip(slot))
        rb = QPushButton("Remove")
        rb.setObjectName("danger")
        rb.clicked.connect(lambda: tab.remove_slot(slot))
        act.addWidget(self.q, 1)
        for b in (sb, fb, cb, ab, kb, rb):
            act.addWidget(b)
        body.addLayout(act)


# ================================================================= Review tab
class ReviewTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.job: Job | None = None
        self.task: Task | None = None
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title = QLabel("Open a project from Create")
        self.title.setObjectName("h1")
        top.addWidget(self.title, 1)
        self.b_render = QPushButton("Render")
        self.b_render.setObjectName("primary")
        self.b_render.clicked.connect(self.render)
        self.b_folder = QPushButton("Open output folder")
        self.b_folder.clicked.connect(self.open_folder)
        self.b_yt = QPushButton("Upload to YouTube")
        self.b_yt.clicked.connect(lambda: self.upload("youtube"))
        self.b_ig = QPushButton("Upload to Instagram")
        self.b_ig.clicked.connect(lambda: self.upload("instagram"))
        for b in (self.b_render, self.b_folder, self.b_yt, self.b_ig):
            top.addWidget(b)
        root.addLayout(top)
        pr = QHBoxLayout()
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        pr.addWidget(self.bar, 1)
        pr.addWidget(self.status, 2)
        root.addLayout(pr)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)
        self.player = Player()
        split.addWidget(self.player)
        self.tabs = QTabWidget()
        split.addWidget(self.tabs)
        split.setSizes([560, 820])

        # --- B-roll
        bw = QWidget()
        bl = QVBoxLayout(bw)
        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        bl.addWidget(self.banner)
        add = QPushButton("+ Add B-roll at the current preview time (Raw video)")
        add.clicked.connect(self.add_slot)
        bl.addWidget(add)
        self.cards_area = QScrollArea()
        self.cards_area.setWidgetResizable(True)
        bl.addWidget(self.cards_area, 1)
        self.tabs.addTab(bw, "B-roll")

        # --- Cuts
        cw = QWidget()
        cl = QVBoxLayout(cw)
        hint = QLabel("Untick a cut to keep that part. Double-click a row to watch it. Re-render to apply.")
        hint.setObjectName("muted")
        cl.addWidget(hint)
        self.cuts = QTableWidget(0, 5)
        self.cuts.setHorizontalHeaderLabels(["Cut", "Start", "Length", "Type", "Text"])
        self.cuts.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.cuts.itemChanged.connect(self._cut_changed)
        self.cuts.cellDoubleClicked.connect(lambda r, c: self.player.seek_source(self.job.plan["cuts"][r]["s"]))
        cl.addWidget(self.cuts)
        self.tabs.addTab(cw, "Cuts")

        # --- Text
        tw = QWidget()
        tl = QVBoxLayout(tw)
        self.callouts = QTableWidget(0, 3)
        self.callouts.setHorizontalHeaderLabels(["Show", "Time", "Pop-up text (max 4 words)"])
        self.callouts.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.callouts.itemChanged.connect(self._callout_changed)
        tl.addWidget(self.callouts)
        ac = QPushButton("+ Add callout at current preview time (Raw video)")
        ac.clicked.connect(self.add_callout)
        tl.addWidget(ac)
        kf = QFormLayout()
        self.keywords = QLineEdit()
        self.keywords.editingFinished.connect(self._save_keywords)
        kf.addRow("Highlighted caption keywords", self.keywords)
        tl.addLayout(kf)
        self.tabs.addTab(tw, "Text")

        # --- Thumbnail
        thw = QWidget()
        thl = QVBoxLayout(thw)
        self.thumb_grid = QGridLayout()
        thl.addLayout(self.thumb_grid)
        self.thumb_group = QButtonGroup(self)
        self.thumb_group.setExclusive(True)
        self.thumb_group.idClicked.connect(self._thumb_choice)
        tf = QFormLayout()
        self.thumb_texts = [QLineEdit() for _ in range(3)]
        for i, e in enumerate(self.thumb_texts):
            tf.addRow(f"Text {i + 1}", e)
        thl.addLayout(tf)
        rg = QPushButton("Regenerate thumbnails")
        rg.clicked.connect(self.regen_thumbs)
        thl.addWidget(rg)
        thl.addStretch()
        self.tabs.addTab(thw, "Thumbnail")

        # --- Publish
        pw = QWidget()
        pf = QFormLayout(pw)
        self.p_title = QComboBox()
        self.p_title.setEditable(True)
        self.p_desc = QPlainTextEdit()
        self.p_tags = QLineEdit()
        self.p_sched_on = QCheckBox("Schedule publish time")
        self.p_sched = QDateTimeEdit(QDateTime.currentDateTime().addDays(1))
        self.p_sched.setCalendarPopup(True)
        self.p_ig = QPlainTextEdit()
        pf.addRow("YouTube title", self.p_title)
        pf.addRow("Description", self.p_desc)
        pf.addRow("Tags (comma separated)", self.p_tags)
        sch = QHBoxLayout()
        sch.addWidget(self.p_sched_on)
        sch.addWidget(self.p_sched)
        pf.addRow("Schedule", sch)
        pf.addRow("Instagram caption", self.p_ig)
        sv = QPushButton("Save text")
        sv.clicked.connect(self.save_publish)
        pf.addRow(sv)
        self.tabs.addTab(pw, "Publish")
        self._set_enabled(False)

    # ----------------------------------------------------------------- load
    def _set_enabled(self, on: bool):
        for b in (self.b_render, self.b_folder, self.b_yt, self.b_ig):
            b.setEnabled(on)

    def load(self, job: Job, focus: str | None = None):
        self.job = Job(job.dir)
        p = self.job.plan
        self.title.setText(Path(p.get("source", "")).name)
        self._set_enabled(True)
        outs = p.get("outputs", {})
        self.player.set_sources({"YouTube edit": outs.get("youtube", ""), "Reel": outs.get("reel", ""),
                                 "Raw video": p.get("source", "")})
        self.refresh_broll()
        self.refresh_cuts()
        self.refresh_text()
        self.refresh_thumbs()
        self.refresh_publish()
        self.b_yt.setEnabled(bool(outs.get("youtube")))
        self.b_ig.setEnabled(bool(outs.get("reel")))
        self.b_folder.setEnabled(bool(p.get("export_dir")))
        if focus == "broll":
            self.tabs.setCurrentIndex(0)

    def refresh_broll(self):
        slots = self.job.plan.get("broll", [])
        need = [s for s in slots if s["status"] == "needs_input"]
        if need:
            self.banner.setText(f"I couldn't find good B-roll for {len(need)} moment(s). For each one I've suggested "
                                f"what to show, how to shoot it and what to search. Add your own clip/image, search "
                                f"online, generate an AI image, or skip - then press Render.")
            self.banner.show()
        else:
            self.banner.hide()
        w = QWidget()
        lay = QVBoxLayout(w)
        for s in sorted(slots, key=lambda s: (s["status"] != "needs_input", s["s"])):
            lay.addWidget(SlotCard(self, s))
        if not slots:
            lay.addWidget(QLabel("No B-roll planned. Use '+ Add B-roll' to place one manually."))
        lay.addStretch()
        self.cards_area.setWidget(w)
        self.tabs.setTabText(0, f"B-roll ({len(need)} need you)" if need else "B-roll")

    def refresh_cuts(self):
        self.cuts.blockSignals(True)
        cuts = self.job.plan.get("cuts", [])
        self.cuts.setRowCount(len(cuts))
        for r, c in enumerate(cuts):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked if c.get("enabled", True) else Qt.Unchecked)
            self.cuts.setItem(r, 0, it)
            for col, val in enumerate([fmt_time(c["s"]) + f".{int(c['s'] * 10) % 10}", f"{c['e'] - c['s']:.1f}s",
                                       c["kind"], c.get("text", "")], start=1):
                x = QTableWidgetItem(val)
                x.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.cuts.setItem(r, col, x)
        total = sum(c["e"] - c["s"] for c in cuts if c.get("enabled", True))
        self.tabs.setTabText(1, f"Cuts ({len(cuts)}, -{total:.0f}s)")
        self.cuts.blockSignals(False)

    def refresh_text(self):
        self.callouts.blockSignals(True)
        cs = self.job.plan.get("callouts", [])
        self.callouts.setRowCount(len(cs))
        for r, c in enumerate(cs):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked if c.get("enabled", True) else Qt.Unchecked)
            self.callouts.setItem(r, 0, it)
            t = QTableWidgetItem(fmt_time(c["s"]))
            t.setFlags(Qt.ItemIsEnabled)
            self.callouts.setItem(r, 1, t)
            self.callouts.setItem(r, 2, QTableWidgetItem(c["text"]))
        self.callouts.blockSignals(False)
        self.keywords.setText(", ".join(self.job.plan.get("keywords", [])))

    def refresh_thumbs(self):
        while self.thumb_grid.count():
            w = self.thumb_grid.takeAt(0).widget()
            if w:
                self.thumb_group.removeButton(w) if isinstance(w, QToolButton) else None
                w.deleteLater()
        th = self.job.plan.get("thumbnails", {})
        for i, o in enumerate(th.get("options", [])):
            b = QToolButton()
            b.setCheckable(True)
            pm = pix(o["path"], 384, 216)
            if pm:
                b.setIcon(QIcon(pm))
                b.setIconSize(QSize(384, 216))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setText(f"Option {i + 1}")
            b.setChecked(i == th.get("choice", 0))
            self.thumb_group.addButton(b, i)
            self.thumb_grid.addWidget(b, i // 2, i % 2)
        texts = self.job.plan.get("metadata", {}).get("thumbnail_texts", [])
        for i, e in enumerate(self.thumb_texts):
            e.setText(texts[i] if i < len(texts) else "")

    def refresh_publish(self):
        m = self.job.plan.get("metadata", {})
        self.p_title.clear()
        for t in [m.get("title", "")] + [t for t in m.get("titles", []) if t != m.get("title")]:
            if t:
                self.p_title.addItem(t)
        self.p_desc.setPlainText(runner.youtube_description(self.job.plan))
        self.p_tags.setText(", ".join(m.get("tags", [])))
        self.p_ig.setPlainText(runner.instagram_caption(self.job.plan))
        self._desc_orig = self.p_desc.toPlainText()
        self._ig_orig = self.p_ig.toPlainText()

    # ---------------------------------------------------------- edits
    def _cut_changed(self, it: QTableWidgetItem):
        if it.column() == 0:
            self.job.plan["cuts"][it.row()]["enabled"] = it.checkState() == Qt.Checked
            self.job.save()
            self.refresh_cuts()

    def _callout_changed(self, it: QTableWidgetItem):
        c = self.job.plan["callouts"][it.row()]
        if it.column() == 0:
            c["enabled"] = it.checkState() == Qt.Checked
        elif it.column() == 2:
            c["text"] = it.text()[:40]
        self.job.save()

    def _save_keywords(self):
        self.job.plan["keywords"] = [k.strip().lower() for k in self.keywords.text().split(",") if k.strip()]
        self.job.save()

    def add_callout(self):
        t = self.player.current_source_time()
        if t is None:
            QMessageBox.information(self, "Callout", "Switch the preview to 'Raw video' and pause where you want it.")
            return
        text, ok = QInputDialog.getText(self, "Callout", "Text (max 4 words):")
        if ok and text.strip():
            self.job.plan.setdefault("callouts", []).append({"s": t, "e": t + 1.6, "text": text.strip()[:40],
                                                             "enabled": True})
            self.job.plan["callouts"].sort(key=lambda c: c["s"])
            self.job.save()
            self.refresh_text()

    def _thumb_choice(self, i: int):
        self.job.plan.setdefault("thumbnails", {})["choice"] = i
        self.job.save()

    def save_publish(self):
        if not self.job:
            return
        m = self.job.plan.setdefault("metadata", {})
        m["title"] = self.p_title.currentText().strip()[:100]
        m["tags"] = [t.strip() for t in self.p_tags.text().split(",") if t.strip()]
        if self.p_desc.toPlainText() != self._desc_orig:
            m["youtube_description"] = self.p_desc.toPlainText()
        if self.p_ig.toPlainText() != self._ig_orig:
            m["instagram_caption_full"] = self.p_ig.toPlainText()
        m["publish_at"] = (self.p_sched.dateTime().toUTC().toString("yyyy-MM-ddTHH:mm:ss") + "Z") \
            if self.p_sched_on.isChecked() else ""
        self.job.save()
        self.win.statusBar().showMessage("Saved", 2000)

    # ---------------------------------------------------------- b-roll ops
    def _slot_saved(self):
        self.job.save()
        self.refresh_broll()

    def use_candidate(self, slot, idx):
        self.run(actions.use_candidate, slot["id"], idx, after=lambda _: self._reload_broll())

    def search_slot(self, slot, query):
        if query.strip():
            self.run(actions.search_slot, slot["id"], query.strip(), after=lambda _: self._reload_broll())

    def ai_slot(self, slot):
        self.run(actions.ai_slot, slot["id"], after=lambda _: self._reload_broll())

    def user_file(self, slot):
        f, _ = QFileDialog.getOpenFileName(self, "Choose B-roll (video or image)", "",
                                           "Media (*.mp4 *.mov *.mkv *.webm *.jpg *.jpeg *.png *.webp)")
        if not f:
            return
        start = 0.0
        if Path(f).suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
            start, ok = QInputDialog.getDouble(self, "Start point", "Start the clip at (seconds):", 0.0, 0, 36000, 1)
            if not ok:
                return
        set_user_file(slot, f, start)
        self._slot_saved()

    def card_slot(self, slot):
        text, ok = QInputDialog.getText(self, "Text card", "Card text (2-4 words):", text=slot.get("card_text", ""))
        if ok:
            make_card(slot, text.strip() or None)
            self._slot_saved()

    def toggle_skip(self, slot):
        if slot["status"] == "skipped":
            slot["status"] = "resolved" if slot.get("file") else "needs_input"
        else:
            slot["status"] = "skipped"
        slot["user_locked"] = True
        self._slot_saved()

    def remove_slot(self, slot):
        self.job.plan["broll"] = [s for s in self.job.plan["broll"] if s is not slot]
        self._slot_saved()

    def add_slot(self):
        t = self.player.current_source_time()
        if t is None:
            QMessageBox.information(self, "B-roll", "Switch the preview to 'Raw video' and pause where you want it.")
            return
        slots = self.job.plan.setdefault("broll", [])
        nid = max([s["id"] for s in slots], default=-1) + 1
        slots.append({"id": nid, "seg_id": -1, "s": t, "e": t + 3.0, "line": "(added manually)", "description": "",
                      "queries": [], "shot_suggestion": "", "status": "needs_input", "source": "", "file": "",
                      "file_start": 0.0, "kind": "", "score": 0, "preview": "", "candidates": [],
                      "user_locked": False})
        self._slot_saved()

    def _reload_broll(self):
        self.job = Job(self.job.dir)
        self.refresh_broll()

    # ---------------------------------------------------------- jobs
    def run(self, fn, *args, after=None):
        if self.task and self.task.isRunning():
            QMessageBox.information(self, "Busy", "Please wait for the current task to finish.")
            return
        self.job.save()
        cfg = settings_mod.load()
        self.task = Task(fn, self.job, cfg, *args)
        self.task.progress.connect(lambda p, m: (self.bar.setValue(int(p * 1000)), m and self.status.setText(m)))
        self.task.log.connect(self.win.log)
        self.task.done.connect(lambda r: (self.bar.setValue(1000), after and after(r)))
        self.task.failed.connect(lambda e: (self.status.setText("Failed: " + e[:150]),
                                            QMessageBox.warning(self, "Failed", e[:1500])))
        self.task.start()

    def render(self):
        need = [s for s in self.job.plan.get("broll", []) if s["status"] == "needs_input"]
        if need:
            box = QMessageBox(self)
            box.setWindowTitle("B-roll missing")
            box.setText(f"{len(need)} moment(s) still have no B-roll.")
            cards = box.addButton("Use animated text cards", QMessageBox.AcceptRole)
            skip = box.addButton("Skip them", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() == cards:
                for s in need:
                    make_card(s)
                self.job.save()
            elif box.clickedButton() != skip:
                return
        self.save_publish()
        self.player.release()
        self.run(actions.render, after=lambda _: (self.load(self.job),
                                                  self.status.setText("Rendered! Files are in the output folder."),
                                                  self.win.create.refresh_recent()))

    def regen_thumbs(self):
        texts = [e.text().strip() for e in self.thumb_texts if e.text().strip()]
        self.run(actions.regen_thumbs, texts, after=lambda _: (self.load(self.job), self.tabs.setCurrentIndex(3)))

    def upload(self, where: str):
        self.save_publish()
        if where == "youtube":
            from ..upload import youtube
            if not youtube.connected():
                QMessageBox.information(self, "YouTube", "Connect your YouTube account in Settings first.")
                return
            fn = actions.upload_youtube
        else:
            fn = actions.upload_instagram
        if QMessageBox.question(self, "Publish", f"Upload to {where.title()} now?") != QMessageBox.Yes:
            return
        self.run(fn, after=lambda url: (self.status.setText(f"Published: {url}"),
                                        QMessageBox.information(self, "Published", str(url))))

    def open_folder(self):
        from .main_window import open_path
        d = self.job.plan.get("export_dir")
        if d and Path(d).exists():
            open_path(d)
