"""友達用クライアントパックの作成.

サーバーと同じMOD構成を、参加する側 (クライアント) がランチャーに読み込める形式で出力する。
- Modrinth形式 (.mrpack): Modrinthで配布されているMODはダウンロードURLだけを記載 (MOD本体は同梱しない)
- CurseForge形式 (.zip): CurseForgeアプリの「インポート」で読み込める。MOD本体を overrides に同梱する
- 通常のZIP: mods / config フォルダーと導入手順
サーバーから除外したクライアント専用MOD (.msm/excluded_client_mods) も含める。サーバー専用MODは含めない。
"""
from __future__ import annotations

import json
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from msm import APP_NAME
from msm.core.util import log
from msm.mods import classify as cls
from msm.mods import modrinth_api
from msm.mods.metadata import ModMeta, read_mod
from msm.publish.tunnels import join_addresses

FORMATS = {
    "mrpack": "Modrinth形式 (.mrpack) - Modrinth App / Prism Launcher / ATLauncher など",
    "curseforge": "CurseForge形式 (.zip) - CurseForgeアプリの「インポート」",
    "zip": "通常のZIP (modsフォルダー + 導入手順) - 手動で導入する場合",
}
MRPACK_LOADER_KEYS = {"forge": "forge", "neoforge": "neoforge", "fabric": "fabric-loader", "quilt": "quilt-loader"}


@dataclass
class PackEntry:
    meta: ModMeta
    source: str  # "server" / "client-only"
    classification: cls.Classification | None = None
    modrinth_file: dict | None = None  # {"url","hashes","size","filename"}
    client_only: bool = False


@dataclass
class PackResult:
    path: str
    fmt: str
    total: int = 0
    linked: int = 0  # URLで参照 (同梱しない)
    bundled: int = 0  # 同梱
    skipped_server_only: list[str] = field(default_factory=list)
    not_included: list[str] = field(default_factory=list)  # 同梱しない設定で配布元不明
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"出力先: {self.path}", f"形式: {FORMATS[self.fmt].split(' - ')[0]}",
                 f"MOD: {self.total}個 (Modrinthから取得: {self.linked} / 同梱: {self.bundled})"]
        if self.skipped_server_only:
            lines.append(f"サーバー専用のため含めなかったMOD: {len(self.skipped_server_only)}個")
        if self.not_included:
            lines.append(f"配布元が分からず含めなかったMOD: {len(self.not_included)}個 (パック内の「含まれていないMOD.txt」に記載)")
        lines += self.warnings
        return "\n".join(lines)


def collect(server, use_modrinth: bool = True) -> list[PackEntry]:
    entries: list[PackEntry] = []
    seen: set[str] = set()
    sources = [(server.mods_dir, "server"), (server.path / ".msm" / "excluded_client_mods", "client-only")]
    for d, src in sources:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.jar"), key=lambda x: x.name.lower()):
            m = read_mod(p)
            if not m.valid_jar or m.sha1 in seen:
                continue
            seen.add(m.sha1)
            entries.append(PackEntry(m, src, client_only=(src == "client-only")))
    mr: dict = {}
    vers: dict = {}
    if use_modrinth and entries:
        try:
            mr = modrinth_api.lookup_mods([e.meta.sha1 for e in entries])
            vers = modrinth_api.versions_by_hashes([e.meta.sha1 for e in entries])
        except Exception as e:  # noqa: BLE001
            log.warning("Modrinth照会失敗: %s", e)
    log_ids = cls.client_only_ids_from_logs()
    for e in entries:
        e.classification = cls.classify(e.meta, mr.get(e.meta.sha1), log_ids)
        if e.classification.side == cls.CLIENT:
            e.client_only = True
        v = vers.get(e.meta.sha1)
        if v:
            f = next((x for x in v.get("files", []) if (x.get("hashes") or {}).get("sha1") == e.meta.sha1), None)
            if f and f.get("url"):
                e.modrinth_file = {"url": f["url"], "hashes": f.get("hashes", {}), "size": f.get("size", e.meta.size),
                                   "filename": f.get("filename", e.meta.file_name)}
    return entries


def _guide_text(server, public_address: str, fmt: str) -> str:
    addrs = join_addresses(server, public_address)
    how = {
        "mrpack": "1. Modrinth App / Prism Launcher / ATLauncher などで、このファイル (.mrpack) を「インポート」します。\n"
                  "   (Modrinth App: 左の「＋」→「From file」/ Prism Launcher: 「インスタンスを追加」→「インポート」)\n"
                  "2. 作成されたインスタンスを起動します。",
        "curseforge": "1. CurseForgeアプリを開き、Minecraft →「Create Custom Profile」の横の「Import」を押します。\n"
                      "2. このZIPファイルを選びます。\n3. 作成されたプロファイルの「Play」を押します。",
        "zip": f"1. Minecraftランチャーに {server.type_label} {server.loader_version} (Minecraft {server.mc_version}) を導入します。\n"
               "   (Forge/NeoForge/Fabricの公式サイトのインストーラーで「Install client」を選びます)\n"
               "2. ランチャーでその起動構成を一度起動して終了します。\n"
               "3. %APPDATA%\\.minecraft\\mods フォルダーに、このZIPの mods フォルダーの中身を入れます。\n"
               "   (config フォルダーがある場合は .minecraft に上書きコピーします。念のため元のフォルダーはバックアップしてください)\n"
               "4. 起動構成を起動します。",
    }[fmt]
    return (f"{server.name} に参加するためのMODパック\n作成: {time.strftime('%Y-%m-%d %H:%M')} ({APP_NAME})\n\n"
            f"Minecraft: {server.mc_version}\nローダー: {server.type_label} {server.loader_version}\n\n"
            f"■ 導入方法\n{how}\n\n■ 接続先\n"
            f"  サーバー: {addrs.get('public') or '(サーバーの管理者に聞いてください)'}\n"
            f"  マルチプレイ →「サーバーを追加」→ 上のアドレスを入力して接続します。\n")


