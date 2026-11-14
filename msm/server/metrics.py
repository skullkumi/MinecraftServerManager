"""負荷の記録 (CPU・メモリ・人数・TPS/MSPT)。SQLiteに7日分を保存する."""
from __future__ import annotations

import csv
import time
from pathlib import Path

from msm.core.db import get_db

KEEP_DAYS = 7


def record(server_id: str, cpu, rss, players, tps=None, mspt=None, ts: float | None = None) -> None:
    get_db().execute("INSERT INTO metrics(server_id,ts,cpu,rss,players,tps,mspt) VALUES(?,?,?,?,?,?,?)",
                     (server_id, ts or time.time(), cpu, rss, players, tps, mspt))


def query(server_id: str, since: float) -> list[dict]:
    return get_db().query("SELECT ts,cpu,rss,players,tps,mspt FROM metrics WHERE server_id=? AND ts>=? ORDER BY ts",
                          (server_id, since))


def prune(days: int = KEEP_DAYS) -> None:
    get_db().execute("DELETE FROM metrics WHERE ts < ?", (time.time() - days * 86400,))


def export_csv(server_id: str, since: float, path: Path) -> int:
    rows = query(server_id, since)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["日時", "CPU使用率(%)", "メモリ使用量(MB)", "プレイヤー数", "TPS", "MSPT(ms)"])
        for r in rows:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])),
                        "" if r["cpu"] is None else f"{r['cpu']:.1f}",
                        "" if r["rss"] is None else int(r["rss"] / 1024 / 1024), r["players"] if r["players"] is not None else "",
                        "" if r["tps"] is None else f"{r['tps']:.2f}", "" if r["mspt"] is None else f"{r['mspt']:.2f}"])
    return len(rows)
