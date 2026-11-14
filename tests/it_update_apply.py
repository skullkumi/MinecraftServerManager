"""アプリ更新の入れ替えを実際のEXEで確認する (ローカルの模擬配布サーバーを使用).

python tests/it_update_apply.py <作業フォルダー> <新しい版のZIP (ポータブル版)>
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures import check, summary  # noqa: E402

work = Path(sys.argv[1])
new_zip = Path(sys.argv[2])
shutil.rmtree(work, ignore_errors=True)
(work / "site").mkdir(parents=True)
(work / "installed").mkdir()
os.environ["MSM_DATA_DIR"] = str(work / "data")
os.environ["MSM_ALLOW_HTTP_UPDATE"] = "1"

from msm import update  # noqa: E402
from msm.core.db import get_db  # noqa: E402

# 「インストール済み」の古いEXE (中身の違いが分かるよう末尾にダミーを付けたコピー)
import zipfile  # noqa: E402

with zipfile.ZipFile(new_zip) as zf:
    exe_bytes = zf.read("MinecraftServerManager.exe")
installed = work / "installed" / "MinecraftServerManager.exe"
installed.write_bytes(exe_bytes + b"OLDVERSION")
shutil.copy(new_zip, work / "site" / new_zip.name)
sha = hashlib.sha256(new_zip.read_bytes()).hexdigest()
handler = partial(SimpleHTTPRequestHandler, directory=str(work / "site"))
handler.log_message = lambda *a: None
srv = HTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
(work / "site" / "latest.json").write_text(json.dumps({"version": "99.0.0", "url": base, "notes": "test",
                                                        "downloads": {"onefile": {"url": f"{base}/{new_zip.name}", "sha256": sha}}}))
get_db().set_setting("update_url", f"{base}/latest.json")
update.install_mode = lambda: "onefile"
info = update.check()
check("新しい版を検出し自動更新が可能", info["status"] == "available" and info["auto"], info["message"])
path = update.download(info)
check("SHA-256を検証してダウンロード", path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == sha)
dummy = subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", "Start-Sleep -Seconds 4"], creationflags=0x08000000)
cmd = update.prepare(path, "onefile", app_exe=installed, pid=dummy.pid)
update.launch(cmd)
t0 = time.time()
old = installed.with_name(installed.name + ".old")
while time.time() - t0 < 60 and not (old.exists() and installed.exists() and installed.stat().st_size == len(exe_bytes)):
    time.sleep(1)
check("アプリ終了後にEXEを入れ替え", installed.exists() and installed.read_bytes()[-10:] != b"OLDVERSION")
check("古いEXEを .old として保存", old.exists() and old.read_bytes().endswith(b"OLDVERSION"))
log = path.parent / "update.log"
check("更新ログにOK", log.exists() and "OK" in log.read_text("utf-8-sig", "replace"), log.read_text("utf-8-sig", "replace")[:200] if log.exists() else "")
# 新しいEXEが起動したか確認して終了させる
time.sleep(4)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                    f"Get-CimInstance Win32_Process -Filter \"Name='MinecraftServerManager.exe'\" | Where-Object {{ $_.ExecutablePath -eq '{installed}' }} | ForEach-Object {{ $_.ProcessId }}"],
                   capture_output=True, text=True)
pids = [int(x) for x in r.stdout.split() if x.strip().isdigit()]
check("更新後のアプリを自動で起動", bool(pids), pids)
for pid in pids:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
srv.shutdown()
sys.exit(summary())
