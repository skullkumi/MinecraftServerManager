"""TPS / MSPT の取得.

サーバー種類ごとに公式に用意されたコマンドを使う。コマンドが無い組み合わせは「未対応」と表示する。
コマンドを送るとサーバーコンソールにも結果が表示される。
"""
from __future__ import annotations

from msm.core.util import mc_version_tuple
from msm.server.process import ServerProcess


def tps_method(server_type: str, mc: str) -> tuple[str | None, str]:
    t = mc_version_tuple(mc)
    if server_type in ("paper", "purpur"):
        return "tps", "Paper/Purpurの tps / mspt コマンド"
    if server_type == "forge":
        return "forge tps", "Forgeの forge tps コマンド"
    if server_type == "neoforge":
        return "neoforge tps", "NeoForgeの neoforge tps コマンド"
    if t and t >= (1, 20, 3):
        return "tick query", "バニラの tick query コマンド (1.20.3以降)"
    return None, "このサーバー種類・バージョンではTPS取得コマンドがありません (未対応)"


def query_tps(sp: ServerProcess, timeout: float = 8) -> dict:
    s = sp.server
    cmd, desc = tps_method(s.server_type, s.mc_version)
    res = {"tps": None, "mspt": None, "method": desc, "note": ""}
    if not cmd or not sp.running:
        if not cmd:
            res["note"] = "未対応"
        return res
    if cmd == "tps":
        m = sp.wait_for_line(r"TPS from last 1m, 5m, 15m:\s*\*?([\d.]+)", timeout, send="tps")
        if m:
            res["tps"] = float(m.group(1))
        m = sp.wait_for_line(r"(?:◴|\s)\s*([\d.]+)/([\d.]+)/([\d.]+)", timeout, send="mspt")
        if m:
            res["mspt"] = float(m.group(1))
    elif cmd in ("forge tps", "neoforge tps"):
        m = sp.wait_for_line(
            r"Overall\s*:\s*(?:Mean tick time:\s*([\d.]+)\s*ms\.\s*Mean TPS:\s*([\d.]+)|([\d.]+)\s*TPS\s*\(([\d.]+)\s*ms)",
            timeout, send=cmd)
        if m:
            if m.group(1):
                res["mspt"], res["tps"] = float(m.group(1)), float(m.group(2))
            else:
                res["tps"], res["mspt"] = float(m.group(3)), float(m.group(4))
    else:
        m = sp.wait_for_line(r"Average time per tick:\s*([\d.]+)\s*ms", timeout, send="tick query")
        if m:
            mspt = float(m.group(1))
            res["mspt"] = mspt
            res["tps"] = round(min(20.0, 1000.0 / mspt), 2) if mspt > 0 else 20.0
            res["note"] = "TPSはMSPTからの推定値"
    if res["tps"] is None and res["mspt"] is None:
        res["note"] = "応答を取得できませんでした"
    return res
