"""表示言語 (日本語 / English).

英語モードでは、日本語の表示文字列を辞書 (i18n_en.EN) で置き換える。
- 画面を作った後に部品を走査して置き換える (ボタン・見出し・ラベル・表の見出し・選択肢など)
- その後の setText などの呼び出しも置き換える
- 辞書に無い文 (主に長い説明文・ログ由来のメッセージ) は日本語のまま表示する
"""
from __future__ import annotations

import re

from msm.core.db import get_db

_lang: str | None = None
_EN: dict | None = None


def language() -> str:
    global _lang
    if _lang is None:
        v = get_db().get_setting("language", "ja") or "ja"
        if v == "system":
            import locale

            v = "ja" if (locale.getlocale()[0] or "").lower().startswith(("ja", "japanese")) else "en"
        _lang = v
    return _lang


def set_language(v: str) -> None:
    get_db().set_setting("language", v)


def _dict() -> dict:
    global _EN
    if _EN is None:
        from msm.gui.i18n_en import EN

        _EN = EN
    return _EN


_AFFIX = re.compile(r"^([\s\u3000○△※■▶⚠●→・\-]*)(.*?)([\s\u3000⚠]*)$", re.S)


def tr(text):
    """1文ずつ辞書で訳す。辞書に無い行はそのまま."""
    if not isinstance(text, str) or not text or language() != "en":
        return text
    d = _dict()
    if text in d:
        return d[text]
    if "\n" in text:
        return "\n".join(tr(l) for l in text.split("\n"))
    s = text.strip()
    if s in d:
        return text.replace(s, d[s])
    m = _AFFIX.match(text)  # 先頭・末尾の記号 (○ △ ※ ⚠ など) を除いて照合する
    if m and m.group(2) in d:
        return m.group(1) + d[m.group(2)] + m.group(3)
    return text


def T(ja: str, en: str) -> str:
    """文章を組み立てる箇所用: 言語に応じてどちらかを返す."""
    return en if language() == "en" else ja


def translate_tree(root) -> None:
    """ウィジェット以下の表示文字列を英語に置き換える."""
    if language() != "en":
        return
    from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QComboBox, QGroupBox, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QTableWidget,
                                   QTabWidget, QTreeWidget, QWidget, QWizardPage)

    widgets = [root, *root.findChildren(QWidget)]
    for w in widgets:
        try:
            if isinstance(w, QLabel):
                w.setText(w.text())
            elif isinstance(w, QAbstractButton):
                w.setText(w.text())
                m = getattr(w, "menu", lambda: None)()
                if m:
                    for a in m.actions():
                        a.setText(tr(a.text()))
            elif isinstance(w, QGroupBox):
                w.setTitle(tr(w.title()))
            elif isinstance(w, QTabWidget):
                for i in range(w.count()):
                    w.setTabText(i, tr(w.tabText(i)))
            elif isinstance(w, QTableWidget):
                for i in range(w.columnCount()):
                    it = w.horizontalHeaderItem(i)
                    if it:
                        it.setText(tr(it.text()))
            elif isinstance(w, QTreeWidget):
                h = w.headerItem()
                for i in range(h.columnCount()):
                    h.setText(i, tr(h.text(i)))
            elif isinstance(w, QAbstractSpinBox) and hasattr(w, "suffix"):
                if w.suffix():
                    w.setSuffix(tr(w.suffix()))
            elif isinstance(w, QComboBox):
                for i in range(w.count()):
                    w.setItemText(i, tr(w.itemText(i)))
            elif isinstance(w, QListWidget):
                for i in range(w.count()):
                    it = w.item(i)
                    it.setText(tr(it.text()))
            if isinstance(w, (QLineEdit, QPlainTextEdit)) and w.placeholderText():
                w.setPlaceholderText(tr(w.placeholderText()))
            if isinstance(w, QWizardPage):
                w.setTitle(tr(w.title()))
                w.setSubTitle(tr(w.subTitle()))
            if w.toolTip():
                w.setToolTip(tr(w.toolTip()))
            if w.isWindow() and w.windowTitle():
                w.setWindowTitle(tr(w.windowTitle()))
        except Exception:
            continue


_hooked = False


def install_hooks() -> None:
    """以降の表示更新も英語にする (英語モードのときだけ)."""
    global _hooked
    if _hooked or language() != "en":
        return
    _hooked = True
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QDialog, QLabel, QMessageBox, QTableWidgetItem

    def wrap(cls, name, idx=0):
        orig = getattr(cls, name)

        def f(self, *args, **kw):
            args = list(args)
            if len(args) > idx:
                args[idx] = tr(args[idx])
            return orig(self, *args, **kw)

        setattr(cls, name, f)

    # QLabel.setText は privacy のフックの外側に重ねる
    wrap(QLabel, "setText")
    wrap(QAbstractButton, "setText")
    wrap(QComboBox, "addItem")
    wrap(QTableWidgetItem, "setText")
    wrap(QMessageBox, "setText")
    wrap(QMessageBox, "setWindowTitle")
    from PySide6.QtWidgets import QStatusBar

    wrap(QStatusBar, "showMessage")
    orig_exec = QDialog.exec

    def exec_(self, *a, **k):
        translate_tree(self)
        return orig_exec(self, *a, **k)

    QDialog.exec = exec_
    orig_add_items = QComboBox.addItems
    QComboBox.addItems = lambda self, items: orig_add_items(self, [tr(i) for i in items])
    for name in ("information", "warning", "critical"):
        orig_static = getattr(QMessageBox, name)

        def make(o):
            return staticmethod(lambda parent, title, text, *a, **k: o(parent, tr(title), tr(text), *a, **k))

        setattr(QMessageBox, name, make(orig_static))
