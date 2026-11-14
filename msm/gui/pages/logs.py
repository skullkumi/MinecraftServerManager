"""ログ表示."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox, QPlainTextEdit, QVBoxLayout

from msm.core.paths import logs_dir
from msm.core.util import open_path
from msm.diagnose.analyzer import _tail
from msm.gui import common as C


class LogsPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("ログ", "サーバーのログ・アプリが保存したコンソール記録・アプリ自身のログを表示します。"))
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self.load)
        lay.addLayout(C.hbox(self.combo, C.button("再読み込み", self.refresh), C.button("フォルダーを開く", self.open_dir), stretch_end=False))
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(C.mono_font())
        self.view.setMinimumHeight(520)
        lay.addWidget(self.view, 1)

    def refresh(self) -> None:
        s = self.ctx.current()
        self.combo.blockSignals(True)
        self.combo.clear()
        if s:
            for rel in ("logs/latest.log", "logs/debug.log"):
                p = s.path / rel
                if p.exists():
                    self.combo.addItem(f"サーバー: {rel}", str(p))
            cr = s.path / "crash-reports"
            if cr.exists():
                for p in sorted(cr.glob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
                    self.combo.addItem(f"クラッシュレポート: {p.name}", str(p))
            cdir = logs_dir() / "servers" / s.id
            if cdir.exists():
                for p in sorted(cdir.glob("console-*.log"), reverse=True)[:20]:
                    self.combo.addItem(f"コンソール記録: {p.name}", str(p))
            for p in (s.path / "IMPORT_RESULT.txt", s.path / "IMPORT_FAILED.txt"):
                if p.exists():
                    self.combo.addItem(f"インポート: {p.name}", str(p))
            rep = s.path / ".msm" / "reports"
            if rep.exists():
                for p in sorted(rep.glob("*.txt"), reverse=True)[:10]:
                    self.combo.addItem(f"診断レポート: {p.name}", str(p))
        self.combo.addItem("アプリのログ: app.log", str(logs_dir() / "app.log"))
        self.combo.blockSignals(False)
        self.load()

    def load(self, *_):
        p = self.combo.currentData()
        if not p or not Path(p).exists():
            self.view.setPlainText("")
            return
        self.view.setPlainText(_tail(Path(p), 2_000_000))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def open_dir(self):
        p = self.combo.currentData()
        if p:
            open_path(Path(p))
