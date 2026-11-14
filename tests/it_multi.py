"""サーバー一覧での一括操作 (同時起動・一括停止・ポート重複の回避) の統合テスト."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures import check, summary  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from msm.gui import common as C  # noqa: E402

msgs = []
C.confirm = lambda *a, **k: True
C.info = lambda p, t, *a, **k: msgs.append(t)
C.warn = lambda p, t, *a, **k: msgs.append("WARN " + t)
C.error = lambda p, t, *a, **k: msgs.append("ERR " + t)
QMessageBox.exec = lambda self: 0

from msm.gui.main_window import MainWindow  # noqa: E402
from msm.server import instance  # noqa: E402
from msm.server.process import registry  # noqa: E402

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)
w = MainWindow()
w.show()


def pump_until(fn, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if fn():
            return True
        time.sleep(0.1)
    return False


ss = {s.name: s for s in w.ctx.servers()}
a, b = ss["ミニゲーム (Paper)"], ss["バニラ"]
w.go("servers")
page = w.pages["servers"]
page.refresh()


def set_checked(names):
    for r in range(page.table.rowCount()):
        it = page.table.item(r, 0)
        s = instance.get_server(it.data(Qt.UserRole))
        it.setCheckState(Qt.Checked if s.name in names else Qt.Unchecked)


set_checked({a.name, b.name})
check("チェックした台数を表示", "2台" in page.sel_label.text(), page.sel_label.text())
page.start_checked()
check("2台を同時に起動", pump_until(lambda: w.ctx.state_of(a.id) == "running" and w.ctx.state_of(b.id) == "running", 400))
page.refresh()
check("一覧に稼働中・メモリを表示", "稼働中 2台" in page.summary.text() and "GB" in page.table.item(0, 4).text() + page.table.item(1, 4).text(),
      page.summary.text())
check("上部の選択欄に稼働中の印", "🟢" in w.server_combo.itemText(w.server_combo.findData(a.id)))
page.stop_checked()
check("2台をまとめて停止", pump_until(lambda: w.ctx.state_of(a.id) == "stopped" and w.ctx.state_of(b.id) == "stopped", 120))
# ポートが重なる場合は1台だけ起動
orig = b.port
b.port = a.port
instance.save_server(b)
page.refresh()
set_checked({a.name, b.name})
msgs.clear()
page.start_checked()
check("ポート重複は片方だけ起動し、理由を表示", any("ポート番号が重なる" in m for m in msgs), msgs[-1:] if msgs else "")
pump_until(lambda: w.ctx.state_of(a.id) == "running", 300)
check("重複していない方は起動", w.ctx.state_of(a.id) == "running" and w.ctx.state_of(b.id) == "stopped")
registry.find(a.id).stop(120)
b.port = orig
instance.save_server(b)
w._quitting = True
sys.exit(summary())
