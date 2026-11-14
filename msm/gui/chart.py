"""シンプルな折れ線グラフ (QPainter)。1グラフ1指標・十字線とツールチップ付き."""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

LIGHT = {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "grid": "#e6e5e1", "series": "#2a78d6",
         "ref": "#8a8984"}
DARK = {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "grid": "#33332f", "series": "#3987e5",
        "ref": "#8f8e88"}


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    import math

    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):  # 5分割したとき目盛りがきりの良い値になる候補
        if v <= m * exp:
            return m * exp
    return 10 * exp


class LineChart(QWidget):
    def __init__(self, title: str, unit: str, fmt: str = "{:.1f}", fixed_max: float | None = None, step: bool = False):
        super().__init__()
        self.title = title
        self.unit = unit
        self.fmt = fmt
        self.fixed_max = fixed_max
        self.step = step
        self.points: list[tuple[float, float | None]] = []
        self.ref_value: float | None = None
        self.ref_label = ""
        self.span: tuple[float, float] = (time.time() - 3600, time.time())
        self.hover_x: float | None = None
        self.colors = LIGHT
        self.empty_text = "データがありません"
        self.setMouseTracking(True)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_dark(self, dark: bool) -> None:
        self.colors = DARK if dark else LIGHT
        self.update()

    def set_data(self, points, span, ref_value=None, ref_label="") -> None:
        self.points = points
        self.span = span
        self.ref_value = ref_value
        self.ref_label = ref_label
        self.update()

    # ---- 座標 ----
    def _plot_rect(self) -> QRectF:
        return QRectF(56, 30, max(10, self.width() - 56 - 16), max(10, self.height() - 30 - 26))

    def _ymax(self) -> float:
        v = self._ymax_raw()
        if self.fmt == "{:.0f}":  # 整数の指標は目盛りも整数にする
            return max(5.0, float(-(-int(v) // 5) * 5))
        return v

    def _ymax_raw(self) -> float:
        if self.fixed_max is not None:
            vals = [v for _, v in self.points if v is not None]
            return max(self.fixed_max, _nice_max(max(vals))) if vals and max(vals) > self.fixed_max else self.fixed_max
        vals = [v for _, v in self.points if v is not None]
        if self.ref_value:
            vals.append(self.ref_value)
        return _nice_max(max(vals) * 1.1) if vals else 1.0

    def _xy(self, t: float, v: float, r: QRectF, ymax: float) -> QPointF:
        t0, t1 = self.span
        x = r.left() + (t - t0) / max(1e-9, t1 - t0) * r.width()
        y = r.bottom() - (v / ymax) * r.height()
        return QPointF(x, y)

    # ---- 描画 ----
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self.colors
        p.fillRect(self.rect(), QColor(c["surface"]))
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(c["ink"]))
        latest = next((v for _, v in reversed(self.points) if v is not None), None)
        head = self.title + (f"   最新 {self.fmt.format(latest)}{self.unit}" if latest is not None else "")
        p.drawText(QRectF(8, 4, self.width() - 16, 22), Qt.AlignLeft | Qt.AlignVCenter, head)
        p.setFont(self.font())
        r = self._plot_rect()
        ymax = self._ymax()
        # グリッドと目盛り (控えめ)
        p.setPen(QPen(QColor(c["grid"]), 1))
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1.5))
        p.setFont(small)
        for i in range(6):
            y = r.bottom() - r.height() * i / 5
            p.setPen(QPen(QColor(c["grid"]), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(c["ink2"]))
            p.drawText(QRectF(0, y - 8, r.left() - 6, 16), Qt.AlignRight | Qt.AlignVCenter,
                       self.fmt.format(ymax * i / 5))
        t0, t1 = self.span
        for i in range(5):
            t = t0 + (t1 - t0) * i / 4
            x = r.left() + r.width() * i / 4
            label = time.strftime("%m/%d %H:%M" if t1 - t0 > 86400 else "%H:%M", time.localtime(t))
            p.drawText(QRectF(x - 45, r.bottom() + 4, 90, 18), Qt.AlignHCenter | Qt.AlignTop, label)
        # 基準線 (割り当て上限など)
        if self.ref_value:
            y = self._xy(t0, self.ref_value, r, ymax).y()
            pen = QPen(QColor(c["ref"]), 1, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(c["ink2"]))
            p.drawText(QRectF(r.left() + 6, y - 18, 260, 16), Qt.AlignLeft | Qt.AlignVCenter, self.ref_label)
        pts = [(t, v) for t, v in self.points if t0 <= t <= t1]
        if not [v for _, v in pts if v is not None]:
            p.setPen(QColor(c["ink2"]))
            p.drawText(r, Qt.AlignCenter, self.empty_text)
            p.end()
            return
        # 折れ線 (欠測は線を切る)
        path = QPainterPath()
        started = False
        prev = None
        gap = max(60.0, (t1 - t0) / 200)
        for t, v in pts:
            if v is None or (prev is not None and t - prev > gap * 3):
                started = False
            if v is None:
                prev = t
                continue
            pt = self._xy(t, v, r, ymax)
            if not started:
                path.moveTo(pt)
                started = True
            else:
                if self.step:
                    path.lineTo(QPointF(pt.x(), path.currentPosition().y()))
                path.lineTo(pt)
            prev = t
        p.setPen(QPen(QColor(c["series"]), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        # 十字線とツールチップ
        if self.hover_x is not None and r.left() <= self.hover_x <= r.right():
            t = t0 + (self.hover_x - r.left()) / r.width() * (t1 - t0)
            near = min((pp for pp in pts if pp[1] is not None), key=lambda pp: abs(pp[0] - t), default=None)
            if near:
                pt = self._xy(near[0], near[1], r, ymax)
                p.setPen(QPen(QColor(c["ink2"]), 1))
                p.drawLine(QPointF(pt.x(), r.top()), QPointF(pt.x(), r.bottom()))
                p.setPen(QPen(QColor(c["surface"]), 2))
                p.setBrush(QColor(c["series"]))
                p.drawEllipse(pt, 4.5, 4.5)
                text = f"{self.fmt.format(near[1])}{self.unit}   {time.strftime('%m/%d %H:%M:%S', time.localtime(near[0]))}"
                fm = p.fontMetrics()
                w = fm.horizontalAdvance(text) + 16
                bx = min(max(r.left(), pt.x() + 10), r.right() - w)
                box = QRectF(bx, r.top() + 4, w, 22)
                p.setPen(QPen(QColor(c["grid"]), 1))
                p.setBrush(QColor(c["surface"]))
                p.drawRoundedRect(box, 4, 4)
                p.setPen(QColor(c["ink"]))
                p.drawText(box, Qt.AlignCenter, text)
        p.end()

    def mouseMoveEvent(self, ev) -> None:
        self.hover_x = ev.position().x()
        self.update()

    def leaveEvent(self, ev) -> None:
        self.hover_x = None
        self.update()
