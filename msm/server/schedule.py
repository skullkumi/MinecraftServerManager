"""スケジュール (定時再起動・自動起動/停止・定時バックアップ) の判定ロジック.

options["schedule"] = {
  "enabled": true,
  "days": [0,1,2,3,4,5,6],          # 0=月曜
  "restart_times": ["04:00"],
  "start_time": "18:00",            # 毎日この時刻に起動 (空欄で無効)
  "stop_time": "02:00",             # 毎日この時刻に停止 (空欄で無効)
  "backup_times": ["12:00"],
  "warn_minutes": [5, 1]            # 再起動・停止の何分前にプレイヤーへ予告するか
}
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

WINDOW = timedelta(seconds=150)  # この時間内なら実行 (タイマーの遅れやスリープ復帰を許容)
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


@dataclass
class Action:
    kind: str  # restart / start / stop / backup / warn
    key: str  # 重複実行防止用 (日付込み)
    detail: str = ""
    minutes: int = 0  # warn の残り分数
    target: str = ""  # warn の対象 (restart/stop)


def parse_times(text: str | list) -> list[str]:
    items = text if isinstance(text, list) else re.split(r"[,、\s]+", text or "")
    out = []
    for t in items:
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(t).strip())
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            out.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    return sorted(set(out))


def _at(now: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def due_actions(sched: dict, now: datetime, fired: set[str]) -> list[Action]:
    if not sched or not sched.get("enabled"):
        return []
    days = sched.get("days", list(range(7)))
    out: list[Action] = []

    def hit(kind: str, hhmm: str, detail: str = "") -> None:
        for day_offset in (0, -1):  # 日付をまたいだ直後も考慮
            base = now + timedelta(days=day_offset)
            t = _at(base, hhmm)
            if t.weekday() not in days:
                continue
            if t <= now < t + WINDOW:
                key = f"{kind}:{t:%Y-%m-%d %H:%M}"
                if key not in fired:
                    out.append(Action(kind, key, detail or hhmm))

    def warn(target: str, hhmm: str) -> None:
        for mins in sched.get("warn_minutes", [5, 1]):
            for day_offset in (0, 1):
                t = _at(now + timedelta(days=day_offset), hhmm)
                if t.weekday() not in days:
                    continue
                w = t - timedelta(minutes=int(mins))
                if w <= now < min(w + WINDOW, t):  # 予定時刻を過ぎたら予告しない
                    key = f"warn-{target}-{mins}:{t:%Y-%m-%d %H:%M}"
                    if key not in fired:
                        out.append(Action("warn", key, hhmm, int(mins), target))

    for t in parse_times(sched.get("restart_times", [])):
        warn("restart", t)
        hit("restart", t)
    if parse_times(sched.get("stop_time", "")):
        t = parse_times(sched["stop_time"])[0]
        warn("stop", t)
        hit("stop", t)
    if parse_times(sched.get("start_time", "")):
        hit("start", parse_times(sched["start_time"])[0])
    for t in parse_times(sched.get("backup_times", [])):
        hit("backup", t)
    return out


def describe(sched: dict) -> str:
    if not sched or not sched.get("enabled"):
        return "スケジュール: 無効"
    days = sched.get("days", list(range(7)))
    d = "毎日" if len(days) == 7 else "・".join(WEEKDAYS[i] for i in sorted(days))
    parts = []
    if parse_times(sched.get("restart_times", [])):
        parts.append("再起動 " + ", ".join(parse_times(sched["restart_times"])))
    if parse_times(sched.get("start_time", "")):
        parts.append("起動 " + parse_times(sched["start_time"])[0])
    if parse_times(sched.get("stop_time", "")):
        parts.append("停止 " + parse_times(sched["stop_time"])[0])
    if parse_times(sched.get("backup_times", [])):
        parts.append("バックアップ " + ", ".join(parse_times(sched["backup_times"])))
    return f"スケジュール ({d}): " + (" / ".join(parts) or "設定なし")
