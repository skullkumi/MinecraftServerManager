"""MOD・MODパックのインポート (CurseForge非依存).

- CurseForge形式ZIP (manifest.json + overrides)
- Modrinth形式 (.mrpack)
- サーバーパックZIP (mods/ フォルダーを含む)
- JARだけのZIP / MODフォルダー / 個別JAR (複数可)
元のZIP・フォルダー・ファイルは読み取るだけで変更しない。
"""
from __future__ import annotations

import html
import json
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from msm.core import net
from msm.core.db import get_db
from msm.core.util import CancelToken, log
from msm.mods import classify as cls
from msm.mods import curseforge_api as cf
from msm.mods import modrinth_api
from msm.mods.metadata import ModMeta, loader_matches, read_mod, sha1_of

CLIENT_ONLY_OVERRIDE_DIRS = {"resourcepacks", "shaderpacks", "screenshots", "saves", "options.txt", "optionsof.txt",
                             "optionsshaders.txt", "servers.dat", "servers.dat_old", "logs", "crash-reports"}
SERVERPACK_SKIP = {"libraries", "logs", "crash-reports", "run.bat", "run.sh", "user_jvm_args.txt", "eula.txt",
                   "start.bat", "start.sh", "startserver.bat", "startserver.sh", "variables.txt", "world",
                   "server-icon.png"}
MRPACK_ALLOWED_HOSTS = ("cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com")


# ------------------------------------------------------------------ 解析
@dataclass
class ArchiveInfo:
    path: str
    kind: str  # curseforge / mrpack / serverpack / jars / unknown
    name: str = ""
    version: str = ""
    mc_version: str = ""
    loader: str = ""  # forge / neoforge / fabric / quilt / vanilla
    loader_version: str = ""
    mod_count: int = 0
    entries: list[str] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)
    root_prefix: str = ""
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        kinds = {"curseforge": "CurseForge形式MODパック", "mrpack": "Modrinth形式MODパック (.mrpack)",
                 "serverpack": "サーバーパック (modsフォルダー入りZIP)", "jars": "MOD (JAR) をまとめたZIP",
                 "unknown": "判定できない形式"}
        lines = [f"形式: {kinds.get(self.kind, self.kind)}"]
        if self.name:
            lines.append(f"パック名: {self.name} {self.version}")
        if self.mc_version:
            lines.append(f"Minecraft: {self.mc_version}")
        if self.loader:
            lines.append(f"ローダー: {self.loader} {self.loader_version}")
        lines.append(f"MOD数: {self.mod_count}")
        lines += self.notes
        return "\n".join(lines)


def _parse_cf_loader(loader_id: str) -> tuple[str, str]:
    for prefix in ("neoforge", "forge", "fabric", "quilt"):
        if loader_id.startswith(prefix + "-"):
            return prefix, loader_id[len(prefix) + 1:]
    return "", loader_id


def analyze_archive(path: str) -> ArchiveInfo:
    p = Path(path)
    with zipfile.ZipFile(p) as zf:
        names = zf.namelist()
        info = ArchiveInfo(str(p), "unknown", entries=names[:5000])
        if "modrinth.index.json" in names:
            idx = json.loads(zf.read("modrinth.index.json").decode("utf-8"))
            info.kind = "mrpack"
            info.manifest = idx
            info.name = idx.get("name", "")
            info.version = idx.get("versionId", "")
            deps = idx.get("dependencies", {})
            info.mc_version = deps.get("minecraft", "")
            for key, loader in (("neoforge", "neoforge"), ("forge", "forge"), ("fabric-loader", "fabric"),
                                ("quilt-loader", "quilt")):
                if key in deps:
                    info.loader, info.loader_version = loader, deps[key]
                    break
            info.mod_count = sum(1 for f in idx.get("files", []) if f.get("path", "").startswith("mods/"))
            return info
        manifest_name = next((n for n in names if PurePosixPath(n).name == "manifest.json" and n.count("/") <= 1), None)
        if manifest_name:
            try:
                m = json.loads(zf.read(manifest_name).decode("utf-8-sig"))
                if "minecraft" in m and "files" in m:
                    info.kind = "curseforge"
                    info.manifest = m
                    info.root_prefix = manifest_name[: -len("manifest.json")]
                    info.name = m.get("name", "")
                    info.version = m.get("version", "")
                    info.mc_version = m["minecraft"].get("version", "")
                    loaders = m["minecraft"].get("modLoaders", [])
                    primary = next((l for l in loaders if l.get("primary")), loaders[0] if loaders else None)
                    if primary:
                        info.loader, info.loader_version = _parse_cf_loader(primary.get("id", ""))
                    info.mod_count = len(m.get("files", []))
                    over = info.root_prefix + (m.get("overrides") or "overrides") + "/mods/"
                    extra = sum(1 for n in names if n.startswith(over) and n.endswith(".jar"))
                    if extra:
                        info.notes.append(f"overrides内に同梱MOD: {extra}個")
                        info.mod_count += extra
                    return info
            except Exception as e:  # noqa: BLE001
                info.notes.append(f"manifest.jsonを読み込めません: {e}")
        mods_entries = [n for n in names if re.match(r"^(?:[^/]+/)?mods/[^/]+\.jar$", n)]
        if mods_entries:
            info.kind = "serverpack"
            info.root_prefix = mods_entries[0][: mods_entries[0].index("mods/")]
            info.mod_count = len(mods_entries)
            info.notes.append("サーバー用の起動ファイル (run.batなど) やlibrariesは取り込まず、本アプリでローダーを導入します。")
            return info
        jars = [n for n in names if n.lower().endswith(".jar")]
        if jars and len(jars) >= max(1, len([n for n in names if not n.endswith("/")]) * 0.6):
            info.kind = "jars"
            info.mod_count = len(jars)
            return info
        info.notes.append("自動で形式を判定できませんでした。中身を確認して取り込み方法を選んでください。")
        info.mod_count = len(jars)
        return info


