"""Adaptador de impresoras para Windows 10/11.

Usa el módulo PrintManagement de PowerShell (Get-Printer, Add-Printer, Add-PrinterPort,
Add-PrinterDriver...), pnputil para el almacén de drivers y la API winspool (ctypes)
para enviar datos RAW (ESC/POS) a una cola.
Requiere ejecutar eREDES como administrador para crear/eliminar impresoras y drivers.
"""
from __future__ import annotations

import ctypes
import json
import logging
import time
from ctypes import wintypes

from app.core.platform_info import IS_WINDOWS, is_admin
from app.core.shell import ps_quote, run, run_powershell
from app.platform.base import (DriverRef, OpResult, PrinterBackend, PrintJob, SystemDriver,
                               SystemPrinter)

log = logging.getLogger("eredes.windows.printers")

PRINTER_STATUS = {
    "Normal": "Lista", "Idle": "Inactiva (lista)", "Printing": "Imprimiendo", "Paused": "En pausa",
    "Error": "Error", "PaperJam": "Atasco de papel", "PaperOut": "Sin papel", "Offline": "Sin conexión",
    "DoorOpen": "Tapa abierta", "NotAvailable": "No disponible", "Busy": "Ocupada",
    "TonerLow": "Tóner bajo", "UserInterventionRequired": "Requiere intervención",
}


def port_name_for(ip: str, port: int) -> str:
    return f"IP_{ip}" if port == 9100 else f"IP_{ip}_{port}"


def _json_list(text: str) -> list[dict]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        # buscar la última línea JSON
        for line in reversed(text.splitlines()):
            try:
                data = json.loads(line)
                break
            except ValueError:
                continue
        else:
            return []
    if isinstance(data, dict):
        return [data]
    return data or []


def _need_admin() -> OpResult | None:
    if IS_WINDOWS and not is_admin():
        return OpResult(False, "Esta operación requiere ejecutar eREDES como administrador.",
                        "Use 'Reiniciar como administrador' en la barra superior.")
    return None


