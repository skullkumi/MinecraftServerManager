"""友達用クライアントパックの統合テスト."""
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures import check, get_server, modrinth_file, summary  # noqa: E402

from msm.mods import clientpack, importer  # noqa: E402

root = Path(sys.argv[1])
s = get_server(root, "fabric", "1.20.1")
dl = root / "dl"
mods = {slug: modrinth_file(slug, "fabric", "1.20.1", dl) for slug in ("fabric-api", "lithium", "sodium", "modmenu", "cloth-config")}
# サーバーに導入 (sodium/modmenuはクライアント専用として除外される)
plan = importer.plan_for_files([m["path"] for m in mods.values()], s.mods_dir, "fabric", True)
importer.apply_plan(s, plan, set(), "テスト")
# Modrinthに無い自作MOD
fake = s.mods_dir / "my-private-mod-1.0.jar"
if not fake.exists():
    with zipfile.ZipFile(fake, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps({"id": "myprivate", "version": "1.0", "name": "My Private Mod", "environment": "*"}))
        zf.writestr("a/A.class", b"\xca\xfe\xba\xbe")
(s.path / "config").mkdir(exist_ok=True)
(s.path / "config" / "lithium.properties").write_text("x=1\n")

entries = clientpack.collect(s, True)
names = {e.meta.file_name for e in entries}
check("サーバーのMODと除外したクライアント専用MODを収集", any("sodium" in n for n in names) and any("lithium" in n for n in names), sorted(names))

out = root / "out"
shutil.rmtree(out, ignore_errors=True)
r1 = clientpack.build(s, out / "pack.mrpack", "mrpack", entries, True, False, "example.joinmc.link")
print(r1.summary())
with zipfile.ZipFile(r1.path) as zf:
    idx = json.loads(zf.read("modrinth.index.json"))
    zn = zf.namelist()
check(".mrpack: ModrinthのMODはURL参照 (同梱しない)", r1.linked == 5 and not any(n.startswith("overrides/mods/") for n in zn), f"linked={r1.linked}")
check(".mrpack: Modrinthに無いMODは同梱せず一覧に記載", "含まれていないMOD.txt" in zn and r1.not_included == ["my-private-mod-1.0.jar"])
sod = next(f for f in idx["files"] if "sodium" in f["path"])
check(".mrpack: クライアント専用MODは env.server=unsupported", sod["env"]["server"] == "unsupported")
check(".mrpack: ローダー情報", idx["dependencies"] == {"minecraft": "1.20.1", "fabric-loader": s.loader_version}, idx["dependencies"])
check(".mrpack: config・参加方法を含む", "overrides/config/lithium.properties" in zn and "参加方法.txt" in zn)
info = importer.analyze_archive(r1.path)
check("作成した.mrpackを本アプリでも正しく解析できる", info.kind == "mrpack" and info.mod_count == 5, info.summary().replace("\n", " / "))
# ダウンロードURLとハッシュの整合性 (実際に1つ取得して照合)
from msm.core import net  # noqa: E402

f0 = idx["files"][0]
tmpf = out / "verify.jar"
net.download(f0["downloads"][0], tmpf, expected_hash=("sha512", f0["hashes"]["sha512"]))
check(".mrpack: URLとsha512が一致", tmpf.exists())

r2 = clientpack.build(s, out / "pack.mrpack2", "mrpack", entries, False, True)
with zipfile.ZipFile(r2.path) as zf:
    zn2 = zf.namelist()
check(".mrpack (同梱あり): 不明MODをoverrides/modsに同梱", "overrides/mods/my-private-mod-1.0.jar" in zn2 and r2.bundled == 1 and r2.warnings)

r3 = clientpack.build(s, out / "pack-cf.zip", "curseforge", entries, True, True)
with zipfile.ZipFile(r3.path) as zf:
    man = json.loads(zf.read("manifest.json"))
    zn3 = zf.namelist()
check("CurseForge形式: manifestとoverrides/mods", man["minecraft"]["modLoaders"][0]["id"] == f"fabric-{s.loader_version}"
      and sum(1 for n in zn3 if n.startswith("overrides/mods/")) == 6, man["minecraft"])
info3 = importer.analyze_archive(r3.path)
check("CurseForge形式を本アプリでも解析できる", info3.kind == "curseforge" and info3.loader == "fabric")

r4 = clientpack.build(s, out / "pack.zip", "zip", entries, True, True)
with zipfile.ZipFile(r4.path) as zf:
    zn4 = zf.namelist()
check("通常ZIP: mods/ と 参加方法.txt", sum(1 for n in zn4 if n.startswith("mods/")) == 6 and "参加方法.txt" in zn4)
sys.exit(summary())
