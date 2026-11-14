"""英語UI用: 画面に出る日本語の文字列 (行単位) を抽出し、未翻訳の行を一覧にする (開発用).

python build/i18n_extract.py            → 未翻訳の行を表示
python build/i18n_extract.py --dump X   → 全行を番号付きでファイルへ
"""
import ast
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JP = re.compile(r"[぀-ヿ一-鿿]")
FILES = sorted(glob.glob(str(ROOT / "msm" / "gui" / "**" / "*.py"), recursive=True)) + [
    str(ROOT / "msm" / p) for p in ("server/properties.py", "server/presets.py", "publish/tunnels.py", "server/process.py",
                                     "mods/classify.py", "server/providers.py", "integrations/discord.py",
                                     "mods/clientpack.py", "server/schedule.py")]
SKIP_FILES = {"i18n_en.py"}


def lines() -> list[str]:
    out = set()
    for f in FILES:
        if Path(f).name in SKIP_FILES:
            continue
        tree = ast.parse(Path(f).read_text("utf-8"))
        skip = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                skip.update(id(v) for v in node.values)  # f文字列の断片は照合できないので除外
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)                     and node.func.value.id == "log":
                skip.update(id(a) for a in ast.walk(node))  # ログ出力は翻訳しない
        for node in ast.walk(tree):
            vals = []
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                vals = [node.value]
            for v in vals:
                if not JP.search(v):
                    continue
                for l in v.split("\n"):
                    l = l.strip()
                    if l and JP.search(l):
                        out.add(l)
    return sorted(out)


if __name__ == "__main__":
    all_lines = lines()
    if "--dump" in sys.argv:
        dest = Path(sys.argv[sys.argv.index("--dump") + 1])
        dest.write_text("\n".join(f"{i}\t{l}" for i, l in enumerate(all_lines)), "utf-8")
        print(len(all_lines), "lines")
    else:
        sys.path.insert(0, str(ROOT))
        from msm.gui.i18n_en import EN

        missing = [l for l in all_lines if l not in EN]
        print(f"{len(all_lines)} lines / missing {len(missing)}")
        for l in missing[:200]:
            print(" ", l)
