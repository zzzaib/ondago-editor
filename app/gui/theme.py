ACCENT = "#7C5CFF"

QSS = f"""
* {{ font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 10pt; color: #E8E8F0; }}
QMainWindow, QWidget {{ background: #14141C; }}
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QTabWidget::pane {{ border: 1px solid #2A2A38; border-radius: 8px; background: #181822; }}
QTabBar::tab {{ background: #1C1C28; padding: 9px 18px; border-top-left-radius: 8px; border-top-right-radius: 8px;
               margin-right: 2px; color: #A0A0B8; }}
QTabBar::tab:selected {{ background: #262636; color: #FFFFFF; }}
QGroupBox {{ border: 1px solid #2A2A38; border-radius: 8px; margin-top: 14px; padding: 10px; background: #1A1A24; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #B8A8FF; font-weight: 600; }}
QPushButton {{ background: #2A2A3A; border: 1px solid #3A3A50; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ background: #34344A; }}
QPushButton:disabled {{ color: #666; background: #1E1E28; }}
QPushButton#primary {{ background: {ACCENT}; border: none; font-weight: 600; font-size: 11pt; padding: 10px 22px; }}
QPushButton#primary:hover {{ background: #8E72FF; }}
QPushButton#danger {{ background: #5A2A34; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
    background: #101018; border: 1px solid #2E2E40; border-radius: 6px; padding: 5px; selection-background-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: #1C1C28; selection-background-color: {ACCENT}; }}
QProgressBar {{ background: #101018; border: 1px solid #2E2E40; border-radius: 6px; text-align: center; height: 20px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QListWidget, QTableWidget {{ background: #101018; border: 1px solid #2A2A38; border-radius: 6px; gridline-color: #2A2A38; }}
QHeaderView::section {{ background: #1C1C28; padding: 5px; border: none; color: #A0A0B8; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QScrollArea {{ border: none; }}
QLabel#h1 {{ font-size: 16pt; font-weight: 700; }}
QLabel#muted {{ color: #8888A0; }}
QFrame#drop {{ border: 2px dashed #4A4A66; border-radius: 14px; background: #171722; }}
QFrame#drop[hover="true"] {{ border-color: {ACCENT}; background: #1E1A33; }}
QFrame#card {{ background: #1C1C28; border: 1px solid #2E2E40; border-radius: 10px; }}
QFrame#cardWarn {{ background: #2A2018; border: 1px solid #8A6A2A; border-radius: 10px; }}
QLabel#badgeOk {{ background: #1F5A3A; border-radius: 6px; padding: 2px 8px; }}
QLabel#badgeWarn {{ background: #8A5A12; border-radius: 6px; padding: 2px 8px; }}
QLabel#badgeSkip {{ background: #3A3A4A; border-radius: 6px; padding: 2px 8px; }}
QLabel#banner {{ background: #3A2A10; border: 1px solid #A07A20; border-radius: 8px; padding: 8px; color: #FFD27A; }}
"""
