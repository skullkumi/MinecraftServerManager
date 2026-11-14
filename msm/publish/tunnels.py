"""外部公開方法 (playit.gg / 直接公開 / LAN限定 / その他トンネル).

どの方式も任意。playit.ggがインストールされていなくてもアプリは利用できる。
新しいトンネルサービスは TunnelProvider を継承して PROVIDERS に登録すれば追加できる。
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

from msm.core import net
from msm.core.db import get_db
from msm.core.util import CREATE_NO_WINDOW, run_hidden
from msm.server import ports

METHOD_INFO = {
    "playit": {
        "label": "playit.gg (ポート開放不要)",
        "merits": "ルーターの設定が不要です。自宅のIPアドレスを相手に知られません。無料で使えます。",
        "demerits": "playit.ggのアカウント登録と専用ソフト (playit) の導入が必要です。中継するため遅延が少し増えることがあります。"
                    "playit.ggのサービス状況に依存します。",
    },
    "direct": {
        "label": "直接公開 (ポート開放)",
        "merits": "中継を挟まないので遅延が最も少ないです。追加ソフトが不要です。",
        "demerits": "ルーターでポート開放の設定が必要です (できない回線もあります)。\n"
                    "⚠ 自宅のグローバルIPアドレスを相手に教えることになります。IPから大まかな地域が分かったり、"
                    "攻撃 (DoS) の対象になる危険があります。信頼できる相手だけに教えてください。",
    },
    "lan": {
        "label": "LAN限定",
        "merits": "設定不要で最も安全です。同じWi-Fi・同じルーターに繋がっている人だけが参加できます。",
        "demerits": "外出先や別の家の友達は参加できません。",
    },
    "custom": {
        "label": "VPN・その他のトンネル",
        "merits": "Tailscale・ZeroTier・Radmin VPN・ngrokなど好きなサービスを使えます。",
        "demerits": "サービスごとの設定が必要です。本アプリはコマンドの起動・停止と接続アドレスの表示のみを補助します。",
    },
}

ADDRESS_RE = re.compile(r"\b([a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.(?:joinmc\.link|ply\.gg|playit\.gg|playit\.plus|at\.ply\.gg))(?::(\d{1,5}))?\b",
                        re.I)


# ---------------------------------------------------------------- playit
def find_playit() -> str | None:
    custom = get_db().get_setting("playit_path")
    cands = [custom, r"C:\Program Files\playit_gg\bin\playit.exe",
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "playit_gg", "bin", "playit.exe"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "playit_gg", "bin", "playit.exe"),
             shutil.which("playit"), shutil.which("playit.exe")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


@dataclass
class PlayitStatus:
    installed: bool
    path: str = ""
    version: str = ""
    service_mode: bool = False  # 1.x の常駐サービス (playitd) 方式か
    service_running: bool = False
    secret_configured: bool | None = None
    raw: str = ""
    message: str = ""


def playit_status() -> PlayitStatus:
    exe = find_playit()
    if not exe:
        return PlayitStatus(False, message="playitが見つかりません (インストールされていないか、場所が不明です)")
    st = PlayitStatus(True, exe)
    try:
        r = run_hidden([exe, "version"], timeout=10)
        st.version = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()[0] if r.stdout else ""
    except Exception:
        pass
    try:
        r = run_hidden([exe, "status"], timeout=15)
        text = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
        st.raw = text
        if "Phase:" in text or "service" in text.lower():
            st.service_mode = "Phase:" in text
            m = re.search(r"Phase:\s*(\w+)", text)
            st.service_running = bool(m and m.group(1).lower() == "running")
            m = re.search(r"Secret configured:\s*(\w+)", text)
            if m:
                st.secret_configured = m.group(1).lower() == "true"
    except Exception as e:  # noqa: BLE001
        st.message = f"状態を取得できません: {e}"
    if not st.message:
        if st.service_mode:
            st.message = "常駐サービス: " + ("稼働中" if st.service_running else "停止中")
            if st.secret_configured is False:
                st.message += " / 未設定 (playit.ggアカウントとの連携が必要です)"
        else:
            st.message = "旧形式のplayitエージェントです (アプリから直接起動します)"
    return st


def playit_log_dirs() -> list[str]:
    cands = [os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "playit_gg", "logs"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "playit_gg", "logs")]
    return [c for c in cands if os.path.isdir(c)]


@dataclass
class PlayitLogInfo:
    public_addresses: list[str]  # 実際に接続があった公開側の IP:ポート (新しい順)
    origin_errors: list[str]  # ローカルのサーバーに接続できなかった転送先 (IP:ポート)
    api_error: str = ""
    last_seen: str = ""


def playit_log_info(max_bytes: int = 2_000_000) -> PlayitLogInfo:
    """playitサービスのログから接続先の手がかりを読む.

    取り出すのは「公開側の接続先 (connect_addr)」と「転送先エラー (origin_addr)」だけ。
    接続元のIPアドレスやトークンなどは読み取らない・保存しない。
    """
    info = PlayitLogInfo([], [])
    files = []
    for d in playit_log_dirs():
        for name in os.listdir(d):
            if name.startswith("playitd.log"):
                p = os.path.join(d, name)
                files.append((os.path.getmtime(p), p))
    for _mt, p in sorted(files, reverse=True)[:3]:
        try:
            with open(p, "rb") as f:
                size = os.path.getsize(p)
                if size > max_bytes:
                    f.seek(size - max_bytes)
                text = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        for line in reversed(text.splitlines()):
            m = re.search(r"New TCP Client .*?connect_addr: (\d{1,3}(?:\.\d{1,3}){3}:\d{1,5})", line)
            if m and m.group(1) not in info.public_addresses:
                info.public_addresses.append(m.group(1))
                if not info.last_seen:
                    info.last_seen = line[:19].replace("T", " ")
            m = re.search(r"timed out connecting to local TCP server.*?origin_addr=(\d{1,3}(?:\.\d{1,3}){3}:\d{1,5})", line)
            if m and m.group(1) not in info.origin_errors:
                info.origin_errors.append(m.group(1))
            if not info.api_error and "Failed to load agent data" in line:
                info.api_error = "playitサービスがplayit.ggと通信できなかった記録があります (一時的な障害の可能性)"
    info.public_addresses = info.public_addresses[:5]
    info.origin_errors = info.origin_errors[:5]
    return info


class TunnelProcess:
    """トンネル用プロセス (出力を監視してアドレスを拾う)."""

    def __init__(self, name: str, on_line: Callable[[str], None] | None = None):
        self.name = name
        self.proc: subprocess.Popen | None = None
        self.address = ""
        self.on_line = on_line
        self.on_exit: Callable[[int], None] | None = None
        self.lines: list[str] = []
        self._expected_stop = False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, args: list[str], cwd: str | None = None) -> None:
        if self.running:
            return
        self._expected_stop = False
        self.proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc and self.proc.stdout
        for raw in self.proc.stdout:
            line = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw.decode("utf-8", "replace")).rstrip()
            if not line:
                continue
            self.lines.append(line)
            self.lines = self.lines[-500:]
            m = ADDRESS_RE.search(line)
            if m and "playit.gg/" not in line[m.start(): m.end() + 1]:
                self.address = m.group(0)
            if self.on_line:
                self.on_line(line)
        code = self.proc.wait()
        if self.on_exit and not self._expected_stop:
            self.on_exit(code)

    def stop(self) -> None:
        self._expected_stop = True
        if self.running:
            try:
                self.proc.terminate()  # type: ignore[union-attr]
                self.proc.wait(5)  # type: ignore[union-attr]
            except Exception:
                try:
                    self.proc.kill()  # type: ignore[union-attr]
                except Exception:
                    pass


class PlayitController:
    """playitの起動・停止・状態確認."""

    def __init__(self) -> None:
        self.proc = TunnelProcess("playit")

    def start(self) -> str:
        st = playit_status()
        if not st.installed:
            raise RuntimeError("playitがインストールされていません。「友達を招待」画面の案内から導入してください。")
        if st.service_mode:
            if not st.service_running:
                r = run_hidden([st.path, "start"], timeout=30)
                if r.returncode != 0:
                    msg = (r.stdout + r.stderr).decode("utf-8", "replace").strip()
                    raise RuntimeError(f"playitサービスを起動できませんでした (管理者権限が必要な場合があります)。\n{msg}")
            # ログを受け取るためにattachする (アドレスが出力されれば自動取得)
            self.proc.start([st.path, "attach", "--stdout"])
            return "playitサービスを使用しています"
        self.proc.start([st.path, "--stdout"])
        return "playitエージェントを起動しました"

    def stop(self, stop_service: bool = False) -> None:
        self.proc.stop()
        if stop_service:
            st = playit_status()
            if st.service_mode and st.service_running:
                run_hidden([st.path, "stop"], timeout=30)

    def healthy(self) -> bool:
        st = playit_status()
        if st.service_mode:
            return st.service_running
        return self.proc.running


# ---------------------------------------------------------------- 直接公開
def global_ip() -> str:
    """グローバルIPを外部サービス (ipify) に問い合わせる。ユーザー操作時のみ呼ぶこと."""
    return net.get_bytes("https://api.ipify.org", timeout=10).decode().strip()


def external_check(ip: str, port: int) -> dict:
    """mcsrvstat.us を使った外部からの接続テスト (ユーザーの同意後にのみ呼ぶ)."""
    data = net.get_json(f"https://api.mcsrvstat.us/3/{ip}:{port}", timeout=20)
    return {"online": bool(data.get("online")), "version": (data.get("version") or ""),
            "players": data.get("players", {})}


def firewall_commands(port: int, java_path: str = "") -> str:
    lines = [
        "■ Windowsファイアウォールの許可 (管理者として実行したPowerShell/コマンドプロンプトで実行)",
        f'netsh advfirewall firewall add rule name="Minecraft Server TCP {port}" dir=in action=allow protocol=TCP localport={port}',
    ]
    if java_path:
        lines.append(f'netsh advfirewall firewall add rule name="Minecraft Server Java" dir=in action=allow program="{java_path}"')
    lines += [
        "",
        "■ ルーターのポート開放 (機種ごとに画面が異なります)",
        "  1. ブラウザでルーターの管理画面を開く (例: http://192.168.1.1 / http://192.168.0.1)",
        "  2. 「ポート開放」「ポートフォワーディング」「静的IPマスカレード」などの設定を探す",
        f"  3. プロトコル: TCP / ポート: {port} / 転送先: このPCのIPアドレス ({', '.join(ports.local_ipv4_addresses()[:1]) or '下記LANアドレス'})",
        "  4. 設定後、「接続テスト」で外部から接続できるか確認",
        "",
        "※ 本アプリはファイアウォールやルーターの設定を自動では変更しません。",
        "※ 携帯回線・マンション共用回線 (CGNAT) ではポート開放できない場合があります。その場合はplayit.ggなどを使ってください。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- カスタム
class CustomTunnel:
    def __init__(self) -> None:
        self.proc = TunnelProcess("custom")

    def start(self, exe: str, args: str, cwd: str = "") -> None:
        if not exe:
            return
        if not os.path.isfile(exe):
            raise RuntimeError(f"実行ファイルが見つかりません: {exe}")
        self.proc.start([exe, *shlex.split(args, posix=False)], cwd or os.path.dirname(exe))

    def stop(self) -> None:
        self.proc.stop()


def join_addresses(server, public_address: str = "") -> dict:
    port = server.port
    suffix = "" if port == 25565 else f":{port}"
    out = {"localhost": f"localhost{suffix}", "lan": [f"{ip}{suffix}" for ip in ports.local_ipv4_addresses()]}
    if public_address:
        out["public"] = public_address
    return out
