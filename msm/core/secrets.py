"""APIキーなどの秘密情報を Windows DPAPI (CurrentUser) で暗号化して保存する.

ソースコードにキーを直書きしない。暗号化したデータは同じWindowsユーザーでのみ復号できる。
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import os


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _out(blob: _DATA_BLOB) -> bytes:
    data = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)
    return data


def protect(text: str) -> str:
    if not text:
        return ""
    if os.name != "nt":
        raise RuntimeError("DPAPIはWindowsでのみ利用できます")
    src = _blob(text.encode("utf-8"))
    dst = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(src), "MSM", None, None, None, 0x01, ctypes.byref(dst)
    ):
        raise ctypes.WinError()
    return base64.b64encode(_out(dst)).decode("ascii")


def unprotect(value: str) -> str:
    if not value:
        return ""
    try:
        src = _blob(base64.b64decode(value))
        dst = _DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(src), None, None, None, None, 0x01, ctypes.byref(dst)
        ):
            return ""
        return _out(dst).decode("utf-8")
    except Exception:
        return ""
