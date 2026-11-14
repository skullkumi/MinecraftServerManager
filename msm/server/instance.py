"""サーバー登録情報 (DB) と新規作成・既存フォルダー登録."""
from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from msm.core.db import get_db
from msm.core.util import CancelToken, log
from msm.server import providers
from msm.server.properties import read_properties, write_properties

PUBLISH_METHODS = {"lan": "LAN限定", "playit": "playit.gg", "direct": "直接公開 (ポート開放)", "custom": "その他のトンネル/VPN"}


@dataclass
class Server:
    id: str
    name: str
    directory: str
    mc_version: str
    server_type: str
    loader_version: str = ""
    java_mode: str = "auto"
    java_path: str = ""
    min_mem_mb: int = 1024
    max_mem_mb: int = 4096
    port: int = 25565
    max_players: int = 10
    jvm_args: str = ""
    launch: dict = field(default_factory=dict)
    publish_method: str = "lan"
    publish_config: dict = field(default_factory=dict)
    auto_backup_minutes: int = 0
    backup_keep: int = 10
    created_at: float = 0.0
    notes: str = ""
    options: dict = field(default_factory=dict)  # 自動再起動・スケジュール・通知など

    @property
    def path(self) -> Path:
        return Path(self.directory)

    @property
    def type_label(self) -> str:
        return providers.TYPE_LABELS.get(self.server_type, self.server_type)

    @property
    def is_modded(self) -> bool:
        return self.server_type in providers.MODDED_TYPES

    @property
    def mods_dir(self) -> Path:
        return self.path / ("plugins" if self.server_type in providers.PLUGIN_TYPES else "mods")

    def level_name(self) -> str:
        return read_properties(self.path / "server.properties").get("level-name", "world") or "world"


def _from_row(r: dict) -> Server:
    d = dict(r)
    d["launch"] = json.loads(d.get("launch") or "{}")
    d["publish_config"] = json.loads(d.get("publish_config") or "{}")
    d["options"] = json.loads(d.get("options") or "{}")
    return Server(**d)


def list_servers() -> list[Server]:
    return [_from_row(r) for r in get_db().query("SELECT * FROM servers ORDER BY created_at")]


def get_server(server_id: str) -> Server | None:
    rows = get_db().query("SELECT * FROM servers WHERE id=?", (server_id,))
    return _from_row(rows[0]) if rows else None


def save_server(s: Server) -> None:
    d = asdict(s)
    d["launch"] = json.dumps(s.launch, ensure_ascii=False)
    d["publish_config"] = json.dumps(s.publish_config, ensure_ascii=False)
    d["options"] = json.dumps(s.options, ensure_ascii=False)
    cols = ",".join(d.keys())
    qs = ",".join("?" for _ in d)
    get_db().execute(f"INSERT OR REPLACE INTO servers({cols}) VALUES({qs})", list(d.values()))
    # フォルダー側にも控えを残す (アプリ再インストール時に再登録できるように)
    try:
        meta = s.path / ".msm"
        meta.mkdir(parents=True, exist_ok=True)
        (meta / "server.json").write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), "utf-8")
    except OSError as e:
        log.warning("server.json保存失敗: %s", e)


def unregister_server(server_id: str) -> None:
    """登録解除のみ。サーバーフォルダーは削除しない."""
    get_db().execute("DELETE FROM servers WHERE id=?", (server_id,))


def port_conflicts(port: int, exclude_id: str | None = None) -> list[Server]:
    return [s for s in list_servers() if s.port == port and s.id != exclude_id]


def suggest_port(start: int = 25565) -> int:
    """登録済みサーバーと、今ほかのプログラムが使っているポートを避けて提案する."""
    from msm.server import ports

    used = {s.port for s in list_servers()}
    p = start
    while p in used or not ports.is_port_free(p):
        p += 1
        if p > 65535:
            return start
    return p


def write_eula(server_dir: Path, accepted: bool) -> None:
    (Path(server_dir) / "eula.txt").write_text(
        "#By changing the setting below to TRUE you are indicating your agreement to our EULA "
        "(https://aka.ms/MinecraftEULA).\n"
        f"#Set by Minecraft Server Manager at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"eula={'true' if accepted else 'false'}\n",
        "utf-8",
    )


def eula_accepted(server_dir: Path) -> bool:
    p = Path(server_dir) / "eula.txt"
    if not p.exists():
        return False
    for line in p.read_text("utf-8", "replace").splitlines():
        if line.strip().lower().replace(" ", "") == "eula=true":
            return True
    return False


