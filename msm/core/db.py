"""SQLiteによるデータ管理 (サーバー登録・Java・バックアップ履歴・MOD無効化履歴・設定)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from msm.core.paths import app_data_dir

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""",
    """CREATE TABLE IF NOT EXISTS servers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        directory TEXT NOT NULL,
        mc_version TEXT NOT NULL,
        server_type TEXT NOT NULL,
        loader_version TEXT DEFAULT '',
        java_mode TEXT DEFAULT 'auto',
        java_path TEXT DEFAULT '',
        min_mem_mb INTEGER DEFAULT 1024,
        max_mem_mb INTEGER DEFAULT 4096,
        port INTEGER DEFAULT 25565,
        max_players INTEGER DEFAULT 10,
        jvm_args TEXT DEFAULT '',
        launch TEXT DEFAULT '{}',
        publish_method TEXT DEFAULT 'lan',
        publish_config TEXT DEFAULT '{}',
        auto_backup_minutes INTEGER DEFAULT 0,
        backup_keep INTEGER DEFAULT 10,
        created_at REAL,
        notes TEXT DEFAULT '',
        options TEXT DEFAULT '{}'
    )""",
    """CREATE TABLE IF NOT EXISTS java_installs (
        path TEXT PRIMARY KEY,
        version TEXT,
        major INTEGER,
        vendor TEXT,
        arch TEXT,
        source TEXT,
        managed INTEGER DEFAULT 0,
        ok INTEGER DEFAULT 1,
        last_checked REAL
    )""",
    """CREATE TABLE IF NOT EXISTS backups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id TEXT,
        path TEXT,
        kind TEXT,
        targets TEXT,
        size INTEGER,
        created_at REAL,
        note TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS mod_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id TEXT,
        action TEXT,
        file_name TEXT,
        from_path TEXT,
        to_path TEXT,
        sha1 TEXT,
        size INTEGER,
        mtime REAL,
        reason TEXT,
        created_at REAL,
        restored INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS metrics (
        server_id TEXT,
        ts REAL,
        cpu REAL,
        rss INTEGER,
        players INTEGER,
        tps REAL,
        mspt REAL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_metrics ON metrics(server_id, ts)""",
    """CREATE TABLE IF NOT EXISTS mod_info_cache (
        sha1 TEXT PRIMARY KEY,
        source TEXT,
        data TEXT,
        fetched_at REAL
    )""",
]


class Database:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else app_data_dir() / "msm.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            for stmt in SCHEMA:
                self._conn.execute(stmt)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """古いバージョンのDBに列を追加する."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(servers)").fetchall()}
        if "options" not in cols:
            self._conn.execute("ALTER TABLE servers ADD COLUMN options TEXT DEFAULT '{}'")

    # ---- 汎用 ----
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- 設定 ----
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self.query("SELECT value FROM settings WHERE key=?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except Exception:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    # ---- MOD情報キャッシュ ----
    def cache_get(self, sha1: str, source: str, max_age: float = 7 * 86400) -> Any:
        rows = self.query(
            "SELECT data, fetched_at FROM mod_info_cache WHERE sha1=? AND source=?", (sha1, source)
        )
        if rows and time.time() - (rows[0]["fetched_at"] or 0) < max_age:
            return json.loads(rows[0]["data"])
        return None

    def cache_put(self, sha1: str, source: str, data: Any) -> None:
        self.execute(
            "INSERT OR REPLACE INTO mod_info_cache(sha1,source,data,fetched_at) VALUES(?,?,?,?)",
            (sha1, source, json.dumps(data, ensure_ascii=False), time.time()),
        )


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def set_db(db: Database) -> None:
    global _db
    _db = db
