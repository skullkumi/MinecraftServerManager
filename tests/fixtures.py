"""統合テスト用の共通準備 (サーバー作成・実MODの取得)。既にあれば再利用する.

環境変数 MSM_DATA_DIR を指定して実行すること。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from msm.core import net  # noqa: E402
from msm.java import manager as jm  # noqa: E402
from msm.server import instance, ops  # noqa: E402
from msm.server.process import DONE_RE  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> bool:
    results.append((name, bool(cond), str(detail)))
    print(("OK  " if cond else "NG  ") + name + (f"  ({detail})" if detail else ""), flush=True)
    return bool(cond)


def summary() -> int:
    ng = [r for r in results if not r[1]]
    print(f"\n===== {len(results) - len(ng)}/{len(results)} OK =====")
    for r in ng:
        print("NG:", r[0], r[2])
    return 1 if ng else 0


def get_server(root: Path, st: str, mc: str, name: str | None = None):
    name = name or f"T {st} {mc}"
    for s in instance.list_servers():
        if s.name == name and s.path.exists():
            return s
    from msm.core.paths import safe_name

    d = root / safe_name(name)
    jm.refresh()
    dummy = instance.Server(id="x", name=name, directory=str(d), mc_version=mc, server_type=st)
    java, _r, note = jm.resolve_for_server(dummy)
    req = instance.CreateRequest(name=name, directory=str(d), mc_version=mc, server_type=st, max_mem_mb=3072,
                                 port=instance.suggest_port(25600), eula=True)
    return instance.create_server(req, java.path if java else None, lambda *a: None, lambda l: None)


def modrinth_file(slug: str, loader: str, mc: str, dest: Path, index: int = 0) -> dict:
    vs = net.get_json(f"https://api.modrinth.com/v2/project/{slug}/version?loaders=%5B%22{loader}%22%5D&game_versions=%5B%22{mc}%22%5D")
    v = vs[min(index, len(vs) - 1)]
    f = next((x for x in v["files"] if x.get("primary")), v["files"][0])
    dest.mkdir(parents=True, exist_ok=True)
    p = dest / f["filename"]
    if not p.exists():
        net.download(f["url"], p, expected_hash=("sha1", f["hashes"]["sha1"]))
    return {"path": p, "file": f, "version": v}


def start_wait(s, timeout=400):
    sp, _ = ops.start(s)
    ev = threading.Event()
    sp.on_line.append(lambda l: ev.set() if DONE_RE.search(l) else None)
    sp.on_state.append(lambda st: ev.set() if st in ("stopped", "crashed") else None)
    ev.wait(timeout)
    ok = sp.state == "running"
    return ok, sp


def stop(sp):
    if sp.running and not sp.stop(120):
        sp.kill()
    while sp.running:
        time.sleep(0.2)
