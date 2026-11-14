"""Modrinth API (APIキー不要) によるMOD情報取得.

- SHA-1ハッシュからMODを特定 (ファイル名に頼らない確実な照合)
- プロジェクトの client_side / server_side 情報
- .mrpack の依存ファイル取得
"""
from __future__ import annotations

import json

from msm.core import net
from msm.core.db import get_db
from msm.core.util import log

API = "https://api.modrinth.com/v2"


def versions_by_hashes(sha1s: list[str]) -> dict[str, dict]:
    """sha1 -> バージョン情報。キャッシュを使い、未知のものだけ問い合わせる."""
    db = get_db()
    out: dict[str, dict] = {}
    todo = []
    for h in sha1s:
        c = db.cache_get(h, "modrinth_version")
        if c is not None:
            if c:
                out[h] = c
        else:
            todo.append(h)
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        try:
            res = net.post_json(f"{API}/version_files", {"hashes": chunk, "algorithm": "sha1"})
        except Exception as e:  # noqa: BLE001
            log.warning("Modrinth照会失敗: %s", e)
            continue
        for h in chunk:
            v = res.get(h)
            db.cache_put(h, "modrinth_version", v or {})
            if v:
                out[h] = v
    return out


def projects(ids: list[str]) -> dict[str, dict]:
    db = get_db()
    out: dict[str, dict] = {}
    todo = []
    for pid in set(ids):
        c = db.cache_get(pid, "modrinth_project")
        if c:
            out[pid] = c
        else:
            todo.append(pid)
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        try:
            res = net.get_json(f"{API}/projects?ids={net.quote(json.dumps(chunk))}")
        except Exception as e:  # noqa: BLE001
            log.warning("Modrinthプロジェクト取得失敗: %s", e)
            continue
        for p in res:
            slim = {k: p.get(k) for k in ("id", "slug", "title", "client_side", "server_side", "project_type",
                                          "source_url", "issues_url")}
            db.cache_put(p["id"], "modrinth_project", slim)
            out[p["id"]] = slim
    return out


def lookup_mods(sha1s: list[str]) -> dict[str, dict]:
    """sha1 -> {project_id, slug, title, client_side, server_side, game_versions, loaders, version_number}"""
    vers = versions_by_hashes(sha1s)
    projs = projects([v["project_id"] for v in vers.values()])
    out = {}
    for h, v in vers.items():
        p = projs.get(v["project_id"], {})
        out[h] = {
            "project_id": v["project_id"],
            "slug": p.get("slug"),
            "title": p.get("title"),
            "client_side": p.get("client_side"),
            "server_side": p.get("server_side"),
            "game_versions": v.get("game_versions", []),
            "loaders": v.get("loaders", []),
            "version_number": v.get("version_number"),
            "dependencies": v.get("dependencies", []),
            "page": f"https://modrinth.com/mod/{p.get('slug') or v['project_id']}",
        }
    return out
