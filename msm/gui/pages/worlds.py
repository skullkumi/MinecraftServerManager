"""ワールド管理."""
from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path

from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHeaderView, QInputDialog, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from msm.backup import backup as bk
from msm.core.paths import safe_name
from msm.core.util import dir_size, human_size, open_path
from msm.gui import common as C
from msm.server import ops
from msm.server.process import registry
from msm.server.properties import write_properties


def find_worlds(server_dir: Path) -> list[Path]:
    out = []
    for d in sorted(server_dir.iterdir()) if server_dir.exists() else []:
        if d.is_dir() and (d / "level.dat").exists() and not d.name.startswith("."):
            out.append(d)
    return out


class WorldsPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("ワールド", "ワールドは削除せず、退避フォルダーへ移動して管理します。"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ワールド", "使用中", "サイズ", "最終更新"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(260)
        lay.addWidget(self.table)
        lay.addLayout(C.hbox(C.button("このワールドを使う", self.use, "primary"), C.button("新しいワールドで始める...", self.new_world),
                             C.button("ワールドをバックアップ", self.backup), C.button("フォルダーを開く", self.open)))
        lay.addLayout(C.hbox(C.button("ワールドを取り込む (フォルダー)...", self.import_folder),
                             C.button("ワールドを取り込む (ZIP)...", self.import_zip),
                             C.button("退避する (削除しない)...", self.archive, "danger")))
        lay.addWidget(C.help_label("※ ワールドの切り替え・退避・取り込みはサーバー停止中のみ行えます。\n"
                                   "※ 取り込んだワールドは元のフォルダー/ZIPからコピーするため、元データは変更されません。"))
        lay.addStretch(1)

    def refresh(self) -> None:
        s = self.ctx.current()
        self.setEnabled(s is not None)
        if not s:
            return
        level = s.level_name()
        ws = find_worlds(s.path)
        self._worlds = ws
        self.table.setRowCount(len(ws))
        for r, w in enumerate(ws):
            used = w.name in (level, f"{level}_nether", f"{level}_the_end")
            vals = [w.name, "● 使用中" if w.name == level else ("(関連)" if used else ""), "計算中...",
                    time.strftime("%Y-%m-%d %H:%M", time.localtime((w / "level.dat").stat().st_mtime))]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))

        def sizes():
            return [dir_size(w) for w in ws]

        def done(res, err):
            if res and len(res) == self.table.rowCount():
                for r, n in enumerate(res):
                    self.table.setItem(r, 2, QTableWidgetItem(human_size(n)))

        C.bg().run(sizes, done)

    def _sel(self) -> Path | None:
        rows = self.table.selectionModel().selectedRows()
        return self._worlds[rows[0].row()] if rows else None

    def _stopped(self) -> bool:
        s = self.ctx.current()
        sp = registry.find(s.id) if s else None
        if sp and sp.running:
            C.warn(self, "サーバーを停止してから操作してください。")
            return False
        return True

    def use(self):
        s = self.ctx.current()
        w = self._sel()
        if not s or not w or not self._stopped():
            return
        if C.confirm(self, f"使用するワールドを「{w.name}」に切り替えますか？\n(server.properties の level-name を変更します。変更前にバックアップします)"):
            bk.backup_file(s, ["server.properties"], "ワールド切り替え前")
            write_properties(s.path / "server.properties", {"level-name": w.name})
            self.refresh()

    def new_world(self):
        s = self.ctx.current()
        if not s or not self._stopped():
            return
        name, ok = QInputDialog.getText(self, "新しいワールド", "新しいワールドのフォルダー名:", text=f"world_{time.strftime('%Y%m%d')}")
        if not ok or not name.strip():
            return
        name = safe_name(name.strip())
        if (s.path / name).exists():
            C.warn(self, "同じ名前のフォルダーが既にあります。")
            return
        seed, ok = QInputDialog.getText(self, "シード", "シード値 (空欄でランダム):")
        if not ok:
            return
        bk.backup_file(s, ["server.properties"], "新ワールド作成前")
        write_properties(s.path / "server.properties", {"level-name": name, "level-seed": seed.strip()})
        C.info(self, f"次回の起動で新しいワールド「{name}」が作成されます。\n今までのワールドはそのまま残っています。")
        self.refresh()

    def backup(self):
        s = self.ctx.current()
        if not s:
            return
        C.run_task(self, "ワールドをバックアップしています",
                   lambda p, l, c: ops.backup_with_server_control(s, ["world", "config"], "manual", "ワールド管理から", p, c),
                   lambda r: C.info(self, f"バックアップを作成しました:\n{r.path}"))

    def open(self):
        w = self._sel()
        s = self.ctx.current()
        open_path(w if w else s.path) if s else None

    def import_folder(self):
        s = self.ctx.current()
        if not s or not self._stopped():
            return
        d = QFileDialog.getExistingDirectory(self, "取り込むワールドフォルダー (level.datがあるフォルダー) を選択")
        if not d:
            return
        src = Path(d)
        if not (src / "level.dat").exists():
            C.warn(self, "level.dat が見つかりません。ワールドのフォルダーを選択してください。")
            return
        self._copy_in(s, src.name, lambda dest: shutil.copytree(src, dest))

    def import_zip(self):
        s = self.ctx.current()
        if not s or not self._stopped():
            return
        f, _ = QFileDialog.getOpenFileName(self, "ワールドのZIPを選択", "", "ZIP (*.zip)")
        if not f:
            return
        with zipfile.ZipFile(f) as zf:
            lv = [n for n in zf.namelist() if n.endswith("level.dat") and n.count("/") <= 1]
        if not lv:
            C.warn(self, "ZIP内にlevel.datが見つかりません。")
            return
        prefix = lv[0][: -len("level.dat")]

        def extract(dest: Path):
            with zipfile.ZipFile(f) as zf:
                root = dest.resolve()
                for n in zf.namelist():
                    if not n.startswith(prefix) or n.endswith("/"):
                        continue
                    out = (dest / n[len(prefix):]).resolve()
                    if not str(out).startswith(str(root)):
                        raise RuntimeError("ZIPに不正なパスが含まれています")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(n) as a, open(out, "wb") as b:
                        shutil.copyfileobj(a, b)

        self._copy_in(s, prefix.strip("/") or Path(f).stem, extract)

    def _copy_in(self, s, default_name: str, copier):
        name, ok = QInputDialog.getText(self, "ワールド名", "サーバー内でのフォルダー名:", text=safe_name(default_name))
        if not ok or not name.strip():
            return
        dest = s.path / safe_name(name.strip())
        if dest.exists():
            C.warn(self, "同じ名前のフォルダーが既にあります。別の名前にしてください。")
            return

        def job(p, l, c):
            p("ワールドをコピーしています...", None)
            copier(dest)
            return dest

        def done(d):
            if C.confirm(self, f"取り込みました: {d.name}\nこのワールドを使用するワールドに設定しますか？"):
                bk.backup_file(s, ["server.properties"], "ワールド取り込み時")
                write_properties(s.path / "server.properties", {"level-name": d.name})
            self.refresh()

        C.run_task(self, "ワールドを取り込んでいます", job, done)

    def archive(self):
        s = self.ctx.current()
        w = self._sel()
        if not s or not w or not self._stopped():
            return
        if w.name == s.level_name():
            C.warn(self, "使用中のワールドは退避できません。先に別のワールドに切り替えてください。")
            return
        dest = s.path / ".msm" / "old_worlds" / f"{w.name}-{time.strftime('%Y%m%d-%H%M%S')}"
        if C.confirm(self, f"ワールド「{w.name}」を退避フォルダーへ移動します (削除はしません)。\n移動先: {dest}"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(w), str(dest))
            self.refresh()
