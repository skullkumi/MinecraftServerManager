"""依存関係を維持したMODの自動切り分け.

安全条件:
- 元のサーバーフォルダー・MODフォルダー・ワールドは一切変更しない (読み取りのみ)
- アプリのデータフォルダー内に独立したテスト環境を作る (新規のテスト用ワールドを使う)
- MODは必ず「必須の前提MODを含めた集合」でテストする (依存関係を壊さない)
- 結果は本番環境へ自動適用しない (レポートとして提示するだけ)
"""
from __future__ import annotations

import copy
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from msm.core.paths import test_env_dir
from msm.core.util import CancelToken, Cancelled, link_or_copy, log
from msm.diagnose.analyzer import Analyzer, Finding
from msm.mods import deps as depmod
from msm.mods.metadata import ModMeta, scan_dir
from msm.server import ports
from msm.server.instance import Server, eula_accepted, write_eula
from msm.server.process import DONE_RE, ServerProcess
from msm.server.properties import write_properties

COPY_DIRS = ["config", "defaultconfigs", "kubejs", "scripts", "global_packs", "openloader", "datapacks"]
LINK_DIRS = ["libraries", ".fabric"]
SKIP_ROOT = {"mods", "logs", "crash-reports", "disabled_mods", ".msm", "backups"}


@dataclass
class TestRun:
    files: list[str]
    passed: bool | None  # None = タイムアウト等で判定不能
    seconds: float
    note: str
    findings: list[Finding] = field(default_factory=list)


@dataclass
class BisectResult:
    culprits: list[str] = field(default_factory=list)  # 原因候補 (ファイルパス)
    culprit_closure: list[str] = field(default_factory=list)
    confidence: str = "不明"
    summary: str = ""
    runs: list[TestRun] = field(default_factory=list)
    env_dir: str = ""
    plan_only: bool = False
    plan_text: str = ""


class TestEnvironment:
    """本番サーバーを元にした独立テスト環境."""

    def __init__(self, server: Server):
        self.server = server
        self.dir = test_env_dir() / f"{server.id}-{time.strftime('%Y%m%d-%H%M%S')}"

    def prepare(self, log_fn) -> None:
        src = self.server.path
        self.dir.mkdir(parents=True, exist_ok=True)
        level = self.server.level_name()
        worlds = {level, f"{level}_nether", f"{level}_the_end"}
        for item in src.iterdir():
            name = item.name
            if name in SKIP_ROOT or name in worlds or name in COPY_DIRS:
                continue
            if item.is_dir():
                if name in LINK_DIRS:
                    log_fn(f"テスト環境へリンク: {name}")
                    for f in item.rglob("*"):
                        if f.is_file():
                            link_or_copy(f, self.dir / f.relative_to(src))
                elif (item / "level.dat").exists():
                    continue  # 他のワールドも使わない
                else:
                    continue
            else:
                if name.endswith((".log", ".zip")) or name.startswith("hs_err"):
                    continue
                link_or_copy(item, self.dir / name) if name.endswith(".jar") else shutil.copy2(item, self.dir / name)
        (self.dir / "mods").mkdir(exist_ok=True)

    def reset_for_run(self, mod_files: list[str]) -> None:
        src = self.server.path
        for d in ["mods", "logs", "crash-reports", "msm_test_world", *COPY_DIRS]:
            p = self.dir / d
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
        for d in COPY_DIRS:
            if (src / d).is_dir():
                shutil.copytree(src / d, self.dir / d)
        (self.dir / "mods").mkdir(exist_ok=True)
        for f in mod_files:
            link_or_copy(Path(f), self.dir / "mods" / Path(f).name)
        port = 25565
        while not ports.is_port_free(port) or port == self.server.port:
            port += 1
        write_properties(self.dir / "server.properties", {
            "level-name": "msm_test_world", "server-port": str(port), "online-mode": "true",
            "view-distance": "4", "simulation-distance": "4", "enable-rcon": "false", "enable-query": "false",
        })
        if eula_accepted(src):
            write_eula(self.dir, True)

    def test_server(self) -> Server:
        s = copy.deepcopy(self.server)
        s.directory = str(self.dir)
        s.id = self.server.id + "-test"
        from msm.server.properties import read_properties

        s.port = int(read_properties(self.dir / "server.properties").get("server-port", "25566"))
        return s

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


