"""PCにインストールされているJavaの検出と情報取得."""
from __future__ import annotations

import glob
import os
import re
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path

from msm.core.paths import managed_java_dir
from msm.core.util import log, run_hidden


@dataclass
class JavaInfo:
    path: str  # java.exe のフルパス
    version: str = ""
    major: int = 0
    vendor: str = ""
    arch: str = ""  # "64bit" / "32bit" / ""
    sources: list[str] = field(default_factory=list)
    managed: bool = False
    ok: bool = False
    error: str = ""
    runtime_name: str = ""

    @property
    def home(self) -> str:
        return str(Path(self.path).parent.parent)

    @property
    def label(self) -> str:
        if not self.ok:
            return f"(使用不可) {self.path}"
        return f"Java {self.major} ({self.version}, {self.vendor or '配布元不明'}, {self.arch})"


def parse_major(version: str) -> int:
    v = version.strip().strip('"')
    if v.startswith("1."):
        m = re.match(r"1\.(\d+)", v)
        return int(m.group(1)) if m else 0
    m = re.match(r"(\d+)", v)
    return int(m.group(1)) if m else 0


def pe_bitness(exe: str) -> str:
    """実行ファイルのPEヘッダーからbit数を判定する (起動せずに確認できる)."""
    try:
        with open(exe, "rb") as f:
            if f.read(2) != b"MZ":
                return ""
            f.seek(0x3C)
            pe_off = struct.unpack("<I", f.read(4))[0]
            f.seek(pe_off)
            if f.read(4) != b"PE\0\0":
                return ""
            machine = struct.unpack("<H", f.read(2))[0]
            if machine in (0x8664, 0xAA64):
                return "64bit"
            if machine == 0x14C:
                return "32bit"
    except OSError:
        pass
    return ""


_VENDOR_HINTS = [
    ("Temurin", "Eclipse Temurin"),
    ("Adoptium", "Eclipse Temurin"),
    ("AdoptOpenJDK", "AdoptOpenJDK"),
    ("Zulu", "Azul Zulu"),
    ("Corretto", "Amazon Corretto"),
    ("Microsoft", "Microsoft Build of OpenJDK"),
    ("Liberica", "BellSoft Liberica"),
    ("BellSoft", "BellSoft Liberica"),
    ("Semeru", "IBM Semeru"),
    ("GraalVM", "GraalVM"),
    ("Oracle", "Oracle"),
    ("JetBrains", "JetBrains Runtime"),
    ("Mojang", "Mojang (Minecraftランチャー同梱)"),
]


def probe(java_exe: str) -> JavaInfo:
    """java -XshowSettings:properties -version を実行して情報を得る."""
    info = JavaInfo(path=str(Path(java_exe)))
    if not os.path.isfile(java_exe):
        info.error = "ファイルが存在しません"
        return info
    try:
        r = run_hidden([java_exe, "-XshowSettings:properties", "-version"], timeout=20)
        text = (r.stderr or b"").decode("utf-8", "replace") + (r.stdout or b"").decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        info.error = f"起動できません: {e}"
        info.arch = pe_bitness(java_exe)
        return info
    props: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"\s+([\w.]+) = (.*)$", line)
        if m:
            props[m.group(1)] = m.group(2).strip()
    info.version = props.get("java.version", "")
    if not info.version:
        m = re.search(r'version "([^"]+)"', text)
        info.version = m.group(1) if m else ""
    info.major = parse_major(info.version) if info.version else 0
    model = props.get("sun.arch.data.model", "")
    info.arch = f"{model}bit" if model in ("32", "64") else pe_bitness(java_exe)
    info.runtime_name = props.get("java.runtime.name", "")
    vendor_text = " ".join(
        [props.get("java.vendor", ""), props.get("java.vendor.version", ""), props.get("java.vm.vendor", ""), text[-400:]]
    )
    info.vendor = props.get("java.vendor", "")
    for key, name in _VENDOR_HINTS:
        if key.lower() in vendor_text.lower():
            info.vendor = name
            break
    info.ok = info.major > 0 and r.returncode == 0
    if not info.ok and not info.error:
        info.error = "バージョンを取得できませんでした"
    return info


def _registry_homes() -> list[tuple[str, str]]:
    homes: list[tuple[str, str]] = []
    if os.name != "nt":
        return homes
    import winreg

    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE"),
    ]
    vendors = [
        r"JavaSoft\Java Runtime Environment",
        r"JavaSoft\Java Development Kit",
        r"JavaSoft\JDK",
        r"JavaSoft\JRE",
        r"Eclipse Adoptium\JDK",
        r"Eclipse Adoptium\JRE",
        r"Eclipse Foundation\JDK",
        r"AdoptOpenJDK\JDK",
        r"AdoptOpenJDK\JRE",
        r"Azul Systems\Zulu",
        r"Microsoft\JDK",
        r"Amazon Corretto",
        r"BellSoft\Liberica",
    ]

    def walk(key, depth: int, label: str):
        if depth > 4:
            return
        for value_name in ("JavaHome", "Path", "InstallationPath"):
            try:
                val, _ = winreg.QueryValueEx(key, value_name)
                if isinstance(val, str) and val:
                    homes.append((val, f"レジストリ ({label})"))
            except OSError:
                pass
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(key, sub) as sk:
                    walk(sk, depth + 1, label)
            except OSError:
                pass

    for hive, base in roots:
        for v in vendors:
            try:
                with winreg.OpenKey(hive, base + "\\" + v) as k:
                    walk(k, 0, v.split("\\")[0])
            except OSError:
                continue
    return homes


