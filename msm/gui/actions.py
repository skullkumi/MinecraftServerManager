"""複数の画面で使うサーバー操作 (起動・停止・再起動・Java導入)."""
from __future__ import annotations

import threading
import time

from PySide6.QtWidgets import QWidget

from msm.gui import common as C
from msm.java import adoptium
from msm.java import manager as jm
from msm.server import instance, ops
from msm.server.instance import Server
from msm.server.process import EulaNotAccepted, StartError, registry


def ensure_eula(parent: QWidget, server: Server) -> bool:
    if instance.eula_accepted(server.path):
        return True
    choice = C.ask_choice(
        parent,
        "Minecraftサーバーを起動するには、Minecraft EULA (使用許諾契約) への同意が必要です。\n\n"
        "EULA: https://aka.ms/MinecraftEULA\n\n内容を確認し、同意する場合は「同意する」を押してください。",
        ["同意する", "EULAを読む (ブラウザ)"], "Minecraft EULA")
    if choice == 1:
        C.open_url(parent, "https://aka.ms/MinecraftEULA", ask=False)
        return ensure_eula(parent, server)
    if choice == 0:
        instance.write_eula(server.path, True)
        return True
    return False


def install_java_for(parent: QWidget, server: Server, on_done=None) -> None:
    from msm.java.requirements import requirement_for

    req = requirement_for(server.mc_version, server.server_type)
    major = req.recommended

    def find(p, l, c):
        return adoptium.find_package(major, "jre")

    def got(pkg):
        if not C.confirm(parent, f"「{server.name}」には {req.describe()} が必要ですが、PCに見つかりませんでした。\n\n"
                                 f"次のJavaをダウンロードして導入しますか？\n\n{pkg.describe()}\n\n"
                                 "導入先: アプリ専用フォルダー (環境変数や他のJavaは変更しません)"):
            return

        def inst(p, l, c):
            info = adoptium.install(pkg, p, c)
            jm.save(info)
            jm.refresh()
            if server.java_mode == "manual":
                s2 = instance.get_server(server.id)
                if s2:
                    s2.java_path = info.path
                    instance.save_server(s2)
            return info

        def fin(info):
            C.info(parent, f"Java {info.major} を導入しました。\n{info.path}\n\nこのサーバーに自動で割り当てられます。")
            if on_done:
                on_done()

        C.run_task(parent, f"Java {major} を導入しています", inst, fin)

    C.run_task(parent, "Javaの配布情報を確認しています", find, got)


def start_server(parent: QWidget, ctx, server: Server, after=None) -> bool:
    sp = registry.find(server.id)
    if sp and sp.running:
        return True
    if not ensure_eula(parent, server):
        return False
    warns = ops.check_port_conflicts(server)
    if warns and not any("稼働中" in w for w in warns):
        ctx.status.emit("注意: " + " / ".join(warns))
    java, req, note = jm.resolve_for_server(server)
    if java is None:
        if server.java_mode == "manual":
            C.error(parent, note + "\nサーバー設定でJavaを選び直してください。")
            return False
        install_java_for(parent, server, on_done=lambda: start_server(parent, ctx, server, after))
        return False
    if "警告" in note and not C.confirm(parent, note + "\n\nこのまま起動しますか？"):
        return False
    try:
        sp = ctx.process(server)
        sp.server = server
        _sp, msg = ops.start(server)
        ctx.status.emit(f"{server.name}: {msg}")
    except EulaNotAccepted:
        return False
    except StartError as e:
        C.error(parent, str(e))
        return False
    except Exception as e:  # noqa: BLE001
        C.error(parent, f"起動に失敗しました:\n{e}")
        return False
    if server.publish_config.get("tunnel_with_server") and server.publish_method in ("playit", "custom"):
        try:
            ctx.start_tunnel(server)
        except Exception as e:  # noqa: BLE001
            C.warn(parent, f"公開用トンネルを起動できませんでした:\n{e}")
    if after:
        after()
    return True


def stop_server(parent: QWidget, ctx, server: Server, then=None) -> None:
    sp = registry.find(server.id)
    if not sp or not sp.running:
        if then:
            then()
        return

    def job(p, l, c):
        p("サーバーを安全に停止しています (ワールドを保存中)...", None)
        return sp.stop(90)

    def done(ok):
        if not ok:
            if C.confirm(parent, "90秒待っても停止しませんでした。強制終了しますか？\n"
                                 "(保存中のデータが失われ、ワールドが破損する恐れがあります)", danger=True):
                sp.kill()
                deadline = time.time() + 10
                while sp.running and time.time() < deadline:
                    time.sleep(0.2)
            else:
                return
        if then:
            then()

    C.run_task(parent, f"{server.name} を停止しています", job, done, cancellable=False)


def restart_server(parent: QWidget, ctx, server: Server) -> None:
    stop_server(parent, ctx, server, then=lambda: start_server(parent, ctx, instance.get_server(server.id) or server))


def stop_all(parent: QWidget, ctx) -> None:
    running = registry.running()
    if not running:
        C.info(parent, "稼働中のサーバーはありません。")
        return
    if not C.confirm(parent, f"稼働中の {len(running)} 台のサーバーをすべて停止しますか？"):
        return

    def job(p, l, c):
        results = {}
        threads = []
        for sp in running:
            def one(x=sp):
                results[x.server.name] = x.stop(90)
            t = threading.Thread(target=one)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        return results

    def done(res):
        failed = [n for n, ok in res.items() if not ok]
        if failed:
            C.warn(parent, "次のサーバーは停止しませんでした:\n" + "\n".join(failed) + "\n個別に強制終了してください。")

    C.run_task(parent, "すべてのサーバーを停止しています", job, done, cancellable=False)


def restart_all(parent: QWidget, ctx) -> None:
    running = [sp.server for sp in registry.running()]
    if not running:
        C.info(parent, "稼働中のサーバーはありません。")
        return
    if not C.confirm(parent, f"稼働中の {len(running)} 台のサーバーをすべて再起動しますか？"):
        return

    def job(p, l, c):
        for sp in registry.running():
            sp.stop(90)
        return True

    def done(_):
        for s in running:
            start_server(parent, ctx, instance.get_server(s.id) or s)

    C.run_task(parent, "すべてのサーバーを再起動しています", job, done, cancellable=False)
