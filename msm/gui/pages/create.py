"""新規サーバー作成."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QLineEdit, QSpinBox,
                               QVBoxLayout)

from msm.core.db import get_db
from msm.core.paths import default_servers_root, safe_name
from msm.gui import actions
from msm.gui import common as C
from msm.gui import privacy
from msm.java import manager as jm
from msm.java.requirements import choose_java, requirement_for
from msm.server import instance, ports, providers
from msm.server.presets import pc_info, safe_max_memory


def servers_root() -> Path:
    return Path(get_db().get_setting("servers_root") or str(default_servers_root()))


class CreatePage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("新しいサーバーを作る", "必要なファイルは公式配布元から自動で取得します。分からない項目はそのままで大丈夫です。"))
        g = QGroupBox("基本設定")
        f = QFormLayout(g)
        self.name = QLineEdit("マイサーバー")
        self.name.textChanged.connect(self._update_folder)
        f.addRow("サーバー名", self.name)
        self.type = QComboBox()
        for k, p in providers.PROVIDERS.items():
            self.type.addItem(p.label, k)
        self.type.currentIndexChanged.connect(self._type_changed)
        f.addRow("サーバーの種類", self.type)
        self.type_desc = C.help_label("")
        f.addRow("", self.type_desc)
        self.mc = QComboBox()
        self.mc.currentIndexChanged.connect(self._mc_changed)
        self.snap = QCheckBox("スナップショット/プレリリースも表示")
        self.snap.toggled.connect(self._type_changed)
        f.addRow("Minecraftバージョン", C.hbox(self.mc, self.snap))
        self.loader = QComboBox()
        f.addRow("ローダーバージョン", self.loader)
        self.folder = QLineEdit()
        f.addRow("保存先", C.hbox(self.folder, C.button("参照...", self._browse), stretch_end=False))
        lay.addWidget(g)

        g2 = QGroupBox("性能・接続")
        f2 = QFormLayout(g2)
        info = pc_info()
        self.total_mb = info["total_mb"]
        self.maxmem = QSpinBox()
        self.maxmem.setRange(1024, max(2048, self.total_mb))
        self.maxmem.setSingleStep(512)
        self.maxmem.setSuffix(" MB")
        self.maxmem.setValue(min(4096, safe_max_memory(self.total_mb)))
        self.minmem = QSpinBox()
        self.minmem.setRange(512, max(2048, self.total_mb))
        self.minmem.setSingleStep(512)
        self.minmem.setSuffix(" MB")
        self.minmem.setValue(1024)
        f2.addRow("最大メモリ", self.maxmem)
        f2.addRow("", C.help_label(f"このPCのメモリ: {self.total_mb // 1024}GB (目安: バニラ2～4GB / MOD入り6～10GB / "
                                   f"割り当て上限の目安 {safe_max_memory(self.total_mb) // 1024}GB)"))
        f2.addRow("最小メモリ", self.minmem)
        self.players = QSpinBox()
        self.players.setRange(1, 1000)
        self.players.setValue(10)
        f2.addRow("最大プレイヤー数", self.players)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.valueChanged.connect(self._check_port)
        f2.addRow("サーバーポート", self.port)
        self.port_note = C.help_label("")
        f2.addRow("", self.port_note)
        lay.addWidget(g2)

        g3 = QGroupBox("Java実行環境")
        f3 = QFormLayout(g3)
        self.java_mode = QComboBox()
        self.java_mode.addItem("自動選択 (おすすめ)", "auto")
        self.java_mode.addItem("手動で選ぶ", "manual")
        self.java_mode.currentIndexChanged.connect(self._java_changed)
        f3.addRow("Java", self.java_mode)
        self.java_combo = QComboBox()
        f3.addRow("使用するJava", self.java_combo)
        self.java_req = C.help_label("")
        f3.addRow("", self.java_req)
        self.java_btn = C.button("必要なJavaを導入...", self._install_java)
        f3.addRow("", self.java_btn)
        lay.addWidget(g3)

        self.eula = QCheckBox("Minecraft EULA (https://aka.ms/MinecraftEULA) に同意します")
        lay.addLayout(C.hbox(self.eula, C.button("EULAを読む", lambda: C.open_url(self, "https://aka.ms/MinecraftEULA"))))
        self.create_btn = C.button("サーバーを作成", self.create, "primary")
        lay.addLayout(C.hbox(self.create_btn))
        lay.addStretch(1)
        self._loaded = False

    def refresh(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._type_changed()
        self.port.setValue(instance.suggest_port())
        self._update_folder()
        self._refresh_java()

    def _update_folder(self):
        self.folder.setText(privacy.path(servers_root() / safe_name(self.name.text().strip() or "server")))

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "保存先フォルダー", str(servers_root()))
        if d:
            self.folder.setText(privacy.path(Path(d) / safe_name(self.name.text().strip() or "server")))

    def _type_changed(self, *_):
        key = self.type.currentData()
        p = providers.get_provider(key)
        self.type_desc.setText(p.description)
        self.mc.clear()
        self.mc.addItem("読み込み中...")
        self.mc.setEnabled(False)
        snap = self.snap.isChecked()

        def done(res, err):
            self.mc.clear()
            self.mc.setEnabled(True)
            if err:
                self.mc.addItem("(取得失敗)")
                C.warn(self, f"バージョン一覧を取得できませんでした。インターネット接続を確認してください。\n{err}")
                return
            self.mc.blockSignals(True)
            self.mc.addItems(res)
            self.mc.blockSignals(False)
            self._mc_changed()

        C.bg().run(lambda: p.list_mc_versions(snap), done)

    def _mc_changed(self, *_):
        key = self.type.currentData()
        mc = self.mc.currentText()
        p = providers.get_provider(key)
        self.loader.clear()
        if not p.needs_loader or not mc or "(" in mc or "読み込み" in mc:
            self.loader.addItem("(不要)", "")
            self.loader.setEnabled(False)
            self._refresh_java()
            return
        self.loader.setEnabled(False)
        self.loader.addItem("読み込み中...", "")

        def done(res, err):
            self.loader.clear()
            self.loader.setEnabled(True)
            if err or not res:
                self.loader.addItem("(取得失敗 - 最新を使用)", "")
                return
            rec = 0
            for i, lv in enumerate(res):
                self.loader.addItem(lv.label + (" ★おすすめ" if lv.recommended else ""), lv.version)
                if lv.recommended:
                    rec = i
            self.loader.setCurrentIndex(rec)

        C.bg().run(lambda: p.list_loader_versions(mc), done)
        self._refresh_java()

    def _check_port(self, *_):
        p = self.port.value()
        msgs = []
        conf = instance.port_conflicts(p)
        if conf:
            msgs.append("⚠ 他のサーバーと重複: " + ", ".join(s.name for s in conf))
        if not ports.is_port_free(p):
            msgs.append(f"⚠ ポート {p} は現在ほかのプログラムが使用中です " + ports.port_owner(p))
        self.port_note.setText(" / ".join(msgs) if msgs else "使用できます (通常は25565。複数サーバーは25566, 25567...)")

    def _java_changed(self, *_):
        self.java_combo.setEnabled(self.java_mode.currentData() == "manual")
        self._refresh_java()

    def _refresh_java(self):
        mc = self.mc.currentText()
        key = self.type.currentData()
        installs = jm.list_all() or jm.refresh()
        cur = self.java_combo.currentData()
        self.java_combo.clear()
        for j in installs:
            if j.ok:
                self.java_combo.addItem(j.label + "  " + privacy.path(j.path), j.path)
        if cur:
            self.java_combo.setCurrentIndex(max(0, self.java_combo.findData(cur)))
        self.java_combo.setEnabled(self.java_mode.currentData() == "manual")
        if not mc or "(" in mc or "読み込み" in mc:
            self.java_req.setText("")
            return
        try:
            req = requirement_for(mc, key)
        except Exception as e:  # noqa: BLE001
            self.java_req.setText(f"要件を確認できません: {e}")
            return
        chosen = choose_java(installs, req)
        txt = f"必要: {req.describe()}\n"
        if self.java_mode.currentData() == "auto":
            txt += f"→ 自動選択: {chosen.label} ({chosen.path})" if chosen else "→ 条件に合うJavaがありません。「必要なJavaを導入」を押してください。"
        self.java_req.setText(txt)
        self.java_btn.setVisible(chosen is None)

    def _install_java(self):
        mc = self.mc.currentText()
        dummy = instance.Server(id="new", name=self.name.text(), directory="", mc_version=mc,
                                server_type=self.type.currentData())
        actions.install_java_for(self, dummy, on_done=self._refresh_java)

    def create(self):
        name = self.name.text().strip()
        mc = self.mc.currentText()
        key = self.type.currentData()
        folder = privacy.to_real_path(self.folder.text())
        if not name:
            C.warn(self, "サーバー名を入力してください。")
            return
        if not mc or "(" in mc or "読み込み" in mc:
            C.warn(self, "Minecraftバージョンを選択してください。")
            return
        if not folder:
            C.warn(self, "保存先を入力してください。")
            return
        if self.minmem.value() > self.maxmem.value():
            C.warn(self, "最小メモリは最大メモリ以下にしてください。")
            return
        if self.maxmem.value() > safe_max_memory(self.total_mb) and not C.confirm(
                self, "最大メモリがPCのメモリに対して大きすぎます。PC全体が重くなる恐れがあります。続けますか？"):
            return
        if not instance.folder_is_empty(folder):
            C.warn(self, f"保存先フォルダーが空ではありません:\n{folder}\n\n既存のファイルを守るため、空のフォルダーか新しいフォルダーを指定してください。\n"
                         "既存のサーバーを使う場合は「サーバー一覧 → 既存のサーバーフォルダーを登録」を使ってください。")
            return
        if instance.port_conflicts(self.port.value()) and not C.confirm(
                self, "このポート番号は他のサーバーと重複しています。同時に起動できませんが、続けますか？"):
            return
        if not self.eula.isChecked():
            if not C.confirm(self, "EULAに同意していないため、作成後に起動するときに改めて確認します。\nこのまま作成しますか？"):
                return
        java_mode = self.java_mode.currentData()
        java_path = self.java_combo.currentData() if java_mode == "manual" else ""
        req = instance.CreateRequest(
            name=name, directory=folder, mc_version=mc, server_type=key, loader_version=self.loader.currentData() or "",
            min_mem_mb=self.minmem.value(), max_mem_mb=self.maxmem.value(), max_players=self.players.value(),
            port=self.port.value(), java_mode=java_mode, java_path=java_path or "", eula=self.eula.isChecked())
        p = providers.get_provider(key)
        java_exe = None
        if p.needs_java_for_install:
            dummy = instance.Server(id="new", name=name, directory=folder, mc_version=mc, server_type=key,
                                    java_mode=java_mode, java_path=java_path or "")
            j, _r, note = jm.resolve_for_server(dummy)
            if not j:
                C.warn(self, f"{p.label} のインストールにはJavaが必要です。\n{note}\n\n「必要なJavaを導入」を押してください。")
                return
            java_exe = j.path

        def job(prog, log_fn, cancel):
            return instance.create_server(req, java_exe, prog, log_fn, cancel)

        def done(s):
            self.ctx.notify_servers_changed()
            self.ctx.set_current(s.id)
            C.info(self, f"サーバー「{s.name}」を作成しました。\n\n保存先: {s.directory}\n\n"
                         "ホームの「サーバーを起動」で起動できます。")
            self.ctx.navigate.emit("dashboard")

        C.run_task(self, "サーバーを作成しています", job, done, keep_open=False)
