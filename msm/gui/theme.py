"""見た目 (ライト / ダークテーマ)。色はトークンで定義し、スタイルシートとパレットを生成する."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette

LIGHT = {
    "bg": "#f3f5f8", "surface": "#ffffff", "surface2": "#f7f9fb", "border": "#dde3ea", "border_strong": "#c3ccd6",
    "text": "#1d2733", "text2": "#566373", "muted": "#8a95a3", "primary": "#2f7d4a", "primary_hover": "#37915a",
    "primary_soft": "#e3f1e8", "danger": "#c23b30", "danger_soft": "#fbe9e7", "warn": "#9a6a00", "warn_soft": "#fff4d6",
    "nav_bg": "#17202a", "nav_text": "#c7d0da", "nav_section": "#6e7b8a", "nav_hover": "#222d39", "nav_sel": "#2f7d4a",
    "input": "#ffffff", "hover": "#eef2f6", "header": "#f0f3f7", "selection": "#cfe6d7", "selection_text": "#1d2733",
    "console_bg": "#111820", "console_text": "#d7e0e8",
}
DARK = {
    "bg": "#14181d", "surface": "#1d232a", "surface2": "#222930", "border": "#2f3842", "border_strong": "#45505c",
    "text": "#e5e9ee", "text2": "#a9b3be", "muted": "#7c8794", "primary": "#3a9a5c", "primary_hover": "#46ad6a",
    "primary_soft": "#1f3a2a", "danger": "#e0574c", "danger_soft": "#3a2120", "warn": "#e0b54a", "warn_soft": "#3a3220",
    "nav_bg": "#0e1216", "nav_text": "#c3ccd6", "nav_section": "#65717e", "nav_hover": "#1a2128", "nav_sel": "#2f7d4a",
    "input": "#161b21", "hover": "#28303a", "header": "#252c34", "selection": "#2c5a3d", "selection_text": "#ffffff",
    "console_bg": "#0d1116", "console_text": "#d7e0e8",
}

STATE_COLORS = {
    False: {"running": "#2e8b57", "starting": "#b7860b", "stopping": "#b7860b", "stopped": "#6b7785", "crashed": "#c23b30"},
    True: {"running": "#4cc77d", "starting": "#e0b54a", "stopping": "#e0b54a", "stopped": "#93a0ad", "crashed": "#ff6b5f"},
}

_current_dark = False


def is_dark() -> bool:
    return _current_dark


def tokens() -> dict:
    return DARK if _current_dark else LIGHT


def system_prefers_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def resolve(setting: str) -> bool:
    if setting == "dark":
        return True
    if setting == "light":
        return False
    return system_prefers_dark()


def _arrow_images(dark: bool) -> tuple[str, str]:
    """コンボボックス・数値入力の矢印画像を作る (スタイルシートで枠を変えると標準の矢印が消えるため)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QImage, QPainter, QPolygonF

    from msm.core.paths import cache_dir

    color = QColor((DARK if dark else LIGHT)["text2"])
    out = []
    for name, pts in (("down", [(2, 4), (10, 4), (6, 9)]), ("up", [(2, 8), (10, 8), (6, 3)])):
        path = cache_dir() / f"arrow_{name}_{'dark' if dark else 'light'}.png"
        if not path.exists():
            img = QImage(12, 12, QImage.Format_ARGB32)
            img.fill(Qt.transparent)
            pa = QPainter(img)
            pa.setRenderHint(QPainter.Antialiasing)
            pa.setPen(Qt.NoPen)
            pa.setBrush(color)
            pa.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
            pa.end()
            img.save(str(path))
        out.append(str(path).replace("\\", "/"))
    return out[0], out[1]


