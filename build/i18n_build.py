"""ja_short.tsv (番号\t日本語) と en_short.tsv (番号\t英語) から msm/gui/i18n_en.py を作る (開発用)."""
import json
import sys
from pathlib import Path

ja = dict(l.split("\t", 1) for l in Path(sys.argv[1]).read_text("utf-8").splitlines() if "\t" in l)
en = dict(l.split("\t", 1) for l in Path(sys.argv[2]).read_text("utf-8").splitlines() if "\t" in l)
out = Path(__file__).resolve().parents[1] / "msm" / "gui" / "i18n_en.py"
existing = {}
if out.exists():
    ns = {}
    exec(out.read_text("utf-8"), ns)
    existing = ns.get("EN", {})
pairs = dict(existing)
for k, v in en.items():
    if k in ja and v.strip():
        pairs[ja[k]] = v.strip()
body = json.dumps(dict(sorted(pairs.items())), ensure_ascii=False, indent=0)
out.write_text('"""英語UIの辞書 (日本語の表示文字列 → 英語)。build/i18n_build.py で生成."""\n# flake8: noqa\nEN = ' + body + "\n", "utf-8")
print(len(pairs), "entries ->", out)
