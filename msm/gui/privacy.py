"""画面に映ってはいけない情報の伏せ表示 (配信・画面共有・スクリーンショット対策).

既定でオン。伏せる対象:
- グローバルIP・公開アドレス (playit等) ・LANのIPアドレス → 「👁」ボタンで一時的に表示できる
- パスに含まれるWindowsのユーザー名 (C:\\Users\\<名前>) → %USERPROFILE% と表示
- コンソール・ログ中のプレイヤーの接続元IP
コピー機能は本当の値をコピーする。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from msm.core.db import get_db

MASK = "●●●●●●"
IPV4 = r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"
IP_PORT_RE = re.compile(IPV4 + r"(?::\d{1,5})?")
LOGIN_IP_RE = re.compile(r"\[/(?:\d{1,3}\.){3}\d{1,3}:\d+\]")  # 例: Steve[/1.2.3.4:5678] logged in
HOME = str(Path.home())


def enabled() -> bool:
    return bool(get_db().get_setting("privacy_mode", True))


def set_enabled(v: bool) -> None:
    get_db().set_setting("privacy_mode", bool(v))


def _is_safe_ip(ip: str) -> bool:
    return ip.startswith(("127.", "0.0.0.0", "255."))


def mask_ips(text: str) -> str:
    def rep(m):
        s = m.group(0)
        ip = s.split(":")[0]
        if _is_safe_ip(ip):
            return s
        parts = ip.split(".")
        if not all(p.isdigit() and int(p) <= 255 for p in parts):
            return s
        return MASK + (":" + s.split(":")[1] if ":" in s else "")
    return IP_PORT_RE.sub(rep, text)


def mask_path(text: str) -> str:
    """パス中のユーザー名を伏せる."""
    if not text:
        return text
    out = text
    for home in {HOME, HOME.replace("\\", "/")}:
        out = re.sub(re.escape(home), "%USERPROFILE%", out, flags=re.I)
    user = os.environ.get("USERNAME", "")
    if user and len(user) >= 3:
        out = re.sub(rf"(?i)(\\Users\\|/Users/){re.escape(user)}\b", r"\1<ユーザー>", out)
    return out


def text(s: str) -> str:
    """表示用: プライバシーモードならIPとユーザー名を伏せる."""
    if not s or not enabled():
        return s
    return mask_path(mask_ips(s))


def console(line: str) -> str:
    """コンソール表示用: 接続元IPとユーザー名を伏せる (バージョン番号などを誤って伏せないよう対象を絞る)."""
    if not enabled():
        return line
    line = LOGIN_IP_RE.sub("[/" + MASK + "]", line)
    line = re.sub(r"(connect_addr: |peer_addr: |origin_addr=)" + IPV4, r"\1" + MASK, line)
    return mask_path(line)


def path(p) -> str:
    s = str(p)
    return mask_path(s) if enabled() else s


class SecretLabel(QWidget):
    """伏せ字で表示し、👁ボタンで表示/非表示を切り替えるラベル (コピーは本当の値)."""

    def __init__(self, placeholder: str = "-", big: bool = False, copy_button: bool = True):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.label = QLabel(placeholder)
        if big:
            self.label.setObjectName("value")
        self.eye = QPushButton("👁")
        self.eye.setObjectName("icon")
        self.eye.setToolTip("表示 / 非表示 (配信中は注意)")
        self.eye.setCheckable(True)
        self.eye.toggled.connect(self._render)
        lay.addWidget(self.label)
        lay.addWidget(self.eye)
        self.copy = None
        if copy_button:
            self.copy = QPushButton("コピー")
            self.copy.clicked.connect(self._copy)
            lay.addWidget(self.copy)
        lay.addStretch(1)
        self._value = ""
        self._placeholder = placeholder
        self.on_copied = None
        self._render()

    def set_value(self, value: str, placeholder: str | None = None) -> None:
        self._value = value or ""
        if placeholder is not None:
            self._placeholder = placeholder
        self._render()

    def value(self) -> str:
        return self._value

    def _render(self, *_):
        has = bool(self._value)
        masked = has and enabled() and not self.eye.isChecked()
        self.eye.setVisible(has and enabled())
        if self.copy:
            self.copy.setEnabled(has)
        self.label.setStyleSheet("" if has else "font-weight: normal; font-size: 10pt;")
        if not has:
            self.label.setText(self._placeholder)
        elif masked:
            self.label.setText(mask_ips(self._value) if IP_PORT_RE.search(self._value) else MASK)
        else:
            self.label.setText(self._value)

    def _copy(self):
        from msm.gui.common import copy_text

        if self._value:
            copy_text(self._value)
            if self.on_copied:
                self.on_copied(self._value)


_hooked = False


def install_hooks() -> None:
    """QLabel / QPlainTextEdit への表示時に、パス中のユーザー名とプレイヤーの接続元IPを自動で伏せる."""
    global _hooked
    if _hooked:
        return
    _hooked = True
    from PySide6.QtWidgets import QLabel, QPlainTextEdit

    orig_label = QLabel.setText
    orig_set = QPlainTextEdit.setPlainText
    orig_append = QPlainTextEdit.appendPlainText

    def label_set(self, t):
        if t and enabled() and isinstance(t, str):
            t = mask_path(t)
        return orig_label(self, t)

    def pte_set(self, t):
        if t and enabled() and isinstance(t, str):
            t = console(t)
        return orig_set(self, t)

    def pte_append(self, t):
        if t and enabled() and isinstance(t, str):
            t = console(t)
        return orig_append(self, t)

    QLabel.setText = label_set
    QPlainTextEdit.setPlainText = pte_set
    QPlainTextEdit.appendPlainText = pte_append


def to_real_path(text: str) -> str:
    """入力欄の %USERPROFILE% 表記などを実際のパスに戻す."""
    return os.path.expandvars(text.strip()) if text else text


def secret_line_edit(edit) -> None:
    """QLineEdit を伏せ字入力にする (プライバシーモード時)."""
    from PySide6.QtWidgets import QLineEdit

    edit.setEchoMode(QLineEdit.Password if enabled() else QLineEdit.Normal)
