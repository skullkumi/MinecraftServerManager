"""バックアップの作成・復元・整理."""
from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from msm.core.db import get_db
from msm.core.paths import default_backup_root
from msm.core.util import CancelToken, dir_size, free_space, human_size, log
from msm.server.instance import Server

CONFIG_TARGETS = ["server.properties", "config", "defaultconfigs", "scripts", "kubejs", "whitelist.json", "ops.json",
                  "banned-players.json", "banned-ips.json", "eula.txt", "user_jvm_args.txt"]
TARGET_LABELS = {
    "world": "ワールドデータ",
    "mods": "MOD (mods / plugins)",
    "config": "設定 (server.properties / config / scripts / kubejs など)",
}


class BackupError(Exception):
    pass


@dataclass
class BackupRecord:
    id: int
    server_id: str
    path: str
    kind: str
    targets: list[str]
    size: int
    created_at: float
    note: str


def _unique(path: Path) -> Path:
    """同じ秒に作られても上書きしないよう番号を付ける."""
    if not path.exists() and not path.with_name(path.name + ".tmp").exists():
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not cand.exists() and not cand.with_name(cand.name + ".tmp").exists():
            return cand
        i += 1


def backup_root() -> Path:
    root = get_db().get_setting("backup_dir") or str(default_backup_root())
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def world_dirs(server: Server) -> list[Path]:
    level = server.level_name()
    out = []
    for name in (level, f"{level}_nether", f"{level}_the_end"):
        p = server.path / name
        if p.is_dir():
            out.append(p)
    return out


def collect_paths(server: Server, targets: list[str]) -> list[Path]:
    paths: list[Path] = []
    if "world" in targets:
        paths += world_dirs(server)
    if "mods" in targets:
        for name in ("mods", "plugins"):
            p = server.path / name
            if p.exists():
                paths.append(p)
    if "config" in targets:
        for name in CONFIG_TARGETS:
            p = server.path / name
            if p.exists():
                paths.append(p)
    for t in targets:
        if t.startswith("path:"):
            p = server.path / t[5:]
            if p.exists():
                paths.append(p)
    return paths


def estimate_size(paths: list[Path]) -> int:
    return sum(dir_size(p) if p.is_dir() else p.stat().st_size for p in paths)


def create_backup(server: Server, targets: list[str], kind: str = "manual", note: str = "",
                  progress=None, cancel: CancelToken | None = None) -> BackupRecord:
    """ZIPでバックアップを作る。容量不足は事前に検出する.

    サーバー稼働中のワールドバックアップは、呼び出し側で save-off / save-all を行うこと
    (manager.backup_with_server_control を使う)。
    """
    paths = collect_paths(server, targets)
    if not paths:
        raise BackupError("バックアップ対象のファイルがありません")
    need = estimate_size(paths)
    dest_dir = backup_root() / server.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    free = free_space(dest_dir)
    if free < need * 1.1 + 100 * 1024 * 1024:
        raise BackupError(f"バックアップ先の空き容量が不足しています (必要: 約{human_size(need)} / 空き: {human_size(free)})")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = _unique(dest_dir / f"{stamp}_{kind}.zip")
    tmp = dest.with_suffix(".zip.tmp")
    files: list[tuple[Path, str]] = []
    for p in paths:
        if p.is_dir():
            for root, _d, fs in os.walk(p):
                for f in fs:
                    full = Path(root) / f
                    if f == "session.lock":
                        continue
                    files.append((full, full.relative_to(server.path).as_posix()))
        else:
            files.append((p, p.relative_to(server.path).as_posix()))
    total = len(files) or 1
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for i, (full, arc) in enumerate(files):
                if cancel and cancel.cancelled:
                    raise BackupError("キャンセルされました")
                try:
                    zf.write(full, arc)
                except (PermissionError, OSError) as e:
                    log.warning("バックアップできないファイル: %s (%s)", full, e)
                if progress and i % 50 == 0:
                    progress(f"バックアップ中: {arc}", i / total)
            zf.writestr(".msm_backup.json", json.dumps(
                {"server_id": server.id, "server_name": server.name, "targets": targets, "kind": kind,
                 "created_at": time.time(), "note": note}, ensure_ascii=False))
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    size = dest.stat().st_size
    cur = get_db().execute(
        "INSERT INTO backups(server_id,path,kind,targets,size,created_at,note) VALUES(?,?,?,?,?,?,?)",
        (server.id, str(dest), kind, json.dumps(targets), size, time.time(), note),
    )
    log.info("バックアップ作成: %s", dest)
    return BackupRecord(cur.lastrowid, server.id, str(dest), kind, targets, size, time.time(), note)


