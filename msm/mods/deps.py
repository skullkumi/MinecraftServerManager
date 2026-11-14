"""MOD依存関係の解析とグループ化."""
from __future__ import annotations

from dataclasses import dataclass, field

from msm.mods.metadata import IGNORED_DEP_IDS, ModMeta, loader_matches
from msm.mods.versionrange import matches

# Fabric APIのモジュールは多数あるので、fabric-apiがあればfabric-*を満たしたとみなす
FABRIC_API_IDS = {"fabric", "fabric-api"}


@dataclass
class Issue:
    level: str  # 確定 / 高 / 中
    kind: str  # missing / version / conflict / duplicate / mc / loader / broken
    file: str
    message: str
    advice: str = ""
    related: list[str] = field(default_factory=list)
    dep_id: str = ""


@dataclass
class DepReport:
    issues: list[Issue]
    groups: list[list[str]]  # ファイル名のグループ (一緒に扱うべきMOD)
    id_index: dict[str, ModMeta]
    required_by: dict[str, set[str]]  # file -> 依存されているfile集合


def build_index(mods: list[ModMeta]) -> dict[str, ModMeta]:
    idx: dict[str, ModMeta] = {}
    for m in mods:
        for i in [*m.mod_ids, *m.provides]:
            idx.setdefault(i, m)
    return idx


def _dep_satisfied_by_fabric_api(dep_id: str, idx: dict) -> bool:
    return dep_id.startswith("fabric-") and any(k in idx for k in FABRIC_API_IDS)


def dependency_edges(mods: list[ModMeta]) -> dict[str, set[str]]:
    """file -> 必須依存先file集合."""
    idx = build_index(mods)
    edges: dict[str, set[str]] = {m.file: set() for m in mods}
    for m in mods:
        for d in m.required_deps():
            if d.mod_id in IGNORED_DEP_IDS:
                continue
            target = idx.get(d.mod_id)
            if target is None and _dep_satisfied_by_fabric_api(d.mod_id, idx):
                target = next(idx[k] for k in FABRIC_API_IDS if k in idx)
            if target is not None and target.file != m.file:
                edges[m.file].add(target.file)
    return edges


def closure(files: set[str], edges: dict[str, set[str]]) -> set[str]:
    out = set(files)
    stack = list(files)
    while stack:
        f = stack.pop()
        for t in edges.get(f, ()):
            if t not in out:
                out.add(t)
                stack.append(t)
    return out


def components(mods: list[ModMeta], edges: dict[str, set[str]]) -> list[list[str]]:
    parent = {m.file: m.file for m in mods}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, ts in edges.items():
        for b in ts:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    groups: dict[str, list[str]] = {}
    for m in mods:
        groups.setdefault(find(m.file), []).append(m.file)
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def analyze(mods: list[ModMeta], mc_version: str, server_type: str) -> DepReport:
    issues: list[Issue] = []
    idx = build_index(mods)
    maven = server_type in ("forge", "neoforge")  # quilt/fabric は SemVer 形式
    # 重複
    seen: dict[str, ModMeta] = {}
    for m in mods:
        for mid in m.mod_ids:
            if mid in seen and seen[mid].file != m.file:
                issues.append(Issue("確定", "duplicate", m.file_name,
                                    f"MOD「{mid}」が重複しています: {seen[mid].file_name} と {m.file_name}",
                                    "古い方のバージョンを無効化してください。", [seen[mid].file]))
            else:
                seen[mid] = m
    for m in mods:
        if not m.valid_jar:
            issues.append(Issue("確定", "broken", m.file_name, f"{m.file_name} はJARとして読み込めません",
                                "再ダウンロードしてください。"))
            continue
        lm = loader_matches(m, server_type)
        if lm is False:
            issues.append(Issue("確定", "loader", m.file_name,
                                f"{m.display_name} は {m.loader} 用です (サーバー: {server_type})",
                                f"{server_type}用のバージョンを入手してください。"))
        if m.mc_range:
            ok = matches(m.mc_range, mc_version, maven=maven)
            if ok is False:
                issues.append(Issue("高", "mc", m.file_name,
                                    f"{m.display_name} は Minecraft {mc_version} に対応していない可能性があります "
                                    f"(MOD側の対応: {m.mc_range})",
                                    f"{m.display_name} の {mc_version} 対応版を確認してください。"))
        for d in m.deps:
            if d.mod_id in IGNORED_DEP_IDS:
                continue
            target = idx.get(d.mod_id)
            if d.kind in ("breaks", "conflicts"):
                if target is not None:
                    ok = matches(d.version_range, target.version, maven=maven) if d.version_range else True
                    if ok is not False:
                        issues.append(Issue("確定" if d.kind == "breaks" else "中", "conflict", m.file_name,
                                            f"{m.display_name} は {target.display_name} と競合すると宣言しています",
                                            "どちらかを無効化してください。", [target.file]))
                continue
            if not d.required:
                continue
            if target is None:
                if _dep_satisfied_by_fabric_api(d.mod_id, idx):
                    continue
                issues.append(Issue("確定", "missing", m.file_name,
                                    f"{m.display_name} の前提MOD「{d.mod_id}」がありません"
                                    + (f" (必要なバージョン: {d.version_range})" if d.version_range else ""),
                                    f"「{d.mod_id}」をダウンロードして追加してください。", dep_id=d.mod_id))
            elif d.version_range and target.version:
                ok = matches(d.version_range, target.version, maven=maven)
                if ok is False:
                    issues.append(Issue("高", "version", m.file_name,
                                        f"{m.display_name} は {d.mod_id} {d.version_range} を必要としますが、"
                                        f"導入されているのは {target.version} です",
                                        f"{d.mod_id} のバージョンを合わせてください。", [target.file]))
    edges = dependency_edges(mods)
    required_by: dict[str, set[str]] = {m.file: set() for m in mods}
    for a, ts in edges.items():
        for b in ts:
            required_by.setdefault(b, set()).add(a)
    return DepReport(issues, components(mods, edges), idx, required_by)
