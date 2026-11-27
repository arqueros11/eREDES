"""Información de red del equipo: interfaces, IPv4, máscara, gateway, DNS, MAC, tipo."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from app.core.platform_info import IS_MACOS, IS_WINDOWS
from app.core.shell import run, run_powershell


@dataclass
class NetInterface:
    name: str
    display: str = ""
    kind: str = "Otro"          # Ethernet | Wi-Fi | Virtual | Otro
    ipv4: str = ""
    netmask: str = ""
    prefix: int = 0
    extra_ipv4: list = field(default_factory=list)
    mac: str = ""
    is_up: bool = False
    speed_mbps: int = 0
    gateway: str = ""

    @property
    def network(self) -> str:
        if not self.ipv4 or not self.netmask:
            return ""
        return str(ipaddress.IPv4Network(f"{self.ipv4}/{self.netmask}", strict=False))


@dataclass
class NetworkSummary:
    interfaces: list[NetInterface]
    primary: NetInterface | None
    gateway: str
    dns: list[str]
    hostname: str


_VIRTUAL = re.compile(r"^(lo|utun|awdl|llw|gif|stf|anpi|ap\d|bridge|vmnet|vboxnet|docker|veth|tun|tap|"
                      r"Loopback|vEthernet|isatap|Teredo)", re.I)


def _mac_hw_ports() -> dict[str, str]:
    out = {}
    r = run(["networksetup", "-listallhardwareports"], timeout=10)
    port = None
    for line in r.stdout.splitlines():
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and port:
            out[line.split(":", 1)[1].strip()] = port
    return out


def _win_adapters() -> dict[str, dict]:
    r = run_powershell(
        "$l=@(Get-NetAdapter | Select-Object Name,InterfaceDescription,MediaType,PhysicalMediaType,Status,"
        "MacAddress,LinkSpeed,Virtual); ConvertTo-Json -InputObject $l -Compress", timeout=30)
    out = {}
    try:
        data = json.loads(r.stdout.strip() or "[]")
        if isinstance(data, dict):
            data = [data]
        for d in data:
            out[d.get("Name", "")] = d
    except ValueError:
        pass
    return out


def default_gateway() -> tuple[str, str]:
    """Devuelve (gateway, interfaz)."""
    if IS_MACOS:
        r = run(["route", "-n", "get", "default"], timeout=5)
        gw = re.search(r"gateway:\s*(\S+)", r.stdout)
        iface = re.search(r"interface:\s*(\S+)", r.stdout)
        return (gw.group(1) if gw else "", iface.group(1) if iface else "")
    if IS_WINDOWS:
        r = run_powershell(
            "$r=Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | "
            "Sort-Object {$_.RouteMetric + $_.InterfaceMetric} | Select-Object -First 1;"
            "if($r){\"$($r.NextHop)|$($r.InterfaceAlias)\"}", timeout=20)
        line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        if "|" in line:
            gw, iface = line.split("|", 1)
            return gw.strip(), iface.strip()
        return "", ""
    r = run(["ip", "route", "show", "default"], timeout=5)
    m = re.search(r"default via (\S+) dev (\S+)", r.stdout)
    return (m.group(1), m.group(2)) if m else ("", "")


def dns_servers() -> list[str]:
    servers: list[str] = []
    if IS_MACOS:
        r = run(["scutil", "--dns"], timeout=5)
        for m in re.finditer(r"nameserver\[\d+\]\s*:\s*(\S+)", r.stdout):
            if m.group(1) not in servers:
                servers.append(m.group(1))
    elif IS_WINDOWS:
        r = run_powershell(
            "(Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object {$_.ServerAddresses} | "
            "Select-Object -ExpandProperty ServerAddresses) -join ','", timeout=20)
        for s in r.stdout.strip().split(","):
            s = s.strip()
            if s and s not in servers:
                servers.append(s)
    else:
        try:
            for line in Path("/etc/resolv.conf").read_text().splitlines():
                if line.startswith("nameserver"):
                    servers.append(line.split()[1])
        except OSError:
            pass
    return servers


def get_interfaces(include_virtual: bool = False) -> list[NetInterface]:
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    hw = _mac_hw_ports() if IS_MACOS else {}
    win = _win_adapters() if IS_WINDOWS else {}
    out = []
    for name, alist in addrs.items():
        st = stats.get(name)
        ni = NetInterface(name=name, is_up=bool(st and st.isup), speed_mbps=(st.speed if st else 0))
        for a in alist:
            if a.family == socket.AF_INET:
                if not ni.ipv4:
                    ni.ipv4, ni.netmask = a.address, a.netmask or ""
                else:
                    ni.extra_ipv4.append((a.address, a.netmask or ""))
            elif a.family == psutil.AF_LINK:
                ni.mac = (a.address or "").replace("-", ":").upper()
        if ni.netmask:
            try:
                ni.prefix = ipaddress.IPv4Network(f"0.0.0.0/{ni.netmask}").prefixlen
            except ValueError:
                pass
        if IS_MACOS:
            port = hw.get(name, "")
            ni.display = (port if name in port else f"{port} ({name})") if port else name
            pl = port.lower()
            ni.kind = "Wi-Fi" if "wi-fi" in pl or "airport" in pl else (
                "Ethernet" if ("ethernet" in pl or "lan" in pl or "usb" in pl) else (
                    "Virtual" if (not port or "bridge" in pl or "thunderbolt" in pl) else "Otro"))
        elif IS_WINDOWS:
            d = win.get(name, {})
            ni.display = f"{name} — {d.get('InterfaceDescription', '')}".strip(" —")
            media = f"{d.get('MediaType', '')} {d.get('PhysicalMediaType', '')}".lower()
            ni.kind = "Wi-Fi" if ("802.11" in media or "wireless" in media) else (
                "Virtual" if d.get("Virtual") else ("Ethernet" if "802.3" in media else "Otro"))
        else:
            ni.display = name
            ni.kind = "Wi-Fi" if name.startswith("wl") else ("Ethernet" if name.startswith(("en", "eth")) else "Otro")
        if _VIRTUAL.match(name) and ni.kind not in ("Wi-Fi", "Ethernet"):
            ni.kind = "Virtual"
        if not include_virtual and (ni.kind == "Virtual" or name.startswith("lo")):
            continue
        out.append(ni)
    out.sort(key=lambda i: (not i.is_up, not i.ipv4, i.kind != "Ethernet", i.name))
    return out


def network_summary() -> NetworkSummary:
    ifaces = get_interfaces()
    gw, gw_if = default_gateway()
    primary = None
    for i in ifaces:
        if i.name == gw_if or (IS_WINDOWS and gw_if and i.name == gw_if):
            primary = i
            i.gateway = gw
    if primary is None:
        primary = next((i for i in ifaces if i.ipv4 and i.is_up), None)
    return NetworkSummary(interfaces=ifaces, primary=primary, gateway=gw, dns=dns_servers(),
                          hostname=socket.gethostname())


def local_ipv4s() -> set[str]:
    out = set()
    for alist in psutil.net_if_addrs().values():
        for a in alist:
            if a.family == socket.AF_INET:
                out.add(a.address)
    return out


def interface_for_ip(ip: str) -> NetInterface | None:
    """Interfaz local cuya red contiene a `ip` (None si está en otra subred)."""
    target = ipaddress.IPv4Address(ip)
    for i in get_interfaces(include_virtual=True):
        for addr, mask in [(i.ipv4, i.netmask)] + list(i.extra_ipv4):
            if addr and mask:
                try:
                    if target in ipaddress.IPv4Network(f"{addr}/{mask}", strict=False):
                        return i
                except ValueError:
                    continue
    return None
