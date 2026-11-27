"""Test de velocidad REAL contra la red de Cloudflare (speed.cloudflare.com).

* Latencia y jitter: tiempos de establecimiento TCP (handshake) al servidor de prueba.
* Pérdida de paquetes: ping ICMP del sistema a 1.1.1.1.
* Descarga: varias conexiones HTTPS paralelas leyendo /__down durante N segundos.
* Subida: varias conexiones HTTPS paralelas enviando datos a /__up durante N segundos.
No se simulan valores: si una fase falla se informa el error.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import ssl
import statistics
import threading
import time
from dataclasses import asdict, dataclass, field

from app.database.db import get_db, now
from app.network import probes

log = logging.getLogger("eredes.speedtest")

HOST = "speed.cloudflare.com"
STREAMS = 4
CHUNK = 64 * 1024


@dataclass
class SpeedResult:
    download_mbps: float | None = None
    upload_mbps: float | None = None
    ping_ms: float | None = None
    jitter_ms: float | None = None
    loss_pct: float | None = None
    server: str = ""
    client_ip: str = ""
    isp: str = ""
    duration_s: float = 0.0
    errors: list = field(default_factory=list)
    samples_down: list = field(default_factory=list)
    samples_up: list = field(default_factory=list)


def _ssl_ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def _conn(timeout: float = 10) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(HOST, 443, timeout=timeout, context=_ssl_ctx())


def server_info() -> dict:
    c = _conn()
    try:
        c.request("GET", "/meta", headers={"User-Agent": "eREDES"})
        r = c.getresponse()
        body = r.read()
        if r.status == 200:
            return json.loads(body)
    except Exception as e:  # noqa: BLE001
        log.info("meta no disponible: %s", e)
    finally:
        c.close()
    # alternativa: cabeceras cf-meta-* de /__down
    c = _conn()
    try:
        c.request("GET", "/__down?bytes=0", headers={"User-Agent": "eREDES"})
        r = c.getresponse()
        r.read()
        return {"colo": r.getheader("cf-meta-colo") or (r.getheader("cf-ray") or "-").split("-")[-1],
                "city": r.getheader("cf-meta-city") or "", "country": r.getheader("cf-meta-country") or "",
                "clientIp": r.getheader("cf-meta-ip") or ""}
    finally:
        c.close()


def measure_latency(samples: int = 12) -> tuple[float, float, list[float]]:
    addr = socket.getaddrinfo(HOST, 443, socket.AF_INET, socket.SOCK_STREAM)[0][4]
    rtts = []
    for _ in range(samples):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        t0 = time.perf_counter()
        try:
            s.connect(addr)
            rtts.append((time.perf_counter() - t0) * 1000)
        except OSError:
            pass
        finally:
            s.close()
        time.sleep(0.05)
    if not rtts:
        raise OSError("No se pudo conectar con el servidor de prueba")
    jitter = statistics.mean(abs(a - b) for a, b in zip(rtts[1:], rtts[:-1])) if len(rtts) > 1 else 0.0
    return statistics.median(rtts), jitter, rtts


class _Counter:
    """`bytes` = bytes transferidos (visualización en vivo).
    `confirmed` = bytes confirmados (descarga: recibidos; subida: POST respondido por el servidor)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.bytes = 0
        self.confirmed = 0
        self.errors: list[str] = []

    def add(self, n: int, confirmed: bool = True) -> None:
        with self.lock:
            self.bytes += n
            if confirmed:
                self.confirmed += n

    def confirm(self, n: int) -> None:
        with self.lock:
            self.confirmed += n


def _download_worker(stop: threading.Event, counter: _Counter) -> None:
    while not stop.is_set():
        c = _conn(15)
        try:
            c.request("GET", "/__down?bytes=50000000", headers={"User-Agent": "eREDES"})
            r = c.getresponse()
            if r.status != 200:
                counter.errors.append(f"HTTP {r.status}")
                time.sleep(0.5)
                continue
            while not stop.is_set():
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                counter.add(len(chunk))
        except Exception as e:  # noqa: BLE001
            counter.errors.append(str(e))
            time.sleep(0.3)
        finally:
            c.close()


_UP_PAYLOAD = os.urandom(1024 * 1024)


def _upload_worker(stop: threading.Event, counter: _Counter) -> None:
    payload = memoryview(_UP_PAYLOAD)
    while not stop.is_set():
        c = _conn(20)
        try:
            c.putrequest("POST", "/__up")
            c.putheader("Content-Type", "application/octet-stream")
            c.putheader("Content-Length", str(len(payload)))
            c.putheader("User-Agent", "eREDES")
            c.endheaders()
            sent = 0
            while sent < len(payload) and not stop.is_set():
                n = c.sock.send(payload[sent:sent + CHUNK])
                sent += n
                counter.add(n, confirmed=False)
            if stop.is_set():
                break
            r = c.getresponse()
            r.read()
            if r.status == 200:
                counter.confirm(sent)  # solo cuenta lo que el servidor confirmó haber recibido
            else:
                counter.errors.append(f"HTTP {r.status}")
        except Exception as e:  # noqa: BLE001
            counter.errors.append(str(e))
            time.sleep(0.3)
        finally:
            c.close()


