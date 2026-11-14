"""サーバー種類ごとの取得元 (公式API・公式Maven) と導入処理.

対応: Vanilla / Paper / Fabric / Forge / NeoForge
配布元のURLやAPIはこのファイルに集約しているので、変更時はここだけ直せばよい。
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from msm.core import net
from msm.core.paths import cache_dir
from msm.core.util import CREATE_NO_WINDOW, CancelToken, Cancelled, log, mc_version_tuple, version_key

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
PAPER_API = "https://fill.papermc.io/v3/projects/paper"
FABRIC_META = "https://meta.fabricmc.net/v2"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEOFORGE_API = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
PURPUR_API = "https://api.purpurmc.org/v2/purpur"
QUILT_META = "https://meta.quiltmc.org/v3"

LogFn = Callable[[str], None]
ProgressFn = Callable[[str, float | None], None]


@dataclass
class LoaderVersion:
    version: str
    label: str
    recommended: bool = False
    stable: bool = True


# ---------------------------------------------------------------- キャッシュ付き取得
def _cached_json(name: str, url: str, max_age: float = 3600, headers: dict | None = None):
    path = cache_dir() / name
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:
            pass
    try:
        data = net.get_json(url, headers)
        path.write_text(json.dumps(data), "utf-8")
        return data
    except Exception:
        if path.exists():  # オフライン時は古いキャッシュを使う
            log.warning("オフラインのためキャッシュを使用: %s", url)
            return json.loads(path.read_text("utf-8"))
        raise


def mojang_manifest() -> dict:
    return _cached_json("mojang_manifest.json", MOJANG_MANIFEST)


def mojang_version_detail(mc: str) -> dict:
    entry = next((v for v in mojang_manifest()["versions"] if v["id"] == mc), None)
    if entry is None:
        raise ValueError(f"Minecraft {mc} は公式バージョン一覧にありません")
    return _cached_json(f"mojang_{re.sub(r'[^0-9A-Za-z.-]', '_', mc)}.json", entry["url"], max_age=30 * 86400)


def paper_project() -> dict:
    return _cached_json("paper_project.json", PAPER_API)


def paper_java_minimum(mc: str) -> int | None:
    data = _cached_json(f"paper_ver_{mc}.json", f"{PAPER_API}/versions/{mc}", max_age=86400)
    try:
        return int(data["version"]["java"]["version"]["minimum"])
    except Exception:
        return None


# ---------------------------------------------------------------- 導入共通
def _run_installer(java: str, installer: Path, args: list[str], cwd: Path, log_fn: LogFn,
                   cancel: CancelToken | None) -> None:
    cmd = [java, "-jar", str(installer), *args]
    log_fn("実行: " + " ".join(shlex.quote(c) for c in cmd))
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    assert p.stdout
    for raw in p.stdout:
        if cancel and cancel.cancelled:
            p.kill()
            raise Cancelled()
        line = raw.decode("utf-8", "replace").rstrip()
        # 展開ファイル一覧などの大量の出力は省略する
        if line and not re.match(r"^\s+\S+(\.class|/|\.json|\.png|\.txt|\.nbt)$", line):
            log_fn(line)
    code = p.wait()
    if code != 0:
        raise RuntimeError(f"インストーラーが終了コード {code} で失敗しました。ログを確認してください。")


def detect_launch_from_runbat(server_dir: Path) -> dict | None:
    """Forge/NeoForgeインストーラーが作るrun.batからjava起動引数を取り出す.

    run.batを直接実行すると「サーバーごとのJava指定」が効かないため、引数だけ利用する。
    """
    bat = server_dir / "run.bat"
    if not bat.exists():
        return None
    for line in bat.read_text("utf-8", "replace").splitlines():
        s = line.strip()
        if re.match(r'^"?java(\.exe)?"?\s', s, re.I):
            tokens = shlex.split(s, posix=False)[1:]
            tokens = [t for t in tokens if t not in ("%*",)]
            return {"kind": "args", "args": tokens}
    return None


def detect_launch(server_dir: Path, server_type: str = "") -> dict:
    """既存フォルダーから起動方法を推定する."""
    server_dir = Path(server_dir)
    st = server_type.lower()
    if st in ("forge", "neoforge", "") and (server_dir / "run.bat").exists():
        found = detect_launch_from_runbat(server_dir)
        if found:
            return found
    for pattern in ("libraries/net/neoforged/neoforge/*/win_args.txt", "libraries/net/minecraftforge/forge/*/win_args.txt"):
        hits = sorted(server_dir.glob(pattern))
        if hits:
            rel = hits[-1].relative_to(server_dir).as_posix()
            args = ["@user_jvm_args.txt"] if (server_dir / "user_jvm_args.txt").exists() else []
            return {"kind": "args", "args": [*args, f"@{rel}"]}
    if (server_dir / "fabric-server-launch.jar").exists():
        return {"kind": "jar", "jar": "fabric-server-launch.jar"}
    if (server_dir / "quilt-server-launch.jar").exists():
        return {"kind": "jar", "jar": "quilt-server-launch.jar"}
    forge_jars = [p for p in server_dir.glob("forge-*.jar") if "installer" not in p.name]
    if forge_jars:
        return {"kind": "jar", "jar": sorted(forge_jars)[-1].name}
    paper = sorted(server_dir.glob("paper*.jar")) or sorted(server_dir.glob("purpur*.jar"))
    if paper:
        return {"kind": "jar", "jar": paper[-1].name}
    if (server_dir / "server.jar").exists():
        return {"kind": "jar", "jar": "server.jar"}
    jars = sorted(server_dir.glob("*.jar"))
    if jars:
        return {"kind": "jar", "jar": jars[0].name}
    return {"kind": "jar", "jar": "server.jar"}


# ---------------------------------------------------------------- 各サーバー種類
class Provider:
    key = ""
    label = ""
    description = ""
    needs_loader = False
    needs_java_for_install = False

    def list_mc_versions(self, snapshots: bool = False) -> list[str]:
        raise NotImplementedError

    def list_loader_versions(self, mc: str) -> list[LoaderVersion]:
        return []

    def install(self, server_dir: Path, mc: str, loader: str, java: str | None,
                progress: ProgressFn, log_fn: LogFn, cancel: CancelToken | None = None) -> dict:
        raise NotImplementedError


def _mojang_versions(snapshots: bool) -> list[str]:
    vs = mojang_manifest()["versions"]
    return [v["id"] for v in vs if v["type"] == "release" or (snapshots and v["type"] == "snapshot")]


class VanillaProvider(Provider):
    key = "vanilla"
    label = "Vanilla (公式)"
    description = "Mojang公式のサーバーです。MODやプラグインは使えません。"

    def list_mc_versions(self, snapshots=False):
        return _mojang_versions(snapshots)

    def install(self, server_dir, mc, loader, java, progress, log_fn, cancel=None):
        detail = mojang_version_detail(mc)
        srv = (detail.get("downloads") or {}).get("server")
        if not srv:
            raise RuntimeError(f"Minecraft {mc} には公式サーバーファイルがありません")
        log_fn(f"公式サーバーを取得: {srv['url']}")
        net.download(srv["url"], server_dir / "server.jar", expected_hash=("sha1", srv["sha1"]),
                     progress=progress, cancel=cancel, label=f"minecraft_server {mc}")
        return {"kind": "jar", "jar": "server.jar"}


class PaperProvider(Provider):
    key = "paper"
    label = "Paper (プラグイン対応・軽量)"
    description = "動作が軽く、Bukkit/Spigot/Paperプラグインが使えます。MODは使えません。"
    needs_loader = True

    def list_mc_versions(self, snapshots=False):
        out = []
        for group in paper_project()["versions"].values():
            for v in group:
                if not snapshots and re.search(r"(pre|rc|snapshot)", v):
                    continue
                out.append(v)
        return out

    def _builds(self, mc):
        return _cached_json(f"paper_builds_{mc}.json", f"{PAPER_API}/versions/{mc}/builds", max_age=1800,
                            headers={"User-Agent": net.USER_AGENT})

    def list_loader_versions(self, mc):
        out = []
        builds = self._builds(mc)
        first_stable = True
        for b in builds[:30]:
            stable = b.get("channel") == "STABLE"
            rec = stable and first_stable
            if rec:
                first_stable = False
            out.append(LoaderVersion(str(b["id"]), f"ビルド {b['id']} ({b.get('channel', '').lower()})", rec, stable))
        return out

    def install(self, server_dir, mc, loader, java, progress, log_fn, cancel=None):
        builds = self._builds(mc)
        build = None
        if loader:
            build = next((b for b in builds if str(b["id"]) == str(loader)), None)
        if build is None:
            build = next((b for b in builds if b.get("channel") == "STABLE"), None) or (builds[0] if builds else None)
        if build is None:
            raise RuntimeError(f"Paper {mc} のビルドが見つかりません")
        d = build["downloads"]["server:default"]
        log_fn(f"Paperを取得: {d['url']}")
        net.download(d["url"], server_dir / d["name"], expected_hash=("sha256", d["checksums"]["sha256"]),
                     progress=progress, cancel=cancel, label=d["name"])
        return {"kind": "jar", "jar": d["name"], "build": build["id"]}


class FabricProvider(Provider):
    key = "fabric"
    label = "Fabric (軽量MODローダー)"
    description = "軽量なMODローダーです。Fabric用MODが使えます。多くのMODでFabric APIが必要です。"
    needs_loader = True

    def list_mc_versions(self, snapshots=False):
        games = _cached_json("fabric_game.json", f"{FABRIC_META}/versions/game")
        return [g["version"] for g in games if g.get("stable") or snapshots]

    def list_loader_versions(self, mc):
        loaders = _cached_json("fabric_loader.json", f"{FABRIC_META}/versions/loader")
        out = []
        rec_done = False
        for l in loaders[:40]:
            rec = bool(l.get("stable")) and not rec_done
            if rec:
                rec_done = True
            out.append(LoaderVersion(l["version"], l["version"] + (" (安定版)" if l.get("stable") else ""), rec, bool(l.get("stable"))))
        return out

    def install(self, server_dir, mc, loader, java, progress, log_fn, cancel=None):
        if not loader:
            loader = next(l.version for l in self.list_loader_versions(mc) if l.recommended)
        installers = _cached_json("fabric_installer.json", f"{FABRIC_META}/versions/installer")
        inst = next((i["version"] for i in installers if i.get("stable")), installers[0]["version"])
        url = f"{FABRIC_META}/versions/loader/{net.quote(mc)}/{net.quote(loader)}/{net.quote(inst)}/server/jar"
        log_fn(f"Fabricサーバーランチャーを取得: {url}")
        net.download(url, server_dir / "fabric-server-launch.jar", progress=progress, cancel=cancel,
                     label="fabric-server-launch.jar")
        return {"kind": "jar", "jar": "fabric-server-launch.jar"}


def _forge_versions() -> dict[str, list[str]]:
    path = cache_dir() / "forge_metadata.xml"
    if not path.exists() or time.time() - path.stat().st_mtime > 3600:
        try:
            path.write_bytes(net.get_bytes(f"{FORGE_MAVEN}/maven-metadata.xml"))
        except Exception:
            if not path.exists():
                raise
    root = ET.fromstring(path.read_bytes())
    by_mc: dict[str, list[str]] = {}
    for v in root.iter("version"):
        full = (v.text or "").strip()
        if "-" not in full:
            continue
        mc, rest = full.split("-", 1)
        by_mc.setdefault(mc, []).append(full)
    return by_mc


class ForgeProvider(Provider):
    key = "forge"
    label = "Forge (定番MODローダー)"
    description = "昔からある定番のMODローダーです。CurseForgeの多くのMODパックで使われています。"
    needs_loader = True
    needs_java_for_install = True

    def list_mc_versions(self, snapshots=False):
        mcs = list(_forge_versions().keys())
        mcs = [m for m in mcs if mc_version_tuple(m) >= (1, 7)]
        return sorted(set(mcs), key=version_key, reverse=True)

    def list_loader_versions(self, mc):
        promos = _cached_json("forge_promos.json", FORGE_PROMOS).get("promos", {})
        rec = promos.get(f"{mc}-recommended")
        latest = promos.get(f"{mc}-latest")
        out = []
        for full in _forge_versions().get(mc, []):
            fv = full.split("-", 1)[1]
            tag = " (推奨)" if fv == rec else (" (最新)" if fv == latest else "")
            out.append(LoaderVersion(fv, fv + tag, fv == rec or (rec is None and fv == latest)))
        out.sort(key=lambda l: version_key(l.version), reverse=True)
        if out and not any(l.recommended for l in out):
            out[0].recommended = True
        return out

    def install(self, server_dir, mc, loader, java, progress, log_fn, cancel=None):
        if not java:
            raise RuntimeError("ForgeのインストールにはJavaが必要です。アプリ設定の「Java」タブで導入してください。")
        if not loader:
            loader = next(l.version for l in self.list_loader_versions(mc) if l.recommended)
        full = f"{mc}-{loader}"
        name = f"forge-{full}-installer.jar"
        inst_dir = server_dir / ".msm" / "installers"
        inst_dir.mkdir(parents=True, exist_ok=True)
        installer = inst_dir / name
        if not installer.exists():
            net.download(f"{FORGE_MAVEN}/{full}/{name}", installer, progress=progress, cancel=cancel, label=name)
        progress("Forgeをインストール中 (数分かかることがあります)...", None)
        _run_installer(java, installer, ["--installServer", str(server_dir)], server_dir, log_fn, cancel)
        launch = detect_launch(server_dir, "forge")
        log_fn(f"起動方法: {launch}")
        return launch


def neoforge_mc_version(nv: str) -> str | None:
    """NeoForgeのバージョン番号から対応するMinecraftバージョンを求める.

    21.1.77 -> 1.21.1 / 21.0.10 -> 1.21 / 26.1.2.126 -> 26.1.2 / 26.3.0.103 -> 26.3
    """
    base = nv.split("-")[0].split("+")[0]
    parts = base.split(".")
    if not all(p.isdigit() for p in parts) or len(parts) < 3:
        return None
    if int(parts[0]) < 20:
        return None
    if len(parts) >= 4:  # 新形式 (年ベース)
        a, b, c = parts[0], parts[1], parts[2]
        return f"{a}.{b}" if c == "0" else f"{a}.{b}.{c}"
    a, b = parts[0], parts[1]
    return f"1.{a}" if b == "0" else f"1.{a}.{b}"


class NeoForgeProvider(Provider):
    key = "neoforge"
    label = "NeoForge (新しいForge系ローダー)"
    description = "Forgeから派生した新しいMODローダーです。1.20.2以降の多くのMODパックで使われています。"
    needs_loader = True
    needs_java_for_install = True

    def _all(self) -> dict[str, list[str]]:
        data = _cached_json("neoforge_versions.json", NEOFORGE_API)
        by_mc: dict[str, list[str]] = {}
        for v in data.get("versions", []):
            if "alpha" in v or "snapshot" in v:
                continue
            mc = neoforge_mc_version(v)
            if mc:
                by_mc.setdefault(mc, []).append(v)
        return by_mc

    def list_mc_versions(self, snapshots=False):
        return sorted(self._all().keys(), key=version_key, reverse=True)

    def list_loader_versions(self, mc):
        vs = sorted(self._all().get(mc, []), key=version_key, reverse=True)
        out = []
        rec_done = False
        for v in vs:
            stable = "beta" not in v
            rec = stable and not rec_done
            if rec:
                rec_done = True
            out.append(LoaderVersion(v, v + (" (ベータ版)" if not stable else ""), rec, stable))
        if out and not rec_done:
            out[0].recommended = True
        return out

    def install(self, server_dir, mc, loader, java, progress, log_fn, cancel=None):
        if not java:
            raise RuntimeError("NeoForgeのインストールにはJavaが必要です。アプリ設定の「Java」タブで導入してください。")
        if not loader:
            loader = next(l.version for l in self.list_loader_versions(mc) if l.recommended)
        name = f"neoforge-{loader}-installer.jar"
        inst_dir = server_dir / ".msm" / "installers"
        inst_dir.mkdir(parents=True, exist_ok=True)
        installer = inst_dir / name
        if not installer.exists():
            net.download(f"{NEOFORGE_MAVEN}/{loader}/{name}", installer, progress=progress, cancel=cancel, label=name)
        progress("NeoForgeをインストール中 (数分かかることがあります)...", None)
        _run_installer(java, installer, ["--install-server", str(server_dir)], server_dir, log_fn, cancel)
        launch = detect_launch(server_dir, "neoforge")
        log_fn(f"起動方法: {launch}")
        return launch


class PurpurProvider(Provider):
    key = "purpur"
    label = "Purpur (Paper系・設定項目が多い)"
    description = "Paperをもとに、ゲーム内容を細かく調整できる設定を追加したサーバーです。Paperのプラグインが使えます。"
    needs_loader = True

    def list_mc_versions(self, snapshots=False):
        vs = _cached_json("purpur_project.json", PURPUR_API).get("versions", [])
        return list(reversed(vs))

    def _builds(self, mc):
        return _cached_json(f"purpur_{mc}.json", f"{PURPUR_API}/{mc}", max_age=1800).get("builds", {})

    def list_loader_versions(self, mc):
        b = self._builds(mc)
        latest = str(b.get("latest", ""))
        return [LoaderVersion(str(x), f"ビルド {x}" + (" (最新)" if str(x) == latest else ""), str(x) == latest)
                for x in reversed(b.get("all", [])[-30:])]

    def install(self, server_dir, mc, loader, java, progress, log_fn, cancel=None):
        build = loader or str(self._builds(mc).get("latest", ""))
        if not build:
            raise RuntimeError(f"Purpur {mc} のビルドが見つかりません")
        info = net.get_json(f"{PURPUR_API}/{mc}/{build}")
        if info.get("result") != "SUCCESS":
            raise RuntimeError(f"Purpur {mc} ビルド {build} は正常にビルドされていません")
        name = f"purpur-{mc}-{build}.jar"
        url = f"{PURPUR_API}/{mc}/{build}/download"
        log_fn(f"Purpurを取得: {url}")
        net.download(url, server_dir / name, expected_hash=("md5", info["md5"]) if info.get("md5") else None,
                     progress=progress, cancel=cancel, label=name)
        return {"kind": "jar", "jar": name, "build": build}


class QuiltProvider(Provider):
    key = "quilt"
    label = "Quilt (Fabric互換のMODローダー)"
    description = "Fabricから派生したMODローダーです。Quilt用MODに加えて、多くのFabric用MODも使えます。"
    needs_loader = True
    needs_java_for_install = True

    def list_mc_versions(self, snapshots=False):
        games = _cached_json("quilt_game.json", f"{QUILT_META}/versions/game")
        return [g["version"] for g in games if g.get("stable") or snapshots]

    def list_loader_versions(self, mc):
        loaders = _cached_json("quilt_loader.json", f"{QUILT_META}/versions/loader")
        out = []
        rec_done = False
        for l in loaders[:40]:
            v = l["version"]
            stable = not re.search(r"(beta|alpha|pre|rc)", v)
            rec = stable and not rec_done
            if rec:
                rec_done = True
            out.append(LoaderVersion(v, v + ("" if stable else " (ベータ版)"), rec, stable))
        return out

    def install(self, server_dir, mc, loader, java, progress, log_fn, cancel=None):
        if not java:
            raise RuntimeError("QuiltのインストールにはJavaが必要です。アプリ設定の「Java」タブで導入してください。")
        if not loader:
            loader = next(l.version for l in self.list_loader_versions(mc) if l.recommended)
        inst = _cached_json("quilt_installer.json", f"{QUILT_META}/versions/installer")[0]
        inst_dir = server_dir / ".msm" / "installers"
        inst_dir.mkdir(parents=True, exist_ok=True)
        jar = inst_dir / f"quilt-installer-{inst['version']}.jar"
        if not jar.exists():
            net.download(inst["url"], jar, expected_hash=("sha256", inst["hashes"]["sha256"]), progress=progress,
                         cancel=cancel, label=jar.name)
        progress("Quiltをインストール中...", None)
        _run_installer(java, jar, ["install", "server", mc, loader, "--download-server",
                                   f"--install-dir={server_dir}"], server_dir, log_fn, cancel)
        if not (server_dir / "quilt-server-launch.jar").exists():
            raise RuntimeError("Quiltのインストール後に quilt-server-launch.jar が見つかりません")
        return {"kind": "jar", "jar": "quilt-server-launch.jar"}


PROVIDERS: dict[str, Provider] = {
    p.key: p for p in (VanillaProvider(), PaperProvider(), PurpurProvider(), FabricProvider(), QuiltProvider(),
                       ForgeProvider(), NeoForgeProvider())
}

TYPE_LABELS = {"vanilla": "Vanilla", "paper": "Paper", "purpur": "Purpur", "fabric": "Fabric", "quilt": "Quilt",
               "forge": "Forge", "neoforge": "NeoForge"}

MODDED_TYPES = {"fabric", "quilt", "forge", "neoforge"}
PLUGIN_TYPES = {"paper", "purpur"}


def get_provider(key: str) -> Provider:
    return PROVIDERS[key.lower()]
