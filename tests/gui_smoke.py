"""GUIのスモークテスト: 全ページを生成して refresh() を呼ぶ (offscreen)."""
import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

# ダイアログで止まらないようにする
QMessageBox.exec = lambda self: 0
QMessageBox.information = staticmethod(lambda *a, **k: print("INFO:", a[2] if len(a) > 2 else a))
QMessageBox.warning = staticmethod(lambda *a, **k: print("WARN:", a[2] if len(a) > 2 else a))
QMessageBox.critical = staticmethod(lambda *a, **k: print("ERROR:", a[2] if len(a) > 2 else a))

from msm.gui.common import STYLE  # noqa: E402
from msm.gui.main_window import NAV, MainWindow  # noqa: E402

app = QApplication(sys.argv)
app.setStyleSheet(STYLE)
w = MainWindow()
w.show()
errors = 0
servers = w.ctx.servers()
print("servers:", [s.name for s in servers])
for s in (servers or [None])[:5]:
    if s:
        w.ctx.set_current(s.id)
    for key, label in NAV:
        try:
            w.go(key)
            w.pages[key].refresh()
            app.processEvents()
        except Exception:
            errors += 1
            print(f"NG {key} ({s.name if s else '-'})")
            traceback.print_exc()
print("errors:", errors)
w.grab().save(str(Path(os.environ.get("MSM_SHOT", "shot.png"))))
sys.exit(1 if errors else 0)
