"""GUI操作の流れを自動で確認する (作成 → 起動 → コマンド → バックアップ → 停止 → 設定変更)."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from msm.gui import common as C  # noqa: E402

log = []
C.confirm = lambda parent, text, title="確認", danger=False: (log.append(("confirm", text[:80])), True)[1]
C.info = lambda parent, text, title="": log.append(("info", text[:120]))
C.warn = lambda parent, text, title="": log.append(("warn", text[:200]))
C.error = lambda parent, text, title="": log.append(("error", text[:300]))
C.ask_choice = lambda parent, text, choices, title="": 0
QMessageBox.exec = lambda self: 0

from msm.gui.common import STYLE  # noqa: E402
from msm.gui.main_window import MainWindow  # noqa: E402
from msm.server.process import registry  # noqa: E402

app = QApplication(sys.argv)
app.setStyleSheet(STYLE)
w = MainWindow()
w.show()
results = []


def check(name, cond, detail=""):
    results.append((name, cond))
    print(("OK  " if cond else "NG  ") + name + (f" ({detail})" if detail else ""), flush=True)


def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.05)


def wait_until(fn, timeout):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        if fn():
            return True
        time.sleep(0.1)
    return False


root = Path(sys.argv[1])
w.go("create")
cp = w.pages["create"]
cp.refresh()
cp.name.setText("GUIテスト")
cp.type.setCurrentIndex(cp.type.findData("vanilla"))
wait_until(lambda: cp.mc.count() > 5 and cp.mc.isEnabled(), 30)
cp.mc.setCurrentIndex(cp.mc.findText("1.20.1"))
pump(1)
cp.folder.setText(str(root / f"gui-{int(time.time())}"))
cp.maxmem.setValue(2048)
cp.eula.setChecked(True)
before = len(w.ctx.servers())
cp.create()
pump(1)
check("GUIからサーバー作成", len(w.ctx.servers()) == before + 1, str(log[-1:]))
s = w.ctx.current()
check("作成したサーバーが選択される", s is not None and s.name == "GUIテスト")
w.go("dashboard")
dash = w.pages["dashboard"]
dash.start()
check("起動ボタンで起動中になる", wait_until(lambda: w.ctx.state_of(s.id) in ("starting", "running"), 10))
check("起動完了 (稼働中)", wait_until(lambda: w.ctx.state_of(s.id) == "running", 300))
w.go("detail")
det = w.pages["detail"]
pump(1)
check("コンソールに出力が表示される", "Done" in det.console.toPlainText())
det.cmd.setText("say GUIテスト")
det.send()
pump(2)
check("コマンド送信", "GUIテスト" in det.console.toPlainText())
dash.tick()
check("ダッシュボードにメモリ表示", "GB" in dash.t_mem[1].text() or "MB" in dash.t_mem[1].text(), dash.t_mem[1].text())
check("ダッシュボードのLAN IPは伏せ字", "●" in dash.j_lan.label.text(), dash.j_lan.label.text())
w.go("backups")
bp = w.pages["backups"]
bp.refresh()
n0 = bp.table.rowCount()
bp.c_inc.setChecked(True)
bp.create()
pump(1)
bp.refresh()
check("稼働中のワールドの差分バックアップ (save-off/save-all)", bp.table.rowCount() == n0 + 1 and "差分" in bp.table.item(0, 3).text(), bp.table.item(0, 3).text() if bp.table.rowCount() else "")
w.go("players")
w.pages["players"].update_list()
pump(2)
w.go("settings")
sp = w.pages["settings"]
sp.refresh()
sp.tabs.setCurrentIndex(1)
sp.widgets["motd"].setText("GUIから変更")
sp.save_props()
from msm.server.properties import read_properties  # noqa: E402

check("server.propertiesの日本語編集・保存", read_properties(s.path / "server.properties").get("motd") == "GUIから変更")
check("変更前の自動バックアップ", any(r.kind == "auto-change" for r in __import__("msm.backup.backup", fromlist=["x"]).list_backups(s.id)))
w.go("dashboard")
dash.stop()
check("停止ボタンで停止", wait_until(lambda: w.ctx.state_of(s.id) == "stopped", 120), w.ctx.state_of(s.id))
check("停止後は「まだ参加できません」を表示", (dash.tick() or True) and dash.join_notice.isVisible())
w.go("worlds")
w.pages["worlds"].refresh()
check("ワールド一覧", w.pages["worlds"].table.rowCount() >= 1)
w.go("diagnose")
w.pages["diagnose"].run_diagnosis()
pump(1)
check("診断が実行できる", True, w.pages["diagnose"].detail.toPlainText()[:60])
print("\nログ:", log[-6:])
ng = [r for r in results if not r[1]]
print(f"{len(results) - len(ng)}/{len(results)} OK")
for sp_ in registry.running():
    sp_.kill()
sys.exit(1 if ng else 0)
