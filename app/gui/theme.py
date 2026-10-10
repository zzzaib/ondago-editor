"""OnDaGo desktop theme: dark, calm, purple accent. Small SVG assets (check marks, arrows) are written next to this
file on first run so Qt style sheets can use them."""
from __future__ import annotations

from pathlib import Path

ACCENT = "#7C5CFF"
ACCENT_2 = "#9B82FF"
BG = "#121219"
SURFACE = "#1A1A25"
SURFACE_2 = "#22222F"
LINE = "#2C2C3C"
TEXT = "#ECECF4"
MUTED = "#9393AB"

ASSETS = Path(__file__).parent / "assets"
_SVGS = {
    "check.svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" fill="none" '
                 'stroke="#ffffff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "chevron.svg": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 6l4 4 4-4" fill="none" '
                   f'stroke="{MUTED}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "up.svg": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 10l4-4 4 4" fill="none" '
              f'stroke="{MUTED}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "down.svg": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 6l4 4 4-4" fill="none" '
                f'stroke="{MUTED}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
}


def _assets() -> Path:
    try:
        ASSETS.mkdir(exist_ok=True)
        for name, svg in _SVGS.items():
            f = ASSETS / name
            if not f.exists() or f.read_text(encoding="utf-8") != svg:
                f.write_text(svg, encoding="utf-8")
    except OSError:
        pass
    return ASSETS


def _u(name: str) -> str:
    return (_assets() / name).as_posix()


QSS = f"""
* {{ font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QWidget {{ background: {BG}; }}
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; border-radius: 6px; }}

/* ---------- sidebar */
QFrame#sidebar {{ background: #0E0E14; border-right: 1px solid {LINE}; }}
QLabel#brand {{ font-size: 15pt; font-weight: 800; padding: 6px 4px 14px 4px; }}
QPushButton#nav {{ text-align: left; background: transparent; border: none; border-radius: 10px; padding: 11px 14px;
                   font-size: 10.5pt; color: {MUTED}; }}
QPushButton#nav:hover {{ background: #1A1A26; color: {TEXT}; }}
QPushButton#nav:checked {{ background: #241C48; color: #FFFFFF; font-weight: 600; }}
QLabel#navfoot {{ color: #6E6E86; font-size: 8.5pt; padding: 4px; }}

/* ---------- text */
QLabel#h1 {{ font-size: 20pt; font-weight: 800; }}
QLabel#h2 {{ font-size: 12.5pt; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#small {{ color: {MUTED}; font-size: 9pt; }}
QLabel#pill {{ background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: 10px; padding: 2px 10px; color: {MUTED}; }}
QLabel#pillAccent {{ background: #241C48; border: 1px solid #3E2F7A; border-radius: 10px; padding: 2px 10px; color: #CFC3FF; }}

/* ---------- cards */
QFrame#card, QFrame#cardWarn, QFrame#panel {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#cardWarn {{ background: #261F14; border-color: #7A5A1A; }}
QFrame#preset {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#preset:hover {{ border-color: #4A4A66; }}
QFrame#preset[selected="true"] {{ background: #1F1A38; border: 2px solid {ACCENT}; }}
QFrame#drop {{ border: 2px dashed #43435E; border-radius: 18px; background: #15151F; }}
QFrame#drop[hover="true"] {{ border-color: {ACCENT}; background: #1C1833; }}
QFrame#filecard {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#step {{ background: transparent; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: 14px; margin-top: 16px; padding: 14px 12px 12px 12px;
             background: {SURFACE}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #C4B6FF; font-weight: 700; }}

/* ---------- buttons */
QPushButton {{ background: {SURFACE_2}; border: 1px solid #383850; border-radius: 9px; padding: 8px 16px; }}
QPushButton:hover {{ background: #2C2C40; border-color: #4A4A66; }}
QPushButton:pressed {{ background: #262638; }}
QPushButton:disabled {{ color: #66667A; background: #191922; border-color: #26263A; }}
QPushButton#primary {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #7C5CFF, stop:1 #A35CFF); border: none;
                       font-weight: 700; font-size: 11.5pt; padding: 12px 26px; border-radius: 12px; color: white; }}
QPushButton#primary:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #8E72FF, stop:1 #B272FF); }}
QPushButton#primary:disabled {{ background: #2A2540; color: #8A82A8; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: #C4B6FF; }}
QPushButton#ghost:hover {{ background: #1E1A33; }}
QPushButton#chip {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 16px; padding: 7px 16px; color: {MUTED}; }}
QPushButton#chip:checked {{ background: #241C48; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#danger {{ background: #3E1E28; border-color: #5A2A36; }}
QToolButton {{ background: {SURFACE_2}; border: 1px solid #383850; border-radius: 9px; padding: 4px; }}
QToolButton:hover {{ border-color: {ACCENT}; }}
QToolButton:checked {{ border: 2px solid {ACCENT}; background: #1F1A38; }}

/* ---------- inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
    background: #0F0F16; border: 1px solid #30304A; border-radius: 9px; padding: 7px 9px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({_u("chevron.svg")}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {LINE}; selection-background-color: {ACCENT};
                              outline: none; padding: 4px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{ border: none; width: 18px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({_u("up.svg")}); width: 11px; height: 11px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({_u("down.svg")}); width: 11px; height: 11px; }}
QCheckBox {{ spacing: 9px; padding: 3px 0; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1.5px solid #4A4A66; background: #0F0F16; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({_u("check.svg")}); }}
QCheckBox::indicator:disabled {{ background: #1A1A22; border-color: #2C2C3C; }}
QCheckBox:disabled {{ color: #66667A; }}

/* ---------- progress / lists / tables */
QProgressBar {{ background: #0F0F16; border: 1px solid {LINE}; border-radius: 7px; text-align: center; height: 14px;
                color: transparent; }}
QProgressBar::chunk {{ border-radius: 6px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7C5CFF, stop:1 #FF5CA8); }}
QListWidget, QTableWidget {{ background: #0F0F16; border: 1px solid {LINE}; border-radius: 12px; gridline-color: {LINE};
                             outline: none; }}
QListWidget::item {{ border-radius: 10px; padding: 6px; }}
QListWidget::item:hover {{ background: #1C1C2A; }}
QListWidget::item:selected {{ background: #241C48; }}
QHeaderView::section {{ background: {SURFACE}; padding: 7px; border: none; border-bottom: 1px solid {LINE}; color: {MUTED}; }}
QTableWidget::item:selected {{ background: #241C48; }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 12px; background: {SURFACE}; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 9px 16px; margin-right: 4px; color: {MUTED}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: white; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #34344A; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4A4A66; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #34344A; border-radius: 4px; min-width: 30px; }}
QStatusBar {{ background: #0E0E14; color: {MUTED}; border-top: 1px solid {LINE}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {LINE}; padding: 6px; border-radius: 8px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; }}

/* ---------- review bits (kept from v1) */
QLabel#badgeOk {{ background: #17432E; color: #8CF0C2; border-radius: 8px; padding: 2px 9px; }}
QLabel#badgeWarn {{ background: #4A3410; color: #FFD27A; border-radius: 8px; padding: 2px 9px; }}
QLabel#badgeSkip {{ background: #2E2E3E; color: {MUTED}; border-radius: 8px; padding: 2px 9px; }}
QLabel#banner {{ background: #2E2312; border: 1px solid #7A5A1A; border-radius: 12px; padding: 10px; color: #FFD27A; }}
QPlainTextEdit#log {{ font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 9pt; background: #0C0C12; }}
"""