def _phase(ctx, worker, seconds: float, label: str, base_pct: int, span: int) -> tuple[float | None, list, list]:
    stop = threading.Event()
    counter = _Counter()
    threads = [threading.Thread(target=worker, args=(stop, counter), daemon=True) for _ in range(STREAMS)]
    for t in threads:
        t.start()
    samples = []
    t0 = time.perf_counter()
    last_b, last_t = 0, t0
    warm_b, warm_t = None, None
    try:
        while True:
            time.sleep(0.25)
            t = time.perf_counter()
            el = t - t0
            b = counter.bytes
            inst = (b - last_b) * 8 / (t - last_t) / 1e6
            last_b, last_t = b, t
            samples.append(round(inst, 2))
            if warm_b is None and el >= 1.5:  # descartar arranque TCP (slow start)
                warm_b, warm_t = counter.confirmed, t
            ctx.emit({"phase": label, "mbps": inst})
            ctx.progress(base_pct + int(min(1, el / seconds) * span), f"{label}: {inst:.1f} Mbps")
            if el >= seconds or ctx.cancelled:
                break
    finally:
        stop.set()
    end_b, end_t = counter.confirmed, time.perf_counter()
    for th in threads:
        th.join(timeout=2)
    if warm_b is None or end_t - warm_t <= 0 or end_b - warm_b <= 0:
        return None, samples, counter.errors
    return (end_b - warm_b) * 8 / (end_t - warm_t) / 1e6, samples, counter.errors


def run_speedtest(ctx, seconds: float = 8.0) -> SpeedResult:
    res = SpeedResult()
    t0 = time.time()
    ctx.progress(2, "Conectando con el servidor de prueba…")
    try:
        info = server_info()
        city = info.get("city", "")
        res.server = f"Cloudflare {info.get('colo', '')}" + (f" · {city}" if city else "") + (
            f" ({info.get('country')})" if info.get("country") else "")
        res.client_ip = info.get("clientIp", "")
        res.isp = info.get("asOrganization", "")
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"Servidor de prueba no disponible: {e}")
        res.duration_s = time.time() - t0
        return res
    ctx.check()
    ctx.progress(6, "Midiendo latencia…")
    try:
        res.ping_ms, res.jitter_ms, _ = measure_latency()
        ctx.emit({"phase": "Latencia", "ping": res.ping_ms, "jitter": res.jitter_ms})
    except OSError as e:
        res.errors.append(f"Latencia: {e}")
    ctx.check()
    ctx.progress(12, "Pérdida de paquetes…")
    st = probes.ping_stats("1.1.1.1", count=20, timeout_ms=1000, interval=0.2)
    res.loss_pct = st.loss_pct if st.sent else None
    if st.received == 0:
        res.errors.append("ICMP bloqueado hacia 1.1.1.1: la pérdida de paquetes no es concluyente.")
    ctx.check()
    res.download_mbps, res.samples_down, errs = _phase(ctx, _download_worker, seconds, "Descarga", 20, 40)
    if res.download_mbps is None:
        res.errors.append("Descarga fallida: " + (errs[0] if errs else "sin datos"))
    ctx.check()
    res.upload_mbps, res.samples_up, errs = _phase(ctx, _upload_worker, seconds, "Subida", 60, 38)
    if res.upload_mbps is None:
        res.errors.append("Subida fallida: " + (errs[0] if errs else "sin datos"))
    res.duration_s = time.time() - t0
    ctx.progress(100, "Test completado")
    if not ctx.cancelled:
        get_db().execute(
            "INSERT INTO speed_tests(ts,download_mbps,upload_mbps,ping_ms,jitter_ms,loss_pct,server,duration_s,detail)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (now(), res.download_mbps, res.upload_mbps, res.ping_ms, res.jitter_ms, res.loss_pct, res.server,
             res.duration_s, json.dumps({k: v for k, v in asdict(res).items() if k.startswith(("errors", "isp",
                                                                                              "client"))})))
        get_db().log_operation("internet", "Speed test", res.server, "OK" if not res.errors else "AVISO",
                               f"↓{_f(res.download_mbps)} ↑{_f(res.upload_mbps)} Mbps · ping {_f(res.ping_ms)} ms")
    return res


def _f(v) -> str:
    return "-" if v is None else f"{v:.1f}"
