"""MODエラー・起動失敗の自動診断.

解析対象: logs/latest.log, logs/debug.log, crash-reports, 終了コード, 起動コンソール, MOD一覧, 依存関係
確信度: 確定 / 可能性:高 / 可能性:中 / 不明
「診断結果」はログから推定した原因であり、実際の原因と異なる場合がある。断定できない場合は「不明」とする。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from msm.mods import deps as depmod
from msm.mods.metadata import ModMeta

LEVELS = ["確定", "可能性:高", "可能性:中", "不明"]


@dataclass
class Finding:
    level: str
    title: str
    detail: str
    advice: str
    category: str
    mod_files: list[str] = field(default_factory=list)
    mod_ids: list[str] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    evidence: str = ""

    def text(self) -> str:
        s = f"原因の可能性：{self.level.replace('可能性:', '')}\n\n{self.title}\n"
        if self.detail:
            s += f"\n{self.detail}\n"
        if self.advice:
            s += f"\n推奨対応：\n{self.advice}\n"
        if self.evidence:
            s += f"\n根拠となったログ:\n{self.evidence}\n"
        return s


def _tail(path: Path, max_bytes: int = 3_000_000) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def collect_texts(server_dir: Path, since: float | None = None, console: list[str] | None = None) -> dict[str, str]:
    """診断に使うテキストを集める。sinceより新しいクラッシュレポートだけを使う."""
    out: dict[str, str] = {}
    for rel in ("logs/latest.log", "logs/debug.log"):
        p = server_dir / rel
        if p.exists() and (since is None or p.stat().st_mtime >= since - 5):
            out[rel] = _tail(p)
    cr = server_dir / "crash-reports"
    if cr.exists():
        reports = sorted(cr.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if reports and (since is None or reports[0].stat().st_mtime >= since - 5):
            out[f"crash-reports/{reports[0].name}"] = _tail(reports[0])
    hs = sorted(server_dir.glob("hs_err_pid*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if hs and (since is None or hs[0].stat().st_mtime >= since - 5):
        out[hs[0].name] = _tail(hs[0], 200_000)
    if console:
        out["console"] = "\n".join(console)
    return out


def _evidence(text: str, m: re.Match, before: int = 0, after: int = 1) -> str:
    start = text.rfind("\n", 0, m.start())
    for _ in range(before):
        start = text.rfind("\n", 0, max(0, start - 1))
    end = m.end()
    for _ in range(after):
        nxt = text.find("\n", end + 1)
        if nxt == -1:
            break
        end = nxt
    return text[max(0, start):end].strip()[:800]


class Analyzer:
    def __init__(self, mc_version: str = "", server_type: str = "", java_major: int = 0,
                 mods: list[ModMeta] | None = None, port: int = 0):
        self.mc = mc_version
        self.type = server_type
        self.java = java_major
        self.mods = mods or []
        self.port = port
        self.idx = depmod.build_index(self.mods)
        self.findings: list[Finding] = []
        self._keys: set[str] = set()

    def _files_for(self, ids: list[str]) -> list[str]:
        out = []
        for i in ids:
            m = self.idx.get(i) or self.idx.get(i.lower())
            if m and m.file not in out:
                out.append(m.file)
        return out

    def add(self, f: Finding, key: str) -> None:
        if key in self._keys:
            return
        self._keys.add(key)
        self.findings.append(f)

    # ------------------------------------------------------------------
    def run(self, texts: dict[str, str], exit_code: int | None = None) -> list[Finding]:
        full = "\n".join(texts.values())
        self._java(full)
        self._eula(full)
        self._port(full)
        self._memory(full)
        self._missing_deps(full)
        self._duplicates(full)
        self._client_only(full)
        self._mixin(full)
        self._config(full)
        self._entrypoint(full)
        self._crash_report(texts)
        self._class_errors(full)
        self._jvm_crash(texts)
        self._static_mod_checks()
        if not any(f.level != "不明" for f in self.findings) and (exit_code not in (None, 0) or full.strip()):
            if exit_code not in (None, 0) or re.search(r"(?i)exception|error|crash", full[-20000:]):
                self.add(Finding(
                    "不明", "原因を特定できませんでした",
                    f"終了コード: {exit_code}" if exit_code is not None else "",
                    "ログの末尾とクラッシュレポートを確認してください。MODが原因と思われる場合は「自動切り分け」で"
                    "問題のMODを絞り込めます (元の環境は変更しません)。",
                    "unknown"), "unknown")
        self.findings.sort(key=lambda f: LEVELS.index(f.level))
        return self.findings

    # ---- 各ルール ----
    def _java(self, t: str) -> None:
        m = re.search(r"class file version (\d+)\.\d+\)?, this version of the Java Runtime only recognizes class file versions up to (\d+)", t)
        if not m:
            m = re.search(r"UnsupportedClassVersionError.*?class file version (\d+)", t)
        if m:
            need = int(m.group(1)) - 44
            cur = (int(m.group(2)) - 44) if m.lastindex and m.lastindex >= 2 else self.java
            self.add(Finding("確定", "Javaのバージョンが古すぎます",
                             f"現在: Java {cur}\n必要: Java {need} 以上",
                             f"サーバー設定でJava {need}以上を選択してください (アプリ設定の「Java」タブから自動導入できます)。",
                             "java", actions=[{"type": "java", "major": need}], evidence=_evidence(t, m)), "java-old")
            return
        m = re.search(r"AppClassLoader cannot be cast to class java\.net\.URLClassLoader|"
                      r"ClassCastException: class jdk\.internal\.loader", t)
        if m:
            self.add(Finding("確定", "このバージョンのForgeはJava 8が必要です",
                             f"現在: Java {self.java or '不明'}", "サーバー設定でJava 8を選択してください。",
                             "java", actions=[{"type": "java", "major": 8}], evidence=_evidence(t, m)), "java-8")
            return
        m = re.search(r"(?i)(?:requires|need(?:s)?) java (\d+)(?: or (?:newer|above|later))?|"
                      r"Java (\d+) or (?:newer|above) is required|Unsupported Java detected \((\d+)\)", t)
        if m:
            need = next((g for g in m.groups() if g), "")
            self.add(Finding("可能性:高", "Javaのバージョンが合っていない可能性があります",
                             f"現在: Java {self.java or '不明'} / ログ上の要求: Java {need}",
                             "サーバー設定のJavaを変更してください。", "java",
                             actions=[{"type": "java", "major": int(need)}] if need.isdigit() else [],
                             evidence=_evidence(t, m)), "java-hint")

    def _eula(self, t: str) -> None:
        m = re.search(r"You need to agree to the EULA", t)
        if m:
            self.add(Finding("確定", "Minecraft EULAに同意していません", "",
                             "サーバー起動時の確認画面でEULAに同意してください。", "eula",
                             actions=[{"type": "eula"}], evidence=_evidence(t, m)), "eula")

    def _port(self, t: str) -> None:
        m = re.search(r"FAILED TO BIND TO PORT|Address already in use|BindException", t)
        if m:
            self.add(Finding("確定", "ポートがすでに使用されています", f"ポート: {self.port}",
                             "同じポートを使う別のサーバーを停止するか、サーバー設定でポート番号を変更してください。",
                             "port", actions=[{"type": "port"}], evidence=_evidence(t, m)), "port")

    def _memory(self, t: str) -> None:
        m = re.search(r"OutOfMemoryError|Java heap space|GC overhead limit exceeded", t)
        if m:
            self.add(Finding("確定", "メモリ不足です", "サーバーに割り当てたメモリが足りません。",
                             "サーバー設定で最大メモリを増やしてください (MOD 100個以上なら6～10GBが目安)。"
                             "PCのメモリが足りない場合は描画距離を下げてください。", "memory",
                             actions=[{"type": "memory"}], evidence=_evidence(t, m)), "oom")
        m = re.search(r"Could not reserve enough space for (?:object heap|\d+KB object heap)|"
                      r"Invalid (?:maximum|initial) heap size|insufficient memory for the Java Runtime", t)
        if m:
            self.add(Finding("確定", "Javaが指定されたメモリを確保できません",
                             "割り当てメモリがPCの空きメモリより大きいか、32bit版Javaを使っています。",
                             "最大メモリを減らすか、64bit版Javaを使ってください。", "memory",
                             actions=[{"type": "memory"}], evidence=_evidence(t, m)), "heap-reserve")

    def _missing_deps(self, t: str) -> None:
        # Forge 1.18+ / NeoForge (旧形式)
        for m in re.finditer(r"Mod ID: '([^']+)', Requested by: '([^']+)', Expected range: '([^']*)', Actual version: '([^']*)'", t):
            dep, by, rng, actual = m.groups()
            if dep == "minecraft":
                self.add(Finding("確定", f"{by} が Minecraft {self.mc} に対応していません",
                                 f"現在：Minecraft {actual}\nMOD側：Minecraft {rng}",
                                 f"{by} の {actual} 対応バージョンを確認してください。", "mc",
                                 self._files_for([by]), [by], [{"type": "disable", "ids": [by]}], _evidence(t, m)),
                         f"mc-{by}")
            elif dep in ("forge", "neoforge", "fml", "javafml"):
                self.add(Finding("確定", f"{by} はこのローダーバージョンに対応していません",
                                 f"必要: {dep} {rng} / 現在: {actual}",
                                 f"ローダーを更新するか、{by} の対応バージョンを使ってください。", "loader",
                                 self._files_for([by]), [by], evidence=_evidence(t, m)), f"loaderver-{by}")
            elif "MISSING" in actual.upper():
                self.add(Finding("確定", f"前提MOD「{dep}」が不足しています",
                                 f"{by} が {dep} ({rng}) を必要としています。",
                                 f"{dep} の Minecraft {self.mc} 対応版を追加してください。", "missing",
                                 self._files_for([by]), [by, dep], [{"type": "find_mod", "id": dep}], _evidence(t, m)),
                         f"missing-{dep}-{by}")
            else:
                self.add(Finding("確定", f"MOD「{dep}」のバージョンが合っていません",
                                 f"{by} が {dep} {rng} を必要としていますが、導入されているのは {actual} です。",
                                 f"{dep} のバージョンを合わせてください。", "version",
                                 self._files_for([dep, by]), [dep, by], evidence=_evidence(t, m)), f"ver-{dep}-{by}")
        # NeoForge 1.20.4+
        for m in re.finditer(r"Mod (\S+) requires (\S+) (\S+(?: or above)?)\s*\n\s*Currently, (\S+) is (not installed|\S+)", t):
            by, dep, rng, _d, cur = m.groups()
            missing = cur == "not installed"
            self.add(Finding("確定", f"前提MOD「{dep}」が{'不足しています' if missing else 'のバージョンが合っていません'}",
                             f"{by} が {dep} {rng} を必要としています。現在: {cur}",
                             f"{dep} の対応バージョンを追加・更新してください。", "missing" if missing else "version",
                             self._files_for([by]), [by, dep], evidence=_evidence(t, m)), f"neo-{dep}-{by}")
        # MOD独自の起動時チェック (例: REIのerror_notifier)
        for m in re.finditer(r"-\s*(.+?) is a required dependency of (.+?)(?: v?\d[\w.\-+]*)? but it is not installed", t):
            dep, by = m.group(1).strip(), m.group(2).strip()
            files = [x.file for x in self.mods if x.display_name == by or by.startswith(x.display_name)]
            url = re.search(r"https?://\S+", t[m.end(): m.end() + 300])
            self.add(Finding("確定", f"前提MOD「{dep}」が不足しています", f"{by} が {dep} を必要としています。",
                             f"{dep} の Minecraft {self.mc} 対応版を追加してください。" + (f"\n配布ページ (ログに記載): {url.group(0)}" if url else ""),
                             "missing", files, [], [{"type": "find_mod", "id": dep}], _evidence(t, m, 1, 1)), f"missing-{dep}-{by}")
        # Fabric
        for m in re.finditer(r"Mod '([^']+)' \(([\w\-.]+)\) \S+ requires (.+?) of (?:mod )?'?([^',!]+?)'?(?: \(([\w\-.]+)\))?, which is missing", t):
            name, mid, rng, depname, depid = m.groups()
            dep = depid or depname
            if dep == "minecraft":
                self.add(Finding("確定", f"{name} が Minecraft {self.mc} に対応していません",
                                 f"現在：Minecraft {self.mc}\nMOD側：{rng}", f"{name} の {self.mc} 対応バージョンを確認してください。",
                                 "mc", self._files_for([mid]), [mid], [{"type": "disable", "ids": [mid]}],
                                 _evidence(t, m)), f"mc-{mid}")
            else:
                self.add(Finding("確定", f"前提MOD「{depname}」が不足しています", f"{name} が {depname} ({rng}) を必要としています。",
                                 f"{depname} を追加してください。", "missing", self._files_for([mid]), [mid, dep],
                                 [{"type": "find_mod", "id": dep}], _evidence(t, m)), f"missing-{dep}-{mid}")
        for m in re.finditer(r"Mod '([^']+)' \(([\w\-.]+)\) \S+ requires (.+?) of (?:mod )?'?([^',!]+?)'?(?: \(([\w\-.]+)\))?, but only the wrong version is present: ([^!\n]+)", t):
            name, mid, rng, depname, depid, actual = m.groups()
            dep = depid or depname
            cat = "mc" if dep == "minecraft" else ("loader" if dep == "fabricloader" else "version")
            self.add(Finding("確定", f"{name} が必要とする {depname} のバージョンが合っていません",
                             f"必要: {rng} / 現在: {actual}", f"{depname} を必要なバージョンにするか、{name} の別バージョンを使ってください。",
                             cat, self._files_for([mid]), [mid], evidence=_evidence(t, m)), f"wrongver-{dep}-{mid}")

    def _duplicates(self, t: str) -> None:
        for m in re.finditer(r"(?:Found duplicate mods|DuplicateModsFoundException)[^\n]*\n?(?:.*?Mod ID: '([^']+)' from mod files: ([^\n]+))?", t):
            mid = m.group(1) or ""
            files = m.group(2) or ""
            self.add(Finding("確定", f"同じMODが重複しています{(' (' + mid + ')') if mid else ''}", files,
                             "同じMODの古い版と新しい版が両方入っていないか確認し、片方を無効化してください。", "duplicate",
                             self._files_for([mid]) if mid else [], [mid] if mid else [], evidence=_evidence(t, m, 0, 3)),
                     f"dup-{mid}")
        for m in re.finditer(r"Mod ID '([^']+)' is present multiple times|Duplicate mod: (\S+)|duplicate mod id (\S+)", t, re.I):
            mid = next(g for g in m.groups() if g)
            self.add(Finding("確定", f"同じMODが重複しています ({mid})", "", "どちらか一方を無効化してください。", "duplicate",
                             [], [mid], evidence=_evidence(t, m, 0, 3)), f"dup-{mid}")

    def _client_only(self, t: str) -> None:
        ids: list[str] = []
        ev = ""
        for m in re.finditer(r"invalid dist DEDICATED_SERVER", t):
            seg = t[max(0, m.start() - 3000): m.end() + 6000]
            for mm in re.finditer(r"(?:TRANSFORMER|MC-BOOTSTRAP|LAYER PLUGIN)?/?([a-z][a-z0-9_\-]{1,63})@[\w.\-+]+/", seg):
                mid = mm.group(1)
                if mid not in ("minecraft", "forge", "neoforge", "fml", "javafml", "mixin") and mid not in ids:
                    ids.append(mid)
            ev = ev or _evidence(t, m, 0, 3)
        for m in re.finditer(r"Environment type SERVER|Cannot load class net\.minecraft\.client\.\S+ in environment type SERVER", t):
            seg = t[max(0, m.start() - 2000): m.end() + 3000]
            for mm in re.finditer(r"(?:Mixin apply for mod|provided by '|from mod )([\w\-]+)", seg):
                if mm.group(1) not in ids:
                    ids.append(mm.group(1))
            ev = ev or _evidence(t, m, 0, 3)
        if ev:
            files = self._files_for(ids)
            from msm.mods.classify import remember_client_only_from_log

            if ids:
                remember_client_only_from_log(ids[:3])
            self.add(Finding("確定" if files else "可能性:高", "クライアント専用MODがサーバーに入っています",
                             ("該当MOD (候補): " + ", ".join(ids[:5])) if ids else "MOD名をログから特定できませんでした。",
                             "該当MODをサーバー側で無効化してください (プレイヤー側には残してください)。", "client",
                             files, ids, [{"type": "disable", "ids": ids[:3]}] if files else [], ev), "client-only")

    def _mixin(self, t: str) -> None:
        for m in re.finditer(r"Mixin apply for mod ([\w\-]+) failed|Mixin \[([^\]]+)\] from phase \[\w+\] in config \[([^\]]+)\] FAILED|"
                             r"MixinApplyError|InvalidMixinException|MixinTransformerError", t):
            mid = m.group(1) or ""
            cfg = m.group(3) or ""
            if not mid and cfg:
                mid = re.split(r"[.\-_]mixins?", cfg)[0].split(".")[0]
            self.add(Finding("可能性:高", f"Mixinエラー (MOD同士の競合またはバージョン違い){(' : ' + mid) if mid else ''}",
                             f"設定: {cfg}" if cfg else "",
                             "表示されたMODとその前提MODのバージョンが、Minecraft・ローダーと一致しているか確認してください。"
                             "直前に追加・更新したMODがあれば無効化して起動を試してください。", "mixin",
                             self._files_for([mid]) if mid else [], [mid] if mid else [], evidence=_evidence(t, m, 0, 2)),
                     f"mixin-{mid or cfg}")
            if len([f for f in self.findings if f.category == "mixin"]) >= 5:
                break

    def _config(self, t: str) -> None:
        for m in re.finditer(r"(?:Failed loading config file|Config file|Configuration file|Failed to load config)\s+['\"]?([^\s'\"]+\.(?:toml|json|json5|cfg|yml|yaml|properties))['\"]?[^\n]*(?:failed|not correct|corrupt|invalid|error|exception)?|"
                             r"(?:ParsingException|TomlParsingException|MalformedJsonException)[^\n]*?([\w./\\\-]+\.(?:toml|json|json5|cfg))", t, re.I):
            path = m.group(1) or m.group(2) or ""
            if not path:
                continue
            self.add(Finding("可能性:高", "設定ファイル (config) が壊れている可能性があります", f"ファイル: {path}",
                             "設定ファイルを退避して再生成させると直ることがあります (アプリの「configを退避」で元ファイルは保管されます)。",
                             "config", actions=[{"type": "config_quarantine", "path": path}], evidence=_evidence(t, m)),
                     f"config-{path}")

    def _entrypoint(self, t: str) -> None:
        for m in re.finditer(r"Could not execute entrypoint stage '(\w+)' due to errors, provided by '([\w\-]+)'", t):
            mid = m.group(2)
            self.add(Finding("可能性:高", f"MOD「{mid}」の初期化に失敗しました", "",
                             f"{mid} のバージョンがMinecraft・前提MODと合っているか確認してください。", "load",
                             self._files_for([mid]), [mid], evidence=_evidence(t, m)), f"entry-{mid}")
        for m in re.finditer(r"Caught exception from [^\n(]*\(([a-z0-9_\-]+)\)|Failed to create mod instance\. ModID: ([a-z0-9_\-]+)|"
                             r"Exception caught during firing event[^\n]*\n(?:[^\n]*\n){0,3}?[^\n]*TRANSFORMER/([a-z0-9_\-]+)@", t):
            mid = next((g for g in m.groups() if g), "")
            if not mid or mid in ("minecraft", "forge", "neoforge"):
                continue
            self.add(Finding("可能性:高", f"MOD「{mid}」の読み込みに失敗しました", "",
                             "前提MOD不足・バージョン違い・他MODとの競合が考えられます。", "load",
                             self._files_for([mid]), [mid], evidence=_evidence(t, m, 0, 2)), f"load-{mid}")
        if re.search(r"Incompatible mods found!|Mod resolution failed|Loading errors encountered", t) and not any(
                f.category in ("missing", "version", "mc", "loader", "duplicate") for f in self.findings):
            m = re.search(r"Incompatible mods found!|Mod resolution failed|Loading errors encountered", t)
            self.add(Finding("可能性:高", "MODの読み込み・互換性チェックに失敗しました", "",
                             "ログ内の該当箇所を確認してください。", "load", evidence=_evidence(t, m, 0, 8)), "load-generic")

    def _crash_report(self, texts: dict[str, str]) -> None:
        for name, t in texts.items():
            if not name.startswith("crash-reports"):
                continue
            desc = re.search(r"^Description: (.+)$", t, re.M)
            sus = re.search(r"Suspected Mods?: (.+)$", t, re.M)
            ids = []
            if sus and "NONE" not in sus.group(1).upper() and "Unknown" not in sus.group(1):
                ids = re.findall(r"\(([\w\-]+)\)", sus.group(1))
            exc = re.search(r"^(?:java|net|com|org)\.[\w.$]+(?:Exception|Error)[^\n]*", t, re.M)
            self.add(Finding("可能性:中" if ids else "不明", "クラッシュレポートが出力されています",
                             (f"内容: {desc.group(1)}\n" if desc else "") + (f"例外: {exc.group(0)[:300]}\n" if exc else "")
                             + (f"疑わしいMOD (クラッシュレポートの推定): {sus.group(1)}" if sus else ""),
                             "疑わしいMODがあれば一時的に無効化して確認してください。推定は外れることもあります。", "crash",
                             self._files_for(ids), ids, evidence=name), f"crash-{name}")

    def _class_errors(self, t: str) -> None:
        if any(f.category in ("missing", "version", "mc", "java") for f in self.findings):
            return
        m = re.search(r"(NoSuchMethodError|NoSuchFieldError|NoClassDefFoundError|ClassNotFoundException)[: ]+([^\s]+)", t)
        if m:
            self.add(Finding("可能性:中", "MODまたはライブラリのバージョンが合っていない可能性があります",
                             f"{m.group(1)}: {m.group(2)[:200]}",
                             "エラーの直前に出ているMODを確認し、前提MODを含めてMinecraftのバージョンに合わせてください。",
                             "version", evidence=_evidence(t, m, 1, 3)), "classerr")

    def _jvm_crash(self, texts: dict[str, str]) -> None:
        for name in texts:
            if name.startswith("hs_err_pid"):
                self.add(Finding("可能性:中", "Java自体が異常終了しました", f"{name} が出力されています。",
                                 "Javaを別のバージョン・配布元に変えるか、メモリ設定を見直してください。", "jvm",
                                 evidence=name), "jvm")

    def _static_mod_checks(self) -> None:
        if not self.mods:
            return
        report = depmod.analyze(self.mods, self.mc, self.type)
        lv = {"確定": "確定", "高": "可能性:高", "中": "可能性:中"}
        for iss in report.issues:
            if iss.kind == "missing" and any(f.category == "missing" for f in self.findings):
                continue
            cat = {"missing": "missing", "version": "version", "conflict": "conflict", "duplicate": "duplicate",
                   "mc": "mc", "loader": "loader", "broken": "broken"}[iss.kind]
            title = {"missing": "前提MODが不足しています (MOD情報から検出)", "version": "MODのバージョンが合っていません",
                     "conflict": "MOD同士の競合が宣言されています", "duplicate": "MODが重複しています",
                     "mc": "Minecraftのバージョンに対応していないMODがあります", "loader": "ローダー違いのMODが入っています",
                     "broken": "壊れたMODファイルがあります"}[iss.kind]
            files = [m.file for m in self.mods if m.file_name == iss.file] + iss.related
            self.add(Finding(lv.get(iss.level, "可能性:中"), title, iss.message, iss.advice, cat, files,
                             actions=[{"type": "disable_files", "files": files[:1]}] if iss.kind in ("loader", "broken", "mc") else []),
                     f"static-{iss.kind}-{iss.file}")


def diagnose_server(server, console: list[str] | None = None, exit_code: int | None = None,
                    java_major: int = 0, since: float | None = None, with_mods: bool = True) -> list[Finding]:
    from msm.mods.metadata import scan_dir

    texts = collect_texts(server.path, since, console)
    mods = scan_dir(server.mods_dir, with_hash=False) if with_mods and server.is_modded else []
    a = Analyzer(server.mc_version, server.server_type, java_major, mods, server.port)
    return a.run(texts, exit_code)


def report_text(server, findings: list[Finding]) -> str:
    lines = [f"診断レポート - {server.name}", f"作成日時: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Minecraft {server.mc_version} / {server.type_label} {server.loader_version}", "",
             "※ 以下はログから推定した診断結果です。実際の原因と異なる場合があります。", ""]
    for i, f in enumerate(findings, 1):
        lines.append(f"===== {i}. [{f.level}] {f.title} =====")
        lines.append(f.text())
    if not findings:
        lines.append("問題は検出されませんでした。")
    return "\n".join(lines)