def read_modlist_html(zf: zipfile.ZipFile, prefix: str = "") -> list[tuple[str, str]]:
    """CurseForgeのエクスポートに含まれるmodlist.htmlから (名前, URL) を読む."""
    try:
        text = zf.read(prefix + "modlist.html").decode("utf-8", "replace")
    except KeyError:
        return []
    return [(html.unescape(t).strip(), u) for u, t in re.findall(r'<a href="([^"]+)">([^<]+)</a>', text)]


# ------------------------------------------------------------------ 取り込み計画
@dataclass
class PlanItem:
    source: str  # 取り込み元ファイル (ローカル)
    file_name: str
    meta: ModMeta | None = None
    classification: cls.Classification | None = None
    action: str = "add"  # add / skip_same / conflict / exclude_client / review_client / invalid / wrong_loader
    note: str = ""


@dataclass
class ManualItem:
    """自動取得できず、手動ダウンロードが必要なMOD."""
    name: str
    reason: str
    page: str
    file_name: str = ""
    sha1: str = ""
    project_id: int | str = ""
    file_id: int | str = ""
    mod_ids: list[str] = field(default_factory=list)
    done: bool = False

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ImportResult:
    plan: list[PlanItem] = field(default_factory=list)
    manual: list[ManualItem] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    downloaded: int = 0
    staging: str = ""


def classify_many(metas: list[ModMeta], use_modrinth: bool = True) -> dict[str, cls.Classification]:
    mr: dict[str, dict] = {}
    if use_modrinth:
        try:
            mr = modrinth_api.lookup_mods([m.sha1 for m in metas if m.sha1])
        except Exception as e:  # noqa: BLE001
            log.warning("Modrinth照会失敗: %s", e)
    log_ids = cls.client_only_ids_from_logs()
    return {m.file: cls.classify(m, mr.get(m.sha1), log_ids) for m in metas}


def plan_for_files(files: list[Path], target_mods: Path, server_type: str, use_modrinth: bool = True) -> list[PlanItem]:
    metas = [read_mod(f) for f in files]
    classes = classify_many([m for m in metas if m.valid_jar], use_modrinth)
    existing = {p.name.lower(): p for p in target_mods.glob("*.jar")} if target_mods.exists() else {}
    existing_hashes: dict[str, str] | None = None
    plan = []
    for m in metas:
        item = PlanItem(m.file, m.file_name, m, classes.get(m.file))
        if not m.valid_jar:
            item.action, item.note = "invalid", "; ".join(m.errors) or "JARとして読み込めません"
        elif loader_matches(m, server_type) is False:
            item.action, item.note = "wrong_loader", f"{m.loader}用のMODです (サーバー: {server_type})"
        elif item.classification and item.classification.auto_exclude:
            item.action, item.note = "exclude_client", "確実にクライアント専用と判定 (サーバーには入れません)"
        elif item.classification and item.classification.side == cls.CLIENT:
            item.action, item.note = "review_client", f"クライアント専用の可能性 ({item.classification.confidence})"
        if item.action in ("add", "review_client"):
            same = existing.get(m.file_name.lower())
            if same is not None:
                if sha1_of(same) == m.sha1:
                    item.action, item.note = "skip_same", "同じファイルが導入済み"
                else:
                    item.action, item.note = "conflict", "同名で中身が異なるファイルがあります (上書きしません)"
            else:
                if existing_hashes is None:
                    existing_hashes = {sha1_of(p): p.name for p in existing.values()}
                if m.sha1 in existing_hashes:
                    item.action, item.note = "skip_same", f"同じ内容のファイルが導入済み ({existing_hashes[m.sha1]})"
        plan.append(item)
    return plan


