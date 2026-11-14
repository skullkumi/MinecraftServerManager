"""スケジュール・Discord・スタートアップ登録の単体テスト."""
import json
import os
import sys
import tempfile
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MSM_DATA_DIR", tempfile.mkdtemp(prefix="msm_unit_"))

from msm.server.schedule import describe, due_actions, parse_times  # noqa: E402

SCHED = {"enabled": True, "days": list(range(7)), "restart_times": ["04:00"], "stop_time": "02:00",
         "start_time": "18:00", "backup_times": ["12:00"], "warn_minutes": [5, 1]}


def kinds(now, fired=None):
    return sorted((a.kind, a.minutes) for a in due_actions(SCHED, now, fired or set()))


def test_parse_times():
    assert parse_times("4:00, 16:30、 25:00 x") == ["04:00", "16:30"]


def test_due_restart_and_warn():
    assert kinds(datetime(2026, 11, 16, 3, 55, 10)) == [("warn", 5)]
    assert kinds(datetime(2026, 11, 16, 3, 59, 0)) == [("warn", 1)]
    assert kinds(datetime(2026, 11, 16, 4, 0, 30)) == [("restart", 0)]
    assert kinds(datetime(2026, 11, 16, 4, 5, 0)) == []  # 時間外
    assert kinds(datetime(2026, 11, 16, 12, 1, 0)) == [("backup", 0)]
    assert kinds(datetime(2026, 11, 16, 18, 0, 5)) == [("start", 0)]


def test_dedupe_and_disabled():
    now = datetime(2026, 11, 16, 4, 0, 30)
    acts = due_actions(SCHED, now, set())
    assert due_actions(SCHED, now, {a.key for a in acts}) == []
    assert due_actions(dict(SCHED, enabled=False), now, set()) == []


def test_weekday_and_midnight():
    # 2026-11-16 は月曜。月曜を除外すると実行しない
    assert due_actions(dict(SCHED, days=[1, 2, 3, 4, 5, 6]), datetime(2026, 11, 16, 4, 0, 30), set()) == []
    # 00:00 の予告は前日 23:55 に出る
    s = dict(SCHED, restart_times=["00:00"], stop_time="", start_time="", backup_times=[])
    acts = due_actions(s, datetime(2026, 11, 16, 23, 55, 20), set())
    assert [(a.kind, a.minutes) for a in acts] == [("warn", 5)]
    assert "再起動 04:00" in describe(SCHED)


class _H(BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        _H.got.append(json.loads(self.rfile.read(n)))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


def test_discord_post_and_validation():
    from msm.integrations import discord

    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    code = discord.post(f"http://127.0.0.1:{srv.server_port}/hook", "テスト通知 @everyone")
    assert code == 204
    assert _H.got[0]["content"] == "テスト通知 @everyone" and _H.got[0]["allowed_mentions"] == {"parse": []}
    assert discord.valid_url("https://discord.com/api/webhooks/123456/abcDEF-_x")
    assert not discord.valid_url("https://evil.example.com/api/webhooks/1/x")
    discord.set_url("https://discord.com/api/webhooks/1/abc")
    assert discord.get_url() == "https://discord.com/api/webhooks/1/abc"
    from msm.core.db import get_db

    assert "abc" not in json.dumps(get_db().get_setting("discord_webhook_protected"))
    discord.set_url("")


def test_startup_registry_roundtrip(monkeypatch):
    from msm.core import startup

    monkeypatch.setattr(startup, "RUN_KEY", r"Software\MinecraftServerManagerTest")
    import winreg

    winreg.CreateKey(winreg.HKEY_CURRENT_USER, startup.RUN_KEY)
    try:
        startup.set_enabled(True)
        assert startup.is_enabled()
        startup.set_enabled(False)
        assert not startup.is_enabled()
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, startup.RUN_KEY)
