"""GUI共通部品: バックグラウンド処理・進捗ダイアログ・メッセージ・スタイル."""
from __future__ import annotations

import threading
import traceback
import webbrowser
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from msm import APP_NAME
from msm.core.util import CancelToken, Cancelled, log

STYLE = __import__("msm.gui.theme", fromlist=["x"]).build_style(False)  # 互換用 (テーマは theme.apply で適用)

LEVEL_COLORS = {"確定": "#c0392b", "可能性:高": "#d35400", "可能性:中": "#b7950b", "不明": "#7f8c8d"}
STATE_COLORS = {"running": "#2e8b57", "starting": "#d4a017", "stopping": "#d4a017", "stopped": "#7b8794",
                "crashed": "#c0392b"}


def state_color(state: str) -> str:
    from msm.gui import theme

    return theme.state_color(state)


def pill(state: str, label: str) -> str:
    """状態を色付きの丸と文字で表す (色だけに頼らない)."""
    from msm.gui.i18n import tr

    c = state_color(state)
    return f"<span style='color:{c}; font-weight:bold'>● {tr(label)}</span>"


# ---------------------------------------------------------------- メッセージ
def _p(text: str) -> str:
    from msm.gui import privacy

    return privacy.mask_path(text) if privacy.enabled() else text


def info(parent, text: str, title: str = APP_NAME) -> None:
    QMessageBox.information(parent, title, _p(text))


def warn(parent, text: str, title: str = APP_NAME) -> None:
    QMessageBox.warning(parent, title, _p(text))


def error(parent, text: str, title: str = APP_NAME) -> None:
    QMessageBox.critical(parent, title, _p(text))


