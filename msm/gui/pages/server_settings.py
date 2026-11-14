"""サーバー設定 (基本設定 / server.properties 日本語編集 / プリセット)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from msm.backup import backup as bk
from msm.gui import common as C
from msm.gui import privacy
from msm.java import manager as jm
from msm.server import instance, ports
from msm.server.presets import PRESETS, pc_info, recommend, safe_max_memory
from msm.server.process import registry
from msm.server.properties import PROPERTY_DEFS, danger_warnings, read_properties, write_properties


class SettingsPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("サーバー設定", "変更前に自動でバックアップを作成します。多くの設定はサーバーの再起動後に反映されます。"))
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # ---- 基本設定 ----
        basic = QWidget()
        bf = QFormLayout(basic)
        self.name = QLineEdit()
        bf.addRow("サーバー名", self.name)
        self.maxmem = QSpinBox()
        self.maxmem.setRange(512, 262144)
        self.maxmem.setSingleStep(512)
        self.maxmem.setSuffix(" MB")
        self.minmem = QSpinBox()
        self.minmem.setRange(256, 262144)
        self.minmem.setSingleStep(512)
        self.minmem.setSuffix(" MB")
        bf.addRow("最大メモリ", self.maxmem)
        bf.addRow("最小メモリ", self.minmem)
        self.mem_help = C.help_label("")
        bf.addRow("", self.mem_help)
        self.java_mode = QComboBox()
        self.java_mode.addItem("自動選択 (Minecraftのバージョンに合わせる)", "auto")
        self.java_mode.addItem("手動で選ぶ", "manual")
        self.java_mode.currentIndexChanged.connect(lambda *_: self.java_combo.setEnabled(self.java_mode.currentData() == "manual"))
        bf.addRow("Java", self.java_mode)
        self.java_combo = QComboBox()
        bf.addRow("使用するJava", self.java_combo)
        self.java_help = C.help_label("")
        bf.addRow("", self.java_help)
        self.jvm_args = QLineEdit()
        self.jvm_args.setPlaceholderText("上級者向け (例: -XX:+UseG1GC)。通常は空欄のままにしてください")
        bf.addRow("追加のJVM引数", self.jvm_args)
        self.auto_backup = QSpinBox()
        self.auto_backup.setRange(0, 1440)
        self.auto_backup.setSuffix(" 分ごと (0で無効)")
        bf.addRow("自動バックアップ", self.auto_backup)
        self.backup_keep = QSpinBox()
        self.backup_keep.setRange(1, 500)
        self.backup_keep.setSuffix(" 個まで保持")
        bf.addRow("自動バックアップの保持数", self.backup_keep)
        bf.addRow(C.button("基本設定を保存", self.save_basic, "primary"))
        self.tabs.addTab(basic, "基本設定")

        # ---- server.properties ----
        props = QWidget()
        pl = QVBoxLayout(props)
        g = QGroupBox("server.properties (日本語)")
        self.pform = QFormLayout(g)
        self.widgets: dict[str, QWidget] = {}
        for d in PROPERTY_DEFS:
            if d.kind == "bool":
                w = QCheckBox("有効")
            elif d.kind == "int":
                w = QSpinBox()
                w.setRange(d.min if d.min is not None else 0, d.max if d.max is not None else 1_000_000)
            elif d.kind == "choice":
                w = QComboBox()
                for v, label in d.choices:
                    w.addItem(f"{label} ({v})", v)
            else:
                w = QLineEdit()
            w.setToolTip(d.help)
            self.widgets[d.key] = w
            box = QWidget()
            vb = QVBoxLayout(box)
            vb.setContentsMargins(0, 0, 0, 4)
            vb.addWidget(w)
            hl = C.help_label(d.help + (" ⚠" if d.danger_value else ""))
            vb.addWidget(hl)
            self.pform.addRow(d.label, box)
        pl.addWidget(g)
        pl.addLayout(C.hbox(C.button("server.propertiesを保存", self.save_props, "primary"),
                            C.button("ファイルから再読み込み", self.load_props)))
        self.tabs.addTab(props, "ゲーム設定")

        raw = QWidget()
        rl = QVBoxLayout(raw)
        rl.addWidget(C.help_label("server.propertiesを直接編集します (上級者向け)。保存前にバックアップされます。"))
        self.raw = QPlainTextEdit()
        self.raw.setFont(C.mono_font())
        rl.addWidget(self.raw, 1)
        rl.addLayout(C.hbox(C.button("直接編集した内容を保存", self.save_raw)))
        self.tabs.addTab(raw, "直接編集")

        # ---- プリセット ----
        pre = QWidget()
        prl = QVBoxLayout(pre)
        self.rec_label = QLabel()
        self.rec_label.setWordWrap(True)
        prl.addWidget(self.rec_label)
        self.preset_list = QListWidget()
        for p in PRESETS:
            it = QListWidgetItem(f"{p.label} - {p.description}")
            it.setData(256, p.key)
            self.preset_list.addItem(it)
        prl.addWidget(self.preset_list)
        prl.addLayout(C.hbox(C.button("選択したプリセットを確認して適用...", self.apply_preset, "primary"),
                             C.button("おすすめを適用...", lambda: self.apply_preset(use_recommended=True))))
        prl.addStretch(1)
        self.tabs.addTab(pre, "プリセット")
        self._rec = None

        # ---- 自動化 ----
        auto = QWidget()
        af = QFormLayout(auto)
        self.a_restart = QCheckBox("異常終了したら自動で再起動する")
        af.addRow(self.a_restart)
        self.a_max = QSpinBox()
        self.a_max.setRange(1, 20)
        self.a_window = QSpinBox()
        self.a_window.setRange(1, 240)
        self.a_window.setSuffix(" 分以内に")
        self.a_max.setSuffix(" 回まで (超えたら自動再起動を止めて知らせます)")
        af.addRow("自動再起動の上限", C.hbox(self.a_window, self.a_max))
        self.a_autostart = QCheckBox("アプリの起動時にこのサーバーも起動する")
        af.addRow(self.a_autostart)
        self.a_discord = QCheckBox("このサーバーのDiscord通知を送る (Webhookの設定は「アプリ設定」)")
        af.addRow(self.a_discord)
        af.addRow(QLabel("<b>スケジュール</b>"))
        self.sc_enabled = QCheckBox("スケジュールを有効にする")
        af.addRow(self.sc_enabled)
        self.sc_days = []
        days_row = []
        from msm.server.schedule import WEEKDAYS

        for d in WEEKDAYS:
            cb = QCheckBox(d)
            self.sc_days.append(cb)
            days_row.append(cb)
        af.addRow("曜日", C.hbox(*days_row))
        self.sc_restart = QLineEdit()
        self.sc_restart.setPlaceholderText("例: 04:00 (複数はカンマ区切り 04:00, 16:00)")
        af.addRow("定時再起動", self.sc_restart)
        self.sc_start = QLineEdit()
        self.sc_start.setPlaceholderText("例: 18:00 (空欄で使わない)")
        af.addRow("毎日の起動時刻", self.sc_start)
        self.sc_stop = QLineEdit()
        self.sc_stop.setPlaceholderText("例: 02:00 (空欄で使わない)")
        af.addRow("毎日の停止時刻", self.sc_stop)
        self.sc_backup = QLineEdit()
        self.sc_backup.setPlaceholderText("例: 12:00, 00:00")
        af.addRow("定時バックアップ", self.sc_backup)
        self.sc_warn = QLineEdit()
        self.sc_warn.setPlaceholderText("例: 5, 1")
        af.addRow("再起動・停止の予告 (分前)", self.sc_warn)
        af.addRow(C.help_label("※ スケジュールはこのアプリが起動している間だけ動作します (タスクトレイに格納中も動作します)。\n"
                               "※ 予告はゲーム内チャット (sayコマンド) でプレイヤーに知らせます。"))
        self.sc_desc = C.help_label("")
        af.addRow(self.sc_desc)
        af.addRow(C.button("自動化の設定を保存", self.save_auto, "primary"))
        self.tabs.addTab(auto, "自動化")

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        s = self.ctx.current()
        self.setEnabled(s is not None)
        if not s:
            return
        self.name.setText(s.name)
        self.maxmem.setValue(s.max_mem_mb)
        self.minmem.setValue(s.min_mem_mb)
        info = pc_info()
        self.mem_help.setText(f"PCのメモリ: {info['total_mb'] // 1024}GB / 空き: {info['available_mb'] // 1024}GB / "
                              f"割り当て上限の目安: {safe_max_memory(info['total_mb']) // 1024}GB")
        self.java_mode.setCurrentIndex(0 if s.java_mode == "auto" else 1)
        self.java_combo.clear()
        for j in jm.list_all() or jm.refresh():
            if j.ok:
                self.java_combo.addItem(f"{j.label}  {privacy.path(j.path)}", j.path)
        if s.java_path:
            i = self.java_combo.findData(s.java_path)
            if i < 0:
                self.java_combo.addItem(s.java_path, s.java_path)
                i = self.java_combo.count() - 1
            self.java_combo.setCurrentIndex(i)
        self.java_combo.setEnabled(s.java_mode == "manual")
        j, req, note = jm.resolve_for_server(s)
        self.java_help.setText(f"必要: {req.describe()}\n現在: {j.label + ' - ' + note if j else note}")
        self.jvm_args.setText(s.jvm_args)
        self.auto_backup.setValue(s.auto_backup_minutes)
        self.backup_keep.setValue(s.backup_keep)
        self.load_props()
        self.load_auto(s)
        mods = len(list(s.mods_dir.glob("*.jar"))) if s.mods_dir.exists() else 0
        rec, reasons = recommend(info["total_mb"], info["cpu_logical"], mods, s.max_players)
        self._rec = rec
        self.rec_label.setText(f"このPC: メモリ{info['total_mb'] // 1024}GB / CPU {info['cpu_logical']}スレッド / MOD {mods}個\n"
                               f"おすすめ: 「{rec.label}」 最大メモリ {rec.max_mem_mb // 1024}GB\n" + "\n".join(reasons))

    def load_auto(self, s):
        from msm.server.schedule import describe

        o = s.options or {}
        self.a_restart.setChecked(bool(o.get("auto_restart", False)))
        self.a_max.setValue(int(o.get("auto_restart_max", 3)))
        self.a_window.setValue(int(o.get("auto_restart_window_min", 10)))
        self.a_autostart.setChecked(bool(o.get("autostart_on_launch", False)))
        self.a_discord.setChecked(bool(o.get("discord", True)))
        sc = o.get("schedule") or {}
        self.sc_enabled.setChecked(bool(sc.get("enabled", False)))
        days = sc.get("days", list(range(7)))
        for i, cb in enumerate(self.sc_days):
            cb.setChecked(i in days)
        self.sc_restart.setText(", ".join(sc.get("restart_times", [])))
        self.sc_start.setText(sc.get("start_time", ""))
        self.sc_stop.setText(sc.get("stop_time", ""))
        self.sc_backup.setText(", ".join(sc.get("backup_times", [])))
        self.sc_warn.setText(", ".join(str(x) for x in sc.get("warn_minutes", [5, 1])))
        self.sc_desc.setText(describe(sc))

    def save_auto(self):
        from msm.server.schedule import describe, parse_times

        s = self.ctx.current()
        if not s:
            return
        bad = []
        for label, w in (("定時再起動", self.sc_restart), ("起動時刻", self.sc_start), ("停止時刻", self.sc_stop),
                         ("定時バックアップ", self.sc_backup)):
            txt = w.text().strip()
            if txt and not parse_times(txt):
                bad.append(label)
        if bad:
            C.warn(self, "時刻は 04:00 のように「時:分」で入力してください: " + "、".join(bad))
            return
        warn = []
        for x in self.sc_warn.text().replace("、", ",").split(","):
            x = x.strip()
            if x.isdigit() and 0 < int(x) <= 120:
                warn.append(int(x))
        sc = {"enabled": self.sc_enabled.isChecked(), "days": [i for i, cb in enumerate(self.sc_days) if cb.isChecked()],
              "restart_times": parse_times(self.sc_restart.text()), "start_time": (parse_times(self.sc_start.text()) or [""])[0],
              "stop_time": (parse_times(self.sc_stop.text()) or [""])[0], "backup_times": parse_times(self.sc_backup.text()),
              "warn_minutes": sorted(set(warn), reverse=True)}
        if sc["enabled"] and not sc["days"]:
            C.warn(self, "曜日を1つ以上選んでください。")
            return
        o = dict(s.options or {})
        o.update({"auto_restart": self.a_restart.isChecked(), "auto_restart_max": self.a_max.value(),
                  "auto_restart_window_min": self.a_window.value(), "autostart_on_launch": self.a_autostart.isChecked(),
                  "discord": self.a_discord.isChecked(), "schedule": sc})
        s.options = o
        instance.save_server(s)
        self.sc_desc.setText(describe(sc))
        C.info(self, "保存しました。\n" + describe(sc))

    def _running(self) -> bool:
        s = self.ctx.current()
        sp = registry.find(s.id) if s else None
        return bool(sp and sp.running)

    def save_basic(self):
        s = self.ctx.current()
        if not s:
            return
        if self.minmem.value() > self.maxmem.value():
            C.warn(self, "最小メモリは最大メモリ以下にしてください。")
            return
        total = pc_info()["total_mb"]
        if self.maxmem.value() > safe_max_memory(total) and not C.confirm(
                self, "最大メモリがPCのメモリに対して大きすぎます。PC全体が不安定になる恐れがあります。保存しますか？"):
            return
        s.name = self.name.text().strip() or s.name
        s.max_mem_mb = self.maxmem.value()
        s.min_mem_mb = self.minmem.value()
        s.java_mode = self.java_mode.currentData()
        s.java_path = (self.java_combo.currentData() or "") if s.java_mode == "manual" else s.java_path
        s.jvm_args = self.jvm_args.text().strip()
        s.auto_backup_minutes = self.auto_backup.value()
        s.backup_keep = self.backup_keep.value()
        instance.save_server(s)
        self.ctx.notify_servers_changed()
        msg = "保存しました。"
        if self._running():
            msg += "\nメモリ・Javaの変更は次回の起動から反映されます。"
        C.info(self, msg)
        self.refresh()

    def load_props(self):
        s = self.ctx.current()
        if not s:
            return
        p = read_properties(s.path / "server.properties")
        for d in PROPERTY_DEFS:
            w = self.widgets[d.key]
            v = p.get(d.key, d.default)
            if isinstance(w, QCheckBox):
                w.setChecked(v.lower() == "true")
            elif isinstance(w, QSpinBox):
                try:
                    w.setValue(int(v))
                except ValueError:
                    w.setValue(int(d.default or 0))
            elif isinstance(w, QComboBox):
                i = w.findData(v)
                w.setCurrentIndex(max(0, i))
            else:
                w.setText(v)
        path = s.path / "server.properties"
        self.raw.setPlainText(path.read_text("utf-8", "replace") if path.exists() else "")

    def _collect(self) -> dict[str, str]:
        out = {}
        for d in PROPERTY_DEFS:
            w = self.widgets[d.key]
            if isinstance(w, QCheckBox):
                out[d.key] = "true" if w.isChecked() else "false"
            elif isinstance(w, QSpinBox):
                out[d.key] = str(w.value())
            elif isinstance(w, QComboBox):
                out[d.key] = w.currentData()
            else:
                out[d.key] = w.text()
        return out

    def save_props(self):
        s = self.ctx.current()
        if not s:
            return
        new = self._collect()
        old = read_properties(s.path / "server.properties")
        changed = {k: v for k, v in new.items() if old.get(k, next(d.default for d in PROPERTY_DEFS if d.key == k)) != v}
        if not changed:
            C.info(self, "変更はありません。")
            return
        warns = danger_warnings(changed)
        if warns and not C.confirm(self, "危険な設定が含まれています:\n\n" + "\n\n".join(warns) + "\n\n保存しますか？", danger=True):
            return
        if "server-port" in changed:
            port = int(changed["server-port"])
            conf = instance.port_conflicts(port, s.id)
            if conf and not C.confirm(self, f"ポート {port} は「{conf[0].name}」と重複しています。同時に起動できませんが保存しますか？"):
                return
            if not ports.is_port_free(port) and not self._running():
                if not C.confirm(self, f"ポート {port} は現在ほかのプログラムが使用中です。保存しますか？"):
                    return
        if "level-name" in changed and not C.confirm(
                self, f"ワールド名を「{changed['level-name']}」に変更すると、そのフォルダーのワールドが使われます。\n"
                      "(存在しなければ新しいワールドが作られます。元のワールドは削除されません)\n変更しますか？"):
            return
        try:
            bk.backup_file(s, ["server.properties"], "server.properties変更前")
        except Exception as e:  # noqa: BLE001
            if not C.confirm(self, f"バックアップに失敗しました: {e}\nバックアップなしで保存しますか？", danger=True):
                return
        write_properties(s.path / "server.properties", changed)
        if "server-port" in changed:
            s.port = int(changed["server-port"])
        if "max-players" in changed:
            s.max_players = int(changed["max-players"])
        instance.save_server(s)
        self.ctx.notify_servers_changed()
        self.load_props()
        if self._running():
            if C.confirm(self, "保存しました。多くの設定は再起動後に反映されます。今すぐ再起動しますか？"):
                from msm.gui import actions

                actions.restart_server(self, self.ctx, s)
        else:
            C.info(self, "保存しました (変更前の内容はバックアップ画面から戻せます)。")

    def save_raw(self):
        s = self.ctx.current()
        if not s:
            return
        if not C.confirm(self, "直接編集した内容で server.properties を上書きします。よろしいですか？\n(変更前の内容はバックアップされます)"):
            return
        text = self.raw.toPlainText()
        if "online-mode=false" in text.replace(" ", "") and read_properties(s.path / "server.properties").get("online-mode", "true") != "false":
            if not C.confirm(self, "online-mode=false はプレイヤーの認証を無効にします。なりすましの危険があります。保存しますか？", danger=True):
                return
        bk.backup_file(s, ["server.properties"], "server.properties直接編集前")
        (s.path / "server.properties").write_text(text, "utf-8")
        p = read_properties(s.path / "server.properties")
        try:
            s.port = int(p.get("server-port", s.port))
            s.max_players = int(p.get("max-players", s.max_players))
        except ValueError:
            pass
        instance.save_server(s)
        self.load_props()
        C.info(self, "保存しました。")

    def apply_preset(self, use_recommended: bool = False):
        s = self.ctx.current()
        if not s:
            return
        if use_recommended:
            p = self._rec
        else:
            it = self.preset_list.currentItem()
            if not it:
                C.info(self, "プリセットを選択してください。")
                return
            key = it.data(256)
            p = next(x for x in PRESETS if x.key == key)
            total = pc_info()["total_mb"]
            if p.max_mem_mb > safe_max_memory(total):
                from msm.server.presets import Preset

                p = Preset(p.key, p.label, p.description, safe_max_memory(total), min(p.min_mem_mb, safe_max_memory(total)), dict(p.props))
        if p is None:
            return
        cur = read_properties(s.path / "server.properties")
        lines = [f"最大メモリ: {s.max_mem_mb}MB → {p.max_mem_mb}MB", f"最小メモリ: {s.min_mem_mb}MB → {p.min_mem_mb}MB"]
        for k, v in p.props.items():
            lines.append(f"{k}: {cur.get(k, '(未設定)')} → {v}")
        if not C.confirm(self, f"プリセット「{p.label}」を適用します。\n\n" + "\n".join(lines) + "\n\n適用しますか？ (変更前にバックアップします)"):
            return
        bk.backup_file(s, ["server.properties"], f"プリセット「{p.label}」適用前")
        write_properties(s.path / "server.properties", p.props)
        s.max_mem_mb, s.min_mem_mb = p.max_mem_mb, p.min_mem_mb
        if "max-players" in p.props:
            s.max_players = int(p.props["max-players"])
        instance.save_server(s)
        self.refresh()
        C.info(self, "適用しました。次回の起動から反映されます。")
