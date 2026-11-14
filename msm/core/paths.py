"""アプリのデータ保存先などのパス管理."""
from __future__ import annotations

import ctypes
import os
import sys
import uuid
from pathlib import Path

from msm import APP_ID


def app_data_dir() -> Path:
    """アプリのデータ保存先 (%LOCALAPPDATA%\\MinecraftServerManager)。

    環境変数 MSM_DATA_DIR で上書きできる (テスト・ポータブル運用向け)。
    """
    override = os.environ.get("MSM_DATA_DIR")
    if override:
        p = Path(override)
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        p = Path(base) / APP_ID
    p.mkdir(parents=True, exist_ok=True)
    return p


def sub_dir(name: str) -> Path:
    p = app_data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def managed_java_dir() -> Path:
    """アプリが導入したJavaの保存先 (システムには一切登録しない)."""
    return sub_dir("java")


def logs_dir() -> Path:
    return sub_dir("logs")


def cache_dir() -> Path:
    return sub_dir("cache")


def test_env_dir() -> Path:
    return sub_dir("test_envs")


def default_servers_root() -> Path:
    return Path.home() / "MinecraftServers"


def default_backup_root() -> Path:
    return Path.home() / "MinecraftServers" / "_backups"


def resource_path(*parts: str) -> Path:
    """PyInstaller同梱リソースのパス."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base.joinpath(*parts)


_FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def windows_downloads_dir() -> Path:
    """Windowsの既定ダウンロードフォルダー (Known Folder API) を取得する."""
    if os.name == "nt":
        try:
            u = uuid.UUID(_FOLDERID_DOWNLOADS)
            guid = _GUID()
            guid.Data1 = u.fields[0]
            guid.Data2 = u.fields[1]
            guid.Data3 = u.fields[2]
            for i, b in enumerate(u.bytes[8:]):
                guid.Data4[i] = b
            path_ptr = ctypes.c_wchar_p()
            res = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
            )
            if res == 0 and path_ptr.value:
                value = path_ptr.value
                ctypes.windll.ole32.CoTaskMemFree(path_ptr)
                return Path(value)
        except Exception:
            pass
    return Path.home() / "Downloads"


def safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if c in bad or ord(c) < 32 else c for c in name).strip(" .")
    return cleaned or "server"