def confirm(parent, text: str, title: str = "確認", danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(_p(text))
    yes = box.addButton("はい", QMessageBox.YesRole)
    box.addButton("いいえ", QMessageBox.NoRole)
    box.exec()
    return box.clickedButton() is yes


def ask_choice(parent, text: str, choices: list[str], title: str = "選択") -> int:
    """ボタンで選ばせる。キャンセル時は -1."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(_p(text))
    btns = [box.addButton(c, QMessageBox.AcceptRole) for c in choices]
    cancel = box.addButton("キャンセル", QMessageBox.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked is cancel or clicked not in btns:
        return -1
    return btns.index(clicked)


def open_url(parent, url: str, ask: bool = True) -> None:
    if not url:
        return
    if ask and not confirm(parent, f"既定のWebブラウザで次のページを開きます。\n\n{url}\n\nよろしいですか？"):
        return
    webbrowser.open(url)


def copy_text(text: str) -> None:
    QApplication.clipboard().setText(text)


# ---------------------------------------------------------------- レイアウト補助
def page_header(title: str, subtitle: str = "") -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 6)
    t = QLabel(title)
    t.setObjectName("title")
    lay.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("subtitle")
        s.setWordWrap(True)
        lay.addWidget(s)
    return w


def help_label(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("help")
    l.setWordWrap(True)
    return l


def card(title: str = "", subtitle: str = "") -> QFrame:
    """角丸のカード。card.body (QVBoxLayout) に中身を追加する."""
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    if title:
        t = QLabel(title)
        t.setStyleSheet("font-size: 11.5pt; font-weight: bold;")
        lay.addWidget(t)
    if subtitle:
        lay.addWidget(help_label(subtitle))
    f.body = lay
    return f


def tile(caption: str) -> tuple[QFrame, QLabel, QLabel]:
    """数値表示用の小さなタイル (見出し・値・補足)."""
    f = QFrame()
    f.setObjectName("tile")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(2)
    cap = QLabel(caption)
    cap.setObjectName("caption")
    val = QLabel("-")
    val.setObjectName("value")
    sub = QLabel("")
    sub.setObjectName("caption")
    lay.addWidget(cap)
    lay.addWidget(val)
    lay.addWidget(sub)
    return f, val, sub


def button(text: str, slot: Callable | None = None, kind: str = "", tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return b


def menu_button(text: str, items: list, kind: str = "") -> QPushButton:
    """押すとメニューが開くボタン。items = [(表示名, 処理) or None(区切り線)]."""
    from PySide6.QtWidgets import QMenu

    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    m = QMenu(b)
    for it in items:
        if it is None:
            m.addSeparator()
        else:
            m.addAction(it[0], it[1])
    b.setMenu(m)
    b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return b


def hbox(*widgets, stretch_end: bool = True) -> QHBoxLayout:
    h = QHBoxLayout()
    for w in widgets:
        if isinstance(w, QWidget):
            h.addWidget(w)
        elif w is None:
            h.addStretch(1)
        else:
            h.addLayout(w)
    if stretch_end:
        h.addStretch(1)
    return h


class Page(QWidget):
    """ページの基底クラス。表示されたときに refresh() が呼ばれる."""

    title = ""
    needs_server = False

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setObjectName("page")

    def refresh(self) -> None:
        pass

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self.refresh)


# ---------------------------------------------------------------- バックグラウンド処理
class TaskSignals(QObject):
    progress = Signal(str, object)
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str, str)


class TaskDialog(QDialog):
    def __init__(self, parent, title: str, cancellable: bool):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(620)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        lay = QVBoxLayout(self)
        self.label = QLabel("準備中...")
        self.label.setWordWrap(True)
        lay.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        lay.addWidget(self.bar)
        self.logview = QPlainTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setMaximumBlockCount(3000)
        self.logview.setMinimumHeight(180)
        self.logview.setObjectName("console")
        lay.addWidget(self.logview)
        self.buttons = QDialogButtonBox()
        self.cancel_btn = self.buttons.addButton("キャンセル", QDialogButtonBox.RejectRole)
        self.cancel_btn.setEnabled(cancellable)
        self.close_btn = self.buttons.addButton("閉じる", QDialogButtonBox.AcceptRole)
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        lay.addWidget(self.buttons)
        self.token = CancelToken()
        self.cancel_btn.clicked.connect(self._cancel)
        self.finished_ok = False

    def _cancel(self) -> None:
        self.token.cancel()
        self.label.setText("キャンセルしています...")
        self.cancel_btn.setEnabled(False)

    def reject(self) -> None:  # Escや×で閉じても処理は止めずキャンセル要求にする
        if self.close_btn.isEnabled():
            super().reject()
        elif self.cancel_btn.isEnabled():
            self._cancel()

    def set_progress(self, text: str, frac) -> None:
        if text:
            self.label.setText(text)
        if frac is None:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(max(0.0, min(1.0, float(frac))) * 1000))

    def append(self, line: str) -> None:
        self.logview.appendPlainText(line)


def run_task(parent, title: str, fn: Callable[..., Any], on_done: Callable[[Any], None] | None = None,
             cancellable: bool = True, keep_open: bool = False, on_fail: Callable[[str], None] | None = None) -> None:
    """fn(progress, log, cancel) を別スレッドで実行し、進捗ダイアログを表示する."""
    dlg = TaskDialog(parent, title, cancellable)
    sig = TaskSignals()
    dlg._sig = sig  # GC防止
    sig.progress.connect(dlg.set_progress)
    sig.log.connect(dlg.append)

    def finish_ok(result):
        dlg.finished_ok = True
        dlg.set_progress("完了しました", 1.0)
        dlg.cancel_btn.setEnabled(False)
        dlg.close_btn.setEnabled(True)
        if not keep_open:
            dlg.accept()
        if on_done:
            try:
                on_done(result)
            except Exception:  # noqa: BLE001
                log.exception("on_done")
                error(parent, "処理後の表示更新でエラーが発生しました:\n" + traceback.format_exc(limit=3))

    def finish_fail(msg, tb):
        dlg.set_progress("エラー: " + msg, 0)
        dlg.append(tb)
        dlg.cancel_btn.setEnabled(False)
        dlg.close_btn.setEnabled(True)
        if on_fail:
            on_fail(msg)
        else:
            error(dlg, msg)

    sig.done.connect(finish_ok, Qt.QueuedConnection)
    sig.failed.connect(finish_fail, Qt.QueuedConnection)

    def worker():
        try:
            res = fn(lambda t, f=None: sig.progress.emit(t, f), sig.log.emit, dlg.token)
            sig.done.emit(res)
        except Cancelled:
            sig.failed.emit("キャンセルしました", "")
        except Exception as e:  # noqa: BLE001
            log.exception("task failed: %s", title)
            sig.failed.emit(str(e) or e.__class__.__name__, traceback.format_exc(limit=6))

    threading.Thread(target=worker, daemon=True, name=f"task-{title}").start()
    dlg.exec()


class Background(QObject):
    """ダイアログを出さない軽いバックグラウンド処理."""

    done = Signal(object, object)

    def run(self, fn: Callable[[], Any], cb: Callable[[Any, Exception | None], None]) -> None:
        sig = Background()
        sig.done.connect(lambda r, e: cb(r, e), Qt.QueuedConnection)
        self._keep = getattr(self, "_keep", [])
        self._keep.append(sig)

        def worker():
            try:
                r = fn()
                sig.done.emit(r, None)
            except Exception as e:  # noqa: BLE001
                log.warning("background error: %s", e)
                sig.done.emit(None, e)

        threading.Thread(target=worker, daemon=True).start()


background = None


def bg() -> Background:
    global background
    if background is None:
        background = Background()
    return background


def mono_font() -> QFont:
    f = QFont("Consolas")
    f.setStyleHint(QFont.Monospace)
    return f
