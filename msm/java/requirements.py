"""Minecraftバージョン・サーバー種類に応じたJava要件の判定.

優先順位:
 1. Mojang公式のバージョン情報 (javaVersion.majorVersion)
 2. PaperMC APIのJava最小要件 (Paperの場合)
 3. 既知の要件表 (オフライン時のフォールバック)
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from msm.core.paths import cache_dir
from msm.core.util import log, mc_version_tuple


@dataclass
class JavaRequirement:
    min_major: int
    recommended: int
    max_major: int | None
    reason: str
    source: str

    def accepts(self, major: int) -> bool:
        if major < self.min_major:
            return False
        if self.max_major is not None and major > self.max_major:
            return False
        return True

    def describe(self) -> str:
        rng = f"Java {self.min_major}以上" if self.max_major is None else (
            f"Java {self.min_major}" if self.min_major == self.max_major else f"Java {self.min_major}～{self.max_major}"
        )
        return f"{rng} (推奨: Java {self.recommended}) - {self.reason} [根拠: {self.source}]"


def fallback_major(mc: str) -> int:
    t = mc_version_tuple(mc)
    if not t:
        return 21
    if t[0] >= 26 or t[0] >= 2:
        return 25
    if t < (1, 17):
        return 8
    if t < (1, 18):
        return 16
    if t < (1, 20, 5):
        return 17
    return 21


def mojang_major(mc: str, allow_network: bool = True) -> tuple[int, str]:
    cache = cache_dir() / "mojang_java.json"
    data: dict = {}
    if cache.exists():
        try:
            data = json.loads(cache.read_text("utf-8"))
        except Exception:
            data = {}
    if mc in data:
        return int(data[mc]), "Mojang公式バージョン情報 (キャッシュ)"
    if allow_network:
        try:
            from msm.server.providers import mojang_version_detail

            detail = mojang_version_detail(mc)
            major = int((detail.get("javaVersion") or {}).get("majorVersion") or 8)
            data[mc] = major
            cache.write_text(json.dumps(data), "utf-8")
            return major, "Mojang公式バージョン情報"
        except Exception as e:  # noqa: BLE001
            log.warning("Mojang Java要件取得失敗 %s: %s", mc, e)
    return fallback_major(mc), "既知の要件表 (オフライン)"


def requirement_for(mc: str, server_type: str, allow_network: bool = True) -> JavaRequirement:
    st = server_type.lower()
    base, source = mojang_major(mc, allow_network)
    t = mc_version_tuple(mc)
    if st == "paper" and allow_network:
        try:
            from msm.server.providers import paper_java_minimum

            pmin = paper_java_minimum(mc)
            if pmin:
                return JavaRequirement(pmin, max(pmin, base), None, "PaperMCが指定する最小Java", "PaperMC API")
        except Exception as e:  # noqa: BLE001
            log.warning("Paper Java要件取得失敗: %s", e)
    if st == "forge" and t and t < (1, 17):
        return JavaRequirement(
            8, 8, 8,
            "1.16.5以前のForgeはJava 8で動作させるのが最も安全です (新しいJavaではModLauncherが起動しない場合があります)",
            source + " + Forge既知要件",
        )
    if t and t < (1, 17):
        return JavaRequirement(8, 8, None, "1.16.5以前はJava 8が標準です", source)
    reason = f"Minecraft {mc} の公式要件"
    return JavaRequirement(base, base, None, reason, source)


def choose_java(installs: list, req: JavaRequirement):
    """要件に合うJavaを選ぶ。推奨バージョン完全一致 > 範囲内で最小 > なし."""
    usable = [j for j in installs if j.ok and req.accepts(j.major)]
    if not usable:
        return None

    def score(j):
        return (
            0 if j.major == req.recommended else 1,
            0 if j.arch == "64bit" else 1,
            j.major,
            0 if j.managed else 1,
        )

    usable.sort(key=score)
    return usable[0]
