"""MODの更新チェック (Modrinth).

導入済みMODのSHA-1から、同じMinecraftバージョン・ローダー向けの最新版を調べる。
更新はユーザーが選んだものだけ行い、古いファイルは削除せず disabled_mods へ退避する。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from msm.core import net
from msm.core.paths import cache_dir
from msm.mods import disable as dis
from msm.mods import modrinth_api
from msm.mods.metadata import scan_dir
from msm.mods.resolver import loaders_for

API = modrinth_api.API


@dataclass
class UpdateInfo:
    file: str
    file_name: str
    title: str
    slug: str
    current: str
    latest: str
    latest_id: str
    new_file_name: str
    url: str
    sha1: str
    sha512: str
    version_type: str
    published: str

    @property
    def page(self) -> str:
        return f"https://modrinth.com/mod/{self.slug}/version/{self.latest_id}"


def check(server) -> tuple[list[UpdateInfo], int, int]:
    """(更新があるMOD, Modrinthで確認できたMOD数, 全MOD数)"""
    metas = [m for m in scan_dir(server.mods_dir) if m.valid_jar and m.sha1]
    if not metas:
        return [], 0, 0
    current = modrinth_api.versions_by_hashes([m.sha1 for m in metas])
    loaders = loaders_for(server.server_type, server.mc_version)
    latest = net.post_json(f"{API}/version_files/update", {
        "hashes": list(current.keys()), "algorithm": "sha1", "loaders": loaders, "game_versions": [server.mc_version]})
    projects = modrinth_api.projects([v["project_id"] for v in current.values()])
    out = []
    for m in metas:
        cur = current.get(m.sha1)
        new = latest.get(m.sha1) if isinstance(latest, dict) else None
        if not cur or not new or new["id"] == cur["id"]:
            continue
        if (new.get("date_published") or "") <= (cur.get("date_published") or ""):
            continue
        f = next((x for x in new.get("files", []) if x.get("primary")), (new.get("files") or [None])[0])
        if not f:
            continue
        p = projects.get(cur["project_id"], {})
        out.append(UpdateInfo(m.file, m.file_name, p.get("title") or m.display_name, p.get("slug") or cur["project_id"],
                              cur.get("version_number", m.version), new.get("version_number", ""), new["id"],
                              f["filename"], f["url"], f.get("hashes", {}).get("sha1", ""),
                              f.get("hashes", {}).get("sha512", ""), new.get("version_type", ""),
                              (new.get("date_published") or "")[:10]))
    return out, len(current), len(metas)


def apply(server, updates: list[UpdateInfo], progress=None, cancel=None) -> list[str]:
    """選ばれたMODを更新する。呼び出し前にバックアップを取ること."""
    d = cache_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    results = []
    for i, u in enumerate(updates):
        if progress:
            progress(f"更新中: {u.title}", (i + 1) / max(1, len(updates)))
        tmp = d / u.new_file_name
        if tmp.exists():
            tmp.unlink()
        algo = ("sha512", u.sha512) if u.sha512 else (("sha1", u.sha1) if u.sha1 else None)
        net.download(u.url, tmp, expected_hash=algo, cancel=cancel, label=u.new_file_name)
        old = Path(u.file)
        dest = server.mods_dir / u.new_file_name
        if dest.exists() and dest.resolve() != old.resolve():
            results.append(f"スキップ (同名の別ファイルあり): {u.new_file_name}")
            continue
        dis.disable(server, [old], f"更新で置き換え ({u.current} → {u.latest})")
        tmp.replace(dest)
        results.append(f"{u.title}: {u.current} → {u.latest}")
    return results


def to_json(updates: list[UpdateInfo]) -> str:
    return json.dumps([u.__dict__ for u in updates], ensure_ascii=False)
