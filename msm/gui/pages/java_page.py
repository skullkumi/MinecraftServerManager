"""Java管理."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QGroupBox, QHeaderView, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from msm.core.paths import managed_java_dir
from msm.core.util import open_path
from msm.gui import actions
from msm.gui import common as C
from msm.gui import privacy
from msm.java import adoptium, detect
from msm.java import manager as jm
from msm.java.requirements import choose_java, requirement_for


class JavaPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("Java", "PC内のJavaを検出し、サーバーごとに適切なJavaを使います。複数のJavaは共存でき、"
                                              "本アプリが環境変数 (JAVA_HOME / PATH) や他のJavaを変更することはありません。"))
        self.env = C.help_label("")
        lay.addWidget(self.env)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Java", "詳細バージョン", "配布元", "bit", "状態", "インストール先 / 検出元"])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(300)
        lay.addWidget(self.table)
        lay.addLayout(C.hbox(C.button("再検出", self.rescan, "primary"), C.button("java.exeを手動で追加", self.add_manual),
                             C.button("アプリ管理のJavaフォルダーを開く", lambda: open_path(managed_java_dir()))))

        g = QGroupBox("Javaを導入 (Eclipse Temurin)")
        gl = QVBoxLayout(g)
        gl.addWidget(C.help_label("Eclipse Adoptium (https://adoptium.net/) が配布する無償のJava (Temurin) を、"
                                  "アプリ専用フォルダーに導入します。ダウンロード後にSHA-256で改ざんがないか確認し、動作確認まで行います。"))
        self.major = QComboBox()
        for m, desc in ((25, "Java 25 (Minecraft 26.1以降)"), (21, "Java 21 (1.20.5～1.21.x)"),
                        (17, "Java 17 (1.18～1.20.4)"), (8, "Java 8 (1.16.5以前)")):
            self.major.addItem(desc, m)
        gl.addLayout(C.hbox(self.major, C.button("導入する...", self.install)))
        lay.addWidget(g)

        g2 = QGroupBox("サーバーごとのJava")
        g2l = QVBoxLayout(g2)
        self.srv_table = QTableWidget(0, 4)
        self.srv_table.setHorizontalHeaderLabels(["サーバー", "必要なJava", "使用するJava", "設定"])
        self.srv_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.srv_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.srv_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.srv_table.verticalHeader().setVisible(False)
        self.srv_table.setMinimumHeight(200)
        self.srv_table.setColumnWidth(0, 180)
        self.srv_table.setColumnWidth(1, 360)
        self.srv_table.setWordWrap(True)
        g2l.addWidget(self.srv_table)
        g2l.addLayout(C.hbox(C.button("選択したサーバーに足りないJavaを導入", self.install_for_selected),
                             C.button("サーバー設定でJavaを変更", lambda: ctx.navigate.emit("settings"))))
        lay.addWidget(g2)
        lay.addStretch(1)

    def refresh(self) -> None:
        self.env.setText(detect.java_home_status() + "\n" + detect.path_java_status())
        javas = jm.list_all()
        if not javas:
            javas = jm.refresh()
        self._fill(javas)

    def _fill(self, javas):
        self.table.setRowCount(len(javas))
        for r, j in enumerate(javas):
            vals = [f"Java {j.major}" if j.major else "?", j.version, j.vendor, j.arch,
                    "使用可能" if j.ok else f"使用不可 ({j.error})",
                    f"{privacy.path(j.path)}\n検出元: {', '.join(j.sources)}{' / アプリ管理' if j.managed else ''}"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if not j.ok:
                    it.setForeground(Qt.gray)
                self.table.setItem(r, c, it)
        self.table.resizeRowsToContents()
        servers = self.ctx.servers()
        self.srv_table.setRowCount(len(servers))
        for r, s in enumerate(servers):
            try:
                req = requirement_for(s.mc_version, s.server_type)
                j, _req, note = jm.resolve_for_server(s)
                vals = [s.name, req.describe(), f"{j.label}  {privacy.path(j.path)}" if j else "⚠ 見つかりません",
                        "自動" if s.java_mode == "auto" else "手動"]
            except Exception as e:  # noqa: BLE001
                vals = [s.name, f"確認できません: {e}", "", s.java_mode]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, s.id)
                self.srv_table.setItem(r, c, it)
        self.srv_table.resizeRowsToContents()

    def rescan(self):
        C.run_task(self, "Javaを検出しています", lambda p, l, c: jm.refresh(), lambda r: self.refresh(), cancellable=False)

    def add_manual(self):
        path, _ = QFileDialog.getOpenFileName(self, "java.exe を選択", "", "java.exe (java.exe)")
        if not path:
            return
        info = jm.add_manual(path)
        if info.ok:
            C.info(self, f"追加しました: {info.label}")
        else:
            C.warn(self, f"このJavaは使用できません: {info.error}")
        self.refresh()

    def install(self):
        major = self.major.currentData()

        def find(p, l, c):
            return adoptium.find_package(major, "jre")

        def got(pkg):
            if not C.confirm(self, f"次のJavaをダウンロードして導入しますか？\n\n{pkg.describe()}\n\n"
                                   f"導入先: {managed_java_dir()}\n環境変数や他のJavaは変更しません。"):
                return

            def inst(p, l, c):
                info = adoptium.install(pkg, p, c)
                jm.save(info)
                return jm.refresh()

            C.run_task(self, f"Java {major} を導入しています", inst, lambda r: (self.refresh(), C.info(self, "導入と動作確認が完了しました。")))

        C.run_task(self, "配布情報を確認しています", find, got)

    def install_for_selected(self):
        rows = self.srv_table.selectionModel().selectedRows()
        if not rows:
            C.info(self, "サーバーを選択してください。")
            return
        from msm.server import instance

        s = instance.get_server(self.srv_table.item(rows[0].row(), 0).data(Qt.UserRole))
        if not s:
            return
        if choose_java(jm.list_all(), requirement_for(s.mc_version, s.server_type)):
            C.info(self, "このサーバーに合うJavaは既に導入されています。")
            return
        actions.install_java_for(self, s, on_done=self.refresh)
