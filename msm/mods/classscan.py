"""JAR内のクラスファイル解析によるクライアント専用MODの推定 (Forge / NeoForge 向け).

メタデータにサーバー対応の宣言がないForge系MODの判定材料を増やす。次を調べる:
1. @Mod アノテーションの dist 指定 (NeoForge: @Mod(value=..., dist=Dist.CLIENT))
2. @Mod のメインクラスがクライアント専用クラス (net/minecraft/client/...) を直接参照しているか
3. MOD独自クラスのうち、クライアント専用クラスを参照するものの割合
4. Mixin設定がクライアント側 ("client") だけか
いずれも推定であり、確信度は最大でも「高」 (自動除外はしない)。
"""
from __future__ import annotations

import json
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

MOD_ANNOTATIONS = {"Lnet/minecraftforge/fml/common/Mod;", "Lnet/neoforged/fml/common/Mod;"}
CLIENT_PREFIXES = ("net/minecraft/client/", "com/mojang/blaze3d/", "net/minecraftforge/client/",
                   "net/neoforged/neoforge/client/")
MAX_CLASSES = 6000


class ClassParseError(Exception):
    pass


@dataclass
class ClassInfo:
    name: str
    class_refs: set[str]
    annotations: list[tuple[str, dict]]


def _parse_element(b: bytes, o: int, cp: dict):
    tag = chr(b[o])
    o += 1
    if tag in "BCDFIJSZsc":
        idx = struct.unpack_from(">H", b, o)[0]
        return cp.get(idx), o + 2
    if tag == "e":
        t, n = struct.unpack_from(">HH", b, o)
        return ("enum", cp.get(t), cp.get(n)), o + 4
    if tag == "@":
        ann, o = _parse_annotation(b, o, cp)
        return ann, o
    if tag == "[":
        cnt = struct.unpack_from(">H", b, o)[0]
        o += 2
        vals = []
        for _ in range(cnt):
            v, o = _parse_element(b, o, cp)
            vals.append(v)
        return vals, o
    raise ClassParseError(f"unknown element tag {tag}")


def _parse_annotation(b: bytes, o: int, cp: dict):
    type_idx, npairs = struct.unpack_from(">HH", b, o)
    o += 4
    vals = {}
    for _ in range(npairs):
        name_idx = struct.unpack_from(">H", b, o)[0]
        o += 2
        v, o = _parse_element(b, o, cp)
        vals[cp.get(name_idx)] = v
    return (cp.get(type_idx), vals), o


def parse_class(b: bytes) -> ClassInfo:
    if b[:4] != b"\xca\xfe\xba\xbe":
        raise ClassParseError("not a class file")
    o = 8
    count = struct.unpack_from(">H", b, o)[0]
    o += 2
    utf8: dict[int, str] = {}
    class_idx: list[int] = []
    i = 1
    while i < count:
        tag = b[o]
        o += 1
        if tag == 1:
            ln = struct.unpack_from(">H", b, o)[0]
            o += 2
            utf8[i] = b[o:o + ln].decode("utf-8", "replace")
            o += ln
        elif tag == 7:
            class_idx.append(struct.unpack_from(">H", b, o)[0])
            o += 2
        elif tag in (8, 16, 19, 20):
            o += 2
        elif tag == 15:
            o += 3
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            o += 4
        elif tag in (5, 6):
            o += 8
            i += 1
        else:
            raise ClassParseError(f"unknown constant tag {tag}")
        i += 1
    access, this_idx, super_idx, n_if = struct.unpack_from(">HHHH", b, o)
    o += 8 + 2 * n_if
    for _ in range(2):  # fields, methods
        n = struct.unpack_from(">H", b, o)[0]
        o += 2
        for _ in range(n):
            o += 6
            na = struct.unpack_from(">H", b, o)[0]
            o += 2
            for _ in range(na):
                ln = struct.unpack_from(">I", b, o + 2)[0]
                o += 6 + ln
    n_attr = struct.unpack_from(">H", b, o)[0]
    o += 2
    anns = []
    for _ in range(n_attr):
        name_idx, ln = struct.unpack_from(">HI", b, o)
        o += 6
        if utf8.get(name_idx) in ("RuntimeVisibleAnnotations", "RuntimeInvisibleAnnotations"):
            p = o
            num = struct.unpack_from(">H", b, p)[0]
            p += 2
            for _ in range(num):
                ann, p = _parse_annotation(b, p, utf8)
                anns.append(ann)
        o += ln
    # this_class は Class定数 → 名前
    refs = {utf8.get(ix, "") for ix in class_idx}
    # 記述子 (Lnet/minecraft/client/...;) で参照される型も数える
    for s in utf8.values():
        if "net/minecraft/client/" in s or "com/mojang/blaze3d/" in s:
            refs.add(s)
    name = ""
    # this_idx は Class 定数のインデックス。Class定数→Utf8 の対応はclass_idxの順に一致しないため、再走査しない簡略化
    return ClassInfo(name, refs, anns)


