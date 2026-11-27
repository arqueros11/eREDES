"""Diagnóstico de conectividad: gateway, DNS, salida a internet y estabilidad."""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from app.network import probes
from app.network.interfaces import default_gateway, dns_servers


@dataclass
class ConnCheck:
    name: str
    ok: bool | None
    detail: str
    value: float | None = None


def _dns_resolve(host: str, timeout: float = 4.0) -> tuple[bool, float | None, str]:
    import concurrent.futures as cf
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(1) as ex:
        fut = ex.submit(socket.getaddrinfo, host, 443, socket.AF_INET)
        try:
            res = fut.result(timeout=timeout)
            return True, (time.perf_counter() - t0) * 1000, res[0][4][0]
        except Exception as e:  # noqa: BLE001
            return False, None, str(e)


def internet_ok(timeout: float = 2.5) -> bool:
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443)):
        if probes.tcp_probe(host, port, timeout)[0] == "open":
            return True
    return False


def run_connectivity(ctx, stability_pings: int = 20) -> list[ConnCheck]:
    out: list[ConnCheck] = []
    gw, iface = default_gateway()
    ctx.progress(5, "Gateway…")
    if gw:
        st = probes.ping_stats(gw, count=5, timeout_ms=1000, interval=0.2)
        ok = st.received > 0
        out.append(ConnCheck("Ping al gateway", ok,
                             f"{gw} ({iface}) · {st.avg:.1f} ms · pérdida {st.loss_pct:.0f}%" if ok else
                             f"{gw} no responde al ping", st.avg))
    else:
        out.append(ConnCheck("Gateway", False, "No hay ruta por defecto: el equipo no tiene salida configurada."))
    ctx.check()
    ctx.progress(30, "DNS…")
    servers = dns_servers()
    ok, ms, info = _dns_resolve("www.google.com")
    out.append(ConnCheck("Resolución DNS", ok, (f"www.google.com → {info} en {ms:.0f} ms" if ok else
                                                f"Fallo: {info}") + f" · servidores: {', '.join(servers) or '—'}", ms))
    ctx.check()
    ctx.progress(50, "Conectividad externa…")
    for host, port, label in (("1.1.1.1", 443, "Cloudflare 1.1.1.1:443"), ("8.8.8.8", 53, "Google DNS 8.8.8.8:53")):
        stt, rtt = probes.tcp_probe(host, port, 3)
        out.append(ConnCheck(f"Conexión externa ({label})", stt == "open",
                             f"{rtt:.0f} ms" if stt == "open" else "Sin conexión", rtt))
    ctx.check()
    ctx.progress(65, "Estabilidad…")
    target = "1.1.1.1"
    st = probes.ping_stats(target, count=stability_pings, timeout_ms=1000, interval=0.2)
    if st.received:
        stable = st.loss_pct < 2 and (st.jitter or 0) < 30
        out.append(ConnCheck("Estabilidad (ICMP 1.1.1.1)", stable,
                             f"{st.received}/{st.sent} respuestas · pérdida {st.loss_pct:.0f}% · media {st.avg:.1f} ms"
                             f" · jitter {st.jitter or 0:.1f} ms · máx {st.max:.1f} ms", st.loss_pct))
    else:
        out.append(ConnCheck("Estabilidad (ICMP 1.1.1.1)", None,
                             "Sin respuestas ICMP (la red podría bloquear ping externo)"))
    ctx.progress(100, "Completado")
    return out
