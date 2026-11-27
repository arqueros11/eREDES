"""Escáner de red real: ICMP + TCP + ARP + DNS inverso + SNMP + identificación de servicios."""
from __future__ import annotations

import concurrent.futures as cf
import ipaddress
import logging
import time
from dataclasses import dataclass, field

from app.core import config
from app.database.db import get_db, now

from . import probes, snmp
from .interfaces import default_gateway, interface_for_ip, local_ipv4s
from .oui import vendor

log = logging.getLogger("eredes.scanner")

# Puertos que se prueban también en hosts que no responden al ping (firewall)
KEY_PORTS = [9100, 515, 631, 80, 443, 445, 22, 3389]

PRINTER_VENDORS = ("epson", "seiko", "xprinter", "bixolon", "star micronics", "citizen", "zebra", "rongta",
                   "hprt", "sewoo", "custom", "brother", "hewlett", "hp inc", "canon", "lexmark", "kyocera",
                   "ricoh", "xerox", "konica", "sharp", "oki", "toshiba tec", "sato", "honeywell", "tsc",
                   "gainscha", "winpos", "posiflex", "snbc", "cbx", "bienex", "pos-d", "beiyang", "jolimark",
                   "gprinter", "zhuhai", "printer")
PRINTER_WORDS = ("printer", "print", "jetdirect", "laserjet", "epson", "tm-", "pos", "receipt", "thermal", "zebra",
                 "bixolon", "xprinter", "label", "impresora")


@dataclass
class HostResult:
    ip: str
    alive: bool = False
    methods: list = field(default_factory=list)   # icmp, tcp, arp
    rtt_ms: float | None = None
    mac: str = ""
    vendor: str = ""
    hostname: str = ""
    open_ports: list = field(default_factory=list)
    closed_ports: list = field(default_factory=list)
    services: dict = field(default_factory=dict)
    snmp_descr: str = ""
    dev_type: str = ""
    status: str = ""
    is_self: bool = False

    @property
    def ports_text(self) -> str:
        return ", ".join(str(p) for p in sorted(self.open_ports))

    @property
    def looks_like_printer(self) -> bool:
        return self.dev_type.startswith("Impresora")


def classify(h: HostResult, gateway: str, printer_ports: list[int]) -> str:
    v = (h.vendor or "").lower()
    d = (h.snmp_descr or "").lower() + " " + " ".join(str(x).lower() for x in h.services.values())
    if h.is_self:
        return "Este equipo"
    if h.ip == gateway:
        return "Router / Gateway"
    pp = [p for p in h.open_ports if p in printer_ports]
    if 9100 in h.open_ports or (any(w in d for w in PRINTER_WORDS) and pp):
        return "Impresora (red RAW)" if 9100 in h.open_ports else "Impresora"
    if any(w in d for w in ("printer", "jetdirect", "laserjet", "impresora")):
        return "Impresora"
    if pp and any(pv in v for pv in PRINTER_VENDORS):
        return "Impresora"
    if pp:
        return "Posible impresora"
    if any(pv in v for pv in PRINTER_VENDORS[:20]):
        return "Posible impresora"
    if 3389 in h.open_ports or 445 in h.open_ports or 139 in h.open_ports:
        return "Computadora (Windows)"
    if 22 in h.open_ports and 445 not in h.open_ports:
        return "Equipo con SSH"
    if any(x in v for x in ("apple",)):
        return "Dispositivo Apple"
    if h.alive:
        return "Dispositivo"
    return ""


def _local_networks() -> list[ipaddress.IPv4Network]:
    import socket

    import psutil
    nets = []
    for alist in psutil.net_if_addrs().values():
        for a in alist:
            if a.family == socket.AF_INET and a.netmask and not a.address.startswith("127."):
                try:
                    nets.append(ipaddress.IPv4Network(f"{a.address}/{a.netmask}", strict=False))
                except ValueError:
                    pass
    return nets


