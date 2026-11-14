"""クラス解析によるクライアント専用判定の精度確認 (Modrinthの client_side/server_side を正解とする)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures import modrinth_file  # noqa: E402

from msm.core import net  # noqa: E402
from msm.mods.classify import classify  # noqa: E402
from msm.mods.classscan import scan  # noqa: E402
from msm.mods.metadata import read_mod  # noqa: E402

SLUGS = ["sodium", "iris", "entityculling", "mouse-tweaks", "controlling", "betterf3", "chat-heads",
         "ambientsounds", "xaeros-minimap", "jei", "appleskin", "jade", "geckolib", "ferrite-core", "modernfix",
         "create", "farmers-delight", "sophisticated-backpacks", "architectury-api", "cloth-config", "immediatelyfast",
         "dynamic-fps", "embeddium", "oculus", "not-enough-animations", "travelersbackpack", "waystones", "balm"]
root = Path(sys.argv[1])
rows = []
for loader, mc in (("neoforge", "1.21.1"), ("forge", "1.20.1")):
    for slug in SLUGS:
        try:
            p = net.get_json(f"https://api.modrinth.com/v2/project/{slug}")
            m = modrinth_file(slug, loader, mc, root / f"eval-{loader}")
        except Exception as e:  # noqa: BLE001
            continue
        truth = "client" if p["server_side"] == "unsupported" else ("server" if p["client_side"] == "unsupported" else "both")
        t0 = time.time()
        r = scan(m["path"])
        c = classify(read_mod(m["path"]), None, set())
        rows.append((loader, slug, truth, c.side if c.side == "client" else "-", c.confidence, f"{r.client_ratio:.0%}",
                     f"{time.time() - t0:.1f}s", r.mod_classes, "; ".join(c.reasons)[:110]))
tp = fp = fn = 0
for row in rows:
    loader, slug, truth, hint, conf = row[:5]
    flagged = hint == "client" and conf in ("確定", "高", "中")
    if flagged and truth == "client":
        tp += 1
    elif flagged and truth != "client":
        fp += 1
    elif not flagged and truth == "client":
        fn += 1
    print(*row, sep=" | ")
print(f"\n検出 (確定/高/中): 正解 {tp} / 誤検出 {fp} / 見逃し {fn}  (総数 {len(rows)})")
