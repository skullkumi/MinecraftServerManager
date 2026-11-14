"""MOD (JAR) のメタデータ解析.

対応: fabric.mod.json / quilt.mod.json / META-INF/mods.toml (Forge) /
      META-INF/neoforge.mods.toml (NeoForge) / mcmod.info (旧Forge) / plugin.yml・paper-plugin.yml (Paper)
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Dependency:
    mod_id: str
    version_range: str = ""
    required: bool = True
    side: str = "BOTH"
    kind: str = "depends"  # depends / breaks / conflicts / recommends


@dataclass
class ModMeta:
    file: str
    file_name: str
    sha1: str = ""
    size: int = 0
    valid_jar: bool = False
    loader: str = ""  # fabric / quilt / forge / neoforge / paper-plugin / unknown
    mod_ids: list[str] = field(default_factory=list)
    name: str = ""
    version: str = ""
    environment: str = ""  # client / server / * / ""
    mc_range: str = ""
    loader_range: str = ""
    deps: list[Dependency] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    display_test: str = ""
    client_side_only_flag: bool = False
    has_client_entrypoint_only: bool = False
    nested_jars: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def primary_id(self) -> str:
        return self.mod_ids[0] if self.mod_ids else ""

    @property
    def display_name(self) -> str:
        return self.name or self.primary_id or self.file_name

    def required_deps(self) -> list[Dependency]:
        return [d for d in self.deps if d.required and d.kind == "depends"]


IGNORED_DEP_IDS = {"minecraft", "java", "fabricloader", "fabric-loader", "forge", "neoforge", "quilt_loader",
                   "javafml", "mixinextras"}


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(zf: zipfile.ZipFile, name: str) -> str | None:
    try:
        return zf.read(name).decode("utf-8", "replace")
    except KeyError:
        return None


def _manifest_attr(zf: zipfile.ZipFile, key: str) -> str:
    text = _read(zf, "META-INF/MANIFEST.MF") or ""
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def _json_loose(text: str):
    """fabric.mod.json には稀に制御文字やコメントが含まれるので緩く読む."""
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        cleaned = re.sub(r"^\s*//.*$", "", text, flags=re.M)
        return json.loads(cleaned, strict=False)


def _parse_fabric(meta: ModMeta, text: str) -> None:
    d = _json_loose(text)
    meta.loader = "fabric"
    meta.mod_ids = [d.get("id", "")]
    meta.name = d.get("name", "")
    meta.version = str(d.get("version", ""))
    meta.environment = d.get("environment", "*") or "*"
    meta.provides = list(d.get("provides", []) or [])
    for kind, required in (("depends", True), ("recommends", False), ("suggests", False), ("breaks", False),
                           ("conflicts", False)):
        for dep_id, rng in (d.get(kind) or {}).items():
            rng_s = " ".join(rng) if isinstance(rng, list) else str(rng)
            if dep_id == "minecraft" and kind == "depends":
                meta.mc_range = rng_s
                continue
            if dep_id == "fabricloader" and kind == "depends":
                meta.loader_range = rng_s
                continue
            k = "depends" if kind in ("depends", "recommends", "suggests") else kind
            meta.deps.append(Dependency(dep_id, rng_s, required and kind == "depends", "BOTH", k))
    ep = d.get("entrypoints") or {}
    if ep and set(ep.keys()) <= {"client", "modmenu", "rei_client", "jei_mod_plugin", "emi", "rei"} and "client" in ep:
        meta.has_client_entrypoint_only = True
    meta.nested_jars = [j.get("file", "") for j in (d.get("jars") or []) if isinstance(j, dict)]


def _parse_quilt(meta: ModMeta, text: str) -> None:
    d = _json_loose(text)
    ql = d.get("quilt_loader", {})
    meta.loader = "quilt"
    meta.mod_ids = [ql.get("id", "")]
    meta.name = (ql.get("metadata") or {}).get("name", "")
    meta.version = str(ql.get("version", ""))
    env = ((d.get("minecraft") or {}).get("environment")) or "*"
    meta.environment = {"client": "client", "dedicated_server": "server"}.get(env, "*")
    for dep in ql.get("depends", []) or []:
        if isinstance(dep, str):
            dep = {"id": dep}
        did = dep.get("id", "")
        if did == "minecraft":
            meta.mc_range = str(dep.get("versions", ""))
            continue
        meta.deps.append(Dependency(did, str(dep.get("versions", "")), not dep.get("optional", False)))


def _parse_mods_toml(meta: ModMeta, text: str, zf: zipfile.ZipFile, loader: str) -> None:
    try:
        d = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        meta.errors.append(f"mods.tomlの解析に失敗: {e}")
        ids = re.findall(r'modId\s*=\s*"([^"]+)"', text)
        meta.mod_ids = ids[:1]
        meta.loader = loader
        return
    meta.loader = loader
    mods = d.get("mods") or []
    impl_ver = _manifest_attr(zf, "Implementation-Version")
    for m in mods:
        mid = m.get("modId", "")
        if mid:
            meta.mod_ids.append(mid)
        if not meta.name:
            meta.name = m.get("displayName", "")
        if not meta.version:
            v = str(m.get("version", ""))
            meta.version = impl_ver if "${" in v else v
        meta.display_test = str(m.get("displayTest", "") or meta.display_test)
    if d.get("clientSideOnly") is True:
        meta.client_side_only_flag = True
    deps = d.get("dependencies") or {}
    own = set(meta.mod_ids)
    for owner, lst in deps.items():
        if owner not in own or not isinstance(lst, list):
            continue
        for dep in lst:
            did = dep.get("modId", "")
            rng = str(dep.get("versionRange", ""))
            side = str(dep.get("side", "BOTH")).upper()
            if "mandatory" in dep:
                required = bool(dep.get("mandatory"))
                kind = "depends"
            else:
                t = str(dep.get("type", "required")).lower()
                required = t == "required"
                kind = {"incompatible": "breaks", "discouraged": "conflicts"}.get(t, "depends")
            if did == "minecraft":
                meta.mc_range = rng
                continue
            if did in ("forge", "neoforge"):
                meta.loader_range = rng
                continue
            meta.deps.append(Dependency(did, rng, required and side in ("BOTH", "SERVER"), side, kind))
    meta.nested_jars = [n for n in zf.namelist() if n.startswith("META-INF/jarjar/") and n.endswith(".jar")]


def _parse_mcmod_info(meta: ModMeta, text: str) -> None:
    try:
        d = _json_loose(text)
    except Exception:
        return
    lst = d.get("modList", d) if isinstance(d, dict) else d
    if not isinstance(lst, list):
        return
    meta.loader = "forge"
    for m in lst:
        if isinstance(m, dict):
            meta.mod_ids.append(m.get("modid", ""))
            meta.name = meta.name or m.get("name", "")
            meta.version = meta.version or m.get("version", "")
            if not meta.mc_range:
                meta.mc_range = m.get("mcversion", "")
            for dep in m.get("requiredMods", []) or []:
                meta.deps.append(Dependency(str(dep).split("@")[0]))


def _parse_plugin_yml(meta: ModMeta, text: str) -> None:
    meta.loader = "paper-plugin"
    for key, attr in (("name", "name"), ("version", "version")):
        m = re.search(rf"^{key}:\s*['\"]?([^'\"\n]+)", text, re.M)
        if m:
            setattr(meta, attr, m.group(1).strip())
    meta.mod_ids = [meta.name.lower()] if meta.name else []
    m = re.search(r"^api-version:\s*['\"]?([\d.]+)", text, re.M)
    if m:
        meta.mc_range = ">=" + m.group(1)
    m = re.search(r"^depend:\s*\[([^\]]*)\]", text, re.M)
    if m:
        for dep in m.group(1).split(","):
            dep = dep.strip().strip("'\"")
            if dep:
                meta.deps.append(Dependency(dep.lower()))
    meta.environment = "server"


def _nested_ids(zf: zipfile.ZipFile, nested: list[str], depth: int = 0) -> list[str]:
    """JAR内に同梱されたJAR (Fabric jars / Forge jarjar) が提供するMOD IDを集める."""
    import io

    ids: list[str] = []
    for n in nested[:200]:
        try:
            data = zf.read(n)
            with zipfile.ZipFile(io.BytesIO(data)) as inner:
                names = set(inner.namelist())
                if "fabric.mod.json" in names:
                    d = _json_loose(_read(inner, "fabric.mod.json") or "{}")
                    if d.get("id"):
                        ids.append(d["id"])
                    ids += list(d.get("provides", []) or [])
                    if depth < 1:
                        sub = [j.get("file", "") for j in (d.get("jars") or []) if isinstance(j, dict)]
                        ids += _nested_ids(inner, sub, depth + 1)
                for toml_name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                    if toml_name in names:
                        ids += re.findall(r'modId\s*=\s*"([^"]+)"', _read(inner, toml_name) or "")
                        break
        except Exception:
            continue
    return [i for i in ids if i]


def read_mod(path: Path, with_hash: bool = True) -> ModMeta:
    path = Path(path)
    meta = ModMeta(file=str(path), file_name=path.name)
    try:
        meta.size = path.stat().st_size
        if with_hash:
            meta.sha1 = sha1_of(path)
    except OSError as e:
        meta.errors.append(str(e))
        return meta
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip() if meta.size < 2_000_000 else None
            if bad:
                meta.errors.append(f"JAR内のファイルが壊れています: {bad}")
            meta.valid_jar = True
            names = set(zf.namelist())
            if "fabric.mod.json" in names:
                _parse_fabric(meta, _read(zf, "fabric.mod.json") or "{}")
            elif "quilt.mod.json" in names:
                _parse_quilt(meta, _read(zf, "quilt.mod.json") or "{}")
            elif "META-INF/neoforge.mods.toml" in names:
                _parse_mods_toml(meta, _read(zf, "META-INF/neoforge.mods.toml") or "", zf, "neoforge")
            elif "META-INF/mods.toml" in names:
                _parse_mods_toml(meta, _read(zf, "META-INF/mods.toml") or "", zf, "forge")
            elif "mcmod.info" in names:
                _parse_mcmod_info(meta, _read(zf, "mcmod.info") or "")
            elif "paper-plugin.yml" in names or "plugin.yml" in names:
                _parse_plugin_yml(meta, _read(zf, "paper-plugin.yml") or _read(zf, "plugin.yml") or "")
            else:
                meta.loader = "unknown"
                if any(n.endswith(".class") for n in list(names)[:2000]):
                    meta.errors.append("MODの情報ファイルが見つかりません (ライブラリまたはコアMODの可能性)")
                else:
                    meta.errors.append("MODの情報ファイルが見つかりません")
            if meta.nested_jars:
                meta.provides += _nested_ids(zf, meta.nested_jars)
    except zipfile.BadZipFile:
        meta.errors.append("JAR (ZIP) として読み込めません。ダウンロードが途中で終わったか、MODファイルではありません。")
    except Exception as e:  # noqa: BLE001
        meta.errors.append(f"解析エラー: {e}")
    meta.mod_ids = [m for m in meta.mod_ids if m]
    return meta


def scan_dir(directory: Path, with_hash: bool = True) -> list[ModMeta]:
    d = Path(directory)
    if not d.exists():
        return []
    return [read_mod(p, with_hash) for p in sorted(d.glob("*.jar"), key=lambda x: x.name.lower())]


def loader_matches(meta: ModMeta, server_type: str) -> bool | None:
    """MODのローダーがサーバーと合うか。判定不能ならNone."""
    if meta.loader in ("", "unknown"):
        return None
    st = server_type.lower()
    if st == "fabric":
        return meta.loader == "fabric"
    if st == "quilt":
        return meta.loader in ("fabric", "quilt")
    if st == "forge":
        return meta.loader == "forge"
    if st == "neoforge":
        # 1.20.1のNeoForgeはForge形式のmods.tomlを読む
        return meta.loader in ("neoforge", "forge")
    if st in ("paper", "purpur"):
        return meta.loader == "paper-plugin"
    return False