class Bisector:
    def __init__(self, server: Server, java_exe: str, java_major: int, timeout: float = 600, max_runs: int = 40,
                 log_fn=None, progress=None, cancel: CancelToken | None = None):
        self.server = server
        self.java = java_exe
        self.java_major = java_major
        self.timeout = timeout
        self.max_runs = max_runs
        self.log = log_fn or (lambda s: None)
        self.progress = progress or (lambda s, f=None: None)
        self.cancel = cancel or CancelToken()
        self.mods: list[ModMeta] = []
        self.edges: dict[str, set[str]] = {}
        self.cache: dict[frozenset, TestRun] = {}
        self.runs: list[TestRun] = []
        self.env: TestEnvironment | None = None

    # ---- 計画 ----
    def load(self) -> None:
        self.mods = [m for m in scan_dir(self.server.mods_dir, with_hash=False) if m.valid_jar]
        self.edges = depmod.dependency_edges(self.mods)

    def ordered_items(self) -> list[str]:
        """依存グループごとにまとめて並べる (分割時に関連MODが離れにくくなる)."""
        comps = depmod.components(self.mods, self.edges)
        return [f for g in comps for f in sorted(g)]

    def plan_text(self) -> str:
        comps = depmod.components(self.mods, self.edges)
        big = [g for g in comps if len(g) > 1]
        lines = [f"MOD数: {len(self.mods)}", f"依存関係グループ: {len(comps)} (うち複数MODのグループ {len(big)})", ""]
        lines.append("切り分けの流れ:")
        lines.append("1. 全MODで起動テスト (問題が再現するか確認)")
        lines.append("2. MODなし (ローダーのみ) で起動テスト")
        lines.append("3. MODを依存グループ単位で半分に分け、前提MODを含めて起動テスト")
        lines.append("4. 失敗した側をさらに分割 (両方成功なら組み合わせ問題として両側を絞り込み)")
        lines.append("5. 原因候補を外して起動できるか確認し、確信度を付けて報告")
        est = 2 + 2 * max(1, len(self.mods)).bit_length() + 1
        lines.append("")
        lines.append(f"予想テスト回数: 約{est}回 (上限 {self.max_runs}回)。1回あたり起動に数分かかります。")
        lines.append("元のサーバー・ワールドは変更しません。テスト環境はアプリのデータフォルダー内に作成されます。")
        for g in big[:15]:
            names = [Path(f).name for f in g]
            lines.append(f"  グループ ({len(g)}個): " + ", ".join(names[:6]) + (" ..." if len(names) > 6 else ""))
        return "\n".join(lines)

    # ---- 実行 ----
    def _test(self, roots: set[str], label: str) -> bool | None:
        files = depmod.closure(set(roots), self.edges)
        key = frozenset(files)
        if key in self.cache:
            return self.cache[key].passed
        if len(self.runs) >= self.max_runs:
            raise RuntimeError(f"テスト回数の上限 ({self.max_runs}回) に達しました")
        self.cancel.check()
        assert self.env
        n = len(self.runs) + 1
        self.progress(f"テスト {n}: {label} (MOD {len(files)}個)", min(0.95, n / max(self.max_runs, 1)))
        self.log(f"--- テスト {n}: {label} / MOD {len(files)}個 ---")
        self.env.reset_for_run(sorted(files))
        ts = self.env.test_server()
        sp = ServerProcess(ts)
        t0 = time.time()
        passed: bool | None = None
        note = ""
        done = threading.Event()
        sp.on_line.append(lambda line: done.set() if DONE_RE.search(line) else None)
        sp.on_state.append(lambda st: done.set() if st in ("stopped", "crashed") else None)
        try:
            sp.start(self.java)
            while not done.wait(1.0):
                if self.cancel.cancelled:
                    sp.kill()
                    raise Cancelled()
                if time.time() - t0 > self.timeout:
                    break
            if sp.state == "running" or any(DONE_RE.search(l) for l in list(sp.lines)[-50:]):
                passed, note = True, "起動成功"
                if not sp.stop(120):
                    sp.kill()
            elif not sp.running:
                passed, note = False, f"起動失敗 (終了コード {sp.exit_code})"
            else:
                passed, note = None, f"{int(self.timeout)}秒以内に起動しませんでした (タイムアウト)"
                sp.kill()
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            passed, note = None, f"テスト実行エラー: {e}"
            sp.kill()
        while sp.running:
            time.sleep(0.5)
        findings = []
        if passed is False:
            from msm.diagnose.analyzer import collect_texts

            sel = [m for m in self.mods if m.file in files]
            a = Analyzer(self.server.mc_version, self.server.server_type, self.java_major, sel, ts.port)
            findings = a.run(collect_texts(self.env.dir, console=list(sp.lines)), sp.exit_code)
        run = TestRun(sorted(files), passed, time.time() - t0, note, findings)
        self.cache[key] = run
        self.runs.append(run)
        self.log(f"結果: {note} ({run.seconds:.0f}秒)")
        return passed

    def _fails(self, roots: set[str], label: str) -> bool:
        r = self._test(roots, label)
        if r is None:
            # タイムアウト等は「失敗」として扱うが、確信度を下げる
            self._uncertain = True
            return True
        return not r

    def _minimize(self, cands: list[str], fixed: set[str], depth: int = 0) -> list[str]:
        while len(cands) > 1:
            half = len(cands) // 2
            a, b = cands[:half], cands[half:]
            if self._fails(set(a) | fixed, f"前半 {len(a)}個" + (f" + 固定 {len(fixed)}個" if fixed else "")):
                cands = a
            elif self._fails(set(b) | fixed, f"後半 {len(b)}個" + (f" + 固定 {len(fixed)}個" if fixed else "")):
                cands = b
            else:
                if depth > 3:
                    return cands
                self.log("どちらの半分も単独では失敗しないため、組み合わせによる問題として絞り込みます")
                a_min = self._minimize(a, fixed | set(b), depth + 1)
                b_min = self._minimize(b, fixed | set(a_min), depth + 1)
                return a_min + b_min
        return cands

    def run(self, keep_env: bool = False) -> BisectResult:
        self._uncertain = False
        res = BisectResult()
        self.load()
        if len(self.mods) < 1:
            res.summary = "MODがありません。"
            return res
        self.env = TestEnvironment(self.server)
        res.env_dir = str(self.env.dir)
        try:
            self.progress("テスト環境を作成しています...", None)
            self.env.prepare(self.log)
            items = self.ordered_items()
            all_set = set(items)
            if not self._fails(all_set, "全MOD"):
                res.summary = ("全MODを入れた状態でも、テスト環境では起動できました。\n"
                               "問題はワールドデータ・特定の操作・プレイヤー接続時に起きる可能性があります (MODだけが原因とは限りません)。")
                res.confidence = "不明"
                return res
            if self._fails(set(), "MODなし (ローダーのみ)"):
                res.summary = ("MODを1つも入れなくても起動に失敗しました。\n"
                               "原因はMOD以外 (Java・ローダー・設定ファイル・メモリなど) の可能性が高いです。")
                res.confidence = "可能性:高"
                return res
            culprits = self._minimize(items, set())
            res.culprits = culprits
            res.culprit_closure = sorted(depmod.closure(set(culprits), self.edges))
            # 検証: 原因候補とそれに依存するMODを除いた構成で起動できるか
            dependents = {f for f in items if depmod.closure({f}, self.edges) & set(culprits)}
            remaining = all_set - dependents
            verified = None
            if remaining and len(self.runs) < self.max_runs:
                verified = self._test(remaining, "検証: 原因候補を除いた全MOD")
            names = ", ".join(Path(c).name for c in culprits)
            if verified is True and not self._uncertain:
                res.confidence = "可能性:高"
                res.summary = (f"原因候補: {names}\n"
                               f"これらのMOD (と、それに依存する{len(dependents) - len(culprits)}個のMOD) を外すと起動できました。")
            elif verified is True:
                res.confidence = "可能性:中"
                res.summary = f"原因候補: {names}\n(一部のテストがタイムアウトしたため確信度を下げています)"
            else:
                res.confidence = "可能性:中"
                res.summary = (f"原因候補: {names}\n"
                               "ただし、これらを外しても起動を確認できませんでした。原因が複数ある可能性があります。")
            if len(culprits) > 1:
                res.summary += "\n複数のMODの組み合わせで問題が起きている可能性があります。"
            return res
        except Cancelled:
            res.summary = "切り分けをキャンセルしました。"
            return res
        except Exception as e:  # noqa: BLE001
            log.exception("bisect")
            res.summary = f"切り分けを中断しました: {e}"
            return res
        finally:
            res.runs = self.runs
            if not keep_env and self.env:
                self.env.cleanup()


def bisect_report(server: Server, res: BisectResult) -> str:
    lines = [f"MOD自動切り分けレポート - {server.name}", time.strftime("%Y-%m-%d %H:%M:%S"), "",
             "※ テスト環境での結果です。本番環境には何も適用していません。", "",
             f"確信度: {res.confidence}", res.summary, ""]
    if res.culprit_closure and set(res.culprit_closure) != set(res.culprits):
        lines.append("原因候補と一緒に読み込まれた前提MOD: " + ", ".join(Path(f).name for f in res.culprit_closure
                                                        if f not in res.culprits))
        lines.append("")
    lines.append(f"テスト回数: {len(res.runs)}")
    for i, r in enumerate(res.runs, 1):
        st = {True: "成功", False: "失敗", None: "判定不能"}[r.passed]
        lines.append(f"  {i}. {st} - MOD {len(r.files)}個 - {r.seconds:.0f}秒 - {r.note}")
        for f in r.findings[:3]:
            lines.append(f"       [{f.level}] {f.title}")
    return "\n".join(lines)
