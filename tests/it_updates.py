"""MOD更新チェックの統合テスト (古い版を入れて最新版へ更新)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures import check, get_server, modrinth_file, start_wait, stop, summary  # noqa: E402

from msm.mods import disable as dis  # noqa: E402
from msm.mods import importer, updates  # noqa: E402

root = Path(sys.argv[1])
s = get_server(root, "fabric", "1.20.1", "T updates")
for f in s.mods_dir.glob("*.jar"):
    f.unlink()
old_lith = modrinth_file("lithium", "fabric", "1.20.1", root / "dl-old", index=3)
old_api = modrinth_file("fabric-api", "fabric", "1.20.1", root / "dl-old", index=6)
for m in (old_lith, old_api):
    importer.apply_plan(s, importer.plan_for_files([m["path"]], s.mods_dir, "fabric", False), set(), "テスト")
ups, known, total = updates.check(s)
for u in ups:
    print("  更新:", u.title, u.current, "->", u.latest, u.version_type)
check("Modrinthで導入済みMODを確認", known == 2 and total == 2, f"{known}/{total}")
check("古い版に更新があることを検出", {u.slug for u in ups} == {"lithium", "fabric-api"})
res = updates.apply(s, ups)
names = sorted(p.name for p in s.mods_dir.glob("*.jar"))
check("最新版に置き換え", old_lith["path"].name not in names and old_api["path"].name not in names and len(names) == 2, names)
hist = [e for e in dis.history(s) if e.action == "disable" and "更新で置き換え" in e.reason]
check("古いファイルは disabled_mods に退避", len(hist) == 2 and all(Path(e.to_path).exists() for e in hist))
ups2, _, _ = updates.check(s)
check("更新後は更新なし", not ups2, [u.title for u in ups2])
ok, sp = start_wait(s)
stop(sp)
check("更新後に起動成功", ok)
sys.exit(summary())