def backup_file(server: Server, rel_paths: list[str], note: str) -> BackupRecord | None:
    """設定変更前の小さなバックアップ (例: server.properties)."""
    targets = [f"path:{r}" for r in rel_paths if (server.path / r).exists()]
    if not targets:
        return None
    return create_backup(server, targets, kind="auto-change", note=note)


def list_backups(server_id: str) -> list[BackupRecord]:
    rows = get_db().query("SELECT * FROM backups WHERE server_id=? ORDER BY created_at DESC", (server_id,))
    out = []
    for r in rows:
        out.append(BackupRecord(r["id"], r["server_id"], r["path"], r["kind"], json.loads(r["targets"] or "[]"),
                                r["size"] or 0, r["created_at"] or 0, r["note"] or ""))
    return out


# ---------------------------------------------------------------- 差分 (スナップショット) バックアップ
SNAP_MANIFEST = ".msm_snapshot.json"


def _snapshots_dir(server: Server) -> Path:
    d = backup_root() / server.id / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _latest_snapshot(server: Server) -> Path | None:
    snaps = [p for p in _snapshots_dir(server).iterdir() if (p / SNAP_MANIFEST).exists()]
    return max(snaps, key=lambda p: p.name) if snaps else None


def create_snapshot(server: Server, targets: list[str], kind: str = "auto", note: str = "",
                    progress=None, cancel: CancelToken | None = None) -> BackupRecord:
    """差分バックアップ: 前回から変わったファイルだけをコピーし、変わっていないファイルは前回分をハードリンクで共有する.

    各スナップショットは完全なフォルダーとして見えるため、どの時点からでもそのまま復元できる。
    (ハードリンクが使えないドライブでは通常のコピーになる)
    """
    paths = collect_paths(server, targets)
    if not paths:
        raise BackupError("バックアップ対象のファイルがありません")
    files: list[tuple[Path, str]] = []
    for p in paths:
        if p.is_dir():
            for root, _d, fs in os.walk(p):
                for f in fs:
                    if f == "session.lock":
                        continue
                    full = Path(root) / f
                    files.append((full, full.relative_to(server.path).as_posix()))
        else:
            files.append((p, p.relative_to(server.path).as_posix()))
    prev = _latest_snapshot(server)
    prev_manifest = {}
    if prev:
        try:
            prev_manifest = json.loads((prev / SNAP_MANIFEST).read_text("utf-8")).get("files", {})
        except Exception:
            prev_manifest = {}
    changed_bytes = 0
    plan = []
    for full, rel in files:
        try:
            st = full.stat()
        except OSError:
            continue
        sig = [st.st_size, st.st_mtime_ns]
        same = prev is not None and prev_manifest.get(rel) == sig and (prev / rel).exists()
        if not same:
            changed_bytes += st.st_size
        plan.append((full, rel, sig, same))
    dest_root = _snapshots_dir(server)
    free = free_space(dest_root)
    if free < changed_bytes * 1.1 + 100 * 1024 * 1024:
        raise BackupError(f"バックアップ先の空き容量が不足しています (必要: 約{human_size(changed_bytes)} / 空き: {human_size(free)})")
    dest = _unique(dest_root / f"{time.strftime('%Y%m%d-%H%M%S')}_{kind}")
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    manifest = {}
    linked = copied = 0
    try:
        for i, (full, rel, sig, same) in enumerate(plan):
            if cancel and cancel.cancelled:
                raise BackupError("キャンセルされました")
            out = tmp / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            done = False
            if same:
                try:
                    os.link(prev / rel, out)
                    linked += 1
                    done = True
                except OSError:
                    pass
            if not done:
                try:
                    shutil.copy2(full, out)
                    copied += 1
                except (PermissionError, OSError) as e:
                    log.warning("バックアップできないファイル: %s (%s)", full, e)
                    continue
            manifest[rel] = sig
            if progress and i % 100 == 0:
                progress(f"差分バックアップ中: {rel}", i / max(1, len(plan)))
        (tmp / SNAP_MANIFEST).write_text(json.dumps({
            "server_id": server.id, "server_name": server.name, "targets": targets, "kind": kind, "note": note,
            "created_at": time.time(), "base": prev.name if prev else "", "files": manifest}, ensure_ascii=False), "utf-8")
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    note2 = (note + " " if note else "") + f"(差分: 変更 {copied}件 / 共有 {linked}件)"
    cur = get_db().execute(
        "INSERT INTO backups(server_id,path,kind,targets,size,created_at,note) VALUES(?,?,?,?,?,?,?)",
        (server.id, str(dest), kind, json.dumps(targets), changed_bytes, time.time(), note2))
    log.info("差分バックアップ作成: %s (変更 %d / 共有 %d)", dest, copied, linked)
    return BackupRecord(cur.lastrowid, server.id, str(dest), kind, targets, changed_bytes, time.time(), note2)


