"""server.properties の読み書き (コメント・未知の項目・順序を保持する) と日本語説明."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def read_properties(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text("utf-8", "replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!") or "=" not in s:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = _unescape(v)
    return out


def _unescape(v: str) -> str:
    return v.replace("\\:", ":").replace("\\=", "=").replace("\\\\", "\\")


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace("\n", "\\n")


def write_properties(path: Path, updates: dict[str, str]) -> None:
    """既存の行を保持したまま値だけ書き換える。無いキーは末尾に追加する."""
    path = Path(path)
    lines = path.read_text("utf-8", "replace").splitlines() if path.exists() else [
        "#Minecraft server properties"
    ]
    done = set()
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = line.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={_escape(str(updates[k]))}")
                done.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in done:
            out.append(f"{k}={_escape(str(v))}")
    tmp = path.with_suffix(".properties.tmp")
    tmp.write_text("\n".join(out) + "\n", "utf-8")
    tmp.replace(path)


@dataclass
class PropDef:
    key: str
    label: str
    kind: str  # bool / int / str / choice
    default: str
    help: str
    choices: tuple = ()
    min: int | None = None
    max: int | None = None
    danger_value: str | None = None
    danger_text: str = ""


PROPERTY_DEFS: list[PropDef] = [
    PropDef("motd", "サーバー説明文", "str", "A Minecraft Server",
            "サーバー一覧に表示される説明文です。§で色を付けられます。"),
    PropDef("gamemode", "ゲームモード", "choice", "survival",
            "新しく参加したプレイヤーのゲームモードです。",
            (("survival", "サバイバル"), ("creative", "クリエイティブ"), ("adventure", "アドベンチャー"), ("spectator", "スペクテイター"))),
    PropDef("difficulty", "難易度", "choice", "easy", "ワールドの難易度です。",
            (("peaceful", "ピースフル"), ("easy", "イージー"), ("normal", "ノーマル"), ("hard", "ハード"))),
    PropDef("max-players", "最大人数", "int", "20", "同時に接続できる最大人数です。", min=1, max=1000),
    PropDef("pvp", "PvP (プレイヤー同士の攻撃)", "bool", "true", "オンにするとプレイヤー同士で攻撃できます。"),
    PropDef("online-mode", "オンライン認証", "bool", "true",
            "Mojang/Microsoftアカウントで本人確認します。通常は必ずオンにしてください。",
            danger_value="false",
            danger_text="プレイヤーの認証が無効になります。\nなりすましや不正ログインの危険があります。\n"
                        "外部に公開するサーバーでは絶対にオフにしないでください。"),
    PropDef("white-list", "ホワイトリスト", "bool", "false",
            "オンにすると、許可したプレイヤーだけが参加できます。外部公開時はオンを強く推奨します。"),
    PropDef("enforce-whitelist", "ホワイトリストを強制", "bool", "false",
            "オンにすると、ホワイトリストから外したプレイヤーを即座に切断します。"),
    PropDef("enable-command-block", "コマンドブロック", "bool", "false",
            "コマンドブロックを使えるようにします。", danger_value="true",
            danger_text="コマンドブロックはOP権限を持つ人が強力な操作に使えます。信頼できる人だけにOPを与えてください。"),
    PropDef("allow-flight", "飛行許可", "bool", "false",
            "サバイバルでの飛行を許可します (MODで飛ぶ場合はオン推奨。オフだと「飛行」でキックされることがあります)。"),
    PropDef("hardcore", "ハードコア", "bool", "false",
            "死亡するとスペクテイターになり復活できません。", danger_value="true",
            danger_text="ハードコアは死亡すると復活できません。既存ワールドに適用すると取り消しが困難です。"),
    PropDef("view-distance", "描画距離 (チャンク)", "int", "10",
            "サーバーが送るチャンクの範囲です。大きいほど重くなります (推奨: 6～12)。", min=3, max=32),
    PropDef("simulation-distance", "シミュレーション距離", "int", "10",
            "動物や作物が動作する範囲です。大きいほど重くなります (推奨: 5～10)。", min=3, max=32),
    PropDef("spawn-protection", "スポーン保護 (ブロック)", "int", "16",
            "初期スポーン地点の周囲をOP以外が壊せない範囲です。0で無効。", min=0, max=1000),
    PropDef("level-name", "ワールド名 (フォルダー名)", "str", "world",
            "使用するワールドのフォルダー名です。変更すると別のワールドが使われます (元のワールドは消えません)。"),
    PropDef("level-seed", "シード", "str", "",
            "新しいワールドを作るときのシード値です。既存ワールドには影響しません。空欄でランダム。"),
    PropDef("server-port", "ポート番号", "int", "25565",
            "接続に使うポートです。複数サーバーを同時に動かす場合は別々の番号にしてください。", min=1, max=65535),
    PropDef("enable-rcon", "RCON (リモート管理)", "bool", "false",
            "外部ツールから管理コマンドを送る機能です。本アプリは不要です。", danger_value="true",
            danger_text="RCONを有効にする場合は推測されにくいパスワードを設定し、RCONポートを外部公開しないでください。"),
]

PROP_BY_KEY = {p.key: p for p in PROPERTY_DEFS}


def danger_warnings(updates: dict[str, str]) -> list[str]:
    out = []
    for k, v in updates.items():
        d = PROP_BY_KEY.get(k)
        if d and d.danger_value is not None and str(v).lower() == d.danger_value:
            out.append(f"【{d.label}: {v}】\n{d.danger_text}")
    return out
