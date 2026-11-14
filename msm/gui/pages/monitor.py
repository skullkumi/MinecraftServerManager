"""負荷モニター (CPU・メモリ・人数・TPS・MSPTの推移)."""
from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QLabel, QVBoxLayout

from msm.core.db import get_db
from msm.gui import common as C
from msm.gui.chart import LineChart
from msm.server import metrics
from msm.server.monitor import tps_method

RANGES = [("1時間", 3600), ("6時間", 6 * 3600), ("24時間", 86400), ("7日間", 7 * 86400)]


class MonitorPage(C.Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.addWidget(C.page_header("負荷モニター", "サーバー稼働中の負荷を15秒ごとに記録します (7日分を保存)。重くなった時間帯の確認に使えます。"))
        self.range = QComboBox()
        for label, sec in RANGES:
            self.range.addItem(label, sec)
        self.range.currentIndexChanged.connect(self.refresh)
        self.tps = QCheckBox("TPS/MSPTも記録する (1分ごとにサーバーへコマンドを送ります)")
        self.tps.toggled.connect(lambda v: get_db().set_setting("tps_polling", v))
        lay.addLayout(C.hbox(QLabel("表示期間:"), self.range, C.button("更新", self.refresh), C.button("CSVで保存...", self.export),
                             self.tps))
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.c_cpu = LineChart("CPU使用率", "%", "{:.0f}", fixed_max=100)
        self.c_mem = LineChart("メモリ使用量", "GB", "{:.1f}")
        self.c_players = LineChart("プレイヤー数", "人", "{:.0f}", step=True)
        self.c_tps = LineChart("TPS (20が正常)", "", "{:.1f}", fixed_max=20)
        self.c_mspt = LineChart("MSPT (1tickの処理時間・50ms以下が正常)", "ms", "{:.1f}")
        self.charts = [self.c_cpu, self.c_mem, self.c_players, self.c_tps, self.c_mspt]
        for c in self.charts:
            lay.addWidget(c)
        lay.addWidget(C.help_label("※ CPU使用率はPC全体に対する割合です。メモリの点線は割り当て上限です。\n"
                                   "※ TPS/MSPTはサーバー種類によって取得方法が異なり、取得できない種類もあります (未対応と表示します)。"))
        lay.addStretch(1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start(15000)

    def set_dark(self, dark: bool) -> None:
        for c in self.charts:
            c.set_dark(dark)

    def refresh(self) -> None:
        s = self.ctx.current()
        self.tps.blockSignals(True)
        self.tps.setChecked(bool(get_db().get_setting("tps_polling", False)))
        self.tps.blockSignals(False)
        if not s:
            for c in self.charts:
                c.set_data([], (time.time() - 3600, time.time()))
            self.summary.setText("サーバーが選択されていません。")
            return
        sec = self.range.currentData()
        now = time.time()
        span = (now - sec, now)
        rows = metrics.query(s.id, now - sec)
        self.c_cpu.set_data([(r["ts"], r["cpu"]) for r in rows], span)
        self.c_mem.set_data([(r["ts"], (r["rss"] or 0) / 1024 ** 3 if r["rss"] is not None else None) for r in rows], span,
                            s.max_mem_mb / 1024, f"割り当て上限 {s.max_mem_mb / 1024:.1f}GB")
        self.c_players.set_data([(r["ts"], r["players"]) for r in rows], span)
        self.c_tps.set_data([(r["ts"], r["tps"]) for r in rows], span)
        self.c_mspt.set_data([(r["ts"], r["mspt"]) for r in rows], span, 50, "目標 50ms")
        cmd, desc = tps_method(s.server_type, s.mc_version)
        for c in (self.c_tps, self.c_mspt):
            c.empty_text = ("このサーバー種類では取得できません (未対応)" if not cmd else
                            ("データがありません (「TPS/MSPTも記録する」をオンにしてください)" if not self.tps.isChecked()
                             else "データがありません"))
        if rows:
            def stat(key, conv=lambda x: x):
                vals = [conv(r[key]) for r in rows if r[key] is not None]
                return (sum(vals) / len(vals), max(vals)) if vals else (None, None)

            cpu = stat("cpu")
            mem = stat("rss", lambda x: x / 1024 ** 3)
            pl = stat("players")
            tps = stat("tps")
            parts = [f"記録 {len(rows)}件"]
            if cpu[0] is not None:
                parts.append(f"CPU 平均{cpu[0]:.0f}% / 最大{cpu[1]:.0f}%")
            if mem[0] is not None:
                parts.append(f"メモリ 平均{mem[0]:.1f}GB / 最大{mem[1]:.1f}GB")
            if pl[0] is not None:
                parts.append(f"最大 {pl[1]:.0f}人")
            if tps[0] is not None:
                low = min(r["tps"] for r in rows if r["tps"] is not None)
                parts.append(f"TPS 平均{tps[0]:.1f} / 最低{low:.1f}")
            self.summary.setText("  ・  ".join(parts))
        else:
            self.summary.setText("この期間の記録はありません (サーバーの稼働中に記録されます)。")

    def export(self):
        s = self.ctx.current()
        if not s:
            return
        path, _ = QFileDialog.getSaveFileName(self, "CSVで保存", f"{s.name}-負荷.csv", "CSV (*.csv)")
        if path:
            from pathlib import Path

            n = metrics.export_csv(s.id, time.time() - self.range.currentData(), Path(path))
            C.info(self, f"{n}件を保存しました。")
