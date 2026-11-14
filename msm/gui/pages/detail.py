"""サーバー詳細 / コンソール."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout

from msm.core.util import open_path
from msm.gui import actions
from msm.gui import common as C
from msm.java import manager as jm
from msm.server.process import build_command, registry


class DetailPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("コンソール", "サーバーの出力を表示し、コマンドを送信できます。"))
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.info)
        self.b_start = C.button("起動", self.start, "primary")
        self.b_stop = C.button("停止", self.stop)
        self.b_restart = C.button("再起動", self.restart)
        self.b_kill = C.button("強制終了", self.kill, "danger")
        lay.addLayout(C.hbox(self.b_start, self.b_stop, self.b_restart, self.b_kill,
                             C.button("フォルダーを開く", self.open_folder), C.button("診断", lambda: ctx.navigate.emit("diagnose"))))
        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)
        self.console.setMinimumHeight(380)
        lay.addWidget(self.console, 1)
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("コマンドを入力 (例: say こんにちは / list / time set day)  ※先頭の / は不要")
        self.cmd.returnPressed.connect(self.send)
        self.b_send = C.button("送信", self.send)
        lay.addLayout(C.hbox(self.cmd, self.b_send, stretch_end=False))
        self.auto_scroll = True
        self._shown_id = None
        ctx.console_line.connect(self._line, Qt.QueuedConnection)
        ctx.state_changed.connect(lambda *_: self._update_buttons(), Qt.QueuedConnection)

    def refresh(self) -> None:
        s = self.ctx.current()
        if not s:
            self.info.setText("サーバーが選択されていません。")
            self.console.clear()
            self._update_buttons()
            return
        java, req, note = jm.resolve_for_server(s)
        cmdline = " ".join(build_command(s, java.path)) if java else "(Javaが未設定)"
        self.info.setText(
            f"<b>{s.name}</b> - Minecraft {s.mc_version} / {s.type_label} {s.loader_version}<br>"
            f"フォルダー: {s.directory}<br>Java: {java.label + ' - ' + note if java else note}<br>"
            f"<span style='color:#616e7c'>起動コマンド: {cmdline}</span>")
        if self._shown_id != s.id:
            self._shown_id = s.id
            self.console.clear()
            sp = registry.find(s.id)
            if sp:
                self.console.setPlainText("\n".join(list(sp.lines)[-2000:]))
                self.console.moveCursor(QTextCursor.End)
        self._update_buttons()

    def _update_buttons(self):
        s = self.ctx.current()
        sp = registry.find(s.id) if s else None
        running = bool(sp and sp.running)
        self.b_start.setEnabled(bool(s) and not running)
        for b in (self.b_stop, self.b_restart, self.b_kill, self.b_send, self.cmd):
            b.setEnabled(running)

    def _line(self, sid: str, line: str):
        s = self.ctx.current()
        if not s or sid != s.id:
            return
        if self._shown_id != sid:
            self._shown_id = sid
        sb = self.console.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        self.console.appendPlainText(line)
        if at_bottom:
            sb.setValue(sb.maximum())

    def send(self):
        s = self.ctx.current()
        sp = registry.find(s.id) if s else None
        text = self.cmd.text().strip().lstrip("/")
        if not text or not sp:
            return
        if text.split()[0].lower() == "stop":
            self.cmd.clear()
            self.stop()
            return
        if sp.send_command(text):
            self.console.appendPlainText(f"> {text}")
            self.cmd.clear()

    def start(self):
        s = self.ctx.current()
        if s:
            self.console.clear()
            actions.start_server(self, self.ctx, s)
            self._update_buttons()

    def stop(self):
        s = self.ctx.current()
        if s:
            actions.stop_server(self, self.ctx, s)

    def restart(self):
        s = self.ctx.current()
        if s:
            actions.restart_server(self, self.ctx, s)

    def kill(self):
        s = self.ctx.current()
        sp = registry.find(s.id) if s else None
        if sp and sp.running and C.confirm(self, "強制終了するとワールドが破損する恐れがあります。強制終了しますか？", danger=True):
            sp.kill()

    def open_folder(self):
        s = self.ctx.current()
        if s:
            open_path(s.path)
