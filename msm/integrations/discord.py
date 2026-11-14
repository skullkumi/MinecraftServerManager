"""Discord Webhook 通知 (任意機能)。WebhookのURLにはトークンが含まれるため、DPAPIで暗号化して保存する."""
from __future__ import annotations

import json
import queue
import re
import ssl
import threading
import time
import urllib.error
import urllib.request

from msm import USER_AGENT
from msm.core.db import get_db
from msm.core.secrets import protect, unprotect
from msm.core.util import log

URL_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w\-]+$")
EVENTS = {
    "start": "サーバーの起動完了",
    "stop": "サーバーの停止",
    "crash": "異常終了・自動再起動",
    "join": "プレイヤーの参加",
    "leave": "プレイヤーの退出",
    "schedule": "スケジュール実行 (再起動・バックアップ)",
    "backup_fail": "バックアップの失敗",
}
DEFAULT_EVENTS = {"start": True, "stop": True, "crash": True, "join": False, "leave": False, "schedule": False,
                  "backup_fail": True}


def valid_url(url: str) -> bool:
    return bool(URL_RE.match(url.strip()))


def get_url() -> str:
    return unprotect(get_db().get_setting("discord_webhook_protected", "") or "")


def set_url(url: str) -> None:
    get_db().set_setting("discord_webhook_protected", protect(url.strip()) if url.strip() else "")


def events() -> dict:
    e = dict(DEFAULT_EVENTS)
    e.update(get_db().get_setting("discord_events", {}) or {})
    return e


def set_events(e: dict) -> None:
    get_db().set_setting("discord_events", e)


def post(url: str, content: str, timeout: float = 10) -> int:
    body = json.dumps({"content": content[:1900], "username": "Minecraft Server Manager",
                       "allowed_mentions": {"parse": []}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    ctx = ssl.create_default_context() if url.startswith("https://") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class Notifier:
    """送信はバックグラウンドで順番に行う (Discordの送信制限に配慮して間隔を空ける)."""

    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self._t = threading.Thread(target=self._loop, daemon=True, name="discord")
        self._t.start()
        self.last_error = ""

    def send(self, event: str, server_name: str, text: str, enabled_for_server: bool = True) -> bool:
        if not enabled_for_server or not events().get(event):
            return False
        url = get_url()
        if not url or not valid_url(url):
            return False
        self.q.put((url, f"**[{server_name}]** {text}"))
        return True

    def _loop(self) -> None:
        while True:
            url, content = self.q.get()
            try:
                code = post(url, content)
                if code == 429:
                    time.sleep(5)
                    code = post(url, content)
                if code >= 300:
                    self.last_error = f"HTTP {code}"
                    log.warning("Discord通知失敗: HTTP %s", code)
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                log.warning("Discord通知失敗: %s", e)
            time.sleep(1.2)


_notifier: Notifier | None = None


def notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier
