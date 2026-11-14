"""Java一覧の管理 (検出結果をDBに保存し、サーバーごとのJava解決を行う)."""
from __future__ import annotations

import json
import time

from msm.core.db import get_db
from msm.java import detect
from msm.java.detect import JavaInfo
from msm.java.requirements import JavaRequirement, choose_java, requirement_for


def _row_to_info(r: dict) -> JavaInfo:
    return JavaInfo(
        path=r["path"], version=r["version"] or "", major=r["major"] or 0, vendor=r["vendor"] or "",
        arch=r["arch"] or "", sources=json.loads(r["source"] or "[]"), managed=bool(r["managed"]), ok=bool(r["ok"]),
    )


def save(info: JavaInfo) -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO java_installs(path,version,major,vendor,arch,source,managed,ok,last_checked) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (info.path, info.version, info.major, info.vendor, info.arch, json.dumps(info.sources, ensure_ascii=False),
         int(info.managed), int(info.ok), time.time()),
    )


def refresh() -> list[JavaInfo]:
    """PC全体を再スキャンする。手動追加したJavaも再確認する."""
    found = detect.detect_all()
    known = {j.path.lower() for j in found}
    for r in get_db().query("SELECT * FROM java_installs"):
        if r["path"].lower() not in known:
            info = detect.probe(r["path"])
            info.sources = json.loads(r["source"] or "[]")
            info.managed = bool(r["managed"])
            found.append(info)
    get_db().execute("DELETE FROM java_installs")
    for j in found:
        save(j)
    return list_all()


def list_all() -> list[JavaInfo]:
    rows = get_db().query("SELECT * FROM java_installs ORDER BY major DESC, path")
    return [_row_to_info(r) for r in rows]


def add_manual(path: str) -> JavaInfo:
    info = detect.probe(path)
    info.sources = ["手動追加"]
    save(info)
    return info


def resolve_for_server(server) -> tuple[JavaInfo | None, JavaRequirement, str]:
    """サーバーで使うJavaを決める。戻り値: (Java, 要件, 説明)."""
    req = requirement_for(server.mc_version, server.server_type)
    installs = list_all()
    if not installs:
        installs = refresh()
    if server.java_mode == "manual" and server.java_path:
        info = next((j for j in installs if j.path.lower() == server.java_path.lower()), None)
        if info is None:
            info = detect.probe(server.java_path)
        if not info.ok:
            return None, req, f"手動指定のJavaが使用できません: {server.java_path} ({info.error})"
        note = "手動指定"
        if not req.accepts(info.major):
            note += f" (警告: 要件 {req.describe()} を満たしていません)"
        return info, req, note
    chosen = choose_java(installs, req)
    if chosen is None:
        return None, req, f"条件に合うJavaがありません。必要: {req.describe()}"
    return chosen, req, "自動選択"
