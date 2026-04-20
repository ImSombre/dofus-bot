"""Global QSS dark theme for the Dofus Bot UI."""

from __future__ import annotations

# Palette
BG_MAIN = "#1e1e2e"
BG_CARD = "#2a2a3e"
BG_INPUT = "#313145"
BG_HOVER = "#353550"
TEXT_PRIMARY = "#e0e0e0"
TEXT_SECONDARY = "#9090a0"
TEXT_DISABLED = "#555570"
ACCENT_BLUE = "#4fc3f7"
ACCENT_GREEN = "#66bb6a"
ACCENT_RED = "#ef5350"
ACCENT_ORANGE = "#ffa726"
ACCENT_PURPLE = "#ce93d8"
BORDER = "#3a3a54"
SCROLLBAR_BG = "#252535"
SCROLLBAR_HANDLE = "#4a4a65"

DARK_QSS = f"""
/* ============================================================
   Base
   ============================================================ */
QMainWindow, QWidget {{
    background-color: {BG_MAIN};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "SF Pro Display", "Helvetica Neue", sans-serif;
    font-size: 11pt;
}}

QMainWindow::separator {{
    width: 1px;
    background: {BORDER};
}}

/* ============================================================
   Tab widget
   ============================================================ */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    background-color: {BG_MAIN};
    top: -1px;
}}

QTabBar::tab {{
    background-color: {BG_CARD};
    color: {TEXT_SECONDARY};
    padding: 8px 20px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    font-size: 10pt;
    font-weight: 500;
}}

QTabBar::tab:selected {{
    background-color: {BG_MAIN};
    color: {ACCENT_BLUE};
    border-bottom: 2px solid {ACCENT_BLUE};
}}

QTabBar::tab:hover:!selected {{
    background-color: {BG_HOVER};
    color: {TEXT_PRIMARY};
}}

/* ============================================================
   Buttons
   ============================================================ */
QPushButton {{
    background-color: {BG_CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 10pt;
    font-weight: 500;
    min-height: 28px;
}}

QPushButton:hover {{
    background-color: {BG_HOVER};
    border-color: {ACCENT_BLUE};
}}

QPushButton:pressed {{
    background-color: #1a1a2a;
    border-color: {ACCENT_BLUE};
}}

QPushButton:disabled {{
    color: {TEXT_DISABLED};
    background-color: {BG_CARD};
    border-color: {BORDER};
}}

QPushButton#btn_start {{
    background-color: #2d5a30;
    color: {ACCENT_GREEN};
    border-color: {ACCENT_GREEN};
    font-size: 11pt;
    font-weight: 600;
    min-height: 36px;
    padding: 8px 24px;
}}

QPushButton#btn_start:hover {{
    background-color: #356636;
}}

QPushButton#btn_start:disabled {{
    background-color: #1e3320;
    color: #3d6b40;
    border-color: #3d6b40;
}}

QPushButton#btn_stop {{
    background-color: #5a2d2d;
    color: {ACCENT_RED};
    border-color: {ACCENT_RED};
}}

QPushButton#btn_stop:hover {{
    background-color: #6b3535;
}}

QPushButton#btn_pause {{
    background-color: #5a4020;
    color: {ACCENT_ORANGE};
    border-color: {ACCENT_ORANGE};
}}

QPushButton#btn_pause:hover {{
    background-color: #6b4c25;
}}

/* ============================================================
   Labels
   ============================================================ */
QLabel {{
    color: {TEXT_PRIMARY};
    background-color: transparent;
}}

QLabel#label_title {{
    font-size: 16pt;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}

QLabel#label_stat_value {{
    font-size: 24pt;
    font-weight: 700;
    color: {ACCENT_BLUE};
}}

QLabel#label_section {{
    font-size: 10pt;
    font-weight: 600;
    color: {TEXT_SECONDARY};
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* ============================================================
   Cards (QFrame)
   ============================================================ */
QFrame#card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

/* ============================================================
   ComboBox
   ============================================================ */
QComboBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 28px;
    font-size: 10pt;
}}

QComboBox:hover {{
    border-color: {ACCENT_BLUE};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {TEXT_SECONDARY};
}}

QComboBox QAbstractItemView {{
    background-color: {BG_CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    selection-background-color: {BG_HOVER};
    outline: none;
}}

/* ============================================================
   LineEdit / TextEdit
   ============================================================ */
QLineEdit {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 10pt;
    min-height: 28px;
}}

QLineEdit:focus {{
    border-color: {ACCENT_BLUE};
}}

QTextEdit, QPlainTextEdit {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px;
    font-family: "Consolas", "Cascadia Code", "Courier New", monospace;
    font-size: 9pt;
}}

/* ============================================================
   Scrollbars
   ============================================================ */
QScrollBar:vertical {{
    background: {SCROLLBAR_BG};
    width: 8px;
    margin: 0;
    border-radius: 4px;
}}

QScrollBar::handle:vertical {{
    background: {SCROLLBAR_HANDLE};
    border-radius: 4px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background: {ACCENT_BLUE};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background: {SCROLLBAR_BG};
    height: 8px;
    border-radius: 4px;
}}

QScrollBar::handle:horizontal {{
    background: {SCROLLBAR_HANDLE};
    border-radius: 4px;
    min-width: 20px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {ACCENT_BLUE};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ============================================================
   Progress bar
   ============================================================ */
QProgressBar {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    height: 8px;
    text-align: center;
    font-size: 8pt;
    color: {TEXT_SECONDARY};
}}

QProgressBar::chunk {{
    background-color: {ACCENT_BLUE};
    border-radius: 4px;
}}

QProgressBar[state="error"]::chunk {{
    background-color: {ACCENT_RED};
}}

QProgressBar[state="warning"]::chunk {{
    background-color: {ACCENT_ORANGE};
}}

/* ============================================================
   TableWidget
   ============================================================ */
QTableWidget {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BORDER};
    font-size: 9pt;
    selection-background-color: {BG_HOVER};
}}

QTableWidget::item {{
    padding: 4px 8px;
}}

QHeaderView::section {{
    background-color: {BG_CARD};
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-size: 9pt;
    font-weight: 600;
}}

/* ============================================================
   ListWidget
   ============================================================ */
QListWidget {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    outline: none;
    font-size: 9pt;
}}

QListWidget::item {{
    padding: 4px 8px;
    border-bottom: 1px solid {BORDER};
}}

QListWidget::item:selected {{
    background-color: {BG_HOVER};
    color: {ACCENT_BLUE};
}}

QListWidget::item:hover {{
    background-color: {BG_HOVER};
}}

/* ============================================================
   Splitter
   ============================================================ */
QSplitter::handle {{
    background-color: {BORDER};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:vertical {{
    height: 1px;
}}

/* ============================================================
   StatusBar
   ============================================================ */
QStatusBar {{
    background-color: {BG_CARD};
    color: {TEXT_SECONDARY};
    border-top: 1px solid {BORDER};
    font-size: 9pt;
}}

/* ============================================================
   SpinBox
   ============================================================ */
QSpinBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 26px;
    font-size: 10pt;
}}

QSpinBox:focus {{
    border-color: {ACCENT_BLUE};
}}

QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {BG_CARD};
    border: none;
    width: 16px;
}}

/* ============================================================
   CheckBox
   ============================================================ */
QCheckBox {{
    color: {TEXT_PRIMARY};
    spacing: 8px;
    font-size: 10pt;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background-color: {BG_INPUT};
}}

QCheckBox::indicator:checked {{
    background-color: {ACCENT_BLUE};
    border-color: {ACCENT_BLUE};
}}

QCheckBox::indicator:hover {{
    border-color: {ACCENT_BLUE};
}}

/* ============================================================
   Tooltip (native)
   ============================================================ */
QToolTip {{
    background-color: {BG_CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 9pt;
}}

/* ============================================================
   MessageBox
   ============================================================ */
QMessageBox {{
    background-color: {BG_MAIN};
    color: {TEXT_PRIMARY};
}}

QMessageBox QPushButton {{
    min-width: 80px;
}}

/* ============================================================
   FormLayout labels
   ============================================================ */
QFormLayout QLabel {{
    color: {TEXT_SECONDARY};
    font-size: 10pt;
}}
"""