def scan(ctx, targets: list[str], ports: list[int] | None = None, identify: bool = True) -> dict:
    """Escanea `targets`. Emite resultados parciales con ctx.emit(HostResult).

    `ctx` es un TaskContext (o cualquier objeto con progress/emit/cancelled).
    """
    t0 = time.time()
    ports = ports or list(config.get("scan_ports"))
    printer_ports = list(config.get("printer_ports"))
    ping_to = int(config.get("ping_timeout_ms"))
    tcp_to = int(config.get("tcp_timeout_ms")) / 1000
    workers = int(config.get("scan_workers"))
    community = config.get("snmp_community") or "public"
    gateway, _ = default_gateway()
    selfs = local_ipv4s()
    tcp_ports = [p for p in ports if p != 161]
    want_snmp = 161 in ports
    results = {ip: HostResult(ip=ip, is_self=ip in selfs) for ip in targets}
    total = len(targets)

    # Fase 1: ICMP (socket nativo: un solo socket para toda la red)
    ctx.progress(2, f"Ping ICMP a {total} direcciones…")
    alive_map = probes.ping_sweep(targets, ping_to, retries=1, workers=workers)
    for ip, rtt in alive_map.items():
        if ip in results:
            results[ip].alive = True
            results[ip].methods.append("ICMP")
            results[ip].rtt_ms = rtt
    ctx.progress(35, f"{len(alive_map)} responden al ping")
    if ctx.cancelled:
        return _finish(results, targets, ports, t0, cancelled=True)

    # Fase 2: ARP (solo efectivo en la subred local)
    arp = probes.arp_table()
    local_nets = _local_networks()
    for ip, h in results.items():
        if ip in arp:
            h.mac = arp[ip]
            if not h.alive and any(ipaddress.IPv4Address(ip) in n for n in local_nets):
                h.alive = True
                h.methods.append("ARP")

    # Fase 3: TCP
    jobs = []
    for ip, h in results.items():
        plist = tcp_ports if h.alive else [p for p in tcp_ports if p in KEY_PORTS]
        jobs.extend((ip, p) for p in plist)
    ctx.progress(36, f"Comprobando {len(jobs)} puertos TCP…")
    done = 0
    with cf.ThreadPoolExecutor(max_workers=min(160, workers * 2)) as ex:
        futs = {ex.submit(probes.tcp_probe, ip, p, tcp_to): (ip, p) for ip, p in jobs}
        for f in cf.as_completed(futs):
            if ctx.cancelled:
                ex.shutdown(cancel_futures=True)
                break
            ip, p = futs[f]
            try:
                state, rtt = f.result()
            except Exception:  # noqa: BLE001
                state, rtt = "filtered", None
            h = results[ip]
            if state in ("open", "closed"):
                if not h.alive:
                    h.alive = True
                if "TCP" not in h.methods:
                    h.methods.append("TCP")
                if h.rtt_ms is None and rtt is not None:
                    h.rtt_ms = rtt
                (h.open_ports if state == "open" else h.closed_ports).append(p)
            done += 1
            if done % 40 == 0:
                ctx.progress(36 + int(done * 34 / max(1, len(jobs))), f"TCP {done}/{len(jobs)}")
    if ctx.cancelled:
        return _finish(results, targets, ports, t0, cancelled=True)

    # ARP de nuevo: los intentos TCP llenan la caché ARP
    arp = probes.arp_table()
    alive_hosts = [h for h in results.values() if h.alive]
    for h in alive_hosts:
        if not h.mac and h.ip in arp:
            h.mac = arp[h.ip]
        if h.is_self:
            from .interfaces import get_interfaces
            for i in get_interfaces(True):
                if i.ipv4 == h.ip:
                    h.mac = i.mac
        h.vendor = vendor(h.mac) if h.mac else ""

    # Fase 4: identificación
    if identify and alive_hosts:
        ctx.progress(72, f"Identificando {len(alive_hosts)} equipos…")

        def ident(h: HostResult):
            h.hostname = probes.reverse_dns(h.ip)
            for p in h.open_ports:
                h.services[p] = probes.SERVICE_NAMES.get(p, f"TCP {p}")
            for p in (80, 8080):
                if p in h.open_ports:
                    b = probes.http_banner(h.ip, p)
                    if b:
                        desc = " · ".join(x for x in (b.get("title"), b.get("server")) if x)
                        if desc:
                            h.services[p] = f"Web: {desc}"
            if want_snmp:
                try:
                    v = snmp.snmp_get(h.ip, [snmp.OID_SYS_DESCR, snmp.OID_SYS_NAME], community, timeout=1.0,
                                      retries=0)
                    h.snmp_descr = snmp.as_text(v.get(snmp.OID_SYS_DESCR))
                    sysname = snmp.as_text(v.get(snmp.OID_SYS_NAME))
                    if h.snmp_descr:
                        h.services[161] = f"SNMP: {h.snmp_descr[:70]}"
                    if sysname and not h.hostname:
                        h.hostname = sysname
                except snmp.SnmpError:
                    pass
            return h

        done = 0
        with cf.ThreadPoolExecutor(max_workers=32) as ex:
            for _ in ex.map(ident, alive_hosts):
                done += 1
                ctx.progress(72 + int(done * 26 / len(alive_hosts)), f"Identificados {done}/{len(alive_hosts)}")
                if ctx.cancelled:
                    break

    # Estado de cada IP del rango. "Libre" solo si está en la red local y no respondió a
    # ICMP, TCP ni ARP; fuera de la red local no se puede confirmar ("Sin respuesta").
    reserved = {r["ip"]: r["name"] for r in get_db().query("SELECT ip, name FROM printers")}
    for h in results.values():
        h.dev_type = classify(h, gateway, printer_ports)
        if h.alive:
            h.status = "En uso"
            ctx.emit(h)
        elif h.ip in reserved:
            h.status = "Reservada"
            h.dev_type = f"Impresora {reserved[h.ip]} (apagada o desconectada)"
        elif h.ip == gateway:
            h.status = "En uso"
            h.dev_type = "Router / Gateway"
        elif any(ipaddress.IPv4Address(h.ip) in n for n in local_nets):
            h.status = "Libre"
        else:
            h.status = "Sin respuesta"
    return _finish(results, targets, ports, t0, cancelled=ctx.cancelled)


