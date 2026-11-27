"""Sondas de red reales: ICMP (ping del sistema), TCP, ARP, DNS inverso, banners."""
from __future__ import annotations

import concurrent.futures as cf
import errno
import re
import socket
import statistics
import time
from dataclasses import dataclass

from app.core.platform_info import IS_MACOS, IS_WINDOWS
from app.core.shell import run

from .oui import normalize_mac

_RTT = re.compile(r"(?:time|tiempo|zeit|temps|tempo)\s*[=<]\s*([\d.,]+)\s*ms", re.I)


@dataclass
class PingStats:
    sent: int
    received: int
    rtts: list[float]

    @property
    def loss_pct(self) -> float:
        return 100.0 * (self.sent - self.received) / self.sent if self.sent else 100.0

    @property
    def avg(self) -> float | None:
        return statistics.mean(self.rtts) if self.rtts else None

    @property
    def min(self) -> float | None:
        return min(self.rtts) if self.rtts else None

    @property
    def max(self) -> float | None:
        return max(self.rtts) if self.rtts else None

    @property
    def jitter(self) -> float | None:
        if len(self.rtts) < 2:
            return None
        return statistics.mean(abs(a - b) for a, b in zip(self.rtts[1:], self.rtts[:-1]))


def _ping_args(ip: str, count: int, timeout_ms: int, interval: float = 1.0) -> list[str]:
    if IS_WINDOWS:
        return ["ping", "-n", str(count), "-w", str(timeout_ms), ip]
    if IS_MACOS:
        args = ["ping", "-n", "-c", str(count), "-W", str(timeout_ms)]
        if count > 1:
            args += ["-i", str(max(0.1, interval))]
        return args + [ip]
    return ["ping", "-n", "-c", str(count), "-W", str(max(1, timeout_ms // 1000)), "-i",
            str(max(0.2, interval)), ip]


# ---------------------------------------------------------------- ICMP nativo (sin procesos externos)
def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    s = sum(int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def _echo(ident: int, seq: int) -> bytes:
    payload = b"eREDES-ping-0123456789abcdef"
    hdr = bytes([8, 0, 0, 0]) + ident.to_bytes(2, "big") + seq.to_bytes(2, "big")
    cs = _checksum(hdr + payload)
    return bytes([8, 0]) + cs.to_bytes(2, "big") + hdr[4:] + payload


def _sweep_socket(ips: list[str], timeout_ms: int, retries: int) -> dict[str, float] | None:
    """Ping a muchas IPs con UN socket ICMP no privilegiado (macOS/Linux). None si no está disponible."""
    import os
    import select
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except OSError:
        return None
    found: dict[str, float] = {}
    sent_at: dict[str, float] = {}
    ident = os.getpid() & 0xFFFF

    def drain():
        """Lee todas las respuestas disponibles sin esperar."""
        while True:
            try:
                data, addr = s.recvfrom(2048)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            t = time.perf_counter()
            if data and (data[0] >> 4) == 4:   # macOS incluye la cabecera IP
                data = data[(data[0] & 0x0F) * 4:]
            if data and data[0] == 0 and addr[0] in sent_at and addr[0] not in found:
                found[addr[0]] = (t - sent_at[addr[0]]) * 1000

    try:
        s.setblocking(False)
        for attempt in range(retries + 1):
            pending = [ip for ip in ips if ip not in found]
            if not pending:
                break
            for n, ip in enumerate(pending):
                try:
                    s.sendto(_echo(ident, (attempt * 4096 + n) & 0xFFFF), (ip, 0))
                    sent_at[ip] = time.perf_counter()
                except OSError:
                    pass
                if n % 16 == 15:          # ritmo moderado y lectura intercalada (RTT exacto)
                    drain()
                    time.sleep(0.001)
            deadline = time.perf_counter() + timeout_ms / 1000
            while time.perf_counter() < deadline:
                r, _, _ = select.select([s], [], [], max(0.0, deadline - time.perf_counter()))
                if not r:
                    break
                drain()
    finally:
        s.close()
    return found


class _ICMP_ECHO_REPLY(__import__("ctypes").Structure):
    import ctypes as _c
    _fields_ = [("Address", _c.c_ulong), ("Status", _c.c_ulong), ("RoundTripTime", _c.c_ulong),
                ("DataSize", _c.c_ushort), ("Reserved", _c.c_ushort), ("Data", _c.c_void_p),
                ("Ttl", _c.c_ubyte), ("Tos", _c.c_ubyte), ("Flags", _c.c_ubyte), ("OptionsSize", _c.c_ubyte),
                ("OptionsData", _c.c_void_p)]


def _ping_windows_api(ip: str, timeout_ms: int) -> tuple[bool, float | None] | None:
    """Ping con IcmpSendEcho (iphlpapi.dll): sin administrador y sin lanzar procesos."""
    import ctypes
    try:
        api = ctypes.windll.iphlpapi  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None
    api.IcmpCreateFile.restype = ctypes.c_void_p
    h = api.IcmpCreateFile()
    if not h or h == ctypes.c_void_p(-1).value:
        return None
    try:
        data = b"eREDES-ping"
        size = ctypes.sizeof(_ICMP_ECHO_REPLY) + len(data) + 8
        buf = ctypes.create_string_buffer(size)
        addr = int.from_bytes(socket.inet_aton(ip), "little")
        api.IcmpSendEcho.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_ushort,
                                     ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        n = api.IcmpSendEcho(h, addr, data, len(data), None, buf, size, timeout_ms)
        if n == 0:
            return False, None
        rep = _ICMP_ECHO_REPLY.from_buffer_copy(buf.raw[:ctypes.sizeof(_ICMP_ECHO_REPLY)])
        if rep.Status != 0:
            return False, None
        return True, float(max(rep.RoundTripTime, 0.5))
    finally:
        api.IcmpCloseHandle(ctypes.c_void_p(h))


def ping_sweep(ips: list[str], timeout_ms: int = 800, retries: int = 1, workers: int = 64) -> dict[str, float]:
    """Ping a una lista de IPs. Devuelve {ip: rtt_ms} de las que responden."""
    res = None if IS_WINDOWS else _sweep_socket(ips, timeout_ms, retries)
    if res is not None:
        return res
    out: dict[str, float] = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for ip, (ok, rtt) in zip(ips, ex.map(lambda i: ping(i, timeout_ms), ips)):
            if ok:
                out[ip] = rtt or 0.0
    return out


def ping(ip: str, timeout_ms: int = 800) -> tuple[bool, float | None]:
    """Ping ICMP de un solo paquete. Devuelve (responde, rtt_ms).
    Usa ICMP nativo; si el sistema no lo permite, recurre al comando ping."""
    if IS_WINDOWS:
        r = _ping_windows_api(ip, timeout_ms)
        if r is not None:
            return r
    else:
        found = _sweep_socket([ip], timeout_ms, 0)
        if found is not None:
            return (ip in found), found.get(ip)
    return _ping_cmd(ip, timeout_ms)


def _ping_cmd(ip: str, timeout_ms: int = 800) -> tuple[bool, float | None]:
    r = run(_ping_args(ip, 1, timeout_ms), timeout=timeout_ms / 1000 + 3)
    out = r.stdout
    if IS_WINDOWS:
        # En Windows el código es 0 incluso con "host inaccesible": exigir 'TTL='
        alive = "TTL=" in out.upper()
    else:
        alive = r.returncode == 0
    m = _RTT.search(out)
    rtt = float(m.group(1).replace(",", ".")) if (alive and m) else None
    if alive and rtt is None and IS_WINDOWS and "<1ms" in out.replace(" ", ""):
        rtt = 0.5
    return alive, rtt


def ping_stats(ip: str, count: int = 10, timeout_ms: int = 1000, interval: float = 0.2) -> PingStats:
    r = run(_ping_args(ip, count, timeout_ms, interval), timeout=count * (interval + timeout_ms / 1000) + 10)
    rtts = []
    for line in r.stdout.splitlines():
        if IS_WINDOWS and "TTL=" not in line.upper():
            continue
        m = _RTT.search(line)
        if m:
            rtts.append(float(m.group(1).replace(",", ".")))
        elif IS_WINDOWS and "<1ms" in line.replace(" ", ""):
            rtts.append(0.5)
    return PingStats(sent=count, received=len(rtts), rtts=rtts)


def tcp_probe(ip: str, port: int, timeout: float = 0.6) -> tuple[str, float | None]:
    """Devuelve ('open'|'closed'|'filtered', rtt_ms). 'closed' implica que el host existe."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    t0 = time.perf_counter()
    try:
        rc = s.connect_ex((ip, port))
        rtt = (time.perf_counter() - t0) * 1000
        if rc == 0:
            return "open", rtt
        if rc in (errno.ECONNREFUSED, 10061):
            return "closed", rtt
        return "filtered", None
    except (socket.timeout, TimeoutError):
        return "filtered", None
    except OSError as e:
        if getattr(e, "errno", None) in (errno.ECONNREFUSED, 10061):
            return "closed", (time.perf_counter() - t0) * 1000
        return "filtered", None
    finally:
        s.close()


def tcp_open(ip: str, port: int, timeout: float = 1.5) -> bool:
    return tcp_probe(ip, port, timeout)[0] == "open"


_ARP_MAC = re.compile(r"\(?(\d+\.\d+\.\d+\.\d+)\)?\s+(?:at\s+)?([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})")


def arp_table() -> dict[str, str]:
    r = run(["arp", "-an"] if not IS_WINDOWS else ["arp", "-a"], timeout=10)
    table = {}
    for line in r.stdout.splitlines():
        m = _ARP_MAC.search(line)
        if m:
            mac = normalize_mac(m.group(2))
            if mac and mac not in ("FF:FF:FF:FF:FF:FF", "00:00:00:00:00:00") and not mac.startswith("01:00:5E"):
                table[m.group(1)] = mac
    return table


_dns_pool = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="rdns")


def reverse_dns(ip: str, timeout: float = 1.5) -> str:
    fut = _dns_pool.submit(socket.gethostbyaddr, ip)
    try:
        return fut.result(timeout=timeout)[0]
    except Exception:  # noqa: BLE001
        return ""


def http_banner(ip: str, port: int = 80, timeout: float = 2.0) -> dict:
    """Lee la cabecera Server y el <title> de la interfaz web (si existe)."""
    out = {}
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f"GET / HTTP/1.0\r\nHost: {ip}\r\nUser-Agent: eREDES\r\n\r\n".encode())
            data = b""
            while len(data) < 16384:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return out
    text = data.decode("latin-1", errors="replace")
    m = re.search(r"^Server:\s*(.+)$", text, re.M | re.I)
    if m:
        out["server"] = m.group(1).strip()[:80]
    m = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    if m:
        out["title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:80]
    m = re.search(r"^HTTP/\S+\s+(\d+)", text)
    if m:
        out["status"] = m.group(1)
    return out


SERVICE_NAMES = {
    9100: "RAW/JetDirect (impresión)", 9101: "RAW 9101", 9102: "RAW 9102", 515: "LPD (impresión)",
    631: "IPP (impresión)", 80: "HTTP", 443: "HTTPS", 8080: "HTTP alternativo", 22: "SSH", 23: "Telnet",
    139: "NetBIOS", 445: "SMB (archivos Windows)", 3389: "Escritorio remoto", 5900: "VNC", 161: "SNMP",
    21: "FTP", 53: "DNS", 8000: "HTTP 8000", 8443: "HTTPS alternativo",
}
