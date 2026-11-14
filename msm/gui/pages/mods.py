"""MOD管理 (一覧・分類・依存関係・追加・無効化/復元・手動ダウンロード補助)."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QPlainTextEdit, QTableWidget, QTableWidgetItem,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from msm.core.db import get_db
from msm.core.util import open_path
from msm.gui import common as C
from msm.mods import classify as cls
from msm.mods import deps as depmod
from msm.mods import disable as dis
from msm.mods import importer
from msm.mods.download_watch import DetectedFile, DownloadWatcher, default_watch_folder
from msm.mods.metadata import scan_dir
from msm.server.process import registry

SIDE_COLORS = {cls.CLIENT: "#c0392b", cls.SERVER: "#2471a3", cls.BOTH: "#1e8449", cls.UNKNOWN: "#7f8c8d"}


class _WatchSignal(QObject):
    detected = Signal(object)


class ModsPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("MOD", "サーバーのmods (Paperはplugins) フォルダーを管理します。MODは削除せず、"
                                              "無効化すると disabled_mods フォルダーへ退避します。"))
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # ---- 一覧 ----
        w = QWidget()
        wl = QVBoxLayout(w)
        self.summary = QLabel()
        wl.addWidget(self.summary)
        self.use_mr = QCheckBox("Modrinthに問い合わせて判定精度を上げる (ファイルのハッシュ値のみ送信)")
        self.use_mr.setChecked(bool(get_db().get_setting("use_modrinth", True)))
        self.use_mr.toggled.connect(lambda v: get_db().set_setting("use_modrinth", v))
        wl.addWidget(self.use_mr)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ファイル", "MOD名", "バージョン", "ローダー", "サーバー対応判定", "確信度", "問題"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(330)
        self.table.doubleClicked.connect(lambda *_: self.details())
        wl.addWidget(self.table, 1)
        wl.addLayout(C.hbox(
            C.menu_button("＋ MODを追加", [("MODファイル (JAR) を選んで追加...", self.add_files),
                                         ("フォルダー内のMODをまとめて追加...", self.add_folder), None,
                                         ("不足している前提MODをModrinthで探して追加...", self.fetch_missing), None,
                                         ("MODパック (ZIP / .mrpack) からこのサーバーへ追加...", self.import_pack),
                                         ("CurseForgeのプロファイルから追加...", lambda: self.ctx.navigate.emit("integrations"))],
                          "primary"),
            C.button("友達用パックを作成...", self.make_client_pack, "", "参加する人がランチャーに読み込める、サーバーと同じMOD構成のパックを作ります"),
            C.button("更新を確認...", self.check_updates),
            C.menu_button("その他", [("詳細を見る", self.details), ("依存関係・グループを見る", self.show_groups),
                                    ("判定不能・要確認MODの扱いを決める...", self.review_unknown), None,
                                    ("再解析 (Modrinthにも問い合わせ)", self.analyze), ("modsフォルダーを開く", self.open_mods)]),
            None,
            C.button("選択したMODを無効化...", self.disable_selected, "danger"), stretch_end=False))
        wl.addWidget(C.help_label("行をダブルクリックすると詳細を表示します。無効化したMODは「無効化・履歴」タブからいつでも戻せます。"))
        self.tabs.addTab(w, "MOD一覧")

        # ---- 無効化中 / 履歴 ----
        h = QWidget()
        hl = QVBoxLayout(h)
        self.hist = QTableWidget(0, 5)
        self.hist.setHorizontalHeaderLabels(["日時", "操作", "ファイル", "理由", "状態"])
        self.hist.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.hist.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.hist.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.hist.verticalHeader().setVisible(False)
        hl.addWidget(self.hist, 1)
        hl.addLayout(C.hbox(C.button("選択したMODを復元", self.restore_selected, "primary"),
                            C.button("disabled_modsを開く", self.open_disabled)))
        self.tabs.addTab(h, "無効化・履歴")

        # ---- 手動ダウンロード補助 ----
        m = QWidget()
        ml = QVBoxLayout(m)
        ml.addWidget(C.help_label(
            "自動取得できなかったMODを、公式配布ページからブラウザでダウンロードして取り込みます。\n"
            "1) 「ページを開く」→ ブラウザでダウンロード  2) ダウンロードフォルダーを監視して自動検出  3) 内容を照合して確認後に取り込み\n"
            "元のダウンロードファイルは変更・削除しません。ファイル名だけでMODを確定せず、ハッシュやMOD情報で照合します。"))
        self.manual = QTableWidget(0, 4)
        self.manual.setHorizontalHeaderLabels(["MOD", "理由", "ダウンロードページ", "状態"])
        self.manual.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.manual.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.manual.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.manual.verticalHeader().setVisible(False)
        ml.addWidget(self.manual, 1)
        self.watch_label = QLabel()
        ml.addWidget(self.watch_label)
        self.b_watch = C.button("監視を開始", self.toggle_watch, "primary")
        ml.addLayout(C.hbox(C.button("ページを開く", self.open_manual_page), self.b_watch,
                            C.button("監視フォルダーを変更...", self.change_watch_dir),
                            C.button("ダウンロード済みから探す", self.scan_existing),
                            C.button("完了した項目を一覧から消す", self.clear_done)))
        self.watch_log = QPlainTextEdit()
        self.watch_log.setReadOnly(True)
        self.watch_log.setMaximumHeight(130)
        ml.addWidget(self.watch_log)
        self.tabs.addTab(m, "手動ダウンロード補助")

        self.metas = []
        self.classes: dict[str, cls.Classification] = {}
        self.report = None
        self.watcher: DownloadWatcher | None = None
        self._wsig = _WatchSignal()
        self._wsig.detected.connect(self._on_detect, Qt.QueuedConnection)
        self._analyzed_for = None

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        s = self.ctx.current()
        self.setEnabled(s is not None)
        if not s:
            return
        if s.server_type == "vanilla":
            self.summary.setText("Vanillaサーバーではプラグイン・MODは使えません。")
        if self._analyzed_for != s.id:
            self.analyze(quick=True)
        self.fill_history()
        self.fill_manual()
        self._update_watch_label()

    def analyze(self, quick: bool = False):
        s = self.ctx.current()
        if not s:
            return
        use_mr = self.use_mr.isChecked() and not quick

        def job(p, l, c):
            p("MODを解析しています...", None)
            metas = scan_dir(s.mods_dir)
            classes = importer.classify_many([m for m in metas if m.valid_jar], use_mr)
            rep = depmod.analyze(metas, s.mc_version, s.server_type)
            return metas, classes, rep

        def done(res):
            self.metas, self.classes, self.report = res
            self._analyzed_for = s.id
            self.fill()

        if quick:
            C.bg().run(lambda: job(lambda *a: None, None, None), lambda r, e: done(r) if r else None)
        else:
            C.run_task(self, "MODを解析しています", job, done)

    def fill(self):
        s = self.ctx.current()
        issues_by_file: dict[str, list[str]] = {}
        if self.report:
            for iss in self.report.issues:
                issues_by_file.setdefault(iss.file, []).append(f"[{iss.level}] {iss.message}")
        self.table.setRowCount(len(self.metas))
        counts = {k: 0 for k in cls.SIDE_LABELS}
        for r, m in enumerate(self.metas):
            c = self.classes.get(m.file) or cls.Classification()
            counts[c.side] += 1
            vals = [m.file_name, m.display_name, m.version, m.loader, c.label, c.confidence,
                    " / ".join(issues_by_file.get(m.file_name, []) + m.errors)]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, m.file)
                if col == 4:
                    it.setForeground(QColor(SIDE_COLORS[c.side]))
                if col == 6 and v:
                    it.setForeground(QColor("#c0392b"))
                self.table.setItem(r, col, it)
        n_iss = len(self.report.issues) if self.report else 0
        self.summary.setText(
            f"{s.mods_dir if s else ''}\nMOD {len(self.metas)}個 - 両対応 {counts[cls.BOTH]} / サーバー専用 {counts[cls.SERVER]} / "
            f"クライアント専用 {counts[cls.CLIENT]} / 判定不能 {counts[cls.UNKNOWN]} / 問題 {n_iss}件")

    def _selected_files(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def details(self):
        files = self._selected_files()
        if not files:
            return
        m = next((x for x in self.metas if x.file == files[0]), None)
        if not m:
            return
        c = self.classes.get(m.file) or cls.Classification()
        lines = [f"ファイル: {m.file_name}", f"MOD ID: {', '.join(m.mod_ids) or '-'}", f"名前: {m.display_name}",
                 f"バージョン: {m.version}", f"ローダー: {m.loader}", f"対応Minecraft: {m.mc_range or '不明'}",
                 f"SHA-1: {m.sha1}", "", f"判定: {c.label} (確信度: {c.confidence})"]
        lines += [f"  ・{r}" for r in c.reasons]
        if c.modrinth:
            lines.append(f"  Modrinth: {c.modrinth.get('page')}")
        lines.append("")
        req = m.required_deps()
        lines.append("必須の前提MOD: " + (", ".join(f"{d.mod_id} {d.version_range}" for d in req) or "なし"))
        if self.report:
            by = [Path(f).name for f in self.report.required_by.get(m.file, set())]
            lines.append("このMODを必要とするMOD: " + (", ".join(by) or "なし"))
        if m.provides:
            lines.append("同梱しているMOD: " + ", ".join(m.provides[:30]))
        if m.errors:
            lines += ["", "エラー:"] + m.errors
        dlg = QDialog(self)
        dlg.setWindowTitle(m.display_name)
        v = QVBoxLayout(dlg)
        t = QPlainTextEdit("\n".join(lines))
        t.setReadOnly(True)
        t.setMinimumSize(640, 420)
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.exec()

    def _check_stopped(self) -> bool:
        s = self.ctx.current()
        sp = registry.find(s.id) if s else None
        if sp and sp.running:
            C.warn(self, "サーバーの稼働中はMODを変更できません。先にサーバーを停止してください。")
            return False
        return True

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "追加するMOD (JAR) を選択", "", "MOD (*.jar)")
        if files:
            self._import_files([Path(f) for f in files])

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "MODフォルダーを選択 (中のJARを取り込みます)")
        if d:
            files = sorted(Path(d).glob("*.jar"))
            if not files:
                C.warn(self, "JARファイルが見つかりませんでした。")
                return
            self._import_files(files)

    def _import_files(self, files: list[Path]):
        s = self.ctx.current()
        if not s or not self._check_stopped():
            return
        use_mr = self.use_mr.isChecked()

        def job(p, l, c):
            p(f"{len(files)}個のMODを確認しています...", None)
            return importer.plan_for_files(files, s.mods_dir, s.server_type, use_mr)

        def done(plan):
            dlg = PlanDialog(self, plan)
            if dlg.exec() != QDialog.Accepted:
                return
            result = importer.apply_plan(s, plan, dlg.included_review(), "手動追加")
            self.analyze()
            C.info(self, "取り込み結果:\n" + ("\n".join(result[:40]) or "追加したMODはありません") +
                   ("\n..." if len(result) > 40 else ""))

        C.run_task(self, "MODを確認しています", job, done)

    def disable_selected(self):
        s = self.ctx.current()
        files = self._selected_files()
        if not s or not files or not self._check_stopped():
            return
        names = [Path(f).name for f in files]
        dependents = set()
        if self.report:
            for f in files:
                dependents |= {Path(x).name for x in self.report.required_by.get(f, set())} - set(names)
        msg = "次のMODを無効化します (disabled_mods へ移動。削除はしません):\n" + "\n".join(names)
        if dependents:
            msg += "\n\n⚠ 次のMODがこれらを前提にしています。一緒に無効化しないと起動できない可能性があります:\n" + "\n".join(sorted(dependents))
        reason, ok = QInputDialog.getText(self, "MODの無効化", msg + "\n\n無効化の理由 (記録用):", QLineEdit.Normal, "手動で無効化")
        if not ok:
            return
        dis.disable(s, [Path(f) for f in files], reason or "手動で無効化")
        self.analyze()
        self.fill_history()

    def review_unknown(self):
        """判定不能・クライアント専用の可能性があるMODを一覧し、扱いを選ばせる."""
        s = self.ctx.current()
        if not s:
            return
        targets = [m for m in self.metas if (self.classes.get(m.file) or cls.Classification()).side in (cls.UNKNOWN, cls.CLIENT)]
        if not targets:
            C.info(self, "判定不能・クライアント専用と判定されたMODはありません。")
            return
        dlg = ReviewDialog(self, targets, self.classes)
        if dlg.exec() != QDialog.Accepted:
            return
        excl = dlg.excluded()
        if excl:
            if not self._check_stopped():
                return
            dis.disable(s, [Path(f) for f in excl], "サーバー対応確認で一時的に除外")
            self.analyze()
            self.fill_history()
            C.info(self, f"{len(excl)}個のMODを一時的に除外しました (無効化・履歴タブから戻せます)。")

    def show_groups(self):
        if not self.report:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("依存関係・グループ")
        v = QVBoxLayout(dlg)
        v.addWidget(C.help_label("必須の前提MODでつながっているMODを1つのグループにまとめています。切り分け時はグループ単位で扱います。"))
        tree = QTreeWidget()
        tree.setHeaderLabels(["グループ / MOD", "前提MOD"])
        meta_by = {m.file: m for m in self.metas}
        edges = depmod.dependency_edges(self.metas)
        for i, g in enumerate(self.report.groups, 1):
            if len(g) == 1:
                continue
            top = QTreeWidgetItem([f"グループ {i} ({len(g)}個)", ""])
            for f in g:
                m = meta_by.get(f)
                need = ", ".join(Path(x).name for x in edges.get(f, ()))
                top.addChild(QTreeWidgetItem([m.display_name if m else Path(f).name, need]))
            tree.addTopLevelItem(top)
        singles = [g[0] for g in self.report.groups if len(g) == 1]
        top = QTreeWidgetItem([f"単独のMOD ({len(singles)}個)", ""])
        for f in singles:
            m = meta_by.get(f)
            top.addChild(QTreeWidgetItem([m.display_name if m else Path(f).name, ""]))
        tree.addTopLevelItem(top)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        tree.setMinimumSize(760, 480)
        v.addWidget(tree)
        miss = [i for i in self.report.issues if i.kind == "missing"]
        if miss:
            v.addWidget(QLabel("不足している前提MOD:\n" + "\n".join(f"・{i.message}" for i in miss[:20])))
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.exec()

    def open_mods(self):
        s = self.ctx.current()
        if s:
            s.mods_dir.mkdir(parents=True, exist_ok=True)
            open_path(s.mods_dir)

    def check_updates(self):
        s = self.ctx.current()
        if not s:
            return
        from msm.mods import updates

        def job(p, l, c):
            p("Modrinthで最新版を確認しています...", None)
            return updates.check(s)

        def done(r):
            ups, known, total = r
            if not ups:
                C.info(self, f"更新できるMODはありません。\n(Modrinthで確認できたMOD: {known} / {total}個。"
                             "Modrinth以外で配布されているMODは確認できません)")
                return
            dlg = UpdateDialog(self, ups, known, total)
            if dlg.exec() != QDialog.Accepted:
                return
            sel = dlg.selected()
            if not sel or not self._check_stopped():
                return

            def job2(p, l, c):
                from msm.backup import backup as bk

                p("更新前のバックアップを作成しています...", None)
                bk.create_backup(s, ["mods"], "auto-change", f"MOD更新前 ({len(sel)}個)")
                return updates.apply(s, sel, p, c)

            def done2(res):
                self.analyze()
                self.fill_history()
                C.info(self, "更新しました (古いファイルは disabled_mods に退避しています):\n" + "\n".join(res))

            C.run_task(self, "MODを更新しています", job2, done2)

        C.run_task(self, "MODの更新を確認しています", job, done)

    def import_pack(self):
        self.ctx.navigate.emit("import")
        page = getattr(self.window(), "pages", {}).get("import")
        if page:
            page.r_exist.setChecked(True)
            page.browse()

    def fetch_missing(self):
        s = self.ctx.current()
        if not s or not self.report:
            return
        ids = [i.dep_id for i in self.report.issues if i.kind == "missing" and i.dep_id]
        from msm.gui.fetch_mods import fetch_missing

        fetch_missing(self, s, ids, on_done=self.analyze)

    def make_client_pack(self):
        s = self.ctx.current()
        if not s:
            return
        if not s.is_modded:
            C.info(self, "このサーバーはMODサーバーではないため、参加する側にMODは不要です。\n同じバージョンのMinecraftでそのまま参加できます。")
            return
        dlg = ClientPackDialog(self, s)
        if dlg.exec() != QDialog.Accepted:
            return
        fmt, out, include_config, bundle = dlg.values()
        from msm.mods import clientpack

        addr = self.ctx.public_address(s)

        def job(p, l, c):
            p("MODの配布元を確認しています (Modrinth)...", None)
            entries = clientpack.collect(s, True)
            return clientpack.build(s, Path(out), fmt, entries, include_config, bundle, addr, progress=p)

        def done(res):
            if C.confirm(self, res.summary() + "\n\n出力先のフォルダーを開きますか？"):
                open_path(Path(res.path))

        C.run_task(self, "友達用パックを作成しています", job, done)

    # ---- 履歴 ----
    def fill_history(self):
        s = self.ctx.current()
        if not s:
            return
        ev = dis.history(s)
        self._events = ev
        self.hist.setRowCount(len(ev))
        labels = {"disable": "無効化", "restore": "復元", "import": "取り込み", "exclude": "サーバー対象外"}
        for r, e in enumerate(ev):
            state = ""
            if e.action == "disable":
                state = "復元済み" if e.restored else ("退避中" if Path(e.to_path).exists() else "退避先にファイルなし")
            vals = [time.strftime("%Y-%m-%d %H:%M", time.localtime(e.created_at)), labels.get(e.action, e.action),
                    e.file_name, e.reason, state]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.UserRole, r)
                self.hist.setItem(r, c, it)

    def restore_selected(self):
        s = self.ctx.current()
        rows = self.hist.selectionModel().selectedRows()
        if not s or not rows or not self._check_stopped():
            return
        done, errs = [], []
        for row in rows:
            e = self._events[self.hist.item(row.row(), 0).data(Qt.UserRole)]
            if e.action != "disable" or e.restored:
                continue
            try:
                dis.restore(s, e)
                done.append(e.file_name)
            except Exception as ex:  # noqa: BLE001
                errs.append(f"{e.file_name}: {ex}")
        self.fill_history()
        self.analyze(quick=True)
        C.info(self, ("復元しました:\n" + "\n".join(done) if done else "復元対象がありません。") +
               ("\n\nエラー:\n" + "\n".join(errs) if errs else ""))

    def open_disabled(self):
        s = self.ctx.current()
        if s:
            open_path(dis.disabled_dir(s))

    # ---- 手動ダウンロード ----
    def fill_manual(self):
        s = self.ctx.current()
        if not s:
            return
        items = importer.load_pending_manual(s.id)
        self._manual = items
        self.manual.setRowCount(len(items))
        for r, it in enumerate(items):
            vals = [it.name + (f" ({it.file_name})" if it.file_name else ""), it.reason, it.page, "取り込み済み" if it.done else "未取得"]
            for c, v in enumerate(vals):
                self.manual.setItem(r, c, QTableWidgetItem(v))

    def open_manual_page(self):
        rows = self.manual.selectionModel().selectedRows()
        if not rows:
            C.info(self, "MODを選択してください。")
            return
        it = self._manual[rows[0].row()]
        if not it.page:
            C.warn(self, "ダウンロードページが分かりません。MOD名で検索してください。")
            return
        C.open_url(self, it.page)
        if not (self.watcher and self.watcher.running):
            if C.confirm(self, "ダウンロードフォルダーの監視を開始しますか？\n(ダウンロードが完了すると自動で検出します)"):
                self.toggle_watch()

    def _update_watch_label(self):
        folder = default_watch_folder()
        running = bool(self.watcher and self.watcher.running)
        self.watch_label.setText(f"監視フォルダー: {folder}   状態: {'監視中' if running else '停止中'}")
        self.b_watch.setText("監視を停止" if running else "監視を開始")

    def toggle_watch(self):
        s = self.ctx.current()
        if not s:
            return
        if self.watcher and self.watcher.running:
            self.watcher.stop()
            self.watcher = None
        else:
            sid = s.id
            self.watcher = DownloadWatcher(default_watch_folder(), lambda: importer.load_pending_manual(sid),
                                           lambda d: self._wsig.detected.emit(d))
            self.watcher.start()
            self.watch_log.appendPlainText(f"{time.strftime('%H:%M:%S')} 監視開始: {self.watcher.folder}")
        self._update_watch_label()

    def change_watch_dir(self):
        d = QFileDialog.getExistingDirectory(self, "ブラウザのダウンロード先フォルダーを選択", str(default_watch_folder()))
        if d:
            get_db().set_setting("download_watch_dir", d)
            if self.watcher and self.watcher.running:
                self.toggle_watch()
                self.toggle_watch()
            self._update_watch_label()

    def scan_existing(self):
        s = self.ctx.current()
        if not s:
            return
        w = DownloadWatcher(default_watch_folder(), lambda: importer.load_pending_manual(s.id), lambda d: None)
        found = w.scan_existing(since=time.time() - 14 * 86400)
        if not found:
            C.info(self, "取り込み待ちのMODと一致するファイルは見つかりませんでした (直近14日)。")
            return
        for d in found:
            self._on_detect(d)

    def clear_done(self):
        s = self.ctx.current()
        if s:
            importer.set_pending_manual(s.id, [m for m in importer.load_pending_manual(s.id) if not m.done])
            self.fill_manual()

    def _on_detect(self, d: DetectedFile):
        s = self.ctx.current()
        self.watch_log.appendPlainText(f"{time.strftime('%H:%M:%S')} 検出: {Path(d.path).name} → {d.confidence} ({d.reason})")
        if not s:
            return
        m = d.meta
        if d.match is None or d.confidence == "不一致":
            if not C.confirm(self, f"ダウンロードフォルダーに新しいMODがあります:\n{Path(d.path).name}\n\n"
                                   f"照合結果: {d.reason}\nMOD名: {m.display_name} / ID: {', '.join(m.mod_ids)} / 対応: {m.mc_range or '不明'}\n\n"
                                   "取り込み待ちのMODとは一致しませんでした。それでもサーバーに追加しますか？"):
                return
        else:
            if not C.confirm(self, f"「{d.match.name}」のダウンロードを検出しました。\n\nファイル: {Path(d.path).name}\n"
                                   f"照合: {d.confidence} - {d.reason}\nMOD: {m.display_name} {m.version} (ローダー: {m.loader}, 対応: {m.mc_range or '不明'})\n\n"
                                   "サーバーのmodsフォルダーへコピーしますか？ (元のファイルはそのまま残します)"):
                return
        if not self._check_stopped():
            return
        plan = importer.plan_for_files([Path(d.path)], s.mods_dir, s.server_type, self.use_mr.isChecked())
        it = plan[0]
        if it.action in ("invalid", "wrong_loader", "conflict"):
            C.warn(self, f"取り込めません: {it.note}")
            return
        if it.action == "skip_same":
            C.info(self, f"既に導入済みです: {it.note}")
        elif it.action in ("exclude_client", "review_client"):
            if not C.confirm(self, f"このMODは{it.note}。\nそれでもサーバーに追加しますか？"):
                importer.apply_plan(s, plan, set(), "手動ダウンロード")
            else:
                importer.apply_plan(s, plan, {it.source}, "手動ダウンロード")
        else:
            importer.apply_plan(s, plan, set(), "手動ダウンロード")
        if d.match:
            items = importer.load_pending_manual(s.id)
            for x in items:
                if (x.project_id, x.file_id, x.file_name, x.name) == (d.match.project_id, d.match.file_id, d.match.file_name, d.match.name):
                    x.done = True
            importer.set_pending_manual(s.id, items)
        self.watch_log.appendPlainText(f"  → 取り込みました: {Path(d.path).name}")
        self.fill_manual()
        self.analyze(quick=True)


class PlanDialog(QDialog):
    """取り込み計画の確認."""

    LABELS = {"add": "追加", "skip_same": "導入済み (スキップ)", "conflict": "同名の別ファイルあり (スキップ)",
              "exclude_client": "クライアント専用 (サーバーに入れない)", "review_client": "クライアント専用の可能性 (要確認)",
              "invalid": "読み込めない (スキップ)", "wrong_loader": "ローダー違い (スキップ)"}

    def __init__(self, parent, plan: list[importer.PlanItem]):
        super().__init__(parent)
        self.setWindowTitle("取り込み内容の確認")
        self.plan = plan
        v = QVBoxLayout(self)
        v.addWidget(C.help_label("「要確認」のMODは、チェックを入れたものだけサーバーに追加します。既存のMODは上書きしません。"))
        self.table = QTableWidget(len(plan), 4)
        self.table.setHorizontalHeaderLabels(["追加", "ファイル", "扱い", "理由"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        for r, it in enumerate(plan):
            chk = QTableWidgetItem()
            if it.action == "review_client":
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Unchecked)
            else:
                chk.setFlags(Qt.ItemIsEnabled)
                chk.setText("○" if it.action == "add" else "-")
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(it.file_name))
            self.table.setItem(r, 2, QTableWidgetItem(self.LABELS.get(it.action, it.action)))
            reasons = it.note
            if it.classification and it.classification.reasons:
                reasons += " / " + "; ".join(it.classification.reasons[:2])
            self.table.setItem(r, 3, QTableWidgetItem(reasons))
        self.table.setMinimumSize(900, 420)
        v.addWidget(self.table)
        n_add = sum(1 for p in plan if p.action == "add")
        v.addWidget(QLabel(f"追加: {n_add}個 / 要確認: {sum(1 for p in plan if p.action == 'review_client')}個 / "
                           f"その他: {len(plan) - n_add}個"))
        bb = QDialogButtonBox()
        bb.addButton("取り込む", QDialogButtonBox.AcceptRole)
        bb.addButton("キャンセル", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def included_review(self) -> set[str]:
        out = set()
        for r, it in enumerate(self.plan):
            if it.action == "review_client" and self.table.item(r, 0).checkState() == Qt.Checked:
                out.add(it.source)
        return out


class ReviewDialog(QDialog):
    """判定不能MODの扱い (サーバーに導入する / 一時的に除外する / 詳細を確認する)."""

    def __init__(self, parent, metas, classes):
        super().__init__(parent)
        self.setWindowTitle("サーバー対応の確認")
        self.metas = metas
        v = QVBoxLayout(self)
        v.addWidget(C.help_label("判定不能・クライアント専用の可能性があるMODです。勝手に削除はしません。\n"
                                 "「一時的に除外」にチェックしたMODだけを disabled_mods へ退避します (いつでも戻せます)。"))
        self.table = QTableWidget(len(metas), 4)
        self.table.setHorizontalHeaderLabels(["一時的に除外", "MOD", "判定", "根拠"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        for r, m in enumerate(metas):
            c = classes.get(m.file) or cls.Classification()
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(f"{m.display_name} ({m.file_name})"))
            self.table.setItem(r, 2, QTableWidgetItem(f"{c.label} / {c.confidence}"))
            self.table.setItem(r, 3, QTableWidgetItem("; ".join(c.reasons)))
        self.table.setMinimumSize(900, 400)
        v.addWidget(self.table)
        bb = QDialogButtonBox()
        bb.addButton("決定 (チェックなしはサーバーに導入したまま)", QDialogButtonBox.AcceptRole)
        bb.addButton("キャンセル", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def excluded(self) -> list[str]:
        return [self.metas[r].file for r in range(self.table.rowCount()) if self.table.item(r, 0).checkState() == Qt.Checked]


class ClientPackDialog(QDialog):
    """友達用クライアントパックの設定."""

    def __init__(self, parent, server):
        super().__init__(parent)
        from PySide6.QtWidgets import QButtonGroup, QRadioButton

        from msm.core.paths import safe_name
        from msm.mods.clientpack import FORMATS

        self.setWindowTitle("友達用パックの作成")
        self.server = server
        v = QVBoxLayout(self)
        v.addWidget(C.help_label(
            "サーバーと同じMOD構成のパックを作ります。参加する人はランチャーで読み込むだけで同じ環境になります。\n"
            "サーバーから除外したクライアント専用MOD (軽量化・ミニマップなど) も含めます。サーバー専用MODは含めません。"))
        self.group = QButtonGroup(self)
        self.radios = {}
        for k, label in FORMATS.items():
            r = QRadioButton(label)
            self.radios[k] = r
            self.group.addButton(r)
            v.addWidget(r)
            r.toggled.connect(self._fmt_changed)
        self.radios["mrpack"].setChecked(True)
        self.config = QCheckBox("configフォルダーも含める (MODの設定をサーバーと揃える)")
        self.config.setChecked(True)
        v.addWidget(self.config)
        self.bundle = QCheckBox("Modrinthに無いMODをパックに同梱する (再配布が禁止されたMODもあるため、友達への個人的な共有に限ってください)")
        v.addWidget(self.bundle)
        self.out = QLineEdit()
        default = Path.home() / "Desktop" / f"{safe_name(server.name)}-client.mrpack"
        self.out.setText(str(default))
        v.addLayout(C.hbox(QLabel("保存先:"), self.out, C.button("参照...", self._browse), stretch_end=False))
        bb = QDialogButtonBox()
        bb.addButton("作成", QDialogButtonBox.AcceptRole)
        bb.addButton("キャンセル", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.setMinimumWidth(640)

    def _fmt(self) -> str:
        return next(k for k, r in self.radios.items() if r.isChecked())

    def _fmt_changed(self, *_):
        fmt = self._fmt()
        ext = ".mrpack" if fmt == "mrpack" else ".zip"
        p = Path(self.out.text() or "pack")
        name = p.name.replace("-client.mrpack", "").replace("-client.zip", "").replace("-client-cf.zip", "")
        name = Path(name).stem if name.endswith((".mrpack", ".zip")) else name
        suffix = "-client-cf.zip" if fmt == "curseforge" else f"-client{ext}"
        self.out.setText(str(p.with_name(name + suffix)))
        self.bundle.setEnabled(fmt == "mrpack")
        if fmt != "mrpack":
            self.bundle.setChecked(True)

    def _browse(self):
        f, _ = QFileDialog.getSaveFileName(self, "保存先", self.out.text())
        if f:
            self.out.setText(f)

    def values(self):
        return self._fmt(), self.out.text().strip(), self.config.isChecked(), self.bundle.isChecked()


class UpdateDialog(QDialog):
    def __init__(self, parent, ups, known, total):
        super().__init__(parent)
        self.setWindowTitle("MODの更新")
        self.ups = ups
        v = QVBoxLayout(self)
        v.addWidget(C.help_label(
            f"Modrinthで確認できたMOD {known} / {total}個のうち、{len(ups)}個に新しい版があります (同じMinecraftバージョン・ローダー向け)。\n"
            "更新するとMOD同士の互換性が変わることがあります。更新前にMODフォルダーを自動でバックアップし、"
            "古いファイルは disabled_mods に退避します (いつでも戻せます)。"))
        self.table = QTableWidget(len(ups), 5)
        self.table.setHorizontalHeaderLabels(["更新", "MOD", "現在", "最新", "種類 / 公開日"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        for r, u in enumerate(ups):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if u.version_type == "release" else Qt.Unchecked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(u.title))
            self.table.setItem(r, 2, QTableWidgetItem(u.current))
            self.table.setItem(r, 3, QTableWidgetItem(u.latest))
            self.table.setItem(r, 4, QTableWidgetItem(f"{u.version_type} / {u.published}"))
        self.table.setMinimumSize(860, 360)
        v.addWidget(self.table)
        v.addLayout(C.hbox(C.button("選択したMODの変更点を見る", self.open_page)))
        bb = QDialogButtonBox()
        bb.addButton("チェックしたMODを更新", QDialogButtonBox.AcceptRole)
        bb.addButton("キャンセル", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def open_page(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.ups):
            C.open_url(self, self.ups[r].page)

    def selected(self):
        return [u for r, u in enumerate(self.ups) if self.table.item(r, 0).checkState() == Qt.Checked]
