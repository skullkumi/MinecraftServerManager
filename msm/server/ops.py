"""サーバー操作の組み合わせ処理 (Java解決 → 起動、安全なバックアップなど)."""
from __future__ import annotations

from msm.backup import backup as bk
from msm.core.util import log
from msm.java import manager as jm
from msm.server.instance import Server, port_conflicts
from msm.server.process import ServerProcess, StartError, registry


def check_port_conflicts(server: Server) -> list[str]:
    msgs = []
    for other in port_conflicts(server.port, server.id):
        sp = registry.find(other.id)
        running = bool(sp and sp.running)
        msgs.append(f"「{other.name}」もポート {server.port} を使用しています" + ("（稼働中）" if running else ""))
    return msgs


def start(server: Server) -> tuple[ServerProcess, str]:
    java, req, note = jm.resolve_for_server(server)
    if java is None:
        raise StartError(note + "\nアプリ設定の「Java」タブから必要なJavaを導入してください。")
    for other in port_conflicts(server.port, server.id):
        sp = registry.find(other.id)
        if sp and sp.running:
            raise StartError(f"ポート {server.port} は稼働中の「{other.name}」が使用しています。"
                             "どちらかのポート番号を変更してください。")
    sp = registry.get(server)
    sp.start(java.path)
    return sp, f"Java {java.major} ({note}) で起動しました: {java.path}"


def stop_all(timeout: float = 90) -> list[str]:
    failed = []
    for sp in registry.running():
        if not sp.stop(timeout):
            failed.append(sp.server.name)
    return failed


def backup_with_server_control(server: Server, targets: list[str], kind: str = "manual", note: str = "",
                               progress=None, cancel=None, incremental: bool = False) -> bk.BackupRecord:
    """稼働中なら save-off → save-all flush → バックアップ → save-on の順で安全にバックアップする."""
    sp = registry.find(server.id)
    if sp and sp.running and "world" in targets:
        sp.send_command("save-off")
        m = sp.wait_for_line(r"Saved the game|Saved the world|All dimensions are saved|Saving is already turned on",
                             60, send="save-all flush")
        if m is None:
            log.warning("save-allの完了を確認できませんでした。バックアップは続行します。")
        try:
            return _make(server, targets, kind, note, progress, cancel, incremental)
        finally:
            sp.send_command("save-on")
    return _make(server, targets, kind, note, progress, cancel, incremental)


def _make(server, targets, kind, note, progress, cancel, incremental):
    if incremental:
        return bk.create_snapshot(server, targets, kind, note, progress, cancel)
    return bk.create_backup(server, targets, kind, note, progress, cancel)
