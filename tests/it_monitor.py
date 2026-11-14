"""負荷記録とグラフ表示の確認 (実サーバー)。最後に画面のスクリーンショットを保存する."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures import check, get_server, summary  # noqa: E402

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from msm.gui import common as C  # noqa: E402

C.confirm = lambda *a, **k: False
C.info = C.warn = C.error = lambda *a, **k: None
QMessageBox.exec = lambda self: 0

from msm.core.db import get_db  # noqa: E402
from msm.gui.main_window import MainWindow  # noqa: E402
from msm.server import metrics  # noqa: E402
from msm.server.process import registry  # noqa: E402

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)
from msm.gui.common import STYLE  # noqa: E402

app.setStyleSheet(STYLE)
root = Path(sys.argv[1])
s = get_server(root, "vanilla", "1.21.1", "T monitor")
w = MainWindow()
w.resize(1280, 1300)
w.show()


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.05)


get_db().set_setting("tps_polling", True)
w.ctx.set_current(s.id)
w.automation._start(s, "テスト")
t0 = time.time()
while w.ctx.state_of(s.id) != "running" and time.time() - t0 < 300:
    pump(0.5)
check("起動", w.ctx.state_of(s.id) == "running")
for i in range(6):
    w.automation._tps_at.clear() if i % 3 == 0 else None
    w.automation.record_metrics()
    pump(15)
rows = metrics.query(s.id, time.time() - 600)
check("負荷を記録 (CPU・メモリ・人数)", len(rows) >= 5 and all(r["rss"] for r in rows), len(rows))
check("CPU使用率が0以外の値を取得", any((r["cpu"] or 0) > 0 for r in rows[1:]), [round(r["cpu"] or 0, 1) for r in rows])
check("TPS/MSPTを記録 (vanilla tick query)", any(r["tps"] is not None for r in rows), [(r["tps"], r["mspt"]) for r in rows])
w.go("monitor")
page = w.pages["monitor"]
page.refresh()
pump(1)
check("グラフにデータを表示", len(page.c_mem.points) >= 5 and "記録" in page.summary.text(), page.summary.text())
csv_path = root / "metrics.csv"
n = metrics.export_csv(s.id, time.time() - 600, csv_path)
check("CSV出力", n >= 5 and csv_path.read_text("utf-8-sig").startswith("日時"))
page.c_mem.hover_x = page.c_mem.width() * 0.9
page.c_mem.update()
pump(0.5)
w.grab().save(str(root / "monitor.png"))
registry.find(s.id).stop(120)
get_db().set_setting("tps_polling", False)
w._quitting = True
sys.exit(summary())
