"""アプリ設定."""
from __future__ import annotations

import os

from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QLabel, QLineEdit, QVBoxLayout

from msm import APP_NAME, APP_VERSION
from msm.backup import backup as bk
from msm.core.db import get_db
from msm.core.paths import app_data_dir, default_servers_root, windows_downloads_dir
from msm.core.util import open_path
from msm.gui import common as C
from msm.gui import privacy
from msm.mods import curseforge_api as cf
from msm import update


class AppSettingsPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("アプリ設定"))
        g = QGroupBox("保存先")
        f = QFormLayout(g)
        self.servers_root = QLineEdit()
        f.addRow("新規サーバーの保存先", self._dir_row(self.servers_root))
        self.backup_dir = QLineEdit()
        f.addRow("バックアップの保存先", self._dir_row(self.backup_dir))
        self.watch_dir = QLineEdit()
        f.addRow("ダウンロード監視フォルダー", self._dir_row(self.watch_dir))
        f.addRow("", C.help_label(f"Windowsの既定のダウンロードフォルダー: {privacy.path(windows_downloads_dir())}\n"
                                  "ブラウザのダウンロード先を変えている場合は、そのフォルダーを指定してください。"))
        lay.addWidget(g)

        g2 = QGroupBox("外部ソフトの場所 (空欄なら自動検出)")
        f2 = QFormLayout(g2)
        self.playit = QLineEdit()
        f2.addRow("playit.exe", self._file_row(self.playit))
        self.cf_path = QLineEdit()
        f2.addRow("CurseForge.exe", self._file_row(self.cf_path))
        self.cf_inst = QLineEdit()
        f2.addRow("CurseForgeのInstancesフォルダー", self._dir_row(self.cf_inst))
        self.launcher = QLineEdit()
        f2.addRow("MinecraftLauncher.exe", self._file_row(self.launcher))
        lay.addWidget(g2)

        g3 = QGroupBox("CurseForge APIキー (任意)")
        f3 = QFormLayout(g3)
        self.key_state = QLabel()
        f3.addRow("状態", self.key_state)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("新しいキーを入力 (保存済みのキーは表示しません)")
        f3.addRow("APIキー", self.key)
        f3.addRow(C.hbox(C.button("キーを保存", self.save_key), C.button("キーを確認", self.test_key), C.button("キーを削除", self.delete_key)))
        f3.addRow(C.help_label("CurseForge形式のMODパックのMODを自動取得するために使います。キーはCurseForgeの開発者サイト (console.curseforge.com) で取得できます。\n"
                               "キーはWindowsの暗号化機能 (DPAPI) で暗号化して保存され、このPCの同じユーザーでのみ読み出せます。"))
        lay.addWidget(g3)

        g4 = QGroupBox("アプリの更新")
        f4 = QFormLayout(g4)
        self.update_github = QLineEdit()
        self.update_github.setPlaceholderText("例: yourname/MinecraftServerManager (GitHubで配布している場合)")
        f4.addRow("GitHubリポジトリ", self.update_github)
        self.update_url = QLineEdit()
        self.update_url.setPlaceholderText("https://.../latest.json (GitHub以外で配布する場合の更新情報URL)")
        f4.addRow("更新情報URL", self.update_url)
        self.update_auto = QCheckBox("起動時に更新を確認する")
        f4.addRow(self.update_auto)
        f4.addRow(C.hbox(C.button("更新を確認", self.check_update)))
        f4.addRow(C.help_label("新しいバージョンがあれば、確認のうえダウンロード・更新します (SHA-256でファイルを検証)。"
                               "古いファイルは .old として残します。検証情報が無い場合は配布ページを開くだけにします。"))
        lay.addWidget(g4)

        g5 = QGroupBox("動作")
        f5 = QFormLayout(g5)
        self.theme = QComboBox()
        self.theme.addItem("Windowsの設定に合わせる", "system")
        self.theme.addItem("ライト", "light")
        self.theme.addItem("ダーク", "dark")
        self.theme.currentIndexChanged.connect(self._theme_changed)
        f5.addRow("見た目", self.theme)
        self.lang = QComboBox()
        self.lang.addItem("日本語", "ja")
        self.lang.addItem("English", "en")
        self.lang.addItem("Windowsの言語に合わせる / Follow Windows", "system")
        self.lang.currentIndexChanged.connect(self._lang_changed)
        f5.addRow("言語 / Language", self.lang)
        self.close_to_tray = QCheckBox("サーバー稼働中に画面を閉じたら、タスクトレイに格納してサーバーを動かし続ける")
        f5.addRow(self.close_to_tray)
        self.login_start = QCheckBox("Windowsにサインインしたときにこのアプリを起動する (タスクトレイに格納した状態で起動)")
        f5.addRow(self.login_start)
        f5.addRow(C.help_label("サーバーを自動で起動するには、各サーバーの「サーバー設定 > 自動化」で"
                               "「アプリの起動時にこのサーバーも起動する」をオンにしてください。"))
        lay.addWidget(g5)

        g6 = QGroupBox("Discord通知 (任意)")
        f6 = QFormLayout(g6)
        self.dc_state = QLabel()
        f6.addRow("状態", self.dc_state)
        self.dc_url = QLineEdit()
        self.dc_url.setEchoMode(QLineEdit.Password)
        self.dc_url.setPlaceholderText("https://discord.com/api/webhooks/... (保存済みのURLは表示しません)")
        f6.addRow("Webhook URL", self.dc_url)
        from msm.integrations.discord import EVENTS

        self.dc_events = {}
        boxes = []
        for k, label in EVENTS.items():
            cb = QCheckBox(label)
            self.dc_events[k] = cb
            boxes.append(cb)
        f6.addRow("通知する出来事", C.hbox(*boxes[:4]))
        f6.addRow("", C.hbox(*boxes[4:]))
        f6.addRow(C.hbox(C.button("URLを保存", self.save_discord), C.button("テスト送信", self.test_discord),
                         C.button("URLを削除", self.delete_discord)))
        f6.addRow(C.help_label("Discordのチャンネル設定 →「連携サービス」→「ウェブフック」で作成したURLを貼り付けます。"
                               "URLは秘密情報のため暗号化 (DPAPI) して保存します。"))
        lay.addWidget(g6)

        lay.addLayout(C.hbox(C.button("設定を保存", self.save, "primary"), C.button("アプリのデータフォルダーを開く", lambda: open_path(app_data_dir()))))
        lay.addWidget(C.help_label(f"{APP_NAME} v{APP_VERSION}\nデータフォルダー: {privacy.path(app_data_dir())}"))
        lay.addStretch(1)

    def _dir_row(self, edit):
        return C.hbox(edit, C.button("参照...", lambda: self._pick_dir(edit)), stretch_end=False)

    def _file_row(self, edit):
        return C.hbox(edit, C.button("参照...", lambda: self._pick_file(edit)), stretch_end=False)

    def _pick_dir(self, edit):
        d = QFileDialog.getExistingDirectory(self, "フォルダーを選択", privacy.to_real_path(edit.text()))
        if d:
            edit.setText(privacy.path(d))

    def _pick_file(self, edit):
        p, _ = QFileDialog.getOpenFileName(self, "ファイルを選択", privacy.to_real_path(edit.text()), "実行ファイル (*.exe)")
        if p:
            edit.setText(privacy.path(p))

    def refresh(self) -> None:
        db = get_db()
        self.servers_root.setText(privacy.path(db.get_setting("servers_root") or str(default_servers_root())))
        self.backup_dir.setText(privacy.path(str(bk.backup_root())))
        self.watch_dir.setText(privacy.path(db.get_setting("download_watch_dir") or str(windows_downloads_dir())))
        self.playit.setText(privacy.path(db.get_setting("playit_path") or ""))
        self.cf_path.setText(privacy.path(db.get_setting("curseforge_path") or ""))
        self.cf_inst.setText(privacy.path(db.get_setting("curseforge_instances_dir") or ""))
        self.launcher.setText(privacy.path(db.get_setting("minecraft_launcher_path") or ""))
        self.update_url.setText(db.get_setting("update_url") or "")
        self.update_github.setText(db.get_setting("update_github") or "")
        self.update_auto.setChecked(bool(db.get_setting("update_auto_check", True)))
        self.key_state.setText("設定済み" if cf.has_api_key() else "未設定")
        self.close_to_tray.setChecked(bool(db.get_setting("close_to_tray", True)))
        self.theme.blockSignals(True)
        self.theme.setCurrentIndex(max(0, self.theme.findData(db.get_setting("theme", "system"))))
        self.theme.blockSignals(False)
        self.lang.blockSignals(True)
        self.lang.setCurrentIndex(max(0, self.lang.findData(db.get_setting("language", "ja"))))
        self.lang.blockSignals(False)
        from msm.core import startup
        from msm.integrations import discord

        self.login_start.setChecked(startup.is_enabled())
        self.dc_state.setText("設定済み" if discord.get_url() else "未設定")
        for k, v in discord.events().items():
            if k in self.dc_events:
                self.dc_events[k].setChecked(bool(v))

    def save(self):
        db = get_db()
        db.set_setting("servers_root", privacy.to_real_path(self.servers_root.text()))
        old_backup = str(bk.backup_root())
        new_backup = privacy.to_real_path(self.backup_dir.text())
        if new_backup and new_backup != old_backup:
            if C.confirm(self, f"バックアップの保存先を変更します。\n{old_backup}\n→ {new_backup}\n\n既存のバックアップは移動しません (履歴からは引き続き参照できます)。"):
                db.set_setting("backup_dir", new_backup)
        watch = privacy.to_real_path(self.watch_dir.text())
        db.set_setting("download_watch_dir", "" if os.path.normcase(watch) == os.path.normcase(str(windows_downloads_dir())) else watch)
        db.set_setting("playit_path", privacy.to_real_path(self.playit.text()))
        db.set_setting("curseforge_path", privacy.to_real_path(self.cf_path.text()))
        db.set_setting("curseforge_instances_dir", privacy.to_real_path(self.cf_inst.text()))
        db.set_setting("minecraft_launcher_path", privacy.to_real_path(self.launcher.text()))
        url = self.update_url.text().strip()
        if url and not url.startswith("https://"):
            C.warn(self, "更新情報URLは https:// で始まる必要があります。")
            return
        db.set_setting("update_url", url)
        db.set_setting("update_github", self.update_github.text().strip())
        db.set_setting("update_auto_check", self.update_auto.isChecked())
        db.set_setting("close_to_tray", self.close_to_tray.isChecked())
        from msm.core import startup
        from msm.integrations import discord

        if self.login_start.isChecked() != startup.is_enabled():
            try:
                startup.set_enabled(self.login_start.isChecked())
            except OSError as e:
                C.warn(self, f"スタートアップの設定を変更できませんでした: {e}")
        discord.set_events({k: cb.isChecked() for k, cb in self.dc_events.items()})
        C.info(self, "保存しました。")
        self.refresh()

    def _lang_changed(self, *_):
        from msm.gui import i18n

        i18n.set_language(self.lang.currentData())
        C.info(self, "言語の設定を保存しました。アプリを再起動すると反映されます。\n"
                     "The language setting was saved. Restart the app to apply it.\n\n"
                     "※ English mode translates menus, buttons and labels. Some long explanations and diagnostic "
                     "messages stay in Japanese.")

    def _theme_changed(self, *_):
        get_db().set_setting("theme", self.theme.currentData())
        w = self.window()
        if hasattr(w, "apply_theme"):
            w.apply_theme(self.theme.currentData())

    def save_discord(self):
        from msm.integrations import discord

        url = self.dc_url.text().strip()
        if not discord.valid_url(url):
            C.warn(self, "DiscordのWebhook URLの形式ではありません。\n(https://discord.com/api/webhooks/数字/文字列 の形式です)")
            return
        discord.set_url(url)
        discord.set_events({k: cb.isChecked() for k, cb in self.dc_events.items()})
        self.dc_url.clear()
        self.refresh()
        C.info(self, "保存しました。「テスト送信」で届くか確認できます。")

    def test_discord(self):
        from msm.integrations import discord

        url = discord.get_url()
        if not url:
            C.info(self, "Webhook URLが設定されていません。")
            return
        C.bg().run(lambda: discord.post(url, "✅ Minecraft Server Manager からのテスト通知です"),
                   lambda r, e: C.info(self, "送信しました。Discordに届いたか確認してください。") if (r and r < 300)
                   else C.warn(self, f"送信できませんでした: {e or ('HTTP ' + str(r))}"))

    def delete_discord(self):
        from msm.integrations import discord

        if C.confirm(self, "保存されているDiscordのWebhook URLを削除しますか？"):
            discord.set_url("")
            self.refresh()

    def save_key(self):
        k = self.key.text().strip()
        if not k:
            C.info(self, "APIキーを入力してください。")
            return
        cf.set_api_key(k)
        self.key.clear()
        self.refresh()
        self.test_key()

    def test_key(self):
        if not cf.has_api_key():
            C.info(self, "APIキーが設定されていません。")
            return
        C.bg().run(cf.test_key, lambda r, e: (C.info(self, r[1]) if r and r[0] else C.warn(self, r[1] if r else str(e))))

    def delete_key(self):
        if C.confirm(self, "保存されているCurseForge APIキーを削除しますか？"):
            cf.set_api_key("")
            self.refresh()

    def check_update(self):
        get_db().set_setting("update_url", self.update_url.text().strip())
        get_db().set_setting("update_github", self.update_github.text().strip())
        run_update_check(self, quiet=False)


def run_update_check(parent, quiet: bool = True) -> None:
    """更新を確認し、あれば確認のうえ更新する。quiet=True なら「最新です」等は表示しない."""

    def done(r, e):
        if e:
            if not quiet:
                C.error(parent, f"更新を確認できませんでした: {e}")
            return
        if r["status"] != "available":
            if not quiet:
                C.info(parent, r["message"])
            return
        notes = (r.get("notes") or "")[:800]
        if not r.get("auto"):
            if C.confirm(parent, r["message"] + (f"\n\n{notes}" if notes else "") + "\n\n配布ページを開きますか？"):
                C.open_url(parent, r["url"], ask=False)
            return
        if not C.confirm(parent, r["message"] + (f"\n\n{notes}" if notes else "") +
                         "\n\nダウンロードして更新しますか？\n(更新時にアプリを終了します。稼働中のサーバーは安全に停止します)"):
            return

        def job(p, l, c):
            path = update.download(r, p, c)
            return update.prepare(path, r["mode"])

        def ready(cmd):
            win = parent.window()
            update.launch(cmd)
            if hasattr(win, "quit_app"):
                win.quit_app()

        C.run_task(parent, "更新をダウンロードしています", job, ready)

    C.bg().run(update.check, done)
