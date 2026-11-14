"""差分 (スナップショット) バックアップの単体テスト."""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MSM_DATA_DIR", tempfile.mkdtemp(prefix="msm_unit_"))


def test_snapshot_incremental(tmp_path):
    from msm.backup import backup as bk
    from msm.core import db as dbmod
    from msm.server.instance import Server

    dbmod.set_db(dbmod.Database(tmp_path / "t.db"))
    dbmod.get_db().set_setting("backup_dir", str(tmp_path / "bk"))
    sd = tmp_path / "srv"
    (sd / "world" / "region").mkdir(parents=True)
    (sd / "server.properties").write_text("level-name=world\n")
    (sd / "world" / "level.dat").write_bytes(b"L1")
    for i in range(20):
        (sd / "world" / "region" / f"r.{i}.0.mca").write_bytes(os.urandom(50_000))
    s = Server(id="snap", name="t", directory=str(sd), mc_version="1.21.1", server_type="vanilla")
    r1 = bk.create_snapshot(s, ["world"], "auto")
    assert "変更 21件" in r1.note and r1.size >= 20 * 50_000
    time.sleep(0.05)
    (sd / "world" / "level.dat").write_bytes(b"L2")
    r2 = bk.create_snapshot(s, ["world"], "auto")
    assert "変更 1件" in r2.note and "共有 20件" in r2.note and r2.size == 2
    a = Path(r1.path) / "world" / "region" / "r.0.0.mca"
    b = Path(r2.path) / "world" / "region" / "r.0.0.mca"
    assert os.stat(a).st_ino == os.stat(b).st_ino  # ハードリンクで共有
    # 古いスナップショットを消しても新しいものは完全
    bk.delete_backup(r1)
    assert b.exists() and (Path(r2.path) / "world" / "level.dat").read_bytes() == b"L2"
    # 復元 (ハードリンクではなくコピーで戻す)
    (sd / "world" / "level.dat").write_bytes(b"BROKEN")
    (sd / "world" / "region" / "r.0.0.mca").unlink()
    bk.restore_backup(s, r2)
    assert (sd / "world" / "level.dat").read_bytes() == b"L2"
    restored = sd / "world" / "region" / "r.0.0.mca"
    assert restored.exists() and os.stat(restored).st_ino != os.stat(b).st_ino
    assert bk.is_snapshot(r2)
    # 整理 (prune) はフォルダーも削除できる
    for _ in range(3):
        bk.create_snapshot(s, ["world"], "auto")
    assert bk.prune("snap", 1, ("auto",)) >= 3
