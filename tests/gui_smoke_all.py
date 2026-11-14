"""全画面を言語・テーマごとに生成・更新して例外が出ないか確認する (offscreen).

python tests/gui_smoke_all.py ja light   (言語: ja/en  テーマ: light/dark)
"""
import os
import sys
import traceback
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QMessageBox.exec = lambda self: 0
from msm.core.db import get_db  # noqa: E402

lang, mode = sys.argv[1], sys.argv[2]
get_db().set_setting("language", lang)
from msm.gui import i18n, privacy, theme  # noqa: E402

app = QApplication(sys.argv)
theme.apply(app, mode)
privacy.install_hooks()
i18n.install_hooks()
from msm.gui.main_window import NAV, NAV_TOP, MainWindow  # noqa: E402

w = MainWindow()
w.show()
err = 0
for s in (w.ctx.servers()[:6] or [None]):
    if s:
        w.ctx.set_current(s.id)
    for key, _ in NAV:
        try:
            w.go(key)
            w.pages[key].refresh()
            app.processEvents()
            assert w.current_key() == key, f"go({key}) -> {w.current_key()}"
        except Exception:
            err += 1
            traceback.print_exc()
    for top, *_ in NAV_TOP:
        try:
            w.go(top)
            app.processEvents()
        except Exception:
            err += 1
            traceback.print_exc()
w.apply_theme("dark" if mode == "light" else "light")
app.processEvents()
get_db().set_setting("language", "ja")
print(lang, mode, "errors:", err, flush=True)
w._quitting = True
os._exit(1 if err else 0)
