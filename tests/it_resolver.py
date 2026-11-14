"""前提MOD自動取得の統合テスト (REI → Architectury API)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures import check, get_server, modrinth_file, start_wait, stop, summary  # noqa: E402

from msm.diagnose.analyzer import diagnose_server  # noqa: E402
from msm.java import manager as jm  # noqa: E402
from msm.mods import deps, importer, resolver  # noqa: E402
from msm.mods.metadata import scan_dir  # noqa: E402

root = Path(sys.argv[1])
s = get_server(root, "fabric", "1.20.1", "T resolver")
for f in s.mods_dir.glob("*.jar"):
    f.unlink()
dl = root / "dl"
for slug in ("fabric-api", "rei", "cloth-config"):
    m = modrinth_file(slug, "fabric", "1.20.1", dl)
    importer.apply_plan(s, importer.plan_for_files([m["path"]], s.mods_dir, "fabric", False), set(), "テスト")
ok, sp = start_wait(s)
stop(sp)
check("前提不足で起動失敗 (再現)", not ok)
j, _r, _n = jm.resolve_for_server(s)
fs = diagnose_server(s, list(sp.lines), sp.exit_code, j.major, sp.started_at)
ids = [a["id"] for f in fs for a in f.actions if a.get("type") == "find_mod"]
check("診断から探すMODを取得", ids == ["Architectury API"], ids)
cands = resolver.resolve(s, ids)
for c in cands:
    print("  候補:", c.requested, "->", c.title, c.version_number, c.file_name, c.confidence, c.reason, c.error)
arch = next((c for c in cands if c.slug == "architectury-api"), None)
check("Architectury APIを確信度「高」で特定", arch is not None and arch.confidence == "高" and arch.ok)
check("Minecraft 1.20.1 / fabric 対応版を選択", arch is not None and "fabric" in arch.file_name.lower(), arch.file_name if arch else "")
files = resolver.download([c for c in cands if c.ok])
plan = importer.plan_for_files(files, s.mods_dir, "fabric", True)
importer.apply_plan(s, plan, set(), "テスト")
ok2, sp2 = start_wait(s)
stop(sp2)
check("前提MODを追加すると起動成功", ok2)
# 依存関係解析からの取得 (宣言済みの不足): fabric-apiを外して lithium? → fabric-apiを必要とするcloth-config
rep = deps.analyze(scan_dir(s.mods_dir), "1.20.1", "fabric")
check("追加後は静的な前提不足なし", not [i for i in rep.issues if i.kind == "missing"], [i.message for i in rep.issues])
c2 = resolver.resolve(s, ["fabric-api"])
check("導入済みのMODは候補にしない", not [c for c in c2 if c.ok], [(c.title, c.error) for c in c2])
c3 = resolver.resolve(s, ["this-mod-does-not-exist-xyz"])
check("見つからないMODはエラーとして表示", c3 and not c3[0].ok and c3[0].error, c3[0].error if c3 else "")
sys.exit(summary())
