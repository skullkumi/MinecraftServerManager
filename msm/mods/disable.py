"""問題MODの無効化 (削除せず disabled_mods へ退避) と復元・履歴."""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from msm.core.db import get_db
from msm.mods.metadata import sha1_of
from msm.server.instance import Server


@dataclass
class ModEvent:
    id: int
    action: str
    file_name: str
    from_path: str
    to_path: str
    sha1: str
    size: int
    mtime: float
    reason: str
    created_at: float
    restored: bool


def disabled_dir(server: Server) -> Path:
    d = server.path / "disabled_mods"
    d.mkdir(parents=True, exist_ok=True)
    return d


def disable(server: Server, files: list[Path], reason: str) -> list[ModEvent]:
    out = []
    dd = disabled_dir(server)
    for f in files:
        f = Path(f)
        if not f.exists():
            continue
        st = f.stat()
        sha = sha1_of(f)
        dest = dd / f.name
        if dest.exists():
            dest = dd / f"{f.stem}.{time.strftime('%Y%m%d%H%M%S')}{f.suffix}"
        shutil.move(str(f), str(dest))
        os.utime(dest, (st.st_atime, st.st_mtime))
        cur = get_db().execute(
            "INSERT INTO mod_events(server_id,action,file_name,from_path,to_path,sha1,size,mtime,reason,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (server.id, "disable", f.name, str(f), str(dest), sha, st.st_size, st.st_mtime, reason, time.time()),
        )
        out.append(ModEvent(cur.lastrowid, "disable", f.name, str(f), str(dest), sha, st.st_size, st.st_mtime,
                            reason, time.time(), False))
    return out


def history(server: Server) -> list[ModEvent]:
    rows = get_db().query("SELECT * FROM mod_events WHERE server_id=? ORDER BY created_at DESC", (server.id,))
    return [ModEvent(r["id"], r["action"], r["file_name"], r["from_path"], r["to_path"], r["sha1"] or "",
                     r["size"] or 0, r["mtime"] or 0, r["reason"] or "", r["created_at"] or 0, bool(r["restored"]))
            for r in rows]


def restore(server: Server, event: ModEvent) -> Path:
    src = Path(event.to_path)
    if not src.exists():
        raise FileNotFoundError(f"退避先のファイルが見つかりません: {src}")
    dest = Path(event.from_path)
    if dest.exists():
        raise FileExistsError(f"元の場所に同名ファイルがあるため復元できません: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    get_db().execute("UPDATE mod_events SET restored=1 WHERE id=?", (event.id,))
    get_db().execute(
        "INSERT INTO mod_events(server_id,action,file_name,from_path,to_path,sha1,size,mtime,reason,created_at,restored) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,1)",
        (server.id, "restore", event.file_name, str(src), str(dest), event.sha1, event.size, event.mtime,
         f"復元 (無効化ID {event.id})", time.time()),
    )
    return dest


def active_disabled(server: Server) -> list[ModEvent]:
    """現在も退避中のMOD."""
    return [e for e in history(server) if e.action == "disable" and not e.restored and Path(e.to_path).exists()]
