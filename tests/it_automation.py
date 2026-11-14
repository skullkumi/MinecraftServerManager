"""自動再起動・スケジュール実行の統合テスト (実サーバー + 実ウィンドウ)."""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures import check, get_server, summary  # noqa: E402

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from msm.gui import common as C  # noqa: E402

C.confirm = lambda *a, **k: False
C.info = C.warn = C.error = lambda *a, **k: None
QMessageBox.exec = lambda self: 0

from msm.gui.main_window import MainWindow  # noqa: E402
from msm.integrations import discord  # noqa: E402
from msm.server import instance  # noqa: E402
from msm.server.process import registry  # noqa: E402

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)
root = Path(sys.argv[1])
s = get_server(root, "vanilla", "1.20.1", "T auto")
sent = []
discord.notifier().send = lambda event, name, text, enabled=True: sent.append((event, text)) or True
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


s.options = {"auto_restart": True, "auto_restart_max": 1, "auto_restart_window_min": 10}
instance.save_server(s)
w.ctx.set_current(s.id)
w.automation._start(s, "テスト起動")
check("起動", pump_until(lambda: w.ctx.state_of(s.id) == "running", 300))
check("起動完了の通知", pump_until(lambda: any(e == "start" for e, _ in sent), 5), sent)
first_pid = registry.find(s.id).pid
import psutil  # noqa: E402

psutil.Process(first_pid).kill()  # クラッシュを再現
check("強制終了を異常終了として検出", pump_until(lambda: any(e == "crash" for e, _ in sent), 30), sent[-1:])
check("10秒後に自動再起動", pump_until(lambda: w.ctx.state_of(s.id) == "running" and registry.find(s.id).pid != first_pid, 300))
psutil.Process(registry.find(s.id).pid).kill()
check("上限 (10分以内に1回) を超えたら自動再起動しない",
      pump_until(lambda: any("止めました" in t for _, t in sent), 30) and not pump_until(lambda: w.ctx.state_of(s.id) == "running", 20))

# スケジュール: 予告 → 定時再起動
w.automation._start(s, "テスト起動")
check("再度起動", pump_until(lambda: w.ctx.state_of(s.id) == "running", 300))
target = datetime.now() + timedelta(minutes=5)
hhmm = target.strftime("%H:%M")
s.options = dict(s.options, schedule={"enabled": True, "days": list(range(7)), "restart_times": [hhmm],
                                      "warn_minutes": [5], "backup_times": []})
instance.save_server(s)
now_warn = target.replace(second=0, microsecond=0) - timedelta(minutes=5) + timedelta(seconds=10)
done = w.automation.tick(now_warn)
check("5分前の予告を実行", [a.kind for _, a in done] == ["warn"], [a.key for _, a in done])
check("予告がゲーム内に送られる", pump_until(lambda: any("5分後にサーバーを再起動" in l for l in registry.find(s.id).lines), 10))
pid_before = registry.find(s.id).pid
done = w.automation.tick(target.replace(second=0, microsecond=0) + timedelta(seconds=10))
check("定時再起動を実行", [a.kind for _, a in done] == ["restart"])
check("定時再起動で停止→起動", pump_until(lambda: registry.find(s.id).pid not in (None, pid_before) and w.ctx.state_of(s.id) == "running", 300))
check("同じ予定は2回実行しない", w.automation.tick(target.replace(second=0, microsecond=0) + timedelta(seconds=20)) == [])
sp = registry.find(s.id)
sp.stop(120)
w._quitting = True
s.options = {}
instance.save_server(s)
sys.exit(summary())
