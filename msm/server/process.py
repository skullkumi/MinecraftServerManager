"""サーバープロセスの起動・停止・状態管理."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from msm.core.paths import logs_dir
from msm.core.util import CREATE_NO_WINDOW, log
from msm.server import ports
from msm.server.instance import Server, eula_accepted

STOPPED, STARTING, RUNNING, STOPPING, CRASHED = "stopped", "starting", "running", "stopping", "crashed"
STATE_LABELS = {STOPPED: "停止中", STARTING: "起動中", RUNNING: "稼働中", STOPPING: "停止処理中", CRASHED: "異常終了"}

DONE_RE = re.compile(r'Done \([\d.,]+s\)! For help, type')
JOIN_RE = re.compile(r"\]:\s(?:\[[^\]]*\]\s)?([A-Za-z0-9_]{2,16}) joined the game")
LEAVE_RE = re.compile(r"\]:\s(?:\[[^\]]*\]\s)?([A-Za-z0-9_]{2,16}) left the game")
LIST_RE = re.compile(r"There are (\d+) of a max(?: of)? (\d+) players online:?\s*(.*)$")
ENCODING_ARGS = [
    "-Dfile.encoding=UTF-8", "-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8",
    "-Dsun.stdout.encoding=UTF-8", "-Dsun.stderr.encoding=UTF-8",
]


class StartError(Exception):
    pass


class EulaNotAccepted(StartError):
    pass


def build_command(server: Server, java_exe: str) -> list[str]:
    cmd = [java_exe]
    launch = server.launch or {"kind": "jar", "jar": "server.jar"}
    jvm_user = shlex.split(server.jvm_args, posix=False) if server.jvm_args.strip() else []
    if launch.get("kind") == "args":
        # @user_jvm_args.txt などはそのまま先頭に置き、メモリ指定はその後ろ (後勝ち) に置く
        args = list(launch.get("args", []))
        pre = [a for a in args if a.startswith("@") and "user_jvm_args" in a]
        rest = [a for a in args if a not in pre]
        cmd += pre
        cmd += [f"-Xms{server.min_mem_mb}M", f"-Xmx{server.max_mem_mb}M", *ENCODING_ARGS, *jvm_user]
        cmd += rest
    else:
        cmd += [f"-Xms{server.min_mem_mb}M", f"-Xmx{server.max_mem_mb}M", *ENCODING_ARGS, *jvm_user]
        cmd += ["-jar", launch.get("jar", "server.jar")]
    cmd.append("nogui")
    return cmd


class ServerProcess:
    """1台のサーバープロセス。イベントはコールバックで通知する (GUIスレッドとは別スレッドから呼ばれる)."""

    def __init__(self, server: Server):
        self.server = server
        self.proc: subprocess.Popen | None = None
        self.state = STOPPED
        self.lines: deque[str] = deque(maxlen=5000)
        self.players: set[str] = set()
        self.max_players: int = server.max_players
        self.started_at: float | None = None
        self.ready_at: float | None = None
        self.exit_code: int | None = None
        self.java_exe = ""
        self.on_line: list[Callable[[str], None]] = []
        self.on_state: list[Callable[[str], None]] = []
        self.on_players: list[Callable[[set], None]] = []
        self._stop_requested = False
        self._crash_seen = False
        self._console_file = None
        self._lock = threading.Lock()
        self._line_waiters: list[tuple[re.Pattern, threading.Event, list]] = []

    # ---- 状態 ----
    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.running else None

    def uptime(self) -> float:
        return time.time() - self.started_at if self.started_at and self.running else 0.0

    def _set_state(self, st: str) -> None:
        self.state = st
        for cb in list(self.on_state):
            try:
                cb(st)
            except Exception:  # noqa: BLE001
                log.exception("state callback")

    def _emit(self, line: str) -> None:
        self.lines.append(line)
        if self._console_file:
            try:
                self._console_file.write(line + "\n")
                self._console_file.flush()
            except Exception:
                pass
        changed = False
        if "Stopping server" in line or "Stopping the server" in line:
            self._stop_requested = True  # コンソールから手動でstopした場合も正常停止扱い
        if "crash report" in line.lower() and ("saved to" in line.lower() or "has been saved" in line.lower()):
            self._crash_seen = True
        if self.state == STARTING and DONE_RE.search(line):
            self.ready_at = time.time()
            self._set_state(RUNNING)
        m = JOIN_RE.search(line)
        if m:
            self.players.add(m.group(1))
            changed = True
        m = LEAVE_RE.search(line)
        if m:
            self.players.discard(m.group(1))
            changed = True
        m = LIST_RE.search(line)
        if m:
            self.max_players = int(m.group(2))
            names = [n.strip() for n in m.group(3).split(",") if n.strip()]
            self.players = set(names)
            changed = True
        for pat, ev, store in list(self._line_waiters):
            mm = pat.search(line)
            if mm:
                store.append(mm)
                ev.set()
        for cb in list(self.on_line):
            try:
                cb(line)
            except Exception:  # noqa: BLE001
                log.exception("line callback")
        if changed:
            for cb in list(self.on_players):
                try:
                    cb(set(self.players))
                except Exception:  # noqa: BLE001
                    pass

    def wait_for_line(self, pattern: str, timeout: float, send: str | None = None):
        """指定パターンの出力を待つ。sendを指定するとコマンド送信後に待つ。一致したMatchを返す."""
        ev = threading.Event()
        store: list = []
        entry = (re.compile(pattern), ev, store)
        self._line_waiters.append(entry)
        try:
            if send:
                self.send_command(send)
            ev.wait(timeout)
            return store[0] if store else None
        finally:
            self._line_waiters.remove(entry)

    # ---- 操作 ----
    def start(self, java_exe: str) -> None:
        with self._lock:
            if self.running:
                return
            s = self.server
            if not s.path.exists():
                raise StartError(f"サーバーフォルダーが見つかりません: {s.path}")
            if not eula_accepted(s.path):
                raise EulaNotAccepted("Minecraft EULAに同意していません。")
            launch = s.launch or {}
            if launch.get("kind", "jar") == "jar" and not (s.path / launch.get("jar", "server.jar")).exists():
                raise StartError(f"起動ファイルが見つかりません: {launch.get('jar', 'server.jar')}")
            if not ports.is_port_free(s.port):
                owner = ports.port_owner(s.port)
                raise StartError(f"ポート {s.port} は既に使用されています{(' (' + owner + ')') if owner else ''}。"
                                 "他のサーバーを停止するか、ポート番号を変更してください。")
            cmd = build_command(s, java_exe)
            self.java_exe = java_exe
            cdir = logs_dir() / "servers" / s.id
            cdir.mkdir(parents=True, exist_ok=True)
            self._console_file = open(cdir / f"console-{time.strftime('%Y%m%d-%H%M%S')}.log", "w", encoding="utf-8")
            self._stop_requested = False
            self._crash_seen = False
            self.players.clear()
            self.exit_code = None
            self.ready_at = None
            self.lines.clear()
            self._emit(f"[Manager] 起動コマンド: {subprocess.list2cmdline(cmd)}")
            env = dict(os.environ)
            env["JAVA_HOME"] = str(Path(java_exe).parent.parent)  # 子プロセスのみ。システムは変更しない
            self.proc = subprocess.Popen(
                cmd, cwd=str(s.path), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW, env=env,
            )
            self.started_at = time.time()
            self._set_state(STARTING)
            self._reader_thread = threading.Thread(target=self._reader, daemon=True, name=f"srv-{s.id}")
            self._reader_thread.start()

    def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            self._emit(line)
        code = self.proc.wait()
        self.exit_code = code
        self.players.clear()
        crashed = (not self._stop_requested) or self._crash_seen
        self._emit(f"[Manager] サーバーが終了しました (終了コード: {code})")
        if self._console_file:
            self._console_file.close()
            self._console_file = None
        self._set_state(CRASHED if crashed else STOPPED)

    def send_command(self, command: str) -> bool:
        if not self.running or not self.proc or not self.proc.stdin:
            return False
        try:
            self.proc.stdin.write((command.strip() + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            return True
        except OSError:
            return False

    def stop(self, timeout: float = 90) -> bool:
        """stopコマンドで安全に停止する。timeout内に止まればTrue."""
        if not self.running:
            return True
        self._stop_requested = True
        self._set_state(STOPPING)
        self.send_command("stop")
        try:
            self.proc.wait(timeout)  # type: ignore[union-attr]
        except subprocess.TimeoutExpired:
            return False
        t = getattr(self, "_reader_thread", None)
        if t is not None and t is not threading.current_thread():
            t.join(10)  # 終了処理 (状態更新) が終わるまで待つ
        return True

    def kill(self) -> None:
        """強制終了 (データ破損の恐れがあるため確認後に呼ぶこと)."""
        if not self.proc:
            return
        self._stop_requested = True
        try:
            import psutil

            parent = psutil.Process(self.proc.pid)
            for c in parent.children(recursive=True):
                c.kill()
            parent.kill()
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    def stats(self) -> dict:
        """CPU・メモリ使用量 (psutil)."""
        out = {"cpu": None, "rss": None}
        if not self.running:
            return out
        try:
            import psutil

            # cpu_percent は前回呼び出しからの差分で計算するため、Processオブジェクトを使い回す
            cache = getattr(self, "_ps_cache", None)
            if cache is None or cache.get("pid") != self.proc.pid:  # type: ignore[union-attr]
                cache = {"pid": self.proc.pid, "procs": {}}  # type: ignore[union-attr]
                self._ps_cache = cache
            root = cache["procs"].get(self.proc.pid) or psutil.Process(self.proc.pid)  # type: ignore[union-attr]
            procs = {self.proc.pid: root}  # type: ignore[union-attr]
            for ch in root.children(recursive=True):
                procs[ch.pid] = cache["procs"].get(ch.pid) or ch
            cache["procs"] = procs
            out["rss"] = sum(x.memory_info().rss for x in procs.values())
            out["cpu"] = sum(x.cpu_percent(None) for x in procs.values()) / (psutil.cpu_count() or 1)
        except Exception:
            pass
        return out


class ProcessRegistry:
    """全サーバーのプロセスを一元管理する."""

    def __init__(self) -> None:
        self._procs: dict[str, ServerProcess] = {}

    def get(self, server: Server) -> ServerProcess:
        sp = self._procs.get(server.id)
        if sp is None:
            sp = ServerProcess(server)
            self._procs[server.id] = sp
        elif not sp.running:
            sp.server = server  # 設定変更を反映
        return sp

    def find(self, server_id: str) -> ServerProcess | None:
        return self._procs.get(server_id)

    def running(self) -> list[ServerProcess]:
        return [p for p in self._procs.values() if p.running]

    def state_of(self, server_id: str) -> str:
        p = self._procs.get(server_id)
        if p is None:
            return STOPPED
        if not p.running and p.state in (STARTING, RUNNING, STOPPING):
            return STOPPED
        return p.state


registry = ProcessRegistry()
