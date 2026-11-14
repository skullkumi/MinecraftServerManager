"""アプリアイコン (assets/icon.ico) を生成する。開発用スクリプト (配布物には含めない)."""
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

app = QGuiApplication(sys.argv)
out = Path(__file__).resolve().parents[1] / "assets"
out.mkdir(exist_ok=True)


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64
    p.setBrush(QColor("#1f2933"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 12 * s, 12 * s)
    for i in range(3):
        y = (12 + i * 15) * s
        p.setBrush(QColor("#3e4c59"))
        p.drawRoundedRect(QRectF(10 * s, y, 44 * s, 11 * s), 3 * s, 3 * s)
        p.setBrush(QColor("#52c878") if i < 2 else QColor("#f5b041"))
        p.drawEllipse(QRectF(15 * s, y + 3 * s, 5 * s, 5 * s))
        p.setBrush(QColor("#9aa5b1"))
        p.drawRect(QRectF(26 * s, y + 4.5 * s, 22 * s, 2 * s))
    p.end()
    return img


draw(256).save(str(out / "icon.ico"), "ICO")
draw(256).save(str(out / "icon.png"), "PNG")
print("icon written", (out / "icon.ico").stat().st_size)