def build(server, out_path: Path, fmt: str, entries: list[PackEntry], include_config: bool = True,
          bundle_unknown: bool = False, public_address: str = "", pack_version: str = "1.0.0",
          progress=None) -> PackResult:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res = PackResult(str(out_path), fmt)
    tmp = out_path.with_name(out_path.name + ".tmp")
    loader_key = MRPACK_LOADER_KEYS.get(server.server_type)
    if not loader_key:
        raise ValueError("このサーバーはMODサーバーではないため、クライアント用MODパックは不要です")
    files_index = []
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        missing = []
        for i, e in enumerate(entries):
            if progress:
                progress(f"パックを作成中: {e.meta.file_name}", (i + 1) / max(1, len(entries)))
            c = e.classification
            if c and c.side == cls.SERVER and c.confidence in ("確定", "高"):
                res.skipped_server_only.append(e.meta.file_name)
                continue
            res.total += 1
            if fmt == "mrpack" and e.modrinth_file:
                files_index.append({
                    "path": f"mods/{e.modrinth_file['filename']}",
                    "hashes": {k: v for k, v in e.modrinth_file["hashes"].items() if k in ("sha1", "sha512")},
                    "env": {"client": "required", "server": "unsupported" if e.client_only else "required"},
                    "downloads": [e.modrinth_file["url"]],
                    "fileSize": int(e.modrinth_file.get("size") or e.meta.size),
                })
                res.linked += 1
                continue
            if fmt == "mrpack" and not bundle_unknown:
                res.not_included.append(e.meta.file_name)
                missing.append(e)
                res.total -= 1
                continue
            prefix = "overrides/mods/" if fmt in ("mrpack", "curseforge") else "mods/"
            zf.write(e.meta.file, prefix + e.meta.file_name)
            res.bundled += 1
        if include_config and (server.path / "config").is_dir():
            prefix = "overrides/config/" if fmt in ("mrpack", "curseforge") else "config/"
            for f in (server.path / "config").rglob("*"):
                if f.is_file() and f.stat().st_size < 5 * 1024 * 1024:
                    zf.write(f, prefix + f.relative_to(server.path / "config").as_posix())
        name = f"{server.name} (クライアント用)"
        if fmt == "mrpack":
            zf.writestr("modrinth.index.json", json.dumps({
                "formatVersion": 1, "game": "minecraft", "versionId": pack_version, "name": name,
                "summary": f"{server.name} に参加するためのMODパック",
                "files": files_index,
                "dependencies": {"minecraft": server.mc_version, loader_key: server.loader_version},
            }, ensure_ascii=False, indent=2))
        elif fmt == "curseforge":
            loader_id = f"{'fabric' if server.server_type == 'fabric' else server.server_type}-{server.loader_version}"
            zf.writestr("manifest.json", json.dumps({
                "minecraft": {"version": server.mc_version, "modLoaders": [{"id": loader_id, "primary": True}]},
                "manifestType": "minecraftModpack", "manifestVersion": 1, "name": name, "version": pack_version,
                "author": APP_NAME, "files": [], "overrides": "overrides",
            }, ensure_ascii=False, indent=2))
            items = "".join(f"<li>{e.meta.display_name} ({e.meta.file_name})</li>" for e in entries)
            zf.writestr("modlist.html", f"<ul>{items}</ul>")
        zf.writestr("参加方法.txt", _guide_text(server, public_address, fmt))
        if missing:
            zf.writestr("含まれていないMOD.txt",
                        "次のMODはModrinthで見つからなかったため、このパックに含まれていません。\n"
                        "サーバー管理者から受け取るか、配布元から同じバージョンを入手して mods フォルダーに入れてください。\n\n"
                        + "\n".join(f"- {e.meta.display_name} ({e.meta.file_name})" for e in missing))
    tmp.replace(out_path)
    if res.bundled and fmt != "zip":
        res.warnings.append("※ MOD本体を同梱しています。MODによっては再配布が禁止されています。"
                            "一緒に遊ぶ友達への個人的な共有にとどめ、公開の場には投稿しないでください。")
    elif res.bundled:
        res.warnings.append("※ MOD本体を同梱しています。一緒に遊ぶ友達への個人的な共有にとどめてください。")
    return res