@dataclass
class CreateRequest:
    name: str
    directory: str
    mc_version: str
    server_type: str
    loader_version: str = ""
    min_mem_mb: int = 1024
    max_mem_mb: int = 4096
    max_players: int = 10
    port: int = 25565
    java_mode: str = "auto"
    java_path: str = ""
    eula: bool = False
    motd: str = ""


def create_server(req: CreateRequest, java_exe: str | None, progress, log_fn, cancel: CancelToken | None = None) -> Server:
    """新規サーバーを作成する。フォルダーが空でない場合は呼び出し側で確認済みであること."""
    d = Path(req.directory)
    d.mkdir(parents=True, exist_ok=True)
    provider = providers.get_provider(req.server_type)
    progress(f"{provider.label} を準備しています...", None)
    launch = provider.install(d, req.mc_version, req.loader_version, java_exe, progress, log_fn, cancel)
    loader = req.loader_version
    if req.server_type in ("paper", "purpur") and launch.get("build"):
        loader = str(launch["build"])
    if not loader and provider.needs_loader:
        try:
            loader = next((l.version for l in provider.list_loader_versions(req.mc_version) if l.recommended), "")
        except Exception:
            loader = ""
    props = {
        "server-port": str(req.port),
        "max-players": str(req.max_players),
        "motd": req.motd or req.name,
    }
    write_properties(d / "server.properties", props)
    write_eula(d, req.eula)
    if req.server_type in providers.MODDED_TYPES:
        (d / "mods").mkdir(exist_ok=True)
    s = Server(
        id=uuid.uuid4().hex[:12], name=req.name, directory=str(d), mc_version=req.mc_version,
        server_type=req.server_type, loader_version=loader, java_mode=req.java_mode, java_path=req.java_path,
        min_mem_mb=req.min_mem_mb, max_mem_mb=req.max_mem_mb, port=req.port, max_players=req.max_players,
        launch=launch, created_at=time.time(),
    )
    save_server(s)
    progress("完了", 1.0)
    return s


def guess_existing(directory: Path) -> dict:
    """既存サーバーフォルダーの種類・バージョンを推定する (登録用)."""
    d = Path(directory)
    meta = d / ".msm" / "server.json"
    if meta.exists():
        try:
            return json.loads(meta.read_text("utf-8"))
        except Exception:
            pass
    info = {"server_type": "vanilla", "mc_version": "", "loader_version": ""}
    launch = providers.detect_launch(d)
    info["launch"] = launch
    joined = " ".join(launch.get("args", [])) + " " + launch.get("jar", "")
    import re

    m = re.search(r"neoforged/neoforge/([\w.\-]+)/", joined)
    if m:
        info.update(server_type="neoforge", loader_version=m.group(1),
                    mc_version=providers.neoforge_mc_version(m.group(1)) or "")
        return info
    m = re.search(r"minecraftforge/forge/([\d.]+)-([\w.\-]+)/", joined) or re.search(r"forge-([\d.]+)-([\w.\-]+?)(?:-shim|-universal)?\.jar", joined)
    if m:
        info.update(server_type="forge", mc_version=m.group(1), loader_version=m.group(2))
        return info
    if "fabric-server-launch" in joined:
        info["server_type"] = "fabric"
        props = d / ".fabric" / "server"
        if props.exists():
            jars = sorted(props.glob("*-server.jar"))
            if jars:
                info["mc_version"] = jars[-1].name.split("-")[0]
        return info
    m = re.search(r"(paper|purpur)-([\w.\-]+)-(\d+)\.jar", joined)
    if m:
        info.update(server_type=m.group(1), mc_version=m.group(2), loader_version=m.group(3))
        return info
    if "quilt-server-launch" in joined:
        info["server_type"] = "quilt"
        return info
    vj = d / "version.json"
    if vj.exists():
        try:
            info["mc_version"] = json.loads(vj.read_text("utf-8")).get("id", "")
        except Exception:
            pass
    return info


def register_existing(name: str, directory: str, mc_version: str, server_type: str, loader_version: str,
                      launch: dict, max_mem_mb: int = 4096) -> Server:
    d = Path(directory)
    props = read_properties(d / "server.properties")
    port = int(props.get("server-port", "25565") or 25565)
    s = Server(
        id=uuid.uuid4().hex[:12], name=name, directory=str(d), mc_version=mc_version, server_type=server_type,
        loader_version=loader_version, port=port, max_players=int(props.get("max-players", "20") or 20),
        launch=launch, max_mem_mb=max_mem_mb, created_at=time.time(),
    )
    save_server(s)
    return s


def folder_is_empty(directory: str) -> bool:
    p = Path(directory)
    return not p.exists() or not any(p.iterdir())


def move_folder_safe(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
