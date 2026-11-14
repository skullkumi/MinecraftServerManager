"""共通ユーティリティ."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Cancelled(Exception):
    """ユーザーによるキャンセル."""


class CancelToken:
    def __init__(self) -> None:
        self._ev = threading.Event()

    def cancel(self) -> None:
        self._ev.set()

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def check(self) -> None:
        if self._ev.is_set():
            raise Cancelled()

    def wait(self, seconds: float) -> bool:
        """seconds待つ。キャンセルされたらTrue."""
        return self._ev.wait(seconds)


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("msm")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        h = RotatingFileHandler(log_dir / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(h)
    return logger


log = logging.getLogger("msm")


def run_hidden(args: list[str], timeout: float = 30, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        cwd=cwd,
        creationflags=CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL,
    )


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def free_space(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}TB"


def version_key(v: str) -> tuple:
    """バージョン文字列の比較キー ("1.20.1" < "1.21" < "26.1")."""
    parts = re.split(r"[.\-+]", v)
    key = []
    for p in parts:
        if p.isdigit():
            key.append((1, int(p), ""))
        else:
            key.append((0, 0, p))
    return tuple(key)


def mc_version_tuple(v: str) -> tuple[int, ...]:
    """Minecraftリリース版を数値タプルに (1.20.1 -> (1,20,1), 26.1 -> (26,1))。不明部分は無視."""
    nums = []
    for p in v.split("."):
        m = re.match(r"\d+", p)
        if not m:
            break
        nums.append(int(m.group()))
    return tuple(nums)


def copy_no_overwrite(src: Path, dst: Path) -> Path:
    """dstが存在する場合は上書きせず、番号付きの別名で保存する."""
    dst = Path(dst)
    if dst.exists():
        stem, suffix = dst.stem, dst.suffix
        i = 1
        while True:
            cand = dst.with_name(f"{stem} ({i}){suffix}")
            if not cand.exists():
                dst = cand
                break
            i += 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def link_or_copy(src: Path, dst: Path) -> None:
    """ハードリンク (同一ドライブ) を試し、失敗したらコピーする。テスト環境用."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def open_path(path: Path | str) -> None:
    """エクスプローラー/既定アプリで開く (実行ファイルは開かない)."""
    p = str(path)
    if os.name == "nt":
        if os.path.isdir(p):
            subprocess.Popen(["explorer.exe", p])
        else:
            subprocess.Popen(["explorer.exe", "/select,", p])
