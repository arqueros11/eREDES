"""Administrador de impresoras: orquesta el procedimiento completo de alta y las ediciones.

Procedimiento de alta (como en Windows):
  1. Validar nombre, IP y puerto.
  2. Comprobar conexión TCP con la impresora.
  3. Instalar/asignar el controlador (si viene de la biblioteca y no está instalado).
  4. Crear el puerto TCP/IP.
  5. Registrar la impresora en el SO.
  6. Confirmar que el SO la muestra.
  7. Guardar en la BD e historial.
Cada paso devuelve un estado real; nada se marca como correcto sin confirmación del SO.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.core.platform_info import os_key
from app.core.validation import ValidationError, validate_ipv4, validate_port, validate_printer_name
from app.database.db import get_db, now
from app.drivers import installers
from app.drivers.library import DriverLibrary
from app.network import probes
from app.platform import printer_backend
from app.platform.base import DriverRef, OpResult, SystemPrinter

from . import escpos

log = logging.getLogger("eredes.printers")


@dataclass
class Step:
    name: str
    status: str = "pendiente"   # ok | error | aviso | omitido | pendiente
    message: str = ""


@dataclass
class CreateResult:
    ok: bool
    steps: list[Step] = field(default_factory=list)
    message: str = ""


class PrinterManager:
    def __init__(self, library: DriverLibrary | None = None):
        self.backend = printer_backend()
        self.lib = library or DriverLibrary()
        self.db = get_db()

    # ------------------------------------------------------------ consultas
    def system_printers(self) -> list[SystemPrinter]:
        printers = self.backend.list_printers()
        self._sync_db(printers)
        return printers

    def _sync_db(self, printers: list[SystemPrinter]) -> None:
        """Actualiza la BD con lo que realmente existe en el SO."""
        names = {p.name.lower(): p for p in printers}
        for row in self.db.query("SELECT * FROM printers WHERE os=?", (os_key(),)):
            p = names.get(row["name"].lower())
            if not p:
                self.db.execute("UPDATE printers SET last_status=?, updated_at=? WHERE id=?",
                                ("No existe en el sistema", now(), row["id"]))
            else:
                self.db.execute("UPDATE printers SET driver=?, ip=?, port=?, last_status=?, updated_at=? WHERE id=?",
                                (p.driver, p.ip or row["ip"], p.port or row["port"], p.status, now(), row["id"]))

    def registered(self):
        return self.db.query("SELECT * FROM printers ORDER BY name")

    def system_drivers(self):
        return self.backend.list_drivers()

    # ------------------------------------------------------------ alta
    def create(self, ctx, name: str, driver: DriverRef, ip: str, port: int,
               skip_connection_check: bool = False, confirmed_install: bool = False) -> CreateResult:
        steps = [Step("Validar datos"), Step("Comprobar conexión"), Step("Controlador"),
                 Step("Puerto TCP/IP y registro en el SO"), Step("Confirmación del sistema")]
        res = CreateResult(False, steps)

        def fail(i: int, msg: str) -> CreateResult:
            steps[i].status, steps[i].message = "error", msg
            res.message = msg
            self.db.log_operation("impresoras", "Crear impresora", name, "ERROR", f"{steps[i].name}: {msg}")
            return res

        # 1
        ctx.progress(5, "Validando datos…")
        try:
            name = validate_printer_name(name)
            ip = validate_ipv4(ip)
            port = validate_port(port)
        except ValidationError as e:
            return fail(0, str(e))
        if self.backend.get_printer(name):
            return fail(0, f"Ya existe una impresora llamada '{name}' en el sistema.")
        steps[0].status, steps[0].message = "ok", f"{name} · {ip}:{port}"
        ctx.check()
        # 2
        ctx.progress(20, f"Comprobando {ip}:{port}…")
        state, rtt = probes.tcp_probe(ip, port, 2.5)
        if state == "open":
            steps[1].status, steps[1].message = "ok", f"Puerto {port} abierto ({rtt:.0f} ms)."
        elif skip_connection_check:
            steps[1].status = "aviso"
            steps[1].message = (f"Puerto {port} {'cerrado' if state == 'closed' else 'sin respuesta'}. "
                                "Se continúa por decisión del técnico (la impresora podría estar apagada).")
        else:
            return fail(1, f"No hay conexión con {ip}:{port} ({'cerrado' if state == 'closed' else 'sin respuesta'}). "
                           "Revise IP, cable y que la impresora esté encendida.")
        ctx.check()
        # 3
        ctx.progress(40, "Preparando controlador…")
        if driver.package_id and os_key() == "windows" and driver.kind == "installed":
            installed = {d.name for d in self.backend.list_drivers()}
            if driver.ref not in installed:
                r = installers.install_package(self.lib, driver.package_id, confirmed=confirmed_install,
                                               driver_names=[driver.ref])
                if not r.ok:
                    return fail(2, f"{r.message} {r.detail}".strip())
                steps[2].status, steps[2].message = "ok", r.message
            else:
                steps[2].status, steps[2].message = "ok", f"Controlador ya instalado: {driver.ref}"
        elif driver.package_id and driver.kind == "ppd_file":
            pc = installers.precheck(self.lib, driver.package_id)
            if not pc.ok:
                return fail(2, " ".join(pc.blocking))
            steps[2].status, steps[2].message = "ok", f"PPD verificado: {driver.label or driver.ref}"
        else:
            steps[2].status, steps[2].message = "ok", f"Controlador del sistema: {driver.label or driver.ref}"
        ctx.check()
        # 4
        ctx.progress(65, "Creando puerto y registrando la impresora…")
        r = self.backend.create_printer(name, driver, ip, port)
        if not r.ok:
            return fail(3, f"{r.message} {r.detail}".strip())
        steps[3].status, steps[3].message = "ok", r.message
        # 5
        ctx.progress(85, "Confirmando con el sistema operativo…")
        p = self.backend.get_printer(name)
        if not p:
            return fail(4, "El sistema operativo no muestra la impresora.")
        steps[4].status, steps[4].message = "ok", f"Visible en el sistema · controlador: {p.driver or driver.label}"
        self.db.execute(
            "INSERT INTO printers(name,driver,driver_package_id,ip,port,os,last_status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET driver=excluded.driver,"
            " driver_package_id=excluded.driver_package_id, ip=excluded.ip, port=excluded.port,"
            " os=excluded.os, last_status=excluded.last_status, updated_at=excluded.updated_at",
            (name, p.driver or driver.label or driver.ref, driver.package_id, ip, port, os_key(), p.status, now(), now()))
        self.db.log_operation("impresoras", "Crear impresora", name, "OK",
                              f"{ip}:{port} · {p.driver or driver.ref}")
        ctx.progress(100, "Impresora creada")
        res.ok = True
        res.message = f"Impresora {name} creada y confirmada por el sistema."
        return res

    # ------------------------------------------------------------ edición
    def _log(self, action: str, name: str, r: OpResult) -> OpResult:
        self.db.log_operation("impresoras", action, name, "OK" if r.ok else "ERROR",
                              r.message + (f" | {r.detail}" if r.detail else ""))
        return r

    def remove(self, name: str) -> OpResult:
        r = self.backend.remove_printer(name)
        if r.ok:
            self.db.execute("DELETE FROM printers WHERE name=?", (name,))
        return self._log("Eliminar impresora", name, r)

    def change_address(self, name: str, ip: str, port: int) -> OpResult:
        try:
            ip = validate_ipv4(ip)
            port = validate_port(port)
        except ValidationError as e:
            return OpResult(False, str(e))
        r = self.backend.set_address(name, ip, port)
        if r.ok:
            self._upsert_from_system(name, ip=ip, port=port)
        return self._log("Cambiar IP/puerto", name, r)

    def change_driver(self, name: str, driver: DriverRef) -> OpResult:
        if driver.package_id and driver.kind == "ppd_file":
            pc = installers.precheck(self.lib, driver.package_id)
            if not pc.ok:
                return OpResult(False, "Controlador incompatible o no válido.", " ".join(pc.blocking))
        r = self.backend.set_driver(name, driver)
        if r.ok:
            self._upsert_from_system(name, package_id=driver.package_id)
        return self._log("Cambiar controlador", name, r)

    def rename(self, old: str, new: str) -> OpResult:
        try:
            new = validate_printer_name(new)
        except ValidationError as e:
            return OpResult(False, str(e))
        if self.backend.get_printer(new):
            return OpResult(False, f"Ya existe '{new}'.")
        r = self.backend.rename_printer(old, new)
        if r.ok:
            self.db.execute("UPDATE printers SET name=?, updated_at=? WHERE name=?", (new, now(), old))
        return self._log("Renombrar impresora", f"{old} → {new}", r)

    def _upsert_from_system(self, name: str, ip: str | None = None, port: int | None = None,
                            package_id: int | None = None) -> None:
        p = self.backend.get_printer(name)
        if not p:
            return
        row = self.db.one("SELECT * FROM printers WHERE name=?", (name,))
        if row:
            self.db.execute("UPDATE printers SET driver=?, ip=?, port=?, driver_package_id=COALESCE(?,driver_package_id),"
                            " last_status=?, updated_at=? WHERE id=?",
                            (p.driver, ip or p.ip, port or p.port, package_id, p.status, now(), row["id"]))
        else:
            self.adopt(p)

    def adopt(self, p: SystemPrinter) -> None:
        """Registra en eREDES una impresora que ya existía en el sistema."""
        if not p.ip:
            return
        self.db.execute(
            "INSERT OR IGNORE INTO printers(name,driver,ip,port,os,last_status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)", (p.name, p.driver, p.ip, p.port or 9100, os_key(), p.status, now(), now()))

    def clear_queue(self, name: str) -> OpResult:
        return self._log("Vaciar cola", name, self.backend.clear_queue(name))

    def restart_queue(self, name: str) -> OpResult:
        return self._log("Reiniciar cola", name, self.backend.restart_queue(name))

    def restart_spooler(self) -> OpResult:
        return self._log("Reiniciar servicio de impresión", "spooler", self.backend.restart_spooler())

    # ------------------------------------------------------------ pruebas
    def test_print_os(self, name: str, escpos_ticket: bool = True, wait: float = 45) -> OpResult:
        """Prueba a través de la cola del SO. Para térmicas se envía un ticket ESC/POS RAW."""
        p = self.backend.get_printer(name)
        if not p:
            return OpResult(False, f"{name} no existe en el sistema.")
        if escpos_ticket:
            data = escpos.test_ticket(name, p.ip or "-", p.port or 0)
            r = self.backend.print_raw(name, data, "eREDES prueba ESC/POS")
        else:
            r = self.backend.print_test_page(name)
        if not r.ok:
            return self._log("Prueba de impresión (cola)", name, r)
        job = r.data.get("job_id", "")
        w = self.backend.wait_job(name, job, wait)
        final = OpResult(w.ok, f"{r.message} {w.message}", w.detail, data={"job_id": job})
        return self._log("Prueba de impresión (cola)", name, final)

    def test_print_direct(self, name: str, ip: str, port: int) -> OpResult:
        ok, msg = escpos.send_raw(ip, port, escpos.test_ticket(name, ip, port))
        return self._log("Prueba de impresión directa TCP", f"{name} {ip}:{port}", OpResult(ok, msg))

    def record_physical_result(self, name: str, printed_ok: bool, notes: str = "") -> None:
        self.db.log_operation("impresoras", "Resultado físico de impresión", name,
                              "OK" if printed_ok else "ERROR",
                              ("Confirmado por el técnico: ticket impreso correctamente" if printed_ok
                               else "El técnico indica que NO se imprimió correctamente") + (f". {notes}" if notes else ""))


def package_driver_choices(lib: DriverLibrary, model_id: int) -> list[tuple[DriverRef | None, str, bool, str]]:
    """Opciones de controlador para un modelo: [(DriverRef, etiqueta, compatible, motivo)]."""
    out = []
    for row, ok, why in lib.compatible_packages(model_id):
        names = json.loads(row["driver_names"] or "[]")
        base = f"{row['filename']} · {row['pkg_type']} · {row['os']}/{row['arch']}" + (
            f" · v{row['version']}" if row["version"] else "")
        if row["pkg_type"] == "INF" and names:
            for n in names:
                out.append((DriverRef("installed", n, n, row["id"]), f"{n}  ({base})", ok, why))
        elif row["pkg_type"] == "PPD":
            out.append((installers.driver_ref_for_package(lib, row["id"]), f"{names[0] if names else row['filename']}  ({base})",
                        ok, why))
        elif names:
            for n in names:
                ref = installers.driver_ref_for_package(lib, row["id"], n)
                out.append((ref, f"{n}  ({base}, instalado)", ok and ref is not None, why))
        else:
            out.append((None, f"{base} — instale el paquete primero desde la Biblioteca", False,
                        "Instalador del fabricante: instálelo y el driver aparecerá aquí."))
    return out
