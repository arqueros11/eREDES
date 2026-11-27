"""Descubrimiento mDNS/Bonjour de impresoras (RFC 6762/6763), sin dependencias.

Las respuestas mDNS son multicast de enlace local (224.0.0.251), por lo que se reciben
aunque la impresora esté configurada en OTRA subred del mismo cable/switch.
"""
from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass, field

MDNS_ADDR = ("224.0.0.251", 5353)
PRINTER_SERVICES = ["_pdl-datastream._tcp.local", "_printer._tcp.local", "_ipp._tcp.local",
                    "_ipps._tcp.local", "_http._tcp.local"]


@dataclass
class MdnsDevice:
    ip: str
    names: set = field(default_factory=set)
    services: set = field(default_factory=set)
    txt: dict = field(default_factory=dict)
    ports: set = field(default_factory=set)


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.strip(".").split("."):
        b = label.encode()
        out += bytes([len(b)]) + b
    return out + b"\x00"


def build_query(services: list[str]) -> bytes:
    header = struct.pack("!HHHHHH", 0, 0, len(services), 0, 0, 0)
    qs = b"".join(_encode_name(s) + struct.pack("!HH", 12, 1) for s in services)  # PTR, IN
    return header + qs


def _read_name(data: bytes, i: int, depth: int = 0) -> tuple[str, int]:
    labels = []
    jumped_end = None
    while True:
        if i >= len(data) or depth > 20:
            raise ValueError("nombre DNS inválido")
        ln = data[i]
        if ln == 0:
            i += 1
            break
        if ln & 0xC0 == 0xC0:
            ptr = ((ln & 0x3F) << 8) | data[i + 1]
            if jumped_end is None:
                jumped_end = i + 2
            name, _ = _read_name(data, ptr, depth + 1)
            labels.append(name)
            i = jumped_end
            return ".".join(x for x in labels if x), i
        labels.append(data[i + 1:i + 1 + ln].decode("utf-8", errors="replace"))
        i += 1 + ln
    return ".".join(labels), (jumped_end if jumped_end is not None else i)


def parse_packet(data: bytes) -> list[tuple[str, int, bytes, str]]:
    """Devuelve [(nombre, tipo, rdata, rdata_decodificada)] de respuestas/adicionales."""
    if len(data) < 12:
        return []
    _id, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
    if not flags & 0x8000:
        return []
    i = 12
    for _ in range(qd):
        _, i = _read_name(data, i)
        i += 4
    out = []
    for _ in range(an + ns + ar):
        name, i = _read_name(data, i)
        rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", data[i:i + 10])
        i += 10
        rdata = data[i:i + rdlen]
        dec = ""
        try:
            if rtype == 1 and rdlen == 4:
                dec = socket.inet_ntoa(rdata)
            elif rtype in (12, 5):
                dec, _ = _read_name(data, i)
            elif rtype == 33:
                port = struct.unpack("!H", rdata[4:6])[0]
                target, _ = _read_name(data, i + 6)
                dec = f"{target}:{port}"
            elif rtype == 16:
                parts, j = [], 0
                while j < len(rdata):
                    ln = rdata[j]
                    parts.append(rdata[j + 1:j + 1 + ln].decode("utf-8", errors="replace"))
                    j += 1 + ln
                dec = "\x1f".join(parts)
        except (ValueError, struct.error, IndexError):
            dec = ""
        out.append((name, rtype, rdata, dec))
        i += rdlen
    return out


def _default_local_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no envía paquetes: solo resuelve la ruta
        return s.getsockname()[0]
    except OSError:
        import psutil
        for alist in psutil.net_if_addrs().values():
            for a in alist:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    return a.address
        return None
    finally:
        s.close()


def discover(timeout: float = 3.0, services: list[str] | None = None,
             interface_ip: str | None = None) -> list[MdnsDevice]:
    services = services or PRINTER_SERVICES
    interface_ip = interface_ip or _default_local_ip()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass

    try:
        sock.bind(("", 5353))
        mreq = socket.inet_aton(MDNS_ADDR[0]) + socket.inet_aton(interface_ip or "0.0.0.0")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError:
        sock.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.bind(("", 0))

    if interface_ip:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
        except OSError:
            pass
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.settimeout(0.3)
    devices: dict[str, MdnsDevice] = {}
    host_ip: dict[str, str] = {}
    inst_host: dict[str, tuple[str, int]] = {}
    inst_txt: dict[str, dict] = {}
    inst_service: dict[str, str] = {}
    q = build_query(services)
    try:
        try:
            sock.sendto(q, MDNS_ADDR)
        except OSError as e:
            sock.close()
            raise OSError(f"No se pudo enviar la consulta mDNS por {interface_ip}: {e}")
        deadline = time.time() + timeout
        resent = False
        while time.time() < deadline:
            if not resent and time.time() > deadline - timeout / 2:
                try:
                    sock.sendto(q, MDNS_ADDR)
                except OSError:
                    pass
                resent = True
            try:
                data, addr = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                records = parse_packet(data)
            except (ValueError, struct.error, IndexError):
                continue
            if not records:
                continue
            src = addr[0]
            for name, rtype, _raw, dec in records:
                if rtype == 1 and dec:
                    host_ip[name.lower()] = dec
                elif rtype == 12 and dec:
                    for s in services:
                        if name.lower() == s.lower():
                            inst_service[dec.lower()] = s
                elif rtype == 33 and dec:
                    tgt, port = dec.rsplit(":", 1)
                    inst_host[name.lower()] = (tgt.lower(), int(port))
                elif rtype == 16 and dec:
                    kv = {}
                    for item in dec.split("\x1f"):
                        if "=" in item:
                            k, v = item.split("=", 1)
                            kv[k.lower()] = v
                    inst_txt[name.lower()] = kv
            ip_for_src = devices.setdefault(src, MdnsDevice(ip=src))
            for name, rtype, _raw, _dec in records:
                if rtype in (33, 16) and not name.startswith("_"):
                    ip_for_src.names.add(name.split("._")[0])
    finally:
        sock.close()
    # consolidar: instancia -> host -> IP (la IP anunciada puede diferir del origen)
    for inst, (host, port) in inst_host.items():
        ip = host_ip.get(host)
        if not ip:
            continue
        d = devices.setdefault(ip, MdnsDevice(ip=ip))
        d.names.add(inst.split("._")[0])
        d.ports.add(port)
        svc = inst_service.get(inst) or next((s for s in services if inst.endswith(s.lower())), "")
        if svc:
            d.services.add(svc.replace(".local", ""))
        d.txt.update(inst_txt.get(inst, {}))
    for ip, d in list(devices.items()):
        if not d.services and not d.txt:
            del devices[ip]
    return list(devices.values())
