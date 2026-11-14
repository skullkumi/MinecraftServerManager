"""CurseForge API (任意機能)。APIキーはユーザーが入力し、DPAPIで暗号化して保存する.

キーが無い場合でもMODパックの解析・overridesの展開・手動ダウンロード補助は利用できる。
"""
from __future__ import annotations

from msm.core import net
from msm.core.db import get_db
from msm.core.secrets import protect, unprotect

API = "https://api.curseforge.com/v1"
CLASS_MODS = 6
CLASS_LABELS = {6: "MOD", 12: "リソースパック", 6552: "シェーダー", 4471: "MODパック", 6945: "データパック",
                17: "ワールド", 4546: "カスタマイズ"}


def get_api_key() -> str:
    return unprotect(get_db().get_setting("curseforge_api_key_protected", "") or "")


def set_api_key(key: str) -> None:
    get_db().set_setting("curseforge_api_key_protected", protect(key.strip()) if key.strip() else "")


def has_api_key() -> bool:
    return bool(get_api_key())


def _headers() -> dict:
    key = get_api_key()
    if not key:
        raise RuntimeError("CurseForge APIキーが設定されていません")
    return {"x-api-key": key}


def get_files(file_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for i in range(0, len(file_ids), 500):
        res = net.post_json(f"{API}/mods/files", {"fileIds": file_ids[i:i + 500]}, _headers())
        for f in res.get("data", []):
            out[int(f["id"])] = f
    return out


def get_mods(mod_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for i in range(0, len(mod_ids), 500):
        res = net.post_json(f"{API}/mods", {"modIds": mod_ids[i:i + 500], "filterPcOnly": True}, _headers())
        for m in res.get("data", []):
            out[int(m["id"])] = m
    return out


def file_sha1(f: dict) -> str:
    for h in f.get("hashes", []) or []:
        if h.get("algo") == 1:
            return h.get("value", "")
    return ""


def test_key() -> tuple[bool, str]:
    try:
        net.get_json(f"{API}/games/432", _headers())
        return True, "APIキーは有効です"
    except Exception as e:  # noqa: BLE001
        return False, f"APIキーを確認できませんでした: {e}"


def file_page(mod: dict | None, project_id: int, file_id: int) -> str:
    if mod and (mod.get("links") or {}).get("websiteUrl"):
        return f"{mod['links']['websiteUrl'].rstrip('/')}/files/{file_id}"
    return f"https://www.curseforge.com/projects/{project_id}"
