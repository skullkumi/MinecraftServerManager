"""Eclipse Temurin (Adoptium) の導入.

- 公式API (api.adoptium.net) からZIP版を取得し、SHA-256を検証する
- アプリ専用フォルダーへ展開するだけで、環境変数・レジストリ・他のJavaは一切変更しない
"""
from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass

from msm.core import net
from msm.core.paths import cache_dir, managed_java_dir
from msm.core.util import CancelToken, log
from msm.java.detect import JavaInfo, probe

API = "https://api.adoptium.net/v3"


@dataclass
class TemurinPackage:
    major: int
    image_type: str
    name: str
    link: str
    sha256: str
    size: int
    version: str

    def describe(self) -> str:
        return (
            f"Eclipse Temurin {self.version} ({self.image_type.upper()}, 64bit)\n"
            f"配布元: Eclipse Adoptium (https://adoptium.net/)\n"
            f"ファイル: {self.name} ({self.size // 1024 // 1024}MB)\n"
            f"URL: {self.link}"
        )


def find_package(major: int, image_type: str = "jre") -> TemurinPackage:
    url = (
        f"{API}/assets/latest/{major}/hotspot?architecture=x64&image_type={image_type}"
        f"&os=windows&vendor=eclipse"
    )
    data = net.get_json(url)
    for item in data:
        b = item.get("binary", {})
        pkg = b.get("package") or {}
        if pkg.get("name", "").endswith(".zip"):
            return TemurinPackage(
                major, image_type, pkg["name"], pkg["link"], pkg.get("checksum", ""), int(pkg.get("size", 0)),
                (item.get("version") or {}).get("openjdk_version", str(major)),
            )
    if image_type == "jre":
        return find_package(major, "jdk")
    raise net.NetError(f"Java {major} のWindows用ZIPが見つかりませんでした")


def install(pkg: TemurinPackage, progress=None, cancel: CancelToken | None = None) -> JavaInfo:
    target = managed_java_dir() / f"temurin-{pkg.major}-{pkg.image_type}-{pkg.version.replace('+', '_')}"
    if (target / "bin" / "java.exe").exists():
        info = probe(str(target / "bin" / "java.exe"))
        if info.ok:
            info.managed = True
            return info
    zpath = cache_dir() / pkg.name
    if zpath.exists():
        zpath.unlink()
    net.download(
        pkg.link, zpath, expected_hash=("sha256", pkg.sha256) if pkg.sha256 else None,
        progress=progress, cancel=cancel, label=pkg.name,
    )
    if progress:
        progress("展開中...", None)
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    with zipfile.ZipFile(zpath) as zf:
        root = tmp.resolve()
        for m in zf.infolist():
            dest = (tmp / m.filename).resolve()
            if not str(dest).startswith(str(root)):
                raise RuntimeError("ZIPに不正なパスが含まれています")
        zf.extractall(tmp)
    # ZIPは "jdk-21.0.5+11-jre/" のような1階層のフォルダーを含む
    inner = [p for p in tmp.iterdir() if p.is_dir()]
    src = inner[0] if len(inner) == 1 and (inner[0] / "bin").exists() else tmp
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(src), str(target))
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        zpath.unlink()
    except OSError:
        pass
    if progress:
        progress("動作確認中...", None)
    info = probe(str(target / "bin" / "java.exe"))
    info.managed = True
    info.sources = ["アプリ管理 (Temurin)"]
    if not info.ok:
        raise RuntimeError(f"導入したJavaの動作確認に失敗しました: {info.error}")
    log.info("Temurin導入完了: %s", info.path)
    return info
