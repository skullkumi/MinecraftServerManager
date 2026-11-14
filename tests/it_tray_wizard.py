"""タスクトレイ常駐とセットアップウィザードのテスト (実際のウィンドウで実行)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures import check, summary  # noqa: E402

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from msm.gui import common as C  # noqa: E402

msgs = []
C.confirm = lambda *a, **k: True
C.info = lambda p, t, *a, **k: msgs.append(t)
C.warn = lambda p, t, *a, **k: msgs.append("WARN " + t)
C.error = lambda p, t, *a, **k: msgs.append("ERR " + t)
QMessageBox.exec = lambda self: 0

from msm.gui.common import STYLE  # noqa: E402
from msm.gui.main_window import MainWindow  # noqa: E402
from msm.server import process  # noqa: E402

app = QApplication(sys.argv)
app.setStyleSheet(STYLE)
app.setQuitOnLastWindowClosed(False)
w = MainWindow()
w.show()
app.processEvents()
check("タスクトレイアイコンを作成", w.tray is not None and w.tray.isVisible())


class Dummy:
    running = True
    server = type("S", (), {"name": "dummy"})()


orig = process.registry.running
process.registry.running = lambda: [Dummy()]
w.close()
app.processEvents()
check("稼働中に閉じるとトレイに格納 (終了しない)", w.isHidden())
w.show_from_tray()
app.processEvents()
check("トレイから画面を戻せる", w.isVisible())
process.registry.running = orig

# ウィザード
from msm.gui.wizard import P_DETAIL, SetupWizard  # noqa: E402

wiz = SetupWizard(w, w.ctx)
wiz.show()
for _ in range(3):
    wiz.next()
    app.processEvents()
d = wiz.page(P_DETAIL)
t0 = time.time()
while d.mc.count() < 3 and time.time() - t0 < 30:
    app.processEvents()
    time.sleep(0.1)
check("ウィザード: 詳細ページでPaperのバージョン一覧を取得", d.mc.count() > 3 and d.type.currentData() == "paper", d.mc.count())
check("ウィザード: EULA未同意では先に進めない", not d.validatePage())
d.eula.setChecked(True)
check("ウィザード: EULA同意後は進める", d.validatePage())
wiz.back()
wiz.purpose_radios["pack"].setChecked(True)
wiz.next()
app.processEvents()
check("ウィザード: MODパック選択時はファイル欄を表示", d.pack.isVisibleTo(wiz) and not d.mc.isVisibleTo(wiz))
wiz.close()
w._quitting = True
w.close()
sys.exit(summary())
