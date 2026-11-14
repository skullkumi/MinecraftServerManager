"""UPnP (IGD) によるポート開放 (任意機能・初期設定はオフ).

- ユーザーが明示的に操作したときだけルーターへ問い合わせる
- 開放したポートはDBに記録し、サーバー停止時・アプリ終了時に閉じられるようにする
- 期限付き (既定2時間) で開放し、稼働中は延長する。ルーターが期限付きに対応しない場合は無期限になる旨を伝える
"""
from __future__ import annotations

import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from msm.core.db import get_db
from msm.core.util import log

SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGETS = ["urn:schemas-upnp-org:device:InternetGatewayDevice:1",
                  "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
                  "urn:schemas-upnp-org:service:WANIPConnection:1"]
SERVICE_TYPES = ["urn:schemas-upnp-org:service:WANIPConnection:2", "urn:schemas-upnp-org:service:WANIPConnection:1",
                 "urn:schemas-upnp-org:service:WANPPPConnection:1"]
DESCRIPTION = "Minecraft Server Manager"
DEFAULT_LEASE = 7200


class UpnpError(Exception):
    pass


@dataclass
class Gateway:
    location: str
    control_url: str
    service_type: str
    friendly_name: str
    local_ip: str  # ルーターへ届くこのPCのアドレス


def discover(timeout: float = 3.0) -> list[str]:
    """SSDPでルーター (IGD) の説明文書URLを探す."""
    found: list[str] = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(0.5)
    try:
        for st in SEARCH_TARGETS:
            msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
                   f"MX: 2\r\nST: {st}\r\n\r\n").encode()
            s.sendto(msg, SSDP_ADDR)
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, _addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m:
                loc = m.group(1).decode(errors="replace")
                if loc not in found:
                    found.append(loc)
    finally:
        s.close()
    return found


def _local_ip_for(host: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 1900))
        return s.getsockname()[0]
    finally:
        s.close()


def gateway_from_location(location: str) -> Gateway | None:
    with urllib.request.urlopen(location, timeout=5) as r:
        xml = r.read()
    root = ET.fromstring(xml)
    ns = {"d": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}

    def find_all(el, tag):
        return el.iter(f"{{{ns['d']}}}{tag}") if ns else el.iter(tag)

    def text(el, tag):
        x = el.find(f"d:{tag}", ns) if ns else el.find(tag)
        return (x.text or "").strip() if x is not None else ""

    base = ""
    for b in find_all(root, "URLBase"):
        base = (b.text or "").strip()
    base = base or location
    name = next((text(d, "friendlyName") for d in find_all(root, "device")), "")
    for svc in find_all(root, "service"):
        st = text(svc, "serviceType")
        if st in SERVICE_TYPES:
            ctrl = urllib.parse.urljoin(base, text(svc, "controlURL"))
            host = urllib.parse.urlparse(ctrl).hostname or ""
            return Gateway(location, ctrl, st, name, _local_ip_for(host))
    return None


def find_gateway(timeout: float = 3.0) -> Gateway:
    locs = discover(timeout)
    if not locs:
        raise UpnpError("UPnPに対応したルーターが見つかりませんでした (ルーターの設定でUPnPが無効になっている可能性があります)")
    for loc in locs:
        try:
            gw = gateway_from_location(loc)
            if gw:
                return gw
        except Exception as e:  # noqa: BLE001
            log.warning("UPnP説明文書の取得に失敗: %s %s", loc, e)
    raise UpnpError("ルーターは見つかりましたが、ポート開放 (WANIPConnection) の機能がありませんでした")


def soap(gw: Gateway, action: str, args: dict, timeout: float = 6) -> dict:
    body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    envelope = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="{gw.service_type}">{body}</u:{action}></s:Body></s:Envelope>').encode()
    req = urllib.request.Request(gw.control_url, data=envelope, method="POST", headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{gw.service_type}#{action}"'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            xml = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        code = re.search(r"<errorCode>(\d+)</errorCode>", detail)
        desc = re.search(r"<errorDescription>([^<]*)</errorDescription>", detail)
        raise UpnpError(f"ルーターが拒否しました ({action}: {code.group(1) if code else e.code} "
                        f"{desc.group(1) if desc else ''})".strip()) from e
    out = {}
    for el in ET.fromstring(xml).iter():
        tag = el.tag.split("}")[-1]
        if tag.startswith("New"):
            out[tag] = (el.text or "").strip()
    return out


def external_ip(gw: Gateway) -> str:
    return soap(gw, "GetExternalIPAddress", {}).get("NewExternalIPAddress", "")


def add_mapping(gw: Gateway, port: int, lease: int = DEFAULT_LEASE) -> int:
    """TCPポートを開放する。実際に使われた期限 (秒・0は無期限) を返す."""
    args = {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP", "NewInternalPort": port,
            "NewInternalClient": gw.local_ip, "NewEnabled": 1, "NewPortMappingDescription": DESCRIPTION,
            "NewLeaseDuration": lease}
    try:
        soap(gw, "AddPortMapping", args)
    except UpnpError as e:
        if "725" in str(e) and lease:  # OnlyPermanentLeasesSupported
            args["NewLeaseDuration"] = 0
            soap(gw, "AddPortMapping", args)
            lease = 0
        else:
            raise
    _remember(port, gw, lease)
    return lease


def delete_mapping(gw: Gateway, port: int) -> None:
    try:
        soap(gw, "DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP"})
    finally:
        _forget(port)


def mapping_entry(gw: Gateway, port: int) -> dict | None:
    try:
        return soap(gw, "GetSpecificPortMappingEntry", {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP"})
    except UpnpError:
        return None


def _remember(port: int, gw: Gateway, lease: int) -> None:
    m = get_db().get_setting("upnp_mappings", {}) or {}
    m[str(port)] = {"location": gw.location, "local_ip": gw.local_ip, "lease": lease, "at": time.time()}
    get_db().set_setting("upnp_mappings", m)


def _forget(port: int) -> None:
    m = get_db().get_setting("upnp_mappings", {}) or {}
    m.pop(str(port), None)
    get_db().set_setting("upnp_mappings", m)


def remembered() -> dict:
    return get_db().get_setting("upnp_mappings", {}) or {}


def gateway_for_remembered(port: int) -> Gateway | None:
    info = remembered().get(str(port))
    if not info:
        return None
    try:
        return gateway_from_location(info["location"])
    except Exception:
        return None