def is_snapshot(rec: "BackupRecord") -> bool:
    return Path(rec.path).is_dir()


def restore_backup(server: Server, rec: BackupRecord, progress=None) -> BackupRecord | None:
    """復元する。サーバーは停止していること。復元前に現在の状態を自動バックアップする."""
    zp = Path(rec.path)
    if not zp.exists():
        raise BackupError(f"バックアップファイルが見つかりません: {zp}")
    pre = None
    try:
        pre = create_backup(server, rec.targets, kind="pre-restore", note=f"復元前の自動バックアップ (復元元ID {rec.id})")
    except BackupError as e:
        if "対象のファイルがありません" not in str(e):
            raise
    root = server.path.resolve()
    if zp.is_dir():
        names = [p.relative_to(zp).as_posix() for p in zp.rglob("*") if p.is_file() and p.name != SNAP_MANIFEST]
    else:
        with zipfile.ZipFile(zp) as zf:
            names = [n for n in zf.namelist() if n != ".msm_backup.json"]
    for n in names:
        dest = (server.path / n).resolve()
        if not str(dest).startswith(str(root)):
            raise BackupError("バックアップに不正なパスが含まれています")
    # ワールドを丸ごと戻す場合は、残骸が混ざらないよう既存ワールドを退避してから展開する
    top_dirs = {n.split("/")[0] for n in names if "/" in n}
    if "world" in rec.targets:
        trash = server.path / ".msm" / "replaced" / time.strftime("%Y%m%d-%H%M%S")
        for d in top_dirs:
            if (server.path / d).is_dir() and d in {w.name for w in world_dirs(server)}:
                trash.mkdir(parents=True, exist_ok=True)
                shutil.move(str(server.path / d), str(trash / d))
    if zp.is_dir():
        for i, n in enumerate(names):
            out = server.path / n
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(zp / n, out)  # コピーする (ハードリンクのままだとバックアップ側も書き換わるため)
            if progress and i % 100 == 0:
                progress(f"復元中: {n}", i / max(1, len(names)))
    else:
        with zipfile.ZipFile(zp) as zf:
            for i, n in enumerate(names):
                if n.endswith("/"):
                    continue
                zf.extract(n, server.path)
                if progress and i % 50 == 0:
                    progress(f"復元中: {n}", i / max(1, len(names)))
    return pre


def _remove_path(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink(missing_ok=True)


def prune(server_id: str, keep: int, kinds: tuple[str, ...] = ("auto", "auto-change")) -> int:
    """古い自動バックアップを削除する (手動バックアップは削除しない).

    差分バックアップはハードリンクで共有しているため、古いものを消しても新しいものは壊れない。
    """
    recs = [r for r in list_backups(server_id) if r.kind in kinds]
    removed = 0
    for r in recs[keep:]:
        try:
            _remove_path(Path(r.path))
        except OSError:
            continue
        get_db().execute("DELETE FROM backups WHERE id=?", (r.id,))
        removed += 1
    return removed


def delete_backup(rec: BackupRecord) -> None:
    _remove_path(Path(rec.path))
    get_db().execute("DELETE FROM backups WHERE id=?", (rec.id,))
