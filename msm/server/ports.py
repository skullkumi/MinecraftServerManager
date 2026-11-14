"""ポートの使用状況確認."""
from __future__ import annotations

import socket


def is_port_free(port: int, host: str = "0.0.0.0") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def is_listening(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def port_owner(port: int) -> str:
    """ポートを使用中のプロセス名 (分かる場合)."""
    try:
        import psutil

        for c in psutil.net_connections(kind="tcp"):
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
                try:
                    return f"{psutil.Process(c.pid).name()} (PID {c.pid})"
                except Exception:
                    return f"PID {c.pid}"
    except Exception:
        pass
    return ""


def local_ipv4_addresses() -> list[str]:
    out: list[str] = []
    try:
        import psutil

        for name, addrs in psutil.net_if_addrs().items():
            stats = psutil.net_if_stats().get(name)
            if stats and not stats.isup:
                continue
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    out.append(a.address)
    except Exception:
        pass
    if not out:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.0.2.1", 80))
            out.append(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    # プライベートアドレスを優先
    out.sort(key=lambda a: (0 if a.startswith(("192.168.", "10.", "172.")) else 1, a))
    return out
