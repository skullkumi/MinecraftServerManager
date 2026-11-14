"""追加機能の単体テスト (更新・playitログ・クラス解析・前提MOD名の推定など)."""
import hashlib
import json
import os
import sys
import tempfile
import threading
import zipfile
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MSM_DATA_DIR", tempfile.mkdtemp(prefix="msm_unit_"))


@pytest.fixture
def http_dir(tmp_path):
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    srv = HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield tmp_path, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_update_check_and_verify(http_dir, monkeypatch):
    from msm import update
    from msm.core.db import get_db

    d, base = http_dir
    z = d / "app.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("MinecraftServerManager.exe", b"MZ fake")
    sha = hashlib.sha256(z.read_bytes()).hexdigest()
    (d / "latest.json").write_text(json.dumps({"version": "99.0.0", "url": base, "notes": "test",
                                               "downloads": {"onefile": {"url": f"{base}/app.zip", "sha256": sha}}}))
    monkeypatch.setenv("MSM_ALLOW_HTTP_UPDATE", "1")
    monkeypatch.setattr(update, "install_mode", lambda: "onefile")
    get_db().set_setting("update_github", "")
    get_db().set_setting("update_url", f"{base}/latest.json")
    r = update.check()
    assert r["status"] == "available" and r["auto"] and r["sha256"] == sha
    p = update.download(r)
    assert p.read_bytes() == z.read_bytes()
    cmd = update.prepare(p, "onefile", app_exe=d / "installed" / "MinecraftServerManager.exe", pid=999999)
    assert cmd[0] == "powershell.exe" and Path(cmd[-1]).exists()
    # ハッシュ不一致は拒否
    r2 = dict(r, sha256="0" * 64)
    with pytest.raises(Exception):
        update.download(r2)
    # SHA-256が無ければ自動更新しない
    (d / "latest.json").write_text(json.dumps({"version": "99.0.0", "url": base, "downloads": {"onefile": {"url": f"{base}/app.zip"}}}))
    assert update.check()["auto"] is False
    # 同じ版なら最新
    (d / "latest.json").write_text(json.dumps({"version": "0.0.1"}))
    assert update.check()["status"] == "latest"
    monkeypatch.delenv("MSM_ALLOW_HTTP_UPDATE")
    assert update.check()["status"] == "error"  # httpは不可
    get_db().set_setting("update_url", "")


def test_playit_log_parse(tmp_path, monkeypatch):
    from msm.publish import tunnels

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "playitd.log").write_text(
        "2026-11-12T13:44:47Z INFO New TCP Client details=NewClient { connect_addr: 203.0.113.10:4000, peer_addr: 1.2.3.4:5, "
        "tunnel_id: 1, claim_instructions: ClaimInstructions { token: SECRET } }\n"
        "2026-11-12T15:41:29Z ERROR timed out connecting to local TCP server; check firewall origin_addr=127.0.0.1:25570\n")
    monkeypatch.setattr(tunnels, "playit_log_dirs", lambda: [str(logs)])
    info = tunnels.playit_log_info()
    assert info.public_addresses == ["203.0.113.10:4000"]
    assert info.origin_errors == ["127.0.0.1:25570"]
    assert "1.2.3.4" not in json.dumps(info.__dict__) and "SECRET" not in json.dumps(info.__dict__)


def _class_bytes(name: str, refs: list[str], annotation: tuple | None = None) -> bytes:
    """最小限のクラスファイルを作る (定数プール・アノテーションのみ)."""
    import struct

    cp = []

    def utf8(s):
        cp.append(b"\x01" + struct.pack(">H", len(s.encode())) + s.encode())
        return len(cp)

    def cls(s):
        i = utf8(s)
        cp.append(b"\x07" + struct.pack(">H", i))
        return len(cp)

    this = cls(name)
    sup = cls("java/lang/Object")
    for r in refs:
        cls(r)
    attrs = b""
    if annotation:
        typ, dist = annotation
        ra = utf8("RuntimeVisibleAnnotations")
        t = utf8(typ)
        body = struct.pack(">HHH", 1, t, 1 if dist else 0)
        if dist:
            dn = utf8("dist")
            et = utf8("Lnet/neoforged/api/distmarker/Dist;")
            ev = utf8(dist)
            body += struct.pack(">H", dn) + b"[" + struct.pack(">H", 1) + b"e" + struct.pack(">HH", et, ev)
        attrs = struct.pack(">HI", ra, len(body)) + body
    out = b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, 61) + struct.pack(">H", len(cp) + 1) + b"".join(cp)
    out += struct.pack(">HHHH", 0x21, this, sup, 0) + struct.pack(">HH", 0, 0)
    out += struct.pack(">H", 1 if attrs else 0) + attrs
    return out


def test_classscan_neoforge_dist(tmp_path):
    from msm.mods.classscan import combine, parse_class, scan

    b = _class_bytes("a/Main", ["net/minecraft/client/Minecraft"], ("Lnet/neoforged/fml/common/Mod;", "CLIENT"))
    ci = parse_class(b)
    assert "net/minecraft/client/Minecraft" in ci.class_refs
    assert ci.annotations and ci.annotations[0][0] == "Lnet/neoforged/fml/common/Mod;"
    jar = tmp_path / "m.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("a/Main.class", b)
        zf.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="m"\nversion="1"\n')
    r = scan(jar)
    assert r.side_hint == "client" and r.confidence == "高"
    assert combine(r, "")[0] == "client"
    # 両対応MOD風 (distなし・クライアント参照なし)
    jar2 = tmp_path / "n.jar"
    with zipfile.ZipFile(jar2, "w") as zf:
        zf.writestr("b/Main.class", _class_bytes("b/Main", ["net/minecraft/world/level/Level"], ("Lnet/neoforged/fml/common/Mod;", None)))
    assert combine(scan(jar2), "")[0] == ""
    assert combine(scan(jar2), "IGNORE_ALL_VERSION")[1] == "中"


def test_resolver_slug_guesses():
    from msm.mods.resolver import _slug_guesses, loaders_for

    g = _slug_guesses("Architectury API")
    assert "architectury-api" in g and "architectury" in g
    assert loaders_for("neoforge", "1.20.1") == ["neoforge", "forge"]
    assert loaders_for("neoforge", "1.21.1") == ["neoforge"]
