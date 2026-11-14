"""MOD機能の統合テスト (実MODをModrinthから取得して検証).

python tests/integration_mods.py <作業フォルダー> <Fabric 1.20.1 サーバーフォルダー>
1. 実MODの取得 (fabric-api / lithium / sodium / modmenu / REI / cloth-config)
2. メタデータ解析・サーバー対応判定・依存関係解析 (REIの前提 architectury が不足)
3. .mrpack / CurseForge形式ZIP / サーバーパックの解析と取り込み計画
4. 実サーバー起動 → 失敗 → ログ診断で前提MOD不足を検出
5. 依存関係を維持した自動切り分けで REI を特定
"""
from __future__ import annotations

import json
import sys
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from msm.core import net  # noqa: E402
from msm.diagnose.analyzer import diagnose_server  # noqa: E402
from msm.diagnose.bisect import Bisector, bisect_report  # noqa: E402
from msm.java import manager as jm  # noqa: E402
from msm.mods import deps, importer  # noqa: E402
from msm.mods.download_watch import DownloadWatcher  # noqa: E402
from msm.mods.metadata import scan_dir  # noqa: E402
from msm.server import instance, ops  # noqa: E402
from msm.server.process import DONE_RE  # noqa: E402

MODS = ["fabric-api", "lithium", "sodium", "modmenu", "rei", "cloth-config"]
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(("OK  " if cond else "NG  ") + name + (f"  ({detail})" if detail else ""), flush=True)


def fetch(work: Path) -> dict[str, dict]:
    out = {}
    d = work / "dl"
    d.mkdir(parents=True, exist_ok=True)
    for slug in MODS:
        vs = net.get_json(f"https://api.modrinth.com/v2/project/{slug}/version?loaders=%5B%22fabric%22%5D&game_versions=%5B%221.20.1%22%5D")
        f = next(x for x in vs[0]["files"] if x.get("primary")) if any(x.get("primary") for x in vs[0]["files"]) else vs[0]["files"][0]
        p = d / f["filename"]
        if not p.exists():
            net.download(f["url"], p, expected_hash=("sha1", f["hashes"]["sha1"]))
        out[slug] = {"path": p, "file": f}
        print("取得:", p.name, flush=True)
    return out


def start_wait(s, timeout=300):
    sp, _ = ops.start(s)
    ev = threading.Event()
    sp.on_line.append(lambda l: ev.set() if DONE_RE.search(l) else None)
    sp.on_state.append(lambda st: ev.set() if st in ("stopped", "crashed") else None)
    ev.wait(timeout)
    ok = sp.state == "running"
    if ok:
        sp.stop(120)
    else:
        if sp.running:
            sp.kill()
        while sp.running:
            time.sleep(0.3)
    return ok, sp


