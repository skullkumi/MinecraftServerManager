"""MOD診断 (ログ解析・推奨対応・自動切り分け)."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QGroupBox, QListWidget, QListWidgetItem, QPlainTextEdit, QSpinBox, QSplitter,
                               QVBoxLayout)

from msm.core.util import open_path
from msm.diagnose.analyzer import Finding, diagnose_server, report_text
from msm.diagnose.bisect import Bisector, bisect_report
from msm.gui import actions
from msm.gui import common as C
from msm.java import manager as jm
from msm.mods import disable as dis
from msm.mods.metadata import scan_dir
from msm.server.process import registry

_noticed: set[tuple[str, float]] = set()


def auto_diagnose_notice(parent, ctx, sid: str) -> None:
    """異常終了時に診断を案内する."""
    sp = registry.find(sid)
    key = (sid, sp.started_at if sp else 0)
    if key in _noticed:
        return
    _noticed.add(key)
    s = next((x for x in ctx.servers() if x.id == sid), None)
    if s and C.confirm(parent, f"「{s.name}」が異常終了しました (終了コード: {sp.exit_code if sp else '?'})。\n原因を診断しますか？"):
        ctx.set_current(sid)
        ctx.navigate.emit("diagnose")
        page = parent.pages.get("diagnose") if hasattr(parent, "pages") else None
        if page:
            page.run_diagnosis()


class DiagnosePage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("トラブル診断", "ログ・クラッシュレポート・MOD情報から原因を推定します。"
                                              "診断結果は推定であり、実際の原因と異なる場合があります。"))
        lay.addLayout(C.hbox(C.button("診断を実行", self.run_diagnosis, "primary"), C.button("推奨対応を実行...", self.do_action),
                             C.button("レポートを保存", self.save_report), C.button("crash-reportsを開く", self.open_crash)))
        sp = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.show_detail)
        sp.addWidget(self.list)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        sp.addWidget(self.detail)
        sp.setSizes([380, 620])
        sp.setMinimumHeight(330)
        lay.addWidget(sp, 1)

        g = QGroupBox("自動切り分け (原因不明のとき)")
        gl = QVBoxLayout(g)
        gl.addWidget(C.help_label(
            "MODを依存関係ごとにグループ化して半分ずつテストし、原因のMODを絞り込みます。\n"
            "元のサーバー・MOD・ワールドは変更しません (独立したテスト環境と新しいテスト用ワールドを使います)。"
            "結果は自動では適用しません。1回のテストでサーバーを1回起動するため、時間がかかります。"))
        self.timeout = QSpinBox()
        self.timeout.setRange(60, 3600)
        self.timeout.setValue(600)
        self.timeout.setSuffix(" 秒 (1回の起動待ち上限)")
        self.max_runs = QSpinBox()
        self.max_runs.setRange(3, 200)
        self.max_runs.setValue(40)
        self.max_runs.setSuffix(" 回まで")
        gl.addLayout(C.hbox(self.timeout, self.max_runs, C.button("分割診断プランを表示", self.show_plan),
                            C.button("自動切り分けを開始...", self.run_bisect, "primary")))
        self.bisect_out = QPlainTextEdit()
        self.bisect_out.setReadOnly(True)
        self.bisect_out.setMaximumHeight(200)
        gl.addWidget(self.bisect_out)
        lay.addWidget(g)
        self.findings: list[Finding] = []

    def refresh(self) -> None:
        self.setEnabled(self.ctx.current() is not None)

    def run_diagnosis(self):
        s = self.ctx.current()
        if not s:
            return
        sp = registry.find(s.id)
        console = list(sp.lines) if sp else None
        exit_code = sp.exit_code if sp else None
        since = sp.started_at if sp else None
        j, _r, _n = jm.resolve_for_server(s)

        def job(p, l, c):
            p("ログを解析しています...", None)
            return diagnose_server(s, console, exit_code, j.major if j else 0, since)

        def done(fs):
            self.findings = fs
            self.list.clear()
            for f in fs:
                it = QListWidgetItem(f"[{f.level}] {f.title}")
                it.setForeground(QColor(C.LEVEL_COLORS.get(f.level, "#333")))
                self.list.addItem(it)
            if fs:
                self.list.setCurrentRow(0)
            else:
                self.detail.setPlainText("問題は検出されませんでした。\n(ログが無い場合は、一度サーバーを起動してから診断してください)")

        C.run_task(self, "診断しています", job, done)

    def show_detail(self, row: int):
        if 0 <= row < len(self.findings):
            f = self.findings[row]
            text = f.text()
            if f.mod_files:
                text += "\n関係するMODファイル:\n" + "\n".join(Path(x).name for x in f.mod_files)
            if f.actions:
                text += "\n\n「推奨対応を実行」で次の操作ができます: " + ", ".join(_action_label(a) for a in f.actions)
            self.detail.setPlainText(text)

    def do_action(self):
        row = self.list.currentRow()
        s = self.ctx.current()
        if not s or not (0 <= row < len(self.findings)):
            C.info(self, "診断結果を選択してください。")
            return
        f = self.findings[row]
        if not f.actions:
            C.info(self, "この項目には自動で実行できる対応がありません。推奨対応の内容を確認してください。")
            return
        a = f.actions[0]
        t = a["type"]
        if t in ("disable", "disable_files"):
            files = [x for x in f.mod_files if Path(x).exists()] if t == "disable" else [x for x in a.get("files", []) if Path(x).exists()]
            if not files:
                C.warn(self, "対象のMODファイルが見つかりません。「MOD」画面で確認してください。")
                return
            sp = registry.find(s.id)
            if sp and sp.running:
                C.warn(self, "サーバーを停止してから実行してください。")
                return
            if C.confirm(self, "次のMODを無効化します (削除せず disabled_mods へ移動):\n" + "\n".join(Path(x).name for x in files) +
                               f"\n\n理由: {f.title}"):
                dis.disable(s, [Path(x) for x in files], f"診断: {f.title}")
                C.info(self, "無効化しました。「MOD」の「無効化・履歴」タブからいつでも戻せます。")
        elif t == "java":
            if C.confirm(self, f"Java {a['major']} 以上が必要です。アプリ設定の「Java」タブを開きますか？\n"
                               "(サーバー設定でJavaを「自動選択」にしていれば、導入後に自動で使われます)"):
                self.ctx.navigate.emit("java")
        elif t == "memory":
            self.ctx.navigate.emit("settings")
        elif t == "eula":
            actions.ensure_eula(self, s)
        elif t == "port":
            self.ctx.navigate.emit("settings")
        elif t == "config_quarantine":
            self._quarantine(s, a["path"])
        elif t == "find_mod":
            from msm.gui.fetch_mods import fetch_missing

            ids = [x["id"] for ff in self.findings for x in ff.actions if x.get("type") == "find_mod"]
            fetch_missing(self, s, ids)

    def _quarantine(self, s, rel: str):
        p = Path(rel)
        cand = p if p.is_absolute() else s.path / rel
        if not cand.exists():
            hits = list((s.path / "config").rglob(Path(rel).name)) if (s.path / "config").exists() else []
            cand = hits[0] if hits else cand
        if not cand.exists():
            C.warn(self, f"設定ファイルが見つかりません: {rel}")
            return
        dest = s.path / ".msm" / "config_quarantine" / time.strftime("%Y%m%d-%H%M%S") / cand.name
        if C.confirm(self, f"設定ファイルを退避します (削除しません):\n{cand}\n→ {dest}\n\n次回起動時に新しい設定ファイルが作られます。"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cand), str(dest))
            C.info(self, "退避しました。元に戻す場合は退避先からコピーしてください。")

    def save_report(self):
        s = self.ctx.current()
        if not s:
            return
        d = s.path / ".msm" / "reports"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"diagnosis-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        p.write_text(report_text(s, self.findings), "utf-8")
        open_path(p)

    def open_crash(self):
        s = self.ctx.current()
        if s:
            d = s.path / "crash-reports"
            open_path(d if d.exists() else s.path)

    def _bisector(self, s, java=None):
        j, _r, note = jm.resolve_for_server(s)
        if not j:
            raise RuntimeError(note)
        return Bisector(s, j.path, j.major, self.timeout.value(), self.max_runs.value())

    def show_plan(self):
        s = self.ctx.current()
        if not s:
            return
        if not s.is_modded:
            C.info(self, "MODサーバー (Forge/NeoForge/Fabric) でのみ使えます。")
            return
        b = self._bisector(s)
        b.load()
        self.bisect_out.setPlainText(b.plan_text())

    def run_bisect(self):
        s = self.ctx.current()
        if not s:
            return
        if not s.is_modded:
            C.info(self, "MODサーバー (Forge/NeoForge/Fabric) でのみ使えます。")
            return
        if not scan_dir(s.mods_dir, with_hash=False):
            C.info(self, "MODがありません。")
            return
        try:
            b = self._bisector(s)
        except Exception as e:  # noqa: BLE001
            C.error(self, str(e))
            return
        b.load()
        if not C.confirm(self, b.plan_text() + "\n\n自動切り分けを開始しますか？\n(テスト中はPCに負荷がかかります。途中でキャンセルできます)"):
            return

        def job(p, l, c):
            b.log, b.progress, b.cancel = l, p, c
            res = b.run()
            text = bisect_report(s, res)
            d = s.path / ".msm" / "reports"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"bisect-{time.strftime('%Y%m%d-%H%M%S')}.txt").write_text(text, "utf-8")
            return res, text

        def done(r):
            res, text = r
            self.bisect_out.setPlainText(text)
            if res.culprits:
                if C.confirm(self, text[:1500] + "\n\nテスト結果は本番環境には適用していません。\n"
                                                 "原因候補のMODを本番サーバーで無効化しますか？ (削除はしません)"):
                    sp = registry.find(s.id)
                    if sp and sp.running:
                        C.warn(self, "サーバーを停止してから、「MOD」画面で無効化してください。")
                        return
                    dis.disable(s, [Path(x) for x in res.culprits if Path(x).exists()], f"自動切り分けの結果 ({res.confidence})")
                    C.info(self, "無効化しました。「MOD」の「無効化・履歴」タブから戻せます。")

        C.run_task(self, "自動切り分け", job, done, keep_open=True)


def _action_label(a: dict) -> str:
    return {"disable": "MODを無効化", "disable_files": "MODを無効化", "java": f"Java {a.get('major')}を設定",
            "memory": "メモリ設定を開く", "eula": "EULAに同意", "port": "ポート設定を開く",
            "config_quarantine": "configを退避", "find_mod": "前提MODを自動で探して追加"}.get(a.get("type"), a.get("type", ""))
