"""外部アプリ連携 (CurseForge・Minecraftランチャー)。どちらも任意."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from msm.core.db import get_db

MS_STORE_LAUNCHER_AUMID = "Microsoft.4297127D64EC6_8wekyb3d8bbwe!Minecraft"


# ---------------------------------------------------------------- CurseForge
def find_curseforge() -> str | None:
    custom = get_db().get_setting("curseforge_path")
    local = os.environ.get("LOCALAPPDATA", "")
    cands = [custom,
             os.path.join(local, "Programs", "CurseForge Windows", "CurseForge.exe"),
             os.path.join(local, "Programs", "CurseForge", "CurseForge.exe"),
             os.path.join(os.environ.get("ProgramFiles", ""), "CurseForge", "CurseForge.exe")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def curseforge_instances_dir() -> Path | None:
    custom = get_db().get_setting("curseforge_instances_dir")
    cands = [custom, str(Path.home() / "curseforge" / "minecraft" / "Instances")]
    # CurseForgeの設定ファイルから保存先を読む (存在すれば)
    try:
        st = Path(os.environ.get("APPDATA", "")) / "CurseForge" / "storage.json"
        if st.exists():
            data = json.loads(st.read_text("utf-8", "replace"))
            mc = data.get("minecraft-settings") if isinstance(data, dict) else None
            if isinstance(mc, str):
                mc = json.loads(mc)
            root = (mc or {}).get("minecraftRoot")
            if root:
                cands.insert(1, str(Path(root) / "Instances"))
    except Exception:
        pass
    for c in cands:
        if c and Path(c).is_dir():
            return Path(c)
    return None


@dataclass
class CfProfile:
    name: str
    path: str
    mc_version: str
    loader: str
    loader_version: str
    mod_count: int


def curseforge_profiles() -> list[CfProfile]:
    root = curseforge_instances_dir()
    out: list[CfProfile] = []
    if not root:
        return out
    for d in sorted(root.iterdir()):
        mi = d / "minecraftinstance.json"
        if not mi.exists():
            continue
        try:
            data = json.loads(mi.read_text("utf-8", "replace"))
        except Exception:
            continue
        bml = data.get("baseModLoader") or {}
        loader_name = str(bml.get("name", ""))  # 例: forge-47.2.0 / neoforge-21.1.77 / fabric-0.15.11-1.20.1
        loader, lv = "", ""
        for p in ("neoforge", "forge", "fabric", "quilt"):
            if loader_name.startswith(p + "-"):
                loader, lv = p, loader_name[len(p) + 1:]
                if p == "fabric" and "-" in lv:
                    lv = lv.split("-")[0]
                break
        mods = d / "mods"
        out.append(CfProfile(data.get("name", d.name), str(d), data.get("gameVersion", ""), loader, lv,
                             len(list(mods.glob("*.jar"))) if mods.exists() else 0))
    return out


def launch_curseforge() -> bool:
    exe = find_curseforge()
    if not exe:
        return False
    subprocess.Popen([exe], cwd=os.path.dirname(exe))
    return True


# ---------------------------------------------------------------- Minecraft Launcher
def find_minecraft_launcher() -> tuple[str, str] | None:
    """(種類, 起動対象) を返す。種類: exe / store."""
    custom = get_db().get_setting("minecraft_launcher_path")
    if custom and os.path.isfile(custom):
        return "exe", custom
    for c in [os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Minecraft Launcher", "MinecraftLauncher.exe"),
              os.path.join(os.environ.get("ProgramFiles", ""), "Minecraft Launcher", "MinecraftLauncher.exe")]:
        if os.path.isfile(c):
            return "exe", c
    pkg = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages" / "Microsoft.4297127D64EC6_8wekyb3d8bbwe"
    if pkg.exists():
        return "store", MS_STORE_LAUNCHER_AUMID
    return None


def launch_minecraft() -> bool:
    found = find_minecraft_launcher()
    if not found:
        return False
    kind, target = found
    if kind == "exe":
        subprocess.Popen([target], cwd=os.path.dirname(target))
    else:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"])
    return True


JOIN_GUIDE = """■ 通常のMinecraftランチャーで参加する方法
1. Minecraftランチャーを起動します。
2. サーバーと同じバージョン (Minecraft {mc}) を選びます。
   MODサーバーの場合は、同じMOD・同じローダー ({loader}) の起動構成が必要です。
   (CurseForgeが無い場合: ローダーのインストーラーでクライアント側にも導入し、modsフォルダーへ同じMODを入れます)
3. 「マルチプレイ」→「サーバーを追加」を選びます。
4. サーバーアドレスに次を貼り付けます:
     同じPCから: {local}
     友達: {public}
5. 「完了」→ サーバーを選んで「サーバーに接続」。"""