def build_style(dark: bool) -> str:
    t = DARK if dark else LIGHT
    try:
        down, up = _arrow_images(dark)
    except Exception:
        down = up = ""
    arrows = f"""
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 20px; background: transparent; }}
QSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
""" if down else ""
    return arrows + f"""
* {{ font-family: "Yu Gothic UI", "Meiryo UI", "Segoe UI"; font-size: 10pt; color: {t['text']}; }}
QMainWindow, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget#page {{ background: {t['bg']}; }}
QWidget#topbar {{ background: {t['surface']}; border-bottom: 1px solid {t['border']}; }}
QListWidget#nav {{ background: {t['nav_bg']}; color: {t['nav_text']}; border: none; font-size: 10.5pt; outline: none; padding-top: 6px; }}
QListWidget#nav::item {{ padding: 8px 16px; border-radius: 6px; margin: 1px 8px; color: {t['nav_text']}; }}
QListWidget#nav::item:selected {{ background: {t['nav_sel']}; color: #ffffff; }}
QListWidget#nav::item:hover:!selected {{ background: {t['nav_hover']}; }}
QListWidget#nav::item:disabled {{ color: {t['nav_section']}; font-size: 8.5pt; font-weight: bold; padding: 14px 16px 4px 16px; margin: 0; background: transparent; }}
QLabel#appname {{ color: #ffffff; font-size: 11pt; font-weight: bold; padding: 14px 18px 4px 18px; background: {t['nav_bg']}; }}
QLabel#title {{ font-size: 17pt; font-weight: bold; color: {t['text']}; }}
QLabel#subtitle {{ color: {t['text2']}; font-size: 10pt; }}
QLabel#help {{ color: {t['muted']}; font-size: 9pt; }}
QLabel#big {{ font-size: 15pt; font-weight: bold; }}
QLabel#value {{ font-size: 14pt; font-weight: bold; }}
QLabel#caption {{ color: {t['text2']}; font-size: 9pt; }}
QLabel#notice {{ background: {t['warn_soft']}; color: {t['warn']}; border-radius: 6px; padding: 8px 10px; }}
QLabel#dangertext {{ color: {t['danger']}; font-weight: bold; }}
QFrame#card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; }}
QFrame#tile {{ background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 8px; }}
QPushButton {{ padding: 7px 16px; border-radius: 7px; border: 1px solid {t['border_strong']}; background: {t['surface']}; color: {t['text']}; }}
QPushButton:hover {{ background: {t['hover']}; }}
QPushButton:disabled {{ color: {t['muted']}; background: {t['surface2']}; border-color: {t['border']}; }}
QPushButton#primary {{ background: {t['primary']}; color: #ffffff; border: 1px solid {t['primary']}; font-weight: bold; }}
QPushButton#primary:hover {{ background: {t['primary_hover']}; }}
QPushButton#primary:disabled {{ background: {t['primary_soft']}; color: {t['muted']}; border-color: {t['border']}; }}
QPushButton#big {{ background: {t['primary']}; color: #ffffff; border: none; font-size: 12pt; font-weight: bold; padding: 12px 26px; border-radius: 9px; }}
QPushButton#big:hover {{ background: {t['primary_hover']}; }}
QPushButton#bigstop {{ background: {t['danger']}; color: #ffffff; border: none; font-size: 12pt; font-weight: bold; padding: 12px 26px; border-radius: 9px; }}
QPushButton#danger {{ background: {t['surface']}; color: {t['danger']}; border: 1px solid {t['danger']}; }}
QPushButton#danger:hover {{ background: {t['danger_soft']}; }}
QPushButton#danger:disabled {{ color: {t['muted']}; border-color: {t['border']}; }}
QPushButton#link {{ border: none; background: transparent; color: {t['primary']}; padding: 2px 4px; text-decoration: underline; }}
QPushButton#icon {{ padding: 4px 8px; min-width: 28px; }}
QGroupBox {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 10px; margin-top: 26px; padding: 12px 10px 10px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 4px; top: 2px; padding: 0 2px; font-weight: bold; font-size: 11pt; color: {t['text']}; }}
QTableWidget, QTreeWidget, QListWidget, QPlainTextEdit, QTextEdit {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 6px;
    alternate-background-color: {t['surface2']}; gridline-color: {t['border']}; selection-background-color: {t['selection']}; selection-color: {t['selection_text']}; }}
QHeaderView::section {{ background: {t['header']}; color: {t['text2']}; padding: 6px; border: none; border-right: 1px solid {t['border']}; font-weight: bold; }}
QPlainTextEdit#console {{ background: {t['console_bg']}; color: {t['console_text']}; font-family: "Consolas", "MS Gothic"; font-size: 9.5pt; border-radius: 8px; }}
QComboBox, QLineEdit, QSpinBox {{ padding: 5px 6px; border: 1px solid {t['border_strong']}; border-radius: 6px; background: {t['input']}; color: {t['text']}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {t['primary']}; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; color: {t['text']}; selection-background-color: {t['selection']}; }}
QTabWidget::pane {{ border: 1px solid {t['border']}; background: {t['surface']}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ padding: 8px 16px; background: transparent; color: {t['text2']}; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t['text']}; border-bottom: 2px solid {t['primary']}; font-weight: bold; }}
QTabBar::tab:hover:!selected {{ color: {t['text']}; }}
QCheckBox, QRadioButton {{ spacing: 6px; }}
QStatusBar {{ background: {t['surface']}; color: {t['text2']}; border-top: 1px solid {t['border']}; }}
QProgressBar {{ border: 1px solid {t['border']}; border-radius: 6px; background: {t['surface2']}; text-align: center; height: 16px; }}
QProgressBar::chunk {{ background: {t['primary']}; border-radius: 5px; }}
QToolTip {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border_strong']}; padding: 4px; }}
QMenu {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border']}; }}
QMenu::item:selected {{ background: {t['selection']}; color: {t['selection_text']}; }}
QWizard, QDialog, QMessageBox {{ background: {t['bg']}; }}
"""


def palette(dark: bool) -> QPalette:
    t = DARK if dark else LIGHT
    p = QPalette()
    p.setColor(QPalette.Window, QColor(t["bg"]))
    p.setColor(QPalette.WindowText, QColor(t["text"]))
    p.setColor(QPalette.Base, QColor(t["input"]))
    p.setColor(QPalette.AlternateBase, QColor(t["surface2"]))
    p.setColor(QPalette.Text, QColor(t["text"]))
    p.setColor(QPalette.Button, QColor(t["surface"]))
    p.setColor(QPalette.ButtonText, QColor(t["text"]))
    p.setColor(QPalette.Highlight, QColor(t["primary"]))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase, QColor(t["surface"]))
    p.setColor(QPalette.ToolTipText, QColor(t["text"]))
    p.setColor(QPalette.PlaceholderText, QColor(t["muted"]))
    p.setColor(QPalette.Link, QColor(t["primary"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(t["muted"]))
    return p


def apply(app, setting: str = "system") -> bool:
    """テーマを適用する。ダークならTrue."""
    global _current_dark
    _current_dark = resolve(setting)
    app.setStyle("Fusion")
    app.setPalette(palette(_current_dark))
    app.setStyleSheet(build_style(_current_dark))
    return _current_dark


def state_color(state: str) -> str:
    return STATE_COLORS[_current_dark].get(state, tokens()["text2"])