def _finish(results: dict, targets: list[str], ports: list[int], t0: float, cancelled: bool) -> dict:
    alive = sorted((h for h in results.values() if h.alive), key=lambda h: ipaddress.IPv4Address(h.ip))
    dur = time.time() - t0
    target_txt = targets[0] if len(targets) == 1 else f"{targets[0]} – {targets[-1]} ({len(targets)})"
    scan_id = None
    if not cancelled:
        db = get_db()
        cur = db.execute("INSERT INTO scans(ts,target,ports,hosts_alive,duration) VALUES(?,?,?,?,?)",
                         (now(), target_txt, ",".join(map(str, ports)), len(alive), dur))
        scan_id = cur.lastrowid
        for h in alive:
            db.execute("INSERT INTO scan_results(scan_id,ip,mac,hostname,vendor,ports,status,dev_type,services)"
                       " VALUES(?,?,?,?,?,?,?,?,?)",
                       (scan_id, h.ip, h.mac, h.hostname, h.vendor, h.ports_text, h.status, h.dev_type,
                        "; ".join(f"{k}: {v}" for k, v in sorted(h.services.items()))))
        free = sum(1 for h in results.values() if h.status == "Libre")
        db.log_operation("red", "Escaneo", target_txt, "OK", f"{len(alive)} en uso · {free} libres · {dur:.1f}s")
    every = sorted(results.values(), key=lambda h: ipaddress.IPv4Address(h.ip))
    return {"hosts": alive, "all": every, "scanned": len(targets), "duration": dur, "cancelled": cancelled,
            "scan_id": scan_id, "free": sum(1 for h in every if h.status == "Libre")}


# ---------------------------------------------------------------- disponibilidad de IP
@dataclass
class IpAvailability:
    ip: str
    verdict: str           # EN_USO | NO_CONFIRMADA | INDETERMINADA
    title: str
    evidence: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def check_ip_availability(ip: str) -> IpAvailability:
    """Comprueba si una IP parece libre. NUNCA afirma que esté libre con certeza."""
    ev, warn = [], []
    in_use = False
    alive, rtt = probes.ping(ip, 1000)
    if alive:
        in_use = True
        ev.append(f"Responde al ping ICMP ({rtt or '?'} ms).")
    else:
        ev.append("No responde al ping (no implica que esté libre: puede bloquear ICMP o estar apagado).")
    for p in KEY_PORTS:
        st, _ = probes.tcp_probe(ip, p, 0.8)
        if st in ("open", "closed"):
            in_use = True
            ev.append(f"Responde en TCP {p} ({'abierto' if st == 'open' else 'rechaza conexión'}).")
            break
    iface = interface_for_ip(ip)
    if iface:
        mac = probes.arp_table().get(ip)
        if mac:
            in_use = True
            ev.append(f"Tiene entrada ARP: {mac} ({vendor(mac) or 'fabricante desconocido'}).")
        else:
            ev.append("Sin entrada ARP tras las sondas (ningún equipo encendido respondió con esa IP).")
    else:
        warn.append("La IP está fuera de la subred de este equipo: ARP no es aplicable y la comprobación "
                    "no es concluyente.")
    db_use = get_db().query("SELECT name FROM printers WHERE ip=?", (ip,))
    if db_use:
        in_use = True
        ev.append("Registrada en eREDES para: " + ", ".join(r["name"] for r in db_use))
    gw, _ = default_gateway()
    if ip == gw:
        in_use = True
        ev.append("Es el gateway de la red.")
    warn.append("Si el router reparte IP por DHCP, use una IP fuera del rango DHCP o haga una reserva.")
    warn.append("Un equipo apagado con esa IP fija generaría un conflicto al encenderse.")
    if in_use:
        return IpAvailability(ip, "EN_USO", "IP EN USO", ev, warn)
    if iface:
        return IpAvailability(ip, "NO_CONFIRMADA",
                              "Sin respuesta: probablemente libre, disponibilidad NO confirmada", ev, warn)
    return IpAvailability(ip, "INDETERMINADA", "No se puede determinar la disponibilidad", ev, warn)
