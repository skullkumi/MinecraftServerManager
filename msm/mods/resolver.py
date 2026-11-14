"""不足している前提MODをModrinthから探して取得する.

- MOD ID・MOD名からModrinthのプロジェクトを探す (完全一致を優先、検索結果は確信度を下げる)
- サーバーのMinecraftバージョン・ローダーに合う版を選ぶ (リリース版優先)
- 取得したMODの必須前提MODも、未導入なら一緒に候補にする
- ダウンロードはハッシュ検証付き。導入は取り込み計画 (importer) を通し、上書きしない
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from msm.core import net
from msm.core.paths import cache_dir
from msm.core.util import log, mc_version_tuple
from msm.mods import modrinth_api
from msm.mods.metadata import scan_dir

API = modrinth_api.API


@dataclass
class Candidate:
    requested: str  # 探したMOD ID/名前
    project_id: str
    slug: str
    title: str
    confidence: str  # 高 / 中
    reason: str
    version_id: str = ""
    version_number: str = ""
    file_name: str = ""
    url: str = ""
    sha1: str = ""
    sha512: str = ""
    size: int = 0
    dependencies: list[str] = field(default_factory=list)  # 必須の依存 project_id
    as_dependency_of: str = ""
    error: str = ""

    @property
    def page(self) -> str:
        return f"https://modrinth.com/mod/{self.slug or self.project_id}"

    @property
    def ok(self) -> bool:
        return bool(self.url) and not self.error


def loaders_for(server_type: str, mc: str) -> list[str]:
    if server_type == "fabric":
        return ["fabric"]
    if server_type == "quilt":
        return ["quilt", "fabric"]
    if server_type == "neoforge":
        return ["neoforge", "forge"] if mc_version_tuple(mc) <= (1, 20, 1) else ["neoforge"]
    if server_type == "forge":
        return ["forge"]
    if server_type in ("paper", "purpur"):
        return ["paper", "spigot", "bukkit", "purpur"]
    return []


def _slug_guesses(text: str) -> list[str]:
    t = text.strip().lower()
    base = re.sub(r"\s*\(.*?\)\s*", "", t)
    cands = [t, t.replace("_", "-"), re.sub(r"[^a-z0-9]+", "-", base).strip("-"), base.replace(" ", "")]
    for suffix in ("-api", "-lib"):
        if cands[2].endswith(suffix):
            cands.append(cands[2][: -len(suffix)])
    out = []
    for c in cands:
        if c and c not in out and re.match(r"^[\w!@$()`.+,\"\-']{2,64}$", c):
            out.append(c)
    return out


def _project(slug: str) -> dict | None:
    try:
        p = net.get_json(f"{API}/project/{net.quote(slug)}")
        return p if p.get("project_type") in ("mod", "plugin") else None
    except Exception:
        return None


def find_project(query: str, mc: str, loaders: list[str]) -> tuple[dict | None, str, str]:
    """(project, 確信度, 理由)"""
    for slug in _slug_guesses(query):
        p = _project(slug)
        if p and (not loaders or set(p.get("loaders", [])) & set(loaders)):
            return p, "高", f"ModrinthのプロジェクトID「{p['slug']}」が一致"
    facets = [["project_type:mod"], [f"versions:{mc}"]]
    if loaders:
        facets.append([f"categories:{l}" for l in loaders])
    try:
        res = net.get_json(f"{API}/search?query={net.quote(query)}&limit=5&facets={net.quote(json.dumps(facets))}")
    except Exception as e:  # noqa: BLE001
        log.warning("Modrinth検索失敗: %s", e)
        return None, "", f"検索に失敗しました: {e}"
    hits = res.get("hits", [])
    if not hits:
        return None, "", "Modrinthで見つかりませんでした"
    q = re.sub(r"[^a-z0-9]", "", query.lower())
    for h in hits:
        if re.sub(r"[^a-z0-9]", "", h.get("title", "").lower()) == q or h.get("slug") == query.lower():
            return {"id": h["project_id"], "slug": h["slug"], "title": h["title"]}, "高", "MOD名が一致"
    h = hits[0]
    return {"id": h["project_id"], "slug": h["slug"], "title": h["title"]}, "中", "検索結果の1件目 (名前が完全には一致しません。内容を確認してください)"


def best_version(project_id: str, mc: str, loaders: list[str]) -> dict | None:
    url = (f"{API}/project/{project_id}/version?game_versions={net.quote(json.dumps([mc]))}"
           + (f"&loaders={net.quote(json.dumps(loaders))}" if loaders else ""))
    vs = net.get_json(url)
    if not vs:
        return None
    for vt in ("release", "beta", "alpha"):
        for v in vs:
            if v.get("version_type") == vt:
                # ローダーの優先順 (neoforge > forge など)
                return v
    return vs[0]


def _fill(c: Candidate, v: dict) -> None:
    f = next((x for x in v.get("files", []) if x.get("primary")), (v.get("files") or [None])[0])
    if not f:
        c.error = "ダウンロードできるファイルがありません"
        return
    c.version_id = v["id"]
    c.version_number = v.get("version_number", "")
    c.file_name = f["filename"]
    c.url = f["url"]
    c.sha1 = f.get("hashes", {}).get("sha1", "")
    c.sha512 = f.get("hashes", {}).get("sha512", "")
    c.size = int(f.get("size") or 0)
    c.dependencies = [d["project_id"] for d in v.get("dependencies", [])
                      if d.get("dependency_type") == "required" and d.get("project_id")]


def installed_project_ids(server) -> set[str]:
    metas = scan_dir(server.mods_dir)
    try:
        found = modrinth_api.versions_by_hashes([m.sha1 for m in metas if m.sha1])
    except Exception:
        found = {}
    return {v["project_id"] for v in found.values()}


def resolve(server, queries: list[str], progress=None) -> list[Candidate]:
    """探すMOD (ID/名前) から、取得候補 (必須の前提MODを含む) を作る."""
    loaders = loaders_for(server.server_type, server.mc_version)
    have = installed_project_ids(server)
    installed_ids = {i for m in scan_dir(server.mods_dir, with_hash=False) for i in [*m.mod_ids, *m.provides]}
    out: list[Candidate] = []
    planned: set[str] = set()
    queue: list[tuple[str, str, str]] = [(q, "", "") for q in queries]  # (query, project_id, parent)
    while queue and len(out) < 40:
        query, pid, parent = queue.pop(0)
        if progress:
            progress(f"Modrinthで探しています: {query}", None)
        if pid:
            p = _project(pid)
            if p is None:
                continue
            conf, reason = "高", f"「{parent}」の必須前提MOD (Modrinthの依存情報)"
        else:
            if query in installed_ids:
                continue
            p, conf, reason = find_project(query, server.mc_version, loaders)
            if p is None:
                out.append(Candidate(query, "", "", query, "", reason, error=reason))
                continue
        pid = p.get("id")
        if pid in planned or pid in have:
            continue
        planned.add(pid)
        c = Candidate(query, pid, p.get("slug", ""), p.get("title", query), conf, reason, as_dependency_of=parent)
        try:
            v = best_version(pid, server.mc_version, loaders)
        except Exception as e:  # noqa: BLE001
            v = None
            c.error = f"バージョン情報を取得できません: {e}"
        if v is None and not c.error:
            c.error = f"Minecraft {server.mc_version} / {'/'.join(loaders)} に対応する版がModrinthにありません"
        if v:
            _fill(c, v)
            for dep in c.dependencies:
                if dep not in planned and dep not in have:
                    queue.append((dep, dep, c.title))
        out.append(c)
    return out


def download(cands: list[Candidate], progress=None, cancel=None) -> list[Path]:
    d = cache_dir() / "resolver"
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for i, c in enumerate([c for c in cands if c.ok]):
        dest = d / c.file_name
        if progress:
            progress(f"ダウンロード中: {c.file_name}", (i + 1) / max(1, len(cands)))
        if dest.exists():
            dest.unlink()
        algo = ("sha512", c.sha512) if c.sha512 else (("sha1", c.sha1) if c.sha1 else None)
        net.download(c.url, dest, expected_hash=algo, cancel=cancel, label=c.file_name)
        out.append(dest)
    return out
