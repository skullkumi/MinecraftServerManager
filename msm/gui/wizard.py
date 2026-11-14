"""初回起動ウィザード (かんたんセットアップ).

Java確認 → 遊び方の選択 → サーバー作成 (またはMODパック取り込み) → EULA → 公開方法 → 参加方法の案内
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QLabel, QLineEdit,
                               QRadioButton, QVBoxLayout, QWizard, QWizardPage)

from msm.core.db import get_db
from msm.core.paths import safe_name
from msm.gui import actions
from msm.gui import common as C
from msm.java import manager as jm
from msm.publish.tunnels import METHOD_INFO
from msm.server import instance, providers
from msm.server.presets import pc_info, safe_max_memory

P_WELCOME, P_JAVA, P_PURPOSE, P_DETAIL, P_PUBLISH, P_DONE = range(6)


class SetupWizard(QWizard):
    def __init__(self, window, ctx):
        super().__init__(window)
        self.window_ = window
        self.ctx = ctx
        self.setWindowTitle("かんたんセットアップ")
        self.setMinimumSize(760, 560)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setButtonText(QWizard.NextButton, "次へ >")
        self.setButtonText(QWizard.BackButton, "< 戻る")
        self.setButtonText(QWizard.FinishButton, "作成する")
        self.setButtonText(QWizard.CancelButton, "あとで")
        self.setPage(P_WELCOME, self._welcome())
        self.setPage(P_JAVA, self._java())
        self.setPage(P_PURPOSE, self._purpose())
        self.setPage(P_DETAIL, DetailPage(self))
        self.setPage(P_PUBLISH, self._publish())
        self.finished.connect(lambda *_: get_db().set_setting("wizard_done", True))

    # ---- ページ ----
    def _welcome(self):
        p = QWizardPage()
        p.setTitle("ようこそ")
        v = QVBoxLayout(p)
        lab = QLabel("このセットアップでは、質問に答えるだけでMinecraftサーバーを作成し、友達と遊ぶ準備をします。\n\n"
                     "・必要なファイル (サーバー本体・Java) は公式の配布元から自動で取得します。\n"
                     "・CurseForgeやplayit.ggが無くても使えます。\n"
                     "・あとから設定を変えることもできます。\n\n"
                     "「あとで」を押すと、セットアップを閉じて通常の画面を使えます (ホームからいつでも開けます)。")
        lab.setWordWrap(True)
        v.addWidget(lab)
        return p

    def _java(self):
        p = QWizardPage()
        p.setTitle("Javaの確認")
        p.setSubTitle("Minecraftサーバーの実行にはJavaが必要です。")
        v = QVBoxLayout(p)
        self.java_label = QLabel("確認中...")
        self.java_label.setWordWrap(True)
        v.addWidget(self.java_label)
        v.addWidget(C.help_label("足りないJavaは、サーバー作成時に自動で導入できます (Eclipse Temurin・アプリ専用フォルダーに導入し、"
                                 "PCの設定は変更しません)。"))

        def init():
            js = [j for j in (jm.list_all() or jm.refresh()) if j.ok]
            self.java_label.setText("見つかったJava:\n" + ("\n".join(f"  ・{j.label}" for j in js) if js else "  なし") +
                                    "\n\nこのまま「次へ」を押してください。")

        p.initializePage = init
        return p

    def _purpose(self):
        p = QWizardPage()
        p.setTitle("どのように遊びますか？")
        v = QVBoxLayout(p)
        self.purpose = QButtonGroup(p)
        opts = [("vanilla_like", "MODなしで遊ぶ (おすすめ: 軽量なPaperサーバー)"),
                ("pack", "MODパック (CurseForge / Modrinth のファイル) を使う"),
                ("modded", "MODサーバーを自分で作る (Fabric / Forge / NeoForge)")]
        self.purpose_radios = {}
        for k, label in opts:
            r = QRadioButton(label)
            self.purpose_radios[k] = r
            self.purpose.addButton(r)
            v.addWidget(r)
        self.purpose_radios["vanilla_like"].setChecked(True)
        v.addWidget(C.help_label("MODパックは、CurseForgeのパックのページからダウンロードしたZIP、または Modrinth の .mrpack ファイルを使います。"))
        return p

    def purpose_key(self) -> str:
        return next(k for k, r in self.purpose_radios.items() if r.isChecked())

    def _publish(self):
        p = QWizardPage()
        p.setTitle("友達の招待方法")
        p.setSubTitle("あとで「友達を招待」画面から変更できます。")
        v = QVBoxLayout(p)
        self.pub = {}
        grp = QButtonGroup(p)
        for k in ("lan", "playit", "direct"):
            info = METHOD_INFO[k]
            r = QRadioButton(info["label"])
            grp.addButton(r)
            self.pub[k] = r
            v.addWidget(r)
            v.addWidget(C.help_label(f"　○ {info['merits']}\n　△ {info['demerits']}"))
        self.pub["lan"].setChecked(True)
        return p

    def publish_key(self) -> str:
        return next(k for k, r in self.pub.items() if r.isChecked())

    # ---- 完了 ----
    def accept(self) -> None:
        d: DetailPage = self.page(P_DETAIL)
        if not d.eula.isChecked():
            C.warn(self, "Minecraft EULAへの同意が必要です。「戻る」で詳細設定のページに戻ってチェックしてください。")
            return
        method = self.publish_key()
        purpose = self.purpose_key()
        super().accept()
        if purpose == "pack":
            page = self.window_.pages["import"]
            self.window_.go("import")
            page.path.setText(d.pack.text().strip())
            page.analyze()
            page.r_new.setChecked(True)
            page.eula.setChecked(True)
            if d.name.text().strip():
                page.name.setText(d.name.text().strip())
            get_db().set_setting("wizard_publish_method", method)
            page.run()
            self._apply_publish_to_new(method)
            return
        st = d.type.currentData()
        mc = d.mc.currentText()
        name = d.name.text().strip() or "マイサーバー"
        from msm.gui.pages.create import servers_root

        folder = str(servers_root() / safe_name(name))
        if not instance.folder_is_empty(folder):
            i = 2
            while not instance.folder_is_empty(f"{folder}-{i}"):
                i += 1
            folder = f"{folder}-{i}"
        total = pc_info()["total_mb"]
        mem = min(6144 if st in providers.MODDED_TYPES else 3072, safe_max_memory(total))
        req = instance.CreateRequest(name=name, directory=folder, mc_version=mc, server_type=st, max_mem_mb=mem,
                                     min_mem_mb=1024, max_players=10, port=instance.suggest_port(), eula=True)
        prov = providers.get_provider(st)
        dummy = instance.Server(id="new", name=name, directory=folder, mc_version=mc, server_type=st)

        def create(java_exe):
            def job(p, l, c):
                return instance.create_server(req, java_exe, p, l, c)

            def done(s):
                s.publish_method = method
                instance.save_server(s)
                self.ctx.notify_servers_changed()
                self.ctx.set_current(s.id)
                self.window_.go("dashboard")
                C.info(self.window_, f"サーバー「{s.name}」ができました。\n\n"
                                     "ホームの「サーバーを起動」を押して、状態が「稼働中」になったら\n"
                                     "Minecraftのマルチプレイで「localhost」に接続してください。\n\n"
                                     "友達を招待するときは「友達を招待」画面で接続アドレスを確認できます。"
                                     + ("\nMODサーバーの場合は「友達を招待」→「友達用パックを作成」で参加用のパックを作れます。"
                                        if st in providers.MODDED_TYPES else ""))

            C.run_task(self.window_, "サーバーを作成しています", job, done)

        if prov.needs_java_for_install:
            j, _r, _n = jm.resolve_for_server(dummy)
            if j is None:
                actions.install_java_for(self.window_, dummy, on_done=lambda: create(jm.resolve_for_server(dummy)[0].path
                                                                                    if jm.resolve_for_server(dummy)[0] else None))
                return
            create(j.path)
        else:
            create(None)

    def _apply_publish_to_new(self, method: str) -> None:
        s = self.ctx.current()
        if s and s.publish_method != method:
            s.publish_method = method
            instance.save_server(s)


class DetailPage(QWizardPage):
    def __init__(self, wiz: SetupWizard):
        super().__init__()
        self.wiz = wiz
        self.setTitle("サーバーの設定")
        f = QFormLayout(self)
        self.name = QLineEdit("マイサーバー")
        f.addRow("サーバー名", self.name)
        self.type = QComboBox()
        self.type_label = QLabel("サーバーの種類")
        f.addRow(self.type_label, self.type)
        self.mc = QComboBox()
        self.mc_label = QLabel("Minecraftバージョン")
        f.addRow(self.mc_label, self.mc)
        self.mc_help = C.help_label("遊ぶMinecraftと同じバージョンを選んでください。")
        f.addRow("", self.mc_help)
        self.pack = QLineEdit()
        self.pack_btn = C.button("参照...", self._browse)
        self.pack_label = QLabel("MODパックのファイル")
        self.pack_row = C.hbox(self.pack, self.pack_btn, stretch_end=False)
        f.addRow(self.pack_label, self.pack_row)
        self.eula = QCheckBox("Minecraft EULA (https://aka.ms/MinecraftEULA) に同意します")
        f.addRow(self.eula)
        f.addRow(C.button("EULAを読む", lambda: C.open_url(self, "https://aka.ms/MinecraftEULA")))
        self.type.currentIndexChanged.connect(self._load_versions)

    def initializePage(self):
        purpose = self.wiz.purpose_key()
        pack = purpose == "pack"
        for w in (self.type, self.type_label, self.mc, self.mc_label, self.mc_help):
            w.setVisible(not pack)
        for w in (self.pack, self.pack_btn, self.pack_label):
            w.setVisible(pack)
        self.type.blockSignals(True)
        self.type.clear()
        keys = ["paper", "purpur", "vanilla"] if purpose == "vanilla_like" else ["fabric", "neoforge", "forge", "quilt"]
        for k in keys:
            self.type.addItem(providers.get_provider(k).label, k)
        self.type.blockSignals(False)
        if not pack:
            self._load_versions()

    def _load_versions(self, *_):
        key = self.type.currentData()
        if not key:
            return
        self.mc.clear()
        self.mc.addItem("読み込み中...")

        def done(res, err):
            self.mc.clear()
            if err or not res:
                self.mc.addItem("(取得できません。インターネット接続を確認してください)")
                return
            self.mc.addItems(res)

        C.bg().run(lambda: providers.get_provider(key).list_mc_versions(False), done)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "MODパックを選択", "", "MODパック (*.zip *.mrpack)")
        if p:
            self.pack.setText(p)
            self.name.setText(Path(p).stem)

    def validatePage(self) -> bool:
        if self.wiz.purpose_key() == "pack":
            if not self.pack.text().strip() or not Path(self.pack.text().strip()).exists():
                C.warn(self, "MODパックのファイルを選んでください。")
                return False
        else:
            mc = self.mc.currentText()
            if not mc or "(" in mc or "読み込み" in mc:
                C.warn(self, "Minecraftバージョンを選んでください。")
                return False
        if not self.eula.isChecked():
            C.warn(self, "サーバーを動かすにはMinecraft EULAへの同意が必要です。")
            return False
        return True