def apply_plan(server, plan: list[PlanItem], include_review: set[str] | None = None,
               reason: str = "インポート") -> list[str]:
    """計画を実行する。exclude_client はサーバー内の除外フォルダーに保管 (削除しない)."""
    include_review = include_review or set()
    target = server.mods_dir
    target.mkdir(parents=True, exist_ok=True)
    excluded_dir = server.path / ".msm" / "excluded_client_mods"
    done = []
    for it in plan:
        src = Path(it.source)
        if it.action == "add" or (it.action == "review_client" and it.source in include_review):
            dest = target / it.file_name
            if dest.exists():
                continue
            shutil.copy2(src, dest)
            done.append(f"追加: {it.file_name}")
            _record(server, "import", it.file_name, str(src), str(dest), it.meta.sha1 if it.meta else "", reason)
        elif it.action in ("exclude_client", "review_client"):
            excluded_dir.mkdir(parents=True, exist_ok=True)
            dest = excluded_dir / it.file_name
            if not dest.exists():
                shutil.copy2(src, dest)
            done.append(f"サーバー対象外として保管: {it.file_name}")
            _record(server, "exclude", it.file_name, str(src), str(dest), it.meta.sha1 if it.meta else "",
                    it.note or "クライアント専用")
    return done


def _record(server, action, name, src, dst, sha1, reason):
    get_db().execute(
        "INSERT INTO mod_events(server_id,action,file_name,from_path,to_path,sha1,size,mtime,reason,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (server.id, action, name, src, dst, sha1, Path(dst).stat().st_size if Path(dst).exists() else 0,
         time.time(), reason, time.time()),
    )


# ------------------------------------------------------------------ パックの展開・取得
def _safe_extract(zf: zipfile.ZipFile, member: str, dest_root: Path, rel: str) -> Path | None:
    dest = (dest_root / rel).resolve()
    if not str(dest).startswith(str(dest_root.resolve())):
        raise RuntimeError(f"ZIPに不正なパスが含まれています: {member}")
    if member.endswith("/"):
        dest.mkdir(parents=True, exist_ok=True)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out)
    return dest


def stage_archive(info: ArchiveInfo, staging: Path, server_type: str, progress=None, cancel: CancelToken | None = None,
                  log_fn=None) -> ImportResult:
    """パックの中身を作業用フォルダー (staging) に展開・取得する。サーバーフォルダーにはまだ触れない.

    staging/files/  ... サーバーフォルダーへコピーする設定ファイル等 (mods以外)
    staging/mods/   ... MOD候補
    """
    log_fn = log_fn or (lambda s: None)
    res = ImportResult(staging=str(staging))
    files_dir = staging / "files"
    mods_dir = staging / "mods"
    files_dir.mkdir(parents=True, exist_ok=True)
    mods_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(info.path) as zf:
        if info.kind == "curseforge":
            prefix = info.root_prefix + (info.manifest.get("overrides") or "overrides") + "/"
            _extract_tree(zf, prefix, files_dir, mods_dir, res, CLIENT_ONLY_OVERRIDE_DIRS)
            _cf_download(info, zf, mods_dir, res, progress, cancel, log_fn)
        elif info.kind == "mrpack":
            for pre in ("overrides/", "server-overrides/"):
                _extract_tree(zf, pre, files_dir, mods_dir, res, CLIENT_ONLY_OVERRIDE_DIRS)
            _mrpack_download(info, files_dir, mods_dir, res, progress, cancel, log_fn)
        elif info.kind == "serverpack":
            _extract_tree(zf, info.root_prefix, files_dir, mods_dir, res, SERVERPACK_SKIP, skip_root_jars=True)
        elif info.kind == "jars":
            for n in zf.namelist():
                if n.lower().endswith(".jar"):
                    _safe_extract(zf, n, mods_dir, PurePosixPath(n).name)
        else:
            raise RuntimeError("形式を判定できないZIPです。取り込み方法を選択してください。")
    return res


