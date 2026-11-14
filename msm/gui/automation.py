"""自動処理: クラッシュ時の自動再起動・スケジュール・定期バックアップ・Discord通知・アプリ起動時の自動起動."""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer

from msm.core.util import log
from msm.gui import common as C
from msm.integrations import discord
from msm.server import instance, ops, schedule
from msm.server.process import JOIN_RE, LEAVE_RE, registry

DEFAULT_RESTART_MAX = 3
DEFAULT_RESTART_WINDOW_MIN = 10


class Automation(QObject):
    def __init__(self, ctx, window):
        super().__init__(window)
        self.ctx = ctx
        self.window = window
        self.fired: set[str] = set()
        self.crash_times: dict[str, list[float]] = {}
        self.restarting: set[str] = set()
        self._backup_busy = False
        self._auto_last: dict[str, float] = {}
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(20_000)
        ctx.console_line.connect(self.on_line, Qt.QueuedConnection)
        QTimer.singleShot(3000, self.autostart)
        self.last_tps: dict[str, dict] = {}
        self._tps_at: dict[str, float] = {}
        self._pruned = 0.0
        self.metrics_timer = QTimer(self)
        self.metrics_timer.timeout.connect(self.record_metrics)
        self.metrics_timer.start(15_000)

    # ---- 負荷の記録 ----
    def record_metrics(self) -> None:
        from msm.core.db import get_db
        from msm.server import metrics
        from msm.server.monitor import query_tps, tps_method

        now = time.time()
        poll = bool(get_db().get_setting("tps_polling", False))
        for sp in registry.running():
            if sp.state != "running":
                continue
            st = sp.stats()
            sid = sp.server.id
            t = self.last_tps.get(sid) or {}
            fresh = t if now - t.get("_at", 0) < 120 else {}
            metrics.record(sid, st.get("cpu"), st.get("rss"), len(sp.players), fresh.get("tps"), fresh.get("mspt"))
            if poll and now - self._tps_at.get(sid, 0) >= 60 and tps_method(sp.server.server_type, sp.server.mc_version)[0]:
                self._tps_at[sid] = now

                def job(x=sp):
                    r = query_tps(x)
                    r["_at"] = time.time()
                    return r

                C.bg().run(job, lambda r, e, i=sid: self.last_tps.__setitem__(i, r) if r else None)
        if now - self._pruned > 3600:
            self._pruned = now
            metrics.prune()

    # ---- 共通 ----
    def _notify(self, event: str, s, text: str) -> None:
        enabled = bool((s.options or {}).get("discord", True))
        try:
            discord.notifier().send(event, s.name, text, enabled)
        except Exception as e:  # noqa: BLE001
            log.warning("通知失敗: %s", e)

    def _start(self, s, reason: str) -> bool:
        s = instance.get_server(s.id) or s
        try:
            self.ctx.process(s)
            _sp, msg = ops.start(s)
            self.ctx.status.emit(f"{s.name}: {reason} - {msg}")
            if s.publish_config.get("tunnel_with_server") and s.publish_method in ("playit", "custom"):
                try:
                    self.ctx.start_tunnel(s)
                except Exception as e:  # noqa: BLE001
                    log.warning("トンネル起動失敗: %s", e)
            return True
        except Exception as e:  # noqa: BLE001
            self.ctx.status.emit(f"{s.name}: {reason}に失敗しました: {e}")
            self.window.notify(s.name, f"{reason}に失敗しました: {e}", True)
            self._notify("crash", s, f"⚠ {reason}に失敗しました: {e}")
            return False

    def _stop_then(self, s, then=None) -> None:
        sp = registry.find(s.id)
        if not sp or not sp.running:
            if then:
                then()
            return

        def job():
            ok = sp.stop(120)
            if not ok:
                sp.kill()
                deadline = time.time() + 15
                while sp.running and time.time() < deadline:
                    time.sleep(0.3)
            return ok

        C.bg().run(job, lambda r, e: then() if then else None)

    # ---- 起動時 ----
    def autostart(self) -> None:
        for s in self.ctx.servers():
            if (s.options or {}).get("autostart_on_launch") and not (registry.find(s.id) and registry.find(s.id).running):
                if instance.eula_accepted(s.path):
                    self._start(s, "アプリ起動時の自動起動")
                else:
                    self.ctx.status.emit(f"{s.name}: EULA未同意のため自動起動しませんでした")

    # ---- 状態変化 ----
    def on_state(self, sid: str, st: str) -> bool:
        """MainWindowから呼ばれる。クラッシュを自動再起動で処理した場合Trueを返す."""
        s = instance.get_server(sid)
        if not s:
            return False
        if st in ("stopped", "crashed"):
            self._upnp_close_if_needed(s)
        if st == "running":
            self._notify("start", s, "✅ サーバーが起動しました")
        elif st == "stopped":
            self._notify("stop", s, "⏹ サーバーが停止しました")
        elif st == "crashed":
            return self._on_crash(s)
        return False

    # ---- UPnP ----
    def _upnp_close_if_needed(self, s) -> None:
        from msm.publish import upnp

        if not (s.publish_config or {}).get("upnp_auto_close", True) or str(s.port) not in upnp.remembered():
            return

        def job():
            gw = upnp.gateway_for_remembered(s.port)
            if gw:
                upnp.delete_mapping(gw, s.port)
            return True

        C.bg().run(job, lambda r, e: self.ctx.status.emit(
            f"{s.name}: UPnPのポート開放を{'解除できませんでした: ' + str(e) if e else '解除しました'}"))

    def _upnp_renew(self) -> None:
        from msm.publish import upnp

        now = time.time()
        for port, info in upnp.remembered().items():
            lease = int(info.get("lease") or 0)
            if not lease or now - float(info.get("at", 0)) < lease / 2:
                continue
            if not any(sp.server.port == int(port) for sp in registry.running()):
                continue

            def job(p=int(port)):
                gw = upnp.gateway_for_remembered(p)
                if gw:
                    upnp.add_mapping(gw, p, lease)
                return True

            C.bg().run(job, lambda r, e: log.warning("UPnP延長失敗: %s", e) if e else None)

    def close_all_upnp(self) -> None:
        """アプリ終了時: 自動で閉じる設定のポートを閉じる."""
        from msm.publish import upnp

        for s in self.ctx.servers():
            if (s.publish_config or {}).get("upnp_auto_close", True) and str(s.port) in upnp.remembered():
                try:
                    gw = upnp.gateway_for_remembered(s.port)
                    if gw:
                        upnp.delete_mapping(gw, s.port)
                except Exception as e:  # noqa: BLE001
                    log.warning("UPnP解除失敗: %s", e)

    def _on_crash(self, s) -> bool:
        opt = s.options or {}
        sp = registry.find(s.id)
        code = sp.exit_code if sp else None
        if not opt.get("auto_restart"):
            self._notify("crash", s, f"❌ サーバーが異常終了しました (終了コード {code})")
            self.window.notify(s.name, f"サーバーが異常終了しました (終了コード {code})", True)
            return False
        now = time.time()
        window_sec = int(opt.get("auto_restart_window_min", DEFAULT_RESTART_WINDOW_MIN)) * 60
        limit = int(opt.get("auto_restart_max", DEFAULT_RESTART_MAX))
        times = [t for t in self.crash_times.get(s.id, []) if now - t < window_sec]
        if len(times) >= limit:
            self.crash_times[s.id] = times
            msg = (f"❌ {window_sec // 60}分以内に{limit}回異常終了したため、自動再起動を止めました。"
                   "「コンソール → トラブル診断」で原因を確認してください。")
            self._notify("crash", s, msg)
            self.window.notify(s.name, msg, True)
            return False
        times.append(now)
        self.crash_times[s.id] = times
        msg = f"⚠ 異常終了しました (終了コード {code})。10秒後に自動再起動します ({len(times)}/{limit}回目)"
        self._notify("crash", s, msg)
        self.window.notify(s.name, msg, True)
        self.ctx.status.emit(f"{s.name}: {msg}")
        QTimer.singleShot(10_000, lambda: self._start(s, "自動再起動"))
        return True

    def on_line(self, sid: str, line: str) -> None:
        m = JOIN_RE.search(line)
        if m:
            s = instance.get_server(sid)
            if s:
                self._notify("join", s, f"👋 {m.group(1)} が参加しました")
            return
        m = LEAVE_RE.search(line)
        if m:
            s = instance.get_server(sid)
            if s:
                self._notify("leave", s, f"{m.group(1)} が退出しました")

    # ---- 定期処理 ----
    def tick(self, now: datetime | None = None) -> list:
        now = now or datetime.now()
        done = []
        for s in self.ctx.servers():
            sched = (s.options or {}).get("schedule") or {}
            for a in schedule.due_actions(sched, now, self.fired):
                self.fired.add(a.key)
                done.append((s.id, a))
                self._do(s, a)
        self._auto_backup()
        self._upnp_renew()
        if len(self.fired) > 2000:
            self.fired = set(sorted(self.fired)[-500:])
        return done

    def _do(self, s, a: schedule.Action) -> None:
        sp = registry.find(s.id)
        running = bool(sp and sp.running)
        log.info("スケジュール実行: %s %s", s.name, a.key)
        if a.kind == "warn" and running:
            what = "再起動" if a.target == "restart" else "停止"
            sp.send_command(f"say [自動] {a.minutes}分後にサーバーを{what}します")
        elif a.kind == "restart" and running:
            self._notify("schedule", s, f"🔄 定時再起動 ({a.detail})")
            self._stop_then(s, lambda: self._start(s, "定時再起動"))
        elif a.kind == "stop" and running:
            self._notify("schedule", s, f"⏹ 定時停止 ({a.detail})")
            self._stop_then(s)
        elif a.kind == "start" and not running:
            self._notify("schedule", s, f"▶ 定時起動 ({a.detail})")
            self._start(s, "定時起動")
        elif a.kind == "backup":
            self._backup(s, "定時バックアップ")

    def _backup(self, s, note: str) -> None:
        if self._backup_busy:
            return
        self._backup_busy = True

        def job():
            from msm.backup import backup as bk

            from msm.core.db import get_db

            inc = bool(get_db().get_setting("backup_incremental", True))
            rec = ops.backup_with_server_control(s, ["world", "config"], "auto", note, incremental=inc)
            bk.prune(s.id, max(1, s.backup_keep), ("auto",))
            return rec

        def done(res, err):
            self._backup_busy = False
            if err:
                self.ctx.status.emit(f"{s.name}: {note}に失敗しました: {err}")
                self._notify("backup_fail", s, f"⚠ {note}に失敗しました: {err}")
            else:
                self.ctx.status.emit(f"{s.name}: {note}を作成しました")
                self._notify("schedule", s, f"💾 {note}を作成しました")

        C.bg().run(job, done)

    def _auto_backup(self) -> None:
        now = time.time()
        for s in self.ctx.servers():
            if s.auto_backup_minutes <= 0:
                continue
            sp = registry.find(s.id)
            if not sp or not sp.running or sp.state != "running":
                continue
            last = self._auto_last.get(s.id, sp.started_at or now)
            if now - last < s.auto_backup_minutes * 60:
                continue
            self._auto_last[s.id] = now
            self._backup(s, "定期自動バックアップ")
            break
