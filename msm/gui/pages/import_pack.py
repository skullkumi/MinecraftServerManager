"""MODパックインポート (CurseForge ZIP / .mrpack / サーバーパック / MOD ZIP / CurseForgeプロファイル)."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QLabel,
                               QLineEdit, QPlainTextEdit, QRadioButton, QSpinBox, QVBoxLayout)

from msm.backup import backup as bk
from msm.core.paths import cache_dir, safe_name
from msm.gui import common as C
from msm.gui import privacy
from msm.gui.pages.create import servers_root
from msm.gui.pages.mods import PlanDialog
from msm.java import manager as jm
from msm.mods import curseforge_api as cf
from msm.mods import importer
from msm.server import instance, providers
from msm.server.presets import pc_info, safe_max_memory
from msm.server.process import registry


class ImportPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("MODパックを取り込む", "CurseForgeアプリが無くても使えます。元のZIPは読み取るだけで変更しません。"))
        g = QGroupBox("1. 取り込むファイル")
        gl = QVBoxLayout(g)
        self.path = QLineEdit()
        self.path.setPlaceholderText("CurseForgeのZIP / Modrinthの.mrpack / サーバーパックZIP / MODをまとめたZIP")
        gl.addLayout(C.hbox(self.path, C.button("参照...", self.browse), C.button("解析", self.analyze, "primary"), stretch_end=False))
        gl.addWidget(C.help_label("CurseForgeプロファイル (インストール済みのMODパック) から取り込む場合は「MOD → ＋MODを追加 → CurseForgeのプロファイルから追加」を使ってください。"))
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(170)
        gl.addWidget(self.summary)
        lay.addWidget(g)

        g2 = QGroupBox("2. 取り込み先")
        f = QFormLayout(g2)
        self.r_new = QRadioButton("新しいサーバーを作成する (おすすめ)")
        self.r_exist = QRadioButton("選択中のサーバーへ追加する")
        self.r_new.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.r_new)
        grp.addButton(self.r_exist)
        f.addRow(self.r_new)
        self.name = QLineEdit()
        f.addRow("サーバー名", self.name)
        self.folder = QLineEdit()
        f.addRow("保存先", self.folder)
        self.mem = QSpinBox()
        tot = pc_info()["total_mb"]
        self.mem.setRange(1024, max(2048, tot))
        self.mem.setSingleStep(512)
        self.mem.setSuffix(" MB")
        self.mem.setValue(min(6144, safe_max_memory(tot)))
        f.addRow("最大メモリ", self.mem)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        f.addRow("ポート", self.port)
        self.eula = QCheckBox("Minecraft EULA (https://aka.ms/MinecraftEULA) に同意します")
        f.addRow(self.eula)
        f.addRow(self.r_exist)
        self.overwrite = QCheckBox("既存の設定ファイル (config等) をパックの内容で上書きする (変更前にバックアップします)")
        f.addRow(self.overwrite)
        lay.addWidget(g2)

        g3 = QGroupBox("3. 実行")
        g3l = QVBoxLayout(g3)
        self.key_label = QLabel()
        g3l.addWidget(self.key_label)
        g3l.addLayout(C.hbox(C.button("インポートを開始", self.run, "primary")))
        g3l.addWidget(C.help_label(
            "処理の流れ: 解析 → 作業フォルダーへ展開・MOD取得 → MODのサーバー対応判定 → 確認画面 → サーバーへ配置\n"
            "・確実にクライアント専用と判定できたMODはサーバーに入れず、サーバー内の .msm/excluded_client_mods に保管します。\n"
            "・自動取得できなかったMODは IMPORT_FAILED.txt に記録し、「MOD > 手動ダウンロード補助」タブで取り込めます。"))
        lay.addWidget(g3)
        lay.addStretch(1)
        self.info: importer.ArchiveInfo | None = None

    def refresh(self) -> None:
        self.port.setValue(instance.suggest_port())
        self.key_label.setText("CurseForge APIキー: " + ("設定済み (MODを自動取得します)" if cf.has_api_key() else
                                                      "未設定 (CurseForge形式のMODは自動取得できません。アプリ設定で入力できます。"
                                                      "Modrinth形式・サーバーパックはキー不要です)"))
        self.r_exist.setEnabled(self.ctx.current() is not None)
        cur = self.ctx.current()
        self.r_exist.setText(f"選択中のサーバーへ追加する ({cur.name})" if cur else "選択中のサーバーへ追加する (サーバーがありません)")

    def browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "MODパックを選択", "", "MODパック (*.zip *.mrpack);;すべて (*.*)")
        if p:
            self.path.setText(p)
            self.analyze()

    def analyze(self):
        p = self.path.text().strip()
        if not p or not Path(p).exists():
            C.warn(self, "ファイルを選択してください。")
            return
        try:
            self.info = importer.analyze_archive(p)
        except Exception as e:  # noqa: BLE001
            C.error(self, f"ZIPを読み込めませんでした:\n{e}")
            return
        text = self.info.summary()
        if self.info.kind == "unknown":
            text += "\n\n--- 中身 (先頭100件) ---\n" + "\n".join(self.info.entries[:100])
        self.summary.setPlainText(text)
        nm = self.info.name or Path(p).stem
        self.name.setText(nm)
        self.folder.setText(privacy.path(servers_root() / safe_name(nm)))

    def run(self):
        if not self.info:
            self.analyze()
            if not self.info:
                return
        info = self.info
        unknown_mode = None
        if info.kind == "unknown":
            ch = C.ask_choice(self, "形式を自動判定できませんでした。どのように取り込みますか？\n(中身は画面の一覧で確認できます)",
                              ["JARファイルだけをMODとして取り込む", "すべてをサーバーフォルダーに展開する"])
            if ch < 0:
                return
            unknown_mode = "jars" if ch == 0 else "all"
        new = self.r_new.isChecked()
        if new:
            if info.kind in ("serverpack", "jars", "unknown") and not info.mc_version:
                C.info(self, "この形式にはMinecraftバージョン・ローダーの情報がありません。\n"
                             "先に「サーバー一覧 → 新しく作る」で同じバージョン・ローダーのサーバーを作成し、\n"
                             "「選択中のサーバーへ追加する」で取り込んでください。")
                return
            if not instance.folder_is_empty(privacy.to_real_path(self.folder.text())):
                C.warn(self, "保存先フォルダーが空ではありません。別のフォルダーを指定してください。")
                return
            server = None
        else:
            server = self.ctx.current()
            if not server:
                return
            sp = registry.find(server.id)
            if sp and sp.running:
                C.warn(self, "サーバーの稼働中は取り込めません。先に停止してください。")
                return
            if info.mc_version and (info.mc_version != server.mc_version or (info.loader and info.loader != server.server_type)):
                if not C.confirm(self, f"パック: Minecraft {info.mc_version} / {info.loader}\nサーバー: Minecraft {server.mc_version} / {server.server_type}\n\n"
                                       "バージョンまたはローダーが異なります。正常に動かない可能性が高いですが続けますか？", danger=True):
                    return
        staging = cache_dir() / "import" / time.strftime("%Y%m%d-%H%M%S")
        name = self.name.text().strip() or info.name or "imported"
        folder = privacy.to_real_path(self.folder.text())
        mem, port, eula, overwrite = self.mem.value(), self.port.value(), self.eula.isChecked(), self.overwrite.isChecked()
        target_type = server.server_type if server else (info.loader or "vanilla")

        def job(p, l, c):
            p("パックを展開・MODを取得しています...", None)
            if unknown_mode:
                res = importer.stage_unknown(info.path, staging, unknown_mode)
            else:
                res = importer.stage_archive(info, staging, target_type, p, c, l)
            c.check()
            p("MODのサーバー対応を判定しています...", None)
            mods = sorted((staging / "mods").glob("*.jar"))
            plan = importer.plan_for_files(mods, (server.mods_dir if server else staging / "_none"), target_type, True)
            return res, plan

        def staged(result):
            res, plan = result
            dlg = PlanDialog(self, plan)
            if dlg.exec() != QDialog.Accepted:
                shutil.rmtree(staging, ignore_errors=True)
                return
            include = dlg.included_review()
            self._finish(info, res, plan, include, server, staging, name, folder, mem, port, eula, overwrite)

        C.run_task(self, "MODパックを取り込んでいます", job, staged)

    def _finish(self, info, res, plan, include, server, staging, name, folder, mem, port, eula, overwrite):
        def job(p, l, c):
            srv = server
            if srv is None:
                req = instance.CreateRequest(name=name, directory=folder, mc_version=info.mc_version,
                                             server_type=info.loader or "vanilla", loader_version=info.loader_version,
                                             max_mem_mb=mem, min_mem_mb=min(2048, mem), port=port, eula=eula)
                prov = providers.get_provider(req.server_type)
                java_exe = None
                if prov.needs_java_for_install:
                    dummy = instance.Server(id="n", name=name, directory=folder, mc_version=req.mc_version, server_type=req.server_type)
                    j, _r, note = jm.resolve_for_server(dummy)
                    if not j:
                        raise RuntimeError(f"ローダーのインストールにJavaが必要です: {note}\nアプリ設定の「Java」タブで導入してから再度実行してください。")
                    java_exe = j.path
                p("サーバー本体・ローダーを準備しています...", None)
                srv = instance.create_server(req, java_exe, p, l, c)
            else:
                p("取り込み前のバックアップを作成しています...", None)
                try:
                    bk.create_backup(srv, ["mods", "config"], "auto-change", f"MODパック取り込み前 ({info.name or Path(info.path).name})")
                except bk.BackupError as e:
                    l(f"バックアップ: {e}")
            p("ファイルを配置しています...", None)
            copied, kept = importer.copy_files_to_server(staging / "files", srv.path, overwrite or server is None)
            done = importer.apply_plan(srv, plan, include, f"パック取り込み: {info.name or Path(info.path).name}")
            failed_extra = [f"{p_.file_name}: {p_.note}" for p_ in plan if p_.action in ("invalid", "wrong_loader", "conflict")]
            report = importer.write_failed_report(srv.path, res.manual, failed_extra)
            if res.manual:
                importer.save_pending_manual(srv.id, res.manual)
            lines = [f"MODパック取り込み結果 ({time.strftime('%Y-%m-%d %H:%M:%S')})", f"元ファイル: {info.path} (変更していません)",
                     info.summary(), "", f"自動取得: {res.downloaded}", f"サーバーに追加したMOD: {sum(1 for d in done if d.startswith('追加'))}",
                     f"サーバー対象外として保管: {sum(1 for d in done if d.startswith('サーバー対象外'))}",
                     f"手動ダウンロードが必要: {len(res.manual)}", f"配置した設定ファイル等: {len(copied)}", f"既存のため残したファイル: {len(kept)}",
                     "", "取り込まなかったファイル:"] + [f"  {x}" for x in res.skipped_files[:200]] + [""] + res.messages
            (srv.path / "IMPORT_RESULT.txt").write_text("\n".join(lines), "utf-8")
            shutil.rmtree(staging, ignore_errors=True)
            return srv, res, report

        def fin(r):
            srv, res, report = r
            self.ctx.notify_servers_changed()
            self.ctx.set_current(srv.id)
            msg = f"取り込みが完了しました。\nサーバー: {srv.name}\n自動取得: {res.downloaded}\n手動ダウンロードが必要: {len(res.manual)}"
            if res.messages:
                msg += "\n\n" + "\n".join(res.messages)
            if res.manual:
                msg += "\n\n「MOD > 手動ダウンロード補助」タブから残りのMODを取り込めます。"
            C.info(self, msg)
            self.ctx.navigate.emit("mods" if res.manual else "dashboard")

        C.run_task(self, "サーバーへ配置しています", job, fin)
