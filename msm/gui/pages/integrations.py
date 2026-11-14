"""外部連携 (CurseForge / Minecraftランチャー)。どちらも任意."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QGroupBox, QHeaderView, QLabel, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from msm.core.util import open_path
from msm.gui import common as C
from msm.gui import privacy
from msm.gui.pages.mods import PlanDialog
from msm.integrations import external
from msm.mods import importer
from msm.server.process import registry


class IntegrationsPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("外部連携", "CurseForge・Minecraftランチャーは任意です。無くてもサーバーの構築・管理・MODパックの取り込みはすべて行えます。"))
        g = QGroupBox("CurseForge")
        gl = QVBoxLayout(g)
        self.cf_status = QLabel()
        self.cf_status.setWordWrap(True)
        gl.addWidget(self.cf_status)
        self.b_cf = C.button("CurseForgeを起動", self.launch_cf)
        gl.addLayout(C.hbox(self.b_cf, C.button("プロファイルの場所を開く", self.open_instances)))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["プロファイル", "Minecraft", "ローダー", "MOD数", "場所"])
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(200)
        gl.addWidget(self.table)
        gl.addLayout(C.hbox(C.button("選択したプロファイルのMODを選択中のサーバーへ取り込む...", self.import_profile),
                            C.button("このプロファイルのフォルダーを開く", self.open_profile)))
        gl.addWidget(C.help_label(
            "できること: インストール確認・アプリ起動・プロファイル (インスタンス) の場所を開く・プロファイル情報の読み取り・MODの取り込み。\n"
            "できないこと: CurseForgeには外部から特定のプロファイルを直接起動する公開された方法が無いため、"
            "プロファイルの起動はCurseForgeアプリ上で「Play」を押してください。"))
        lay.addWidget(g)

        g2 = QGroupBox("Minecraftランチャー")
        g2l = QVBoxLayout(g2)
        self.mc_status = QLabel()
        g2l.addWidget(self.mc_status)
        g2l.addLayout(C.hbox(C.button("Minecraftを起動", self.launch_mc), C.button("参加方法を表示", self.guide)))
        lay.addWidget(g2)
        lay.addStretch(1)

    def refresh(self) -> None:
        exe = external.find_curseforge()
        inst = external.curseforge_instances_dir()
        self.cf_status.setText(f"CurseForge: {'インストール済み (' + exe + ')' if exe else '見つかりません (使わなくても問題ありません)'}\n"
                               f"プロファイルの場所: {inst or '見つかりません'}")
        self.b_cf.setEnabled(bool(exe))
        self.profiles = external.curseforge_profiles()
        self.table.setRowCount(len(self.profiles))
        for r, p in enumerate(self.profiles):
            for c, v in enumerate([p.name, p.mc_version, f"{p.loader} {p.loader_version}", str(p.mod_count), privacy.path(p.path)]):
                self.table.setItem(r, c, QTableWidgetItem(v))
        ml = external.find_minecraft_launcher()
        self.mc_status.setText("Minecraftランチャー: " + (f"見つかりました ({'Microsoft Store版' if ml[0] == 'store' else ml[1]})" if ml else "見つかりません"))

    def _sel(self):
        rows = self.table.selectionModel().selectedRows()
        return self.profiles[rows[0].row()] if rows else None

    def launch_cf(self):
        if not external.launch_curseforge():
            C.warn(self, "CurseForgeが見つかりません。")

    def open_instances(self):
        d = external.curseforge_instances_dir()
        if d:
            open_path(d)
        else:
            C.info(self, "プロファイルの場所が見つかりません。アプリ設定で指定できます。")

    def open_profile(self):
        p = self._sel()
        if p:
            open_path(Path(p.path))

    def import_profile(self):
        p = self._sel()
        s = self.ctx.current()
        if not p or not s:
            C.info(self, "プロファイルとサーバーを選択してください。")
            return
        sp = registry.find(s.id)
        if sp and sp.running:
            C.warn(self, "サーバーを停止してから取り込んでください。")
            return
        if (p.mc_version and p.mc_version != s.mc_version) or (p.loader and p.loader != s.server_type):
            if not C.confirm(self, f"プロファイル: {p.mc_version} / {p.loader}\nサーバー: {s.mc_version} / {s.server_type}\n"
                                   "バージョンまたはローダーが違います。続けますか？", danger=True):
                return
        files = sorted((Path(p.path) / "mods").glob("*.jar"))
        if not files:
            C.info(self, "プロファイルにMODがありません。")
            return

        def job(pr, l, c):
            return importer.plan_for_files(files, s.mods_dir, s.server_type, True)

        def done(plan):
            dlg = PlanDialog(self, plan)
            if dlg.exec() != QDialog.Accepted:
                return
            res = importer.apply_plan(s, plan, dlg.included_review(), f"CurseForgeプロファイル「{p.name}」から取り込み")
            cfg = Path(p.path) / "config"
            msg = f"{sum(1 for x in res if x.startswith('追加'))}個のMODを追加しました。(プロファイル側のファイルは変更していません)"
            if cfg.exists() and C.confirm(self, msg + "\n\nプロファイルのconfigフォルダーもコピーしますか？\n(既存のファイルは上書きしません)"):
                copied, kept = importer.copy_files_to_server(cfg, s.path / "config", False)
                msg += f"\nconfig: {len(copied)}個コピー / 既存のため{len(kept)}個はそのまま"
            C.info(self, msg)

        C.run_task(self, "プロファイルのMODを確認しています", job, done)

    def launch_mc(self):
        if not external.launch_minecraft():
            C.warn(self, "Minecraftランチャーが見つかりません。アプリ設定で場所を指定できます。")

    def guide(self):
        s = self.ctx.current()
        if not s:
            return
        from msm.publish.tunnels import join_addresses

        a = join_addresses(s, self.ctx.public_address(s))
        C.info(self, external.JOIN_GUIDE.format(mc=s.mc_version, loader=s.type_label, local=a["localhost"],
                                                public=a.get("public") or "(「友達を招待」で確認)"), "参加方法")
