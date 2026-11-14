# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 設定。build\build.ps1 から使用する。
#   環境変数 MSM_BUILD_MODE=onefile (既定) : 単体EXE (ポータブル版)
#   環境変数 MSM_BUILD_MODE=onedir         : フォルダー形式 (インストーラー版・起動が速い・Qtの差し替えが容易)
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
MODE = os.environ.get("MSM_BUILD_MODE", "onefile")
PAGES = ("app_settings", "backups", "create", "dashboard", "detail", "diagnose", "import_pack", "integrations",
         "java_page", "logs", "mods", "monitor", "players", "publish", "server_settings", "servers", "worlds")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "assets" / "icon.ico"), "assets"), (str(ROOT / "assets" / "icon.png"), "assets")],
    hiddenimports=["msm.gui.pages." + n for n in PAGES],
    excludes=["tkinter", "unittest", "pytest", "pyflakes", "autoflake", "cryptography", "yaml",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
              "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors",
              "PySide6.QtSerialPort", "PySide6.QtNetwork"],
    noarchive=False,
)
# 不要なQtの大きなファイルを除外してサイズを抑える
drop = ("Qt6WebEngine", "Qt6Quick", "Qt6Qml", "Qt6Pdf", "Qt6Multimedia", "Qt6VirtualKeyboard", "Qt63D", "opengl32sw",
        "Qt6Charts", "Qt6DataVisualization", "Qt6Sql", "Qt6Network", "Qt6OpenGL")
a.binaries = [b for b in a.binaries if not any(d.lower() in b[0].lower() for d in drop)]
a.datas = [d for d in a.datas if "translations" not in d[0].lower() or "qtbase_ja" in d[0].lower()]

pyz = PYZ(a.pure)
common = dict(name="MinecraftServerManager", debug=False, strip=False, upx=False, console=False,
              icon=str(ROOT / "assets" / "icon.ico"), version=str(ROOT / "build" / "version_info.txt"))
if MODE == "onedir":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MinecraftServerManager")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **common)