class WindowsPrinterBackend(PrinterBackend):
    os_name = "windows"

    # ------------------------------------------------------------ consulta
    def list_printers(self) -> list[SystemPrinter]:
        script = (
            "$ports=@{}; Get-PrinterPort | ForEach-Object { $ports[$_.Name]=$_ };"
            "$list=@(Get-Printer | ForEach-Object { $pp=$ports[$_.PortName];"
            "[pscustomobject]@{Name=$_.Name;Driver=$_.DriverName;Port=$_.PortName;"
            "Status=[string]$_.PrinterStatus;Host=if($pp){[string]$pp.PrinterHostAddress}else{''};"
            "PortNumber=if($pp){$pp.PortNumber}else{$null};Shared=[bool]$_.Shared;Jobs=[int]$_.JobCount;"
            "Type=[string]$_.Type;Comment=[string]$_.Comment} });"
            "ConvertTo-Json -InputObject $list -Depth 3 -Compress"
        )
        r = run_powershell(script, timeout=60)
        if not r.ok:
            log.error("Get-Printer falló: %s", r.short_error())
            return []
        out = []
        for d in _json_list(r.stdout):
            st = d.get("Status") or ""
            out.append(SystemPrinter(
                name=d.get("Name", ""), driver=d.get("Driver", ""), port_name=d.get("Port", ""),
                uri=d.get("Port", ""), ip=d.get("Host") or "", port=d.get("PortNumber"),
                status=PRINTER_STATUS.get(st, st), enabled=st not in ("Paused", "Offline"),
                jobs=int(d.get("Jobs") or 0), shared=bool(d.get("Shared")), os="Windows",
                extra={"type": d.get("Type", ""), "comment": d.get("Comment", "")}))
        return sorted(out, key=lambda p: p.name.lower())

    def get_printer(self, name: str) -> SystemPrinter | None:
        for p in self.list_printers():
            if p.name.lower() == name.lower():
                return p
        return None

    def list_drivers(self) -> list[SystemDriver]:
        r = run_powershell(
            "$l=@(Get-PrinterDriver | Select-Object Name,Manufacturer,"
            "@{n='Version';e={[string]$_.DriverVersion}},PrinterEnvironment);"
            "ConvertTo-Json -InputObject $l -Compress", timeout=60)
        out = []
        for d in _json_list(r.stdout):
            ver = d.get("Version") or ""
            try:  # DriverVersion es un UInt64 empaquetado (a.b.c.d)
                v = int(ver)
                ver = f"{(v >> 48) & 0xFFFF}.{(v >> 32) & 0xFFFF}.{(v >> 16) & 0xFFFF}.{v & 0xFFFF}"
            except (TypeError, ValueError):
                pass
            out.append(SystemDriver(name=d.get("Name", ""), ref=d.get("Name", ""),
                                    manufacturer=d.get("Manufacturer") or "", version=ver, kind="installed"))
        return out

    def driver_installed(self, name: str) -> bool:
        r = run_powershell(f"if(Get-PrinterDriver -Name {ps_quote(name)} -ErrorAction SilentlyContinue)"
                           "{'SI'}else{'NO'}", timeout=30)
        return r.stdout.strip().endswith("SI")

    # ------------------------------------------------------------ puertos
    def ensure_tcp_port(self, ip: str, port: int) -> OpResult:
        name = port_name_for(ip, port)
        script = (
            f"$n={ps_quote(name)};$p=Get-PrinterPort -Name $n -ErrorAction SilentlyContinue;"
            f"if($p){{ if($p.PrinterHostAddress -ne {ps_quote(ip)} -or $p.PortNumber -ne {int(port)})"
            "{ throw \"El puerto $n existe con otra configuración\" } ; 'EXISTE' }"
            f"else {{ Add-PrinterPort -Name $n -PrinterHostAddress {ps_quote(ip)} -PortNumber {int(port)};"
            "if(Get-PrinterPort -Name $n -ErrorAction SilentlyContinue){'CREADO'}else{throw 'No se creó'} }"
        )
        r = run_powershell(script)
        if not r.ok:
            return OpResult(False, "No se pudo crear el puerto TCP/IP.", r.short_error())
        state = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        return OpResult(True, f"Puerto {name} {'ya existía' if state == 'EXISTE' else 'creado'}.",
                        data={"port_name": name})

    def _remove_port_if_unused(self, port_name: str) -> None:
        run_powershell(
            f"$n={ps_quote(port_name)};if(-not (Get-Printer | Where-Object {{$_.PortName -eq $n}}))"
            "{Remove-PrinterPort -Name $n -ErrorAction SilentlyContinue}")

    # ------------------------------------------------------------ alta/baja
    def create_printer(self, name: str, driver: DriverRef, ip: str, port: int) -> OpResult:
        if (e := _need_admin()):
            return e
        if driver.kind != "installed":
            return OpResult(False, "En Windows el controlador debe estar instalado antes de crear la impresora.")
        if not self.driver_installed(driver.ref):
            return OpResult(False, f"El controlador '{driver.ref}' no está instalado en Windows.")
        pr = self.ensure_tcp_port(ip, port)
        if not pr.ok:
            return pr
        pname = pr.data["port_name"]
        r = run_powershell(
            f"Add-Printer -Name {ps_quote(name)} -DriverName {ps_quote(driver.ref)} -PortName {ps_quote(pname)};"
            f"$p=Get-Printer -Name {ps_quote(name)}; $p.Name", timeout=120)
        if not r.ok:
            return OpResult(False, "Windows rechazó la creación de la impresora.", r.short_error())
        p = self.get_printer(name)
        if not p:
            return OpResult(False, "La impresora no aparece en Windows tras crearla.")
        return OpResult(True, f"Impresora {name} instalada en Windows (puerto {pname}).",
                        data={"port_name": pname})

    def remove_printer(self, name: str) -> OpResult:
        if (e := _need_admin()):
            return e
        p = self.get_printer(name)
        if not p:
            return OpResult(False, f"{name} no existe en Windows.")
        r = run_powershell(f"Remove-Printer -Name {ps_quote(name)}", timeout=60)
        if not r.ok:
            return OpResult(False, f"No se pudo eliminar {name}.", r.short_error())
        if self.get_printer(name):
            return OpResult(False, f"{name} sigue presente en Windows.")
        if p.port_name.startswith("IP_"):
            self._remove_port_if_unused(p.port_name)
        return OpResult(True, f"Impresora {name} eliminada de Windows.")

    def set_address(self, name: str, ip: str, port: int) -> OpResult:
        if (e := _need_admin()):
            return e
        p = self.get_printer(name)
        if not p:
            return OpResult(False, f"{name} no existe.")
        pr = self.ensure_tcp_port(ip, port)
        if not pr.ok:
            return pr
        r = run_powershell(f"Set-Printer -Name {ps_quote(name)} -PortName {ps_quote(pr.data['port_name'])}")
        if not r.ok:
            return OpResult(False, "No se pudo asignar el nuevo puerto.", r.short_error())
        p2 = self.get_printer(name)
        if not p2 or p2.port_name != pr.data["port_name"]:
            return OpResult(False, "El cambio de puerto no se reflejó en Windows.")
        if p.port_name != p2.port_name and p.port_name.startswith("IP_"):
            self._remove_port_if_unused(p.port_name)
        return OpResult(True, f"{name} ahora usa {pr.data['port_name']} ({ip}:{port}).")

    def set_driver(self, name: str, driver: DriverRef) -> OpResult:
        if (e := _need_admin()):
            return e
        if not self.driver_installed(driver.ref):
            return OpResult(False, f"El controlador '{driver.ref}' no está instalado.")
        r = run_powershell(f"Set-Printer -Name {ps_quote(name)} -DriverName {ps_quote(driver.ref)}")
        if not r.ok:
            return OpResult(False, "Windows rechazó el cambio de controlador.", r.short_error())
        p = self.get_printer(name)
        if not p or p.driver != driver.ref:
            return OpResult(False, "El cambio de controlador no se reflejó.")
        return OpResult(True, f"Controlador de {name}: {driver.ref}.")

    def rename_printer(self, old: str, new: str) -> OpResult:
        if (e := _need_admin()):
            return e
        r = run_powershell(f"Rename-Printer -Name {ps_quote(old)} -NewName {ps_quote(new)}")
        if not r.ok:
            return OpResult(False, "No se pudo renombrar.", r.short_error())
        if not self.get_printer(new):
            return OpResult(False, "El nuevo nombre no aparece en Windows.")
        return OpResult(True, f"Impresora renombrada: {old} → {new}.")

    # ------------------------------------------------------------ trabajos
    def list_jobs(self, name: str | None = None) -> list[PrintJob]:
        if name:
            src = f"Get-PrintJob -PrinterName {ps_quote(name)}"
        else:
            src = "Get-Printer | ForEach-Object { Get-PrintJob -PrinterName $_.Name }"
        r = run_powershell(
            f"$l=@({src} | Select-Object Id,PrinterName,@{{n='St';e={{[string]$_.JobStatus}}}},Size,"
            "@{n='Sub';e={[string]$_.SubmittedTime}});ConvertTo-Json -InputObject $l -Compress")
        return [PrintJob(job_id=str(d.get("Id")), printer=d.get("PrinterName", ""), status=d.get("St", ""),
                         size=str(d.get("Size", "")), submitted=d.get("Sub", "")) for d in _json_list(r.stdout)]

    def clear_queue(self, name: str) -> OpResult:
        r = run_powershell(f"Get-PrintJob -PrinterName {ps_quote(name)} | Remove-PrintJob")
        if not r.ok:
            return OpResult(False, "No se pudo vaciar la cola.", r.short_error())
        left = self.list_jobs(name)
        if left:
            return OpResult(False, f"Quedan {len(left)} trabajos en la cola.")
        return OpResult(True, f"Cola de {name} vaciada.")

    def restart_queue(self, name: str) -> OpResult:
        c = self.clear_queue(name)
        r = run_powershell(
            f"$p=Get-CimInstance Win32_Printer -Filter {ps_quote(self._wql_name(name))};"
            "if($p.WorkOffline){Set-CimInstance -InputObject $p -Property @{WorkOffline=$false}};"
            "Invoke-CimMethod -InputObject $p -MethodName Resume | Out-Null; 'OK'")
        if not r.ok:
            return OpResult(False, "No se pudo reanudar la cola.", r.short_error())
        return OpResult(c.ok, f"Cola {name} vaciada y reanudada." if c.ok else c.message)

    @staticmethod
    def _wql_name(name: str) -> str:
        return "Name='" + name.replace("\\", "\\\\").replace("'", "\\'") + "'"

    def print_test_page(self, name: str) -> OpResult:
        r = run_powershell(
            f"$p=Get-CimInstance Win32_Printer -Filter {ps_quote(self._wql_name(name))};"
            "if(-not $p){throw 'Impresora no encontrada'};"
            "$r=Invoke-CimMethod -InputObject $p -MethodName PrintTestPage; $r.ReturnValue", timeout=60)
        if not r.ok:
            return OpResult(False, "Windows no pudo enviar la página de prueba.", r.short_error())
        rv = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        if rv != "0":
            return OpResult(False, f"PrintTestPage devolvió el código {rv}.")
        time.sleep(1.5)
        jobs = self.list_jobs(name)
        job_id = jobs[-1].job_id if jobs else ""
        return OpResult(True, "Página de prueba de Windows enviada a la cola.", data={"job_id": job_id})

    def print_raw(self, name: str, data: bytes, title: str = "eREDES") -> OpResult:
        if not IS_WINDOWS:
            return OpResult(False, "Solo disponible en Windows.")
        try:
            job = _winspool_raw(name, data, title)
        except OSError as e:
            return OpResult(False, "La cola de Windows rechazó el trabajo RAW.", str(e))
        return OpResult(True, f"Trabajo RAW {job} aceptado por la cola.", data={"job_id": str(job)})

    def wait_job(self, name: str, job_id: str, timeout: float = 30) -> OpResult:
        if not job_id:
            return OpResult(False, "No se obtuvo el identificador del trabajo; no se puede confirmar.")
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            jobs = {j.job_id: j for j in self.list_jobs(name)}
            if job_id not in jobs:
                return OpResult(True, f"El trabajo {job_id} salió de la cola de Windows (entregado al puerto).")
            last = jobs[job_id].status
            if any(w in last for w in ("Error", "Offline", "PaperOut", "Blocked")):
                return OpResult(False, f"El trabajo {job_id} está en estado: {last}")
            time.sleep(1)
        return OpResult(False, f"El trabajo {job_id} sigue en cola tras {int(timeout)} s.", f"Estado: {last}")

    # ------------------------------------------------------------ servicio
    def spooler_status(self) -> OpResult:
        r = run_powershell("(Get-Service -Name Spooler).Status.ToString()")
        st = r.stdout.strip()
        return OpResult(st == "Running", f"Cola de impresión (Spooler): {st or 'desconocido'}", st)

    def restart_spooler(self) -> OpResult:
        if (e := _need_admin()):
            return e
        r = run_powershell("Restart-Service -Name Spooler -Force; (Get-Service Spooler).Status.ToString()",
                           timeout=90)
        if not r.ok:
            return OpResult(False, "No se pudo reiniciar el Spooler.", r.short_error())
        return OpResult("Running" in r.stdout, "Servicio Spooler reiniciado." if "Running" in r.stdout
                        else "El Spooler no quedó en ejecución.", r.stdout)

    # ------------------------------------------------------------ drivers
    def install_driver_package(self, path: str, pkg_type: str, driver_names: list[str]) -> OpResult:
        if (e := _need_admin()):
            return e
        before = {d.name for d in self.list_drivers()}
        if pkg_type == "INF":
            r = run(["pnputil.exe", "/add-driver", path, "/install"], timeout=600)
            if r.returncode not in (0, 3010, 259):
                return OpResult(False, "pnputil no pudo añadir el driver al almacén.", r.output[-1500:])
            added, errors = [], []
            for n in driver_names:
                if n in before:
                    added.append(n)
                    continue
                r2 = run_powershell(f"Add-PrinterDriver -Name {ps_quote(n)}", timeout=300)
                if not r2.ok:
                    # alternativa: printui con la ruta del INF
                    r3 = run(["rundll32.exe", "printui.dll,PrintUIEntry", "/ia", "/m", n, "/f", path], timeout=300)
                    if not self.driver_installed(n):
                        errors.append(f"{n}: {r2.short_error()[:200]} {r3.short_error()[:100]}")
                        continue
                added.append(n)
            if not added:
                return OpResult(False, "El driver se añadió al almacén pero ningún modelo quedó instalado.",
                                "\n".join(errors))
            return OpResult(True, f"Controlador instalado: {', '.join(added)}.", "\n".join(errors),
                            data={"installed": added})
        if pkg_type in ("EXE", "MSI"):
            if pkg_type == "EXE":
                script = f"$p=Start-Process -FilePath {ps_quote(path)} -Wait -PassThru; $p.ExitCode"
            else:
                script = (f"$p=Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i',{ps_quote(path)}) "
                          "-Wait -PassThru; $p.ExitCode")
            r = run_powershell(script, timeout=3600)
            code = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
            if not r.ok or code not in ("0", "3010", "1641"):
                return OpResult(False, f"El instalador terminó con código {code or r.returncode}.", r.short_error())
            new = sorted({d.name for d in self.list_drivers()} - before)
            if not new:
                return OpResult(True, "El instalador terminó correctamente, pero no se detectaron controladores "
                                      "nuevos (puede que ya estuvieran instalados).", data={"installed": []})
            return OpResult(True, f"Instalador finalizado. Controladores nuevos: {', '.join(new)}.",
                            data={"installed": new})
        return OpResult(False, f"Los paquetes {pkg_type} no se pueden instalar en Windows.")


# ---------------------------------------------------------------- winspool RAW
class DOC_INFO_1(ctypes.Structure):
    _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR), ("pDatatype", wintypes.LPWSTR)]


def _winspool_raw(printer: str, data: bytes, title: str) -> int:
    ws = ctypes.WinDLL("winspool.drv", use_last_error=True)  # type: ignore[attr-defined]
    h = wintypes.HANDLE()
    if not ws.OpenPrinterW(printer, ctypes.byref(h), None):
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    try:
        di = DOC_INFO_1(title, None, "RAW")
        job = ws.StartDocPrinterW(h, 1, ctypes.byref(di))
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        try:
            if not ws.StartPagePrinter(h):
                raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
            written = wintypes.DWORD()
            buf = ctypes.create_string_buffer(data, len(data))
            if not ws.WritePrinter(h, buf, len(data), ctypes.byref(written)):
                raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
            ws.EndPagePrinter(h)
        finally:
            ws.EndDocPrinter(h)
        return job
    finally:
        ws.ClosePrinter(h)