def main():
    work = Path(sys.argv[1])
    srv_dir = Path(sys.argv[2])
    server = next(s for s in instance.list_servers() if Path(s.directory) == srv_dir)
    mods = fetch(work)

    # ---- 2. 解析 ----
    paths = [m["path"] for m in mods.values()]
    plan = importer.plan_for_files(paths, server.mods_dir, "fabric", True)
    by = {Path(p.source).name: p for p in plan}
    sod = by[mods["sodium"]["path"].name]
    check("sodiumはクライアント専用(確定)で自動除外", sod.action == "exclude_client", f"{sod.action} {sod.classification.reasons if sod.classification else ''}")
    mm = by[mods["modmenu"]["path"].name]
    check("modmenuはクライアント専用と判定", mm.action in ("exclude_client", "review_client"), mm.action)
    lit = by[mods["lithium"]["path"].name]
    check("lithiumは追加対象", lit.action == "add", f"{lit.action} {lit.classification.label if lit.classification else ''}")
    fapi = by[mods["fabric-api"]["path"].name]
    check("fabric-apiは追加対象", fapi.action == "add", fapi.action)

    # REI (architecturyなし) をサーバーに入れる
    for p in plan:
        if p.action == "add":
            pass
    done = importer.apply_plan(server, plan, set(), "テスト")
    check("取り込み実行 (sodium/modmenuはmodsに入らない)",
          not (server.mods_dir / mods["sodium"]["path"].name).exists() and (server.mods_dir / mods["lithium"]["path"].name).exists(),
          f"{len(done)}件")
    check("除外MODはサーバー内に保管", (server.path / ".msm" / "excluded_client_mods" / mods["sodium"]["path"].name).exists())
    # 同じファイルの再取り込みはスキップ
    plan2 = importer.plan_for_files([mods["lithium"]["path"]], server.mods_dir, "fabric", False)
    check("再取り込みは導入済みとしてスキップ", plan2[0].action == "skip_same", plan2[0].action)

    metas = scan_dir(server.mods_dir)
    rep = deps.analyze(metas, "1.20.1", "fabric")
    miss = [i for i in rep.issues if i.kind == "missing"]
    # REIは前提MODをメタデータで宣言せず起動時に独自チェックするため、静的解析では検出できない (ログ診断で検出する)
    print("INFO 静的解析の不足検出:", "; ".join(i.message for i in miss)[:200] or "なし (REIは依存を宣言していない)")
    check("fabric-api同梱モジュールの依存を誤検出しない", not any("fabric-" in i.message and "fabric-api" not in i.message for i in miss))
    print("INFO 依存グループ:", [len(g) for g in rep.groups])

    # ---- 3. パック解析 ----
    # mrpack
    mr = work / "test.mrpack"
    idx = {"formatVersion": 1, "game": "minecraft", "versionId": "1.0", "name": "TestPack",
           "dependencies": {"minecraft": "1.20.1", "fabric-loader": "0.16.10"}, "files": []}
    for slug in ("lithium", "sodium"):
        f = mods[slug]["file"]
        idx["files"].append({"path": f"mods/{f['filename']}", "hashes": f["hashes"], "downloads": [f["url"]],
                             "env": {"client": "required", "server": "unsupported" if slug == "sodium" else "required"},
                             "fileSize": f["size"]})
    with zipfile.ZipFile(mr, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps(idx))
        zf.writestr("overrides/config/test.toml", "a=1\n")
        zf.writestr("overrides/shaderpacks/x.zip", "x")
    info = importer.analyze_archive(str(mr))
    check(".mrpack解析", info.kind == "mrpack" and info.mc_version == "1.20.1" and info.loader == "fabric", info.summary().replace("\n", " / "))
    res = importer.stage_archive(info, work / "stage_mr", "fabric")
    staged = [p.name for p in (work / "stage_mr" / "mods").glob("*.jar")]
    check(".mrpackのMOD取得 (サーバー非対応指定は取得しない)", staged == [mods["lithium"]["file"]["filename"]], str(staged))
    check(".mrpackのoverrides展開・クライアント用フォルダー除外",
          (work / "stage_mr" / "files" / "config" / "test.toml").exists() and not (work / "stage_mr" / "files" / "shaderpacks").exists())

    # CurseForge形式 (APIキーなし)
    cfz = work / "cfpack.zip"
    with zipfile.ZipFile(cfz, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"minecraft": {"version": "1.20.1", "modLoaders": [{"id": "forge-47.2.0", "primary": True}]},
                                                 "manifestType": "minecraftModpack", "name": "CF Test", "version": "1",
                                                 "files": [{"projectID": 238222, "fileID": 4712866, "required": True}],
                                                 "overrides": "overrides"}))
        zf.writestr("modlist.html", '<ul><li><a href="https://www.curseforge.com/minecraft/mc-mods/jei">Just Enough Items (by mezz)</a></li></ul>')
        zf.writestr("overrides/config/jei.toml", "x=1\n")
        zf.writestr("overrides/mods/extra.jar", open(mods["lithium"]["path"], "rb").read())
    info = importer.analyze_archive(str(cfz))
    check("CurseForge ZIP解析", info.kind == "curseforge" and info.loader == "forge" and info.loader_version == "47.2.0", info.summary().replace("\n", " / "))
    from msm.mods import curseforge_api

    had_key = curseforge_api.has_api_key()
    if not had_key:
        res = importer.stage_archive(info, work / "stage_cf", "forge")
        check("APIキーなしでも処理を止めず手動ダウンロード一覧を作る", len(res.manual) == 1 and "jei" in res.manual[0].page, res.manual[0].page if res.manual else "")
        check("overrides内のMODとconfigを展開", (work / "stage_cf" / "mods" / "extra.jar").exists() and (work / "stage_cf" / "files" / "config" / "jei.toml").exists())
        rep_path = importer.write_failed_report(work, res.manual, [])
        check("IMPORT_FAILED.txt 作成", rep_path is not None and rep_path.exists())

    # サーバーパック
    sp_zip = work / "serverpack.zip"
    with zipfile.ZipFile(sp_zip, "w") as zf:
        zf.writestr("MyPack/mods/a.jar", open(mods["lithium"]["path"], "rb").read())
        zf.writestr("MyPack/config/a.toml", "a")
        zf.writestr("MyPack/run.bat", "java -jar x")
        zf.writestr("MyPack/forge-installer.jar", "x")
    info = importer.analyze_archive(str(sp_zip))
    check("サーバーパック解析", info.kind == "serverpack" and info.mod_count == 1)
    res = importer.stage_archive(info, work / "stage_sp", "fabric")
    check("サーバーパック: run.bat等は取り込まない", not (work / "stage_sp" / "files" / "run.bat").exists()
          and (work / "stage_sp" / "files" / "config" / "a.toml").exists() and (work / "stage_sp" / "mods" / "a.jar").exists())
    # 判定不能ZIP
    uz = work / "unknown.zip"
    with zipfile.ZipFile(uz, "w") as zf:
        zf.writestr("readme.txt", "hello")
        zf.writestr("data/x.bin", "x")
    check("判定不能ZIPは unknown", importer.analyze_archive(str(uz)).kind == "unknown")

    # ---- ダウンロード監視 ----
    dl = work / "watch"
    dl.mkdir(exist_ok=True)
    for f in dl.glob("*"):
        f.unlink()
    expected = [importer.ManualItem("Lithium", "テスト", "", mods["lithium"]["file"]["filename"], mods["lithium"]["file"]["hashes"]["sha1"])]
    got = []
    w = DownloadWatcher(dl, lambda: expected, got.append, interval=0.5)
    w.start()
    part = dl / (mods["lithium"]["file"]["filename"] + ".crdownload")
    part.write_bytes(open(mods["lithium"]["path"], "rb").read()[:1000])
    time.sleep(2)
    check("ダウンロード途中 (.crdownload) は無視", not got)
    part.unlink()
    (dl / mods["lithium"]["file"]["filename"]).write_bytes(open(mods["lithium"]["path"], "rb").read())
    t0 = time.time()
    while not got and time.time() - t0 < 10:
        time.sleep(0.3)
    w.stop()
    check("ダウンロード完了を検出しSHA-1で照合(確定)", bool(got) and got[0].confidence == "確定", got[0].reason if got else "未検出")
    # 名前だけ同じ別ファイル
    expected2 = [importer.ManualItem("Lithium", "テスト", "", mods["lithium"]["file"]["filename"], "0" * 40)]
    from msm.mods.download_watch import match_file
    from msm.mods.metadata import read_mod

    m, conf, reason = match_file(read_mod(dl / mods["lithium"]["file"]["filename"]), expected2)
    check("ファイル名だけ一致・ハッシュ不一致は確定しない", m is None and conf == "不一致", reason)

    # ---- 4. 実起動 → 診断 ----
    ok, sp = start_wait(server)
    check("REIの前提不足で起動失敗する (再現)", not ok)
    j, _r, _n = jm.resolve_for_server(server)
    fs = diagnose_server(server, list(sp.lines), sp.exit_code, j.major, sp.started_at)
    top = fs[0] if fs else None
    check("ログ診断で前提MOD不足を「確定」で検出", bool(top) and top.level == "確定" and top.category == "missing",
          f"{top.level} {top.title}" if top else "なし")

    # ---- 5. 自動切り分け ----
    logs = []
    b = Bisector(server, j.path, j.major, timeout=240, max_runs=12, log_fn=logs.append)
    res = b.run()
    print(bisect_report(server, res))
    culprit_names = [Path(c).name for c in res.culprits]
    check("自動切り分けでREIを特定", any("RoughlyEnoughItems" in n or "rei" in n.lower() for n in culprit_names), str(culprit_names))
    check("元のmodsフォルダーは変更されていない", len(list(server.mods_dir.glob("*.jar"))) == len(metas))
    check("テスト環境は削除済み", not Path(res.env_dir).exists())

    # ---- 無効化と復元 ----
    from msm.mods import disable as dis

    rei = next(m for m in metas if "rei" in m.mod_ids or m.primary_id == "roughlyenoughitems")
    ev = dis.disable(server, [Path(rei.file)], "テスト")
    check("無効化 (disabled_modsへ移動)", not Path(rei.file).exists() and Path(ev[0].to_path).exists())
    ok2, _ = start_wait(server)
    check("問題MOD無効化後に起動成功", ok2)
    dis.restore(server, ev[0])
    check("復元", Path(rei.file).exists())

    print("\n===== まとめ =====")
    ng = [r for r in results if not r[1]]
    print(f"{len(results) - len(ng)}/{len(results)} OK")
    for r in ng:
        print("NG:", r[0], r[2])


if __name__ == "__main__":
    main()
