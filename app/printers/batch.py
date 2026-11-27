"""PC nueva: encontrar las impresoras de la red, identificarlas a distancia e instalarlas en lote.

Pensado para el caso típico de soporte remoto (AnyDesk): el equipo del local se cambió y hay que
volver a dejar todas las impresoras funcionando sin que el usuario tenga que hacer nada.

  1. find_printers   → escanea la red y devuelve las impresoras (puerto RAW/LPD/IPP abierto).
  2. identify        → imprime en cada una su número de IP en grande (TCP directo, sin instalar nada).
  3. install_batch   → crea todas las colas con su controlador y manda un ticket de prueba a cada una.
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass

from app.core.platform_info import IS_WINDOWS
from app.core.validation import ValidationError, suggest_cups_name, validate_printer_name
from app.platform.base import DriverRef, SystemDriver

from . import escpos

log = logging.getLogger("eredes.batch")

RAW_PORTS = (9100, 9101, 9102)
PRINT_PORTS = RAW_PORTS + (515, 631)
SCAN_PORTS = [9100, 9101, 9102, 515, 631, 80, 443, 161]

# Nombres habituales en restaurantes y comercios (sugerencias del desplegable)
COMMON_NAMES = ["COCINA", "BARRA", "CAJA", "CAJA 2", "BEBIDAS", "PARRILLA", "PIZZERIA", "POSTRES",
                "DELIVERY", "SALON", "TERRAZA", "COMANDAS", "OFICINA"]

_POS_WORDS = ("tm-", "pos", "receipt", "thermal", "xp-", "80mm", "58mm", "t20", "t88", "tm t", "ticket")
_VENDOR_STOP = {"corporation", "corp", "inc", "ltd", "co", "company", "technology", "technologies", "electronics",
                "electronic", "group", "limited", "international", "the", "co.,ltd", "co.,ltd."}
_VIRTUAL = ("pdf", "xps", "onenote", "fax", "send to", "microsoft print")


@dataclass
class Candidate:
    ip: str
    port: int
    vendor: str = ""
    model: str = ""          # descripción SNMP o título web
    hostname: str = ""
    mac: str = ""
    rtt_ms: float | None = None
    installed_as: str = ""   # nombre de la impresora del sistema que ya usa esta IP

    @property
    def last_octet(self) -> str:
        return self.ip.rsplit(".", 1)[-1]

    @property
    def short_vendor(self) -> str:
        words = [w for w in (self.vendor or "").replace(",", " ").split()
                 if w.lower().strip(".") not in _VENDOR_STOP]
        return " ".join(words[:2])

    @property
    def label(self) -> str:
        return " · ".join(x for x in (self.short_vendor, self.model) if x) or "Impresora de red"


def _sub(ctx, lo: float, hi: float):
    """Contexto que traduce el progreso 0-100 de una subtarea al tramo [lo, hi] de la tarea principal."""
    class Sub:
        cancelled = property(lambda _s: ctx.cancelled)

        def progress(self, pct, msg=""):
            ctx.progress(int(lo + (hi - lo) * max(0, min(100, pct)) / 100), msg)

        def check(self):
            ctx.check()

        def emit(self, payload):
            pass  # los resultados parciales del escáner no se reenvían
    return Sub()


def installed_by_ip(printers) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in printers:
        if p.ip and p.ip not in out:
            out[p.ip] = p.name
    return out


def find_printers(ctx, targets: list[str]) -> dict:
    """Escanea `targets` y devuelve {"printers": [Candidate], "alive": n, "scanned": n}."""
    from app.network.scanner import scan
    from app.platform import printer_backend
    res = scan(_sub(ctx, 0, 92), targets, SCAN_PORTS, True)
    ctx.progress(94, "Comprobando impresoras ya instaladas…")
    try:
        installed = installed_by_ip(printer_backend().list_printers())
    except Exception as e:  # noqa: BLE001 — sin servicio de impresión se sigue mostrando la red
        log.warning("No se pudo leer las impresoras del sistema: %s", e)
        installed = {}
    found = []
    for h in res["hosts"]:
        if h.is_self or h.dev_type == "Router / Gateway":
            continue
        port = next((p for p in PRINT_PORTS if p in h.open_ports), None)
        if port is None:
            continue
        model = h.snmp_descr or next((v.split(":", 1)[1].strip() for k, v in sorted(h.services.items())
                                      if str(v).startswith("Web:")), "")
        found.append(Candidate(ip=h.ip, port=port, vendor=h.vendor, model=model[:60], hostname=h.hostname,
                               mac=h.mac, rtt_ms=h.rtt_ms, installed_as=installed.get(h.ip, "")))
    found.sort(key=lambda c: ipaddress.IPv4Address(c.ip))
    ctx.progress(100, f"{len(found)} impresoras encontradas")
    return {"printers": found, "alive": len(res["hosts"]), "scanned": res["scanned"],
            "cancelled": res["cancelled"]}


def default_driver(drivers: list[SystemDriver], vendor: str = "", model: str = "") -> SystemDriver | None:
    """Controlador más seguro para una térmica de red.

    Windows: el del fabricante solo si es claramente de tickets (TM-, POS, Receipt…); si no,
    "Generic / Text Only" (eREDES imprime en RAW, así que funciona con cualquier térmica ESC/POS).
    macOS: el PPD ESC/POS de eREDES.
    """
    if not drivers:
        return None
    if not IS_WINDOWS:
        return next((d for d in drivers if d.kind == "ppd_file"), drivers[0])
    words = [w for w in (vendor or "").lower().replace(",", " ").replace(".", " ").split()
             if len(w) > 2 and w not in _VENDOR_STOP]
    hint = (model or "").lower()
    real = [d for d in drivers if not any(v in d.name.lower() for v in _VIRTUAL)]
    for d in real:
        n = d.name.lower()
        if any(w in n for w in words) and any(p in n for p in _POS_WORDS):
            return d
        if hint and any(p in hint for p in _POS_WORDS) and any(tok in n for tok in hint.split() if "-" in tok):
            return d
    generic = next((d for d in drivers if d.name.lower() == "generic / text only"), None)
    return generic or (real[0] if real else drivers[0])


def driver_ref(d: SystemDriver) -> DriverRef:
    return DriverRef(d.kind if d.kind != "model" else "model", d.ref, d.name)


def clean_name(name: str) -> str:
    """Adapta el nombre a las reglas del sistema (en macOS no se admiten espacios)."""
    name = (name or "").strip()
    return name if IS_WINDOWS else suggest_cups_name(name)


def check_names(names: list[str], existing: list[str]) -> list[str]:
    """Errores de nombres antes de instalar (vacíos, inválidos, repetidos o ya existentes)."""
    errs, seen = [], set()
    taken = {e.lower() for e in existing}
    for n in names:
        try:
            validate_printer_name(n)
        except ValidationError as e:
            errs.append(f"{n or '(vacío)'}: {e}")
            continue
        if n.lower() in seen:
            errs.append(f"{n}: nombre repetido")
        elif n.lower() in taken:
            errs.append(f"{n}: ya existe una impresora con ese nombre en este equipo")
        seen.add(n.lower())
    return errs


def identify(ctx, items: list[tuple[str, int]]) -> list[tuple[str, bool, str]]:
    """Imprime en cada impresora su número de IP (TCP directo, no hace falta instalarla)."""
    out = []
    for i, (ip, port) in enumerate(items):
        ctx.check()
        ctx.progress(int(i * 100 / max(1, len(items))), f"Imprimiendo número en {ip}…")
        if port not in RAW_PORTS:
            out.append((ip, False, f"Puerto {port}: solo se puede identificar por 9100"))
            continue
        ok, msg = escpos.send_raw(ip, port, escpos.identify_ticket(ip, port))
        out.append((ip, ok, msg))
    ctx.progress(100, "Listo")
    return out


@dataclass
class InstallJob:
    name: str
    driver: DriverRef
    ip: str
    port: int


@dataclass
class InstallOutcome:
    name: str
    ip: str
    created: bool
    printed: bool
    message: str


def install_batch(ctx, jobs: list[InstallJob], test: bool = True) -> list[InstallOutcome]:
    """Crea todas las impresoras y envía un ticket de prueba a cada una.

    ctx.emit((indice, InstallOutcome)) al terminar cada impresora.
    """
    from .manager import PrinterManager
    m = PrinterManager()
    out: list[InstallOutcome] = []
    n = max(1, len(jobs))
    for i, j in enumerate(jobs):
        ctx.check()
        lo, hi = i * 100 / n, (i + 1) * 100 / n
        ctx.progress(int(lo), f"{j.name}: instalando ({i + 1}/{len(jobs)})…")
        r = m.create(_sub(ctx, lo, lo + (hi - lo) * 0.7), j.name, j.driver, j.ip, j.port)
        if not r.ok:
            o = InstallOutcome(j.name, j.ip, False, False, r.message)
        elif not test:
            o = InstallOutcome(j.name, j.ip, True, False, "Instalada")
        else:
            ctx.progress(int(lo + (hi - lo) * 0.75), f"{j.name}: imprimiendo prueba…")
            t = m.test_print_os(j.name, escpos_ticket=True, wait=25)
            o = InstallOutcome(j.name, j.ip, True, t.ok,
                               "Instalada · prueba enviada" if t.ok else f"Instalada · prueba: {t.message}")
        out.append(o)
        ctx.emit((i, o))
    ctx.progress(100, "Terminado")
    return out
