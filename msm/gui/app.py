"""アプリ起動処理."""
from __future__ import annotations

import sys
import traceback

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from msm import APP_NAME
from msm.core.paths import logs_dir, resource_path
from msm.core.util import log, setup_logging


def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("未処理の例外:\n%s", text)
    try:
        QMessageBox.critical(None, APP_NAME, f"予期しないエラーが発生しました。\n\n{exc}\n\n詳細はログ (app.log) に記録されています。")
    except Exception:
        pass


def selftest(out_path: str) -> int:
    """配布版EXEの動作確認用: 全画面を生成・更新して結果をファイルに書く (画面は表示しない)."""
    import json
    import os

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication(sys.argv)
    from msm.gui import common as C
    from msm.gui.main_window import NAV, MainWindow

    C.confirm = lambda *a, **k: False
    C.info = C.warn = C.error = lambda *a, **k: None
    result = {"ok": True, "errors": [], "pages": []}
    try:
        w = MainWindow()
        w.show()
        for key, _label in NAV:
            try:
                w.go(key)
                w.pages[key].refresh()
                app.processEvents()
                result["pages"].append(key)
            except Exception as e:  # noqa: BLE001
                result["ok"] = False
                result["errors"].append(f"{key}: {e!r}")
        from msm.java import manager as jm
        from msm.server.providers import PROVIDERS

        result["java"] = [j.label for j in jm.refresh()]
        result["vanilla_versions"] = len(PROVIDERS["vanilla"].list_mc_versions())
    except Exception:  # noqa: BLE001
        result["ok"] = False
        result["errors"].append(traceback.format_exc())
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return 0 if result["ok"] else 1


def main() -> int:
    setup_logging(logs_dir())
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        return selftest(sys.argv[i + 1] if len(sys.argv) > i + 1 else "selftest.json")
    log.info("起動")
    sys.excepthook = _excepthook
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon = resource_path("assets", "icon.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    from msm.core.db import get_db
    from msm.gui import theme

    theme.apply(app, get_db().get_setting("theme", "system"))
    from msm.gui import i18n, privacy

    privacy.install_hooks()
    i18n.install_hooks()
    from msm.gui.main_window import MainWindow

    app.setQuitOnLastWindowClosed(False)  # タスクトレイ常駐のため (終了はMainWindowが制御)
    w = MainWindow()
    if "--minimized" in sys.argv and w.tray is not None:
        w.hide()  # Windows起動時: タスクトレイに格納した状態で起動
    else:
        w.show()
    from PySide6.QtCore import QTimer

    from msm.core.db import get_db

    if not w.ctx.servers() and not get_db().get_setting("wizard_done", False):
        QTimer.singleShot(400, w.open_wizard)
    if get_db().get_setting("update_auto_check", True) and (get_db().get_setting("update_github") or get_db().get_setting("update_url")):
        from msm.gui.pages.app_settings import run_update_check

        QTimer.singleShot(5000, lambda: run_update_check(w, quiet=True))
    return app.exec()
