"""サーバー設定プリセットとPC性能に応じたおすすめ."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Preset:
    key: str
    label: str
    description: str
    max_mem_mb: int
    min_mem_mb: int
    props: dict = field(default_factory=dict)


PRESETS = [
    Preset("light", "軽量", "低スペックPC・ノートPC向け。描画距離を抑えて軽くします。", 2048, 1024,
           {"view-distance": "6", "simulation-distance": "5", "max-players": "5"}),
    Preset("standard", "標準", "一般的なPC向けのバランス設定です。", 4096, 1024,
           {"view-distance": "10", "simulation-distance": "8", "max-players": "10"}),
    Preset("high", "高性能PC向け", "メモリ32GB以上・高性能CPU向け。遠くまで描画します。", 8192, 2048,
           {"view-distance": "14", "simulation-distance": "10", "max-players": "20"}),
    Preset("modded", "MOD大量向け", "100個以上のMODを入れたサーバー向け。メモリを多めにし負荷を抑えます。", 8192, 4096,
           {"view-distance": "8", "simulation-distance": "6", "max-players": "8", "allow-flight": "true"}),
    Preset("small", "少人数向け", "2～4人で遊ぶ向け。", 4096, 1024,
           {"max-players": "4", "view-distance": "10", "simulation-distance": "8"}),
    Preset("large", "大人数向け", "20人以上向け。1人あたりの負荷を抑えます。", 8192, 2048,
           {"max-players": "40", "view-distance": "8", "simulation-distance": "6"}),
]


def pc_info() -> dict:
    import os

    import psutil

    vm = psutil.virtual_memory()
    return {
        "total_mb": vm.total // 1024 // 1024,
        "available_mb": vm.available // 1024 // 1024,
        "cpu_logical": psutil.cpu_count() or 1,
        "cpu_physical": psutil.cpu_count(logical=False) or 1,
        "cpu_name": os.environ.get("PROCESSOR_IDENTIFIER", ""),
    }


def recommend(total_mb: int, cpu_logical: int, mod_count: int, players: int = 5) -> tuple[Preset, list[str]]:
    """おすすめプリセットと理由."""
    reasons = []
    if mod_count >= 100:
        p = next(x for x in PRESETS if x.key == "modded")
        reasons.append(f"MODが{mod_count}個あるため「MOD大量向け」をおすすめします。")
    elif total_mb < 8 * 1024 or cpu_logical <= 4:
        p = next(x for x in PRESETS if x.key == "light")
        reasons.append("PCのメモリまたはCPUコア数が少ないため「軽量」をおすすめします。")
    elif total_mb >= 32 * 1024 and cpu_logical >= 12:
        p = next(x for x in PRESETS if x.key == "high")
        reasons.append("メモリ・CPUに余裕があるため「高性能PC向け」も使えます。")
    elif players >= 20:
        p = next(x for x in PRESETS if x.key == "large")
        reasons.append("参加人数が多いため「大人数向け」をおすすめします。")
    else:
        p = next(x for x in PRESETS if x.key == "standard")
        reasons.append("一般的な構成のため「標準」をおすすめします。")
    safe = safe_max_memory(total_mb)
    rec_mem = min(p.max_mem_mb, safe)
    if mod_count >= 200:
        rec_mem = min(max(rec_mem, 10240), safe)
    elif mod_count >= 50:
        rec_mem = min(max(rec_mem, 6144), safe)
    if rec_mem < p.max_mem_mb:
        reasons.append(f"PCのメモリ({total_mb // 1024}GB)に合わせ、最大メモリを{rec_mem // 1024}GBに抑えます (OSと他アプリ用に余裕を残します)。")
    p = Preset(p.key, p.label, p.description, rec_mem, min(p.min_mem_mb, rec_mem), dict(p.props))
    return p, reasons


def safe_max_memory(total_mb: int) -> int:
    """サーバーに割り当てて良い最大メモリの目安 (OS用に4GB以上と全体の30%を残す)."""
    reserve = max(4096, int(total_mb * 0.3))
    return max(1024, (total_mb - reserve) // 512 * 512)