def _is_client_ref(r: str) -> bool:
    return any(p in r for p in CLIENT_PREFIXES)


@dataclass
class ScanResult:
    side_hint: str = ""  # client / server / ""
    confidence: str = "不明"
    reasons: list[str] = field(default_factory=list)
    mod_classes: int = 0
    client_ratio: float = 0.0
    dists: list[list[str]] = field(default_factory=list)
    flags: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def scan(jar: Path) -> ScanResult:
    res = ScanResult()
    try:
        zf = zipfile.ZipFile(jar)
    except zipfile.BadZipFile:
        res.reasons.append("JARを開けません")
        return res
    with zf:
        names = [n for n in zf.namelist() if n.endswith(".class") and not n.startswith("META-INF/")
                 and "module-info" not in n]
        total = 0
        client_classes = 0
        main_refs_client = False
        main_found = 0
        for n in names[:MAX_CLASSES]:
            try:
                ci = parse_class(zf.read(n))
            except Exception:  # noqa: BLE001
                continue
            total += 1
            uses_client = any(_is_client_ref(r) for r in ci.class_refs)
            if uses_client:
                client_classes += 1
            for typ, vals in ci.annotations:
                if typ in MOD_ANNOTATIONS:
                    main_found += 1
                    dist = vals.get("dist")
                    if isinstance(dist, list):
                        res.dists.append([v[2] for v in dist if isinstance(v, tuple)])
                    elif dist is None:
                        res.dists.append([])
                    if uses_client:
                        main_refs_client = True
        mixin_client_only = None
        for n in zf.namelist():
            if n.endswith(".json") and "mixins" in n.lower() and "/" not in n.strip("/"):
                try:
                    d = json.loads(zf.read(n).decode("utf-8", "replace"))
                except Exception:  # noqa: BLE001
                    continue
                has_common = bool(d.get("mixins")) or bool(d.get("server"))
                has_client = bool(d.get("client"))
                if has_common:
                    mixin_client_only = False
                elif has_client and mixin_client_only is None:
                    mixin_client_only = True
    res.mod_classes = total
    res.client_ratio = client_classes / total if total else 0.0
    if res.dists and all(d == ["CLIENT"] for d in res.dists):
        res.side_hint, res.confidence = "client", "高"
        res.reasons.append("@Mod(dist = Dist.CLIENT) だけが宣言されています (サーバーでは何もしないMOD)")
        return res
    if res.dists and all(d == ["DEDICATED_SERVER"] for d in res.dists):
        res.side_hint, res.confidence = "server", "高"
        res.reasons.append("@Mod(dist = Dist.DEDICATED_SERVER) だけが宣言されています")
        return res
    res.flags = {"main_refs_client": main_refs_client, "mixin_client_only": bool(mixin_client_only)}
    if main_refs_client:
        res.reasons.append("MODのメインクラスがクライアント専用クラスを直接参照しています")
    if mixin_client_only:
        res.reasons.append("Mixin設定がクライアント側 (client) だけです")
    if total >= 3:
        res.reasons.append(f"MOD内のクラスの{res.client_ratio:.0%}がクライアント専用クラスを参照しています")
    return res


def combine(res: "ScanResult", display_test: str) -> tuple[str, str, list[str]]:
    """クラス解析とdisplayTestを組み合わせた判定 (side, 確信度, 理由).

    実在のMOD 53件 (Modrinthの情報を正解) で検証した組み合わせ:
    - displayTest=IGNORE_ALL_VERSION はクライアント専用MODでのみ使われていた
    - 「Mixinがclientのみ」「メインクラスがクライアント参照」は単独では両対応MODにも見られるため組み合わせで使う
    """
    if res.side_hint in ("client", "server") and res.confidence == "高":
        return res.side_hint, "高", res.reasons
    a = display_test.upper() == "IGNORE_ALL_VERSION"
    f = getattr(res, "flags", {}) or {}
    b = f.get("mixin_client_only", False)
    c = f.get("main_refs_client", False)
    d = res.mod_classes >= 3 and res.client_ratio >= 0.6
    e = res.mod_classes >= 3 and res.client_ratio >= 0.45
    reasons = list(res.reasons)
    if a:
        reasons.insert(0, "displayTest=IGNORE_ALL_VERSION (クライアント専用MODで使われる設定)")
    if a and (b or c or e):
        return "client", "高", reasons
    if a or (b and (c or d or e)):
        return "client", "中", reasons
    return "", "不明", reasons
