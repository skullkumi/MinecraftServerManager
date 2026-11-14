"""MODのサーバー対応判定 (サーバー専用 / クライアント専用 / 両対応 / 判定不能).

ファイル名だけでは判定しない。メタデータ・配布元 (Modrinth) の情報・既知情報・過去ログを組み合わせる。
確信度「確定」のクライアント専用MODだけを自動でサーバー導入対象外にし、それ以外はユーザーに確認する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from msm.core.db import get_db
from msm.mods.metadata import ModMeta

SERVER, CLIENT, BOTH, UNKNOWN = "server", "client", "both", "unknown"
SIDE_LABELS = {SERVER: "サーバー専用", CLIENT: "クライアント専用", BOTH: "両対応", UNKNOWN: "判定不能"}
CONF_ORDER = {"確定": 3, "高": 2, "中": 1, "不明": 0}

# クライアント専用であることが広く知られているMOD (MOD ID)。
# メタデータに宣言が無いForge系MODの補助情報として使う (確信度「高」止まり)。
KNOWN_CLIENT_ONLY = {
    "oculus", "embeddium", "rubidium", "magnesium", "optifine", "sodium", "iris", "sodium_extra", "sodiumextra",
    "reeses_sodium_options", "entityculling", "entity_model_features", "entity_texture_features",
    "notenoughanimations", "betterf3", "legendarytooltips", "toastcontrol", "fpsreducer", "skinlayers3d",
    "firstperson", "lambdynlights", "dynamiclightsreforged", "controlling", "mousetweaks", "catalogue",
    "betterpingdisplay", "chat_heads", "neat", "presencefootsteps", "sound_physics_remastered", "dynamic_fps",
    "dynamicfps", "continuity", "citresewn", "zoomify", "ok_zoomer", "okzoomer", "wi_zoom", "indium", "replaymod",
    "cull_less_leaves", "cullleaves", "itemborders", "blur", "fancymenu", "drippyloadingscreen", "torohealth",
    "betterthirdperson", "shoulder_surfing", "shouldersurfing", "smoothchunk", "eating_animation",
    "betteradvancements", "jeitweaker_client", "modmenu", "visuality", "particular", "fallingleaves",
    "immersive_ui", "enhancedblockentities", "moreculling", "lithium_client", "nvidium", "distanthorizons_client",
    "durabilitytooltip", "inventoryprofilesnext", "appleskin_client", "xaerominimapfair",
}


@dataclass
class Classification:
    side: str = UNKNOWN
    confidence: str = "不明"
    reasons: list[str] = field(default_factory=list)
    modrinth: dict | None = None

    @property
    def label(self) -> str:
        return SIDE_LABELS[self.side]

    @property
    def auto_exclude(self) -> bool:
        """確実にクライアント専用と判定できた場合のみ自動除外する."""
        return self.side == CLIENT and self.confidence == "確定"


def _set(c: Classification, side: str, conf: str, reason: str) -> None:
    if CONF_ORDER[conf] > CONF_ORDER[c.confidence] or c.side == UNKNOWN:
        c.side, c.confidence = side, conf
    c.reasons.append(reason)


def client_only_ids_from_logs() -> set[str]:
    return set(get_db().get_setting("client_only_from_logs", []) or [])


def remember_client_only_from_log(mod_ids: list[str]) -> None:
    cur = client_only_ids_from_logs()
    cur.update(mod_ids)
    get_db().set_setting("client_only_from_logs", sorted(cur))


def class_scan(meta: ModMeta):
    """クラス解析の結果 (SHA-1でキャッシュ)."""
    from msm.mods.classscan import ScanResult, scan

    if meta.sha1:
        cached = get_db().cache_get(meta.sha1, "classscan_v1", max_age=365 * 86400)
        if cached:
            return ScanResult(**cached)
    r = scan(Path(meta.file))
    if meta.sha1:
        get_db().cache_put(meta.sha1, "classscan_v1", r.to_dict())
    return r


def classify(meta: ModMeta, modrinth: dict | None = None, log_ids: set[str] | None = None,
             deep: bool = True) -> Classification:
    c = Classification(modrinth=modrinth)
    if not meta.valid_jar:
        c.reasons.append("JARとして読み込めないため判定できません")
        return c
    if meta.loader == "paper-plugin":
        _set(c, SERVER, "確定", "Paper/Bukkitプラグイン (plugin.yml) です")
        return c
    env = meta.environment
    if meta.loader in ("fabric", "quilt"):
        if env == "client":
            _set(c, CLIENT, "確定", f"{meta.loader}.mod.json で environment=client と宣言されています")
        elif env == "server":
            _set(c, SERVER, "確定", f"{meta.loader}.mod.json で environment=server と宣言されています")
        else:
            _set(c, BOTH, "中", "メタデータで両対応 (environment=*) と宣言されています")
    if meta.client_side_only_flag:
        _set(c, CLIENT, "確定", "mods.toml で clientSideOnly=true と宣言されています")
    if modrinth:
        cs, ss = modrinth.get("client_side"), modrinth.get("server_side")
        src = f"Modrinthの配布情報 (client_side={cs}, server_side={ss})"
        if ss == "unsupported" and cs in ("required", "optional"):
            _set(c, CLIENT, "高", src)
        elif cs == "unsupported" and ss in ("required", "optional"):
            _set(c, SERVER, "高", src)
        elif ss == "required" and cs == "required":
            _set(c, BOTH, "高", src)
        elif ss in ("required", "optional") and cs in ("required", "optional"):
            _set(c, BOTH, "中", src)
    ids = set(meta.mod_ids)
    known = ids & KNOWN_CLIENT_ONLY
    if known:
        _set(c, CLIENT, "高", f"クライアント専用として知られているMODです ({', '.join(sorted(known))})")
    log_hit = ids & (log_ids if log_ids is not None else client_only_ids_from_logs())
    if log_hit:
        _set(c, CLIENT, "高", "過去の起動ログで「サーバーでは読み込めない (invalid dist)」と記録されています")
    if meta.has_client_entrypoint_only and c.side in (UNKNOWN, BOTH) and c.confidence != "高":
        c.reasons.append("クライアント用の処理だけを持っています (クライアント専用の可能性)")
    if deep and meta.loader in ("forge", "neoforge") and c.confidence in ("不明", "中") and c.side != CLIENT:
        from msm.mods.classscan import combine

        try:
            side, conf, reasons = combine(class_scan(meta), meta.display_test)
        except Exception as e:  # noqa: BLE001
            side, conf, reasons = "", "不明", [f"クラス解析に失敗: {e}"]
        if side == "client" and conf in ("高", "中") and c.side != BOTH:
            _set(c, CLIENT, conf, "クラス解析: " + " / ".join(reasons[:3]))
        elif side == "server" and conf == "高":
            _set(c, SERVER, conf, "クラス解析: " + " / ".join(reasons[:2]))
        elif c.side == BOTH and side == "client":
            c.reasons.append("クラス解析ではクライアント寄りの特徴がありますが、他の情報と合わないため両対応としています")
    if c.side == UNKNOWN and meta.loader in ("forge", "neoforge"):
        c.reasons.append("Forge/NeoForge系MODはサーバー対応をメタデータで宣言しないことが多く、自動判定できません")
    # 矛盾チェック
    sides = {r for r in c.reasons}
    if c.side == CLIENT and any("両対応" in r for r in sides) and c.confidence != "確定":
        c.confidence = "中"
        c.reasons.append("情報源によって判定が異なるため確信度を下げました")
    return c
