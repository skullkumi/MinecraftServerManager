"""サーバー一覧 (複数サーバーの管理・同時起動・一括操作)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHeaderView, QLabel, QLineEdit, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from msm.core.util import open_path
from msm.gui import actions
from msm.gui import common as C
from msm.server import instance, providers
from msm.server.process import STATE_LABELS, registry


COLS = ["", "サーバー", "状態", "参加", "メモリ", "CPU", "Minecraft", "種類", "ポート", "招待方法"]


class ServersPage(C.Page):
    """すべてのサーバーをまとめて管理する (同時起動・一括操作・メモリとポートの確認)."""

    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("サーバー一覧", "チェックを入れたサーバーをまとめて起動・停止できます。行をダブルクリックするとそのサーバーを開きます。"))
        self.summary = QLabel()
        self.summary.setObjectName("caption")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in range(2, len(COLS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 34)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setMinimumHeight(260)
        self.table.itemSelectionChanged.connect(self._sel)
        self.table.doubleClicked.connect(lambda *_: (self._sel(), ctx.navigate.emit("dashboard")))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemChanged.connect(lambda *_: self._update_buttons())
        lay.addWidget(self.table, 1)
        self.b_start = C.button("▶ 起動", self.start_checked, "primary")
        self.b_stop = C.button("■ 停止", self.stop_checked)
        self.b_restart = C.button("↻ 再起動", self.restart_checked)
        self.sel_label = QLabel()
        self.sel_label.setObjectName("caption")
        lay.addLayout(C.hbox(C.button("すべて選択", lambda: self._check_all(True)), C.button("選択解除", lambda: self._check_all(False)),
                             self.sel_label, None, self.b_start, self.b_stop, self.b_restart, stretch_end=False))
        lay.addLayout(C.hbox(C.button("＋ 新しいサーバー", lambda: ctx.navigate.emit("create")),
                             C.button("📦 MODパックから作る", lambda: ctx.navigate.emit("import")),
                             C.button("既存のサーバーフォルダーを登録...", self.register), None,
                             C.button("稼働中をすべて停止", lambda: actions.stop_all(self, ctx)),
                             C.button("稼働中をすべて再起動", lambda: actions.restart_all(self, ctx)), stretch_end=False))
        self.port_warn = QLabel("")
        self.port_warn.setObjectName("notice")
        self.port_warn.setWordWrap(True)
        lay.addWidget(self.port_warn)
        lay.addWidget(C.help_label("右クリックで、そのサーバーのコンソール・設定・MOD・フォルダーを開いたり、登録を解除したりできます。"
                                   "登録を解除してもサーバーフォルダーやワールドは削除されません。"))
        self._checked: set[str] = set()
        self._ids: list[str] = []
        from PySide6.QtCore import QTimer

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start(3000)
        ctx.state_changed.connect(lambda *_: self.refresh() if self.isVisible() else None, Qt.QueuedConnection)
        ctx.servers_changed.connect(self.refresh)

    # ---- 表示 ----
    def refresh(self) -> None:
        from msm.publish.tunnels import METHOD_INFO
        from msm.server.presets import pc_info, safe_max_memory

        servers = self.ctx.servers()
        cur = self.ctx.current()
        ids = [x.id for x in servers]
        self.table.blockSignals(True)
        if ids != self._ids:
            self.table.setRowCount(len(servers))
            self._ids = ids
        ports: dict[int, list[str]] = {}
        run_n = 0
        rss_total = 0
        alloc_running = 0
        for r, s in enumerate(servers):
            st = self.ctx.state_of(s.id)
            sp = registry.find(s.id)
            running = bool(sp and sp.running)
            players = mem = cpu = "-"
            if running:
                run_n += 1
                alloc_running += s.max_mem_mb
                stt = sp.stats()
                if stt.get("rss"):
                    rss_total += stt["rss"]
                    mem = f"{stt['rss'] / 1024 ** 3:.1f} / {s.max_mem_mb / 1024:.0f}GB"
                if stt.get("cpu") is not None:
                    cpu = f"{stt['cpu']:.0f}%"
                players = f"{len(sp.players)}/{sp.max_players}"
            chk = self.table.item(r, 0)
            if chk is None:
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                self.table.setItem(r, 0, chk)
            chk.setData(Qt.UserRole, s.id)
            chk.setCheckState(Qt.Checked if s.id in self._checked else Qt.Unchecked)
            method = METHOD_INFO.get(s.publish_method, {}).get("label", s.publish_method).split(" (")[0]
            vals = [s.name, "● " + STATE_LABELS.get(st, st), players, mem, cpu, s.mc_version,
                    f"{s.type_label} {s.loader_version}".strip(), str(s.port), method]
            for c, v in enumerate(vals, start=1):
                it = self.table.item(r, c)
                if it is None:
                    it = QTableWidgetItem()
                    self.table.setItem(r, c, it)
                it.setText(v)
                it.setData(Qt.UserRole, s.id)
                if c == 2:
                    it.setForeground(QColor(C.state_color(st)))
                f = it.font()
                f.setBold(c == 1 and cur is not None and s.id == cur.id)
                it.setFont(f)
            ports.setdefault(s.port, []).append(s.name)
            if cur and s.id == cur.id and not self.table.selectionModel().selectedRows():
                self.table.selectRow(r)
        self.table.blockSignals(False)
        total = pc_info()["total_mb"]
        safe = safe_max_memory(total)
        self.summary.setText(f"登録 {len(servers)}台 ・ 稼働中 {run_n}台 ・ 使用メモリ合計 {rss_total / 1024 ** 3:.1f}GB "
                             f"(割り当て合計 {alloc_running / 1024:.1f}GB / 目安の上限 {safe / 1024:.0f}GB)")
        dup = [f"ポート {p}: {', '.join(n)}" for p, n in ports.items() if len(n) > 1]
        self.port_warn.setText(("⚠ ポート番号が重複しています (同時に起動できません): " + " / ".join(dup)) if dup else "")
        self.port_warn.setVisible(bool(dup))
        self._update_buttons()

    def _checked_servers(self) -> list:
        out = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.checkState() == Qt.Checked:
                s = instance.get_server(it.data(Qt.UserRole))
                if s:
                    out.append(s)
        self._checked = {s.id for s in out}
        return out

    def _targets(self) -> list:
        """チェックしたサーバー。チェックが無ければ選択中の行."""
        t = self._checked_servers()
        if not t:
            s = self._selected()
            t = [s] if s else []
        return t

    def _update_buttons(self) -> None:
        n = len(self._checked_servers())
        self.sel_label.setText(f"{n}台を選択中" if n else "チェックが無いときは、選んでいる行のサーバーが対象です")

    def _check_all(self, on: bool) -> None:
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._update_buttons()

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return instance.get_server(self.table.item(rows[0].row(), 1).data(Qt.UserRole))

    def _sel(self):
        s = self._selected()
        if s:
            self.ctx.set_current(s.id)

    # ---- 一括操作 ----
    def start_checked(self):
        from msm.server.presets import pc_info, safe_max_memory

        targets = [s for s in self._targets() if not (registry.find(s.id) and registry.find(s.id).running)]
        if not targets:
            C.info(self, "起動するサーバーを選んでください (すでに稼働中のサーバーは除きます)。")
            return
        used_ports = {sp.server.port: sp.server.name for sp in registry.running()}
        ok, skipped = [], []
        for s in targets:
            if s.port in used_ports:
                skipped.append(f"{s.name} (ポート {s.port} を「{used_ports[s.port]}」が使用)")
                continue
            used_ports[s.port] = s.name
            ok.append(s)
        alloc = sum(sp.server.max_mem_mb for sp in registry.running()) + sum(s.max_mem_mb for s in ok)
        safe = safe_max_memory(pc_info()["total_mb"])
        if alloc > safe and not C.confirm(
                self, f"同時に動かすサーバーのメモリ割り当て合計が {alloc / 1024:.1f}GB になり、"
                      f"このPCの目安 ({safe / 1024:.0f}GB) を超えます。PCが重くなる恐れがあります。\n\n起動しますか？"):
            return
        if skipped:
            C.warn(self, "ポート番号が重なるため、次のサーバーは起動しません:\n" + "\n".join(skipped) +
                   "\n\nサーバー設定でポート番号を変えると同時に動かせます。")
        for s in ok:
            actions.start_server(self, self.ctx, s)
        self.refresh()

    def stop_checked(self):
        targets = [s for s in self._targets() if registry.find(s.id) and registry.find(s.id).running]
        if not targets:
            C.info(self, "稼働中のサーバーが選ばれていません。")
            return
        if len(targets) > 1 and not C.confirm(self, f"{len(targets)}台のサーバーを停止しますか？"):
            return
        self._stop_many(targets)

    def _stop_many(self, targets, then=None):
        import threading

        def job(p, l, c):
            p(f"{len(targets)}台のサーバーを安全に停止しています...", None)
            res = {}
            ths = []
            for s in targets:
                sp = registry.find(s.id)

                def one(x=sp, name=s.name):
                    res[name] = x.stop(90)
                t = threading.Thread(target=one)
                t.start()
                ths.append(t)
            for t in ths:
                t.join()
            return res

        def done(res):
            failed = [n for n, ok in res.items() if not ok]
            if failed:
                C.warn(self, "次のサーバーは停止しませんでした:\n" + "\n".join(failed) + "\n右クリックの「強制終了」を使ってください。")
            if then:
                then()
            self.refresh()

        C.run_task(self, "サーバーを停止しています", job, done, cancellable=False)

    def restart_checked(self):
        targets = [s for s in self._targets() if registry.find(s.id) and registry.find(s.id).running]
        if not targets:
            C.info(self, "稼働中のサーバーが選ばれていません。")
            return
        self._stop_many(targets, then=lambda: [actions.start_server(self, self.ctx, instance.get_server(s.id) or s)
                                               for s in targets])

    # ---- 右クリック ----
    def _menu(self, pos):
        from PySide6.QtWidgets import QMenu

        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        s = self._selected()
        if not s:
            return
        sp = registry.find(s.id)
        running = bool(sp and sp.running)
        m = QMenu(self)
        m.addAction("■ 停止" if running else "▶ 起動", lambda: (actions.stop_server if running else actions.start_server)(self, self.ctx, s))
        if running:
            m.addAction("↻ 再起動", lambda: actions.restart_server(self, self.ctx, s))
        m.addSeparator()
        for key, label in (("dashboard", "ホームを開く"), ("detail", "コンソールを開く"), ("settings", "サーバー設定を開く"),
                           ("mods", "MOD・プラグインを開く"), ("publish", "友達を招待")):
            if key == "mods" and s.server_type == "vanilla":
                continue
            m.addAction(label, lambda k=key: (self.ctx.set_current(s.id), self.ctx.navigate.emit(k)))
        m.addAction("フォルダーを開く", lambda: open_path(s.path))
        m.addSeparator()
        if running:
            m.addAction("強制終了...", self.kill)
        m.addAction("登録を解除...", self.unregister)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def kill(self):
        s = self._selected()
        sp = registry.find(s.id) if s else None
        if not sp or not sp.running:
            return
        if C.confirm(self, "強制終了するとワールドが保存されず、破損する恐れがあります。\n通常は「停止」を使ってください。\n\n強制終了しますか？", danger=True):
            sp.kill()

    def open_folder(self):
        s = self._selected()
        if s:
            open_path(s.path)

    def unregister(self):
        s = self._selected()
        if not s:
            return
        if registry.find(s.id) and registry.find(s.id).running:
            C.warn(self, "稼働中のサーバーは登録解除できません。先に停止してください。")
            return
        if C.confirm(self, f"「{s.name}」をアプリの一覧から外しますか？\n\nサーバーフォルダーは削除されません。\n"
                           "あとで「既存のサーバーフォルダーを登録」から再登録できます。"):
            instance.unregister_server(s.id)
            self._checked.discard(s.id)
            self.ctx.notify_servers_changed()

    def register(self):
        d = QFileDialog.getExistingDirectory(self, "既存のサーバーフォルダーを選択")
        if not d:
            return
        guess = instance.guess_existing(Path(d))
        dlg = RegisterDialog(self, d, guess)
        if dlg.exec() != QDialog.Accepted:
            return
        st = dlg.type.currentData()
        launch = guess.get("launch") or providers.detect_launch(Path(d), st)
        s = instance.register_existing(dlg.name.text().strip() or Path(d).name, d, dlg.mc.text().strip(), st,
                                       dlg.loader.text().strip(), launch, dlg.mem.value())
        self.ctx.notify_servers_changed()
        self.ctx.set_current(s.id)
        C.info(self, "登録しました。\n\n※ 元のファイルは変更していません。")


class RegisterDialog(QDialog):
    def __init__(self, parent, d: str, guess: dict):
        super().__init__(parent)
        self.setWindowTitle("既存サーバーの登録")
        f = QFormLayout(self)
        self.name = QLineEdit(guess.get("name") or Path(d).name)
        self.type = QComboBox()
        for k, p in providers.PROVIDERS.items():
            self.type.addItem(p.label, k)
        self.type.setCurrentIndex(max(0, self.type.findData(guess.get("server_type", "vanilla"))))
        self.mc = QLineEdit(guess.get("mc_version", ""))
        self.loader = QLineEdit(guess.get("loader_version", ""))
        self.mem = QSpinBox()
        self.mem.setRange(512, 65536)
        self.mem.setSingleStep(512)
        self.mem.setValue(int(guess.get("max_mem_mb", 4096)))
        self.mem.setSuffix(" MB")
        f.addRow("フォルダー", C.help_label(d))
        f.addRow("サーバー名", self.name)
        f.addRow("種類 (推定)", self.type)
        f.addRow("Minecraftバージョン", self.mc)
        f.addRow("ローダーバージョン", self.loader)
        f.addRow("最大メモリ", self.mem)
        f.addRow(C.help_label("自動推定が間違っている場合は修正してください。Javaの自動選択にMinecraftバージョンを使います。"))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _ok(self):
        if not self.mc.text().strip():
            C.warn(self, "Minecraftバージョンを入力してください。")
            return
        self.accept()
