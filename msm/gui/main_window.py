"""メインウィンドウ (左のメニュー + ページ切り替え + 選択中サーバー)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QPushButton, QStackedWidget, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from msm import APP_NAME, APP_VERSION
from msm.core.db import get_db
from msm.core.paths import resource_path
from msm.core.util import log
from msm.gui import common as C
from msm.gui.context import AppContext
from msm.server import ops
from msm.server.process import STATE_LABELS, registry

# 左のメニュー (7項目)。各メニューの中は (画面キー, タブ名) のタブで構成する
NAV_TOP = [
    ("home", "🏠  ホーム", "ホーム", "選択中のサーバーの状態・起動/停止・参加方法です。",
     [("dashboard", "概要"), ("monitor", "負荷グラフ")]),
    ("serverlist", "🗂  サーバー一覧", "サーバー一覧", "すべてのサーバーをまとめて管理します。複数のサーバーを同時に動かせます。",
     [("servers", "すべてのサーバー"), ("create", "＋ 新しく作る"), ("import", "📦 MODパックから作る")]),
    ("console", "🖥  コンソール", "コンソール", "",
     [("detail", "コンソール"), ("players", "プレイヤー"), ("logs", "ログ"), ("diagnose", "トラブル診断")]),
    ("modsmenu", "🧩  MOD", "", "", [("mods", "MOD")]),
    ("invite", "🌐  友達を招待", "", "", [("publish", "友達を招待")]),
    ("settingsmenu", "⚙  サーバー設定", "", "", [("settings", "サーバー設定")]),
    ("appmenu", "🔧  アプリ設定", "アプリ設定", "",
     [("app", "一般"), ("java", "Java"), ("integrations", "外部連携 (CurseForge・ランチャー)")]),
]
SETTINGS_EXTRA_TABS = [("worlds", "ワールド"), ("backups", "バックアップ")]  # サーバー設定のタブに追加する
NAV = [(k, l) for _t, _i, _ti, _s, tabs in NAV_TOP for k, l in tabs] + SETTINGS_EXTRA_TABS


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1280, 820)
        self.ctx = AppContext()
        from msm.gui.pages import (app_settings, backups, create, dashboard, detail, diagnose, import_pack,
                                   integrations, java_page, logs, mods, monitor, players, publish, server_settings,
                                   servers, worlds)

        factories = {
            "dashboard": dashboard.DashboardPage, "servers": servers.ServersPage, "create": create.CreatePage,
            "detail": detail.DetailPage, "mods": mods.ModsPage, "import": import_pack.ImportPage,
            "java": java_page.JavaPage, "diagnose": diagnose.DiagnosePage, "settings": server_settings.SettingsPage,
            "worlds": worlds.WorldsPage, "backups": backups.BackupsPage, "players": players.PlayersPage, "monitor": monitor.MonitorPage,
            "publish": publish.PublishPage, "logs": logs.LogsPage, "integrations": integrations.IntegrationsPage,
            "app": app_settings.AppSettingsPage,
        }
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        side = QWidget()
        side.setObjectName("side")
        side.setFixedWidth(230)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        appname = QLabel("⛏ Minecraft\nServer Manager")
        appname.setObjectName("appname")
        sl.addWidget(appname)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setSpacing(1)
        self.row_of: dict[str, int] = {}
        for top_key, label, _t, _s, _tabs in NAV_TOP:
            if top_key == "appmenu":
                sep = QListWidgetItem("")
                sep.setFlags(Qt.NoItemFlags)
                self.nav.addItem(sep)
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, top_key)
            self.row_of[top_key] = self.nav.count()
            self.nav.addItem(it)
        sl.addWidget(self.nav, 1)
        side.setStyleSheet("QWidget#side { background: %s; }" % __import__("msm.gui.theme", fromlist=["x"]).tokens()["nav_bg"])
        self.side = side
        root.addWidget(side)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        topw = QWidget()
        topw.setObjectName("topbar")
        top = QHBoxLayout(topw)
        top.setContentsMargins(18, 10, 18, 10)
        lab = QLabel("サーバー:")
        lab.setObjectName("caption")
        top.addWidget(lab)
        self.server_combo = QComboBox()
        self.server_combo.setMinimumWidth(340)
        top.addWidget(self.server_combo)
        self.state_label = QLabel()
        top.addWidget(self.state_label)
        self.quick_btn = QPushButton("▶ 起動")
        self.quick_btn.setObjectName("primary")
        self.quick_btn.clicked.connect(self._quick_toggle)
        top.addWidget(self.quick_btn)
        top.addStretch(1)
        self.privacy_btn = QPushButton()
        self.privacy_btn.setCheckable(True)
        self.privacy_btn.setToolTip("IPアドレス・公開アドレス・ユーザー名を伏せて表示します (配信・画面共有のとき用)")
        self.privacy_btn.toggled.connect(self._privacy_toggled)
        top.addWidget(self.privacy_btn)
        rl.addWidget(topw)
        self.stack = QStackedWidget()
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(22, 14, 22, 10)
        bl.addWidget(self.stack, 1)
        rl.addWidget(body, 1)
        root.addWidget(right, 1)
        self.setCentralWidget(central)

        from msm.gui.pages.containers import TabbedPage, _scroll, hide_page_title

        self.pages: dict[str, C.Page] = {}  # 画面キー → 画面
        self.parent_of: dict[str, str] = {}  # 画面キー → メニューキー
        self.tops: dict[str, QWidget] = {}
        self.page_index: dict[str, int] = {}
        for key, _label in NAV:
            self.pages[key] = factories[key](self.ctx)
        for top_key, _label, title, sub, tabs in NAV_TOP:
            if len(tabs) == 1:
                w = self.pages[tabs[0][0]]
            else:
                w = TabbedPage(self.ctx, title, sub, [(k, l, self.pages[k]) for k, l in tabs])
            for k, _l in tabs:
                self.parent_of[k] = top_key
            self.tops[top_key] = w
            self.page_index[top_key] = self.stack.addWidget(w if isinstance(w, TabbedPage) else _scroll(w))
        # ワールド・バックアップはサーバー設定のタブに入れる
        sp = self.pages["settings"]
        for k, label in SETTINGS_EXTRA_TABS:
            hide_page_title(self.pages[k])
            sp.tabs.addTab(_scroll(self.pages[k]), label)
            self.parent_of[k] = "settingsmenu"
        sp.tabs.currentChanged.connect(self._settings_tab_changed)

        self.nav.currentRowChanged.connect(self._on_nav)
        self.server_combo.currentIndexChanged.connect(self._on_combo)
        self.ctx.servers_changed.connect(self._reload_combo)
        self.ctx.current_changed.connect(self._on_current_changed)
        self.ctx.state_changed.connect(self._on_state_changed, Qt.QueuedConnection)
        self.ctx.navigate.connect(self.go)
        from msm.gui import privacy as _pv

        _pv.install_hooks()
        self.ctx.status.connect(lambda t: self.statusBar().showMessage(_pv.console(_pv.text(t)) if _pv.enabled() else t, 8000))
        self._reload_combo()
        self.nav.setCurrentRow(self.row_of["home"])
        self.statusBar().showMessage("準備完了")
        from msm.gui import privacy

        self.privacy_btn.blockSignals(True)
        self.privacy_btn.setChecked(privacy.enabled())
        self.privacy_btn.blockSignals(False)
        self._update_privacy_btn()
        from msm.gui import i18n

        i18n.install_hooks()
        i18n.translate_tree(self)

        self._quitting = False
        self._tray_notified = False
        self.tray: QSystemTrayIcon | None = None
        self._setup_tray()
        from msm.gui.automation import Automation

        self.automation = Automation(self.ctx, self)

    # ---- タスクトレイ ----
    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon_path = resource_path("assets", "icon.ico")
        icon = QIcon(str(icon_path)) if icon_path.exists() else self.windowIcon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        a_show = QAction("画面を開く", self)
        a_show.triggered.connect(self.show_from_tray)
        menu.addAction(a_show)
        a_stop = QAction("すべてのサーバーを停止", self)
        a_stop.triggered.connect(lambda: (self.show_from_tray(), __import__("msm.gui.actions", fromlist=["x"]).stop_all(self, self.ctx)))
        menu.addAction(a_stop)
        menu.addSeparator()
        a_quit = QAction("終了", self)
        a_quit.triggered.connect(self.quit_app)
        menu.addAction(a_quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_from_tray()
                                    if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger) else None)
        self.tray.show()
        self.ctx.state_changed.connect(lambda *_: self._update_tray_tip(), Qt.QueuedConnection)

    def _update_tray_tip(self) -> None:
        if self.tray:
            n = len(registry.running())
            self.tray.setToolTip(f"{APP_NAME} - 稼働中のサーバー: {n}台")

    def notify(self, title: str, text: str, error: bool = False) -> None:
        """トレイ通知 (画面を隠しているときに便利)."""
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(title, text, QSystemTrayIcon.Critical if error else QSystemTrayIcon.Information, 8000)

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._quitting = True
        self.show_from_tray()
        self.close()

    def open_wizard(self) -> None:
        from msm.gui.wizard import SetupWizard

        SetupWizard(self, self.ctx).exec()

    # ---- ナビゲーション ----
    def go(self, key: str) -> None:
        """画面キー (例: "java", "players") またはメニューキーで移動する."""
        top = self.parent_of.get(key, key)
        if top not in self.row_of:
            return
        if self.nav.currentRow() != self.row_of[top]:
            self.nav.setCurrentRow(self.row_of[top])
        w = self.tops[top]
        if hasattr(w, "select"):
            w.select(key)
        elif top == "settingsmenu":
            extra = [k for k, _l in SETTINGS_EXTRA_TABS]
            sp = self.pages["settings"]
            if key in extra:
                sp.tabs.setCurrentIndex(sp.tabs.count() - len(extra) + extra.index(key))
            elif key == "settings" and sp.tabs.currentIndex() >= sp.tabs.count() - len(extra):
                sp.tabs.setCurrentIndex(0)  # ワールド・バックアップのタブにいたら基本設定へ戻す
        self.pages.get(key, None) and key != top and self.pages[key].refresh()

    def current_key(self) -> str:
        it = self.nav.currentItem()
        top = it.data(Qt.UserRole) if it and it.data(Qt.UserRole) else "home"
        w = self.tops.get(top)
        if hasattr(w, "current_key"):
            return w.current_key()
        if top == "settingsmenu":
            extra = [k for k, _l in SETTINGS_EXTRA_TABS]
            sp = self.pages["settings"]
            i = sp.tabs.currentIndex() - (sp.tabs.count() - len(extra))
            return extra[i] if i >= 0 else "settings"
        return {"modsmenu": "mods", "invite": "publish"}.get(top, "dashboard")

    def _settings_tab_changed(self, *_):
        k = self.current_key()
        if k in ("worlds", "backups"):
            self.pages[k].refresh()

    def _on_nav(self, row: int) -> None:
        it = self.nav.item(row)
        top = it.data(Qt.UserRole) if it else None
        if not top:
            return
        self.stack.setCurrentIndex(self.page_index[top])
        try:
            self.pages[self.current_key()].refresh()
        except Exception as e:  # noqa: BLE001
            log.exception("refresh %s", top)
            self.statusBar().showMessage(f"画面の更新でエラー: {e}", 10000)

    def _update_mod_menu(self) -> None:
        """MOD・プラグインが使えないサーバー (Vanilla) ではMODメニューを隠す."""
        s = self.ctx.current()
        item = self.nav.item(self.row_of["modsmenu"])
        if s is None:
            item.setHidden(True)
            return
        item.setHidden(s.server_type == "vanilla")
        from msm.gui.i18n import tr

        item.setText("🧩  " + (tr("プラグイン") if s.server_type in ("paper", "purpur") else "MOD"))
        if s.server_type == "vanilla" and self.nav.currentRow() == self.row_of["modsmenu"]:
            self.go("home")

    def _reload_combo(self) -> None:
        self.server_combo.blockSignals(True)
        self.server_combo.clear()
        cur = self.ctx.current()
        for s in self.ctx.servers():
            self.server_combo.addItem(self._combo_text(s), s.id)
        if cur:
            idx = self.server_combo.findData(cur.id)
            self.server_combo.setCurrentIndex(max(0, idx))
        if self.server_combo.count() == 0:
            self.server_combo.addItem("(サーバーがありません。新規作成してください)", None)
        self.server_combo.blockSignals(False)
        self._update_state_label()
        if hasattr(self, "row_of"):
            self._update_mod_menu()

    STATE_MARK = {"running": "🟢", "starting": "🟡", "stopping": "🟡", "crashed": "🔴"}

    def _combo_text(self, s) -> str:
        mark = self.STATE_MARK.get(self.ctx.state_of(s.id), "⚪")
        return f"{mark} {s.name}  ({s.mc_version} {s.type_label})"

    def _refresh_combo_marks(self) -> None:
        for i in range(self.server_combo.count()):
            sid = self.server_combo.itemData(i)
            s = next((x for x in self.ctx.servers() if x.id == sid), None)
            if s:
                self.server_combo.setItemText(i, self._combo_text(s))

    def _on_combo(self, idx: int) -> None:
        sid = self.server_combo.itemData(idx)
        self.ctx.set_current(sid)

    def _on_current_changed(self, sid: str) -> None:
        idx = self.server_combo.findData(sid)
        if idx >= 0 and idx != self.server_combo.currentIndex():
            self.server_combo.blockSignals(True)
            self.server_combo.setCurrentIndex(idx)
            self.server_combo.blockSignals(False)
        self._update_state_label()
        self._update_mod_menu()
        self.pages[self.current_key()].refresh()

    def _on_state_changed(self, sid: str, st: str) -> None:
        self._update_state_label()
        self._refresh_combo_marks()
        name = next((x.name for x in self.ctx.servers() if x.id == sid), sid)
        self.statusBar().showMessage(f"{name}: {STATE_LABELS.get(st, st)}", 8000)
        handled = self.automation.on_state(sid, st)
        if st == "crashed" and not handled:
            from msm.gui.pages.diagnose import auto_diagnose_notice

            auto_diagnose_notice(self, self.ctx, sid)

    def _update_state_label(self) -> None:
        s = self.ctx.current()
        if not s:
            self.state_label.setText("")
            return
        st = self.ctx.state_of(s.id)
        self.state_label.setText(C.pill(st, STATE_LABELS.get(st, st)))
        running = st in ("starting", "running", "stopping")
        self.quick_btn.setText("■ 停止" if running else "▶ 起動")
        self.quick_btn.setObjectName("danger" if running else "primary")
        self.quick_btn.setEnabled(st != "stopping")
        self.quick_btn.style().unpolish(self.quick_btn)
        self.quick_btn.style().polish(self.quick_btn)

    def _quick_toggle(self) -> None:
        from msm.gui import actions

        s = self.ctx.current()
        if not s:
            self.go("create")
            return
        if self.ctx.state_of(s.id) in ("starting", "running"):
            actions.stop_server(self, self.ctx, s)
        else:
            actions.start_server(self, self.ctx, s)
        self._update_state_label()

    # ---- 伏せ表示・テーマ ----
    def _update_privacy_btn(self) -> None:
        on = self.privacy_btn.isChecked()
        self.privacy_btn.setText("🙈 伏せ表示: オン" if on else "👁 伏せ表示: オフ")

    def _privacy_toggled(self, on: bool) -> None:
        from msm.gui import privacy

        if not on and not C.confirm(self, "伏せ表示をオフにすると、IPアドレス・公開アドレス・ユーザー名がそのまま表示されます。\n"
                                          "配信や画面共有をしていないことを確認してください。\n\nオフにしますか？"):
            self.privacy_btn.blockSignals(True)
            self.privacy_btn.setChecked(True)
            self.privacy_btn.blockSignals(False)
            return
        privacy.set_enabled(on)
        self._update_privacy_btn()
        self.pages[self.current_key()].refresh()

    def apply_theme(self, setting: str | None = None) -> None:
        from msm.gui import theme

        setting = setting or get_db().get_setting("theme", "system")
        dark = theme.apply(QApplication.instance(), setting)
        self.side.setStyleSheet("QWidget#side { background: %s; }" % theme.tokens()["nav_bg"])
        for p in list(self.pages.values()) + list(self.tops.values()):
            if hasattr(p, "set_dark"):
                p.set_dark(dark)
        self._update_state_label()
        self._update_mod_menu()
        self.pages[self.current_key()].refresh()

    # ---- 終了処理 ----
    def closeEvent(self, ev) -> None:
        running = registry.running()
        if (running and not self._quitting and self.tray is not None
                and bool(get_db().get_setting("close_to_tray", True))):
            self.hide()
            if not self._tray_notified:
                self._tray_notified = True
                self.notify(APP_NAME, "サーバーは動作を続けています。タスクトレイのアイコンから画面を開けます。\n"
                                      "完全に終了するときはアイコンを右クリック →「終了」を選んでください。")
            ev.ignore()
            return
        self._quitting = False
        if running:
            names = "\n".join(f"・{p.server.name}" for p in running)
            choice = C.ask_choice(
                self, f"稼働中のサーバーがあります:\n{names}\n\nアプリを終了するにはサーバーを停止する必要があります。"
                      "\n(コンソール入力ができなくなるため、サーバーだけを動かし続けることはできません)",
                ["すべて安全に停止して終了"], "終了の確認")
            if choice != 0:
                ev.ignore()
                return
            C.run_task(self, "サーバーを停止しています", lambda p, l, c: ops.stop_all(120), cancellable=False)
            still = registry.running()
            if still:
                if C.confirm(self, "停止しないサーバーがあります。強制終了しますか？\n(ワールドが破損する恐れがあります)", danger=True):
                    for sp in still:
                        sp.kill()
                else:
                    ev.ignore()
                    return
        try:
            self.automation.close_all_upnp()
        except Exception:
            pass
        try:
            self.ctx.playit.stop(False)
            for t in self.ctx.custom_tunnels.values():
                t.stop()
        except Exception:
            pass
        if self.tray:
            self.tray.hide()
        ev.accept()
        QTimer.singleShot(0, QApplication.instance().quit)
