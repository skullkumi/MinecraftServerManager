"""前提MODの自動取得 (画面部分)。MOD管理・MOD診断から使う."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from msm.gui import common as C
from msm.mods import importer, resolver
from msm.server.process import registry


class CandidateDialog(QDialog):
    def __init__(self, parent, cands: list[resolver.Candidate], server):
        super().__init__(parent)
        self.setWindowTitle("前提MODの自動取得")
        self.cands = cands
        v = QVBoxLayout(self)
        v.addWidget(C.help_label(
            f"Modrinthで Minecraft {server.mc_version} / {server.type_label} に対応する版を探しました。\n"
            "チェックしたMODをダウンロードし (ハッシュで検証)、サーバーのmodsフォルダーへ追加します。既存のファイルは上書きしません。\n"
            "確信度「中」は名前が完全には一致しなかったものです。ページで内容を確認してからチェックしてください。"))
        self.table = QTableWidget(len(cands), 6)
        self.table.setHorizontalHeaderLabels(["取得", "探したMOD", "見つかったMOD", "バージョン / ファイル", "確信度", "理由"])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        for r, c in enumerate(cands):
            chk = QTableWidgetItem()
            if c.ok:
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Checked if c.confidence == "高" else Qt.Unchecked)
            else:
                chk.setFlags(Qt.ItemIsEnabled)
                chk.setText("×")
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(c.requested if not c.as_dependency_of else f"({c.as_dependency_of} の前提)"))
            self.table.setItem(r, 2, QTableWidgetItem(c.title if c.project_id else "-"))
            self.table.setItem(r, 3, QTableWidgetItem(f"{c.version_number}\n{c.file_name}" if c.ok else ""))
            self.table.setItem(r, 4, QTableWidgetItem(c.confidence or "-"))
            self.table.setItem(r, 5, QTableWidgetItem(c.error or c.reason))
        self.table.resizeRowsToContents()
        self.table.setMinimumSize(980, 360)
        v.addWidget(self.table)
        v.addLayout(C.hbox(C.button("選択したMODのページを開く", self.open_page)))
        if any(not c.ok for c in cands):
            v.addWidget(QLabel("※ 見つからなかったMODは「MOD > 手動ダウンロード補助」タブから取り込めます。"))
        bb = QDialogButtonBox()
        bb.addButton("ダウンロードして追加", QDialogButtonBox.AcceptRole)
        bb.addButton("キャンセル", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def open_page(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.cands) and self.cands[r].project_id:
            C.open_url(self, self.cands[r].page)

    def selected(self) -> list[resolver.Candidate]:
        return [c for r, c in enumerate(self.cands) if c.ok and self.table.item(r, 0).checkState() == Qt.Checked]


def fetch_missing(parent, server, queries: list[str], on_done=None) -> None:
    queries = [q for q in dict.fromkeys(q.strip() for q in queries) if q]
    if not queries:
        C.info(parent, "不足している前提MODは見つかっていません。")
        return
    sp = registry.find(server.id)
    if sp and sp.running:
        C.warn(parent, "サーバーを停止してから実行してください。")
        return

    def job(p, l, c):
        return resolver.resolve(server, queries, p)

    def got(cands):
        if not cands:
            C.info(parent, "取得する必要のあるMODはありません (すでに導入済みです)。")
            return
        dlg = CandidateDialog(parent, cands, server)
        if dlg.exec() != QDialog.Accepted:
            return
        sel = dlg.selected()
        if not sel:
            return

        def job2(p, l, c):
            files = resolver.download(sel, p, c)
            return importer.plan_for_files(files, server.mods_dir, server.server_type, True)

        def done2(plan):
            from msm.gui.pages.mods import PlanDialog

            pd = PlanDialog(parent, plan)
            if pd.exec() != QDialog.Accepted:
                return
            res = importer.apply_plan(server, plan, pd.included_review(), "前提MODの自動取得 (Modrinth)")
            C.info(parent, "結果:\n" + ("\n".join(res) or "追加したMODはありません"))
            if on_done:
                on_done()

        C.run_task(parent, "前提MODをダウンロードしています", job2, done2)

    C.run_task(parent, "前提MODを探しています", job, got)
