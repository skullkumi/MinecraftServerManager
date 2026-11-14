"""UPnPのSOAP処理を模擬ルーター (ローカルHTTPサーバー) で確認する."""
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MSM_DATA_DIR", tempfile.mkdtemp(prefix="msm_unit_"))

ST = "urn:schemas-upnp-org:service:WANIPConnection:1"
DESC = f"""<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><friendlyName>Fake Router</friendlyName>
<deviceList><device><deviceList><device><serviceList><service><serviceType>{ST}</serviceType>
<controlURL>/ctl/IPConn</controlURL></service></serviceList></device></deviceList></device></deviceList></device></root>"""


class FakeIGD(BaseHTTPRequestHandler):
    mappings: dict = {}
    permanent_only = False

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.end_headers()
        self.wfile.write(DESC.encode())

    def _reply(self, action, inner="", code=200):
        body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                f'<u:{action}Response xmlns:u="{ST}">{inner}</u:{action}Response></s:Body></s:Envelope>')
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body.encode())

    def _fault(self, code, desc):
        body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault>'
                f'<detail><UPnPError><errorCode>{code}</errorCode><errorDescription>{desc}</errorDescription></UPnPError>'
                f'</detail></s:Fault></s:Body></s:Envelope>')
        self.send_response(500)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self):
        action = self.headers["SOAPAction"].strip('"').split("#")[1]
        data = self.rfile.read(int(self.headers["Content-Length"])).decode()
        args = dict(re.findall(r"<(New\w+)>([^<]*)</New\w+>", data))
        if action == "GetExternalIPAddress":
            return self._reply(action, "<NewExternalIPAddress>203.0.113.5</NewExternalIPAddress>")
        if action == "AddPortMapping":
            if FakeIGD.permanent_only and args["NewLeaseDuration"] != "0":
                return self._fault(725, "OnlyPermanentLeasesSupported")
            FakeIGD.mappings[args["NewExternalPort"]] = args
            return self._reply(action)
        if action == "DeletePortMapping":
            FakeIGD.mappings.pop(args["NewExternalPort"], None)
            return self._reply(action)
        if action == "GetSpecificPortMappingEntry":
            m = FakeIGD.mappings.get(args["NewExternalPort"])
            if not m:
                return self._fault(714, "NoSuchEntryInArray")
            return self._reply(action, f"<NewInternalClient>{m['NewInternalClient']}</NewInternalClient>")
        self._fault(401, "Invalid Action")


def test_upnp_flow():
    from msm.publish import upnp

    srv = HTTPServer(("127.0.0.1", 0), FakeIGD)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        gw = upnp.gateway_from_location(f"http://127.0.0.1:{srv.server_port}/desc.xml")
        assert gw and gw.friendly_name == "Fake Router" and gw.control_url.endswith("/ctl/IPConn")
        assert upnp.external_ip(gw) == "203.0.113.5"
        assert upnp.add_mapping(gw, 25565) == upnp.DEFAULT_LEASE
        m = FakeIGD.mappings["25565"]
        assert m["NewInternalClient"] == "127.0.0.1" and m["NewProtocol"] == "TCP" and m["NewPortMappingDescription"]
        assert upnp.mapping_entry(gw, 25565)
        assert "25565" in upnp.remembered()
        upnp.delete_mapping(gw, 25565)
        assert "25565" not in FakeIGD.mappings and "25565" not in upnp.remembered()
        assert upnp.mapping_entry(gw, 25565) is None
        FakeIGD.permanent_only = True
        assert upnp.add_mapping(gw, 25566) == 0  # 期限付きに対応しないルーターは無期限で開放
        upnp.delete_mapping(gw, 25566)
    finally:
        srv.shutdown()