def stage_unknown(path: str, staging: Path, mode: str) -> ImportResult:
    """判定できないZIPをユーザーの選択に従って展開する (mode: jars / all)."""
    res = ImportResult(staging=str(staging))
    files_dir, mods_dir = staging / "files", staging / "mods"
    files_dir.mkdir(parents=True, exist_ok=True)
    mods_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        for n in zf.namelist():
            if mode == "jars":
                if n.lower().endswith(".jar"):
                    _safe_extract(zf, n, mods_dir, PurePosixPath(n).name)
            else:
                _safe_extract(zf, n, files_dir, n)
    return res


def _extract_tree(zf, prefix, files_dir, mods_dir, res, skip: set[str], skip_root_jars: bool = False):
    for n in zf.namelist():
        if not n.startswith(prefix) or n == prefix:
            continue
        rel = n[len(prefix):]
        top = rel.split("/")[0]
        if top.lower() in skip:
            if not n.endswith("/"):
                res.skipped_files.append(rel)
            continue
        if skip_root_jars and "/" not in rel and rel.lower().endswith((".jar", ".bat", ".sh", ".ps1", ".exe", ".cmd")):
            res.skipped_files.append(rel)
            continue
        if rel.lower().endswith((".exe", ".msi", ".ps1", ".vbs", ".scr")):
            res.skipped_files.append(rel + " (実行ファイルのため除外)")
            continue
        if rel.startswith("mods/") and rel.lower().endswith(".jar") and rel.count("/") == 1:
            _safe_extract(zf, n, mods_dir, rel[5:])
        else:
            _safe_extract(zf, n, files_dir, rel)


def _cf_download(info, zf, mods_dir: Path, res: ImportResult, progress, cancel, log_fn):
    entries = info.manifest.get("files", [])
    if not entries:
        return
    names = read_modlist_html(zf, info.root_prefix)
    if not cf.has_api_key():
        res.messages.append(f"CurseForge APIキーが未設定のため、manifestの{len(entries)}個のMODは自動取得していません。"
                            "「手動ダウンロード補助」から取得できます。")
        for i, e in enumerate(entries):
            nm, url = names[i] if i < len(names) and len(names) == len(entries) else (f"Project {e.get('projectID')}", "")
            page = (url.rstrip("/") + f"/files/{e.get('fileID')}") if url else f"https://www.curseforge.com/projects/{e.get('projectID')}"
            res.manual.append(ManualItem(nm, "APIキー未設定のため自動取得できません", page,
                                         project_id=e.get("projectID", ""), file_id=e.get("fileID", "")))
        return
    file_ids = [int(e["fileID"]) for e in entries]
    try:
        files = cf.get_files(file_ids)
        mods = cf.get_mods(list({int(e["projectID"]) for e in entries}))
    except Exception as ex:  # noqa: BLE001
        res.messages.append(f"CurseForge APIの呼び出しに失敗しました: {ex}")
        for e in entries:
            res.manual.append(ManualItem(f"Project {e.get('projectID')}", f"API失敗: {ex}",
                                         f"https://www.curseforge.com/projects/{e.get('projectID')}",
                                         project_id=e.get("projectID", ""), file_id=e.get("fileID", "")))
        return
    # 配布不可ファイルはModrinthで同一ハッシュを探す
    no_url = [f for f in files.values() if not f.get("downloadUrl")]
    mr_versions = {}
    if no_url:
        try:
            mr_versions = modrinth_api.versions_by_hashes([cf.file_sha1(f) for f in no_url if cf.file_sha1(f)])
        except Exception:
            mr_versions = {}
    total = len(entries)
    for i, e in enumerate(entries):
        if cancel and cancel.cancelled:
            break
        pid, fid = int(e["projectID"]), int(e["fileID"])
        f = files.get(fid)
        mod = mods.get(pid)
        name = (mod or {}).get("name") or (f or {}).get("displayName") or f"Project {pid}"
        page = cf.file_page(mod, pid, fid)
        if mod and mod.get("classId") not in (None, cf.CLASS_MODS):
            res.skipped_files.append(f"{name} ({cf.CLASS_LABELS.get(mod.get('classId'), 'MOD以外')}のためサーバーには不要)")
            continue
        if f is None:
            res.manual.append(ManualItem(name, "ファイル情報を取得できません (削除された可能性)", page, project_id=pid, file_id=fid))
            continue
        sha1 = cf.file_sha1(f)
        dest = mods_dir / f["fileName"]
        if progress:
            progress(f"MOD取得中 ({i + 1}/{total}): {f['fileName']}", (i + 1) / total)
        url = f.get("downloadUrl")
        source = "CurseForge"
        if not url and sha1 in mr_versions:
            mv = mr_versions[sha1]
            prim = next((x for x in mv.get("files", []) if x.get("hashes", {}).get("sha1") == sha1), None)
            if prim:
                url, source = prim["url"], "Modrinth (同一ハッシュ)"
        if not url:
            res.manual.append(ManualItem(name, "作者が外部アプリからのダウンロードを許可していません", page, f["fileName"],
                                         sha1, pid, fid))
            continue
        try:
            if not dest.exists():
                net.download(url, dest, expected_hash=("sha1", sha1) if sha1 else None, cancel=cancel,
                             label=f["fileName"])
            res.downloaded += 1
            log_fn(f"取得 ({source}): {f['fileName']}")
        except Exception as ex:  # noqa: BLE001
            res.manual.append(ManualItem(name, f"ダウンロード失敗: {ex}", page, f["fileName"], sha1, pid, fid))