def _glob_java(pattern: str) -> list[str]:
    return [p for p in glob.glob(pattern) if os.path.isfile(p)]


def candidate_paths() -> dict[str, list[str]]:
    """java.exeの候補パス -> 検出元のリスト."""
    found: dict[str, list[str]] = {}

    def add(p: str, source: str) -> None:
        if not p:
            return
        try:
            norm = os.path.normcase(os.path.realpath(p))
        except OSError:
            return
        if not os.path.isfile(norm):
            return
        # 表示用には元の大文字小文字を保持
        disp = os.path.realpath(p)
        for k in found:
            if os.path.normcase(k) == norm:
                if source not in found[k]:
                    found[k].append(source)
                return
        found[disp] = [source]

    jh = os.environ.get("JAVA_HOME")
    if jh:
        add(os.path.join(jh, "bin", "java.exe"), "JAVA_HOME")
    for d in os.environ.get("PATH", "").split(os.pathsep):
        d = d.strip('"')
        if d:
            add(os.path.join(d, "java.exe"), "PATH")
    for home, label in _registry_homes():
        add(os.path.join(home, "bin", "java.exe"), label)
    pf_dirs = {os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramW6432")}
    vendors = ["Java", "Eclipse Adoptium", "Eclipse Foundation", "AdoptOpenJDK", "Microsoft", "Zulu", "BellSoft",
               "Amazon Corretto", "Semeru", "OpenJDK", "Azul", "Liberica", "GraalVM"]
    for pf in filter(None, pf_dirs):
        for v in vendors:
            for p in _glob_java(os.path.join(pf, v, "*", "bin", "java.exe")):
                add(p, "Program Files")
            for p in _glob_java(os.path.join(pf, v, "*", "*", "bin", "java.exe")):
                add(p, "Program Files")
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    home = str(Path.home())
    launcher_patterns = [
        (os.path.join(local, "Packages", "Microsoft.4297127D64EC6_8wekyb3d8bbwe", "LocalCache", "Local", "runtime", "*", "windows-x64", "*", "bin", "java.exe"), "Minecraftランチャー"),
        (os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Minecraft Launcher", "runtime", "*", "windows-x64", "*", "bin", "java.exe"), "Minecraftランチャー"),
        (os.path.join(appdata, ".minecraft", "runtime", "*", "windows-x64", "*", "bin", "java.exe"), "Minecraftランチャー"),
        (os.path.join(home, "curseforge", "minecraft", "Install", "runtime", "*", "windows-x64", "*", "bin", "java.exe"), "CurseForge"),
        (os.path.join(appdata, "PrismLauncher", "java", "*", "bin", "java.exe"), "Prism Launcher"),
        (os.path.join(local, "Programs", "Eclipse Adoptium", "*", "bin", "java.exe"), "ユーザーインストール"),
    ]
    for pattern, label in launcher_patterns:
        for p in _glob_java(pattern):
            add(p, label)
    for p in _glob_java(str(managed_java_dir() / "*" / "bin" / "java.exe")):
        add(p, "アプリ管理")
    which = shutil.which("java")
    if which:
        add(which, "PATH")
    return found


def detect_all() -> list[JavaInfo]:
    result: list[JavaInfo] = []
    mdir = os.path.normcase(str(managed_java_dir()))
    for path, sources in candidate_paths().items():
        info = probe(path)
        info.sources = sources
        info.managed = os.path.normcase(path).startswith(mdir)
        result.append(info)
        log.info("Java検出: %s -> %s %s", path, info.version, info.error)
    result.sort(key=lambda j: (-j.major, j.path))
    return result


def java_home_status() -> str:
    jh = os.environ.get("JAVA_HOME")
    if not jh:
        return "JAVA_HOME: 未設定 (未設定でも本アプリは動作します)"
    exe = os.path.join(jh, "bin", "java.exe")
    if os.path.isfile(exe):
        return f"JAVA_HOME: {jh}"
    return f"JAVA_HOME: {jh} (java.exeが見つかりません。設定が古い可能性があります)"


def path_java_status() -> str:
    w = shutil.which("java")
    return f"PATH上のjava: {w}" if w else "PATH上のjava: なし"
