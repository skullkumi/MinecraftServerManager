"""HTTP通信 (標準ライブラリのみ)。進捗通知・ハッシュ検証・キャンセル対応."""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from msm import USER_AGENT
from msm.core.util import CancelToken, Cancelled

ProgressFn = Callable[[str, float | None], None]


class NetError(Exception):
    pass


def _ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def _request(url: str, headers: dict | None = None, data: bytes | None = None, method: str | None = None):
    h = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        h.update(headers)
    return urllib.request.Request(url, data=data, headers=h, method=method)


def get_bytes(url: str, headers: dict | None = None, timeout: float = 30, retries: int = 2) -> bytes:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(_request(url, headers), timeout=timeout, context=_ctx()) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404):
                raise NetError(f"HTTP {e.code}: {url}") from e
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1.0 + attempt)
    raise NetError(f"通信に失敗しました: {url} ({last})")


def get_json(url: str, headers: dict | None = None, timeout: float = 30) -> Any:
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    return json.loads(get_bytes(url, h, timeout).decode("utf-8"))


def post_json(url: str, payload: Any, headers: dict | None = None, timeout: float = 30) -> Any:
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = _request(url, h, json.dumps(payload).encode("utf-8"), "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise NetError(f"HTTP {e.code}: {url}") from e
    except Exception as e:  # noqa: BLE001
        raise NetError(f"通信に失敗しました: {url} ({e})") from e


def file_hash(path: Path, algo: str = "sha1") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(
    url: str,
    dest: Path,
    *,
    expected_hash: tuple[str, str] | None = None,
    headers: dict | None = None,
    progress: ProgressFn | None = None,
    cancel: CancelToken | None = None,
    label: str = "",
    timeout: float = 60,
) -> Path:
    """URLをdestへダウンロードする。一時ファイルに書いてから検証後にリネームする.

    expected_hash = ("sha1"|"sha256"|"sha512", hex)
    既存ファイルは上書きしない (呼び出し側で事前に確認すること)。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".msmdownload")
    hasher = hashlib.new(expected_hash[0]) if expected_hash else None
    label = label or dest.name
    try:
        with urllib.request.urlopen(_request(url, headers), timeout=timeout, context=_ctx()) as r:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            last_report = 0.0
            with open(tmp, "wb") as f:
                while True:
                    if cancel and cancel.cancelled:
                        raise Cancelled()
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    if hasher:
                        hasher.update(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if progress and now - last_report > 0.2:
                        last_report = now
                        frac = done / total if total else None
                        progress(f"ダウンロード中: {label} ({done // 1024 // 1024}MB)", frac)
        if hasher and hasher.hexdigest().lower() != expected_hash[1].lower():
            raise NetError(f"ハッシュが一致しません ({label})。ファイルが破損している可能性があります。")
        if dest.exists():
            raise NetError(f"保存先に同名ファイルが既に存在します: {dest}")
        os.replace(tmp, dest)
        return dest
    except urllib.error.HTTPError as e:
        raise NetError(f"ダウンロードに失敗しました HTTP {e.code}: {url}") from e
    except (Cancelled, NetError):
        raise
    except Exception as e:  # noqa: BLE001
        raise NetError(f"ダウンロードに失敗しました: {label} ({e})") from e
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def quote(s: str) -> str:
    return urllib.parse.quote(s, safe="")