def _mrpack_download(info, files_dir: Path, mods_dir: Path, res: ImportResult, progress, cancel, log_fn):
    files = info.manifest.get("files", [])
    total = len(files) or 1
    for i, f in enumerate(files):
        if cancel and cancel.cancelled:
            break
        path = f.get("path", "")
        env = f.get("env") or {}
        if env.get("server") == "unsupported":
            res.skipped_files.append(f"{path} (パック作者がサーバー非対応と指定)")
            continue
        if ".." in PurePosixPath(path).parts or path.startswith("/"):
            res.skipped_files.append(f"{path} (不正なパス)")
            continue
        urls = [u for u in f.get("downloads", []) if any(h in u for h in MRPACK_ALLOWED_HOSTS)]
        name = PurePosixPath(path).name
        sha1 = (f.get("hashes") or {}).get("sha1", "")
        if not urls:
            res.manual.append(ManualItem(name, "許可された配布元のURLがありません", "", name, sha1))
            continue
        dest = (mods_dir / name) if path.startswith("mods/") and path.count("/") == 1 else (files_dir / path)
        if progress:
            progress(f"取得中 ({i + 1}/{total}): {name}", (i + 1) / total)
        try:
            if not dest.exists():
                net.download(urls[0], dest, expected_hash=("sha1", sha1) if sha1 else None, cancel=cancel, label=name)
            res.downloaded += 1
            log_fn(f"取得: {name}")
        except Exception as ex:  # noqa: BLE001
            res.manual.append(ManualItem(name, f"ダウンロード失敗: {ex}", urls[0], name, sha1))


def copy_files_to_server(files_dir: Path, server_dir: Path, overwrite: bool) -> tuple[list[str], list[str]]:
    """staging/files をサーバーへコピー。overwrite=Falseなら既存ファイルは残す."""
    copied, kept = [], []
    for src in files_dir.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(files_dir)
        dest = server_dir / rel
        if dest.exists() and not overwrite:
            kept.append(str(rel))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(str(rel))
    return copied, kept


def write_failed_report(server_dir: Path, manual: list[ManualItem], extra: list[str]) -> Path | None:
    if not manual and not extra:
        return None
    p = server_dir / "IMPORT_FAILED.txt"
    lines = [f"自動取得・導入できなかった項目 ({time.strftime('%Y-%m-%d %H:%M:%S')})", ""]
    for m in manual:
        lines.append(f"- {m.name}")
        lines.append(f"    理由: {m.reason}")
        if m.file_name:
            lines.append(f"    ファイル名: {m.file_name}")
        if m.page:
            lines.append(f"    ダウンロードページ: {m.page}")
    if extra:
        lines += ["", "その他:"] + [f"- {e}" for e in extra]
    lines += ["", "アプリの「MOD > 手動ダウンロード補助」タブから、ブラウザで開いてダウンロードフォルダー監視で取り込めます。"]
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")
    return p


def save_pending_manual(server_id: str, items: list[ManualItem]) -> None:
    cur = load_pending_manual(server_id)
    keys = {(m.project_id, m.file_id, m.file_name) for m in cur}
    for it in items:
        if (it.project_id, it.file_id, it.file_name) not in keys:
            cur.append(it)
    get_db().set_setting(f"pending_manual:{server_id}", [m.to_dict() for m in cur])


def load_pending_manual(server_id: str) -> list[ManualItem]:
    data = get_db().get_setting(f"pending_manual:{server_id}", []) or []
    return [ManualItem(**d) for d in data]


def set_pending_manual(server_id: str, items: list[ManualItem]) -> None:
    get_db().set_setting(f"pending_manual:{server_id}", [m.to_dict() for m in items])
