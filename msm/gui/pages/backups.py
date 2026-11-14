"""バックアップ (手動・自動・履歴・復元・整理)."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHeaderView, QLabel, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from msm.backup import backup as bk
from msm.core.db import get_db
from msm.core.util import free_space, human_size, open_path
from msm.gui import common as C
from msm.server import ops
from msm.server.process import registry

KIND_LABELS = {"manual": "手動", "auto": "自動 (定期)", "auto-change": "自動 (変更前)", "pre-restore": "復元前の自動保存"}


class BackupsPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("バックアップ", "設定変更前・MOD取り込み前などに自動でバックアップします。手動でも作成できます。"))
        self.loc = QLabel()
        self.loc.setWordWrap(True)
        lay.addWidget(self.loc)
        self.c_world = QCheckBox("ワールドデータ")
        self.c_world.setChecked(True)
        self.c_mods = QCheckBox("MOD (mods / plugins)")
        self.c_conf = QCheckBox("設定 (server.properties / config / scripts / kubejs など)")
        self.c_conf.setChecked(True)
        lay.addLayout(C.hbox(self.c_world, self.c_mods, self.c_conf))
        self.c_inc = QCheckBox("差分バックアップ (前回から変わったファイルだけ保存。大きなワールドで容量と時間を節約)")
        self.c_auto_inc = QCheckBox("定期・定時の自動バックアップも差分で保存する")
        self.c_auto_inc.toggled.connect(lambda v: get_db().set_setting("backup_incremental", v))
        lay.addLayout(C.hbox(self.c_inc, self.c_auto_inc))
        lay.addLayout(C.hbox(C.button("今すぐバックアップ", self.create, "primary"),
                             C.button("バックアップ先を変更", lambda: self.ctx.navigate.emit("app")),
                             C.button("フォルダーを開く", self.open_dir)))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["日時", "種類", "対象", "サイズ", "メモ"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(300)
        lay.addWidget(self.table, 1)
        self.keep = QSpinBox()
        self.keep.setRange(1, 500)
        self.keep.setValue(10)
        lay.addLayout(C.hbox(C.button("選択したバックアップから復元...", self.restore, "primary"),
                             C.button("直前の状態へ戻す...", self.undo_last),
                             C.button("選択を削除...", self.delete, "danger")))
        lay.addLayout(C.hbox(QLabel("自動バックアップを新しい順に"), self.keep, QLabel("個だけ残して整理"),
                             C.button("整理する...", self.prune)))
        lay.addWidget(C.help_label("※ 定期自動バックアップの間隔は「サーバー設定 > 基本設定」で設定します。\n"
                                   "※ 復元はサーバー停止中のみ行えます。復元前に現在の状態を自動でバックアップします。\n"
                                   "※ 稼働中のワールドは save-off / save-all でデータを書き出してからバックアップします。"))

    def refresh(self) -> None:
        s = self.ctx.current()
        self.setEnabled(s is not None)
        if not s:
            return
        root = bk.backup_root()
        self.loc.setText(f"バックアップ先: {root / s.id}   (空き容量: {human_size(free_space(root))})")
        self.keep.setValue(s.backup_keep)
        self.c_auto_inc.blockSignals(True)
        self.c_auto_inc.setChecked(bool(get_db().get_setting("backup_incremental", True)))
        self.c_auto_inc.blockSignals(False)
        self.recs = bk.list_backups(s.id)
        self.table.setRowCount(len(self.recs))
        for r, b in enumerate(self.recs):
            targets = ", ".join(bk.TARGET_LABELS.get(t, t.replace("path:", "")) for t in b.targets)
            exists = Path(b.path).exists()
            vals = [time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(b.created_at)), KIND_LABELS.get(b.kind, b.kind),
                    targets, (("差分 " if bk.is_snapshot(b) else "") + human_size(b.size)) if exists else "ファイルなし", b.note]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if not exists:
                    it.setForeground(Qt.gray)
                self.table.setItem(r, c, it)

    def _targets(self) -> list[str]:
        t = []
        if self.c_world.isChecked():
            t.append("world")
        if self.c_mods.isChecked():
            t.append("mods")
        if self.c_conf.isChecked():
            t.append("config")
        return t

    def create(self):
        s = self.ctx.current()
        targets = self._targets()
        if not s or not targets:
            C.info(self, "バックアップ対象を選んでください。")
            return
        paths = bk.collect_paths(s, targets)
        size = bk.estimate_size(paths)
        free = free_space(bk.backup_root())
        if free < size * 1.1:
            C.error(self, f"空き容量が不足しています。\n必要: 約{human_size(size)} / 空き: {human_size(free)}")
            return
        inc = self.c_inc.isChecked()
        C.run_task(self, "バックアップしています",
                   lambda p, l, c: ops.backup_with_server_control(s, targets, "manual", "手動バックアップ", p, c, incremental=inc),
                   lambda r: (self.refresh(), self.ctx.status.emit(f"バックアップを作成しました: {r.path}")))

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.recs[rows[0].row()] if rows else None

    def _stopped(self, s) -> bool:
        sp = registry.find(s.id)
        if sp and sp.running:
            C.warn(self, "復元はサーバーを停止してから行ってください。")
            return False
        return True

    def restore(self, rec=None):
        s = self.ctx.current()
        rec = rec or self._selected()
        if not s or not rec or not self._stopped(s):
            return
        targets = ", ".join(bk.TARGET_LABELS.get(t, t) for t in rec.targets)
        if not C.confirm(self, f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(rec.created_at))} のバックアップから復元します。\n"
                               f"対象: {targets}\n\n現在の状態は復元前に自動でバックアップされます。\n"
                               "(ワールドを復元する場合、現在のワールドは .msm/replaced へ移動されます)\n\n復元しますか？", danger=True):
            return
        C.run_task(self, "復元しています", lambda p, l, c: bk.restore_backup(s, rec, p),
                   lambda pre: (self.refresh(), C.info(self, "復元しました。" + (f"\n復元前の状態: {pre.path}" if pre else ""))))

    def undo_last(self):
        s = self.ctx.current()
        if not s:
            return
        recs = [r for r in bk.list_backups(s.id) if r.kind in ("auto-change", "pre-restore") and Path(r.path).exists()]
        if not recs:
            C.info(self, "戻せる自動バックアップがありません。")
            return
        r = recs[0]
        C.info(self, f"直前の自動バックアップ: {r.note}\n({time.strftime('%Y-%m-%d %H:%M', time.localtime(r.created_at))})")
        self.restore(r)

    def delete(self):
        rec = self._selected()
        if rec and C.confirm(self, f"このバックアップを完全に削除します。元に戻せません。\n{rec.path}\n\n削除しますか？", danger=True):
            bk.delete_backup(rec)
            self.refresh()

    def prune(self):
        s = self.ctx.current()
        if not s:
            return
        n = self.keep.value()
        cands = [r for r in bk.list_backups(s.id) if r.kind in ("auto", "auto-change")][n:]
        if not cands:
            C.info(self, "整理するバックアップはありません。")
            return
        if C.confirm(self, f"古い自動バックアップ {len(cands)} 個を完全に削除します (手動バックアップは削除しません)。\n元に戻せません。よろしいですか？", danger=True):
            removed = bk.prune(s.id, n)
            self.refresh()
            C.info(self, f"{removed} 個削除しました。")

    def open_dir(self):
        s = self.ctx.current()
        if s:
            d = bk.backup_root() / s.id
            d.mkdir(parents=True, exist_ok=True)
            open_path(d)
