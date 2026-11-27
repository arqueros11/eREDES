"""Diagnóstico real de impresoras.

Estados posibles de cada comprobación:
  OK · ERROR · DESCONECTADO · NO_DISPONIBLE · NO_COMPATIBLE · INDETERMINADO
Nunca se informa un error físico que no se haya leído realmente del dispositivo.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from app.core import config
from app.database.db import get_db, now
from app.network import probes, snmp
from app.platform import printer_backend

from app.printers import escpos

log = logging.getLogger("eredes.diag")

OK, ERROR, DESCONECTADO, NO_DISPONIBLE, NO_COMPATIBLE, INDETERMINADO = (
    "OK", "ERROR", "DESCONECTADO", "NO_DISPONIBLE", "NO_COMPATIBLE", "INDETERMINADO")

LABELS = {OK: "Correcto", ERROR: "Error", DESCONECTADO: "Desconectado", NO_DISPONIBLE: "No disponible",
          NO_COMPATIBLE: "No compatible", INDETERMINADO: "No se pudo determinar"}


@dataclass
class Check:
    group: str
    name: str
    status: str
    detail: str = ""


def _bool_check(group, name, value, bad_text, good_text, source):
    if value is None:
        return Check(group, name, INDETERMINADO, f"No informado por {source}")
    return Check(group, name, ERROR if value else OK, bad_text if value else good_text)


def diagnose(ctx, ip: str, port: int = 9100, queue_name: str | None = None, use_escpos: bool = True,
             use_snmp: bool = True) -> dict:
    checks: list[Check] = []
    backend = printer_backend()

    # Conectividad
    ctx.progress(5, "Ping…")
    alive, rtt = probes.ping(ip, 1200)
    checks.append(Check("Red", "Ping ICMP", OK if alive else INDETERMINADO,
                        f"{rtt:.1f} ms" if alive and rtt else ("Responde" if alive else
                                                               "Sin respuesta (algunas impresoras bloquean ICMP)")))
    ctx.progress(15, f"TCP {port}…")
    state, trt = probes.tcp_probe(ip, port, 2.5)
    tcp_ok = state == "open"
    checks.append(Check("Red", f"Puerto TCP {port}", OK if tcp_ok else DESCONECTADO,
                        f"Abierto ({trt:.0f} ms)" if tcp_ok else ("Cerrado: la impresora rechaza la conexión" if
                                                                  state == "closed" else "Sin respuesta")))
    arp = probes.arp_table().get(ip)
    if arp:
        from app.network.oui import vendor
        checks.append(Check("Red", "MAC (ARP)", OK, f"{arp} · {vendor(arp) or 'fabricante desconocido'}"))

    # Sistema operativo / cola
    ctx.progress(30, "Servicio de impresión…")
    sp = backend.spooler_status()
    checks.append(Check("Sistema", "Servicio de impresión", OK if sp.ok else ERROR, sp.message))
    if queue_name:
        p = backend.get_printer(queue_name)
        if not p:
            checks.append(Check("Sistema", "Cola de impresión", ERROR, f"'{queue_name}' no existe en el sistema"))
        else:
            checks.append(Check("Sistema", "Cola de impresión", OK if p.enabled else ERROR,
                                f"{p.status} · {p.jobs} trabajo(s) pendientes"
                                + (f" · {p.extra.get('reason')}" if p.extra.get("reason") else "")))
            checks.append(Check("Sistema", "Controlador", OK if p.driver else INDETERMINADO,
                                p.driver or "El sistema no informa el controlador"))
            if p.ip and p.ip != ip:
                checks.append(Check("Sistema", "Dirección configurada", ERROR,
                                    f"La cola apunta a {p.ip}:{p.port}, no a {ip}:{port}"))
            elif p.ip:
                checks.append(Check("Sistema", "Dirección configurada", OK if (p.port in (None, port)) else ERROR,
                                    f"{p.uri or p.port_name}"))
            if p.jobs > 0:
                jobs = backend.list_jobs(queue_name)
                stuck = [j for j in jobs if any(w in (j.status or "") for w in ("Error", "Blocked"))]
                checks.append(Check("Sistema", "Trabajos en cola", ERROR if stuck else INDETERMINADO,
                                    f"{len(jobs)} en cola" + (f", {len(stuck)} con error" if stuck else "")))

    # ESC/POS
    ctx.progress(50, "Estado ESC/POS…")
    if use_escpos and tcp_ok:
        st = escpos.query_status(ip, port)
        if not st.responded:
            checks.append(Check("Impresora (ESC/POS)", "Estado en tiempo real", NO_DISPONIBLE, st.message))
        elif not st.compatible:
            checks.append(Check("Impresora (ESC/POS)", "Estado en tiempo real", NO_COMPATIBLE,
                                st.message + f" Bytes: {st.raw}"))
        else:
            src = "ESC/POS DLE EOT"
            checks.append(Check("Impresora (ESC/POS)", "Comunicación", OK, "Responde a DLE EOT 1-4"))
            checks.append(Check("Impresora (ESC/POS)", "En línea", OK if st.online else ERROR,
                                "En línea" if st.online else "Fuera de línea"))
            checks.append(_bool_check("Impresora (ESC/POS)", "Papel", st.paper_out, "Papel agotado", "Papel presente", src))
            checks.append(_bool_check("Impresora (ESC/POS)", "Papel casi agotado", st.paper_near_end,
                                      "Rollo casi terminado", "Nivel adecuado", src))
            checks.append(_bool_check("Impresora (ESC/POS)", "Tapa", st.cover_open, "Tapa abierta", "Tapa cerrada", src))
            checks.append(_bool_check("Impresora (ESC/POS)", "Cortador", st.cutter_error, "Error del cortador",
                                      "Sin error", src))
            checks.append(_bool_check("Impresora (ESC/POS)", "Temperatura / error recuperable",
                                      st.auto_recoverable_error,
                                      "Error auto-recuperable (p.ej. sobrecalentamiento del cabezal)",
                                      "Sin error de temperatura", src))
            checks.append(_bool_check("Impresora (ESC/POS)", "Error irrecuperable", st.unrecoverable_error,
                                      "Error irrecuperable: requiere servicio técnico", "Sin error", src))
            checks.append(Check("Impresora (ESC/POS)", "Atasco de papel", NO_DISPONIBLE,
                                "ESC/POS no informa atascos de forma específica (se reflejan como error/offline)."))
    elif use_escpos:
        checks.append(Check("Impresora (ESC/POS)", "Estado en tiempo real", INDETERMINADO,
                            "No se puede consultar: sin conexión TCP"))

    # SNMP
    ctx.progress(75, "SNMP…")
    if use_snmp:
        try:
            v = snmp.snmp_get(ip, [snmp.OID_SYS_DESCR, snmp.OID_HR_PRINTER_STATUS, snmp.OID_HR_PRINTER_ERROR],
                              config.get("snmp_community") or "public", timeout=1.5, retries=1)
            descr = snmp.as_text(v.get(snmp.OID_SYS_DESCR))
            checks.append(Check("Impresora (SNMP)", "Identificación", OK if descr else INDETERMINADO, descr or "-"))
            pst = v.get(snmp.OID_HR_PRINTER_STATUS)
            if isinstance(pst, int):
                checks.append(Check("Impresora (SNMP)", "Estado (Printer-MIB)", OK if pst in (3, 4, 5) else ERROR,
                                    snmp.HR_PRINTER_STATUS.get(pst, str(pst))))
            else:
                checks.append(Check("Impresora (SNMP)", "Estado (Printer-MIB)", NO_DISPONIBLE,
                                    "El dispositivo no implementa Printer-MIB"))
            errs = snmp.decode_printer_errors(v.get(snmp.OID_HR_PRINTER_ERROR))
            if v.get(snmp.OID_HR_PRINTER_ERROR) is None:
                pass
            else:
                checks.append(Check("Impresora (SNMP)", "Errores detectados", ERROR if errs else OK,
                                    ", ".join(errs) if errs else "Ninguno"))
        except snmp.SnmpError:
            checks.append(Check("Impresora (SNMP)", "SNMP", NO_DISPONIBLE,
                                "No responde a SNMP (desactivado o comunidad distinta)"))

    overall = _overall(checks)
    ctx.progress(100, "Diagnóstico completado")
    rows = [asdict(c) for c in checks]
    target = queue_name or ip
    get_db().execute("INSERT INTO diagnostics(ts,target,ip,port,overall,results) VALUES(?,?,?,?,?,?)",
                     (now(), target, ip, port, overall, json.dumps(rows, ensure_ascii=False)))
    get_db().log_operation("diagnostico", "Diagnóstico de impresora", f"{target} ({ip}:{port})", overall,
                           "; ".join(f"{c.name}: {LABELS[c.status]}" for c in checks if c.status != OK)[:1000])
    return {"checks": checks, "overall": overall}


def _overall(checks: list[Check]) -> str:
    sts = {c.status for c in checks}
    if any(c.status == DESCONECTADO for c in checks if c.group == "Red"):
        return DESCONECTADO
    if ERROR in sts:
        return ERROR
    if OK in sts:
        return OK
    return INDETERMINADO
