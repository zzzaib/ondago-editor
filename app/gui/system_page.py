"""'This computer' page: detects the graphics card / chip and shows, feature by feature, what OnDaGo can run here,
on which engine, how fast - and what isn't available (e.g. AI images on a Mac)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ..core import settings as settings_mod
from .workers import Task

COLORS = {  # status -> (pill background, pill text)
    "fast": ("#17432E", "#8CF0C2"),
    "ok": ("#1D3350", "#9CC8FF"),
    "slow": ("#4A3410", "#FFD27A"),
    "off": ("#3E1E28", "#FF9DB0"),
}
MOOD = {"great": ("#123A2A", "#2F7A55", "#8CF0C2"), "good": ("#16283F", "#2E5A8A", "#9CC8FF"),
        "basic": ("#2E2312", "#7A5A1A", "#FFD27A")}


def _label(text: str = "", name: str = "", wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def _pill(status: str, text: str) -> QLabel:
    bg, fg = COLORS.get(status, COLORS["ok"])
    p = QLabel(text)
    p.setAlignment(Qt.AlignCenter)
    p.setFixedWidth(118)
    p.setStyleSheet(f"background:{bg};color:{fg};border-radius:9px;padding:4px 8px;font-weight:700;")
    return p


def _check(log=None, progress=None, refresh=False):
    from ..core import capabilities
    return capabilities.report(settings_mod.load(), refresh=refresh)


class SystemPage(QScrollArea):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.task: Task | None = None
        self.report: dict | None = None
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        outer = QHBoxLayout(body)
        outer.addStretch(1)
        col = QWidget()
        col.setMaximumWidth(980)
        outer.addWidget(col, 12)
        outer.addStretch(1)
        self.lay = QVBoxLayout(col)
        self.lay.setContentsMargins(28, 26, 28, 36)
        self.lay.setSpacing(12)

        top = QHBoxLayout()
        h = QLabel("This computer")
        h.setObjectName("h1")
        top.addWidget(h)
        top.addStretch()
        self.copy_btn = QPushButton("Copy report")
        self.copy_btn.clicked.connect(self.copy)
        self.again = QPushButton("Check again")
        self.again.clicked.connect(lambda: self.start(refresh=True))
        top.addWidget(self.copy_btn)
        top.addWidget(self.again)
        self.lay.addLayout(top)
        sub = _label("OnDaGo checks your graphics card and chip, then tells you what each part of the editor runs on "
                     "here - and what this computer can't do.", "muted")
        self.lay.addWidget(sub)

        self.verdict = _label("Checking your computer... (a few seconds)", "")
        self.verdict.setStyleSheet("background:#1A1A25;border:1px solid #2C2C3C;border-radius:14px;padding:16px;"
                                   "font-size:12pt;font-weight:600;")
        self.lay.addWidget(self.verdict)

        self.hw = QFrame()
        self.hw.setObjectName("card")
        self.hw_grid = QGridLayout(self.hw)
        self.hw_grid.setContentsMargins(18, 14, 18, 14)
        self.hw_grid.setHorizontalSpacing(18)
        self.hw_grid.setVerticalSpacing(6)
        self.lay.addWidget(self.hw)

        h2 = QLabel("What runs where")
        h2.setObjectName("h2")
        self.lay.addWidget(h2)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        self.lay.addLayout(self.rows)

        self.limits_box = QFrame()
        self.limits_box.setObjectName("cardWarn")
        self.limits_lay = QVBoxLayout(self.limits_box)
        self.limits_lay.setContentsMargins(18, 14, 18, 14)
        self.limits_box.hide()
        self.lay.addWidget(self.limits_box)

        legend = _label("Fast = uses your graphics card or Apple chip.  Works = fine, on the processor.  "
                        "Slow = works, but takes longer.  Not available = this computer can't run it.", "small")
        self.lay.addWidget(legend)
        self.lay.addStretch()
        self._started = False

    # ------------------------------------------------------------------ run
    def showEvent(self, e):
        super().showEvent(e)
        if not self._started:
            self._started = True
            self.start()

    def start(self, refresh: bool = False):
        if self.task and self.task.isRunning():
            return
        self.again.setEnabled(False)
        self.verdict.setText("Checking your computer... (a few seconds)")
        self.task = Task(_check, refresh=refresh)
        self.task.done.connect(self.show_report)
        self.task.failed.connect(lambda e: (self.verdict.setText("Couldn't finish the check: " + e[:200]),
                                            self.again.setEnabled(True)))
        self.task.start()

    def copy(self):
        if self.report:
            from ..core import capabilities
            QGuiApplication.clipboard().setText(capabilities.summary_text(self.report))
            self.win.statusBar().showMessage("Report copied", 3000)

    # --------------------------------------------------------------- render
    @staticmethod
    def _clear(lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                SystemPage._clear(it.layout())

    def show_report(self, r: dict):
        self.report = r
        self.again.setEnabled(True)
        bg, border, fg = MOOD.get(r["mood"], MOOD["good"])
        self.verdict.setText(r["verdict"])
        self.verdict.setStyleSheet(f"background:{bg};border:1px solid {border};border-radius:14px;padding:16px;"
                                   f"font-size:12pt;font-weight:600;color:{fg};")
        m = r["machine"]
        self._clear(self.hw_grid)
        if m["nvidia"]:
            gpu = ", ".join(f"{g['name']} - {g['vram_gb']:.0f} GB, driver {g['driver']}" for g in m["nvidia"])
            others = [n for n in m["gpus"] if not any(n in g["name"] or g["name"] in n for g in m["nvidia"])]
            if others:
                gpu += "  +  " + ", ".join(others)
        elif m["apple_silicon"]:
            gpu = f"{', '.join(m['gpus']) or 'Apple GPU'} - shares the {m['ram_gb']:.0f} GB unified memory"
        else:
            gpu = ", ".join(m["gpus"]) or "none found"
        t = m.get("torch", {})
        if not t.get("version"):
            ai = t.get("error") or "PyTorch not found"
        elif t.get("cuda"):
            cap = t.get("cap")
            ai = f"PyTorch {t['version']}: NVIDIA CUDA" + (f" (compute {cap[0]}.{cap[1]})" if cap else "")
        else:
            ai = f"PyTorch {t['version']}: " + ("Apple GPU (Metal)" if t.get("mps") else "CPU only")
            if t.get("error"):
                ai += f" - {t['error']}"
        info = [("System", m["os"] + (" (Apple Silicon)" if m["apple_silicon"] else " (Intel Mac)" if m["intel_mac"]
                                      else "")),
                ("Processor", f"{m['cpu']}  ·  {m['cores']} cores"),
                ("Memory", f"{m['ram_gb']:.0f} GB" + (" unified" if m["apple_silicon"] else "")),
                ("Graphics", gpu),
                ("AI engine", ai)]
        for i, (k, v) in enumerate(info):
            kl = QLabel(k)
            kl.setObjectName("muted")
            self.hw_grid.addWidget(kl, i, 0, Qt.AlignTop)
            self.hw_grid.addWidget(_label(v), i, 1)
        self.hw_grid.setColumnStretch(1, 1)

        self._clear(self.rows)
        for f in r["features"]:
            self.rows.addWidget(self._row(f))

        self._clear(self.limits_lay)
        if r["limits"]:
            self.limits_lay.addWidget(_label("<b>Limits of this computer</b>"))
            for x in r["limits"]:
                self.limits_lay.addWidget(_label("•  " + x))
            self.limits_box.show()
        else:
            self.limits_box.hide()

    def _row(self, f: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        h = QHBoxLayout(card)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(14)
        h.addWidget(_pill(f["status"], f["level"]), 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(3)
        v.addWidget(_label(f"<b>{f['title']}</b>  <span style='color:#9393AB'>- {f['what']}</span>"))
        v.addWidget(_label(f"Runs on: <b>{f['engine']}</b>" + (f"  ·  {f['note']}" if f["note"] else "")))
        if f.get("tip"):
            tip = _label("💡 " + f["tip"])
            tip.setStyleSheet("color:#FFD27A;")
            v.addWidget(tip)
        h.addLayout(v, 1)
        a = f.get("action")
        if a:
            b = QPushButton(a["label"])
            b.clicked.connect(lambda _=False, a=a: self.apply(a))
            h.addWidget(b, 0, Qt.AlignVCenter)
        return card

    def apply(self, action: dict):
        settings_mod.update(**action["set"])
        self.win.cfg.update(action["set"])
        w = getattr(self.win, "settings", None)
        if w is not None:  # keep the Settings page in step
            for k, val in action["set"].items():
                e = w.w.get(k)
                if e is None:
                    continue
                if hasattr(e, "setText"):
                    e.setText(str(val))
                elif hasattr(e, "findData"):
                    e.setCurrentIndex(max(0, e.findData(val)))
        self.win.statusBar().showMessage(f"Saved: {', '.join(f'{k} = {v}' for k, v in action['set'].items())}", 4000)
        self._recheck_soft()

    def _recheck_soft(self):
        from ..core import capabilities
        capabilities._cache = None  # settings changed: rebuild rows (hardware checks stay cached)
        self.start(refresh=False)
