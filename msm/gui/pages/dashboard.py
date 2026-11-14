"""ダッシュボード: 状態・操作・参加方法を1画面にまとめる."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QVBoxLayout, QWidget)

from msm.core.db import get_db
from msm.core.util import human_size
from msm.gui import actions
from msm.gui import common as C
from msm.gui.i18n import T, tr
from msm.gui.privacy import SecretLabel
from msm.integrations import external
from msm.java import manager as jm
from msm.publish.tunnels import METHOD_INFO, join_addresses, playit_status
from msm.server.monitor import tps_method
from msm.server.process import STATE_LABELS, registry


def fmt_uptime(sec: float) -> str:
    if sec <= 0:
        return "-"
    h, rem = divmod(int(sec), 3600)
    m, _s = divmod(rem, 60)
    return f"{h}時間{m:02d}分" if h else f"{m}分"


class DashboardPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.addWidget(C.page_header("ダッシュボード"))

        # ---- サーバーが無いとき ----
        self.empty = C.card("ようこそ！まずはサーバーを作りましょう",
                            "質問に答えるだけで、サーバーの作成から友達の招待まで案内します。")
        self.empty.body.addLayout(C.hbox(C.button("かんたんセットアップを始める", self.open_wizard, "big"),
                                         C.button("自分で設定して作る", lambda: ctx.navigate.emit("create")),
                                         C.button("MODパックから作る", lambda: ctx.navigate.emit("import"))))
        lay.addWidget(self.empty)

        # ---- サーバーの状態と操作 ----
        self.hero = C.card()
        hl = QHBoxLayout()
        info = QVBoxLayout()
        self.h_name = QLabel("-")
        self.h_name.setObjectName("big")
        self.h_sub = QLabel("-")
        self.h_sub.setObjectName("caption")
        self.h_state = QLabel("-")
        self.h_state.setStyleSheet("font-size: 12pt;")
        info.addWidget(self.h_name)
        info.addWidget(self.h_sub)
        info.addWidget(self.h_state)
        hl.addLayout(info, 1)
        btns = QVBoxLayout()
        self.b_main = QPushButton("▶  サーバーを起動")
        self.b_main.setObjectName("big")
        self.b_main.clicked.connect(self.toggle)
        btns.addWidget(self.b_main)
        row = QHBoxLayout()
        self.b_restart = C.button("再起動", self.restart)
        self.b_all = C.button("全部起動", self.start_all, "", "サーバー → 公開用トンネル → ランチャーを順番に起動します")
        self.b_more = QPushButton("その他")
        menu = QMenu(self)
        menu.addAction("Minecraftを起動", self.launch_mc)
        self.a_cf = menu.addAction("CurseForgeを起動", self.launch_cf)
        menu.addAction("参加方法を表示", self.join_guide)
        menu.addSeparator()
        menu.addAction("コンソールを開く", lambda: ctx.navigate.emit("detail"))
        menu.addAction("サーバー設定を開く", lambda: ctx.navigate.emit("settings"))
        self.b_more.setMenu(menu)
        self.b_why = C.button("🩺 原因を調べる", self.investigate, "danger", "ログから異常終了の原因を調べます")
        btns.addWidget(self.b_why)
        row.addWidget(self.b_restart)
        row.addWidget(self.b_all)
        row.addWidget(self.b_more)
        btns.addLayout(row)
        hl.addLayout(btns)
        self.hero.body.addLayout(hl)
        lay.addWidget(self.hero)

        # ---- 数値 ----
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        tiles.setContentsMargins(0, 0, 0, 0)
        self.t_players = C.tile("参加人数")
        self.t_players[0].setCursor(Qt.PointingHandCursor)
        self.t_players[0].setToolTip("クリックでプレイヤー一覧 (キック・OP・ホワイトリスト) を開きます")
        self.t_players[0].mousePressEvent = lambda e: self.ctx.navigate.emit("players")
        self.t_mem = C.tile("メモリ")
        self.t_cpu = C.tile("CPU")
        self.t_uptime = C.tile("稼働時間")
        self.t_tps = C.tile("TPS (20が正常)")
        for t in (self.t_players, self.t_mem, self.t_cpu, self.t_uptime, self.t_tps):
            tiles.addWidget(t[0], 1)
        self.tiles_w = QWidget()
        self.tiles_w.setLayout(tiles)
        lay.addWidget(self.tiles_w)

        # ---- 参加方法 ----
        self.join = C.card("参加する")
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(8)
        g.addWidget(self._cap("このPCから"), 0, 0)
        self.j_local = SecretLabel("-")
        self.j_local.on_copied = lambda v: ctx.status.emit(f"「{v}」をコピーしました")
        g.addWidget(self.j_local, 0, 1)
        g.addWidget(self._cap("同じWi-Fiの人"), 1, 0)
        self.j_lan = SecretLabel("-")
        self.j_lan.on_copied = lambda v: ctx.status.emit("LANアドレスをコピーしました")
        g.addWidget(self.j_lan, 1, 1)
        g.addWidget(self._cap("友達 (インターネット)"), 2, 0)
        self.j_public = SecretLabel("-", big=True)
        self.j_public.on_copied = lambda v: ctx.status.emit("公開アドレスをコピーしました")
        g.addWidget(self.j_public, 2, 1)
        self.j_method = QLabel("-")
        self.j_method.setObjectName("caption")
        g.addWidget(self.j_method, 3, 1)
        g.setColumnStretch(1, 1)
        self.join.body.addLayout(g)
        self.join_notice = QLabel("")
        self.join_notice.setObjectName("notice")
        self.join_notice.setWordWrap(True)
        self.join.body.addWidget(self.join_notice)
        self.join.body.addLayout(C.hbox(C.button("友達を招待する", lambda: ctx.navigate.emit("publish")),
                                        C.button("参加方法を見る", self.join_guide)))
        lay.addWidget(self.join)

        # ---- はじめての方へ ----
        self.guide = C.card("はじめの3ステップ")
        self.g_steps = QLabel("")
        self.g_steps.setWordWrap(True)
        self.g_steps.setTextFormat(Qt.RichText)
        self.guide.body.addWidget(self.g_steps)
        self.guide.body.addLayout(C.hbox(C.button("このガイドを閉じる", self._hide_guide, "link")))
        lay.addWidget(self.guide)

        # ---- 詳細オプション ----
        self.opts = C.card("オプション")
        self.launcher_choice = QComboBox()
        self.launcher_choice.addItems(["ランチャーは起動しない", "Minecraftランチャーも起動", "CurseForgeも起動"])
        self.launcher_choice.setCurrentIndex(int(get_db().get_setting("allstart_launcher", 0) or 0))
        self.launcher_choice.currentIndexChanged.connect(lambda i: get_db().set_setting("allstart_launcher", i))
        self.tps_check = QCheckBox("TPS/MSPTを定期的に取得する (1分ごとにコマンドを送信)")
        self.tps_check.toggled.connect(lambda v: get_db().set_setting("tps_polling", v))
        self.opts.body.addLayout(C.hbox(QLabel("「全部起動」のとき:"), self.launcher_choice, self.tps_check))
        self.multi_label = QLabel("-")
        self.multi_label.setObjectName("caption")
        self.opts.body.addLayout(C.hbox(self.multi_label, None, C.button("サーバー一覧", lambda: ctx.navigate.emit("servers")),
                                        C.button("一括停止", lambda: actions.stop_all(self, self.ctx)), stretch_end=False))
        lay.addWidget(self.opts)
        lay.addStretch(1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(2000)
        self._java_label: dict[str, str] = {}
        self._mods_count: dict[str, int] = {}
        self._playit_checked = 0.0
        ctx.state_changed.connect(lambda *_: self.tick(), Qt.QueuedConnection)
        ctx.players_changed.connect(lambda *_: self.tick(), Qt.QueuedConnection)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        QTimer.singleShot(0, self.refresh)

    @staticmethod
    def _cap(text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("caption")
        return l

    def open_wizard(self):
        w = self.window()
        if hasattr(w, "open_wizard"):
            w.open_wizard()

    def _hide_guide(self):
        get_db().set_setting("dashboard_guide_hidden", True)
        self.guide.hide()

    def refresh(self) -> None:
        s = self.ctx.current()
        self.tps_check.blockSignals(True)
        self.tps_check.setChecked(bool(get_db().get_setting("tps_polling", False)))
        self.tps_check.blockSignals(False)
        if s:
            self._java_label.pop(s.id, None)
            self._mods_count[s.id] = len(list(s.mods_dir.glob("*.jar"))) if s.mods_dir.exists() else 0
        self.tick()

    def _java_text(self, s) -> str:
        if s.id not in self._java_label:
            try:
                j, _req, _note = jm.resolve_for_server(s)
                self._java_label[s.id] = f"Java {j.major}" if j else "Java未設定"
            except Exception:  # noqa: BLE001
                self._java_label[s.id] = "Java不明"
        return self._java_label[s.id]

    def tick(self) -> None:
        if not self.isVisible():
            return
        s = self.ctx.current()
        has = s is not None
        self.empty.setVisible(not has)
        for w in (self.hero, self.tiles_w, self.join, self.opts):
            w.setVisible(has)
        running_list = registry.running()
        self.multi_label.setText(T(f"登録サーバー {len(self.ctx.servers())}台 ・ 稼働中 {len(running_list)}台",
                                   f"{len(self.ctx.servers())} servers ・ {len(running_list)} running"))
        if not s:
            self.guide.hide()
            return
        sp = registry.find(s.id)
        st = self.ctx.state_of(s.id)
        running = bool(sp and sp.running)
        ready = st == "running"
        # ---- 状態 ----
        self.h_name.setText(s.name)
        n = self._mods_count.get(s.id, 0)
        extra = T(f" ・ MOD {n}個", f" ・ {n} mods") if s.is_modded else (
            T(f" ・ プラグイン {n}個", f" ・ {n} plugins") if s.server_type in ("paper", "purpur") else "")
        self.h_sub.setText(f"Minecraft {s.mc_version} ・ {s.type_label} {s.loader_version} ・ {self._java_text(s)}"
                           + T(f" ・ ポート {s.port}", f" ・ port {s.port}") + extra)
        hint = {"running": "遊べます", "starting": "起動しています... (MODが多いと数分かかります)",
                "stopping": "安全に停止しています...", "stopped": "「サーバーを起動」を押すと遊べます", "crashed": "異常終了しました → トラブル診断へ"}
        self.h_state.setText(C.pill(st, STATE_LABELS.get(st, st)) + f"<span style='font-size:10pt'>　{tr(hint.get(st, ''))}</span>")
        if running:
            self.b_main.setText("■  サーバーを停止")
            self.b_main.setObjectName("bigstop")
        else:
            self.b_main.setText("▶  サーバーを起動")
            self.b_main.setObjectName("big")
        self.b_main.style().unpolish(self.b_main)
        self.b_main.style().polish(self.b_main)
        self.b_main.setEnabled(st != "stopping")
        self.b_restart.setEnabled(running)
        self.b_all.setEnabled(not running)
        self.b_why.setVisible(st == "crashed")
        self.a_cf.setVisible(external.find_curseforge() is not None)
        # ---- 数値 ----
        if running:
            stats = sp.stats()
            self.t_players[1].setText(f"{len(sp.players)} / {sp.max_players}")
            self.t_players[2].setText(", ".join(sorted(sp.players))[:40] if sp.players else "")
            rss = stats.get("rss")
            self.t_mem[1].setText(human_size(rss) if rss else "-")
            self.t_mem[2].setText(T(f"割り当て {s.max_mem_mb / 1024:.1f}GB", f"of {s.max_mem_mb / 1024:.1f}GB allocated"))
            cpu = stats.get("cpu")
            self.t_cpu[1].setText(f"{cpu:.0f}%" if cpu is not None else "-")
            self.t_cpu[2].setText("PC全体に対する割合")
            self.t_uptime[1].setText(fmt_uptime(sp.uptime()))
            self.t_uptime[2].setText("")
        else:
            self.t_players[1].setText(f"0 / {s.max_players}")
            self.t_players[2].setText("")
            self.t_mem[1].setText("-")
            self.t_mem[2].setText(T(f"割り当て {s.max_mem_mb / 1024:.1f}GB", f"{s.max_mem_mb / 1024:.1f}GB allocated"))
            self.t_cpu[1].setText("-")
            self.t_cpu[2].setText("")
            self.t_uptime[1].setText("-")
            self.t_uptime[2].setText("")
        cmd, _desc = tps_method(s.server_type, s.mc_version)
        auto = getattr(self.window(), "automation", None)
        t = auto.last_tps.get(s.id) if (auto and running) else None
        if not cmd:
            self.t_tps[1].setText("未対応")
            self.t_tps[2].setText("この種類では取得できません")
        elif t and t.get("tps") is not None:
            self.t_tps[1].setText(f"{t['tps']:.1f}")
            self.t_tps[2].setText(f"MSPT {t['mspt']:.1f}ms" if t.get("mspt") is not None else "")
        else:
            self.t_tps[1].setText("-")
            self.t_tps[2].setText("オプションで取得をオン" if not self.tps_check.isChecked() else "取得待ち")
        # ---- 参加方法 ----
        addrs = join_addresses(s)
        self.j_local.set_value(addrs["localhost"])
        self.j_local.eye.setVisible(False)
        self.j_local.label.setText(addrs["localhost"])
        self.j_lan.set_value(addrs["lan"][0] if addrs["lan"] else "", "(取得できません)")
        public = self.ctx.public_address(s) if s.publish_method != "lan" else ""
        method = METHOD_INFO.get(s.publish_method, {}).get("label", s.publish_method)
        self.j_public.set_value(public, "未設定 (「友達を招待」で招待の方法を選んでください)" if s.publish_method == "lan"
                                else "まだ分かりません (「友達を招待」で確認・入力できます)")
        self.j_method.setText(T("公開方法: ", "Method: ") + f"{tr(method)} ・ {tr(self.ctx.tunnel_state(s))}")
        if not ready:
            self.join_notice.setText(T("サーバーが起動していないため、まだ参加できません。上の「サーバーを起動」を押してください。",
                                       "The server is not running, so nobody can join yet. Press \"Start server\" above."))
            self.join_notice.show()
        else:
            self.join_notice.hide()
        if s.publish_method == "playit" and time.time() - self._playit_checked > 20:
            self._playit_checked = time.time()
            C.bg().run(playit_status, lambda r, e: setattr(self.ctx, "playit_status", r) if r else None)
        # ---- ガイド ----
        if get_db().get_setting("dashboard_guide_hidden", False):
            self.guide.hide()
        else:
            self.guide.show()
            ever = (s.path / "logs" / "latest.log").exists()
            ok = "<span style='color:%s'>✔</span>" % C.state_color("running")
            steps = [
                (ready or ever, tr("サーバーを起動する"), tr("上の大きな「サーバーを起動」ボタンを押します。初回は準備に少し時間がかかります。")),
                (False, tr("Minecraftで参加する"),
                 T(f"Minecraft {s.mc_version} を起動 →「マルチプレイ」→「サーバーを追加」→ アドレスに <b>{addrs['localhost']}</b> と入力",
                   f"Launch Minecraft {s.mc_version} → Multiplayer → Add Server → enter <b>{addrs['localhost']}</b> as the address")),
                (s.publish_method != "lan", tr("友達を招待する"),
                 T("「友達を招待」で招待の方法を選び、表示された「友達 (インターネット)」のアドレスを友達に伝えます",
                   "Choose how to invite in \"Publishing\" and share the \"Friends (internet)\" address")
                 + (tr("。MODサーバーなら「友達を招待 → 友達用パックを作成」で同じMODを渡せます") if s.is_modded else "")),
            ]
            html = []
            for i, (done, title, desc) in enumerate(steps, 1):
                mark = ok if done else f"<b>{i}</b>"
                html.append(f"<p style='margin:4px 0'>{mark}　<b>{title}</b><br>"
                            f"<span style='font-size:9pt'>　　{desc}</span></p>")
            self.g_steps.setText("".join(html))

    # ---- 操作 ----
    def investigate(self):
        self.ctx.navigate.emit("diagnose")
        w = self.window()
        page = getattr(w, "pages", {}).get("diagnose")
        if page:
            page.run_diagnosis()

    def toggle(self):
        s = self.ctx.current()
        if not s:
            return
        if self.ctx.state_of(s.id) in ("starting", "running"):
            actions.stop_server(self, self.ctx, s)
        else:
            actions.start_server(self, self.ctx, s)
        self.tick()

    def start(self):
        s = self.ctx.current()
        if s:
            actions.start_server(self, self.ctx, s)

    def stop(self):
        s = self.ctx.current()
        if s:
            actions.stop_server(self, self.ctx, s)

    def restart(self):
        s = self.ctx.current()
        if s:
            actions.restart_server(self, self.ctx, s)

    def copy_local(self):
        s = self.ctx.current()
        if s:
            a = join_addresses(s)["localhost"]
            C.copy_text(a)
            self.ctx.status.emit(f"「{a}」をコピーしました")

    def launch_mc(self):
        if not external.launch_minecraft():
            C.warn(self, "Minecraftランチャーが見つかりません。アプリ設定で場所を指定できます。")

    def launch_cf(self):
        if not external.launch_curseforge():
            C.warn(self, "CurseForgeが見つかりません。")

    def join_guide(self):
        s = self.ctx.current()
        if not s:
            return
        from msm.gui import privacy

        addrs = join_addresses(s, self.ctx.public_address(s))
        public = addrs.get("public") or "(「友達を招待」で確認)"
        if privacy.enabled() and addrs.get("public"):
            public = privacy.MASK + " (ホームのコピーボタンでコピーできます)"
        C.info(self, external.JOIN_GUIDE.format(mc=s.mc_version, loader=s.type_label, local=addrs["localhost"],
                                                public=public), "参加方法")

    def start_all(self):
        s = self.ctx.current()
        if not s:
            return
        if not actions.start_server(self, self.ctx, s):
            return
        sp = registry.find(s.id)
        launcher = self.launcher_choice.currentIndex()
        want_tunnel = s.publish_method in ("playit", "custom")

        def job(p, l, c):
            p("1/3 Minecraftサーバーの起動完了を待っています...", None)
            l("サーバーの起動を待っています (MODが多いと数分かかります)")
            t0 = time.time()
            while sp.running and sp.state != "running":
                if c.wait(1):
                    return "キャンセルしました"
                if time.time() - t0 > 1200:
                    raise RuntimeError("20分待っても起動が完了しませんでした")
            if not sp.running:
                raise RuntimeError("サーバーが起動中に停止しました。トラブル診断を確認してください。")
            l("サーバーの起動が完了しました")
            addr = ""
            if want_tunnel:
                p("2/3 公開用トンネルを起動しています...", None)
                l(self.ctx.start_tunnel(s) or "トンネル起動")
                for _ in range(30):
                    addr = self.ctx.public_address(s)
                    if addr or c.wait(1):
                        break
                l("公開アドレス: " + ("取得しました" if addr else "自動取得できませんでした (「友達を招待」で確認してください)"))
            else:
                l("2/3 トンネルは使用しません")
            p("3/3 ランチャーを起動しています...", None)
            if launcher == 1:
                l("Minecraftランチャーを起動" if external.launch_minecraft() else "Minecraftランチャーが見つかりません")
            elif launcher == 2:
                l("CurseForgeを起動" if external.launch_curseforge() else "CurseForgeが見つかりません")
            return addr

        def done(addr):
            local = join_addresses(s)["localhost"]
            C.copy_text(local)
            msg = f"準備ができました。\n\nこのPCから参加: {local} (コピーしました)"
            if addr:
                msg += "\n友達の参加先: ホームの「友達 (インターネット)」からコピーできます"
            if launcher == 2 or (launcher == 0 and not external.find_curseforge()):
                msg += "\n\nCurseForgeが無い場合は「参加方法を見る」の手順で通常のランチャーから参加できます。"
            C.info(self, msg)

        C.run_task(self, "全部起動", job, done, keep_open=False)
