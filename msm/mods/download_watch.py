"""ダウンロードフォルダー監視.

ブラウザでダウンロードされたMODを検出し、内容を照合してから取り込み候補として通知する。
- ダウンロード途中のファイル (.crdownload / .part など) は無視
- サイズが一定時間変化しない & ZIPとして開ける & 排他的に開ける ことを完了条件とする
- ファイル名だけでMODを確定しない (SHA-1 > ファイル名+MOD情報 > 名前の類似 の順で照合)
- 元のダウンロードファイルは変更・削除しない
"""
from __future__ import annotations

import os
import re
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from msm.mods.importer import ManualItem
from msm.mods.metadata import ModMeta, read_mod

TEMP_SUFFIXES = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload", ".!ut", ".msmdownload")


@dataclass
class DetectedFile:
    path: str
    meta: ModMeta
    match: ManualItem | None
    confidence: str  # 確定 / 高 / 中 / 不一致
    reason: str


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def match_file(meta: ModMeta, expected: list[ManualItem]) -> tuple[ManualItem | None, str, str]:
    pending = [e for e in expected if not e.done]
    for e in pending:
        if e.sha1 and meta.sha1 and e.sha1.lower() == meta.sha1.lower():
            return e, "確定", "SHA-1ハッシュが一致しました"
    for e in pending:
        if e.file_name and e.file_name.lower() == meta.file_name.lower():
            if e.sha1:
                return None, "不一致", f"ファイル名は {e.name} と同じですが、ハッシュが一致しません (別バージョンの可能性)"
            return e, "高", "ファイル名が一致しました (ハッシュ情報なし)"
    nm = _norm(meta.display_name)
    ids = {_norm(i) for i in meta.mod_ids}
    for e in pending:
        en = _norm(e.name)
        if en and (en == nm or en in ids or (len(en) >= 5 and (en in _norm(meta.file_name) or nm and nm in en))):
            return e, "中", "MOD名が似ています (内容をご確認ください)"
    return None, "不一致", "取り込み待ちのMODと一致しませんでした"


def is_complete(path: Path) -> bool:
    try:
        with open(path, "rb+"):
            pass
    except OSError:
        return False  # ブラウザが書き込み中
    try:
        with zipfile.ZipFile(path) as zf:
            zf.namelist()
        return True
    except Exception:
        return False


class DownloadWatcher:
    def __init__(self, folder: Path, expected_provider: Callable[[], list[ManualItem]],
                 on_detect: Callable[[DetectedFile], None], interval: float = 2.0):
        self.folder = Path(folder)
        self.expected_provider = expected_provider
        self.on_detect = on_detect
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._baseline: dict[str, float] = {}
        self._sizes: dict[str, tuple[int, int]] = {}
        self._handled: set[str] = set()
        self.started_at = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.started_at = time.time()
        self._baseline = {}
        if self.folder.exists():
            for p in self.folder.iterdir():
                try:
                    self._baseline[p.name] = p.stat().st_mtime
                except OSError:
                    pass
        self._thread = threading.Thread(target=self._loop, daemon=True, name="download-watch")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._scan()
            except Exception:  # noqa: BLE001
                pass

    def _scan(self) -> None:
        if not self.folder.exists():
            return
        for p in self.folder.iterdir():
            name = p.name
            low = name.lower()
            if low.endswith(TEMP_SUFFIXES) or not low.endswith(".jar") or not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            key = f"{name}|{st.st_mtime}"
            if key in self._handled:
                continue
            if name in self._baseline and self._baseline[name] >= st.st_mtime:
                continue  # 監視開始前からあるファイル
            prev = self._sizes.get(name)
            if prev is None or prev[0] != st.st_size or st.st_size == 0:
                self._sizes[name] = (st.st_size, 0)
                continue
            stable_count = prev[1] + 1
            self._sizes[name] = (st.st_size, stable_count)
            if stable_count < 1:
                continue
            if not is_complete(p):
                continue
            self._handled.add(key)
            meta = read_mod(p)
            match, conf, reason = match_file(meta, self.expected_provider())
            if not meta.valid_jar:
                conf, reason = "不一致", "JARとして読み込めません"
            self.on_detect(DetectedFile(str(p), meta, match, conf, reason))

    def scan_existing(self, since: float = 0) -> list[DetectedFile]:
        """既にダウンロード済みのファイルから照合する (手動での再確認用)."""
        out = []
        if not self.folder.exists():
            return out
        expected = self.expected_provider()
        for p in self.folder.glob("*.jar"):
            try:
                if p.stat().st_mtime < since or not is_complete(p):
                    continue
            except OSError:
                continue
            meta = read_mod(p)
            match, conf, reason = match_file(meta, expected)
            if match is not None:
                out.append(DetectedFile(str(p), meta, match, conf, reason))
        return out


def default_watch_folder() -> Path:
    from msm.core.db import get_db
    from msm.core.paths import windows_downloads_dir

    custom = get_db().get_setting("download_watch_dir")
    return Path(custom) if custom and os.path.isdir(custom) else windows_downloads_dir()
