"""複数の画面をタブでまとめる入れ物."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QScrollArea, QTabWidget, QVBoxLayout

from msm.gui import common as C


def _scroll(w) -> QScrollArea:
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QScrollArea.NoFrame)
    sc.setWidget(w)
    return sc


def hide_page_title(page) -> None:
    """タブの中では各画面の大見出しを隠す (説明文は残す)."""
    for lab in page.findChildren(QLabel):
        if lab.objectName() == "title":
            lab.hide()
            break


class TabbedPage(C.Page):
    def __init__(self, ctx, title: str, subtitle: str, tabs: list[tuple[str, str, C.Page]]):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(C.page_header(title, subtitle))
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.keys: list[str] = []
        self.inner: dict[str, C.Page] = {}
        for key, label, page in tabs:
            hide_page_title(page)
            self.tabs.addTab(_scroll(page), label)
            self.keys.append(key)
            self.inner[key] = page
        self.tabs.currentChanged.connect(lambda *_: self.refresh())
        lay.addWidget(self.tabs, 1)

    def select(self, key: str) -> None:
        if key in self.keys:
            self.tabs.setCurrentIndex(self.keys.index(key))

    def current_key(self) -> str:
        return self.keys[self.tabs.currentIndex()]

    def refresh(self) -> None:
        self.inner[self.current_key()].refresh()

    def set_dark(self, dark: bool) -> None:
        for p in self.inner.values():
            if hasattr(p, "set_dark"):
                p.set_dark(dark)
