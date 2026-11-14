"""Windowsへのサインイン時にアプリを起動する設定 (現在のユーザーのみ・HKCU\\...\\Run)."""
from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "MinecraftServerManager"


def command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    main = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "main.py"))
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" "{main}" --minimized'


def is_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, VALUE)
            return bool(val)
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, VALUE)
            except FileNotFoundError:
                pass
