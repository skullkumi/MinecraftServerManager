"""実際にサーバーを作成→起動→停止する統合テスト (ネットワーク・時間が必要).

使い方: python tests/integration_servers.py <作業フォルダー> [type:mc ...]
例:     python tests/integration_servers.py D:/tmp/msm_it vanilla:26.3 fabric:1.20.1
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from msm.java import manager as jm  # noqa: E402
from msm.server import instance, ops  # noqa: E402
from msm.server.process import DONE_RE  # noqa: E402

DEFAULT = ["vanilla:26.3", "paper:26.3", "fabric:1.20.1", "forge:1.20.1", "neoforge:1.21.1"]


def run_one(root: Path, spec: str) -> tuple[str, bool, str]:
    st, mc = spec.split(":")
    d = root / f"{st}-{mc}"
    req = instance.CreateRequest(name=f"IT {st} {mc}", directory=str(d), mc_version=mc, server_type=st,
                                 max_mem_mb=3072, min_mem_mb=1024, port=instance.suggest_port(25600), eula=True)
    jm.refresh()
    dummy = instance.Server(id="x", name="x", directory=str(d), mc_version=mc, server_type=st)
    java, _req, note = jm.resolve_for_server(dummy)
    print(f"[{spec}] Java: {java.label if java else None} {note}", flush=True)
    t0 = time.time()
    s = instance.create_server(req, java.path if java else None,
                               lambda m, f=None: None, lambda l: print(f"  [{spec} install] {l}"[:200], flush=True))
    print(f"[{spec}] 作成完了 {time.time() - t0:.0f}s launch={s.launch}", flush=True)
    sp, msg = ops.start(s)
    print(f"[{spec}] {msg}", flush=True)
    done = threading.Event()
    sp.on_line.append(lambda l: done.set() if DONE_RE.search(l) else None)
    sp.on_state.append(lambda st_: done.set() if st_ in ("stopped", "crashed") else None)
    done.wait(900)
    ok = sp.state == "running"
    tail = "\n".join(list(sp.lines)[-8:])
    if ok:
        sp.send_command("list")
        time.sleep(2)
        stopped = sp.stop(120)
        print(f"[{spec}] 停止: {stopped} state={sp.state} exit={sp.exit_code}", flush=True)
    else:
        sp.kill()
    return spec, ok, tail


def main() -> None:
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    specs = sys.argv[2:] or DEFAULT
    results = []
    for spec in specs:
        try:
            results.append(run_one(root, spec))
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            results.append((spec, False, str(e)))
    print("\n===== 結果 =====")
    for spec, ok, tail in results:
        print(f"{'OK ' if ok else 'NG '} {spec}")
        if not ok:
            print(tail)


if __name__ == "__main__":
    main()
