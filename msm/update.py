"""アプリの更新 (確認・ダウンロード・適用).

更新元 (アプリ設定で指定):
  1. GitHub Releases: "owner/repo"  → 最新リリースの添付ファイルと .sha256 を使う
  2. 更新情報JSONのURL (https):
     {"version": "1.1.0", "url": "配布ページ", "notes": "変更点",
      "downloads": {"onefile":   {"url": "...zip", "sha256": "..."},
                    "onedir":    {"url": "...zip", "sha256": "..."},
                    "installer": {"url": "...exe", "sha256": "..."}}}
安全のため:
  - SHA-256 が分からないファイルは自動更新に使わない (配布ページを開くだけ)
  - 更新はユーザーの確認後に行い、古いファイルは .old として残す
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from msm import APP_VERSION
from msm.core import net
from msm.core.db import get_db
from msm.core.paths import cache_dir
from msm.core.util import version_key

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def update_url() -> str:
    return get_db().get_setting("update_url", "") or ""


def github_repo() -> str:
    return get_db().get_setting("update_github", "") or ""


def install_mode() -> str:
    """onefile / onedir / installer / source"""
    if not getattr(sys, "frozen", False):
        return "source"
    exe_dir = Path(sys.executable).parent
    if list(exe_dir.glob("unins*.exe")):
        return "installer"
    if (exe_dir / "_internal").is_dir():
        return "onedir"
    return "onefile"


def _allowed(url: str) -> bool:
    if url.startswith("https://"):
        return True
    return bool(os.environ.get("MSM_ALLOW_HTTP_UPDATE")) and url.startswith("http://127.0.0.1")


def _from_github(repo: str) -> dict:
    if not re.match(r"^[\w.\-]+/[\w.\-]+$", repo):
        raise ValueError("GitHubリポジトリは「所有者/リポジトリ名」の形式で入力してください")
    rel = net.get_json(f"https://api.github.com/repos/{repo}/releases/latest",
                       headers={"Accept": "application/vnd.github+json"})
    version = str(rel.get("tag_name", "")).lstrip("vV")
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    downloads = {}
    patterns = {"installer": r"Setup.*\.exe$", "onedir": r"-folder\.zip$", "onefile": r"^MinecraftServerManager-v[\d.]+\.zip$"}
    for kind, pat in patterns.items():
        name = next((n for n in assets if re.search(pat, n)), None)
        if not name:
            continue
        sha = ""
        if name + ".sha256" in assets:
            try:
                sha = net.get_bytes(assets[name + ".sha256"]).decode().split()[0]
            except Exception:
                sha = ""
        if not sha:
            m = re.search(rf"([0-9a-fA-F]{{64}})\s+\*?{re.escape(name)}", rel.get("body") or "")
            sha = m.group(1) if m else ""
        downloads[kind] = {"url": assets[name], "sha256": sha.lower(), "name": name}
    return {"version": version, "url": rel.get("html_url", ""), "notes": rel.get("body", "") or "", "downloads": downloads}


def check() -> dict:
    repo, url = github_repo(), update_url()
    if not repo and not url:
        return {"status": "not_configured", "message": "更新の確認先が設定されていません (アプリ設定で指定できます)"}
    if repo:
        data = _from_github(repo)
    else:
        if not _allowed(url):
            return {"status": "error", "message": "更新確認URLは https:// で始まる必要があります"}
        data = net.get_json(url, timeout=15)
    latest = str(data.get("version", ""))
    mode = install_mode()
    dl = (data.get("downloads") or {}).get(mode if mode != "source" else "onefile") or {}
    out = {"version": latest, "url": data.get("url", ""), "notes": data.get("notes", ""), "mode": mode,
           "download": dl.get("url", ""), "sha256": (dl.get("sha256") or "").lower(), "name": dl.get("name", "")}
    if latest and version_key(latest) > version_key(APP_VERSION):
        out.update(status="available", message=f"新しいバージョン {latest} があります (現在 {APP_VERSION})")
        out["auto"] = bool(out["download"] and len(out["sha256"]) == 64 and mode != "source" and _allowed(out["download"]))
    else:
        out.update(status="latest", message=f"最新版です (現在 {APP_VERSION})", auto=False)
    return out


def download(info: dict, progress=None, cancel=None) -> Path:
    if not info.get("auto"):
        raise RuntimeError("このバージョンは自動更新に対応していません (SHA-256が不明)。配布ページからダウンロードしてください。")
    d = cache_dir() / "app_update"
    d.mkdir(parents=True, exist_ok=True)
    name = info.get("name") or Path(info["download"].split("?")[0]).name or "update.bin"
    dest = d / name
    if dest.exists():
        dest.unlink()
    return net.download(info["download"], dest, expected_hash=("sha256", info["sha256"]), progress=progress,
                        cancel=cancel, label=name)


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def prepare(path: Path, mode: str, app_exe: Path | None = None, pid: int | None = None) -> list[str]:
    """更新を適用するコマンドを用意する。アプリの終了後に実行される (古いファイルは .old として残す)."""
    app_exe = Path(app_exe or sys.executable)
    pid = pid or os.getpid()
    if mode == "installer":
        return [str(path)]  # インストーラーの画面で更新 (既存の設定は残る)
    work = path.parent / (path.stem + "_extracted")
    if work.exists():
        import shutil

        shutil.rmtree(work)
    with zipfile.ZipFile(path) as zf:
        root = work.resolve()
        for n in zf.namelist():
            if not str((work / n).resolve()).startswith(str(root)):
                raise RuntimeError("更新ファイルに不正なパスが含まれています")
        zf.extractall(work)
    new_exe = next(work.rglob("MinecraftServerManager.exe"), None)
    if new_exe is None:
        raise RuntimeError("更新ファイルにMinecraftServerManager.exeが見つかりません")
    log = path.parent / "update.log"
    if mode == "onedir":
        src, dst = new_exe.parent, app_exe.parent
        body = (f"$src={_ps_quote(str(src))}; $dst={_ps_quote(str(dst))}; $old=$dst + '.old';"
                "if (Test-Path $old) { Remove-Item $old -Recurse -Force };"
                "Rename-Item -LiteralPath $dst -NewName (Split-Path $old -Leaf);"
                "Copy-Item -LiteralPath $src -Destination $dst -Recurse;"
                f"Start-Process -FilePath (Join-Path $dst {_ps_quote(app_exe.name)})")
    else:
        body = (f"$new={_ps_quote(str(new_exe))}; $exe={_ps_quote(str(app_exe))}; $old=$exe + '.old';"
                "if (Test-Path $old) { Remove-Item $old -Force };"
                "Move-Item -LiteralPath $exe -Destination $old;"
                "Copy-Item -LiteralPath $new -Destination $exe;"
                "Start-Process -FilePath $exe")
    script = (f"$ErrorActionPreference='Stop'; try {{ Wait-Process -Id {pid} -Timeout 180 -ErrorAction SilentlyContinue;"
              f"Start-Sleep -Milliseconds 800; {body}; 'OK' | Out-File -Encoding utf8 {_ps_quote(str(log))} }} "
              f"catch {{ $_ | Out-String | Out-File -Encoding utf8 {_ps_quote(str(log))} }}")
    ps1 = path.parent / "apply_update.ps1"
    ps1.write_text("﻿" + script, encoding="utf-8")
    return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(ps1)]


def launch(cmd: list[str]) -> None:
    """更新コマンドをアプリから切り離して起動する (この後アプリは終了すること)."""
    if cmd and cmd[0].lower().endswith(".exe") and "powershell" not in cmd[0].lower():
        subprocess.Popen(cmd, close_fds=True)
        return
    # PowerShellはコンソールが無いと動かないため、非表示のコンソールで起動する。
    # アプリ終了後も動き続けるよう、可能ならジョブから切り離す。
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
    except OSError:
        subprocess.Popen(cmd, creationflags=flags, close_fds=True)
