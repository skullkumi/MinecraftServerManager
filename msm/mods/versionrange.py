"""MODが宣言するバージョン範囲の判定.

- Maven形式 (Forge/NeoForge): [1.20.1,1.21) / [1.20,) / 1.20.1
- SemVer風 (Fabric): >=1.20 <1.21 / ~1.20.1 / ^1.20 / 1.20.x / 1.20.1 / * / 複数指定 (OR)
判定できない書式は None (不明) を返す。
"""
from __future__ import annotations

import re

from msm.core.util import mc_version_tuple


def _t(v: str) -> tuple[int, ...]:
    return mc_version_tuple(v.strip().lstrip("v"))


def _pad(a: tuple, n: int = 4) -> tuple:
    return tuple(list(a) + [0] * (n - len(a)))


def _cmp(a: tuple, b: tuple) -> int:
    a, b = _pad(a), _pad(b)
    return (a > b) - (a < b)


def _maven_match(rng: str, ver: tuple) -> bool | None:
    rng = rng.strip()
    if not rng:
        return None
    # 複数区間 "[1.18,1.19),[1.20,)"
    parts = re.findall(r"[\[(][^\])]*[\])]", rng)
    if not parts:
        t = _t(rng)
        if not t:
            return None
        return _cmp(ver, t) >= 0  # Mavenの裸のバージョンは「推奨(以上)」の意味
    for p in parts:
        lo_inc, hi_inc = p[0] == "[", p[-1] == "]"
        inner = p[1:-1]
        if "," not in inner:
            t = _t(inner)
            if t and _cmp(ver, t) == 0:
                return True
            continue
        lo, hi = inner.split(",", 1)
        ok = True
        if lo.strip():
            c = _cmp(ver, _t(lo))
            ok &= c > 0 or (lo_inc and c == 0)
        if hi.strip():
            c = _cmp(ver, _t(hi))
            ok &= c < 0 or (hi_inc and c == 0)
        if ok:
            return True
    return False


def _semver_single(pred: str, ver: tuple) -> bool | None:
    pred = pred.strip()
    if pred in ("*", "", "x"):
        return True
    m = re.match(r"^(>=|<=|>|<|=|~|\^)?\s*(.+)$", pred)
    if not m:
        return None
    op, v = m.group(1) or "", m.group(2).strip()
    v = v.split("-")[0].split("+")[0]
    if v.endswith((".x", ".X", ".*")):
        base = _t(v[:-2])
        return tuple(ver[: len(base)]) == base if base else None
    t = _t(v)
    if not t:
        return None
    c = _cmp(ver, t)
    if op == ">=":
        return c >= 0
    if op == "<=":
        return c <= 0
    if op == ">":
        return c > 0
    if op == "<":
        return c < 0
    if op == "~":
        upper = (t[0], t[1] + 1) if len(t) >= 2 else (t[0] + 1,)
        return c >= 0 and _cmp(ver, upper) < 0
    if op == "^":
        upper = (t[0] + 1,) if t[0] > 0 else (0, t[1] + 1)
        return c >= 0 and _cmp(ver, upper) < 0
    return c == 0


def _semver_match(rng: str, ver: tuple) -> bool | None:
    alternatives = re.split(r"\s*\|\|\s*|\s{2,}(?=[<>=~^\d])", rng) if "||" in rng else [rng]
    # fabricの配列指定はスペース連結されて届く ("1.20" "1.20.1") → いずれか一致
    results = []
    for alt in alternatives:
        preds = alt.split()
        # 比較演算子が無い単純なリストはOR扱い
        if len(preds) > 1 and not any(re.match(r"^(>=|<=|>|<)", p) for p in preds):
            rs = [_semver_single(p, ver) for p in preds]
            results.append(True if True in rs else (None if None in rs else False))
            continue
        rs = [_semver_single(p, ver) for p in preds]
        if None in rs:
            results.append(None)
        else:
            results.append(all(rs))
    if True in results:
        return True
    if None in results:
        return None
    return False


def matches(rng: str, version: str, maven: bool = False) -> bool | None:
    """versionがrngを満たすか。判定不能ならNone. maven=TrueならForge系の書式として扱う."""
    if not rng or not version:
        return None
    ver = _t(version)
    if not ver:
        return None
    rng = rng.strip()
    try:
        if rng[0] in "[(" or (maven and re.match(r"^[\d.]+$", rng)):
            return _maven_match(rng, ver)
        if re.match(r"^[\d.]+$", rng):
            # fabricの"1.20.1"は完全一致、Mavenの裸指定も一般的に完全一致として扱う
            return _cmp(ver, _t(rng)) == 0
        return _semver_match(rng, ver)
    except Exception:
        return None
