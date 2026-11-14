"""プレイヤー管理 (コンソールコマンド経由) と各種リストファイルの読み込み."""
from __future__ import annotations

import json
import re
from pathlib import Path

from msm.server.process import ServerProcess

NAME_RE = re.compile(r"^[A-Za-z0-9_]{2,16}$")


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))


def _read_json_list(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def whitelist(server_dir: Path) -> list[str]:
    return [e.get("name", "") for e in _read_json_list(Path(server_dir) / "whitelist.json")]


def ops(server_dir: Path) -> list[str]:
    return [e.get("name", "") for e in _read_json_list(Path(server_dir) / "ops.json")]


def banned(server_dir: Path) -> list[dict]:
    return _read_json_list(Path(server_dir) / "banned-players.json")


COMMANDS = {
    "kick": ("kick {name} {reason}", "キック"),
    "ban": ("ban {name} {reason}", "BAN"),
    "pardon": ("pardon {name}", "BAN解除"),
    "op": ("op {name}", "OP付与"),
    "deop": ("deop {name}", "OP解除"),
    "whitelist_add": ("whitelist add {name}", "ホワイトリスト追加"),
    "whitelist_remove": ("whitelist remove {name}", "ホワイトリスト削除"),
}


def run_action(sp: ServerProcess, action: str, name: str, reason: str = "") -> str:
    if not valid_name(name):
        raise ValueError("プレイヤー名は英数字と_の2～16文字で入力してください")
    if not sp.running:
        raise RuntimeError("サーバーが起動しているときだけ操作できます")
    tmpl, label = COMMANDS[action]
    cmd = tmpl.format(name=name, reason=reason.replace("\n", " ")).strip()
    sp.send_command(cmd)
    return f"{label}: {name} (コマンド: {cmd})"


def refresh_list(sp: ServerProcess) -> None:
    sp.send_command("list")
