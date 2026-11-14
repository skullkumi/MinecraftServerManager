"""アプリ全体の状態 (選択中のサーバー・プロセスイベントの中継・トンネル)."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal

from msm.publish.tunnels import CustomTunnel, PlayitController
from msm.server import instance
from msm.server.instance import Server
from msm.server.process import ServerProcess, registry


class AppContext(QObject):
    servers_changed = Signal()
    current_changed = Signal(str)
    state_changed = Signal(str, str)  # server_id, state
    console_line = Signal(str, str)  # server_id, line
    players_changed = Signal(str)
    tunnel_line = Signal(str)
    navigate = Signal(str)  # ページキー
    status = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._current: str | None = None
        self._attached: set[int] = set()
        self.playit = PlayitController()
        self.playit.proc.on_line = lambda l: self.tunnel_line.emit(l)
        self.custom_tunnels: dict[str, CustomTunnel] = {}
        self.java_cache: dict[str, str] = {}
        self.playit_status = None  # PlayitStatus のキャッシュ (定期的にバックグラウンドで更新)
        servers = instance.list_servers()
        if servers:
            self._current = servers[0].id
        # 異常終了でUIが止まらないよう、シグナルはキュー接続で受ける
        self.state_changed.connect(self._on_state, Qt.QueuedConnection)

    # ---- サーバー ----
    def servers(self) -> list[Server]:
        return instance.list_servers()

    def current(self) -> Server | None:
        if not self._current:
            return None
        return instance.get_server(self._current)

    def set_current(self, server_id: str | None) -> None:
        if server_id != self._current:
            self._current = server_id
            self.current_changed.emit(server_id or "")

    def notify_servers_changed(self) -> None:
        if self._current and instance.get_server(self._current) is None:
            ss = instance.list_servers()
            self._current = ss[0].id if ss else None
            self.current_changed.emit(self._current or "")
        self.servers_changed.emit()

    def process(self, server: Server) -> ServerProcess:
        sp = registry.get(server)
        self.attach(sp)
        return sp

    def attach(self, sp: ServerProcess) -> None:
        if id(sp) in self._attached:
            return
        self._attached.add(id(sp))
        sid = sp.server.id
        sp.on_line.append(lambda line: self.console_line.emit(sid, line))
        sp.on_state.append(lambda st: self.state_changed.emit(sid, st))
        sp.on_players.append(lambda _p: self.players_changed.emit(sid))

    def state_of(self, server_id: str) -> str:
        return registry.state_of(server_id)

    def _on_state(self, sid: str, st: str) -> None:
        # サーバー停止時のトンネル制御
        if st in ("stopped", "crashed"):
            s = instance.get_server(sid)
            if not s:
                return
            cfg = s.publish_config or {}
            if cfg.get("stop_tunnel_with_server", True):
                if s.publish_method == "playit" and not any(
                        p.running for p in registry.running() if p.server.publish_method == "playit"):
                    self.playit.stop(stop_service=bool(cfg.get("stop_playit_service", False)))
                if s.publish_method == "custom" and sid in self.custom_tunnels:
                    self.custom_tunnels[sid].stop()

    # ---- 公開 ----
    def custom_tunnel(self, server_id: str) -> CustomTunnel:
        t = self.custom_tunnels.get(server_id)
        if t is None:
            t = CustomTunnel()
            t.proc.on_line = lambda l: self.tunnel_line.emit(l)
            self.custom_tunnels[server_id] = t
        return t

    def public_address(self, server: Server) -> str:
        cfg = server.publish_config or {}
        if server.publish_method == "playit":
            return self.playit.proc.address or cfg.get("playit_address", "")
        if server.publish_method == "custom":
            t = self.custom_tunnels.get(server.id)
            return (t.proc.address if t else "") or cfg.get("custom_address", "")
        if server.publish_method == "direct":
            ip = cfg.get("global_ip", "")
            if ip:
                return ip + ("" if server.port == 25565 else f":{server.port}")
        if server.publish_method == "lan":
            from msm.server.ports import local_ipv4_addresses

            ips = local_ipv4_addresses()
            if ips:
                return ips[0] + ("" if server.port == 25565 else f":{server.port}")
        return ""

    def tunnel_state(self, server: Server) -> str:
        m = server.publish_method
        if m == "playit":
            st = self.playit_status
            if st is not None and not st.installed:
                return "playit未インストール"
            if st is not None and st.service_mode:
                return "接続中 (playitサービス稼働中)" if st.service_running else "停止中"
            return "接続中" if self.playit.proc.running else "停止中"
        if m == "custom":
            t = self.custom_tunnels.get(server.id)
            return "起動中" if t and t.proc.running else "停止中"
        if m == "direct":
            return "ポート開放で公開 (状態は接続テストで確認)"
        return "LAN内のみ"

    def start_tunnel(self, server: Server) -> str:
        cfg = server.publish_config or {}
        if server.publish_method == "playit":
            return self.playit.start()
        if server.publish_method == "custom":
            exe = cfg.get("custom_exe", "")
            if not exe:
                return "トンネルのコマンドが設定されていません"
            self.custom_tunnel(server.id).start(exe, cfg.get("custom_args", ""), cfg.get("custom_cwd", ""))
            return "トンネルを起動しました"
        return ""
