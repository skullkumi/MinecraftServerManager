"""単体テスト (ネットワーク不要)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MSM_DATA_DIR"] = tempfile.mkdtemp(prefix="msm_unit_")

from msm.core import db as dbmod  # noqa: E402
from msm.diagnose.analyzer import Analyzer  # noqa: E402
from msm.mods.metadata import read_mod  # noqa: E402
from msm.mods.versionrange import matches  # noqa: E402
from msm.server.properties import read_properties, write_properties, danger_warnings  # noqa: E402
from msm.server.providers import neoforge_mc_version  # noqa: E402
from msm.java.detect import parse_major  # noqa: E402
from msm.java.requirements import JavaRequirement, choose_java, fallback_major  # noqa: E402
from msm.java.detect import JavaInfo  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    dbmod.set_db(dbmod.Database(tmp_path / "t.db"))
    yield


def test_neoforge_mapping():
    assert neoforge_mc_version("21.1.77") == "1.21.1"
    assert neoforge_mc_version("21.0.10-beta") == "1.21"
    assert neoforge_mc_version("20.4.237") == "1.20.4"
    assert neoforge_mc_version("26.1.2.126") == "26.1.2"
    assert neoforge_mc_version("26.3.0.103") == "26.3"
    assert neoforge_mc_version("0.25w14craftmine.3-beta") is None


def test_java_major_parse_and_choose():
    assert parse_major("1.8.0_392") == 8
    assert parse_major("21.0.5") == 21
    assert parse_major("25") == 25
    assert fallback_major("1.16.5") == 8 and fallback_major("1.20.1") == 17 and fallback_major("1.21.1") == 21
    assert fallback_major("26.3") == 25
    js = [JavaInfo("a", "25", 25, ok=True, arch="64bit"), JavaInfo("b", "17", 17, ok=True, arch="64bit"),
          JavaInfo("c", "21", 21, ok=True, arch="64bit")]
    assert choose_java(js, JavaRequirement(17, 17, None, "", "")).path == "b"
    assert choose_java(js, JavaRequirement(21, 21, None, "", "")).path == "c"
    assert choose_java(js, JavaRequirement(8, 8, 8, "", "")) is None


def test_version_range():
    assert matches("[1.20.1,1.21)", "1.20.1") is True
    assert matches("[1.20.1,1.21)", "1.21") is False
    assert matches(">=1.20 <1.21", "1.20.4") is True
    assert matches("~1.20.1", "1.21") is False
    assert matches("1.20.x", "1.20.6") is True
    assert matches("*", "26.1") is True


def test_properties_preserve(tmp_path):
    p = tmp_path / "server.properties"
    p.write_text("#comment\nmotd=Hello\ncustom-key=keep\nserver-port=25565\n", "utf-8")
    write_properties(p, {"server-port": "25570", "pvp": "false"})
    text = p.read_text("utf-8")
    assert "#comment" in text and "custom-key=keep" in text
    props = read_properties(p)
    assert props["server-port"] == "25570" and props["pvp"] == "false" and props["motd"] == "Hello"
    assert danger_warnings({"online-mode": "false"})
    assert not danger_warnings({"online-mode": "true"})


def _jar(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for k, v in files.items():
            zf.writestr(k, v)
    return path


def test_metadata_forge_and_fabric(tmp_path):
    forge = _jar(tmp_path / "a.jar", {"META-INF/mods.toml": '''
modLoader="javafml"
loaderVersion="[47,)"
[[mods]]
modId="examplemod"
version="1.2.3"
displayName="Example Mod"
[[dependencies.examplemod]]
modId="minecraft"
mandatory=true
versionRange="[1.21,1.22)"
side="BOTH"
[[dependencies.examplemod]]
modId="geckolib"
mandatory=true
versionRange="[4.0,)"
side="BOTH"
[[dependencies.examplemod]]
modId="jei"
mandatory=false
versionRange="[1,)"
side="CLIENT"
'''})
    m = read_mod(forge)
    assert m.loader == "forge" and m.mod_ids == ["examplemod"] and m.mc_range == "[1.21,1.22)"
    assert [d.mod_id for d in m.required_deps()] == ["geckolib"]
    fab = _jar(tmp_path / "b.jar", {"fabric.mod.json": json.dumps({"id": "clientthing", "version": "1", "environment": "client",
                                                                   "depends": {"minecraft": "1.20.1", "fabric-api": "*"}})})
    m2 = read_mod(fab)
    assert m2.environment == "client" and m2.mc_range == "1.20.1"
    from msm.mods.classify import classify

    c = classify(m2, None, set())
    assert c.side == "client" and c.auto_exclude
    c1 = classify(m, None, set())
    assert c1.side == "unknown" and not c1.auto_exclude  # Forge系は判定不能 → 勝手に除外しない
    bad = tmp_path / "c.jar"
    bad.write_bytes(b"not a zip")
    assert not read_mod(bad).valid_jar


def test_deps_analysis(tmp_path):
    from msm.mods import deps

    a = _jar(tmp_path / "a.jar", {"META-INF/mods.toml": '[[mods]]\nmodId="a"\nversion="1"\n[[dependencies.a]]\nmodId="b"\nmandatory=true\nversionRange="[2,)"\nside="BOTH"\n[[dependencies.a]]\nmodId="minecraft"\nmandatory=true\nversionRange="[1.21,1.22)"\nside="BOTH"\n'})
    b = _jar(tmp_path / "b.jar", {"META-INF/mods.toml": '[[mods]]\nmodId="b"\nversion="1.5"\n'})
    c = _jar(tmp_path / "c.jar", {"META-INF/mods.toml": '[[mods]]\nmodId="c"\nversion="1"\n[[dependencies.c]]\nmodId="zzz"\nmandatory=true\nversionRange="[1,)"\nside="BOTH"\n'})
    d = _jar(tmp_path / "d.jar", {"META-INF/mods.toml": '[[mods]]\nmodId="b"\nversion="1.6"\n'})
    metas = [read_mod(x) for x in (a, b, c, d)]
    rep = deps.analyze(metas, "1.20.1", "forge")
    kinds = {i.kind for i in rep.issues}
    assert {"missing", "version", "mc", "duplicate"} <= kinds
    edges = deps.dependency_edges(metas)
    assert deps.closure({str(a)}, edges) >= {str(a)}


LOG_SAMPLES = {
    "java": ("java.lang.UnsupportedClassVersionError: x has been compiled by a more recent version of the Java Runtime "
             "(class file version 65.0), this version of the Java Runtime only recognizes class file versions up to 61.0", "java", "確定"),
    "forge_missing": ("Missing or unsupported mandatory dependencies:\n\tMod ID: 'architectury', Requested by: 'rei', "
                      "Expected range: '[9.1.12,)', Actual version: '[MISSING]'", "missing", "確定"),
    "forge_mc": ("\tMod ID: 'minecraft', Requested by: 'coolmod', Expected range: '[1.21,1.22)', Actual version: '1.20.1'", "mc", "確定"),
    "fabric_missing": ("net.fabricmc.loader.impl.FormattedException: Mod resolution encountered an incompatible mod set!\n"
                       " - Mod 'Roughly Enough Items (REI)' (roughlyenoughitems) 12.1.785 requires version 9.2.14 or later of mod "
                       "'Architectury' (architectury), which is missing!", "missing", "確定"),
    "client": ("java.lang.RuntimeException: Attempted to load class net/minecraft/client/gui/screens/Screen for invalid dist DEDICATED_SERVER\n"
               "\tat TRANSFORMER/oculus@1.6.9/net.coderbot.Foo.bar(Foo.java:10)", "client", None),
    "notifier": ("Found 1 error(s) during startup:\n- Architectury API is a required dependency of Roughly Enough Items 12.1.785 but it is not installed!\n"
                 "https://www.curseforge.com/minecraft/mc-mods/architectury-api/", "missing", "確定"),
    "oom": ("java.lang.OutOfMemoryError: Java heap space", "memory", "確定"),
    "port": ("**** FAILED TO BIND TO PORT!\njava.net.BindException: Address already in use", "port", "確定"),
    "eula": ("You need to agree to the EULA in order to run the server. Go to eula.txt for more info.", "eula", "確定"),
    "dup": ("net.minecraftforge.fml.loading.DuplicateModsFoundException: Found duplicate mods:\n"
            "\tMod ID: 'jei' from mod files: jei-1.jar, jei-2.jar", "duplicate", "確定"),
    "mixin": ("org.spongepowered.asm.mixin.transformer.throwables.MixinTransformerError: An unexpected critical error was encountered\n"
              "Mixin [foo.MixinBar] from phase [DEFAULT] in config [create.mixins.json] FAILED during APPLY", "mixin", "可能性:高"),
    "config": ("com.electronwill.nightconfig.core.io.ParsingException: Not enough data available\n"
               "Failed loading config file serverconfig/foo-server.toml of type SERVER for modid foo", "config", "可能性:高"),
    "heap": ("Error occurred during initialization of VM\nCould not reserve enough space for 8388608KB object heap", "memory", "確定"),
}


@pytest.mark.parametrize("key", list(LOG_SAMPLES))
def test_analyzer_rules(key):
    text, cat, level = LOG_SAMPLES[key]
    fs = Analyzer("1.20.1", "forge", 17).run({"console": text}, 1)
    cats = {f.category for f in fs}
    assert cat in cats, f"{key}: {[(f.category, f.title) for f in fs]}"
    f = next(f for f in fs if f.category == cat)
    if level:
        assert f.level == level
    if key == "java":
        assert f.actions[0]["major"] == 21
    if key == "client":
        assert "oculus" in f.mod_ids


def test_analyzer_unknown():
    fs = Analyzer("1.20.1", "forge", 17).run({"console": "something weird happened\nException in thread main"}, 1)
    assert fs and fs[0].level == "不明"


def test_backup_restore_prune(tmp_path):
    from msm.backup import backup as bk
    from msm.server.instance import Server

    dbmod.get_db().set_setting("backup_dir", str(tmp_path / "bk"))
    sd = tmp_path / "srv"
    (sd / "world" / "region").mkdir(parents=True)
    (sd / "world" / "level.dat").write_bytes(b"L1")
    (sd / "world" / "region" / "r.0.0.mca").write_bytes(b"R1")
    (sd / "config").mkdir()
    (sd / "config" / "a.toml").write_text("a=1")
    (sd / "server.properties").write_text("level-name=world\n")
    s = Server(id="s1", name="t", directory=str(sd), mc_version="1.20.1", server_type="forge")
    rec = bk.create_backup(s, ["world", "config"])
    assert Path(rec.path).exists()
    (sd / "world" / "level.dat").write_bytes(b"L2")
    (sd / "world" / "new.dat").write_bytes(b"N")
    (sd / "config" / "a.toml").write_text("a=2")
    pre = bk.restore_backup(s, rec)
    assert (sd / "world" / "level.dat").read_bytes() == b"L1"
    assert not (sd / "world" / "new.dat").exists()  # 既存ワールドは退避される
    assert (sd / "config" / "a.toml").read_text() == "a=1"
    assert pre is not None and Path(pre.path).exists()
    assert list((sd / ".msm" / "replaced").rglob("new.dat"))  # 削除せず保管
    for _ in range(4):
        bk.create_backup(s, ["config"], kind="auto")
    assert bk.prune("s1", 2, ("auto",)) == 2
    assert len([r for r in bk.list_backups("s1") if r.kind == "auto"]) == 2
    assert Path(rec.path).exists()  # 手動バックアップは整理対象外


def test_disable_restore(tmp_path):
    from msm.mods import disable as dis
    from msm.server.instance import Server

    sd = tmp_path / "srv"
    (sd / "mods").mkdir(parents=True)
    f = _jar(sd / "mods" / "x.jar", {"fabric.mod.json": '{"id":"x"}'})
    s = Server(id="s2", name="t", directory=str(sd), mc_version="1.20.1", server_type="fabric")
    ev = dis.disable(s, [f], "テスト")[0]
    assert not f.exists() and Path(ev.to_path).exists() and ev.sha1
    assert dis.active_disabled(s)
    dis.restore(s, dis.history(s)[0])
    assert f.exists()
    assert not dis.active_disabled(s)


def test_secret_roundtrip():
    from msm.core.secrets import protect, unprotect

    enc = protect("my-key-123")
    assert enc and "my-key" not in enc
    assert unprotect(enc) == "my-key-123"


def test_zip_slip_rejected(tmp_path):
    from msm.mods import importer

    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("mods/ok.jar", "x")
        zf.writestr("../../evil.txt", "x")
    info = importer.analyze_archive(str(z))
    with pytest.raises(Exception):
        importer.stage_unknown(str(z), tmp_path / "st", "all")
    assert not (tmp_path.parent / "evil.txt").exists()
    assert info.kind in ("serverpack", "jars", "unknown")


def test_build_command():
    from msm.server.instance import Server
    from msm.server.process import build_command

    s = Server(id="x", name="x", directory=".", mc_version="1.20.1", server_type="forge", max_mem_mb=6144, min_mem_mb=1024,
               launch={"kind": "args", "args": ["@user_jvm_args.txt", "@libraries/net/minecraftforge/forge/1.20.1-47.4.28/win_args.txt"]})
    cmd = build_command(s, "java.exe")
    assert cmd[1] == "@user_jvm_args.txt"
    assert cmd.index("-Xmx6144M") < cmd.index("@libraries/net/minecraftforge/forge/1.20.1-47.4.28/win_args.txt")
    assert cmd[-1] == "nogui"
